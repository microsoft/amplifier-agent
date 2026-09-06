"""Independent scripted engine using owned records and callback correlation only."""

import asyncio
import copy
import difflib
import fcntl
import json
import os
import pickle
import re
import uuid
from contextvars import ContextVar
from dataclasses import asdict, fields
from datetime import datetime
from decimal import Decimal, localcontext
from pathlib import Path
from types import SimpleNamespace

from amplifier_agent_engine._ports import active_turn_id
from amplifier_agent_engine._records import (
    AgentError,
    AgentOptions,
    ApprovalDecision,
    ApprovalRequest,
    ApprovalRequestEvent,
    ApprovalResolution,
    Event,
    OutputDelta,
    Progress,
    ReasoningDelta,
    ReasoningFinal,
    Selection,
    SessionOptions,
    SessionRecord,
    TextPart,
    ToolCall,
    ToolCallEvent,
    ToolContext,
    ToolFailed,
    ToolOutcomeUnknown,
    ToolResolution,
    ToolResultEvent,
    TurnInfo,
    TurnInput,
    TurnRecord,
    TurnResult,
    TurnStarted,
    Usage,
    UsageEntry,
    UsageEvent,
)
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from conformance.fixtures import replacement_tools

SCENARIOS = json.loads((Path(__file__).parents[1] / "scenarios/turns.json").read_text())
RECORDS = json.loads((Path(__file__).parents[1] / "scenarios/records.json").read_text())
VERSIONS = ("agent-interface/1", "turn-events/1", "language-binding/1", "host-config/1")


def error(code, message, *, category="input", remedy="Supply a valid value and try again.", **fields):
    return AgentError(code, category, message, remedy, **fields)


def closed():
    return error("closed", "This handle is closed.", category="lifecycle",
                 remedy="Create a new agent or session before doing work.")


async def settled(task):
    interrupted = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            interrupted = True
    if interrupted:
        raise asyncio.CancelledError
    return task.result()


def fixture_error():
    record = copy.deepcopy(RECORDS["error"])
    owned = {key: record.pop(key) for key in list(record) if "." in key}
    failure = AgentError(**record)
    for key, value in owned.items():
        setattr(failure, key, value)
    return failure


def decimal_sum(first, second):
    first, second = Decimal(first), Decimal(second)
    with localcontext() as context:
        context.prec = max(first.adjusted(), second.adjusted()) - min(
            first.as_tuple().exponent, second.as_tuple().exponent,
        ) + 2
        return first + second


def parts(content, field):
    if not isinstance(content, list):
        raise error("invalid_input", f"{field} must be a text-parts list.", details={"field": field})
    for part in content:
        if (not hasattr(part, "type") or part.type != "text" or not isinstance(part.text, str)
                or set(vars(part)) - {"type", "text"}):
            raise error("invalid_input", f"{field} contains an unsupported text part.", details={"field": field})


def select(model, ceiling):
    if model is None or model == ceiling:
        return ceiling
    if ceiling == "claude-opus-5" and model == "claude-sonnet-5":
        return model
    raise error("selector_rejected", "The model is outside the configured ceiling.", category="selection",
                remedy="Choose the configured model or a known cheaper model within its provider.")


def configuration(options):
    names = {field.name for field in fields(AgentOptions)}
    if not isinstance(options, AgentOptions):
        raise error("invalid_input", "AgentOptions must be a record.")
    unknown = set(vars(options)) - names
    if unknown:
        key = sorted(unknown)[0]
        nearest = max(sorted(names), key=lambda candidate: difflib.SequenceMatcher(None, key, candidate).ratio())
        raise error("invalid_input", f"Unknown AgentOptions fields: {sorted(unknown)}.",
                    remedy=f"Use {nearest} instead.")
    if not isinstance(options.tool_error_policy, str) or options.tool_error_policy not in {"stop", "continue"}:
        raise error("invalid_input", "tool_error_policy must be stop or continue.",
                    remedy="Choose stop or continue for tool_error_policy.",
                    details={"field": "tool_error_policy"})
    if options.tools is not None:
        if not isinstance(options.tools, list):
            raise error("invalid_input", "tools must be a list of tool declarations.")
        seen = set()
        for index, tool in enumerate(options.tools):
            try:
                assert isinstance(tool.name, str) and tool.name and tool.name not in seen
                assert callable(tool.handler)
                assert isinstance(tool.input_schema, dict)
                assert tool.input_schema.get("$schema") == "https://json-schema.org/draft/2020-12/schema"
                Draft202012Validator.check_schema(tool.input_schema)
            except (AssertionError, AttributeError, SchemaError):
                raise error("invalid_input", f"tools[{index}] has an invalid declaration.") from None
            seen.add(tool.name)
    registered = {"provider", "model", "storage", "workspace", "extra_request_params"}
    configured = {}
    if path := os.environ.get("AMPLIFIER_AGENT_CONFIG"):
        try:
            configured = json.loads(Path(path).read_text())
        except (OSError, ValueError):
            raise error("invalid_input", "AMPLIFIER_AGENT_CONFIG must name a readable JSON object.") from None
        if not isinstance(configured, dict):
            raise error("invalid_input", "The host configuration must be an object.")
    for key in configured:
        if key not in registered:
            nearest = max(sorted(registered), key=lambda candidate: difflib.SequenceMatcher(None, key, candidate).ratio())
            raise error("invalid_input", f"The host key {key} is unregistered.", remedy=f"Use {nearest}.")
    for variable, value in os.environ.items():
        if not variable.startswith("AMPLIFIER_AGENT_") or variable == "AMPLIFIER_AGENT_CONFIG":
            continue
        key = variable.removeprefix("AMPLIFIER_AGENT_").lower()
        if key.startswith("engine_"):
            continue
        if key not in registered:
            nearest = max(sorted(registered), key=lambda candidate: difflib.SequenceMatcher(None, key, candidate).ratio())
            raise error("invalid_input", f"The host key {variable} is unregistered.",
                        remedy=f"Use AMPLIFIER_AGENT_{nearest.upper()}.")
        if key == "extra_request_params":
            try:
                value = json.loads(value)
            except ValueError:
                raise error("invalid_input", "extra_request_params must be JSON.") from None
        configured[key] = value
    resolved = {"provider": "anthropic", "model": "claude-sonnet-5", "workspace": "default",
                "storage": "~/.amplifier-agent", "extra_request_params": {}, **configured}
    resolved.update({key: getattr(options, key) for key in ("provider", "model", "storage")
                     if getattr(options, key) is not None})
    if not isinstance(resolved["provider"], str) or not resolved["provider"] or "," in resolved["provider"]:
        raise error("invalid_input", "Configure exactly one provider.")
    if not isinstance(resolved["workspace"], str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", resolved["workspace"]):
        raise error("invalid_input", "The workspace slug is invalid.")
    overrides = resolved["extra_request_params"]
    if not isinstance(overrides, dict) or any(not isinstance(value, dict) for value in overrides.values()):
        raise error("invalid_input", "extra_request_params must map providers to settings objects.")
    overrides = copy.deepcopy(overrides.get(resolved["provider"], {}))
    for key, value in overrides.items():
        if key in {"previous_response_id", "conversation", "input", "messages", "instructions", "model", "tools"}:
            raise error("invalid_input", f"extra_request_params.{key} cannot replace local conversation state.")
        if key == "store":
            if type(value) is bool:
                continue
            if isinstance(value, str) and value in {"false", "0", "no"}:
                overrides[key] = False
            else:
                raise error("invalid_input", f"extra_request_params.{resolved['provider']}.store is ambiguous.",
                            remedy="Supply a boolean or an accepted false string.")
    resolved["extra_request_params"] = overrides
    resolved["storage"] = str(Path(resolved["storage"]).expanduser().absolute())
    return resolved


class Probe:
    def __init__(self, script=None):
        self.script = script
        self.requests = []
        self.active = 0
        self.offsets = {}
        self.cursors = {}
        self.entered, self.settled = asyncio.Event(), asyncio.Event()
        self.settled.set()

    def responses(self, input, session):
        identity = session._info.session_id
        if self.script is not None:
            key = (id(session.agent), identity, id(self.script))
            self.cursors[identity] = key
            return copy.deepcopy(self.script[self.offsets.get(key, 0):])
        text = "".join(part.text for part in input.content)
        history_text = " ".join(part.text for message in input.history or [] for part in message.content)
        for candidate in [text, *("".join(part.text for part in message.content) for message in reversed(input.history or []))]:
            if candidate.startswith("conformance-script:"):
                script = json.loads(candidate.removeprefix("conformance-script:"))
                key = (identity, candidate)
                self.cursors[identity] = key
                if self.offsets.get(key, 0) >= len(script):
                    self.offsets[key] = 0
                return copy.deepcopy(script[self.offsets.get(key, 0):])
        self.cursors.pop(identity, None)
        for scenario in SCENARIOS:
            prompt = "".join(part["text"] for part in scenario["input"]["content"])
            if prompt and prompt == text:
                return copy.deepcopy(scenario["provider"])
        if "Preserve this history" in history_text:
            return copy.deepcopy(next(case["provider"] for case in SCENARIOS if case["id"] == "history"))
        return copy.deepcopy(SCENARIOS[0]["provider"])


probe_factory = Probe


class ReplacementTurn:
    def __init__(self, session, input):
        self.session, self.input = session, copy.deepcopy(input)
        self.info = TurnInfo(session._info.session_id, str(uuid.uuid4()))
        self.model = select(input.model, session.model)
        self.cancelled = False
        self._cancelled, self._changed = asyncio.Event(), asyncio.Event()
        self._events, self._output = [], []
        self._consumed = self._done = False
        self._unknown_call = self._final_history = self._task = None
        self._usage = Usage([UsageEntry(session.agent.provider, self.model)])
        self._inside_hook = ContextVar("replacement_inside_hook", default=False)
        self.evolved = any(RECORDS["marker"] in part.text for part in input.content)
        self._messages = copy.deepcopy(session._conversation)
        self._messages.extend(asdict(message) for message in input.history or [])
        if input.content:
            self._messages.append({"role": "user", "content": [asdict(part) for part in input.content]})

    def launch(self):
        self._task = asyncio.create_task(self._execute())

    @property
    def final_history(self):
        return copy.deepcopy(self._final_history)

    def emit(self, name, payload):
        if name == "tool_result":
            resolution = payload.resolution
            if resolution.outcome == "unknown" and self.session.agent.options.tool_error_policy == "continue":
                self._unknown_call = self._unknown_call or resolution.call_id
            self._messages.append({"role": "tool", "tool_call_id": resolution.call_id,
                                   "content": json.dumps({"call_id": resolution.call_id,
                                       "outcome": resolution.outcome, "content": resolution.content,
                                       "error": vars(resolution.error) if resolution.error else None})})
        event = Event(
            "turn-events/1", self.info.session_id, self.info.turn_id,
            len(self._events) + 1, name, copy.deepcopy(payload),
        )
        if self.evolved:
            event.at = datetime.fromisoformat(RECORDS["at"].replace("Z", "+00:00"))
            setattr(event, "org.example.envelope", copy.deepcopy(RECORDS["envelope_extension"]))
            if hasattr(event.payload, "__dict__"):
                for field in ("future_optional", "org.example.payload"):
                    setattr(event.payload, field, copy.deepcopy(RECORDS["payload_extension"]))
        self._events.append(event)
        self._changed.set()

    def request(self):
        messages = []
        if self.session.agent.options.instructions is not None:
            messages.append({"role": "system", "content": self.session.agent.options.instructions})
        messages.extend(copy.deepcopy(self._messages))
        tools = self.session.agent.tools
        if self._unknown_call:
            tools = [tool for tool in tools if tool["name"] in replacement_tools.INSPECTION]
        return {"model": self.model, "messages": messages, "tools": copy.deepcopy(tools)}

    def provider_events(self, step):
        reasoning = ""
        for event in step.get("events", []):
            name, data = event["type"], event["data"]
            if name == "llm:stream_block_delta" and data.get("block_type") == "thinking":
                reasoning += data["text"]
                self.emit("reasoning_delta", ReasoningDelta(data["text"]))
            elif name == "llm:stream_block_end" and data.get("block_type") == "thinking":
                self.emit("reasoning_final", ReasoningFinal(reasoning))
                reasoning = ""
            elif re.fullmatch(r"(?:[a-z][a-z0-9-]*\.)+[a-z][a-z0-9_-]*", name):
                self.emit(name, data.get("payload", data))
                for key, value in data.items():
                    if "." in key:
                        setattr(self._events[-1], key, copy.deepcopy(value))
                if "at" in data:
                    self._events[-1].at = datetime.fromisoformat(data["at"].replace("Z", "+00:00"))

    def events(self):
        if self._consumed:
            raise error("stream_already_consumed", "The turn already has a consumer.", category="turn",
                        remedy="Consume each turn through one event iterator.")
        self._consumed = True
        return self._iterate()

    async def _iterate(self):
        index = 0
        while True:
            while index < len(self._events):
                event = copy.deepcopy(self._events[index])
                index += 1
                yield event
            if self._done:
                return
            self._changed.clear()
            await self._changed.wait()

    async def cancel(self):
        if not self._done:
            self.cancelled = True
            self._cancelled.set()
        await settled(self._task)

    def cancellation(self):
        return error("turn_cancelled", "Cancellation was accepted.", category="turn",
                     remedy="Start a new turn for further work.")

    async def settle_call(self, awaitable, *, timeout=None):
        work = asyncio.create_task(awaitable)
        cancelling = asyncio.create_task(self._cancelled.wait())
        try:
            done, _ = await asyncio.wait({work, cancelling}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
            cause = None if work in done else "cancel" if cancelling in done else "timeout"
            if cause:
                work.cancel()
            try:
                return await asyncio.shield(work), cause
            except asyncio.CancelledError:
                return None, cause or "cancel"
        finally:
            cancelling.cancel()

    async def _tool(self, request, *, call_id=None):
        call_id = call_id or str(uuid.uuid4())
        name, arguments = request["name"], copy.deepcopy(request.get("arguments", {}))
        deadline = datetime.fromisoformat(RECORDS["deadline"].replace("Z", "+00:00")) if name == "conformance_records" else None
        source = self.session.agent.sources.get(name, "built-in")
        def announce():
            self._messages.append({"role": "assistant", "content": "", "tool_calls": [
                {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}
            ]})
            self.emit("tool_call", ToolCallEvent(ToolCall(call_id, name, source, arguments, deadline=deadline)))

        if self._unknown_call is not None and not (name in replacement_tools.INSPECTION and source == "built-in"):
            announce()
            failure = error("tool_recovery_blocked", "A prior tool has an uncertain outcome.", category="executor",
                            remedy="Inspect the uncertain effect, then start a new turn for further work.",
                            details={"uncertain_call_id": self._unknown_call})
            self.emit("tool_result", ToolResultEvent(ToolResolution(call_id, "cancelled", error=failure)))
            return "failure", failure
        handler = self.session.agent.executors.get(name)
        if name in replacement_tools.BUILTINS:
            async def handler(arguments, context):
                return await self.builtin(name, arguments, context)
        tool = SimpleNamespace(handler=handler) if handler else None
        if tool is None:
            announce()
            failure = error("tool_callback_failed", f"No executor is registered for {name}.", category="executor",
                            remedy="Register the named tool with a handler.")
            self.emit("tool_result", ToolResultEvent(ToolResolution(call_id, "failed", error=failure)))
            return "failure", failure
        specification = next((item for item in self.session.agent.tools if item["name"] == name), None)
        try:
            json.dumps(arguments, allow_nan=False)
            if specification:
                Draft202012Validator(specification["parameters"]).validate(arguments)
        except (ValueError, TypeError, ValidationError):
            announce()
            failure = error("tool_arguments_invalid", "The tool arguments do not satisfy its declaration.",
                            category="executor", correlation_id=call_id)
            self.emit("tool_result", ToolResultEvent(ToolResolution(call_id, "failed", error=failure)))
            return "failure", failure
        if not self._inside_hook.get():
            state, failure = await self.hooks("PreToolUse", name)
            if state:
                return state, failure
        announce()

        def summary(value):
            if isinstance(value, dict):
                return {key: "[redacted]" if any(word in key.lower() for word in ("token", "password", "secret", "key", "authorization"))
                        else summary(item) for key, item in value.items()}
            if isinstance(value, list):
                return [summary(item) for item in value[:10]]
            return value[:256] + "[truncated]" if isinstance(value, str) and len(value) > 256 else value

        directory = arguments.get("cwd", self.session.agent.cwd)
        description = f'Run {source} tool "{name}" in {directory}: {json.dumps(summary(arguments))}'
        approval = ApprovalRequest(str(uuid.uuid4()), description[:4096], call_id, name)
        self.emit("approval_request", ApprovalRequestEvent(approval))
        policy = self.session.agent.options.approvals
        token = active_turn_id.set(getattr(self, "callback_turn_id", self.info.turn_id))
        try:
            if callable(policy):
                try:
                    response, cause = await self.settle_call(policy(approval), timeout=0.25)
                except Exception:
                    decision, reason = "unavailable", None
                else:
                    decision, reason = getattr(response, "decision", None), getattr(response, "reason", None)
                    if (cause or not hasattr(response, "__dict__")
                            or set(vars(response)) - {"decision", "reason"}
                            or (reason is not None and not isinstance(reason, str))):
                        decision, reason = cause or "invalid", None
            else:
                decision, reason = policy or "unavailable", None
            if self.cancelled:
                decision = "cancel"
            if (self._unknown_call and not self.cancelled
                    and not (name in replacement_tools.INSPECTION and source == "built-in")):
                self.emit("approval_decision", ApprovalDecision(ApprovalResolution(approval.request_id, "cancel")))
                failure = error("tool_recovery_blocked", "An earlier effect remains uncertain.", category="executor",
                                remedy="Inspect the earlier effect, then start a new turn for further work.",
                                details={"uncertain_call_id": self._unknown_call})
                self.emit("tool_result", ToolResultEvent(ToolResolution(call_id, "cancelled", error=failure)))
                return "failure", failure
            if not isinstance(decision, str) or decision not in {"allow", "deny", "cancel", "unavailable", "timeout"}:
                decision = "invalid"
            self.emit("approval_decision", ApprovalDecision(ApprovalResolution(approval.request_id, decision, reason)))
            if decision != "allow":
                code, state = {
                    "deny": ("approval_denied", "rejected"),
                    "cancel": ("approval_cancelled", "cancelled"),
                    "unavailable": ("approval_unavailable", "failure"),
                    "timeout": ("approval_timeout", "failure"),
                }.get(decision, ("approval_invalid", "failure"))
                failure = self.cancellation() if self.cancelled else error(
                    code, "The tool was not authorized.", category="approval",
                    remedy="Supply an approval policy that permits the requested effect.",
                    correlation_id=approval.request_id,
                )
                self.emit("tool_result", ToolResultEvent(ToolResolution(call_id, "cancelled", error=failure)))
                return state, failure
            partial = None
            try:
                context = ToolContext(call_id, deadline=deadline)
                result, cause = await self.settle_call(tool.handler(arguments, context))
                if cause and result is None:
                    raise ToolOutcomeUnknown("The executing effect did not establish its result before cancellation.")
            except ToolOutcomeUnknown as exc:
                partial = getattr(exc, "content", None)
                failure = error("tool_completion_unknown", str(exc), category="executor",
                                remedy="Inspect the effect before requesting further work.", correlation_id=call_id)
                outcome, self._unknown_call = "unknown", call_id
            except ToolFailed as exc:
                partial = getattr(exc, "content", None)
                failure = error("tool_failed", str(exc), category="executor",
                                remedy="Correct the reported tool failure before trying again.", correlation_id=call_id)
                outcome = "failed"
            except AgentError as exc:
                failure, outcome = exc, "cancelled" if exc.category in {"selection", "input"} else "unknown"
            except Exception as exc:
                failure = error("tool_callback_failed", str(exc), category="executor",
                                remedy="Restore the tool executor before requesting work.", correlation_id=call_id)
                outcome = "unknown"
            else:
                if not isinstance(result, str):
                    failure = error("tool_result_invalid", "The tool did not return text.", category="executor",
                                    remedy="Return the tool result as a string.", correlation_id=call_id)
                    outcome = "unknown"
                else:
                    self.emit("tool_result", ToolResultEvent(ToolResolution(call_id, "completed", result)))
                    if not self.cancelled and not self._inside_hook.get():
                        state, failure = await self.hooks("PostToolUse", name)
                        if state:
                            return state, failure
                    return ("cancelled", self.cancellation()) if self.cancelled else (None, None)
            self.emit("tool_result", ToolResultEvent(ToolResolution(call_id, outcome, partial, failure)))
            if self.cancelled:
                return "cancelled", self.cancellation()
            if (self.session.agent.options.tool_error_policy == "continue"
                    and failure.code in {"tool_failed", "tool_completion_unknown"}
                    and not self._inside_hook.get()):
                return None, None
            return {"approval_denied": "rejected", "approval_cancelled": "cancelled"}.get(failure.code, "failure"), failure
        finally:
            active_turn_id.reset(token)

    async def hooks(self, event, name=None):
        for rule in getattr(self, "skill_hooks", {}).get(event, []):
            if name is not None and rule.get("matcher") and not re.fullmatch(rule["matcher"], name):
                continue
            for hook in rule.get("hooks", []):
                if hook.get("type") != "command":
                    continue
                token = self._inside_hook.set(True)
                identity = str(uuid.uuid4())
                try:
                    state, failure = await self._tool({"name": "bash", "arguments": {
                        "command": hook["command"], "timeout": hook.get("timeout", 30),
                        "cwd": rule.get("_cwd", self.session.agent.cwd),
                    }}, call_id=identity)
                finally:
                    self._inside_hook.reset(token)
                if state:
                    return state, failure
                response = next(item.payload.resolution.content for item in reversed(self._events)
                                if item.type == "tool_result" and item.payload.resolution.call_id == identity)
                if response:
                    try:
                        decision = json.loads(response)
                        if (not isinstance(decision, dict)
                                or ("continue" in decision and type(decision["continue"]) is not bool)
                                or decision.get("continue") is False
                                or decision.get("decision") not in {None, "allow", "approve"}):
                            raise ValueError("The guard refused the effect.")
                    except ValueError:
                        return "failure", error("tool_failed", "The skill guard refused the effect or returned an invalid decision.", category="executor")
        return None, None

    async def builtin(self, name, arguments, context):
        if name == "delegate":
            return await self.delegate(arguments, context)
        if name == "load_skill":
            metadata, instruction, descriptor, directory = replacement_tools.skill(self.session.agent.options.skills, arguments["name"])
            while match := re.search(r"!`([^`]+)`", instruction):
                content = await self.embedded_effect("bash", {"command": match[1], "cwd": str(directory)})
                instruction = instruction[:match.start()] + content + instruction[match.end():]
            instruction = instruction.replace("$ARGUMENTS", str(arguments.get("arguments", "")))
            hooks = {event: [{**rule, "_cwd": str(directory)} for rule in rules]
                     for event, rules in metadata.get("hooks", {}).items()}
            if metadata.get("context") != "fork" and not metadata.get("agent"):
                self.skill_hooks = {**getattr(self, "skill_hooks", {}), **hooks}
                return instruction
            return await self.delegate({"instruction": instruction, "model": descriptor.get("model"),
                                        "tools": descriptor.get("tools"), "skill_hooks": hooks}, context)
        try:
            return await replacement_tools.local(name, arguments, cwd=self.session.agent.cwd,
                                                 environment=self.session.agent.environment)
        except (OSError, KeyError) as exc:
            raise ToolFailed(str(exc)) from None

    async def embedded_effect(self, name, arguments):
        identity = str(uuid.uuid4())
        state, failure = await self._tool({"name": name, "arguments": arguments}, call_id=identity)
        if state:
            raise failure
        result = next(event.payload.resolution for event in reversed(self._events)
                      if event.type == "tool_result" and event.payload.resolution.call_id == identity)
        return result.content or ""

    async def delegate(self, arguments, context):
        model = select(arguments.get("model"), self.model)
        instruction = arguments.get("instruction")
        if not isinstance(instruction, str) or not instruction:
            raise error("invalid_input", "A delegate needs an instruction.")
        child_agent = copy.copy(self.session.agent)
        child_agent.options = copy.deepcopy(child_agent.options)
        child_agent.config = copy.deepcopy(child_agent.config)
        child_agent.model = child_agent.options.model = child_agent.config["model"] = model
        names = arguments.get("tools")
        if names is not None:
            child_agent.options.tools = [tool for tool in child_agent.options.tools or [] if tool.name in names]
            child_agent.tools = [tool for tool in child_agent.tools if tool["name"] in names]
            child_agent.executors = {name: handler for name, handler in child_agent.executors.items() if name in names}
        child_agent.sessions = {}
        child_agent.probe = probe_factory()
        session = await child_agent.create_session(SessionOptions(persistence="ephemeral"))
        turn = await session.start_turn(TurnInput([TextPart(instruction)]))
        turn.callback_turn_id = getattr(self, "callback_turn_id", self.info.turn_id)
        turn.skill_hooks = arguments.get("skill_hooks", {})
        baseline = copy.deepcopy(self._usage)
        delivered = 0

        def relay(event):
            if event.type == "usage":
                merged = copy.deepcopy(baseline)
                for addition in event.payload.snapshot.entries:
                    current = next((entry for entry in merged.entries
                                    if (entry.provider, entry.model) == (addition.provider, addition.model)), None)
                    if current is None:
                        merged.entries.append(copy.deepcopy(addition))
                        continue
                    for key in ("tokens_in", "tokens_out", "cache_read_tokens", "cache_write_tokens"):
                        value = getattr(addition, key)
                        if value is not None:
                            setattr(current, key, (getattr(current, key) or 0) + value)
                    for currency, amount in (addition.cost or {}).items():
                        current.cost = {**(current.cost or {}), currency: decimal_sum((current.cost or {}).get(currency, 0), amount)}
                self._usage = merged
                self.emit("usage", UsageEvent(merged))
            elif event.type in {"tool_call", "tool_result", "approval_request", "approval_decision", "progress"}:
                self.emit(event.type, event.payload)

        try:
            async for event in turn.events():
                delivered += 1
                relay(event)
        finally:
            await session.close()
            for event in turn._events[delivered:]:
                relay(event)
        result = turn._events[-1].payload
        if result.state != "success":
            raise result.error or error("tool_failed", "The delegated turn did not complete.", category="executor")
        return "".join(part.text for part in result.content or [])

    async def _execute(self):
        self.emit("turn_started", TurnStarted(
            "resumed" if self.session.accepted else "fresh", Selection(self.session.agent.provider, self.model),
        ))
        state, failure = "success", None
        probe = self.session.agent.probe
        try:
            for step in probe.responses(self.input, self.session):
                if self.cancelled:
                    state, failure = "cancelled", self.cancellation()
                    break
                probe.requests.append(copy.deepcopy(self.request()))
                if (cursor := probe.cursors.get(self.session._info.session_id)) is not None:
                    probe.offsets[cursor] = probe.offsets.get(cursor, 0) + 1
                if ledger := os.environ.get("CONFORMANCE_PROVIDER_REQUEST_LOG"):
                    with Path(ledger).open("a") as stream:
                        stream.write(json.dumps(probe.requests[-1]) + "\n")
                self.emit("usage", UsageEvent(self._usage))
                if self.evolved:
                    self.emit("progress", Progress(copy.deepcopy(RECORDS["progress"])))
                probe.active += 1
                probe.entered.set()
                probe.settled.clear()
                try:
                    if step.get("observe_request"):
                        self.emit("org.example.request", probe.requests[-1])
                    if step.get("observe_config"):
                        self.emit("org.example.config", self.session.agent.config)
                    self.provider_events(step)
                    for text in step.get("chunks", [step.get("text", "")]):
                        if text or "chunks" in step:
                            part = TextPart(text)
                            self._output.append(part)
                            self.emit("output_delta", OutputDelta([part]))
                            await asyncio.sleep(0)
                    if step.get("text") or step.get("chunks"):
                        self._messages.append({"role": "assistant", "content": "".join(step.get("chunks", [step.get("text", "")]))})
                    if step.get("block"):
                        await self._cancelled.wait()
                    if self.cancelled:
                        state, failure = "cancelled", self.cancellation()
                        break
                    if step.get("failure"):
                        state, failure = "failure", error(
                            "provider_failed", "The scripted provider failed.", category="provider",
                            remedy="Inspect provider availability before retrying.",
                            retryable=bool(step.get("failure_retryable", False)),
                        )
                        break
                    usage = step.get("usage", {"input_tokens": 7, "output_tokens": 2})
                    entry = self._usage.entries[0]
                    for target, source in (("tokens_in", "input_tokens"), ("tokens_out", "output_tokens"),
                                           ("cache_read_tokens", "cache_read_tokens"), ("cache_write_tokens", "cache_write_tokens")):
                        if source in usage:
                            setattr(entry, target, (getattr(entry, target) or 0) + usage[source])
                    if "cost_usd" in usage:
                        entry.cost = {"USD": decimal_sum((entry.cost or {}).get("USD", Decimal(0)), usage["cost_usd"])}
                    self.emit("usage", UsageEvent(self._usage))
                finally:
                    probe.active -= 1
                    if probe.active == 0:
                        probe.settled.set()
                if "tool" in step or "tools" in step:
                    requests = step.get("tools", [step.get("tool")])
                    results = await asyncio.gather(*(self._tool(request) for request in requests))
                    state, failure = next((item for item in results if item[0] is not None), (None, None))
                    if state is not None:
                        break
                else:
                    state = "success"
                    break
            if state is None:
                state, failure = "failure", error("provider_failed", "The response script ended before a reply.", category="provider")
        except Exception as exc:
            state, failure = "failure", error("internal_failed", str(exc), category="internal",
                                             remedy="Correct the replacement fixture before retrying.")
        result = TurnResult(state, copy.deepcopy(self._output), failure, copy.deepcopy(self._usage))
        if state == "success" and getattr(self, "skill_hooks", {}).get("Stop"):
            state, failure = await self.hooks("Stop")
            if state:
                result.state, result.error = state, failure
        if any(part.text == RECORDS["terminal_error_marker"] for part in self.input.content):
            result.state, result.error = "failure", fixture_error()
        self.session._history.append(TurnRecord(self.info.turn_id, self.input, copy.deepcopy(result)))
        self.session._conversation = copy.deepcopy(self._messages)
        self.session.accepted = True
        self.session._save()
        self._final_history = copy.deepcopy(self.session._history)
        self.emit("usage", UsageEvent(self._usage))
        self.emit("terminal", result)
        self._done = True
        self.session.active = None
        self._changed.set()


class ReplacementSession:
    def __init__(self, agent, info, model, *, history=None, conversation=None, accepted=False, lease=None):
        self.agent, self._info, self.model = agent, info, model
        self._history = copy.deepcopy(history or [])
        self._conversation = copy.deepcopy(conversation or [])
        self.accepted, self.lease = accepted, lease
        self.active = None
        self.closed = False

    def check(self):
        if self.closed or self.agent.closed:
            raise closed()

    @property
    def info(self):
        self.check()
        return self._info

    @property
    def history(self):
        self.check()
        return copy.deepcopy(self._history)

    def _save(self):
        if self._info.persistence == "durable":
            target = self.agent.path(self._info.session_id)
            pending = target.with_suffix(".pending")
            pending.write_bytes(pickle.dumps({
                "history": self._history, "accepted": self.accepted,
                "conversation": self._conversation,
                "provider": self.agent.provider, "model": self.model,
            }))
            pending.replace(target)

    async def start_turn(self, input):
        self.check()
        if self.active is not None:
            raise error("busy", "The session has an active turn.", category="turn", remedy="Wait for its terminal result.")
        if not hasattr(input, "content") or set(vars(input)) - {"content", "history", "model"}:
            raise error("invalid_input", "input must be a TurnInput record.", details={"field": "input.content"})
        parts(input.content, "input.content")
        if input.history is not None:
            if self._info.persistence != "ephemeral" or self.accepted:
                raise error("invalid_input", "History cannot seed this session.", remedy="Seed an unused ephemeral session.")
            if not isinstance(input.history, list):
                raise error("invalid_input", "input.history must be a conversation list.", details={"field": "input.history"})
            for message in input.history:
                if (not hasattr(message, "role") or not isinstance(message.role, str)
                        or message.role not in {"system", "developer", "user", "assistant"}
                        or set(vars(message)) - {"role", "content"}):
                    raise error("invalid_input", "The history message is unsupported.", details={"field": "input.history"})
                parts(message.content, "input.history.content")
        if not input.content and not input.history:
            raise error("invalid_input", "A turn needs content or supplied history.",
                        remedy="Provide content or at least one history message.",
                        details={"field": "input.content"})
        turn = ReplacementTurn(self, input)
        self.active = turn
        turn.launch()
        return turn

    async def run(self, input):
        result, _ = await self.run_with_history(input)
        return result

    async def run_with_history(self, input):
        turn = await self.start_turn(input)
        async for event in turn.events():
            if event.type == "terminal":
                return event.payload, turn.final_history
        raise AssertionError("The replacement turn omitted its terminal result")

    async def fork(self):
        self.check()
        if self.active is not None:
            raise error("busy", "The session has an active turn.", category="turn")
        child = await self.agent.create_session(SessionOptions(persistence=self._info.persistence, model=self.model))
        child._history = copy.deepcopy(self._history)
        child._conversation = copy.deepcopy(self._conversation)
        child.accepted = self.accepted
        child._save()
        return child

    async def close(self):
        if not hasattr(self, "_closing"):
            self._closing = asyncio.create_task(self._close())
        await settled(self._closing)

    async def _close(self):
        if self.closed:
            return
        if self.active is not None:
            await self.active.cancel()
        self.closed = True
        if self.lease is not None:
            self.lease.close()
            self.lease = None


class ReplacementAgent:
    contract_versions = VERSIONS

    def __init__(self, options):
        self.config = configuration(options)
        self.config["instructions"] = options.instructions
        self.options = copy.deepcopy(options)
        self.cwd, self.environment = str(Path.cwd()), dict(os.environ)
        self.options.skills = [str(Path(source).expanduser().absolute()) for source in self.options.skills or []]
        self.provider, self.model = self.config["provider"], self.config["model"]
        self.root = Path(self.config["storage"]) / "replacement-state" / self.config["workspace"]
        self.sessions, self.closed = {}, False
        self.probe = probe_factory()
        self.tools = [{"name": name, "description": f"Execute {name}.", "parameters": replacement_tools.SCHEMA}
                      for name in sorted(replacement_tools.BUILTINS)]
        self.sources = {name: "built-in" for name in replacement_tools.BUILTINS}
        self.executors, self.connections = {}, []
        for tool in self.options.tools or []:
            if tool.name in self.sources:
                raise error("invalid_input", f"tools contains duplicate name {tool.name}.")
            self.tools.append({"name": tool.name, "description": tool.description, "parameters": tool.input_schema})
            self.sources[tool.name], self.executors[tool.name] = "caller", tool.handler

    async def initialize(self):
        try:
            for declaration in self.options.mcp_servers or []:
                connection = replacement_tools.Mcp(declaration)
                self.connections.append(connection)
                for tool in await connection.open():
                    name = f"mcp_{declaration.name}_{tool['name']}"
                    if name in self.sources:
                        raise error("invalid_input", f"Duplicate MCP tool {name}.")
                    self.tools.append({"name": name, "description": tool.get("description", name), "parameters": tool["inputSchema"]})
                    self.sources[name] = "mcp"

                    async def execute(arguments, context, connection=connection, name=tool["name"]):
                        return await connection.call(name, arguments)

                    self.executors[name] = execute
        except BaseException:
            for connection in self.connections:
                await connection.close()
            raise

    def check(self):
        if self.closed:
            raise closed()

    def path(self, session_id):
        return self.root / f"{session_id}.pickle"

    def lease(self, session_id):
        self.root.mkdir(parents=True, exist_ok=True)
        handle = (self.root / f"{session_id}.lock").open("a+b")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            raise error("session_in_use", "The session already has a live owner.", category="session",
                        remedy="Close its existing owner before trying again.") from None
        return handle

    async def create_session(self, options=None):
        self.check()
        options = options or SessionOptions()
        session_id = str(uuid.uuid4()) if options.session_id is None else options.session_id
        if not isinstance(session_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{7,63}", session_id):
            raise error("session_id_invalid", "The session id is invalid.", category="session")
        if options.persistence not in {"durable", "ephemeral"}:
            raise error("invalid_input", "Choose durable or ephemeral persistence.")
        model = select(options.model, self.model)
        if self.path(session_id).exists() or (session_id in self.sessions and not self.sessions[session_id].closed):
            raise error("already_exists", "The session id already exists.", category="session")
        lease = self.lease(session_id) if options.persistence == "durable" else None
        session = ReplacementSession(self, SessionRecord(session_id, options.persistence), model, lease=lease)
        session._save()
        self.sessions[session_id] = session
        return session

    async def resume_session(self, session_id):
        self.check()
        if not isinstance(session_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{7,63}", session_id) or not self.path(session_id).exists():
            raise error("not_found", "No durable session has this id.", category="session",
                        remedy="Use an existing id or create a new session explicitly.")
        lease = self.lease(session_id)
        try:
            saved = pickle.loads(self.path(session_id).read_bytes())
            if saved["provider"] != self.provider:
                raise error("selector_rejected", "The saved session belongs to another provider.", category="selection")
            session = ReplacementSession(
                self, SessionRecord(session_id, "durable"), select(saved["model"], self.model),
                history=saved["history"], accepted=saved["accepted"], lease=lease,
                conversation=saved.get("conversation", []),
            )
        except BaseException:
            lease.close()
            raise
        self.sessions[session_id] = session
        return session

    async def list_sessions(self):
        self.check()
        return [SessionRecord(path.stem, "durable") for path in sorted(self.root.glob("*.pickle"))]

    async def delete_session(self, session_id):
        self.check()
        if not isinstance(session_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{7,63}", session_id) or not self.path(session_id).exists():
            raise error("not_found", "No durable session has this id.", category="session")
        lease = self.lease(session_id)
        try:
            self.path(session_id).unlink()
        finally:
            lease.close()

    async def close(self):
        if not hasattr(self, "_closing"):
            self._closing = asyncio.create_task(self._close())
        await settled(self._closing)

    async def _close(self):
        if self.closed:
            return
        for session in self.sessions.values():
            await session.close()
        for connection in self.connections:
            await connection.close()
        self.closed = True


async def create_engine(options):
    if options.instructions == RECORDS["method_error_marker"]:
        raise fixture_error()
    agent = ReplacementAgent(options)
    await agent.initialize()
    return agent

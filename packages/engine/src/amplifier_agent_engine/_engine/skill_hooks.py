"""Scope skill commands to observable, approved work in an admitted turn."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .._records import AgentError, ToolContext, ToolFailed
from .builtin_tools import bash_tool
from .configuration import strict_json

EVENTS = {"PreToolUse": "PreToolUse", "PostToolUse": "PostToolUse", "Stop": "Stop",
          "pre-tool": "PreToolUse", "post-tool": "PostToolUse", "stop": "Stop"}


def invalid(message: str) -> AgentError:
    return AgentError("invalid_input", "input", message,
                      "Use command hooks for PreToolUse, PostToolUse, or Stop in the skill source.")


@dataclass(frozen=True)
class CommandHook:
    event: str
    command: str
    matcher: re.Pattern[str] | None
    timeout: int


@dataclass(frozen=True)
class SkillHooks:
    key: str
    directory: Path
    commands: tuple[CommandHook, ...]


def parse_hooks(raw: Any, path: Path) -> SkillHooks:
    commands: list[CommandHook] = []

    def command(event: Any, definition: Any, matcher: Any = None) -> None:
        if not isinstance(event, str) or event not in EVENTS:
            raise invalid(f"The skill hook event {event!r} has no supported execution point in a turn.")
        if not isinstance(definition, dict) or any(not isinstance(key, str) for key in definition):
            raise invalid("A skill command hook must be a mapping.")
        unknown = set(definition) - {"event", "type", "command", "timeout", "matcher"}
        if unknown:
            raise invalid(f"Unsupported skill hook fields: {', '.join(sorted(unknown))}.")
        if definition.get("type", "command") != "command":
            raise invalid("Only command skill hooks have an approved executor.")
        text = definition.get("command")
        timeout = definition.get("timeout", 30)
        if not isinstance(text, str) or not text.strip():
            raise invalid("A skill command hook requires a nonempty command.")
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 120:
            raise invalid("A skill command hook timeout must be an integer from 1 through 120.")
        matcher = definition.get("matcher", matcher)
        if matcher is not None and not isinstance(matcher, str):
            raise invalid("A skill hook matcher must be a tool-name expression.")
        try:
            pattern = re.compile(matcher) if matcher and matcher != "*" else None
        except re.error as error:
            raise invalid("A skill hook matcher is not a valid regular expression.") from error
        commands.append(CommandHook(EVENTS[event], text, pattern, timeout))

    if raw is None:
        return SkillHooks(str(path), path.parent, ())
    if not isinstance(raw, dict):
        raise invalid("Skill hooks must be a mapping of events to commands.")
    for event, groups in raw.items():
        if not isinstance(event, str) or event not in {*EVENTS, "shell"}:
            raise invalid(f"The skill hook event {event!r} has no supported execution point in a turn.")
        if not isinstance(groups, list):
            raise invalid(f"The skill hook {event!r} must contain a list.")
        for group in groups:
            if event == "shell":
                command(group.get("event") if isinstance(group, dict) else None, group)
                continue
            if not isinstance(group, dict) or set(group) - {"matcher", "hooks"}:
                raise invalid("A skill hook group accepts matcher and hooks only.")
            nested = group.get("hooks")
            if not isinstance(nested, list) or not nested:
                raise invalid("A skill hook group requires at least one command.")
            for item in nested:
                command(event, item, group.get("matcher"))
    return SkillHooks(str(path), path.parent, tuple(commands))


def hook_context(stdout: str, event: str) -> str | None:
    text = stdout.strip()
    if not text:
        return None
    if not text.startswith("{"):
        return text
    try:
        def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError(f"Repeated field: {key}")
                result[key] = value
            return result

        value = json.loads(text, object_pairs_hook=unique)
        strict_json(value, "skill.command.result")
    except (ValueError, TypeError, AgentError) as error:
        raise ToolFailed("The skill command returned malformed JSON.") from error
    if not isinstance(value, dict):
        raise ToolFailed("The skill command must return a JSON object or plain text.")
    unknown = set(value) - {"continue", "stopReason", "decision", "reason", "systemMessage",
                            "suppressOutput", "hookSpecificOutput"}
    if unknown:
        raise ToolFailed(f"Unsupported skill hook result fields: {', '.join(sorted(unknown))}.")
    specific = value.get("hookSpecificOutput", {})
    if not isinstance(specific, dict):
        raise ToolFailed("The skill command's hookSpecificOutput must be an object.")
    unknown = set(specific) - {"hookEventName", "permissionDecision", "permissionDecisionReason",
                               "additionalContext"}
    if unknown:
        raise ToolFailed(f"Unsupported skill hook result fields: {', '.join(sorted(unknown))}.")
    decision = value.get("decision")
    permission = specific.get("permissionDecision")
    if decision not in (None, "approve", "block") or permission not in (None, "allow", "deny", "ask"):
        raise ToolFailed("The skill command returned an unknown decision.")
    if "continue" in value and not isinstance(value["continue"], bool):
        raise ToolFailed("The skill command's continue field must be a boolean.")
    if "suppressOutput" in value and not isinstance(value["suppressOutput"], bool):
        raise ToolFailed("The skill command's suppressOutput field must be a boolean.")
    for field in ("stopReason", "reason", "systemMessage"):
        if field in value and not isinstance(value[field], str):
            raise ToolFailed(f"The skill command's {field} field must be text.")
    for field in ("hookEventName", "permissionDecisionReason", "additionalContext"):
        if field in specific and not isinstance(specific[field], str):
            raise ToolFailed(f"The skill command's {field} field must be text.")
    if specific.get("hookEventName", event) != event:
        raise ToolFailed("The skill command returned a result for a different event.")
    if value.get("continue") is False or decision == "block" or permission == "deny":
        reason = value.get("stopReason") or value.get("reason") or specific.get("permissionDecisionReason")
        raise ToolFailed(reason if isinstance(reason, str) else "A skill command blocked the work.")
    context = specific.get("additionalContext", value.get("systemMessage"))
    if context is not None and not isinstance(context, str):
        raise ToolFailed("The skill command's additional context must be text.")
    return context


class HookScope:
    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime
        self.automatic: tuple[SkillHooks, ...] = ()
        self.automatic_tools: dict[str, tuple[str, ...] | None] = {}
        self.automatic_selection: tuple[dict[str, Any], ...] = ()
        self.active: dict[str, SkillHooks] = {}
        self.restrictions: dict[str, tuple[str, ...]] = {}

    def activate(self, hooks: SkillHooks, tools: tuple[str, ...] | None = None) -> None:
        if hooks.commands:
            self.active[hooks.key] = hooks
        if tools is not None:
            self.restrictions[hooks.key] = tools

    def tools(self) -> set[str]:
        available = set(self.runtime.registry.tools)
        for restriction in self.restrictions.values():
            available.intersection_update(restriction)
        return available

    def validate_tools(self) -> None:
        for restriction in self.automatic_tools.values():
            unknown = set(restriction or ()) - self.runtime.registry.tools.keys()
            if unknown:
                raise invalid(f"Automatic skill commands request unavailable tools: {', '.join(sorted(unknown))}.")

    def begin(self, inherited: tuple[SkillHooks, ...]) -> None:
        self.active.clear()
        self.restrictions.clear()
        for hooks in (*inherited, *self.automatic):
            self.activate(hooks, self.automatic_tools.get(hooks.key))

    async def run(self, event: str, *, name: str | None = None,
                  arguments: dict[str, Any] | None = None, response: str | None = None) -> None:
        runtime = self.runtime
        for scope in tuple(self.active.values()):
            for hook in scope.commands:
                if hook.event != event or (hook.matcher and not hook.matcher.search(name or "")):
                    continue
                if "bash" not in self.tools():
                    raise invalid("A skill command requires bash outside the inherited tool set.")
                data: dict[str, Any] = {"hook_event_name": event,
                                       "cwd": str(runtime.config.working_directory)}
                if name is not None:
                    data.update(tool_name=name, tool_input=arguments)
                if response is not None:
                    try:
                        data["tool_response"] = json.loads(response)
                    except ValueError:
                        data["tool_response"] = response
                shell = bash_tool(runtime, directory=scope.directory,
                                  stdin=json.dumps(data, ensure_ascii=False),
                                  environment={"AMPLIFIER_SKILL_DIR": str(scope.directory),
                                               "CLAUDE_SKILL_DIR": str(scope.directory)})
                contexts: list[str] = []

                async def handler(arguments: dict[str, Any], context: ToolContext) -> str:
                    result = await shell.handler(arguments, context)
                    addition = hook_context(json.loads(result)["stdout"], event)
                    if addition:
                        contexts.append(addition)
                    return result

                preview = {**(shell.approval_context or {}), "hook_event_name": event}
                if name is not None:
                    preview.update(tool_name=name, tool_input=arguments)
                tool = replace(shell, handler=handler, approval_context=preview, guard=True)
                await runtime.require_observer().call_tool(
                    tool, str(uuid.uuid4()), {"command": hook.command, "timeout": hook.timeout},
                )
                runtime.context.skill_context.extend(contexts)

    def clear(self) -> None:
        self.active.clear()
        self.restrictions.clear()

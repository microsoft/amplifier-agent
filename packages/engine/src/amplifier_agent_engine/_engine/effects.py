"""Authorize tool effects and preserve their authoritative resolutions."""

from __future__ import annotations

import asyncio
import copy
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from jsonschema import ValidationError
from jsonschema.validators import validator_for

from .._records import (
    AgentError,
    ApprovalDecision,
    ApprovalRequest,
    ApprovalRequestEvent,
    ApprovalResolution,
    ApprovalResponse,
    Tool,
    ToolCall,
    ToolCallEvent,
    ToolContext,
    ToolFailed,
    ToolOutcomeUnknown,
    ToolResolution,
    ToolResultEvent,
    _ToolNotExecuted,
)
from .approval_summaries import approval_summary
from .configuration import ResolvedConfig, strict_json
from .tools import CapturedToolFailed, CapturedToolUnknown, RegisteredTool

APPROVAL_TIMEOUT_SECONDS = 120


class PolicyStop(BaseException):
    """Unwind an upstream operation while retaining an owned failure record."""


@dataclass
class RecoveryState:
    uncertain_call: str | None = None
    restricted: asyncio.Event = field(default_factory=asyncio.Event)
    executing: dict[asyncio.Task[Any], int] = field(default_factory=dict)


def bounded_result(content: str, ceiling: int | None) -> tuple[str, int | None]:
    """Keep at most `ceiling` UTF-8 bytes, cut at a character boundary, and name the loss.

    Returns the content to carry and the original byte length when it was shortened.
    """
    encoded = content.encode()
    if ceiling is None or len(encoded) <= ceiling:
        return content, None
    kept = encoded[:ceiling].decode(errors="ignore")
    marker = f"...[tool output reached limit: kept {len(kept.encode())} of {len(encoded)} bytes]"
    return f"{kept}\n{marker}", len(encoded)


def resolution_text(resolution: ToolResolution) -> str:
    if resolution.outcome == "completed":
        return resolution.content or ""
    return json.dumps({
        "call_id": resolution.call_id,
        "outcome": resolution.outcome,
        "content": resolution.content,
        "error": vars(resolution.error) if resolution.error else None,
    }, ensure_ascii=False, allow_nan=False)


class EffectTurn(Protocol):
    cancelled: bool
    recovery: RecoveryState

    def emit(self, name: str, payload: Any) -> None: ...
    def stop(self, state: str, error: AgentError) -> PolicyStop: ...


async def approval_reply(
    authority: Callable[[ApprovalRequest], Awaitable[ApprovalResponse]],
    request: ApprovalRequest,
    recovery: RecoveryState | None,
) -> tuple[Any, bool]:
    if recovery is None:
        return await authority(copy.deepcopy(request)), False
    callback = asyncio.ensure_future(authority(copy.deepcopy(request)))
    restricted = asyncio.create_task(recovery.restricted.wait())
    try:
        done, _ = await asyncio.wait((callback, restricted), return_when=asyncio.FIRST_COMPLETED)
        if callback in done:
            return await callback, False
        return None, True
    finally:
        for task in (callback, restricted):
            if not task.done():
                task.cancel()
        await asyncio.gather(callback, restricted, return_exceptions=True)


async def execute_tool(
    turn: EffectTurn,
    config: ResolvedConfig,
    tool: Tool | RegisteredTool,
    call_id: str,
    arguments: dict[str, Any],
) -> ToolResolution:
    if turn.cancelled:
        raise asyncio.CancelledError
    strict_json(arguments, "tool.arguments")
    try:
        validator_for(tool.input_schema)(tool.input_schema).validate(arguments)
    except ValidationError as exc:
        error = AgentError(
            "invalid_input", "input", f"Arguments for {tool.name} do not match its schema.",
            "Supply the required fields and types declared by the tool.",
            correlation_id=call_id,
            details={"path": list(exc.absolute_path), "validator": exc.validator},
        )
        raise turn.stop("failure", error) from exc
    calls = getattr(turn, "_effect_call_ids", None)
    if calls is None:
        calls = set()
        setattr(turn, "_effect_call_ids", calls)
    if not isinstance(call_id, str) or not call_id or call_id in calls:
        raise turn.stop("failure", AgentError(
            "tool_result_invalid", "executor", "A tool call has an invalid correlation id.",
            "Use an executor that assigns each call a unique nonempty id.",
            correlation_id=call_id if isinstance(call_id, str) else None,
        ))
    calls.add(call_id)
    arguments = copy.deepcopy(arguments)
    source = tool.source if isinstance(tool, RegisteredTool) else "caller"
    deadline = tool.deadline if isinstance(tool, RegisteredTool) else None
    turn.emit("tool_call", ToolCallEvent(ToolCall(
        call_id, tool.name, source, arguments, deadline,
    )))

    def restriction() -> AgentError | None:
        if turn.recovery.uncertain_call is None or (
            isinstance(tool, RegisteredTool) and tool.read_only_inspection and not tool.guard
        ):
            return None
        return AgentError(
            "tool_recovery_blocked", "executor",
            "An earlier tool outcome is unknown; this turn permits only local read-only inspection.",
            "Inspect the uncertain effect before requesting further work in a new turn.",
            correlation_id=call_id,
            details={"uncertain_call_id": turn.recovery.uncertain_call},
        )

    if blocked := restriction():
        turn.emit("tool_result", ToolResultEvent(ToolResolution(call_id, "cancelled", error=blocked)))
        raise turn.stop("failure", blocked)
    request = ApprovalRequest(
        str(uuid.uuid4()), approval_summary(
            source, tool.name, arguments,
            tool.approval_context if isinstance(tool, RegisteredTool) else None,
        ), call_id, tool.name,
    )
    turn.emit("approval_request", ApprovalRequestEvent(request))
    approval_settled = False
    tool_settled = False
    executing = False
    try:
        authority = config.approvals
        reason = None
        interrupted = False
        if callable(authority):
            try:
                async with asyncio.timeout(APPROVAL_TIMEOUT_SECONDS) as deadline_scope:
                    reply, interrupted = await approval_reply(
                        authority, request,
                        None if isinstance(tool, RegisteredTool) and tool.read_only_inspection
                        and not tool.guard else turn.recovery,
                    )
                if deadline_scope.expired():
                    decision = "timeout"
                elif interrupted:
                    decision = "cancel"
                elif (
                    not isinstance(reply, ApprovalResponse)
                    or reply.decision not in ("allow", "deny", "cancel")
                    or (reply.reason is not None and not isinstance(reply.reason, str))
                ):
                    decision = "invalid"
                else:
                    decision, reason = reply.decision, reply.reason
            except TimeoutError:
                decision = "timeout"
            except asyncio.CancelledError:
                raise
            except AgentError as error:
                decision = "unavailable" if error.code == "approval_unavailable" else "invalid"
            except Exception:
                decision = "invalid"
        else:
            decision = authority or "unavailable"
        turn.emit(
            "approval_decision",
            ApprovalDecision(ApprovalResolution(request.request_id, decision, reason)),
        )
        approval_settled = True
        if interrupted:
            blocked = restriction()
            assert blocked is not None
            turn.emit("tool_result", ToolResultEvent(ToolResolution(call_id, "cancelled", error=blocked)))
            tool_settled = True
            raise turn.stop("failure", blocked)
        if decision != "allow":
            states = {
                "deny": ("rejected", "approval_denied"),
                "cancel": ("cancelled", "approval_cancelled"),
                "timeout": ("failure", "approval_timeout"),
                "unavailable": ("failure", "approval_unavailable"),
                "invalid": ("failure", "approval_invalid"),
            }
            state, code = states[decision]
            error = AgentError(
                code,
                "approval",
                f"Approval resolved as {decision}.",
                "Provide an available approval handler returning allow, deny, or cancel, or set a static policy.",
                correlation_id=request.request_id,
            )
            turn.emit(
                "tool_result", ToolResultEvent(ToolResolution(call_id, "cancelled", error=error))
            )
            tool_settled = True
            raise turn.stop(state, error)
        if turn.cancelled:
            raise asyncio.CancelledError
        if blocked := restriction():
            turn.emit("tool_result", ToolResultEvent(ToolResolution(call_id, "cancelled", error=blocked)))
            tool_settled = True
            raise turn.stop("failure", blocked)
        executing = True
        task = asyncio.current_task()
        assert task is not None
        turn.recovery.executing[task] = turn.recovery.executing.get(task, 0) + 1
        error = None
        recoverable = False
        content = None
        outcome: Any = "completed"
        try:
            content = await tool.handler(copy.deepcopy(arguments), ToolContext(call_id, deadline))
            if not isinstance(content, str):
                outcome = "unknown"
                error = AgentError(
                    "tool_result_invalid",
                    "executor",
                    "The tool returned a non-text result.",
                    "Return a string from the tool handler.",
                    correlation_id=call_id,
                )
                content = None
        except _ToolNotExecuted as exc:
            outcome = "cancelled"
            error = AgentError(
                "tool_callback_failed", "executor",
                str(exc) or "The tool callback was cancelled before its executor started.",
                "Start another turn when the caller executor is available.",
                correlation_id=call_id,
            )
        except ToolFailed as exc:
            outcome = "failed"
            recoverable = True
            content = exc.content if isinstance(exc, CapturedToolFailed) else None
            error = AgentError(
                "tool_failed",
                "executor",
                str(exc) or "The tool reported failure.",
                "Correct the reported tool failure before trying again.",
                correlation_id=call_id,
            )
        except ToolOutcomeUnknown as exc:
            outcome = "unknown"
            recoverable = True
            content = exc.content if isinstance(exc, CapturedToolUnknown) else None
            error = AgentError(
                "tool_completion_unknown",
                "executor",
                str(exc) or "The effect outcome is unknown.",
                "Inspect the effect's actual outcome before attempting it again.",
                correlation_id=call_id,
            )
        except AgentError as exc:
            outcome = "unknown" if exc.code in (
                "tool_result_invalid", "tool_completion_unknown", "tool_callback_failed"
            ) else "failed"
            error = copy.deepcopy(exc)
            error.correlation_id = call_id
        except asyncio.CancelledError:
            raise
        except Exception:
            outcome = "unknown"
            error = AgentError(
                "tool_callback_failed",
                "executor",
                "The tool callback ended without an authoritative result.",
                "Inspect the effect's actual outcome and repair the callback before trying again.",
                correlation_id=call_id,
            )
        can_continue = (
            recoverable and config.tool_error_policy == "continue"
            and not (isinstance(tool, RegisteredTool) and tool.guard)
        )
        if can_continue and outcome == "unknown":
            if turn.recovery.uncertain_call is None:
                turn.recovery.uncertain_call = call_id
                turn.recovery.restricted.set()
            assert error is not None
            error.remedy = (
                "Inspect the effect's actual outcome before attempting it again. "
                "Only engine-provided read_file, glob, and grep inspection may run for the "
                "remainder of this turn; request further work in a new turn."
            )
        original_bytes = None
        if outcome == "completed" and content is not None:
            content, original_bytes = bounded_result(content, config.tool_result_max_bytes)
        resolution = ToolResolution(
            call_id, outcome, content, error,
            truncated=original_bytes is not None, original_bytes=original_bytes,
        )
        turn.emit("tool_result", ToolResultEvent(resolution))
        tool_settled = True
        if turn.cancelled:
            raise asyncio.CancelledError
        if error is not None and not can_continue:
            raise turn.stop("failure", error)
        return resolution
    except PolicyStop:
        if not approval_settled:
            turn.emit(
                "approval_decision",
                ApprovalDecision(ApprovalResolution(request.request_id, "cancel")),
            )
        if not tool_settled:
            turn.emit(
                "tool_result",
                ToolResultEvent(ToolResolution(call_id, "unknown" if executing else "cancelled")),
            )
        raise
    except asyncio.CancelledError:
        if not approval_settled:
            turn.emit(
                "approval_decision",
                ApprovalDecision(ApprovalResolution(request.request_id, "cancel")),
            )
        if not tool_settled:
            turn.emit(
                "tool_result",
                ToolResultEvent(ToolResolution(call_id, "unknown" if executing else "cancelled")),
            )
        raise
    finally:
        if executing:
            task = asyncio.current_task()
            if task is not None:
                depth = turn.recovery.executing[task] - 1
                if depth:
                    turn.recovery.executing[task] = depth
                else:
                    del turn.recovery.executing[task]

"""Authorize caller effects and preserve their authoritative resolutions."""

from __future__ import annotations

import asyncio
import copy
import uuid
from typing import Any, Protocol

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
)
from .configuration import ResolvedConfig, strict_json


class PolicyStop(BaseException):
    """Unwind an upstream operation while retaining an owned failure record."""


class EffectTurn(Protocol):
    cancelled: bool

    def emit(self, name: str, payload: Any) -> None: ...
    def stop(self, state: str, error: AgentError) -> PolicyStop: ...


async def execute_tool(
    turn: EffectTurn, config: ResolvedConfig, tool: Tool, call_id: str, arguments: dict[str, Any]
) -> str:
    if turn.cancelled:
        raise asyncio.CancelledError
    strict_json(arguments, "tool.arguments")
    arguments = copy.deepcopy(arguments)
    turn.emit("tool_call", ToolCallEvent(ToolCall(call_id, tool.name, "caller", arguments)))
    request = ApprovalRequest(
        str(uuid.uuid4()), f"Run caller tool {tool.name}.", call_id, tool.name
    )
    turn.emit("approval_request", ApprovalRequestEvent(request))
    approval_settled = False
    tool_settled = False
    executing = False
    try:
        authority = config.approvals
        reason = None
        if callable(authority):
            try:
                reply = await authority(copy.deepcopy(request))
                if (
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
            except Exception:
                decision = "invalid"
        else:
            decision = authority or "unavailable"
        turn.emit(
            "approval_decision",
            ApprovalDecision(ApprovalResolution(request.request_id, decision, reason)),
        )
        approval_settled = True
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
        executing = True
        error = None
        content = None
        outcome: Any = "completed"
        try:
            content = await tool.handler(copy.deepcopy(arguments), ToolContext(call_id))
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
        except ToolFailed as exc:
            outcome = "failed"
            error = AgentError(
                "tool_failed",
                "executor",
                str(exc) or "The tool reported failure.",
                "Correct the reported tool failure before trying again.",
                correlation_id=call_id,
            )
        except ToolOutcomeUnknown as exc:
            outcome = "unknown"
            error = AgentError(
                "tool_completion_unknown",
                "executor",
                str(exc) or "The effect outcome is unknown.",
                "Inspect the effect's actual outcome before attempting it again.",
                correlation_id=call_id,
            )
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
        turn.emit("tool_result", ToolResultEvent(ToolResolution(call_id, outcome, content, error)))
        tool_settled = True
        if error is not None:
            raise turn.stop("failure", error)
        assert isinstance(content, str)
        return content
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

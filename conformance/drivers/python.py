"""Exercise deterministic scenarios exclusively through public Python operations."""

import os
from typing import Any

from amplifier_agent import (
    AgentOptions,
    ApprovalResponse,
    ConversationMessage,
    SessionOptions,
    TextPart,
    Tool,
    ToolFailed,
    ToolOutcomeUnknown,
    TurnInput,
    create_agent,
)


async def run(case: dict[str, Any], probe: Any) -> dict[str, Any]:
    effects = []
    callback_pids = []
    approvals = []

    async def counter(arguments, context):
        callback_pids.append(os.getpid())
        effects.append({"call_id": context.call_id, "value": arguments["value"]})
        if case.get("tool_error") == "tool_failed":
            raise ToolFailed("The counter rejected the operation.")
        if case.get("tool_error") == "tool_completion_unknown":
            raise ToolOutcomeUnknown("The counter outcome cannot be established.")
        if size := case.get("tool_result_size"):
            return "x" * size
        return str(arguments["value"])

    async def approve(request):
        approvals.append(request.request_id)
        callback_pids.append(os.getpid())
        return ApprovalResponse(decision="allow")

    policy = approve if case.get("approvals") == "handler" else case.get("approvals")
    tools = (
        [
            Tool(
                "counter",
                "Record a value",
                {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "type": "object",
                    "properties": {"value": {"type": "integer"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
                counter,
            )
        ]
        if case.get("tool")
        else []
    )
    options = AgentOptions(
        provider="anthropic",
        model="claude-sonnet-5",
        instructions="Server instructions",
        tools=tools,
        approvals=policy,
        tool_error_policy=case.get("tool_error_policy", "stop"),
        tool_result_max_bytes=case.get("tool_result_max_bytes", 262_144),
    )
    input = case["input"]
    turn_input = TurnInput(
        content=[TextPart(**part) for part in input["content"]],
        history=[
            ConversationMessage(
                role=message["role"], content=[TextPart(**part) for part in message["content"]]
            )
            for message in input["history"]
        ]
        if "history" in input
        else None,
    )
    events = []
    deltas = []
    delta_parts = []
    calls = []
    resolutions = []
    async with await create_agent(options) as agent:
        async with await agent.create_session(SessionOptions(persistence="ephemeral")) as session:
            turn = await session.start_turn(turn_input)
            async for event in turn.events():
                events.append(
                    {
                        "type": event.type,
                        "sequence": event.sequence,
                        "contract_version": event.contract_version,
                        "session_id": event.session_id,
                        "turn_id": event.turn_id,
                    }
                )
                if event.type == "output_delta":
                    deltas.append("".join(part.text for part in event.payload.content))
                    delta_parts.extend(
                        {"type": part.type, "text": part.text} for part in event.payload.content
                    )
                if event.type == "tool_call":
                    calls.append(event.payload.call.call_id)
                if event.type == "tool_result":
                    resolution = event.payload.resolution
                    resolutions.append({
                        "call_id": resolution.call_id,
                        "outcome": resolution.outcome,
                        "code": resolution.error.code if resolution.error else None,
                        "message": resolution.error.message if resolution.error else None,
                        "remedy": resolution.error.remedy if resolution.error else None,
                        "content": resolution.content,
                        "truncated": resolution.truncated,
                        "original_bytes": resolution.original_bytes,
                    })
                if event.type == case.get("cancel_after"):
                    await turn.cancel()
                if event.type == "terminal":
                    result = event.payload
    error = result.error
    return {
        "content": [{"type": part.type, "text": part.text} for part in result.content or []],
        "delta_parts": delta_parts,
        "state": result.state,
        "text": "".join(part.text for part in result.content or []),
        "code": error.code if error else None,
        "deltas": deltas,
        "effects": len(effects),
        "callbacks": len(effects),
        "approvals": len(approvals),
        "pid": os.getpid(),
        "callback_pids": callback_pids,
        "events": events,
        "calls": calls,
        "resolutions": resolutions,
        "outcomes": [resolution["outcome"] for resolution in resolutions],
        "tool_codes": [resolution["code"] for resolution in resolutions],
        "tool_contents": [resolution["content"] for resolution in resolutions],
        "truncated": [resolution["truncated"] for resolution in resolutions],
        "original_bytes": [resolution["original_bytes"] for resolution in resolutions],
        "provider_requests": len(getattr(probe, "requests", [])),
        "active_provider": probe.active,
        "error": {
            "code": error.code,
            "category": error.category,
            "message": error.message,
            "remedy": error.remedy,
            "retryable": error.retryable,
        }
        if error
        else None,
    }

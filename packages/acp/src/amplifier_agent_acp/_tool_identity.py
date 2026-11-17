"""Project the engine-owned tool extension without interpreting model arguments."""

import copy
import json
from typing import Any

from acp.schema import ToolCallStart, ToolCallUpdate
from amplifier_agent import ApprovalRequest, ToolCall

NAMESPACE = "com.microsoft.amplifier.tool"


def capability() -> dict[str, Any]:
    return {NAMESPACE: {"version": 1, "rawInputShape": "arguments"}}


def identity(value: ToolCall | ApprovalRequest) -> dict[str, Any] | None:
    data = getattr(value, NAMESPACE, None)
    if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1:
        return None
    kind, name = data.get("kind"), data.get("name")
    if not isinstance(name, str) or not name:
        return None
    if kind == "builtin":
        if set(data) != {"version", "kind", "name"}:
            return None
        if isinstance(value, ToolCall) and (value.source != "built-in" or value.name != name):
            return None
    elif kind == "mcp":
        if set(data) != {"version", "kind", "server", "name"}:
            return None
        if not isinstance(data["server"], str) or not data["server"]:
            return None
        if isinstance(value, ToolCall) and value.source != "mcp":
            return None
    else:
        return None
    return copy.deepcopy(data)


def permission(request: ApprovalRequest, shown: ToolCallStart) -> ToolCallUpdate | None:
    expected = (shown.field_meta or {}).get(NAMESPACE)
    if (request.call_id or request.request_id) != shown.tool_call_id or identity(request) != expected:
        return None
    if request.call_id is not None and request.name != shown.name:
        return None
    try:
        if json.dumps(request.arguments, sort_keys=True, allow_nan=False) != json.dumps(
            shown.raw_input, sort_keys=True, allow_nan=False
        ):
            return None
    except (TypeError, ValueError):
        return None
    return ToolCallUpdate(
        tool_call_id=shown.tool_call_id,
        title=shown.title,
        kind=shown.kind,
        status="pending",
        raw_input=copy.deepcopy(shown.raw_input),
        field_meta={NAMESPACE: copy.deepcopy(expected)} if expected is not None else None,
    )

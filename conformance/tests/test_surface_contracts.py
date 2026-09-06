import dataclasses
import inspect
import json
from collections.abc import AsyncIterator
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, get_type_hints

import amplifier_agent as binding
import pytest

from conformance.surface import check as surface

RECORD_FIELDS = {
    "TextPart": "text type",
    "ConversationMessage": "role content",
    "TurnInput": "content model history",
    "TurnResult": "state content error usage",
    "SessionRecord": "session_id persistence",
    "TurnInfo": "session_id turn_id",
    "TurnRecord": "turn_id input result",
    "Event": "contract_version session_id turn_id sequence type payload at",
    "Usage": "entries",
    "UsageEntry": "provider model tokens_in tokens_out cache_read_tokens cache_write_tokens cost",
    "ToolContext": "call_id deadline",
    "Tool": "name description input_schema handler safety",
    "ToolCall": "call_id name source arguments deadline",
    "ToolResolution": "call_id outcome content error",
    "McpServer": "name transport command args env url headers",
    "ApprovalRequest": "request_id summary call_id name",
    "ApprovalResponse": "decision reason",
    "AgentOptions": "provider model instructions tools skills mcp_servers storage approvals tool_error_policy",
    "SessionOptions": "session_id persistence model",
    "Selection": "provider model",
    "TurnStarted": "continuation primary_actual",
    "OutputDelta": "content",
    "ReasoningDelta": "text",
    "ReasoningFinal": "text",
    "ToolCallEvent": "call",
    "ToolResultEvent": "resolution",
    "ApprovalRequestEvent": "request",
    "ApprovalResolution": "request_id decision reason",
    "ApprovalDecision": "resolution",
    "Progress": "data",
    "UsageEvent": "snapshot",
}

OPTIONAL_FIELDS = {
    "TextPart": {"type": "text"},
    "TurnInput": {"model": None, "history": None},
    "TurnResult": {"content": None, "error": None, "usage": None},
    "Event": {"at": None},
    "UsageEntry": dict.fromkeys("tokens_in tokens_out cache_read_tokens cache_write_tokens cost".split()),
    "ToolContext": {"deadline": None},
    "Tool": {"safety": None},
    "ToolCall": {"deadline": None},
    "ToolResolution": {"content": None, "error": None},
    "McpServer": dict.fromkeys("command args env url headers".split()),
    "ApprovalRequest": {"call_id": None, "name": None},
    "ApprovalResponse": {"reason": None},
    "AgentOptions": {
        **dict.fromkeys("provider model instructions tools skills mcp_servers storage approvals".split()),
        "tool_error_policy": "stop",
    },
    "SessionOptions": {"session_id": None, "model": None, "persistence": "durable"},
    "ApprovalResolution": {"reason": None},
}

FIELD_TYPES = {
    "TextPart": {"text": str, "type": Literal["text"]},
    "ConversationMessage": {
        "role": Literal["system", "developer", "user", "assistant"],
        "content": list[binding.ContentPart],
    },
    "TurnInput": {
        "content": list[binding.ContentPart], "model": str | None,
        "history": list[binding.ConversationMessage] | None,
    },
    "TurnResult": {
        "state": Literal["success", "failure", "rejected", "cancelled"],
        "content": list[binding.ContentPart] | None, "error": binding.AgentError | None,
        "usage": binding.Usage | None,
    },
    "SessionRecord": {"session_id": str, "persistence": Literal["durable", "ephemeral"]},
    "TurnInfo": {"session_id": str, "turn_id": str},
    "TurnRecord": {"turn_id": str, "input": binding.TurnInput, "result": binding.TurnResult},
    "Event": {
        "contract_version": str, "session_id": str, "turn_id": str, "sequence": int,
        "type": str, "payload": Any, "at": datetime | None,
    },
    "Usage": {"entries": list[binding.UsageEntry]},
    "UsageEntry": {
        "provider": str, "model": str, "tokens_in": int | None, "tokens_out": int | None,
        "cache_read_tokens": int | None, "cache_write_tokens": int | None,
        "cost": dict[str, Decimal] | None,
    },
    "ToolContext": {"call_id": str, "deadline": datetime | None},
    "Tool": {
        "name": str, "description": str, "input_schema": dict[str, Any],
        "handler": binding.ToolHandler, "safety": dict[str, Any] | None,
    },
    "ToolCall": {
        "call_id": str, "name": str, "source": Literal["built-in", "caller", "mcp"],
        "arguments": dict[str, Any], "deadline": datetime | None,
    },
    "ToolResolution": {
        "call_id": str, "outcome": Literal["completed", "failed", "cancelled", "unknown"],
        "content": str | None, "error": binding.AgentError | None,
    },
    "McpServer": {
        "name": str, "transport": Literal["stdio", "http"], "command": str | None,
        "args": list[str] | None, "env": dict[str, str] | None,
        "url": str | None, "headers": dict[str, str] | None,
    },
    "ApprovalRequest": {"request_id": str, "summary": str, "call_id": str | None, "name": str | None},
    "ApprovalResponse": {"decision": Literal["allow", "deny", "cancel"], "reason": str | None},
    "AgentOptions": {
        "provider": str | None, "model": str | None, "instructions": str | None,
        "tools": list[binding.Tool] | None, "skills": list[str] | None,
        "mcp_servers": list[binding.McpServer] | None, "storage": str | Path | None,
        "approvals": binding.ApprovalHandler | Literal["allow", "deny"] | None,
        "tool_error_policy": Literal["stop", "continue"],
    },
    "SessionOptions": {
        "session_id": str | None, "persistence": Literal["durable", "ephemeral"], "model": str | None,
    },
    "Selection": {"provider": str, "model": str},
    "TurnStarted": {"continuation": Literal["fresh", "resumed"], "primary_actual": binding.Selection},
    "OutputDelta": {"content": list[binding.ContentPart]},
    "ReasoningDelta": {"text": str},
    "ReasoningFinal": {"text": str},
    "ToolCallEvent": {"call": binding.ToolCall},
    "ToolResultEvent": {"resolution": binding.ToolResolution},
    "ApprovalRequestEvent": {"request": binding.ApprovalRequest},
    "ApprovalResolution": {"request_id": str, "decision": str, "reason": str | None},
    "ApprovalDecision": {"resolution": binding.ApprovalResolution},
    "Progress": {"data": Any},
    "UsageEvent": {"snapshot": binding.Usage},
}

OPERATIONS = {
    "Agent": {
        "create_session": ({"options": binding.SessionOptions | None}, binding.Session),
        "resume_session": ({"session_id": str}, binding.Session),
        "list_sessions": ({}, list[binding.SessionRecord]),
        "delete_session": ({"session_id": str}, type(None)),
        "close": ({}, type(None)),
    },
    "Session": {
        "info": ({}, binding.SessionRecord), "history": ({}, list[binding.TurnRecord]),
        "run": ({"input": binding.TurnInput}, binding.TurnResult),
        "start_turn": ({"input": binding.TurnInput}, binding.Turn),
        "fork": ({}, binding.Session), "close": ({}, type(None)),
    },
    "Turn": {"info": ({}, binding.TurnInfo), "events": ({}, AsyncIterator[binding.Event]),
             "cancel": ({}, type(None))},
}


def record_errors(name, record):
    fields = dataclasses.fields(record)
    errors = []
    if {field.name for field in fields} != set(RECORD_FIELDS[name].split()):
        errors.append("fields")
    if get_type_hints(record) != FIELD_TYPES[name]:
        errors.append("types")
    for field in fields:
        default = OPTIONAL_FIELDS.get(name, {}).get(field.name, dataclasses.MISSING)
        if field.default != default or field.default_factory is not dataclasses.MISSING:
            errors.append(f"default:{field.name}")
    if name in {"SessionRecord", "TurnInfo", "ToolContext"} and not record.__dataclass_params__.frozen:
        errors.append("read-only")
    return errors


def operation_errors(name, handle):
    expected = OPERATIONS[name]
    members = {key: value for key, value in inspect.getmembers(handle) if not key.startswith("_")}
    errors = []
    if members.keys() != expected.keys():
        errors.append("members")
    for key in members.keys() & expected.keys():
        member = members[key]
        readonly = key in {"info", "history"}
        if readonly:
            if not isinstance(member, property) or member.fset is not None:
                errors.append(f"read-only:{key}")
                continue
            function = member.fget
        else:
            function = member
        arguments, result = expected[key]
        parameters = dict(inspect.signature(function).parameters)
        parameters.pop("self", None)
        if parameters.keys() != arguments.keys():
            errors.append(f"arguments:{key}")
        if get_type_hints(function) != {**arguments, "return": result}:
            errors.append(f"types:{key}")
        for parameter in parameters.values():
            default = None if (name, key, parameter.name) == ("Agent", "create_session", "options") else inspect.Parameter.empty
            if parameter.default != default:
                errors.append(f"default:{key}")
        if inspect.iscoroutinefunction(function) != (not readonly and key != "events"):
            errors.append(f"async:{key}")
    return errors


@pytest.mark.parametrize("name", RECORD_FIELDS)
def test_public_record_shapes_types_and_defaults(name):
    assert record_errors(name, getattr(binding, name)) == []


@pytest.mark.parametrize("name", OPERATIONS)
def test_public_operation_shapes(name):
    assert operation_errors(name, getattr(binding, name)) == []
    with pytest.raises(TypeError):
        getattr(binding, name)()


def test_factory_and_error_record():
    assert inspect.iscoroutinefunction(binding.create_agent)
    assert tuple(inspect.signature(binding.create_agent).parameters) == ("options",)
    assert get_type_hints(binding.create_agent) == {"options": binding.AgentOptions, "return": binding.Agent}
    error = binding.AgentError("invalid_input", "input", "Invalid value.", "Supply a valid value.")
    assert vars(error) == {
        "code": "invalid_input", "category": "input", "message": "Invalid value.",
        "remedy": "Supply a valid value.", "retryable": False,
        "correlation_id": None, "details": None,
    }
    assert get_type_hints(binding.AgentError.__init__) == {
        "code": str, "category": str, "message": str, "remedy": str, "retryable": bool,
        "correlation_id": str | None, "details": dict[str, Any] | None, "return": type(None),
    }


@pytest.mark.parametrize("mutation", ["missing", "extra", "retyped", "redefaulted", "required"])
def test_record_validator_rejects_contract_mutations(mutation):
    fields = [
        ("content", list[binding.ContentPart]),
        ("model", str | None, None),
        ("history", list[binding.ConversationMessage] | None, None),
    ]
    if mutation == "missing":
        fields.pop()
    elif mutation == "extra":
        fields.append(("engine_port", int, 9099))
    elif mutation == "retyped":
        fields[0] = ("content", str)
    elif mutation == "redefaulted":
        fields[1] = ("model", str | None, "invented-model")
    elif mutation == "required":
        fields[1] = ("model", str | None)
    assert record_errors("TurnInput", dataclasses.make_dataclass("MutatedInput", fields))


@pytest.mark.parametrize("mutation", ["missing", "extra", "sync", "overload"])
def test_operation_validator_rejects_contract_mutations(mutation):
    class Handle:
        info = binding.Turn.info
        events = binding.Turn.events
        cancel = binding.Turn.cancel

    if mutation == "missing":
        del Handle.cancel
    elif mutation == "extra":
        Handle.engine_port = 9099
    elif mutation == "sync":
        def cancel(self) -> None:
            pass
        Handle.cancel = cancel
    elif mutation == "overload":
        async def cancel(self, force: bool = False) -> None:
            pass
        Handle.cancel = cancel
    assert operation_errors("Turn", Handle)


def test_surface_lint_rejects_added_and_missing_exports(monkeypatch):
    assert surface.check() == []
    original = binding.__all__
    for exports in ([*original, "engine_port"], [name for name in original if name != "Turn"]):
        monkeypatch.setattr(binding, "__all__", exports)
        assert any("Export mismatch" in error for error in surface.check())


def test_binding_packages_ship_no_agent_command():
    import tomllib

    python = tomllib.loads((surface.ROOT / "packages/python/pyproject.toml").read_text())
    typescript = json.loads((surface.ROOT / "packages/typescript/package.json").read_text())
    assert not python["project"].get("scripts")
    assert not python["project"].get("gui-scripts")
    assert not typescript.get("bin")


def test_http_service_has_no_command_configuration_surface():
    import ast
    import tomllib

    from amplifier_agent_http.__main__ import main

    metadata = tomllib.loads((surface.ROOT / "packages/http/pyproject.toml").read_text())
    assert metadata["project"]["scripts"] == {"amplifier-agent-face": "amplifier_agent_http.__main__:main"}
    assert not inspect.signature(main).parameters
    launcher = ast.parse(inspect.getsource(main))
    assert not any(
        isinstance(node, ast.Attribute) and node.attr in {"argv", "parse_args", "parse_known_args", "add_argument"}
        for node in ast.walk(launcher)
    )


def test_host_configuration_key_registry():
    import ast

    source = surface.ROOT / "packages/engine/src/amplifier_agent_engine/_engine/configuration.py"
    registries = [
        ast.literal_eval(node.value)
        for node in ast.walk(ast.parse(source.read_text()))
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "registered" for target in node.targets)
    ]
    assert registries == [{"provider", "model", "storage", "workspace", "extra_request_params"}]


def test_published_python_mapping_resolves_operations_and_records():
    names = (surface.ROOT / "docs/python/names.md").read_text()
    operation_block = names.split("## Operations", 1)[1].split("```", 2)[1]
    expected_mapping = {
        f"{handle.lower()}.{operation}": f"{handle}.{operation}"
        for handle, operations in OPERATIONS.items() for operation in operations
    }
    expected_mapping.update({
        value: f"amplifier_agent.{value}"
        for value in ("create_agent", "contract_version", "contract_versions")
    })
    rows = [line.split() for line in operation_block.splitlines() if line.strip()]
    assert len(rows) == len(expected_mapping)
    assert all(len(row) == 2 for row in rows)
    assert dict(rows) == expected_mapping
    for handle, operations in OPERATIONS.items():
        for operation in operations:
            assert f"{handle.lower()}.{operation}" in names
            assert f"{handle}.{operation}" in names
            assert hasattr(getattr(binding, handle), operation)
    for record in RECORD_FIELDS:
        assert record in names
        assert hasattr(binding, record)
    for value in ("create_agent", "contract_version", "contract_versions"):
        assert f"amplifier_agent.{value}" in names
        assert hasattr(binding, value)

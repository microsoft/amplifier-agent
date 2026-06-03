"""Tests for TypedDict shapes for JSON-RPC method requests/responses — protocol/methods.py."""

from __future__ import annotations

import json


def test_protocol_version_constant() -> None:
    """PROTOCOL_VERSION is a non-empty string equal to '0.3.0'."""
    from amplifier_agent_lib.protocol.methods import PROTOCOL_VERSION

    assert isinstance(PROTOCOL_VERSION, str)
    assert PROTOCOL_VERSION != ""
    assert PROTOCOL_VERSION == "0.3.0"


def test_initialize_params_json_roundtrip() -> None:
    """InitializeParams is JSON-serializable and round-trips cleanly."""
    from amplifier_agent_lib.protocol.methods import InitializeParams

    params: InitializeParams = {
        "protocolVersion": "0.3.0",
        "clientInfo": {"name": "test-client", "version": "1.0.0"},
        "capabilities": {"streaming": True},
        "sessionId": "sess-abc123",
        "resume": False,
        "providerOverride": "anthropic",
        "cwd": "/home/user/project",
    }

    serialized = json.dumps(params)
    restored = json.loads(serialized)

    assert restored["protocolVersion"] == "0.3.0"
    assert restored["clientInfo"]["name"] == "test-client"
    assert restored["capabilities"]["streaming"] is True
    assert restored["sessionId"] == "sess-abc123"
    assert restored["cwd"] == "/home/user/project"


def test_initialize_params_minimal_json_roundtrip() -> None:
    """InitializeParams works with only required fields (NotRequired fields omitted)."""
    from amplifier_agent_lib.protocol.methods import InitializeParams

    params: InitializeParams = {
        "protocolVersion": "0.3.0",
        "clientInfo": {"name": "minimal-client", "version": "0.1.0"},
        "capabilities": {},
    }

    serialized = json.dumps(params)
    restored = json.loads(serialized)

    assert restored["protocolVersion"] == "0.3.0"
    assert "sessionId" not in restored
    assert "resume" not in restored
    assert "providerOverride" not in restored
    assert "cwd" not in restored


def test_initialize_result_json_roundtrip() -> None:
    """InitializeResult is JSON-serializable and round-trips cleanly."""
    from amplifier_agent_lib.protocol.methods import InitializeResult

    result: InitializeResult = {
        "capabilities": {"tools": True},
        "serverInfo": {"name": "amplifier-agent", "version": "2.0.0"},
        "sessionState": {"sessionId": "sess-xyz789", "resumed": False},
    }

    serialized = json.dumps(result)
    restored = json.loads(serialized)

    assert restored["capabilities"]["tools"] is True
    assert restored["serverInfo"]["name"] == "amplifier-agent"
    assert restored["sessionState"]["sessionId"] == "sess-xyz789"
    assert restored["sessionState"]["resumed"] is False


def test_turn_submit_params_minimal() -> None:
    """TurnSubmitParams works with only required fields."""
    from amplifier_agent_lib.protocol.methods import TurnSubmitParams

    params: TurnSubmitParams = {
        "sessionId": "sess-001",
        "turnId": "turn-001",
        "prompt": "Hello, world!",
    }

    serialized = json.dumps(params)
    restored = json.loads(serialized)

    assert restored["sessionId"] == "sess-001"
    assert restored["turnId"] == "turn-001"
    assert restored["prompt"] == "Hello, world!"
    assert "attachments" not in restored


def test_turn_submit_result_shape() -> None:
    """TurnSubmitResult serializes with reply and optional finalEvent."""
    from amplifier_agent_lib.protocol.methods import TurnSubmitResult

    # With reply, no finalEvent
    result_no_final: TurnSubmitResult = {
        "reply": "Hello back!",
        "turnId": "turn-001",
    }
    serialized = json.dumps(result_no_final)
    restored = json.loads(serialized)
    assert restored["reply"] == "Hello back!"
    assert restored["turnId"] == "turn-001"
    assert "finalEvent" not in restored

    # With reply=None and finalEvent
    result_with_final: TurnSubmitResult = {
        "reply": None,
        "turnId": "turn-002",
        "finalEvent": {"type": "complete", "data": {}},
    }
    serialized2 = json.dumps(result_with_final)
    restored2 = json.loads(serialized2)
    assert restored2["reply"] is None
    assert restored2["finalEvent"]["type"] == "complete"


def test_agent_shutdown_params_empty() -> None:
    """AgentShutdownParams is an empty TypedDict (can be instantiated as empty dict)."""
    from amplifier_agent_lib.protocol.methods import AgentShutdownParams, AgentShutdownResult

    params: AgentShutdownParams = {}
    result: AgentShutdownResult = {}

    assert json.dumps(params) == "{}"
    assert json.dumps(result) == "{}"


def test_cache_info_shapes() -> None:
    """CacheInfoParams is empty; CacheInfoResult has cachePath and preparedBundles."""
    from amplifier_agent_lib.protocol.methods import CacheInfoParams, CacheInfoResult

    params: CacheInfoParams = {}
    assert json.dumps(params) == "{}"

    result: CacheInfoResult = {
        "cachePath": "/tmp/cache/amplifier",
        "preparedBundles": ["foundation:explorer", "amplifier:amplifier-expert"],
    }
    serialized = json.dumps(result)
    restored = json.loads(serialized)
    assert restored["cachePath"] == "/tmp/cache/amplifier"
    assert len(restored["preparedBundles"]) == 2


def test_client_info_and_server_info() -> None:
    """ClientInfo and ServerInfo have name and version string fields."""
    from amplifier_agent_lib.protocol.methods import ClientInfo, ServerInfo

    client: ClientInfo = {"name": "my-client", "version": "1.2.3"}
    server: ServerInfo = {"name": "my-server", "version": "4.5.6"}

    assert json.loads(json.dumps(client))["name"] == "my-client"
    assert json.loads(json.dumps(server))["version"] == "4.5.6"


def test_session_state_resumed_field() -> None:
    """SessionState has sessionId and resumed bool fields."""
    from amplifier_agent_lib.protocol.methods import SessionState

    state_new: SessionState = {"sessionId": "sess-new", "resumed": False}
    state_resumed: SessionState = {"sessionId": "sess-old", "resumed": True}

    assert json.loads(json.dumps(state_new))["resumed"] is False
    assert json.loads(json.dumps(state_resumed))["resumed"] is True


def test_turn_cancel_typeddicts_removed() -> None:
    """TurnCancelParams and TurnCancelResult must not exist in protocol/methods.py (design D3)."""
    from amplifier_agent_lib.protocol import methods as _methods

    forbidden = ("TurnCancelParams", "TurnCancelResult")
    module_names = dir(_methods)
    for name in forbidden:
        assert name not in module_names, f"{name!r} must be removed from protocol/methods.py (design D3)"

"""Tests for session-resume via X-Client-Session-Id and client-authoritative reconciliation.

Covers:
- _reconciler.reconcile_client_history persists client view to store.
- Returned list is the same object (not a copy).
- Existing stored transcript is overwritten when client view differs.
- Empty client_messages list is handled without error.
- Route: deterministic ``http-<sid>`` when X-Client-Session-Id is present.
- Route: is_resumed=True on the second POST with the same header.
- Route: fresh random sid (None from route perspective) when header is absent.
- Route: client-edited history replaces stored transcript.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from amplifier_agent_http._reconciler import reconcile_client_history
from amplifier_agent_http.routes import chat_completions as cc_module
from amplifier_agent_lib.session_store import SessionStore

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

AUTH = {"Authorization": "Bearer test-key"}

_REGISTRY = {"claude-3-5-sonnet-20241022": "anthropic"}


def _make_test_app(
    *,
    registry: dict[str, str] | None = None,
    workspace: str | None = None,
) -> FastAPI:
    """Build a minimal FastAPI app for session-resume tests.

    Accepts an optional *workspace* to simulate a server that has
    ``resolved_workspace`` set (required for the X-Client-Session-Id
    deterministic-sid path).
    """
    prepared_mock = MagicMock()
    prepared_mock.mount_plan = {}
    state_registry = registry or {}

    @asynccontextmanager
    async def _noop_lifespan(application: FastAPI):
        application.state.config = MagicMock()
        application.state.config.model_id = "amplifier"
        application.state.config.api_key = "test-key"
        application.state.prepared = prepared_mock
        application.state.agent_configs = {}
        application.state.resolved_workspace = workspace
        application.state.host_config = {}
        application.state.available_models = []
        application.state.served_models_registry = state_registry
        yield

    app = FastAPI(lifespan=_noop_lifespan)
    app.include_router(cc_module.router)
    return app


def _chat_payload(model: str = "claude-3-5-sonnet-20241022", **kwargs: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": "hello"}],
    }
    base.update(kwargs)
    return base


# ---------------------------------------------------------------------------
# Unit tests: _reconciler.reconcile_client_history
# ---------------------------------------------------------------------------


def test_reconciler_persists_client_view_to_store(tmp_path: Path) -> None:
    """Calling reconcile_client_history persists the messages to the store with
    the expected metadata, and returns the same list."""
    store = SessionStore(tmp_path)
    msgs = [{"role": "user", "content": "hello"}]

    result = reconcile_client_history(
        client_messages=msgs,
        session_id="http-test-sid",
        store=store,
    )

    # Should be persisted on disk.
    loaded = store.load("http-test-sid")
    assert loaded is not None
    transcript, metadata = loaded
    assert transcript == msgs
    assert metadata == {"last_turn": "client_reconciled"}

    # Return value should be the same object (caller convenience).
    assert result is msgs


def test_reconciler_returns_client_messages_unchanged(tmp_path: Path) -> None:
    """The returned list IS the input list — not a copy."""
    store = SessionStore(tmp_path)
    msgs: list[dict[str, Any]] = [{"role": "user", "content": "unchanged"}]

    returned = reconcile_client_history(
        client_messages=msgs,
        session_id="http-identity-check",
        store=store,
    )
    assert returned is msgs


def test_reconciler_overwrites_existing_stored_transcript(tmp_path: Path) -> None:
    """When the store already has a different transcript for this sid,
    reconcile_client_history overwrites it with the client's view."""
    store = SessionStore(tmp_path)
    sid = "http-overwrite-test"

    # Seed an old transcript.
    store.save(sid, [{"role": "assistant", "content": "old"}], metadata={"last_turn": "old"})

    # Client sends new history.
    new_msgs = [{"role": "user", "content": "new turn"}]
    reconcile_client_history(client_messages=new_msgs, session_id=sid, store=store)

    loaded = store.load(sid)
    assert loaded is not None
    transcript, metadata = loaded
    assert transcript == new_msgs
    assert metadata["last_turn"] == "client_reconciled"


def test_reconciler_handles_empty_client_messages(tmp_path: Path) -> None:
    """An empty messages array is valid input and gets persisted as such."""
    store = SessionStore(tmp_path)
    sid = "http-empty-messages"

    result = reconcile_client_history(
        client_messages=[],
        session_id=sid,
        store=store,
    )

    assert result == []
    loaded = store.load(sid)
    assert loaded is not None
    transcript, _ = loaded
    assert transcript == []


# ---------------------------------------------------------------------------
# Integration tests: chat_completions route + session-resume
# ---------------------------------------------------------------------------


def test_chat_completions_route_uses_deterministic_sid_with_client_header(
    tmp_path: Path,
) -> None:
    """POST /v1/chat/completions with X-Client-Session-Id uses a deterministic
    ``http-<client_sid>`` as the amplifier session_id passed to run_chat_turn."""
    app = _make_test_app(registry=_REGISTRY, workspace="test-ws")
    captured: dict[str, Any] = {}

    async def _fake_run(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "ok"

    with (
        patch(
            "amplifier_agent_http.routes.chat_completions.run_chat_turn",
            side_effect=_fake_run,
        ),
        patch(
            "amplifier_agent_http.routes.chat_completions.workspaces_root",
            return_value=tmp_path,
        ),
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        resp = client.post(
            "/v1/chat/completions",
            json=_chat_payload(),
            headers={**AUTH, "X-Client-Session-Id": "mysession123"},
        )

    assert resp.status_code == 200
    assert captured.get("session_id") == "http-mysession123"


def test_chat_completions_route_resumes_on_second_request(
    tmp_path: Path,
) -> None:
    """Second POST with the same X-Client-Session-Id passes is_resumed=True to
    run_chat_turn after the first call has persisted the session state dir."""
    app = _make_test_app(registry=_REGISTRY, workspace="test-ws")
    captured_calls: list[dict[str, Any]] = []

    async def _fake_run(**kwargs: Any) -> str:
        captured_calls.append(dict(kwargs))
        return "ok"

    with (
        patch(
            "amplifier_agent_http.routes.chat_completions.run_chat_turn",
            side_effect=_fake_run,
        ),
        patch(
            "amplifier_agent_http.routes.chat_completions.workspaces_root",
            return_value=tmp_path,
        ),
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        # First turn — session state dir does not exist yet.
        resp1 = client.post(
            "/v1/chat/completions",
            json=_chat_payload(),
            headers={**AUTH, "X-Client-Session-Id": "session-resume-test"},
        )
        assert resp1.status_code == 200
        assert captured_calls[0]["is_resumed"] is False

        # Second turn — state dir was created by the first turn's reconciler call.
        resp2 = client.post(
            "/v1/chat/completions",
            json=_chat_payload(),
            headers={**AUTH, "X-Client-Session-Id": "session-resume-test"},
        )
        assert resp2.status_code == 200
        assert captured_calls[1]["is_resumed"] is True


def test_chat_completions_route_fresh_sid_when_header_absent(
    tmp_path: Path,
) -> None:
    """Without X-Client-Session-Id, session_id=None is passed to run_chat_turn
    (the runner generates a random sid internally), and is_resumed=False."""
    app = _make_test_app(registry=_REGISTRY, workspace="test-ws")
    captured_calls: list[dict[str, Any]] = []

    async def _fake_run(**kwargs: Any) -> str:
        captured_calls.append(dict(kwargs))
        return "ok"

    with (
        patch(
            "amplifier_agent_http.routes.chat_completions.run_chat_turn",
            side_effect=_fake_run,
        ),
        patch(
            "amplifier_agent_http.routes.chat_completions.workspaces_root",
            return_value=tmp_path,
        ),
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        resp1 = client.post(
            "/v1/chat/completions",
            json=_chat_payload(),
            headers=AUTH,  # no X-Client-Session-Id
        )
        resp2 = client.post(
            "/v1/chat/completions",
            json=_chat_payload(),
            headers=AUTH,
        )

    assert resp1.status_code == 200
    assert resp2.status_code == 200
    # Both turns have session_id=None (runner picks a random sid) and no resume.
    assert captured_calls[0]["session_id"] is None
    assert captured_calls[0]["is_resumed"] is False
    assert captured_calls[1]["session_id"] is None
    assert captured_calls[1]["is_resumed"] is False


def test_chat_completions_route_client_edits_history_replaces_stored(
    tmp_path: Path,
) -> None:
    """Client sends T1 on turn 1, then T2 (different messages) on turn 2 with
    the same X-Client-Session-Id.  The store must contain T2 after turn 2."""
    app = _make_test_app(registry=_REGISTRY, workspace="test-ws")

    async def _fake_run(**kwargs: Any) -> str:
        return "ok"

    sid_clean = "edits-test"
    session_id = f"http-{sid_clean}"

    with (
        patch(
            "amplifier_agent_http.routes.chat_completions.run_chat_turn",
            side_effect=_fake_run,
        ),
        patch(
            "amplifier_agent_http.routes.chat_completions.workspaces_root",
            return_value=tmp_path,
        ),
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        # Turn 1: original history.
        client.post(
            "/v1/chat/completions",
            json={
                "model": "claude-3-5-sonnet-20241022",
                "messages": [{"role": "user", "content": "turn one message"}],
            },
            headers={**AUTH, "X-Client-Session-Id": sid_clean},
        )

        # Turn 2: user edited the past message (different content).
        client.post(
            "/v1/chat/completions",
            json={
                "model": "claude-3-5-sonnet-20241022",
                "messages": [{"role": "user", "content": "edited message"}],
            },
            headers={**AUTH, "X-Client-Session-Id": sid_clean},
        )

    # After turn 2, the store should contain the turn-2 client view (T2).
    # workspace is NOT suffixed -- stays at base_workspace ("test-ws").
    workspace_slug = "test-ws"
    store = SessionStore(tmp_path / workspace_slug)
    loaded = store.load(session_id)
    assert loaded is not None
    transcript, _ = loaded
    # The transcript should reflect T2 (the edited version).
    assert any(msg.get("content") == "edited message" for msg in transcript if isinstance(msg.get("content"), str)), (
        f"Expected 'edited message' in transcript, got: {transcript}"
    )


# ---------------------------------------------------------------------------
# Unit tests: repair step in reconcile_client_history
# ---------------------------------------------------------------------------


def test_reconciler_repairs_orphaned_tool_use_before_persist(tmp_path: Path) -> None:
    """Client sends a transcript with an orphaned tool_call (no matching tool result).

    The foundation's diagnose_transcript / repair_transcript operates on the
    OpenAI wire format: ``tool_calls`` on the assistant message and ``role:
    "tool"`` response messages with ``tool_call_id``.

    reconcile_client_history runs the foundation repair pass, synthesises a
    synthetic ``role: "tool"`` result, persists the cleaned version to the
    store, and returns the repaired list — not the original broken one.
    """
    store = SessionStore(tmp_path)
    sid = "http-repair-orphan"

    broken: list[dict[str, Any]] = [
        {"role": "user", "content": "do the thing"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-123",
                    "type": "function",
                    "function": {"name": "bash", "arguments": '{"command": "ls"}'},
                }
            ],
        },
        # missing role:"tool" tool_call_id:"call-123" result — that's the break
        {"role": "user", "content": "follow up"},
    ]

    result = reconcile_client_history(
        client_messages=broken,
        session_id=sid,
        store=store,
    )

    # The returned transcript must no longer have an orphaned tool_call.
    # After repair the stored and returned transcripts should be consistent.
    loaded = store.load(sid)
    assert loaded is not None
    transcript, metadata = loaded
    assert metadata == {"last_turn": "client_reconciled"}

    # Returned value must be the repaired (stored) version.
    assert result == transcript

    # Every tool_call id in the repaired transcript must have a paired
    # role:"tool" result message.
    tool_call_ids: set[str] = set()
    tool_result_ids: set[str] = set()
    for msg in result:
        if msg.get("role") == "assistant":
            for tc in msg.get("tool_calls") or []:
                tool_call_ids.add(tc["id"])
        elif msg.get("role") == "tool":
            tool_result_ids.add(msg.get("tool_call_id", ""))
    unmatched = tool_call_ids - tool_result_ids
    assert not unmatched, f"Orphaned tool_call ids remain after repair: {unmatched}"


def test_reconciler_healthy_transcript_passes_through_unchanged(tmp_path: Path) -> None:
    """Client sends a well-formed transcript.

    diagnose_transcript reports healthy — no repair is invoked — and
    store.save sees a transcript identity-equal to the original input.
    """
    store = SessionStore(tmp_path)
    sid = "http-healthy-passthrough"

    healthy: list[dict[str, Any]] = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
        {"role": "user", "content": "goodbye"},
    ]

    result = reconcile_client_history(
        client_messages=healthy,
        session_id=sid,
        store=store,
    )

    # Healthy transcript: returned value is the same object (identity check).
    assert result is healthy

    # Persisted view matches input.
    loaded = store.load(sid)
    assert loaded is not None
    transcript, _ = loaded
    assert transcript == healthy


def test_reconciler_logs_warning_with_failure_modes_on_repair(tmp_path: Path, caplog: Any) -> None:
    """When a repair happens, a warning is logged containing 'failure_modes='
    and the session_id — operator visibility for production debugging.

    Uses the OpenAI wire format (``tool_calls`` / ``role: "tool"``) which is
    what foundation's diagnose_transcript understands.
    """
    import logging

    store = SessionStore(tmp_path)
    sid = "http-warn-on-repair"

    broken: list[dict[str, Any]] = [
        {"role": "user", "content": "do the thing"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-456",
                    "type": "function",
                    "function": {"name": "bash", "arguments": '{"command": "pwd"}'},
                }
            ],
        },
        # missing role:"tool" tool_call_id:"call-456" result
        {"role": "user", "content": "next step"},
    ]

    with caplog.at_level(logging.WARNING, logger="amplifier_agent_http._reconciler"):
        reconcile_client_history(
            client_messages=broken,
            session_id=sid,
            store=store,
        )

    # At least one WARNING record must contain the expected fields.
    warning_texts = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warning_texts, "Expected at least one WARNING log entry from the reconciler"
    combined = " ".join(warning_texts)
    assert "failure_modes=" in combined, f"'failure_modes=' not found in: {combined}"
    assert sid in combined, f"session_id '{sid}' not found in warning: {combined}"

"""Tests for X-Session-Id fallback and no-workspace-suffix behavior.

Covers:
- X-Session-Id alone (no X-Client-Session-Id): server uses it as the client
  session id, creates session_dir at workspaces/<base>/sessions/http-<sid>/.
  Workspace is NOT suffixed.
- Both headers present: X-Client-Session-Id takes precedence (amplifier-native
  is authoritative).
- Neither header present: fresh random sid per turn, no resume (legacy behavior
  unchanged).
- Workspace path is never suffixed with the client_sid regardless of which
  header is used.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from amplifier_agent_http.routes import chat_completions as cc_module

# ---------------------------------------------------------------------------
# Shared helpers (mirrors test_reconciler.py)
# ---------------------------------------------------------------------------

AUTH = {"Authorization": "Bearer test-key"}

_REGISTRY = {"claude-3-5-sonnet-20241022": "anthropic"}


def _make_test_app(
    *,
    registry: dict[str, str] | None = None,
    workspace: str | None = None,
) -> FastAPI:
    """Build a minimal FastAPI app for session-id fallback tests."""
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
# Tests
# ---------------------------------------------------------------------------


def test_x_session_id_only_uses_it_as_client_sid(tmp_path: Path) -> None:
    """Only X-Session-Id set (no X-Client-Session-Id): server uses it as the
    client session id, creating a deterministic ``http-<sid>`` and storing the
    session at workspaces/<base>/sessions/http-<sid>/.  Workspace is NOT
    suffixed."""
    app = _make_test_app(registry=_REGISTRY, workspace="test-ws")
    captured: dict[str, Any] = {}

    async def _fake_run(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "ok"

    opencode_sid = "ses_10dba803effekTqUujLAFyL9me"

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
            headers={**AUTH, "X-Session-Id": opencode_sid},
        )

    assert resp.status_code == 200
    # session_id must be the deterministic http- prefixed form.
    assert captured.get("session_id") == f"http-{opencode_sid}"
    # workspace passed to run_chat_turn must be the base workspace, not suffixed.
    assert captured.get("workspace") == "test-ws"


def test_x_client_session_id_wins_over_x_session_id(tmp_path: Path) -> None:
    """Both headers present: X-Client-Session-Id takes precedence.  The
    amplifier-native header is authoritative when both are sent."""
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
            headers={
                **AUTH,
                "X-Client-Session-Id": "my-native-sid",
                "X-Session-Id": "opencode-sdk-sid",
            },
        )

    assert resp.status_code == 200
    # X-Client-Session-Id (amplifier-native) must win.
    assert captured.get("session_id") == "http-my-native-sid"


def test_neither_header_set_falls_back_to_random_sid(tmp_path: Path) -> None:
    """No session headers: behavior is unchanged from pre-PR -- fresh random
    sid per turn (session_id=None from route perspective), no resume.
    Workspace is base_workspace."""
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
            headers=AUTH,  # no session headers
        )
        resp2 = client.post(
            "/v1/chat/completions",
            json=_chat_payload(),
            headers=AUTH,
        )

    assert resp1.status_code == 200
    assert resp2.status_code == 200
    # No session id -- runner picks a random sid each time.
    assert captured_calls[0]["session_id"] is None
    assert captured_calls[0]["is_resumed"] is False
    assert captured_calls[1]["session_id"] is None
    assert captured_calls[1]["is_resumed"] is False


def test_x_session_id_workspace_is_not_suffixed(tmp_path: Path) -> None:
    """Specifically assert the workspace path is NOT suffixed with the
    client_sid.  The session_dir lives at
    workspaces/<base>/sessions/http-<sid>/, NOT
    workspaces/<base>-<sid>/sessions/http-<sid>/."""
    app = _make_test_app(registry=_REGISTRY, workspace="test-ws")

    async def _fake_run(**kwargs: Any) -> str:
        return "ok"

    opencode_sid = "ses_abc123"
    session_id = f"http-{opencode_sid}"

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
        client.post(
            "/v1/chat/completions",
            json=_chat_payload(),
            headers={**AUTH, "X-Session-Id": opencode_sid},
        )

    # The session_dir must be under the un-suffixed base workspace.
    correct_session_dir = tmp_path / "test-ws" / "sessions" / session_id
    wrong_workspace_dir = tmp_path / f"test-ws-{opencode_sid}"

    assert correct_session_dir.exists(), (
        f"Expected session_dir at {correct_session_dir} but it does not exist. "
        f"tmp_path contents: {list(tmp_path.iterdir())}"
    )
    assert not wrong_workspace_dir.exists(), f"Workspace was incorrectly suffixed: {wrong_workspace_dir} exists."

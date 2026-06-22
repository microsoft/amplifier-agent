"""Tests for chat-completions validation: model registry, streaming flag, upstream errors.

Covers:
- Unknown model → HTTP 400 with structured error body.
- ``stream: false`` → single JSON body (chat.completion shape).
- ``stream: true`` → SSE (text/event-stream).
- Upstream error before first chunk → HTTP 502 with structured error body.
- Upstream error after first chunk (mid-stream) → embedded in delta.content.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from amplifier_agent_http.routes import chat_completions as cc_module
from amplifier_agent_http.routes import models as models_module

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _make_test_app(*, registry: dict[str, str] | None = None) -> FastAPI:
    """Build a minimal FastAPI app with no real lifespan for chat-completions tests.

    Uses a no-op lifespan so TestClient doesn't trigger any bundle loading,
    provider enumeration, or sys.exit() calls.  State is seeded directly.
    """
    prepared_mock = MagicMock()
    prepared_mock.mount_plan = {}

    state_registry = registry or {}

    @asynccontextmanager
    async def _noop_lifespan(application: FastAPI):
        # Seed required app.state attributes before yielding.
        application.state.config = MagicMock()
        application.state.config.model_id = "amplifier"
        application.state.config.api_key = "test-key"
        application.state.prepared = prepared_mock
        application.state.agent_configs = {}
        application.state.resolved_workspace = None
        application.state.host_config = {}
        application.state.available_models = []
        application.state.served_models_registry = state_registry
        yield

    app = FastAPI(lifespan=_noop_lifespan)
    app.include_router(cc_module.router)
    app.include_router(models_module.router)
    return app


AUTH = {"Authorization": "Bearer test-key"}


def _chat_payload(model: str = "claude-3-5-sonnet-20241022", **kwargs: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": "hello"}],
    }
    base.update(kwargs)
    return base


# ---------------------------------------------------------------------------
# Edit A — model validation
# ---------------------------------------------------------------------------


def test_unknown_model_returns_400() -> None:
    """Request with an unregistered model → HTTP 400 + structured error body."""
    app = _make_test_app(registry={})  # empty registry → no models served
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post(
            "/v1/chat/completions",
            json=_chat_payload(model="nonexistent-model"),
            headers=AUTH,
        )

    assert resp.status_code == 400
    body = resp.json()
    assert "error" in body["detail"]
    error = body["detail"]["error"]
    assert error["code"] == "unknown_model"
    assert error["type"] == "invalid_request_error"
    assert "nonexistent-model" in error["message"]


# ---------------------------------------------------------------------------
# Edit B — stream flag
# ---------------------------------------------------------------------------


def test_stream_false_returns_single_json() -> None:
    """``stream: false`` → single JSON body, Content-Type: application/json."""
    registry = {"claude-3-5-sonnet-20241022": "anthropic"}
    app = _make_test_app(registry=registry)

    async def _fake_run_chat_turn(**kwargs: Any) -> str:
        display = kwargs["display"]
        # display.emit is async; must be awaited.
        await display.emit({"type": "text:delta", "text": "Hello from non-streaming path"})
        return "done"

    with (
        patch(
            "amplifier_agent_http.routes.chat_completions.run_chat_turn",
            side_effect=_fake_run_chat_turn,
        ),
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        resp = client.post(
            "/v1/chat/completions",
            json=_chat_payload(stream=False),
            headers=AUTH,
        )

    assert resp.status_code == 200
    assert "application/json" in resp.headers["content-type"]
    body = resp.json()
    assert body["object"] == "chat.completion"
    assert "choices" in body
    assert body["choices"][0]["message"]["role"] == "assistant"


def test_stream_true_returns_sse() -> None:
    """``stream: true`` → SSE stream, Content-Type: text/event-stream."""
    registry = {"claude-3-5-sonnet-20241022": "anthropic"}
    app = _make_test_app(registry=registry)

    async def _fake_run_chat_turn(**kwargs: Any) -> str:
        display = kwargs["display"]
        # display.emit is async; must be awaited.
        await display.emit({"type": "text:delta", "text": "hi"})
        return "done"

    with (
        patch(
            "amplifier_agent_http.routes.chat_completions.run_chat_turn",
            side_effect=_fake_run_chat_turn,
        ),
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        resp = client.post(
            "/v1/chat/completions",
            json=_chat_payload(stream=True),
            headers=AUTH,
        )

    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    # Response body should contain SSE data lines
    assert "data:" in resp.text


# ---------------------------------------------------------------------------
# Edit C — upstream errors
# ---------------------------------------------------------------------------


def test_upstream_error_before_first_chunk_returns_502() -> None:
    """run_chat_turn raises immediately → HTTP 502 with structured error body."""
    registry = {"claude-3-5-sonnet-20241022": "anthropic"}
    app = _make_test_app(registry=registry)

    with (
        patch(
            "amplifier_agent_http.routes.chat_completions.run_chat_turn",
            new=AsyncMock(side_effect=RuntimeError("provider init failure")),
        ),
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        resp = client.post(
            "/v1/chat/completions",
            json=_chat_payload(),
            headers=AUTH,
        )

    assert resp.status_code == 502
    body = resp.json()
    assert "error" in body["detail"]
    error = body["detail"]["error"]
    assert error["code"] == "upstream_error"
    assert error["type"] == "upstream_error"


def test_upstream_error_after_first_chunk_embeds_in_delta() -> None:
    """Error raised after the preflight window → embedded in delta.content (SSE 200).

    The preflight check in ``chat_completions`` waits 50 ms for immediate failures.
    This mock emits a content event and then raises AFTER 100 ms, so the preflight
    check does not see a failed task; the error occurs mid-stream and must be
    embedded in delta.content of the in-progress SSE response.
    """
    import asyncio as _asyncio

    registry = {"claude-3-5-sonnet-20241022": "anthropic"}
    app = _make_test_app(registry=registry)

    async def _run_with_late_error(**kwargs: Any) -> str:
        display = kwargs["display"]
        # Emit a real content event so the task is known-running at preflight time.
        # Must await display.emit because it is an async method.
        await display.emit({"type": "text:delta", "text": "partial response"})
        # Sleep longer than the preflight window (50 ms) so the pre-flight check
        # sees a still-running task and allows the SSE stream to start.
        await _asyncio.sleep(0.1)
        raise RuntimeError("mid-stream provider error")

    with (
        patch(
            "amplifier_agent_http.routes.chat_completions.run_chat_turn",
            side_effect=_run_with_late_error,
        ),
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        resp = client.post(
            "/v1/chat/completions",
            json=_chat_payload(stream=True),
            headers=AUTH,
        )

    # Response is still 200 SSE (headers already committed before error occurs)
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    # The error should be embedded somewhere in the SSE body
    sse_body = resp.text
    assert "amplifier-agent error" in sse_body or "mid-stream provider error" in sse_body

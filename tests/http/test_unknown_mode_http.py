"""Fail-closed unknown-mode handling on the HTTP face.

Mirrors the existing ``unknown_model`` 400 in ``test_chat_completions_validation.py``,
with one addition E2E structurally cannot reach: the 503 ``modes_unavailable`` path.
Triggering that for real means sabotaging mode discovery inside a running install, so it
is stubbed here instead -- ``app.state`` is seeded directly and no lifespan runs.

The four ``app.state`` permutations under test are the whole contract:

    modes_discovery_error=None, available_modes=[...]  -> membership check   -> 200/400
    modes_discovery_error=<exc>                        -> could not verify   -> 503
    modes_discovery_error absent entirely              -> could not verify   -> 503
    available_modes absent entirely                    -> could not verify   -> 503

The two "absent" rows are the subtle ones: an app assembled without the lifespan that
records these attributes has never verified anything, and the absence of a recorded error
is not evidence that discovery succeeded.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from amplifier_agent_http.routes import chat_completions as cc_module
from amplifier_agent_http.routes.chat_completions import _known_mode_names

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

#: Distinguishes "seed this attribute with None" from "do not seed it at all".
#: ``None`` is a legitimate value for ``modes_discovery_error`` (it means discovery
#: succeeded), so it cannot double as the "absent" marker.
_ABSENT = object()

_MODEL = "claude-3-5-sonnet-20241022"
_REGISTRY = {_MODEL: "anthropic"}

AUTH = {"Authorization": "Bearer test-key"}


def _make_test_app(
    *,
    available_modes: Any = _ABSENT,
    modes_discovery_error: Any = _ABSENT,
) -> FastAPI:
    """Build a chat-completions app with mode-discovery state seeded directly.

    Uses a no-op lifespan (as ``test_chat_completions_validation`` does) so TestClient
    never loads a bundle or enumerates providers. Attributes passed as ``_ABSENT`` are
    genuinely never set, which is what makes the "attribute missing" cases testable at
    all -- seeding them with ``None`` would test a different branch.
    """
    prepared_mock = MagicMock()
    prepared_mock.mount_plan = {}

    @asynccontextmanager
    async def _noop_lifespan(application: FastAPI):
        application.state.config = MagicMock()
        application.state.config.model_id = "amplifier"
        application.state.config.api_key = "test-key"
        application.state.prepared = prepared_mock
        application.state.agent_configs = {}
        application.state.resolved_workspace = None
        application.state.host_config = {}
        application.state.available_models = []
        application.state.served_models_registry = dict(_REGISTRY)
        if available_modes is not _ABSENT:
            application.state.available_modes = available_modes
        if modes_discovery_error is not _ABSENT:
            application.state.modes_discovery_error = modes_discovery_error
        yield

    app = FastAPI(lifespan=_noop_lifespan)
    app.include_router(cc_module.router)
    return app


def _payload(*, mode: str | None = None) -> dict[str, Any]:
    """Chat payload, optionally carrying an ``[amplifier-agent:mode=<name>]`` directive.

    The directive rides in a system message because that is the only role
    ``_detect_mode_from_messages`` honors -- an echoed one in a user/assistant turn must
    not be able to select a mode.
    """
    messages: list[dict[str, Any]] = []
    if mode is not None:
        messages.append({"role": "system", "content": f"You are helpful.\n\n[amplifier-agent:mode={mode}]"})
    messages.append({"role": "user", "content": "hello"})
    return {"model": _MODEL, "messages": messages, "stream": False}


def _modes(*names: str) -> list[dict[str, str]]:
    """``resources.list_modes()``-shaped records, which is what lifespan stores."""
    return [{"name": n, "description": f"{n} mode"} for n in names]


def _post(app: FastAPI, payload: dict[str, Any]):
    """POST the payload with ``run_chat_turn`` stubbed, and report whether it ran.

    Returns ``(response, calls)`` where ``calls`` is a list appended to once per turn.
    An empty ``calls`` proves the request was rejected BEFORE any provider work started
    -- which is the point of failing closed. A fail-open would still be billable.
    """
    calls: list[dict[str, Any]] = []

    async def _fake_run_chat_turn(**kwargs: Any) -> str:
        calls.append(kwargs)
        await kwargs["display"].emit({"type": "text:delta", "text": "ok"})
        return "done"

    with (
        patch(
            "amplifier_agent_http.routes.chat_completions.run_chat_turn",
            side_effect=_fake_run_chat_turn,
        ),
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        resp = client.post("/v1/chat/completions", json=payload, headers=AUTH)
    return resp, calls


# ---------------------------------------------------------------------------
# Route level -- unknown mode (caller error, 400)
# ---------------------------------------------------------------------------


def test_unknown_mode_directive_returns_400() -> None:
    """Discovery ran and the directive names something else: blame the caller.

    Shape mirrors the existing ``unknown_model`` 400 exactly, including the
    "call GET /v1/..." affordance, so a client can handle both the same way.
    """
    app = _make_test_app(available_modes=_modes("brainstorm", "plan"), modes_discovery_error=None)

    resp, calls = _post(app, _payload(mode="e2e-no-such-mode"))

    assert resp.status_code == 400
    error = resp.json()["detail"]["error"]
    assert error["code"] == "unknown_mode"
    assert error["type"] == "invalid_request_error"
    assert "e2e-no-such-mode" in error["message"]
    assert "/v1/modes" in error["message"]
    assert calls == [], "a rejected turn must not reach the provider"


def test_empty_available_modes_with_no_error_returns_400() -> None:
    """Zero modes discovered is an ANSWER, so a named mode really is unknown -> 400.

    The counterpart to ``test_discovery_error_returns_503``: identical ``available_modes``
    ([]), opposite verdict, and the only thing separating them is
    ``modes_discovery_error``. Without that flag these two states are byte-identical and
    the 400/503 split is unimplementable.
    """
    app = _make_test_app(available_modes=[], modes_discovery_error=None)

    resp, calls = _post(app, _payload(mode="plan"))

    assert resp.status_code == 400
    assert resp.json()["detail"]["error"]["code"] == "unknown_mode"
    assert calls == []


# ---------------------------------------------------------------------------
# Route level -- unverifiable (server error, 503). E2E cannot reach these.
# ---------------------------------------------------------------------------


def test_discovery_error_returns_503() -> None:
    """THE case E2E cannot cover: lifespan discovery raised, so nothing was verified.

    A DTU cannot sabotage its own mode discovery without breaking the install it is
    validating, so this path only exists as a stubbed unit test. Reporting it as
    ``unknown_mode`` would blame the caller for our broken machinery and send them
    hunting for a typo that does not exist -- hence 503, not 400.
    """
    app = _make_test_app(
        available_modes=[],  # what lifespan writes alongside the error
        modes_discovery_error=ImportError("amplifier_module_hooks_mode is not installed"),
    )

    resp, calls = _post(app, _payload(mode="plan"))

    assert resp.status_code == 503
    error = resp.json()["detail"]["error"]
    assert error["code"] == "modes_unavailable"
    assert error["type"] == "server_error"
    assert "plan" in error["message"]
    assert calls == []


def test_missing_discovery_error_attribute_returns_503() -> None:
    """No recorded error is NOT evidence of a successful discovery.

    An app assembled without the lifespan that writes ``modes_discovery_error`` has never
    run discovery at all. Treating the missing attribute as "no error" would silently
    fail open on exactly the configuration where we know the least.
    """
    app = _make_test_app(available_modes=_modes("plan"), modes_discovery_error=_ABSENT)

    resp, calls = _post(app, _payload(mode="nope"))

    assert resp.status_code == 503, "absent error flag must be unverifiable (503), not unknown (400)"
    assert resp.json()["detail"]["error"]["code"] == "modes_unavailable"
    assert calls == []


def test_missing_available_modes_attribute_returns_503() -> None:
    """Same reasoning from the other side: no snapshot means nothing to check against.

    Note the error flag here says discovery succeeded. The candidate set is still missing,
    so we still cannot answer the question -- both attributes have to be present for a
    membership check to mean anything.
    """
    app = _make_test_app(available_modes=_ABSENT, modes_discovery_error=None)

    resp, calls = _post(app, _payload(mode="plan"))

    assert resp.status_code == 503
    assert resp.json()["detail"]["error"]["code"] == "modes_unavailable"
    assert calls == []


# ---------------------------------------------------------------------------
# Route level -- the paths that must keep working
# ---------------------------------------------------------------------------


def test_known_mode_directive_proceeds() -> None:
    """Fail-closed must not become fail-always: a real mode still runs the turn."""
    app = _make_test_app(available_modes=_modes("brainstorm", "plan"), modes_discovery_error=None)

    resp, calls = _post(app, _payload(mode="plan"))

    assert resp.status_code == 200
    assert resp.json()["object"] == "chat.completion"
    assert len(calls) == 1
    assert calls[0]["mode"] == "plan"


def test_no_mode_directive_proceeds_unrestricted() -> None:
    """An omitted mode is per-turn-disable, not an error -- and is never validated.

    Deliberately seeded with NEITHER discovery attribute. If the route validated
    unconditionally this would 503; that it returns 200 proves the check is gated on a
    mode actually having been named.
    """
    app = _make_test_app(available_modes=_ABSENT, modes_discovery_error=_ABSENT)

    resp, calls = _post(app, _payload(mode=None))

    assert resp.status_code == 200
    assert len(calls) == 1
    assert calls[0]["mode"] is None


# ---------------------------------------------------------------------------
# _known_mode_names -- direct unit coverage of every state permutation
# ---------------------------------------------------------------------------


def test_known_mode_names_projects_snapshot_to_names() -> None:
    """Lifespan stores ``{name, description}`` dicts; the resolver wants bare names."""
    state = SimpleNamespace(
        modes_discovery_error=None,
        available_modes=_modes("brainstorm", "plan"),
    )
    assert _known_mode_names(state) == ["brainstorm", "plan"]


def test_known_mode_names_returns_empty_list_for_verified_empty() -> None:
    """Verified-empty must be ``[]``, not ``None`` -- it is an answer, and answers 400."""
    state = SimpleNamespace(modes_discovery_error=None, available_modes=[])

    result = _known_mode_names(state)

    assert result == []
    assert result is not None, "verified-empty is NOT the same signal as could-not-verify"


@pytest.mark.parametrize(
    ("state", "why"),
    [
        (
            SimpleNamespace(modes_discovery_error=RuntimeError("boom"), available_modes=[]),
            "discovery raised",
        ),
        (
            SimpleNamespace(available_modes=_modes("plan")),
            "no lifespan recorded an error flag",
        ),
        (
            SimpleNamespace(modes_discovery_error=None),
            "no lifespan recorded a mode snapshot",
        ),
        (
            SimpleNamespace(),
            "neither attribute exists",
        ),
        (
            SimpleNamespace(modes_discovery_error=None, available_modes=None),
            "snapshot present but not a list",
        ),
    ],
    ids=["error-set", "error-absent", "modes-absent", "both-absent", "modes-not-a-list"],
)
def test_known_mode_names_returns_none_when_unusable(state: SimpleNamespace, why: str) -> None:
    """Every unusable state collapses to ``None``, the sole "could not verify" signal.

    Asserted with ``is None`` rather than falsiness because ``[]`` is falsy too and means
    the opposite thing -- a regression to ``[]`` here would turn every 503 into a 400.
    """
    assert _known_mode_names(state) is None, f"unusable state ({why}) must be None"

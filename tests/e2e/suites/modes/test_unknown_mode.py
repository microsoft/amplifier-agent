"""E2E: an unknown mode must be REJECTED, on both faces (the fail-closed contract).

Contract under test
-------------------
A mode name is always a deliberate, structured request: ``--mode`` on the CLI, or the
``[amplifier-agent:mode=<name>]`` directive in a system/developer message over HTTP. It
is never incidental user prose. So a name that does not resolve is a caller error and
the turn must not run.

    mode omitted             -> no restriction, turn runs      (unchanged)
    mode named, known        -> activate, turn runs
    mode named, NOT known    -> REJECT, turn never starts      (client error)
    mode named, unverifiable -> REJECT, turn never starts      (server error, distinct code)

Core invariant: ``session_state["active_mode"]`` / ``metadata.activeMode`` is only ever
set to a name that actually resolved. It is NEVER set to an unverified name.

Why that invariant is the real severity
---------------------------------------
Today's fail-open is worse than "no restriction applied". The turn proceeds with the
active mode set to a name no policy backs, so every downstream reader -- the envelope,
hooks-mode, a host UI -- believes a mode is active while nothing whatsoever is enforced.
Silent non-enforcement that reports itself as enforcement is the bug; the missing error
message is only the symptom.

Expected error shapes
---------------------
CLI mirrors the existing argv-validation convention (``_emit_argv_envelope``, the same
path ``argv_workspace_invalid`` uses): the section 4.1 envelope on stdout, ``error.code``
set, exit 2.

HTTP mirrors the one existing fail-closed case, ``unknown_model``
(``routes/chat_completions.py``): 400 with an OpenAI-shaped error object carrying a
machine-readable ``code``, wrapped by FastAPI in ``detail``.

    {"detail": {"error": {"type": "invalid_request_error",
                          "code": "unknown_mode",
                          "message": "... Call GET /v1/modes ..."}}}

Not covered here
----------------
The "discovery unavailable -> 503 / modes_unavailable" half of the contract. Triggering
it end-to-end means sabotaging the DTU's install so mode discovery genuinely breaks,
which is slow and fragile and would prove little. It belongs in ``tests/http/`` as a
unit test with discovery stubbed out.

Why this is a bespoke suite (not ``framework.harness``)
-------------------------------------------------------
``run_cli_case`` hardcodes ``exit_code == 0`` and ``run_http_case`` hardcodes HTTP 200 as
the baseline, and neither can send a request body. Every case here asserts a FAILURE
status. Rather than extend the shared runners, this follows the precedent set by
``suites/skills/test_sigil_dispatch.py`` and builds its commands locally, leaving
``framework/`` untouched (docs/E2E_TESTING.md: "stable; rarely touched").

Cases
-----
``unknown-mode-cli-rejects``          RED  -- exit 2 + error.code == argv_mode_unknown
``unknown-mode-cli-not-active``       RED  -- the invariant, stated independently of exit code
``unknown-mode-http-rejects``         RED  -- 400 + detail.error.code == unknown_mode
``known-mode-cli-still-runs``         CONTROL -- a real mode must still activate
``known-mode-http-still-runs``        CONTROL -- a real mode via directive must still run
"""

from __future__ import annotations

import json
import shlex
from uuid import uuid4

import pytest
from framework import dtu

pytestmark = pytest.mark.dtu

# Host-config seeded into every DTU by provisioning (anthropic provider, approval "yes").
_CONFIG = "/root/e2e/host-config.json"

# A mode shipped in the bundle. Asserted by suites/modes/cases.py::MODES, so if this
# stops existing that suite fails first and names the real cause.
_KNOWN_MODE = "plan"

# Marker curl appends after the body so the status is readable from the same stream.
_META = "__META__"

# Family of the model host-config.json selects, so both faces run comparable models.
_MODEL_HINT = "sonnet"


def _bogus_mode() -> str:
    """A mode name that cannot exist, but is still SHAPE-VALID.

    Shape matters. ``_MODE_DIRECTIVE_RE`` only matches ``[A-Za-z0-9._-]+``, so a name
    with illegal characters would make ``_detect_mode_from_messages`` return None and
    the HTTP case would silently test nothing (a vacuous pass). Hex from uuid4 keeps
    the name inside the character class while guaranteeing it matches no real or
    seeded mode.
    """
    return f"e2e-no-such-mode-{uuid4().hex[:12]}"


def _run_cli(dtu_id: str, argv: list[str]) -> dict:
    """Run ``amplifier-agent <argv>`` inside the DTU. Returns the raw exec result."""
    return dtu.exec_json(dtu_id, ["amplifier-agent", *argv])


def _envelope(result: dict, context: str) -> dict:
    """Parse the section 4.1 JSON envelope from a run's stdout."""
    stdout = result.get("stdout", "")
    try:
        parsed = json.loads(stdout)
    except (json.JSONDecodeError, ValueError) as exc:
        raise AssertionError(
            f"{context}: stdout is not the JSON envelope ({exc})\nstdout:\n{stdout}\nstderr:\n{result.get('stderr', '')}"
        ) from exc
    if not isinstance(parsed, dict):
        raise AssertionError(f"{context}: expected an envelope object, got {type(parsed).__name__}\nstdout:\n{stdout}")
    return parsed


def _post_chat(dtu_id: str, base_url: str, token: str, body: dict) -> tuple[str, str]:
    """POST ``body`` to /v1/chat/completions from INSIDE the DTU.

    curl runs in the container so ``localhost`` resolves to the in-DTU server. ``stream``
    is forced off: an error must be decided BEFORE the StreamingResponse is constructed
    (once Starlette commits the 200 status line the status can no longer change), so a
    non-streaming request is the honest way to observe the intended status.

    Returns ``(http_status, raw_body)``.
    """
    payload = json.dumps({**body, "stream": False})
    cmd = (
        f"curl -s -X POST {base_url}/v1/chat/completions "
        f"-H 'Authorization: Bearer {token}' "
        f"-H 'Content-Type: application/json' "
        f"-w '\\n{_META}%{{http_code}}' "
        f"--data-binary {shlex.quote(payload)}"
    )
    result = dtu.exec_json(dtu_id, ["bash", "-lc", cmd])
    assert result.get("exit_code") == 0, f"curl failed: exit={result.get('exit_code')} stderr={result.get('stderr')}"

    raw = result.get("stdout", "")
    raw_body, _, status_line = raw.rpartition(f"\n{_META}")
    return status_line.strip(), raw_body


def _mode_directive_body(model: str, mode: str) -> dict:
    """A chat-completions body whose system message carries the mode directive.

    This is the only route by which an ARBITRARY mode name reaches the HTTP face. The
    other route, the ``mode_alias_map`` synthetic model, is built at lifespan from the
    real discovered modes, so an unknown name there is rejected earlier as
    ``unknown_model`` and would exercise a different code path entirely.
    """
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": f"[amplifier-agent:mode={mode}]\nYou are operating in a special mode."},
            {"role": "user", "content": "Reply with the single word: acknowledged."},
        ],
    }


@pytest.fixture(scope="session")
def model_id(dtu_id: str, server: dict[str, str]) -> str:
    """Resolve a served model id at runtime from GET /v1/models.

    ``model`` is required on the request body (omitting it is a 422) and the served set
    depends on host-config, so it is never hardcoded.
    """
    cmd = f"curl -s -H 'Authorization: Bearer {server['token']}' {server['base_url']}/v1/models"
    result = dtu.exec_json(dtu_id, ["bash", "-lc", cmd])
    assert result.get("exit_code") == 0, f"/v1/models failed: {result.get('stderr')}"
    data = json.loads(result["stdout"])
    models = data.get("data") or []
    assert models, f"no served models: {data}"

    ids = [m["id"] for m in models]
    for candidate in ids:
        if _MODEL_HINT in candidate:
            return candidate
    return ids[0]


# --------------------------------------------------------------------------- #
# 1. RED -- the CLI face must reject an unknown mode.
# --------------------------------------------------------------------------- #


def test_unknown_mode_cli_rejects(dtu_id: str) -> None:
    """``--mode <unknown>`` must exit 2 with error.code == argv_mode_unknown.

    Exit 2 (not 1) because this is argv validation, matching the existing
    ``argv_workspace_invalid`` precedent. The turn must never start, so no provider call
    is made and no session is written.
    """
    bogus = _bogus_mode()
    result = _run_cli(
        dtu_id,
        ["run", "-y", "--output", "json", "--config", _CONFIG, "--mode", bogus, "say hi"],
    )

    exit_code = result.get("exit_code")
    assert exit_code == 2, (
        f"expected exit 2 for unknown mode {bogus!r}, got {exit_code}\n"
        f"stdout:\n{result.get('stdout', '')}\nstderr:\n{result.get('stderr', '')}"
    )

    envelope = _envelope(result, "unknown-mode-cli-rejects")
    error = envelope.get("error") or {}
    assert error.get("code") == "argv_mode_unknown", f"expected error.code == 'argv_mode_unknown', got {error!r}"


# --------------------------------------------------------------------------- #
# 2. RED -- the invariant, independent of how rejection is signalled.
# --------------------------------------------------------------------------- #


def test_unknown_mode_cli_never_reports_it_active(dtu_id: str) -> None:
    """An unresolved mode name must NEVER appear in metadata.activeMode.

    Deliberately separate from the exit-code case, and deliberately weaker: it asserts
    only that the unknown name is not echoed as active, saying nothing about exit codes
    or error shapes. That makes it survive any future change to the rejection mechanism
    while still pinning the thing that actually matters.

    Today this fails because the CLI echoes ``spec.mode`` -- the raw argv value -- into
    the envelope, so a mode nothing enforces is reported as active.
    """
    bogus = _bogus_mode()
    result = _run_cli(
        dtu_id,
        ["run", "-y", "--output", "json", "--config", _CONFIG, "--mode", bogus, "say hi"],
    )

    envelope = _envelope(result, "unknown-mode-cli-not-active")
    metadata = envelope.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    active = metadata.get("activeMode")

    assert active != bogus, (
        f"metadata.activeMode reports {active!r} as the active mode, but no such mode exists, "
        f"so nothing is enforced. A mode that is not backed by a policy must never be "
        f"reported as active.\nenvelope:\n{json.dumps(envelope, indent=2)}"
    )


# --------------------------------------------------------------------------- #
# 3. RED -- the HTTP face must reject an unknown mode.
# --------------------------------------------------------------------------- #


def test_unknown_mode_http_rejects(dtu_id: str, server: dict[str, str], model_id: str) -> None:
    """An unknown mode directive must be a 400 with code == unknown_mode.

    Assertions are ordered so a failure names its own cause: status first, then the
    error shape. Reaching the shape assertion means the request really was rejected and
    only the body is in question.
    """
    bogus = _bogus_mode()
    status, raw_body = _post_chat(
        dtu_id,
        server["base_url"],
        server["token"],
        _mode_directive_body(model_id, bogus),
    )

    assert status == "400", (
        f"expected HTTP 400 for unknown mode {bogus!r}, got {status!r}. A 200 here means the "
        f"turn ran with an unenforced mode marked active.\nbody:\n{raw_body}"
    )

    obj = json.loads(raw_body)
    error = (obj.get("detail") or {}).get("error") or {}
    assert error.get("code") == "unknown_mode", (
        f"expected detail.error.code == 'unknown_mode' (mirroring the existing "
        f"'unknown_model' 400), got {error!r}\nbody:\n{raw_body}"
    )


# --------------------------------------------------------------------------- #
# 4. CONTROL -- a real mode must still work. Guards against over-rejecting.
# --------------------------------------------------------------------------- #


def test_known_mode_cli_still_runs(dtu_id: str) -> None:
    """CONTROL: a genuine mode still activates and the turn still runs.

    Validates the instrument. If this fails, the RED verdicts above carry no
    information -- the delta would be "modes are broken", not "unknown modes are
    accepted". It is also the regression guard for the fix: the danger of adding a
    rejection path is rejecting too much.
    """
    result = _run_cli(
        dtu_id,
        ["run", "-y", "--output", "json", "--config", _CONFIG, "--mode", _KNOWN_MODE, "say hi"],
    )

    exit_code = result.get("exit_code")
    assert exit_code == 0, (
        f"known mode {_KNOWN_MODE!r} must still run, got exit {exit_code}\n"
        f"stdout:\n{result.get('stdout', '')}\nstderr:\n{result.get('stderr', '')}"
    )

    envelope = _envelope(result, "known-mode-cli-still-runs")
    metadata = envelope.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    assert metadata.get("activeMode") == _KNOWN_MODE, (
        f"expected activeMode == {_KNOWN_MODE!r}, got {metadata.get('activeMode')!r}\n"
        f"envelope:\n{json.dumps(envelope, indent=2)}"
    )


def test_known_mode_http_directive_still_runs(dtu_id: str, server: dict[str, str], model_id: str) -> None:
    """CONTROL: a genuine mode directive over HTTP still yields a 200 completion.

    The HTTP twin of the case above, and the reason it earns its keep separately: the
    directive path is where the new 400 will be raised, so this proves the rejection
    lands only on names that genuinely do not resolve.
    """
    status, raw_body = _post_chat(
        dtu_id,
        server["base_url"],
        server["token"],
        _mode_directive_body(model_id, _KNOWN_MODE),
    )

    assert status == "200", f"known mode {_KNOWN_MODE!r} must still be accepted, got {status!r}\nbody:\n{raw_body}"

    obj = json.loads(raw_body)
    reply = obj["choices"][0]["message"]["content"] or ""
    assert reply.strip(), f"empty assistant reply; the turn did not produce a completion\nbody:\n{raw_body}"

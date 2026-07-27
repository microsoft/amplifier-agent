"""Fail-closed unknown-mode handling on the CLI face.

Two layers, tested separately because they have separate jobs:

* ``_execute_turn`` RAISES. It has no stdout discipline and no exit-code authority, so
  catching there would recreate the fail-open it was written to remove.
* ``run()`` TRANSLATES, mapping each exception onto the §4.1 argv envelope with the
  matching exit code and classification.

The placement test below is the one that pins the actual severity. Validation sits between
``load_and_prepare_cached`` and the ``--fresh`` rmtree, and a test that only checks "does it
raise" would still pass if the check drifted below the rmtree -- at which point a rejected
turn would delete the user's session state on its way to failing.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from amplifier_agent_cli.__main__ import cli
from amplifier_agent_cli.modes import single_turn
from amplifier_agent_lib.mode_resolution import (
    ModeDiscoveryUnavailableError,
    ModeUnknownError,
)
from amplifier_agent_lib.persistence import state_root

# ---------------------------------------------------------------------------
# Helpers (mirroring tests/test_runtime_fresh_workspace.py)
# ---------------------------------------------------------------------------


def _make_spec(*, mode: str | None, fresh: bool = False, session_id: str | None = None):
    """A ``_TurnSpec`` stand-in.

    A bare ``MagicMock`` attribute is TRUTHY, so ``mode`` is always set explicitly --
    leaving it unset would make ``if spec.mode:`` fire and try to resolve a MagicMock
    repr as a mode name.
    """
    spec = MagicMock()
    spec.mode = mode
    spec.fresh = fresh
    spec.session_id = session_id
    spec.workspace = "ws-a"
    spec.resume = False
    spec.cwd = None
    spec.provider = "anthropic"
    spec.host_config = None
    spec.allow_protocol_skew = False
    spec.prompt = "hi"
    return spec


def _stub_engine_path(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Stub everything ``_execute_turn`` touches after validation.

    Returns the ``make_turn_handler`` double so tests can assert it was never reached --
    "did not raise" and "did not start the turn" are different claims, and the second is
    the one that matters for a rejection.
    """
    monkeypatch.setattr(single_turn, "load_and_prepare_cached", AsyncMock(return_value=MagicMock()))
    # inject_provider is imported locally inside _execute_turn, so patch it at the source.
    import amplifier_agent_cli.provider_sources as _ps

    monkeypatch.setattr(_ps, "inject_provider", lambda *a, **k: None)
    monkeypatch.setattr(_ps, "inject_routing_matrix", lambda *a, **k: None)

    handler_spy = MagicMock(return_value=None)
    monkeypatch.setattr(single_turn, "make_turn_handler", handler_spy)

    fake_engine = MagicMock()
    fake_engine.boot = AsyncMock()
    fake_engine.submit_turn = AsyncMock(return_value={"reply": "ok", "turnId": "turn-1"})
    fake_engine.shutdown = AsyncMock()
    monkeypatch.setattr(single_turn, "Engine", lambda *a, **k: fake_engine)
    return handler_spy


def _set_known_modes(monkeypatch: pytest.MonkeyPatch, known: list[str] | None) -> list:
    """Stub discovery with a fixed answer; returns the list of calls made.

    ``None`` models a discovery failure. ``discover_known_modes`` is bound at module
    level in ``single_turn``, so patching the name there intercepts the call site.
    """
    calls: list = []

    def _fake(config=None):
        calls.append(config)
        return known

    monkeypatch.setattr(single_turn, "discover_known_modes", _fake)
    return calls


def _seed_session(workspace: str, session_id: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    sess = state_root() / "workspaces" / workspace / "sessions" / session_id
    sess.mkdir(parents=True, exist_ok=True)
    (sess / "transcript.jsonl").write_text('{"role":"user"}', encoding="utf-8")
    return sess


# ---------------------------------------------------------------------------
# _execute_turn -- rejection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_turn_raises_on_unknown_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unknown ``--mode`` propagates out rather than being logged and ignored.

    The exception escaping is the contract: ``run()`` is the only layer that owns stdout
    and the exit code, so this function must not swallow it.
    """
    handler_spy = _stub_engine_path(monkeypatch)
    _set_known_modes(monkeypatch, ["brainstorm", "plan"])

    with pytest.raises(ModeUnknownError) as exc_info:
        await single_turn._execute_turn(_make_spec(mode="nope"))

    assert exc_info.value.name == "nope"
    assert exc_info.value.available == ["brainstorm", "plan"]
    handler_spy.assert_not_called()


@pytest.mark.asyncio
async def test_execute_turn_raises_when_discovery_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Discovery returning ``None`` rejects the turn as OUR failure, not the caller's.

    Distinct from the unknown case: the mode name here may be perfectly valid. We simply
    never got to check, so the turn is refused without any claim about the name.
    """
    handler_spy = _stub_engine_path(monkeypatch)
    _set_known_modes(monkeypatch, None)

    with pytest.raises(ModeDiscoveryUnavailableError) as exc_info:
        await single_turn._execute_turn(_make_spec(mode="plan"))

    assert exc_info.value.name == "plan"
    handler_spy.assert_not_called()


@pytest.mark.asyncio
async def test_execute_turn_accepts_known_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail-closed must not become fail-always: a real mode still runs the turn."""
    handler_spy = _stub_engine_path(monkeypatch)
    _set_known_modes(monkeypatch, ["plan"])

    result = await single_turn._execute_turn(_make_spec(mode="plan"))

    assert result["reply"] == "ok"
    handler_spy.assert_called_once()
    assert handler_spy.call_args.kwargs["mode"] == "plan"


@pytest.mark.asyncio
async def test_execute_turn_does_not_raise_when_mode_omitted(monkeypatch: pytest.MonkeyPatch) -> None:
    """An omitted mode is per-turn-disable, and is never validated at all.

    Discovery is asserted UNCALLED, not merely tolerant of failure: a user who passes no
    ``--mode`` must not have their turn made contingent on mode discovery working. If the
    gate were dropped, every no-mode turn on a host with broken discovery would start
    failing with ``modes_unavailable`` for a mode they never asked for.
    """
    handler_spy = _stub_engine_path(monkeypatch)
    discovery_calls = _set_known_modes(monkeypatch, None)

    result = await single_turn._execute_turn(_make_spec(mode=None))

    assert result["reply"] == "ok"
    assert discovery_calls == [], "no --mode means no discovery, even when discovery is broken"
    handler_spy.assert_called_once()


# ---------------------------------------------------------------------------
# _execute_turn -- WHERE the check sits (the load-bearing one)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_mode_rejected_before_fresh_rmtree(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A rejected turn must not destroy user state: validation precedes the --fresh rmtree.

    ``--fresh`` deletes the session's state directory, which is irreversible. If the mode
    check ever drifted below that block, a typo'd ``--mode`` would wipe a session's
    transcript on the way to a "your mode name is wrong" error -- the user loses their
    history over a rejected turn that never ran.

    "It raises" alone would not catch that regression, since the exception is identical
    either way. Only the surviving directory distinguishes the two orderings.
    """
    sess = _seed_session("ws-a", "sid-1", monkeypatch, tmp_path)
    handler_spy = _stub_engine_path(monkeypatch)
    _set_known_modes(monkeypatch, ["plan"])

    spec = _make_spec(mode="nope", fresh=True, session_id="sid-1")

    with pytest.raises(ModeUnknownError):
        await single_turn._execute_turn(spec)

    assert sess.exists(), "--fresh must not run when the mode was rejected"
    assert (sess / "transcript.jsonl").exists(), "session transcript survived the rejected turn"
    handler_spy.assert_not_called()


@pytest.mark.asyncio
async def test_fresh_rmtree_does_run_when_the_mode_resolves(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Control for the placement test: same setup, KNOWN mode, directory is deleted.

    Without this, ``sess.exists()`` above could pass for the wrong reason -- a mistyped
    workspace slug or an unset ``XDG_STATE_HOME`` would leave the seeded directory
    untouched no matter where the check sits. This proves the rmtree really does reach
    that exact path, so its survival in the rejection case means something.
    """
    sess = _seed_session("ws-a", "sid-1", monkeypatch, tmp_path)
    _stub_engine_path(monkeypatch)
    _set_known_modes(monkeypatch, ["plan"])

    await single_turn._execute_turn(_make_spec(mode="plan", fresh=True, session_id="sid-1"))

    assert not sess.exists(), "--fresh deletes this exact directory when the turn is allowed to run"


@pytest.mark.asyncio
async def test_unverifiable_mode_also_rejected_before_fresh_rmtree(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Same protection on the 503-equivalent path.

    Worth pinning separately: the discovery-unavailable case is the one a user can hit
    through no fault of their own, so silently eating their transcript would be doubly
    unfair.
    """
    sess = _seed_session("ws-a", "sid-1", monkeypatch, tmp_path)
    handler_spy = _stub_engine_path(monkeypatch)
    _set_known_modes(monkeypatch, None)

    with pytest.raises(ModeDiscoveryUnavailableError):
        await single_turn._execute_turn(_make_spec(mode="plan", fresh=True, session_id="sid-1"))

    assert sess.exists()
    handler_spy.assert_not_called()


# ---------------------------------------------------------------------------
# run() -- envelope translation
# ---------------------------------------------------------------------------


def _invoke_run_raising(runner: CliRunner, exc: Exception):
    """Invoke ``amplifier-agent run`` with ``_execute_turn`` replaced by a raiser.

    Isolates the translation layer: what ``run()`` does with each exception type, without
    re-testing the resolution that produced it.
    """

    async def _fake(spec):
        raise exc

    with patch("amplifier_agent_cli.modes.single_turn._execute_turn", _fake):
        return runner.invoke(cli, ["run", "-y", "--output", "json", "--mode", "nope", "hello"])


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def _anthropic_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provider detection must succeed so the run reaches _execute_turn."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")


def test_run_translates_unknown_mode_to_argv_envelope(runner: CliRunner) -> None:
    """Exit 2 + ``argv_mode_unknown``, matching the other argv-validation rejections.

    Exit 2 and ``classification: "protocol"`` both say the same thing to a wrapper: the
    caller sent something wrong, retrying verbatim will not help.
    """
    result = _invoke_run_raising(runner, ModeUnknownError("nope", ["brainstorm", "plan"]))

    assert result.exit_code == 2, f"expected exit 2, got {result.exit_code}. Output:\n{result.output}"
    envelope = json.loads(result.stdout)
    error = envelope["error"]
    assert error["code"] == "argv_mode_unknown"
    assert error["classification"] == "protocol"
    assert error["severity"] == "error"
    assert "nope" in error["message"]
    # The CLI enumerates alternatives inline (discovery is local); HTTP points at an
    # endpoint instead to avoid an unbounded list on the wire.
    assert "plan" in error["message"]
    assert envelope["reply"] == ""


def test_run_translates_discovery_unavailable_to_engine_envelope(runner: CliRunner) -> None:
    """Exit 1 + ``modes_unavailable`` + ``classification: "engine"`` -- OUR failure, not theirs.

    The classification is the part that would silently regress: ``_emit_argv_envelope``
    hardcoded ``"protocol"`` until this contract added the parameter, and every other
    caller still uses the default. A protocol classification here would tell the wrapper
    the caller's argv was malformed when in fact the machinery broke.
    """
    result = _invoke_run_raising(runner, ModeDiscoveryUnavailableError("plan", ImportError("boom")))

    assert result.exit_code == 1, f"expected exit 1, got {result.exit_code}. Output:\n{result.output}"
    envelope = json.loads(result.stdout)
    error = envelope["error"]
    assert error["code"] == "modes_unavailable"
    assert error["classification"] == "engine"
    assert "plan" in error["message"]


def test_run_envelope_is_well_formed_for_both_rejections(runner: CliRunner) -> None:
    """Both rejections still emit a complete §4.1 envelope on stdout.

    A rejection happens before a session or turn exists, so the identity fields are empty
    strings rather than absent -- wrappers parse the same shape on every path.
    """
    for exc in (ModeUnknownError("nope", []), ModeDiscoveryUnavailableError("nope")):
        result = _invoke_run_raising(runner, exc)
        envelope = json.loads(result.stdout)

        assert envelope["sessionId"] == ""
        assert envelope["turnId"] == ""
        assert envelope["reply"] == ""
        assert envelope["error"]["correlationId"]
        assert envelope["metadata"]["correlationId"] == envelope["error"]["correlationId"]

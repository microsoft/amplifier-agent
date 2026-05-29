"""Tests for engine.py — Engine lifecycle and dispatch.

9 tests covering:
  1. boot returns capabilities with all 9 events
  2. boot is idempotent
  3. submit_turn before boot raises EngineNotBootedError
  4. submit_turn emits display events and returns reply
  5. shutdown marks engine unusable (submit raises EngineShutdownError)
  6. shutdown is idempotent
  7. dispatch routes known methods (agent/initialize, turn/submit, agent/shutdown)
  8. dispatch unknown method raises ValueError
  9. engine writes only via injected display (capsys confirms zero stdout)
"""

from __future__ import annotations

import io

import pytest

from amplifier_agent_lib.engine import (
    Engine,
    EngineNotBootedError,
    EngineShutdownError,
    TurnContext,
)
from amplifier_agent_lib.protocol import CANONICAL_DISPLAY_EVENTS
from amplifier_agent_lib.protocol_points import (
    CliApprovalSystem,
    CliDisplaySystem,
    DisplayVerbosity,
    ProtocolPoints,
)

# ---------------------------------------------------------------------------
# Mock turn handler
# ---------------------------------------------------------------------------


async def _echo_turn_handler(ctx: TurnContext) -> str:
    """Echo handler: emits result/delta + result/final, returns 'echo: {prompt}'."""
    text = f"echo: {ctx.prompt}"
    # Concrete notification types carry extra fields (e.g. 'text') beyond DisplayEvent base.
    # Suppress arg-type on the emit calls below — same pattern as test_protocol_points_defaults_cli.py.
    delta = {"type": "result/delta", "sessionId": ctx.session_id, "turnId": ctx.turn_id, "text": text}
    await ctx.display.emit(delta)  # type: ignore[arg-type]
    final = {"type": "result/final", "sessionId": ctx.session_id, "turnId": ctx.turn_id, "text": text}
    await ctx.display.emit(final)  # type: ignore[arg-type]
    return text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_engine(buf: io.StringIO | None = None) -> Engine:
    """Create an Engine wired to CliDisplaySystem (StringIO) and CliApprovalSystem."""
    if buf is None:
        buf = io.StringIO()
    display = CliDisplaySystem(stream=buf, verbosity=DisplayVerbosity.VERBOSE)
    approval = CliApprovalSystem(override=None, is_tty=False)
    protocol_points: ProtocolPoints = {"approval": approval, "display": display}
    return Engine(turn_handler=_echo_turn_handler, protocol_points=protocol_points)


def _boot_params(**kwargs: object) -> dict:
    """Return a minimal valid InitializeParams dict with all 9 canonical events."""
    params: dict = {
        "protocolVersion": "0.2.0",
        "clientInfo": {"name": "test-client", "version": "0.0.0"},
        "capabilities": {
            "display": {"events": list(CANONICAL_DISPLAY_EVENTS)},
            "approval": {"actions": ["accept", "decline", "cancel"]},
        },
        "sessionId": "test-session-1",
    }
    params.update(kwargs)
    return params


# ---------------------------------------------------------------------------
# Test 1: boot returns capabilities with all 9 events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_boot_returns_capabilities_with_all_9_events() -> None:
    """Engine.boot() returns InitializeResult whose capabilities.display.events
    contains all 9 canonical display events."""
    engine = _make_engine()
    result = await engine.boot(_boot_params())
    events = result["capabilities"]["display"]["events"]
    assert set(events) == set(CANONICAL_DISPLAY_EVENTS)


# ---------------------------------------------------------------------------
# Test 2: boot is idempotent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_boot_is_idempotent() -> None:
    """Calling boot() a second time returns the identical (cached) InitializeResult."""
    engine = _make_engine()
    params = _boot_params()
    result1 = await engine.boot(params)
    result2 = await engine.boot(params)
    assert result1 is result2


# ---------------------------------------------------------------------------
# Test 3: submit_turn before boot raises EngineNotBootedError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_turn_before_boot_raises_not_booted() -> None:
    """submit_turn() before boot() raises EngineNotBootedError."""
    engine = _make_engine()
    with pytest.raises(EngineNotBootedError):
        await engine.submit_turn(
            {
                "sessionId": "s1",
                "turnId": "t1",
                "prompt": "hello",
            }
        )


# ---------------------------------------------------------------------------
# Test 4: submit_turn emits display events and returns reply
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_turn_emits_events_and_returns_reply() -> None:
    """submit_turn() emits result/delta + result/final via handler and returns echo reply."""
    buf = io.StringIO()
    engine = _make_engine(buf)
    await engine.boot(_boot_params())
    result = await engine.submit_turn(
        {
            "sessionId": "test-session-1",
            "turnId": "t1",
            "prompt": "hello",
        }
    )
    assert result["reply"] == "echo: hello"
    assert result["turnId"] == "t1"
    output = buf.getvalue()
    assert "[result/delta]" in output
    assert "[result/final]" in output


# ---------------------------------------------------------------------------
# Test 5: shutdown marks engine unusable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shutdown_makes_engine_unusable() -> None:
    """After shutdown(), submit_turn() raises EngineShutdownError."""
    engine = _make_engine()
    await engine.boot(_boot_params())
    await engine.shutdown()
    with pytest.raises(EngineShutdownError):
        await engine.submit_turn(
            {
                "sessionId": "test-session-1",
                "turnId": "t2",
                "prompt": "hello after shutdown",
            }
        )


# ---------------------------------------------------------------------------
# Test 6: shutdown is idempotent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shutdown_is_idempotent() -> None:
    """Calling shutdown() twice returns {} both times without raising."""
    engine = _make_engine()
    await engine.boot(_boot_params())
    result1 = await engine.shutdown()
    result2 = await engine.shutdown()
    assert result1 == {}
    assert result2 == {}


# ---------------------------------------------------------------------------
# Test 7: dispatch routes known methods
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_routes_known_methods() -> None:
    """dispatch() correctly routes agent/initialize, turn/submit, agent/shutdown."""
    buf = io.StringIO()
    engine = _make_engine(buf)

    # agent/initialize
    init_result = await engine.dispatch("agent/initialize", _boot_params())
    assert "capabilities" in init_result
    assert "serverInfo" in init_result
    assert "sessionState" in init_result

    # turn/submit
    turn_result = await engine.dispatch(
        "turn/submit",
        {
            "sessionId": "test-session-1",
            "turnId": "t1",
            "prompt": "hi",
        },
    )
    assert turn_result["reply"] == "echo: hi"

    # agent/shutdown
    shutdown_result = await engine.dispatch("agent/shutdown", {})
    assert shutdown_result == {}


# ---------------------------------------------------------------------------
# Test 8: dispatch unknown method raises ValueError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_unknown_method_raises_value_error() -> None:
    """dispatch() raises ValueError with 'unknown method' message for unrecognised methods."""
    engine = _make_engine()
    with pytest.raises(ValueError, match="unknown method"):
        await engine.dispatch("unknown/method", {})


# ---------------------------------------------------------------------------
# Test 9: engine writes only via injected display (capsys confirms zero stdout)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_engine_writes_only_via_injected_display(capsys: pytest.CaptureFixture) -> None:
    """Engine never writes to stdout; all output flows through injected DisplaySystem.

    Uses capsys to assert captured.out == '' while buf.getvalue() contains '[result/final]'.
    """
    buf = io.StringIO()
    display = CliDisplaySystem(stream=buf, verbosity=DisplayVerbosity.VERBOSE)
    approval = CliApprovalSystem(override=None, is_tty=False)
    protocol_points: ProtocolPoints = {"approval": approval, "display": display}
    engine = Engine(turn_handler=_echo_turn_handler, protocol_points=protocol_points)

    await engine.boot(_boot_params())
    await engine.submit_turn(
        {
            "sessionId": "test-session-1",
            "turnId": "t1",
            "prompt": "hello",
        }
    )
    await engine.shutdown()

    captured = capsys.readouterr()
    assert captured.out == "", f"Expected no stdout, got: {captured.out!r}"
    assert "[result/final]" in buf.getvalue(), f"Expected '[result/final]' in buf, got: {buf.getvalue()!r}"


# ---------------------------------------------------------------------------
# Test 10: boot propagates sessionId, resume, cwd, providerOverride
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_boot_propagates_session_id_and_resume_and_cwd() -> None:
    """Engine.boot() with sessionId, resume, cwd, and providerOverride echoes sessionId/resumed.

    Verifies that the engine correctly reads sessionId and resume from init_params
    even when cwd and providerOverride are also present (as NotRequired fields).
    Engine itself does not consume cwd/providerOverride — they are forwarded to the
    bundle layer — but boot() must not crash when they are present.
    """
    from unittest.mock import MagicMock

    async def _noop_handler(ctx: TurnContext) -> str:
        return ""

    buf = io.StringIO()
    display = CliDisplaySystem(stream=buf, verbosity=DisplayVerbosity.VERBOSE)
    approval = CliApprovalSystem(override=None, is_tty=False)
    protocol_points: ProtocolPoints = {"approval": approval, "display": display}
    engine = Engine(turn_handler=_noop_handler, protocol_points=protocol_points)

    params = _boot_params(
        sessionId="sess-xyz",
        resume=True,
        cwd="/tmp",
        providerOverride="anthropic",
    )
    result = await engine.boot(params, bundle_override=MagicMock())

    assert result["sessionState"]["sessionId"] == "sess-xyz"
    assert result["sessionState"]["resumed"] is True


# ---------------------------------------------------------------------------
# Test SC-6: sessionId in turn/submit result envelope
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_turn_result_includes_session_id() -> None:
    """SC-6: TurnSubmitResult must carry sessionId in the final-reply envelope."""

    async def _h(ctx: TurnContext) -> str:
        return "the answer"

    class _Display:
        async def emit(self, event: object) -> None:
            pass

    class _Approval:
        async def request(self, req: object) -> dict:
            return {"action": "accept"}

    from amplifier_agent_lib.protocol.methods import PROTOCOL_VERSION

    engine = Engine(
        turn_handler=_h,
        protocol_points={"approval": _Approval(), "display": _Display()},  # type: ignore[arg-type]
    )
    await engine.boot(
        {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "sessionId": "sess-9",
            "resume": False,
        },
        bundle_override=object(),  # type: ignore[arg-type]
    )
    result = await engine.submit_turn({"sessionId": "sess-9", "turnId": "turn-1", "prompt": "?"})
    assert result["sessionId"] == "sess-9"
    assert result["reply"] == "the answer"
    assert result["turnId"] == "turn-1"

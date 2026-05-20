"""Tests for protocol_points/defaults_cli.py — CLI Mode A defaults."""

from __future__ import annotations

import io

import pytest

# ---------------------------------------------------------------------------
# CliDisplaySystem tests
# ---------------------------------------------------------------------------


def test_display_default_emits_prefixed_lines() -> None:
    """DEFAULT verbosity writes '[type] summary' lines to the injected stream."""
    from amplifier_agent_lib.protocol_points.defaults_cli import CliDisplaySystem, DisplayVerbosity

    stream = io.StringIO()
    display = CliDisplaySystem(stream=stream, verbosity=DisplayVerbosity.DEFAULT)
    event = {"type": "result/delta", "sessionId": "s1", "text": "Hello"}
    display.emit(event)  # type: ignore[arg-type]
    output = stream.getvalue()
    assert output.startswith("[result/delta] Hello\n")


def test_display_quiet_suppresses_all() -> None:
    """QUIET verbosity emits nothing to the stream."""
    from amplifier_agent_lib.protocol_points.defaults_cli import CliDisplaySystem, DisplayVerbosity

    stream = io.StringIO()
    display = CliDisplaySystem(stream=stream, verbosity=DisplayVerbosity.QUIET)
    event = {"type": "result/delta", "sessionId": "s1", "text": "Hello"}
    display.emit(event)  # type: ignore[arg-type]
    assert stream.getvalue() == ""


def test_display_default_suppresses_thinking_and_progress() -> None:
    """DEFAULT verbosity suppresses thinking/* and progress events."""
    from amplifier_agent_lib.protocol_points.defaults_cli import CliDisplaySystem, DisplayVerbosity

    stream = io.StringIO()
    display = CliDisplaySystem(stream=stream, verbosity=DisplayVerbosity.DEFAULT)
    for event_type in ("thinking/delta", "thinking/final", "progress"):
        event = {"type": event_type, "sessionId": "s1", "text": "x", "message": "x"}
        display.emit(event)  # type: ignore[arg-type]
    assert stream.getvalue() == ""


def test_display_verbose_enables_thinking_and_progress() -> None:
    """VERBOSE verbosity allows thinking/* and progress events through."""
    from amplifier_agent_lib.protocol_points.defaults_cli import CliDisplaySystem, DisplayVerbosity

    stream = io.StringIO()
    display = CliDisplaySystem(stream=stream, verbosity=DisplayVerbosity.VERBOSE)
    thinking_event = {"type": "thinking/delta", "sessionId": "s1", "text": "thoughts"}
    display.emit(thinking_event)  # type: ignore[arg-type]
    progress_event = {"type": "progress", "sessionId": "s1", "message": "step 1"}
    display.emit(progress_event)  # type: ignore[arg-type]
    output = stream.getvalue()
    assert "[thinking/delta] thoughts\n" in output
    assert "[progress] step 1\n" in output


def test_display_debug_includes_json_dump() -> None:
    """DEBUG verbosity appends a JSON dump of the event after the summary line."""
    import json

    from amplifier_agent_lib.protocol_points.defaults_cli import CliDisplaySystem, DisplayVerbosity

    stream = io.StringIO()
    display = CliDisplaySystem(stream=stream, verbosity=DisplayVerbosity.DEBUG)
    event = {"type": "result/delta", "sessionId": "s1", "text": "Hi"}
    display.emit(event)  # type: ignore[arg-type]
    output = stream.getvalue()
    # First line: prefixed summary
    lines = output.splitlines()
    assert lines[0] == "[result/delta] Hi"
    # Second line: JSON dump of event
    parsed = json.loads(lines[1])
    assert parsed["type"] == "result/delta"
    assert parsed["text"] == "Hi"


# ---------------------------------------------------------------------------
# CliApprovalSystem tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approval_override_yes_returns_accept() -> None:
    """ApprovalOverride.YES always returns accept regardless of TTY/prompt."""
    from amplifier_agent_lib.protocol_points.defaults_cli import ApprovalOverride, CliApprovalSystem

    system = CliApprovalSystem(override=ApprovalOverride.YES, is_tty=False)
    req = {
        "sessionId": "s1",
        "turnId": "t1",
        "approvalId": "a1",
        "kind": "tool_call",
        "payload": {"toolName": "bash"},
        "timeoutMs": 5000,
    }
    response = await system.request(req)  # type: ignore[arg-type]
    assert response["action"] == "accept"


@pytest.mark.asyncio
async def test_approval_override_no_returns_decline() -> None:
    """ApprovalOverride.NO always returns decline regardless of TTY/prompt."""
    from amplifier_agent_lib.protocol_points.defaults_cli import ApprovalOverride, CliApprovalSystem

    system = CliApprovalSystem(override=ApprovalOverride.NO, is_tty=True)
    req = {
        "sessionId": "s1",
        "turnId": "t1",
        "approvalId": "a1",
        "kind": "tool_call",
        "payload": {"toolName": "bash"},
        "timeoutMs": 5000,
    }
    response = await system.request(req)  # type: ignore[arg-type]
    assert response["action"] == "decline"


@pytest.mark.asyncio
async def test_approval_no_tty_no_override_returns_decline() -> None:
    """Non-TTY with no override returns decline without prompting."""
    from amplifier_agent_lib.protocol_points.defaults_cli import CliApprovalSystem

    system = CliApprovalSystem(override=None, is_tty=False)
    req = {
        "sessionId": "s1",
        "turnId": "t1",
        "approvalId": "a1",
        "kind": "tool_call",
        "payload": {"toolName": "bash"},
        "timeoutMs": 5000,
    }
    response = await system.request(req)  # type: ignore[arg-type]
    assert response["action"] == "decline"


@pytest.mark.asyncio
async def test_approval_tty_prompt_accept_y() -> None:
    """TTY with prompt_fn returning 'y' returns accept."""
    from amplifier_agent_lib.protocol_points.defaults_cli import CliApprovalSystem

    system = CliApprovalSystem(override=None, is_tty=True, prompt_fn=lambda _: "y")
    req = {
        "sessionId": "s1",
        "turnId": "t1",
        "approvalId": "a1",
        "kind": "tool_call",
        "payload": {"toolName": "bash"},
        "timeoutMs": 5000,
    }
    response = await system.request(req)  # type: ignore[arg-type]
    assert response["action"] == "accept"


@pytest.mark.asyncio
async def test_approval_tty_prompt_decline_n() -> None:
    """TTY with prompt_fn returning 'n' returns decline."""
    from amplifier_agent_lib.protocol_points.defaults_cli import CliApprovalSystem

    system = CliApprovalSystem(override=None, is_tty=True, prompt_fn=lambda _: "n")
    req = {
        "sessionId": "s1",
        "turnId": "t1",
        "approvalId": "a1",
        "kind": "tool_call",
        "payload": {"toolName": "bash"},
        "timeoutMs": 5000,
    }
    response = await system.request(req)  # type: ignore[arg-type]
    assert response["action"] == "decline"


@pytest.mark.asyncio
async def test_approval_tty_prompt_blank_declines() -> None:
    """TTY with prompt_fn returning '' (blank/Enter) returns decline."""
    from amplifier_agent_lib.protocol_points.defaults_cli import CliApprovalSystem

    system = CliApprovalSystem(override=None, is_tty=True, prompt_fn=lambda _: "")
    req = {
        "sessionId": "s1",
        "turnId": "t1",
        "approvalId": "a1",
        "kind": "tool_call",
        "payload": {"toolName": "bash"},
        "timeoutMs": 5000,
    }
    response = await system.request(req)  # type: ignore[arg-type]
    assert response["action"] == "decline"

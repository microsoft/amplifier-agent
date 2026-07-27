"""Tests for the shared skill-sigil dispatcher.

``dispatch_skill_or_execute`` is the single implementation both faces call (the
CLI via ``_runtime.make_turn_handler``, the HTTP face via
``_session_runner.run_chat_turn``), so its contract is worth pinning cheaply here
rather than only through the DTU e2e suite.

The load-bearing property is THE USER-TURN INVARIANT: the ``!amplifier:skill``
sigil is honored ONLY on a human-authored user turn. Skills execute tools, so if
the sigil were honored from any other role, whoever can place text into the
conversation could invoke one: the host via a system prompt, the model itself via
assistant text, or an upstream tool via a result.

Two things every non-user case asserts:
  1. ``load_skill`` was NOT invoked (no dispatch), and
  2. the turn still ran normally with the original prompt (no dropped turn).

The second matters as much as the first. A gate that silently swallowed the turn
would be a denial of service, not a defense.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_agent_lib.skill_dispatch import (
    SKILL_SIGIL,
    USER_TURN_ROLE,
    dispatch_skill_or_execute,
    parse_skill_sigil,
)

SKILL = "e2e-sigil-probe"
SIGIL_PROMPT = f"{SKILL_SIGIL} {SKILL}"
BODY = f"# {SKILL}\n\nWrite the sentinel and stop."


def _session(*, load_skill_output: Any = None, tool_mounted: bool = True) -> tuple[MagicMock, AsyncMock, AsyncMock]:
    """Build a fake session.

    Returns ``(session, execute_mock, tool_execute_mock)``.

    ``execute_mock`` is ``session.execute``. ``tool_execute_mock`` is the mounted
    ``load_skill`` tool's ``execute``; assert against it to prove whether dispatch
    happened, since a dispatched skill calls the tool directly.
    """
    execute_mock = AsyncMock(return_value="normal reply")

    tool_result = MagicMock()
    tool_result.success = True
    tool_result.output = load_skill_output if load_skill_output is not None else {"content": BODY}
    tool_execute_mock = AsyncMock(return_value=tool_result)

    tool = MagicMock()
    tool.execute = tool_execute_mock

    session = MagicMock()
    session.execute = execute_mock
    session.coordinator.get.return_value = tool if tool_mounted else None
    return session, execute_mock, tool_execute_mock


# --------------------------------------------------------------------------- #
# parse_skill_sigil: the pure parse
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "prompt,expected",
    [
        (f"{SKILL_SIGIL} {SKILL}", (SKILL, "")),
        (f"{SKILL_SIGIL} {SKILL} some args", (SKILL, "some args")),
        (f"   {SKILL_SIGIL} {SKILL}", (SKILL, "")),
        (f"{SKILL_SIGIL}\t{SKILL}", (SKILL, "")),
        # Arguments are preserved verbatim, internal spacing included.
        (f"{SKILL_SIGIL} {SKILL} a  b   c", (SKILL, "a  b   c")),
        # Non-sigil prompts.
        ("just a normal prompt", None),
        ("", None),
        # Bare sigil with no skill name.
        (SKILL_SIGIL, None),
        (f"{SKILL_SIGIL}   ", None),
        # Sigil must be a prefix, not merely present.
        (f"please run {SKILL_SIGIL} {SKILL}", None),
        # Prefix-adjacent text must not match.
        (f"{SKILL_SIGIL}x {SKILL}", None),
    ],
)
def test_parse_skill_sigil(prompt: str, expected: tuple[str, str] | None) -> None:
    assert parse_skill_sigil(prompt) == expected


# --------------------------------------------------------------------------- #
# The user-turn gate
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_sigil_on_user_turn_dispatches() -> None:
    """The permitted case: a sigil on a live user turn invokes load_skill."""
    session, execute_mock, tool_execute = _session()

    reply = await dispatch_skill_or_execute(session, SIGIL_PROMPT, prompt_role=USER_TURN_ROLE)

    tool_execute.assert_awaited_once_with({"skill_name": SKILL, "arguments": ""})
    # An inline skill body is executed so the agent actually follows it.
    execute_mock.assert_awaited_once_with(BODY)
    assert reply == "normal reply"


@pytest.mark.asyncio
async def test_sigil_passes_arguments_verbatim() -> None:
    """Text after the skill name reaches the tool unchanged."""
    session, _execute, tool_execute = _session()

    await dispatch_skill_or_execute(session, f"{SIGIL_PROMPT} keep  this   spacing", prompt_role=USER_TURN_ROLE)

    tool_execute.assert_awaited_once_with({"skill_name": SKILL, "arguments": "keep  this   spacing"})


@pytest.mark.parametrize("role", ["system", "developer", "assistant", "tool", None, "", "User", "USER"])
@pytest.mark.asyncio
async def test_sigil_on_non_user_turn_does_not_dispatch(role: str | None) -> None:
    """Every non-user role is refused, and the turn still runs normally.

    ``None`` is the fail-closed default a caller gets by not declaring a role.
    ``"User"`` / ``"USER"`` confirm the comparison is exact rather than
    case-insensitive, so a host sending an oddly-cased role cannot slip through.
    """
    session, execute_mock, tool_execute = _session()

    reply = await dispatch_skill_or_execute(session, SIGIL_PROMPT, prompt_role=role)

    tool_execute.assert_not_awaited()
    # The turn is NOT dropped: the original prompt still ran, unchanged.
    execute_mock.assert_awaited_once_with(SIGIL_PROMPT)
    assert reply == "normal reply"


@pytest.mark.asyncio
async def test_non_sigil_prompt_on_user_turn_executes_normally() -> None:
    """The common case: an ordinary prompt is untouched."""
    session, execute_mock, tool_execute = _session()

    reply = await dispatch_skill_or_execute(session, "what is the capital of France?", prompt_role=USER_TURN_ROLE)

    tool_execute.assert_not_awaited()
    execute_mock.assert_awaited_once_with("what is the capital of France?")
    assert reply == "normal reply"


@pytest.mark.asyncio
async def test_non_sigil_prompt_on_non_user_turn_executes_normally() -> None:
    """A continuation turn with no sigil is unaffected by the gate."""
    session, execute_mock, tool_execute = _session()

    await dispatch_skill_or_execute(session, "", prompt_role=None)

    tool_execute.assert_not_awaited()
    execute_mock.assert_awaited_once_with("")


@pytest.mark.asyncio
async def test_non_user_sigil_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    """A refused sigil is visible to an operator rather than silently ignored."""
    session, _execute, _tool = _session()

    with caplog.at_level("WARNING"):
        await dispatch_skill_or_execute(session, SIGIL_PROMPT, prompt_role="system")

    messages = [record.getMessage() for record in caplog.records]
    assert any("non-user turn" in m for m in messages), (
        f"expected a warning naming the refused non-user sigil, got: {messages}"
    )
    assert any("role='system'" in m for m in messages), f"the warning should name the offending role, got: {messages}"


# --------------------------------------------------------------------------- #
# Fail-open behavior (preserved from the pre-extraction implementation)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_missing_load_skill_tool_falls_back_to_normal_execute() -> None:
    """No load_skill mounted: run the prompt rather than failing the turn."""
    session, execute_mock, _tool = _session(tool_mounted=False)

    reply = await dispatch_skill_or_execute(session, SIGIL_PROMPT, prompt_role=USER_TURN_ROLE)

    execute_mock.assert_awaited_once_with(SIGIL_PROMPT)
    assert reply == "normal reply"


@pytest.mark.asyncio
async def test_tool_exception_falls_back_to_normal_execute() -> None:
    """A raising tool must not crash the turn."""
    session, execute_mock, tool_execute = _session()
    tool_execute.side_effect = RuntimeError("boom")

    reply = await dispatch_skill_or_execute(session, SIGIL_PROMPT, prompt_role=USER_TURN_ROLE)

    execute_mock.assert_awaited_once_with(SIGIL_PROMPT)
    assert reply == "normal reply"


@pytest.mark.asyncio
async def test_unsuccessful_load_falls_back_to_normal_execute() -> None:
    """A skill that does not load falls back to the normal agent loop."""
    session, execute_mock, tool_execute = _session()
    failed = MagicMock()
    failed.success = False
    failed.error = "no such skill"
    tool_execute.return_value = failed

    reply = await dispatch_skill_or_execute(session, SIGIL_PROMPT, prompt_role=USER_TURN_ROLE)

    execute_mock.assert_awaited_once_with(SIGIL_PROMPT)
    assert reply == "normal reply"


@pytest.mark.asyncio
async def test_fork_skill_output_becomes_the_reply_directly() -> None:
    """A fork skill already ran in a sub-session; its response is the reply."""
    session, execute_mock, _tool = _session(load_skill_output={"response": "fork result", "context": "fork"})

    reply = await dispatch_skill_or_execute(session, SIGIL_PROMPT, prompt_role=USER_TURN_ROLE)

    # No second execute: the body was not re-run through the agent loop.
    execute_mock.assert_not_awaited()
    assert reply == "fork result"

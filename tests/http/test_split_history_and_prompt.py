"""Tests for ``_split_history_and_prompt``: which message becomes the prompt.

The function decides what text is submitted as THIS turn's prompt and what is
replayed as history. That choice is a privilege boundary, not just formatting:
``prompt`` is the only text eligible for ``!amplifier:skill`` sigil dispatch (see
THE USER-TURN INVARIANT in ``amplifier_agent_lib.skill_dispatch``), while
``history`` is never scanned.

The case that motivated this file: the function used to search BACKWARDS for the
last ``role=user`` message. When a client posted an array ending in ``assistant``,
that search reached back to an EARLIER, already-answered user turn and re-submitted
it. If that stale turn contained a sigil, the skill re-dispatched, even though the
client was not asking for it now. The role gate could not catch it because the
message genuinely was ``role=user``; it simply was not the current turn.
"""

from __future__ import annotations

from typing import Any

from amplifier_agent_http._wire import ChatMessage
from amplifier_agent_http.routes.chat_completions import _split_history_and_prompt

SIGIL = "!amplifier:skill e2e-sigil-probe"


def _msgs(*pairs: tuple[str, str]) -> list[ChatMessage]:
    """Build a message list from ``(role, content)`` pairs."""
    return [ChatMessage(role=role, content=content) for role, content in pairs]  # type: ignore[arg-type]


def _roles(history: list[dict[str, Any]]) -> list[str]:
    return [m.get("role", "") for m in history]


# --------------------------------------------------------------------------- #
# The security case: a stale user turn must never become the prompt.
# --------------------------------------------------------------------------- #


def test_trailing_assistant_does_not_resubmit_stale_user_turn() -> None:
    """A sigil in an already-answered user turn must not become this turn's prompt.

    The client is not invoking a skill here. It sent a conversation whose last
    message is the assistant's reply. Honoring the sigil from the earlier turn
    would re-run the skill on a turn the user did not submit.
    """
    messages = _msgs(
        ("user", SIGIL),
        ("assistant", "I ran that skill for you."),
    )

    _history, prompt, prompt_role, _eligible = _split_history_and_prompt(messages)

    assert prompt != SIGIL, (
        "the stale user turn was re-submitted as this turn's prompt; a sigil in "
        "already-answered history would re-dispatch the skill"
    )
    assert prompt_role != "user", (
        f"prompt_role={prompt_role!r} marks this as a live user turn, which would open "
        "the sigil gate for replayed history"
    )


def test_trailing_assistant_preserves_the_assistant_reply_in_history() -> None:
    """The assistant's own last turn must survive into history, not be discarded.

    The backwards search truncated at the last user message
    (``messages[:last_user_idx]``), so everything after it, including the
    assistant reply being continued from, was dropped.
    """
    messages = _msgs(
        ("user", "hello"),
        ("assistant", "hi there"),
    )

    history, prompt, prompt_role, _eligible = _split_history_and_prompt(messages)

    assert _roles(history) == ["user", "assistant"], (
        f"expected the full conversation in history, got roles={_roles(history)}"
    )
    assert prompt == "", f"expected an empty continuation prompt, got {prompt!r}"
    assert prompt_role is None, f"expected prompt_role=None for a continuation, got {prompt_role!r}"


def test_trailing_assistant_with_sigil_deep_in_history() -> None:
    """Same guarantee when the sigil sits several turns back."""
    messages = _msgs(
        ("user", SIGIL),
        ("assistant", "done"),
        ("user", "thanks"),
        ("assistant", "you are welcome"),
    )

    _history, prompt, prompt_role, _eligible = _split_history_and_prompt(messages)

    assert SIGIL not in prompt, f"a historical sigil leaked into the prompt: {prompt!r}"
    assert prompt_role != "user", f"prompt_role={prompt_role!r} would open the sigil gate on replayed history"


# --------------------------------------------------------------------------- #
# Regression: the paths that already worked must keep working.
# --------------------------------------------------------------------------- #


def test_normal_user_turn_is_the_prompt() -> None:
    """The ordinary case: the array ends with the user's new message."""
    messages = _msgs(
        ("user", "first"),
        ("assistant", "reply"),
        ("user", "second"),
    )

    history, prompt, prompt_role, _eligible = _split_history_and_prompt(messages)

    assert prompt == "second"
    assert prompt_role == "user"
    assert _roles(history) == ["user", "assistant"]


def test_live_user_sigil_still_dispatches() -> None:
    """A sigil the client is submitting NOW stays eligible. The fix must not over-correct."""
    messages = _msgs(
        ("user", "earlier question"),
        ("assistant", "earlier answer"),
        ("user", SIGIL),
    )

    _history, prompt, prompt_role, _eligible = _split_history_and_prompt(messages)

    assert prompt == SIGIL
    assert prompt_role == "user"


def test_tool_continuation_unchanged() -> None:
    """Case 1: an array ending in role=tool keeps its empty-prompt continuation."""
    messages = [
        ChatMessage(role="user", content="do the thing"),
        ChatMessage(role="assistant", content=None, tool_calls=[{"id": "c1", "type": "function", "function": {}}]),
        ChatMessage(role="tool", content="result", tool_call_id="c1"),
    ]

    history, prompt, prompt_role, _eligible = _split_history_and_prompt(messages)

    assert prompt == ""
    assert prompt_role is None
    assert _roles(history) == ["user", "assistant", "tool"]


def test_no_user_message_at_all() -> None:
    """Case 3: nothing to prompt with, and no user turn to honor a sigil on."""
    messages = _msgs(("assistant", "orphaned reply"))

    _history, prompt, prompt_role, _eligible = _split_history_and_prompt(messages)

    assert prompt == ""
    assert prompt_role is None


def test_single_user_message() -> None:
    """The very first turn of a conversation."""
    messages = _msgs(("user", SIGIL))

    history, prompt, prompt_role, _eligible = _split_history_and_prompt(messages)

    assert prompt == SIGIL
    assert prompt_role == "user"
    assert history == []

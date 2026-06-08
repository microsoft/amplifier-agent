"""Unit tests for the ``_repair_loaded_transcript_if_needed`` helper in
``_runtime.py``.

The helper bridges ``SessionStore.load`` and ``context.set_messages``, calling
the foundation's ``diagnose_transcript`` / ``repair_transcript`` against the
loaded entries.  These tests exercise the helper in isolation so failures are
attributable to the repair contract itself rather than to the full
``make_turn_handler`` wiring (covered by ``test_runtime_resume_wiring.py``).

The helper mirrors the app-cli pattern (PR #156 + PR #146,
microsoft/amplifier-app-cli) — sessions interrupted mid-tool-call can persist
orphaned ``tool_calls`` with no matching ``tool`` result, ordering violations,
or incomplete assistant turns; replaying such a transcript causes providers
(notably Anthropic) to reject the next LLM call with a 400.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from amplifier_agent_lib._runtime import _repair_loaded_transcript_if_needed
from amplifier_agent_lib.session_store import SessionStore

# ---------------------------------------------------------------------------
# Healthy-path tests
# ---------------------------------------------------------------------------


def test_empty_transcript_returns_unchanged(tmp_path) -> None:
    """An empty list short-circuits before diagnosis — no I/O, no log."""
    store = SessionStore(tmp_path)
    result = _repair_loaded_transcript_if_needed([], session_id="sess-empty", store=store)
    assert result == []


def test_healthy_transcript_passes_through_unchanged(tmp_path) -> None:
    """A healthy conversational transcript must be returned identical."""
    store = SessionStore(tmp_path)
    transcript: list[dict] = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "how are you?"},
        {"role": "assistant", "content": "fine, thanks"},
    ]
    original_snapshot = [dict(e) for e in transcript]

    result = _repair_loaded_transcript_if_needed(transcript, session_id="sess-healthy", store=store)

    # Returned list is the original (identity-preserved on healthy path).
    assert result is transcript
    # And the contents were not mutated (no leaked line_num key).
    assert transcript == original_snapshot
    for entry in transcript:
        assert "line_num" not in entry


def test_healthy_transcript_with_tool_call_pair_passes_through(tmp_path) -> None:
    """A complete tool-call/tool-result pair is healthy; no synthetic injection."""
    store = SessionStore(tmp_path)
    transcript: list[dict] = [
        {"role": "user", "content": "list files"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call_1", "tool": "ls", "input": {}},
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "a.txt\nb.txt"},
        {"role": "assistant", "content": "Two files: a.txt and b.txt"},
    ]

    result = _repair_loaded_transcript_if_needed(transcript, session_id="sess-healthy-tool", store=store)

    assert result is transcript


# ---------------------------------------------------------------------------
# Repair-path tests
# ---------------------------------------------------------------------------


def test_orphaned_tool_call_gets_synthetic_result_injected(tmp_path, caplog) -> None:
    """A tool_call with no matching tool_result must trigger repair.

    Foundation's repair_transcript injects a synthetic tool_result for the
    orphaned id so the next provider call has a complete tool_use/tool_result
    pair.  A warning must be logged for operator visibility.
    """
    store = SessionStore(tmp_path)
    session_id = "sess-orphan"
    broken: list[dict] = [
        {"role": "user", "content": "list files"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call_orphan", "tool": "ls", "input": {}},
            ],
        },
        # NB: no {"role": "tool", "tool_call_id": "call_orphan", ...} —
        # this is the broken-transcript shape produced by an interrupted
        # tool execution (Ctrl+C, SIGKILL, OOM, MCP drop).
    ]

    with caplog.at_level(logging.WARNING, logger="amplifier_agent_lib._runtime"):
        repaired = _repair_loaded_transcript_if_needed(broken, session_id=session_id, store=store)

    # Repair returns a NEW list (not the original).
    assert repaired is not broken
    # The synthetic tool result was injected after the assistant tool_calls
    # entry, so the repaired transcript is strictly longer.
    assert len(repaired) > len(broken)
    # A tool message with the orphaned id must now exist.
    tool_results = [e for e in repaired if e.get("role") == "tool" and e.get("tool_call_id") == "call_orphan"]
    assert len(tool_results) == 1, (
        f"Expected one synthetic tool result for the orphaned call_orphan; got repaired transcript: {repaired!r}"
    )
    # No line_num keys leak into the returned dicts.
    for entry in repaired:
        assert "line_num" not in entry, f"line_num leaked into repaired output: {entry!r}"
    # The warning was emitted with session_id correlation.
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any(session_id in r.getMessage() for r in warning_records), (
        f"Expected a WARNING mentioning session_id={session_id}; got: {[r.getMessage() for r in warning_records]}"
    )


def test_repaired_transcript_is_persisted_back_to_disk(tmp_path) -> None:
    """After repair, the cleaned transcript must be written back via
    ``store.save`` so the next ``--resume`` starts clean even if this turn
    also fails.  Mirrors PR #146 in microsoft/amplifier-app-cli.
    """
    store = SessionStore(tmp_path)
    session_id = "sess-writeback"
    broken: list[dict] = [
        {"role": "user", "content": "list files"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call_orphan_wb", "tool": "ls", "input": {}},
            ],
        },
    ]
    # Pre-write the broken transcript so we have a baseline to overwrite.
    store.save(session_id, broken, metadata={"last_turn": "complete"})

    repaired = _repair_loaded_transcript_if_needed(broken, session_id=session_id, store=store)

    # Read back from disk; the persisted transcript must match the repaired
    # in-memory result, not the original broken list.
    reloaded = store.load(session_id)
    assert reloaded is not None
    persisted_transcript, _ = reloaded
    assert persisted_transcript == repaired
    assert persisted_transcript != broken


def test_writeback_failure_is_non_fatal(tmp_path, caplog) -> None:
    """If ``store.save`` raises during write-back, the helper must still
    return the in-memory repaired transcript so the current turn can proceed.
    A flaky disk should not prevent recovery — log and continue.
    """
    # Build a real store, then swap its .save method to raise.
    store = SessionStore(tmp_path)
    save_mock = MagicMock(side_effect=OSError("disk full"))
    store.save = save_mock  # type: ignore[method-assign]

    broken: list[dict] = [
        {"role": "user", "content": "list files"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call_nonfatal", "tool": "ls", "input": {}},
            ],
        },
    ]

    with caplog.at_level(logging.ERROR, logger="amplifier_agent_lib._runtime"):
        repaired = _repair_loaded_transcript_if_needed(broken, session_id="sess-nonfatal", store=store)

    # The helper still returned the repaired in-memory transcript.
    assert repaired is not broken
    assert len(repaired) > len(broken)
    # save was attempted exactly once.
    assert save_mock.call_count == 1
    # An exception was logged (logger.exception emits at ERROR with traceback).
    assert any(
        "Failed to persist repaired transcript" in r.getMessage() for r in caplog.records if r.levelno == logging.ERROR
    ), f"Expected ERROR log about persist failure; got: {[(r.levelno, r.getMessage()) for r in caplog.records]}"


def test_original_input_is_not_mutated_on_repair(tmp_path) -> None:
    """The helper annotates ``line_num`` on shallow copies, not on the input.

    Foundation's diagnostic prefers ``line_num`` annotations; we add them to
    copies so callers' lists stay pristine.  This guards against accidentally
    leaking ``line_num`` back into ``set_messages`` or the persisted JSONL.
    """
    store = SessionStore(tmp_path)
    broken: list[dict] = [
        {"role": "user", "content": "list files"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call_no_mutate", "tool": "ls", "input": {}},
            ],
        },
    ]
    original_snapshot = [dict(e) for e in broken]

    _repair_loaded_transcript_if_needed(broken, session_id="sess-no-mutate", store=store)

    # The input list and its entries are untouched.
    assert broken == original_snapshot
    for entry in broken:
        assert "line_num" not in entry


# ---------------------------------------------------------------------------
# Failure-mode coverage: misplaced tool result
# ---------------------------------------------------------------------------


def test_incomplete_assistant_turn_after_tool_result_is_repaired(tmp_path) -> None:
    """A tool_result followed directly by another user message — with no
    assistant response in between — is an incomplete turn.  Providers expect
    an assistant response to a tool result before the next user message.

    Foundation's repair injects a synthetic assistant response at that gap so
    the next provider call sees a complete turn structure.
    """
    store = SessionStore(tmp_path)
    broken: list[dict] = [
        {"role": "user", "content": "list files"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call_ok", "tool": "ls", "input": {}},
            ],
        },
        {"role": "tool", "tool_call_id": "call_ok", "content": "a.txt"},
        # Missing: the assistant's reply summarising the tool result.
        {"role": "user", "content": "next prompt"},
    ]

    repaired = _repair_loaded_transcript_if_needed(broken, session_id="sess-incomplete", store=store)

    # Repair returns a NEW list strictly longer than the input.
    assert repaired is not broken
    assert len(repaired) > len(broken)
    # A synthetic assistant message must now sit between the tool result and
    # the second user message.
    tool_idx = next(i for i, e in enumerate(repaired) if e.get("role") == "tool")
    assert repaired[tool_idx + 1]["role"] == "assistant", (
        f"Expected synthetic assistant response after tool result; got repaired transcript: {repaired!r}"
    )


# ---------------------------------------------------------------------------
# Pytest config: mark all as sync (no asyncio)
# ---------------------------------------------------------------------------


pytestmark = pytest.mark.filterwarnings("error::DeprecationWarning")

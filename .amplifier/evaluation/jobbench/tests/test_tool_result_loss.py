"""Pin harness-side detection of provider tool-result loss.

Root cause lives outside this harness: when a tool_call has no paired
tool_result, amplifier-module-provider-anthropic injects a synthetic
"[SYSTEM ERROR: Tool result missing from conversation history]" message and
only logs a `logger.warning` (see
amplifier_module_provider_anthropic/__init__.py:2014). The model then
narrates that error back in its own words, burning wall-clock time on a
degenerate loop while exit code, deliverables, and score all stay normal.
That combination is what makes the condition dangerous: it publishes a
plausible number rather than failing.

Fixing the provider is out of scope here; these tests pin only the harness's
detection of it, using synthetic agent.log fixtures. No real benchmark
content appears in this file.
"""

from __future__ import annotations

from pathlib import Path

from jobbench.trial import TOOL_RESULT_LOSS_SIGNATURES, _detect_tool_result_loss


def test_clean_log_produces_no_warning(tmp_path: Path) -> None:
    log_path = tmp_path / "agent.log"
    log_path.write_text(
        "$ some-agent-cli run\n"
        "doing the task normally, no issues here\n"
        "\n--- stderr ---\n"
        "\n--- exit 0 (12.3s) ---\n\n",
        encoding="utf-8",
    )
    assert _detect_tool_result_loss(log_path) is None


def test_missing_log_produces_no_warning(tmp_path: Path) -> None:
    """A log that was never created (e.g. crash before exec) is not an error."""
    assert _detect_tool_result_loss(tmp_path / "does-not-exist.log") is None


def test_single_signature_detected_with_count(tmp_path: Path) -> None:
    log_path = tmp_path / "agent.log"
    log_path.write_text(
        "$ some-agent-cli run\n"
        "I should check what I've already done here.\n"
        "[SYSTEM ERROR: Tool result missing from conversation history]\n"
        "Let me re-read the task instructions to be sure...\n"
        "\n--- stderr ---\n"
        "\n--- exit 0 (240.1s) ---\n\n",
        encoding="utf-8",
    )
    warning = _detect_tool_result_loss(log_path)
    assert warning is not None
    assert warning["kind"] == "tool_result_loss"
    assert warning["count"] == 1
    assert "SYSTEM ERROR" in warning["detail"]


def test_repeated_occurrences_counted(tmp_path: Path) -> None:
    """Multiple occurrences across a long-running loop are all counted, not
    just detected as a boolean."""
    log_path = tmp_path / "agent.log"
    body = "[SYSTEM ERROR: Tool result missing from conversation history]\n" * 3
    log_path.write_text(
        f"$ agent run\n{body}\n--- stderr ---\n\n--- exit 0 (300s) ---\n\n", encoding="utf-8"
    )
    warning = _detect_tool_result_loss(log_path)
    assert warning is not None
    assert warning["count"] == 3


def test_second_signature_also_detected(tmp_path: Path) -> None:
    """The interrupted-tool-execution variant is recognized independently
    of the SYSTEM ERROR wording."""
    log_path = tmp_path / "agent.log"
    log_path.write_text(
        "$ agent run\n"
        "Tool execution was interrupted and no result was captured\n"
        "\n--- stderr ---\n\n--- exit 0 (5s) ---\n\n",
        encoding="utf-8",
    )
    warning = _detect_tool_result_loss(log_path)
    assert warning is not None
    assert warning["count"] == 1


def test_both_signatures_sum_into_one_count(tmp_path: Path) -> None:
    log_path = tmp_path / "agent.log"
    log_path.write_text(
        "$ agent run\n"
        f"{TOOL_RESULT_LOSS_SIGNATURES[0]}\n"
        f"{TOOL_RESULT_LOSS_SIGNATURES[1]}\n"
        "\n--- stderr ---\n\n--- exit 0 (10s) ---\n\n",
        encoding="utf-8",
    )
    warning = _detect_tool_result_loss(log_path)
    assert warning is not None
    assert warning["count"] == 2


# ---------------------------------------------------------------------------
# Narration heuristic
#
# The literal signatures above are frequently NOT observable: the provider
# injects that text into the message history sent to the model and only logs a
# local warning. For a CLI whose stdout carries just assistant prose, the
# string never reaches agent.log -- measured 0 hits on a real run that looped
# 27 times. These tests pin the indirect fallback that catches that case.
# ---------------------------------------------------------------------------


def _log(prose: str, stderr: str = "") -> str:
    return f"$ agent run\n{prose}\n\n--- stderr ---\n{stderr}\n--- exit 0 (1.0s) ---\n"


def test_narration_below_threshold_is_not_flagged(tmp_path: Path) -> None:
    """One re-read is ordinary agent behavior, not evidence of a fault."""
    log_path = tmp_path / "agent.log"
    log_path.write_text(_log("I need to actually read the config file first."), encoding="utf-8")

    assert _detect_tool_result_loss(log_path) is None


def test_repeated_narration_is_flagged_as_heuristic(tmp_path: Path) -> None:
    """Repetition is the tell: the model keeps re-deciding it never read a file."""
    log_path = tmp_path / "agent.log"
    prose = (
        "I still need to actually read the instructions file.\n"
        "Good, that confirms my understanding.\n"
        "I realize I haven't actually read the instructions yet.\n"
        "I notice I have been operating on inferred context.\n"
        "Let me correct that without actually opening it earlier."
    )
    log_path.write_text(_log(prose), encoding="utf-8")

    result = _detect_tool_result_loss(log_path)
    assert result is not None
    assert result["kind"] == "tool_result_loss"
    assert result["confidence"] == "heuristic"
    assert result["count"] >= 3


def test_direct_signature_outranks_narration(tmp_path: Path) -> None:
    """A literal provider signature is proof, so it must not be downgraded."""
    log_path = tmp_path / "agent.log"
    prose = "\n".join(
        [
            TOOL_RESULT_LOSS_SIGNATURES[0],
            "I need to actually read the file.",
            "I haven't actually read it yet.",
            "I realize I have not opened it.",
        ]
    )
    log_path.write_text(_log(prose), encoding="utf-8")

    result = _detect_tool_result_loss(log_path)
    assert result is not None
    assert result["confidence"] == "direct"


def test_stderr_half_is_not_scanned_for_narration(tmp_path: Path) -> None:
    """The CLI's own TUI echo would otherwise inflate the count without evidence.

    A terminal UI re-renders its todo list on every update, so a checked item
    like "Read the instructions directly" can appear many times without the
    model having re-decided anything.
    """
    log_path = tmp_path / "agent.log"
    echoed = "\n".join(["[x] Need to actually read the instructions"] * 10)
    log_path.write_text(_log("did the task cleanly", stderr=echoed), encoding="utf-8")

    assert _detect_tool_result_loss(log_path) is None

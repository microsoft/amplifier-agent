"""SC-4 — Resume continuity end-to-end test.

Validates that context is preserved across two separate CLI invocations that
share the same --session-id, where the second invocation uses --resume.

Per design A7, context-simple is assumed to replay transcripts when
is_resumed=True.  If it does not, this test will FAIL and the fix is to swap
the context module in bundle.md from context-simple to context-persistent.

Requires ANTHROPIC_API_KEY to be set; skipped otherwise.
"""

from __future__ import annotations

import json
import os
import subprocess


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    """Run ``amplifier-agent`` via ``uv run`` in a real subprocess.

    Parameters
    ----------
    *args:
        Extra arguments forwarded to ``amplifier-agent run`` (e.g.
        ``"--session-id"``, ``"my-id"``, ``"--fresh"``).

    Returns
    -------
    subprocess.CompletedProcess[str]
        Completed process with stdout/stderr captured.
    """
    return subprocess.run(
        ["uv", "run", "amplifier-agent", *args],
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_resume_continuity_two_turns_share_context() -> None:
    """Two turns with the same session-id share context via resume.

    Strategy:
      - Turn 1 (--fresh): plant "My favorite color is purple. Please remember it."
      - Turn 2 (--resume): ask "What is my favorite color?"
      - Assert 'purple' appears in the turn-2 reply.

    If this test fails with context-simple, the fix is to swap the context
    module in bundle.md from context-simple to context-persistent per design A7.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        import pytest

        pytest.skip("ANTHROPIC_API_KEY not set — skipping live API test")

    session_id = "test-resume-cont-001"

    # Turn 1: plant the fact in a fresh session.
    turn1 = _run(
        "run",
        "--session-id",
        session_id,
        "--fresh",
        "My favorite color is purple. Please remember it.",
    )
    assert turn1.returncode == 0, (
        f"Turn 1 failed (exit {turn1.returncode}).\nstdout:\n{turn1.stdout}\nstderr:\n{turn1.stderr}"
    )

    # Turn 2: resume the session and ask for the fact.
    turn2 = _run(
        "run",
        "--session-id",
        session_id,
        "--resume",
        "--output",
        "json",
        "What is my favorite color?",
    )
    assert turn2.returncode == 0, (
        f"Turn 2 failed (exit {turn2.returncode}).\nstdout:\n{turn2.stdout}\nstderr:\n{turn2.stderr}"
    )

    # Parse the JSON reply from turn 2.
    try:
        result = json.loads(turn2.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"Turn 2 stdout is not valid JSON: {exc!r}\nstdout:\n{turn2.stdout}\nstderr:\n{turn2.stderr}"
        ) from exc

    reply: str = result.get("reply", "")
    assert "purple" in reply.lower(), (
        f"'purple' not found in turn-2 reply: {reply!r}\n\n"
        "If this fails, swap the context module in bundle.md from "
        "context-simple to context-persistent per design A7:\n\n"
        "  context:\n"
        "    module: context-persistent\n"
        "    source: git+https://github.com/microsoft/amplifier-module-context-persistent@main\n"
        "    config:\n"
        "      max_tokens: 300000"
    )

"""End-to-end delegation test — proves session.spawn capability works.

This test verifies the full delegation flow:
  1. amplifier-agent run receives a prompt asking it to use delegate
  2. The delegate tool finds session.spawn registered on the coordinator
  3. A child AmplifierSession is spawned for the explorer agent
  4. The child executes the instruction and returns a reply
  5. The parent returns the child's reply as JSON

The test requires a real ANTHROPIC_API_KEY in the environment.  If the key is
absent, the test is SKIPPED — it is NOT marked xfail because successful delegation
is the expected outcome.

Mark: ``pytest.mark.integration`` so slow tests can be excluded with:
    pytest -m "not integration"

But included by default in ``pytest -q`` runs as specified by the task scope.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

# Phrases that must NOT appear in the output — indicate delegation failed.
_DELEGATION_FAILURE_PHRASES = [
    "delegation isn't available",
    "spawning capability",
    "app layer must register",
    "session spawning not available",
    "session.spawn",  # The error message text from tool-delegate
]


def _run_amplifier_agent(prompt: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    """Run ``amplifier-agent run --output json <prompt>`` via ``uv run`` in a real subprocess."""
    merged_env = os.environ.copy()
    return subprocess.run(
        ["uv", "run", "amplifier-agent", "run", "--output", "json", prompt],
        capture_output=True,
        text=True,
        env=merged_env,
        timeout=timeout,
    )


@pytest.mark.integration
def test_delegation_spawns_child_and_returns_pong() -> None:
    """Delegation via the delegate tool must produce a reply containing 'PONG'.

    Acceptance criteria (from task spec §4):
    - exit code 0
    - stdout is parseable JSON with a 'reply' key
    - reply contains 'PONG'
    - reply does NOT contain delegation-failure phrases

    This test requires a real LLM call (ANTHROPIC_API_KEY must be set).
    Skip, not fail, if the key is absent.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key or api_key.startswith("sk-test"):
        pytest.skip("ANTHROPIC_API_KEY not set — skipping live delegation test")

    prompt = (
        "Use the delegate tool to spawn the explorer agent. "
        "Ask the explorer to return the literal string PONG and nothing else. "
        "Return only the explorer's reply, verbatim."
    )

    result = _run_amplifier_agent(prompt, timeout=180)

    # ---- exit code 0 -------------------------------------------------------
    assert result.returncode == 0, (
        f"amplifier-agent exited {result.returncode}.\nstdout: {result.stdout[:500]!r}\nstderr: {result.stderr[:500]!r}"
    )

    # ---- parse JSON reply ----------------------------------------------------
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(f"stdout is not valid JSON: {exc}\nstdout: {result.stdout[:500]!r}")

    reply: str = data.get("reply", "")
    assert reply, f"'reply' key is missing or empty in JSON output: {data!r}"

    # ---- reply contains PONG ------------------------------------------------
    assert "PONG" in reply.upper(), f"Expected 'PONG' in reply but got: {reply!r}"

    # ---- delegation-failure phrases must be absent --------------------------
    combined = (result.stdout + result.stderr).lower()
    for phrase in _DELEGATION_FAILURE_PHRASES:
        assert phrase.lower() not in combined, (
            f"Delegation failure phrase found in output: {phrase!r}\n"
            f"stdout: {result.stdout[:500]!r}\n"
            f"stderr: {result.stderr[:200]!r}"
        )


@pytest.mark.integration
def test_explorer_bash_tool_mounts_in_child_session() -> None:
    """Explorer's tool-bash must actually mount and execute in the child session.

    Capstone test for the agent-tools install gap fix: the parent (orchestrator)
    does NOT have tool-bash — it only has tool-todo and tool-delegate.  If
    bundle.load_agent_metadata() was not called before Bundle.prepare(), the
    BundleModuleResolver never installed tool-bash and the explorer sub-session
    starts without it.

    This test delegates to the explorer agent and asks it to run a bash command.
    A successful ``echo HELLOFROMBASH`` result proves:
      1. tool-bash was installed at cold-prepare time (via load_agent_metadata)
      2. The child session's resolver contains the tool-bash module path
      3. The bash tool mounts cleanly and executes in the child's AmplifierSession

    Acceptance criteria (from task spec §3):
    - exit code 0
    - stdout is parseable JSON with a 'reply' key
    - reply contains 'HELLOFROMBASH'

    This test requires a real LLM call (ANTHROPIC_API_KEY must be set).
    Skip, not fail, if the key is absent.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key or api_key.startswith("sk-test"):
        pytest.skip("ANTHROPIC_API_KEY not set — skipping live bash delegation test")

    prompt = (
        "Use the delegate tool to spawn the explorer agent. "
        "Have it run 'echo HELLOFROMBASH' in a bash shell and return that exact "
        "output verbatim. Return only the explorer's reply."
    )

    result = _run_amplifier_agent(prompt, timeout=180)

    # ---- exit code 0 -------------------------------------------------------
    assert result.returncode == 0, (
        f"amplifier-agent exited {result.returncode}.\nstdout: {result.stdout[:500]!r}\nstderr: {result.stderr[:500]!r}"
    )

    # ---- parse JSON reply ----------------------------------------------------
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(f"stdout is not valid JSON: {exc}\nstdout: {result.stdout[:500]!r}")

    reply: str = data.get("reply", "")
    assert reply, f"'reply' key is missing or empty in JSON output: {data!r}"

    # ---- reply contains HELLOFROMBASH ---------------------------------------
    assert "HELLOFROMBASH" in reply, (
        f"Expected 'HELLOFROMBASH' in reply but got: {reply!r}\n"
        "This likely means tool-bash is not mounted in the explorer child session. "
        "Verify that bundle.load_agent_metadata() is called before Bundle.prepare() "
        "in amplifier_agent_lib/bundle/loader.py."
    )

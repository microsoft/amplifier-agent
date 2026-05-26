"""Phase A — CR-B stdout-discipline test.

A bundle module that calls print() during turn execution must NOT corrupt
the JSON envelope on real stdout. The 50 prints land on stderr; the envelope
on stdout remains parseable.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from click.testing import CliRunner

from amplifier_agent_cli.modes.single_turn import run


def test_noisy_module_prints_do_not_corrupt_envelope() -> None:
    """A bundle calling print() 50 times must not break envelope parsing."""

    async def noisy_execute(spec):
        # Simulates a bundle module that prints to "stdout" during turn.
        for i in range(50):
            print(f"DEBUG line {i} from a misbehaving module")
        return {"sessionId": spec.session_id or "", "turnId": "turn-1", "reply": "hi"}

    # NOTE: click 8.2+ removed the `mix_stderr` kwarg; stdout/stderr are now
    # always captured separately, which is the behavior `mix_stderr=False`
    # used to provide. See spec for original signature.
    runner = CliRunner()
    with (
        patch("amplifier_agent_cli.modes.single_turn._execute_turn", side_effect=noisy_execute),
        patch("amplifier_agent_cli.provider_detect.detect_provider", return_value="anthropic"),
    ):
        result = runner.invoke(run, ["--session-id", "sid-1", "hello"])

    assert result.exit_code == 0, (result.stdout, result.stderr)
    # Critical: stdout must parse as a single JSON envelope despite the 50 prints.
    envelope = json.loads(result.stdout)
    assert envelope["reply"] == "hi"
    # The 50 print lines must appear on stderr, not stdout.
    assert "DEBUG line 0" in result.stderr
    assert "DEBUG line 49" in result.stderr
    # And NOT on stdout:
    assert "DEBUG line" not in result.stdout

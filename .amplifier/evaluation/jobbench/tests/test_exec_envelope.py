"""Unit tests for the DTU CLI exec JSON-envelope unwrap (fail-loud setup_cmds).

Current DTU CLI versions report the INNER command's result as a JSON envelope
on stdout ({id, command, exit_code, stdout, stderr}) and exit 0 themselves.
Any caller that checks the outer `CommandResult.returncode` without unwrapping
tests the CLI process, not the command -- a failed in-DTU gate is recorded but
never enforced. Observed live in an earlier evaluation run: all six trials
recorded a FAILED code-identity gate (envelope "exit_code": 1) and ran to
full metered completion.

Adapted from amplifier-bundle-evaluation's harness test suite. The
install_agent/grader-path composed-state tests were dropped -- those modules
aren't vendored here -- but every assertion on `_unwrap_exec_envelope` itself
is kept intact; that's the function this harness actually depends on.
"""

from __future__ import annotations

import json

from jobbench.dtu import _unwrap_exec_envelope

# A realistically shaped failing envelope: the outer CLI exits 0 while the
# inner command exited 1, with multi-line stdout and stderr both present.
FAILED_ENVELOPE = json.dumps(
    {
        "id": "dtu-1a2b3c4d",
        "command": "bash -lc 'set -e\\n...setup verification...'",
        "exit_code": 1,
        "stdout": (
            "--- setup verification ---\n"
            "checking installed package version\n"
            "found version 0.1.0, expected 0.2.0\n"
        ),
        "stderr": (
            "ERROR: setup verification failed -- installed version does not "
            "match the requested version\n"
            "hint: rerun the install step before continuing\n"
        ),
    }
)


# ---------------------------------------------------------------------------
# _unwrap_exec_envelope unit contract
# ---------------------------------------------------------------------------


def test_unwrap_failed_envelope():
    rc, stdout, stderr = _unwrap_exec_envelope(0, FAILED_ENVELOPE, "")
    assert rc == 1
    assert "found version 0.1.0, expected 0.2.0" in stdout
    assert "setup verification failed" in stderr


def test_unwrap_success_envelope():
    env = json.dumps(
        {
            "id": "dtu-x",
            "command": "bash -lc 'true'",
            "exit_code": 0,
            "stdout": "ok\n",
            "stderr": "",
        }
    )
    rc, stdout, stderr = _unwrap_exec_envelope(0, env, "")
    assert (rc, stdout, stderr) == (0, "ok\n", "")


def test_plain_stdout_passthrough():
    """Plain (non-JSON) stdout -- e.g. `--stream` mode output or a mock
    backend that doesn't wrap -- passes through untouched."""
    rc, stdout, stderr = _unwrap_exec_envelope(1, "boom\n", "err\n")
    assert (rc, stdout, stderr) == (1, "boom\n", "err\n")
    rc, stdout, stderr = _unwrap_exec_envelope(0, "hello world\n", "")
    assert (rc, stdout, stderr) == (0, "hello world\n", "")


def test_json_lookalike_stdout_passthrough():
    """A command whose real output is JSON without the envelope keys passes through."""
    payload = json.dumps({"result": "ok", "count": 3})
    rc, stdout, stderr = _unwrap_exec_envelope(0, payload, "")
    assert (rc, stdout, stderr) == (0, payload, "")


def test_non_int_exit_code_passthrough():
    env = json.dumps({"command": "x", "exit_code": "1", "stdout": "", "stderr": ""})
    assert _unwrap_exec_envelope(0, env, "")[0] == 0


def test_warning_on_unrecognizable_stdout(caplog):
    """Outer success + unrecognizable stdout passes through, but LOUDLY:
    the real CLI always envelopes, so plain output at rc 0 means the
    envelope shape drifted -- that must show up in logs, not vanish."""
    import logging

    with caplog.at_level(logging.WARNING, logger="jobbench.dtu"):
        rc, stdout, stderr = _unwrap_exec_envelope(0, "hello world\n", "")
    assert (rc, stdout, stderr) == (0, "hello world\n", "")
    assert any("not a recognizable JSON envelope" in r.message for r in caplog.records)

    # A proper envelope unwraps silently -- no drift warning.
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="jobbench.dtu"):
        _unwrap_exec_envelope(0, FAILED_ENVELOPE, "")
    assert not caplog.records

    # Outer CLI failure passes through silently too (raw contract applies).
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="jobbench.dtu"):
        _unwrap_exec_envelope(7, "boom\n", "cli blew up")
    assert not caplog.records


def test_last_line_envelope_scan():
    """An envelope preceded by other output on the same stream (e.g. a
    wrapper banner) is still found via the last-line fallback."""
    stdout = "some banner line\nanother line\n" + FAILED_ENVELOPE + "\n"
    rc, inner_stdout, stderr = _unwrap_exec_envelope(0, stdout, "")
    assert rc == 1
    assert "found version 0.1.0, expected 0.2.0" in inner_stdout
    assert "setup verification failed" in stderr


def test_nested_output_envelope_unwrapped():
    """Some CLI versions nest the envelope fields under an "output" key --
    unwrapped only after the flat 4-key gate fails."""
    env = json.dumps(
        {
            "id": "dtu-x",
            "output": {
                "command": "bash -lc 'exit 3'",
                "exit_code": 3,
                "stdout": "partial\n",
                "stderr": "gate failed\n",
            },
        }
    )
    rc, stdout, stderr = _unwrap_exec_envelope(0, env, "")
    assert (rc, stdout, stderr) == (3, "partial\n", "gate failed\n")


def test_flat_envelope_wins_over_nested_output():
    """Flat-gate-first ordering: a flat envelope that also carries an
    "output" sub-object is never shadowed by it."""
    env = json.dumps(
        {
            "command": "c",
            "exit_code": 5,
            "stdout": "flat\n",
            "stderr": "",
            "output": {
                "command": "x",
                "exit_code": 9,
                "stdout": "nested\n",
                "stderr": "",
            },
        }
    )
    rc, stdout, _ = _unwrap_exec_envelope(0, env, "")
    assert (rc, stdout) == (5, "flat\n")


def test_lookalike_with_output_subobject_passthrough():
    """JSON command output whose "output" value is not an envelope still
    passes through -- the nested tolerance doesn't widen the lookalike net."""
    payload = json.dumps({"output": {"result": "ok"}, "count": 3})
    rc, stdout, stderr = _unwrap_exec_envelope(0, payload, "")
    assert (rc, stdout, stderr) == (0, payload, "")


def test_outer_failure_never_unwrapped():
    """Outer CLI failure (timeout, container gone) is reported as-is."""
    rc, _, _ = _unwrap_exec_envelope(7, FAILED_ENVELOPE, "cli blew up")
    assert rc == 7


def test_outer_stderr_preserved_alongside_inner():
    rc, _, stderr = _unwrap_exec_envelope(0, FAILED_ENVELOPE, "outer warning\n")
    assert rc == 1
    assert "setup verification failed" in stderr
    assert "outer warning" in stderr

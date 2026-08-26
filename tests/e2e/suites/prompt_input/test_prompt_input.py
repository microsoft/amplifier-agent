"""E2E: a prompt reaches the engine regardless of its SIZE or its leading characters.

Contract under test
-------------------
A prompt is caller data. Its size and its first character are properties of the user's
content, not of our transport, so neither may decide whether a turn can run at all.

    prompt of any size          -> the turn runs
    prompt beginning with '-'   -> the turn runs, the text is not parsed as options

Today the prompt travels as the final positional argv element
(``docs/spec/wrapper-contract.md``, "argv assembly"), which imposes two limits that
belong to the transport rather than to the content:

1. SIZE. Linux caps a single argv element at ``MAX_ARG_STRLEN`` (32 pages = 131072
   bytes); Windows caps the whole command line at 32767 chars. Past that, ``execve``
   fails with ``E2BIG`` before the engine boots. There is no other input path: the run
   command takes only a positional prompt and the engine has no stdin ingestion
   (``docs/spec/cli.md``).

2. LEADING '-'. A positional that begins with '-' is parsed by click as an option, so
   the turn dies with exit 2 before the engine boots -- unless a ``--`` separator
   precedes it.

The same spec already applies the right mitigation to a SMALLER field
(``docs/spec/wrapper-contract.md``, "MCP config spill"):

    "MCP server configuration is always spilled to a file, never passed on argv,
     so a large server map cannot overflow the OS argv limit."

The prompt is the field most likely to be large, and it was left on argv.

Cases
-----
``prompt-file-oversized``             RED  -- >MAX_ARG_STRLEN prompt via --prompt-file runs
``argv-separator-leading-dashes``     CONTROL -- '--' makes a '-'-leading positional run
``prompt-file-rejects-both-inputs``   RED  -- --prompt-file AND a positional is a caller error

Why ``prompt-file-oversized`` also begins with '---'
----------------------------------------------------
It folds the leading-dash half of the contract into the same model call. A file-borne
prompt never touches argv, so its first character must be irrelevant; asserting that
here costs nothing extra and pins both properties of the file path at once.

Why ``argv-separator-leading-dashes`` is a CONTROL and not RED
--------------------------------------------------------------
The engine already honours ``--`` today. That is precisely why it is worth pinning: the
wrapper-side fix for a '-'-leading prompt is to emit ``--`` before the positional, so
this case is the engine-side guarantee that fix depends on. If a future click upgrade or
a ``context_settings`` change breaks it, the wrappers break silently and this case names
the cause.

Why this is a bespoke suite (not ``framework.harness``)
-------------------------------------------------------
``run_cli_case`` hardcodes ``exit_code == 0`` and its ``check`` callable never sees
stderr or the exit code, so it cannot express ``prompt-file-rejects-both-inputs``. This
follows the precedent set by ``suites/modes/test_unknown_mode.py`` and
``suites/skills/test_sigil_dispatch.py`` and builds its commands locally, leaving
``framework/`` untouched (docs/E2E_TESTING.md: "stable; rarely touched").

Why the payload is generated INSIDE the DTU
--------------------------------------------
``dtu.exec_json`` is ``shlex.join``ed into one string and run as ``bash -lc <string>``,
so that string is itself a single argv element on the HOST and is bound by the host's
own ``MAX_ARG_STRLEN``. Passing a 200000-byte literal would fail on the host before it
ever reached the container, testing the harness instead of the engine. Generating the
payload in-container keeps the outer command ~100 bytes and puts the size pressure
exactly where the contract lives. Generation and the run share ONE exec because the DTU
filesystem is not guaranteed to persist between exec calls.
"""

from __future__ import annotations

import json

import pytest
from framework import dtu

pytestmark = pytest.mark.dtu

# Host-config seeded into every DTU by provisioning (anthropic provider, approval "yes").
_CONFIG = "/root/e2e/host-config.json"

_PROMPT_FILE = "/tmp/e2e-oversized-prompt.txt"

# Comfortably past Linux MAX_ARG_STRLEN (131072). Chosen so the case still fails on a
# kernel with a larger page size rather than passing vacuously.
_OVERSIZE_BYTES = 200_000

# A short, closed-class reply keeps the assertion unambiguous and the completion cheap.
_SENTINEL = "banana"

# Leading '---' so the file path's indifference to the first character is pinned too.
_LEAD = "--- turn context ---"

_INSTRUCTION = f"Ignore the padding above. Reply with exactly one word: {_SENTINEL}"


def _generate_prompt_file() -> str:
    """A shell fragment that writes an oversized, '---'-leading prompt to _PROMPT_FILE."""
    return (
        "python3 - <<'PYEOF' > " + _PROMPT_FILE + "\n"
        "import sys\n"
        f"lead = {_LEAD!r}\n"
        f"instruction = {_INSTRUCTION!r}\n"
        f"pad_to = {_OVERSIZE_BYTES}\n"
        "line = 'padding that carries no instruction.\\n'\n"
        "body = line * (max(0, pad_to - len(lead) - len(instruction)) // len(line) + 1)\n"
        "sys.stdout.write(lead + '\\n' + body + '\\n' + instruction + '\\n')\n"
        "PYEOF"
    )


def _envelope(result: dict, context: str) -> dict:
    """Parse the section 4.1 JSON envelope from a run's stdout."""
    stdout = result.get("stdout", "")
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    raise AssertionError(
        f"[{context}] no JSON envelope on stdout\n"
        f"exit_code: {result.get('exit_code')}\n"
        f"stdout:\n{stdout}\nstderr:\n{result.get('stderr', '')}"
    )


def _fail_detail(context: str, result: dict) -> str:
    return (
        f"[{context}] exit_code={result.get('exit_code')}\n"
        f"stdout:\n{result.get('stdout', '')}\n"
        f"stderr:\n{result.get('stderr', '')}"
    )


def test_prompt_file_oversized(dtu_id: str) -> None:
    """A prompt past the OS argv limit runs, and its leading '-' is not parsed as an option.

    RED until the engine grows a non-argv prompt input path. The failure to expect on old
    code is exit 2 with "No such option '--prompt-file'".
    """
    command = (
        _generate_prompt_file()
        + "\n"
        + " ".join(
            [
                "amplifier-agent run -y",
                f"--config {_CONFIG}",
                "--output json",
                f"--prompt-file {_PROMPT_FILE}",
            ]
        )
    )
    result = dtu.exec_json(dtu_id, ["bash", "-lc", command])

    context = "prompt-file-oversized"
    assert result.get("exit_code") == 0, _fail_detail(context, result)

    envelope = _envelope(result, context)
    assert not envelope.get("error"), f"[{context}] unexpected error: {envelope.get('error')!r}"
    assert _SENTINEL in envelope.get("reply", "").lower(), (
        f"[{context}] expected {_SENTINEL!r} in reply, got {envelope.get('reply', '')!r}"
    )


def test_argv_separator_preserves_leading_dashes(dtu_id: str) -> None:
    """`--` makes a '-'-leading positional prompt reach the engine intact.

    CONTROL. Green today. This is the engine-side guarantee the wrapper fix relies on
    when it emits `--` before the positional prompt.
    """
    prompt = f"{_LEAD}\n{_INSTRUCTION}"
    result = dtu.exec_json(
        dtu_id,
        [
            "amplifier-agent",
            "run",
            "-y",
            "--config",
            _CONFIG,
            "--output",
            "json",
            "--",
            prompt,
        ],
    )

    context = "argv-separator-leading-dashes"
    assert result.get("exit_code") == 0, _fail_detail(context, result)

    envelope = _envelope(result, context)
    assert not envelope.get("error"), f"[{context}] unexpected error: {envelope.get('error')!r}"
    assert _SENTINEL in envelope.get("reply", "").lower(), (
        f"[{context}] expected {_SENTINEL!r} in reply, got {envelope.get('reply', '')!r}"
    )


def test_prompt_file_rejects_both_inputs(dtu_id: str) -> None:
    """Supplying BOTH --prompt-file and a positional prompt is a caller error.

    Two sources of truth for one field is ambiguous, and silently preferring one would
    make a host's bug invisible. Mirrors the existing argv-validation convention: the
    section 4.1 envelope on stdout, `error.code` set, exit 2. Costs no model call.

    RED until --prompt-file exists.
    """
    command = (
        _generate_prompt_file()
        + "\n"
        + " ".join(
            [
                "amplifier-agent run -y",
                f"--config {_CONFIG}",
                "--output json",
                f"--prompt-file {_PROMPT_FILE}",
                "-- 'a positional prompt as well'",
            ]
        )
    )
    result = dtu.exec_json(dtu_id, ["bash", "-lc", command])

    context = "prompt-file-rejects-both-inputs"
    assert result.get("exit_code") == 2, _fail_detail(context, result)

    envelope = _envelope(result, context)
    error = envelope.get("error") or {}
    assert error.get("code") == "argv_prompt_conflict", (
        f"[{context}] expected error.code == 'argv_prompt_conflict', got {error!r}"
    )

"""E2E test model + runners.

Pure logic (no side effects beyond the subprocess shell-outs delegated to ``dtu``).
A test case is data: a name, a kind (``cli``, ``http``, or ``cli-multi``), a command
(or ordered ``steps`` for ``cli-multi``), and an optional structural ``check`` on the
parsed output. The baseline criterion is always enforced -- CLI exit 0, HTTP 200 --
so a case with ``check=None`` simply asserts "ran clean".

Multi-step cases (``cli-multi``) share one generated session id across an ordered
sequence of CLI invocations, letting a single ``E2ECase`` exercise stateful flows
such as session-resume. Use the literal token ``"{SID}"`` inside a ``Step.command``
wherever the generated session id belongs; ``run_multi_case`` substitutes it.
"""

from __future__ import annotations

import json
import shlex
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

from . import dtu


@dataclass(frozen=True)
class Step:
    """A single command within a multi-step (``cli-multi``) case.

    Attributes:
        command: The argv WITHOUT the leading ``amplifier-agent``. May contain the
            literal token ``"{SID}"``, replaced with the case's generated session id.
        check: Optional structural assertion on the parsed stdout for this step.
            Receives the JSON-parsed value when parseable, else the raw string.
            Should raise AssertionError on mismatch. ``None`` means no extra check.
    """

    command: list[str]
    check: Callable[[Any], None] | None = None


@dataclass(frozen=True)
class E2ECase:
    """A single end-to-end case.

    Attributes:
        name: Stable identifier (used as the pytest parametrize id).
        kind: ``"cli"`` runs a single amplifier-agent subcommand inside the DTU;
            ``"http"`` issues an HTTP request against the in-DTU server;
            ``"cli-multi"`` runs an ordered sequence of ``steps`` sharing one
            generated session id (see ``run_multi_case``).
        command: For ``cli``, the argv WITHOUT the leading ``amplifier-agent``.
            For ``http``, a ``(method, path)`` tuple. Unused (pass ``[]``) for
            ``cli-multi``, which uses ``steps`` instead.
        check: Optional structural assertion on the parsed stdout / body. Receives
            the JSON-parsed value when parseable, else the raw string. Should raise
            AssertionError on mismatch. ``None`` means only the baseline is enforced.
            Unused for ``cli-multi``; put per-step checks on each ``Step`` instead.
        extra_args: Extra argv appended to CLI commands (unused for http/cli-multi).
        steps: Ordered commands for ``cli-multi``, sharing one generated session id.
        cwd: For ``cli`` cases only. When set, the command is launched from this
            working directory inside the DTU (via ``bash -lc 'cd <cwd> && amplifier-agent ...'``).
            Skill discovery keys off the launch directory, so this controls which
            project ``.amplifier/skills/`` is seen. ``None`` runs from the exec default.
    """

    name: str
    kind: Literal["cli", "http", "cli-multi"]
    command: list[str] | tuple[str, str]
    check: Callable[[Any], None] | None = None
    extra_args: tuple[str, ...] = field(default_factory=tuple)
    steps: tuple[Step, ...] = field(default_factory=tuple)
    cwd: str | None = None


def _parse(raw: str) -> Any:
    """Parse a payload as JSON, falling back to the raw string."""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return raw


def run_cli_case(dtu_id: str, case: E2ECase) -> None:
    """Run a CLI case inside the DTU and enforce baseline + optional check.

    Executes ``amplifier-agent <command> <extra_args>`` via ``dtu.exec_json``.
    Baseline: exit_code == 0. If ``case.check`` is set, parse stdout and call it.
    """
    if not isinstance(case.command, list):
        raise TypeError(f"cli case {case.name!r} must have a list command, got {type(case.command)}")

    if case.cwd:
        inner = "amplifier-agent " + " ".join(shlex.quote(a) for a in [*case.command, *case.extra_args])
        full = f"cd {shlex.quote(case.cwd)} && {inner}"
        result = dtu.exec_json(dtu_id, ["bash", "-lc", full])
    else:
        argv = ["amplifier-agent", *case.command, *case.extra_args]
        result = dtu.exec_json(dtu_id, argv)

    exit_code = result.get("exit_code")
    assert exit_code == 0, (
        f"[{case.name}] expected exit 0, got {exit_code}\n"
        f"stdout:\n{result.get('stdout', '')}\n"
        f"stderr:\n{result.get('stderr', '')}"
    )

    if case.check is not None:
        case.check(_parse(result.get("stdout", "")))


def run_http_case(base_url: str, token: str, dtu_id: str, case: E2ECase) -> None:
    """Run an HTTP case from INSIDE the DTU and enforce baseline + optional check.

    Issues ``curl`` inside the DTU so localhost resolves to the in-DTU server. The
    curl writes the body followed by a newline and the numeric status code; we split
    the trailing line to get the status. Baseline: HTTP 200. If ``case.check`` is set,
    parse the body and call it.
    """
    if not isinstance(case.command, tuple):
        raise TypeError(f"http case {case.name!r} must have a (method, path) tuple, got {type(case.command)}")

    method, path = case.command
    url = f"{base_url}{path}"
    curl = f"curl -s -X {method} -w '\\n%{{http_code}}' -H 'Authorization: Bearer {token}' {url}"
    result = dtu.exec_json(dtu_id, ["bash", "-lc", curl])

    exit_code = result.get("exit_code")
    assert exit_code == 0, f"[{case.name}] curl failed with exit {exit_code}\nstderr:\n{result.get('stderr', '')}"

    raw = result.get("stdout", "")
    body, _, status_line = raw.rpartition("\n")
    status = status_line.strip()
    assert status == "200", f"[{case.name}] expected HTTP 200, got {status!r}\nbody:\n{body}"

    if case.check is not None:
        case.check(_parse(body))


def run_multi_case(dtu_id: str, case: E2ECase) -> None:
    """Run an ordered ``cli-multi`` case: one generated session id, N CLI steps.

    Generates a session id (``e2e-<name>-<8 hex chars>``), substitutes it for the
    literal token ``"{SID}"`` in each ``Step.command``, then runs the steps in
    order via ``dtu.exec_json``. Baseline (exit_code == 0) is enforced per step;
    a failure stops the case immediately. If a step's ``check`` is set, parse its
    stdout and call it.
    """
    if case.kind != "cli-multi":
        raise TypeError(f"case {case.name!r} is not cli-multi (kind={case.kind!r})")
    if not case.steps:
        raise ValueError(f"cli-multi case {case.name!r} has no steps")

    sid = f"e2e-{case.name}-{uuid4().hex[:8]}"

    for i, step in enumerate(case.steps):
        argv = ["amplifier-agent", *(token if token != "{SID}" else sid for token in step.command)]
        result = dtu.exec_json(dtu_id, argv)

        exit_code = result.get("exit_code")
        assert exit_code == 0, (
            f"[{case.name}] step {i} expected exit 0, got {exit_code}\n"
            f"stdout:\n{result.get('stdout', '')}\n"
            f"stderr:\n{result.get('stderr', '')}"
        )

        if step.check is not None:
            step.check(_parse(result.get("stdout", "")))

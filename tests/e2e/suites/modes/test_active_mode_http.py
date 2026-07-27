"""Wire contract for ``activeMode`` on POST /v1/chat/completions.

The HTTP face detects a mode from the ``[amplifier-agent:mode=<name>]`` directive on a
system/developer message and applies it to the turn. This suite pins the other half of
that contract: the response must tell the client WHICH mode ran, so a mode-aware host
(the opencode launcher) can read it back instead of guessing.

The contract, mirroring the CLI envelope's ``metadata.activeMode``:

  streaming (stream:true)    -> the terminal stop chunk carries top-level ``activeMode``
  non-streaming (stream:false) -> the chat.completion body carries top-level ``activeMode``

The field is ALWAYS present on a successful turn, and is ``null`` when no mode is active.
Present-and-null is what distinguishes "no mode" from "this server does not report modes",
so the no-mode cases assert key presence, not just a falsy value.

Why this is a bespoke suite (not framework.harness.run_http_case): the shared http runner
issues a bodyless request from a ``(method, path)`` tuple, and these cases need a POST body.
framework/ stays untouched (docs/E2E_TESTING.md: "stable; rarely touched").

Regression guard for item 12b (notes/skills_modes_open_issues.md): ``mode`` used to be
threaded into ``_stream_chat_completion`` and then never read, so every response reported
null, and ``_collect_completion`` did not carry the field at all. Three of these four cases
failed before that fix.
"""

from __future__ import annotations

import json
import shlex
from typing import Any

import pytest
from framework import dtu

pytestmark = pytest.mark.dtu

# Short prompt: these cases assert on the terminal frame only, so generation length is
# pure cost. The streaming suite owns the long-generation timing checks.
PROMPT = "Reply with exactly: ok"

# A mode shipped with amplifier-agent (see MODES in cases.py).
MODE = "plan"
DIRECTIVE = f"[amplifier-agent:mode={MODE}]"

# (name, messages, stream, expected activeMode)
CASES: list[tuple[str, list[dict[str, str]], bool, str | None]] = [
    (
        "activemode-stream-with-mode",
        [{"role": "system", "content": DIRECTIVE}, {"role": "user", "content": PROMPT}],
        True,
        MODE,
    ),
    (
        "activemode-stream-no-mode",
        [{"role": "user", "content": PROMPT}],
        True,
        None,
    ),
    (
        "activemode-buffered-with-mode",
        [{"role": "system", "content": DIRECTIVE}, {"role": "user", "content": PROMPT}],
        False,
        MODE,
    ),
    (
        "activemode-buffered-no-mode",
        [{"role": "user", "content": PROMPT}],
        False,
        None,
    ),
]

# Sentinel appended by curl -w after the transfer, carrying the final HTTP status.
_META = "__META__"


def _post(dtu_id: str, base_url: str, token: str, body: dict[str, Any]) -> tuple[str, str]:
    """POST a chat completion from INSIDE the DTU. Returns (status, raw stdout body)."""
    payload = shlex.quote(json.dumps(body))
    curl = (
        f"curl -sN -X POST {base_url}/v1/chat/completions "
        f"-H 'Authorization: Bearer {token}' "
        f"-H 'Content-Type: application/json' "
        f"-w '\\n{_META}%{{http_code}}\\n' "
        f"-d {payload}"
    )
    result = dtu.exec_json(dtu_id, ["bash", "-lc", curl])
    assert result.get("exit_code") == 0, f"curl failed: {result.get('stderr')}"

    stdout = result.get("stdout", "")
    status = ""
    lines: list[str] = []
    for line in stdout.splitlines():
        if line.startswith(_META):
            status = line[len(_META) :].strip()
            continue
        lines.append(line)
    return status, "\n".join(lines)


def _terminal_frame(status: str, raw: str, *, stream: bool) -> dict[str, Any]:
    """Extract the frame that must carry ``activeMode``.

    Streaming: the SSE chunk whose choices[0].finish_reason is set (the stop chunk).
    Non-streaming: the whole chat.completion body.
    """
    assert status == "200", f"expected HTTP 200, got {status!r}\nbody:\n{raw}"

    if not stream:
        return json.loads(raw)

    for line in raw.splitlines():
        if not line.startswith("data: "):
            continue
        data = line[len("data: ") :].strip()
        if data == "[DONE]":
            continue
        try:
            obj = json.loads(data)
        except json.JSONDecodeError:
            continue
        choices = obj.get("choices") or []
        if choices and choices[0].get("finish_reason"):
            return obj
    raise AssertionError(f"no terminal chunk (finish_reason set) in SSE stream:\n{raw}")


@pytest.mark.parametrize("name,messages,stream,expected", CASES, ids=[c[0] for c in CASES])
def test_http_active_mode(
    name: str,
    messages: list[dict[str, str]],
    stream: bool,
    expected: str | None,
    dtu_id: str,
    server: dict[str, str],
    model_id: str,
) -> None:
    """The response must echo the mode that ran (``null`` when none), on both transports."""
    body = {"model": model_id, "stream": stream, "messages": messages}
    status, raw = _post(dtu_id, server["base_url"], server["token"], body)
    frame = _terminal_frame(status, raw, stream=stream)

    assert "activeMode" in frame, (
        f"[{name}] terminal frame has no activeMode key -- the field must always be present "
        f"(null when no mode is active), so clients can distinguish 'no mode' from "
        f"'server does not report modes'\nframe:\n{frame!r}"
    )
    assert frame["activeMode"] == expected, (
        f"[{name}] expected activeMode={expected!r}, got {frame['activeMode']!r}\nframe:\n{frame!r}"
    )

"""E2E streaming tests for POST /v1/chat/completions.

These verify that token streaming actually happens at the amplifier-agent HTTP
level, not merely that a response comes back. The trap this suite avoids: a
buffered server can emit every SSE frame in one burst at the very end, so a
structural check ("we got delta frames") passes even when streaming is broken.
To catch that, we timestamp each SSE frame as it arrives and assert the content
was delivered incrementally over time, not all at once.

Why this is a bespoke suite (not framework.harness.run_http_case): the shared
http runner shells a plain `curl` inside the DTU and buffers the whole body, so
it cannot time individual frames. Here we run `curl -N` (unbuffered) piped
through a per-line `date +%s.%N` stamp, then parse the timestamped transcript
host-side. framework/ stays untouched (docs/E2E_TESTING.md: "stable; rarely
touched").

Three cases:
  streaming-plain     stream:true                         -> must stream
  streaming-buffered  stream:false                        -> detector sanity: NOT a stream
  streaming-mode      stream:true + [amplifier-agent:mode=plan] system msg
                      -> regression probe. Same streaming assertions as plain.
                         A failure here (while plain passes) localizes a streaming
                         regression to the active-mode path at the amplifier-agent
                         layer.
"""

from __future__ import annotations

import json
import shlex

import pytest
from framework import dtu

pytestmark = pytest.mark.dtu

# Longer natural-language generation so a genuine stream spreads its tokens over
# several seconds of wall-clock, while any buffered-at-end delivery collapses the
# arrival spread toward zero.
PROMPT = "Write a 3 paragraph essay about bananas."

# Minimum wall-clock gap between the first and last content token. A real stream
# clears this easily on a multi-paragraph generation; an all-at-once (buffered)
# response collapses toward 0. Deliberately loose so model-speed jitter never
# false-fails. Tune from the control runs if needed.
MIN_SPREAD_S = 0.5

# Sentinel appended by curl -w after the transfer completes, carrying the final
# HTTP status and content-type on one line: "__META__<status>\t<content_type>".
_META = "__META__"

# Host-config seeded into every DTU by provisioning (the modes suite relies on the
# same path). Selects the anthropic provider + default model for `amplifier-agent run`.
_CLI_CONFIG = "/root/e2e/host-config.json"

# (name, body-extra merged over {model, ...}, expectation)
CASES: list[tuple[str, dict, str]] = [
    (
        "streaming-plain",
        {"stream": True, "messages": [{"role": "user", "content": PROMPT}]},
        "stream",
    ),
    (
        "streaming-buffered",
        {"stream": False, "messages": [{"role": "user", "content": PROMPT}]},
        "buffered",
    ),
    (
        "streaming-mode",
        {
            "stream": True,
            "messages": [
                {"role": "system", "content": "[amplifier-agent:mode=plan]"},
                {"role": "user", "content": PROMPT},
            ],
        },
        "stream",
    ),
]


@pytest.fixture(scope="session")
def model_id(dtu_id: str, server: dict[str, str]) -> str:
    """Resolve a served model id at runtime from GET /v1/models (first entry).

    The served model depends on host-config, so we never hardcode it.
    """
    cmd = f"curl -s -H 'Authorization: Bearer {server['token']}' {server['base_url']}/v1/models"
    result = dtu.exec_json(dtu_id, ["bash", "-lc", cmd])
    assert result.get("exit_code") == 0, f"/v1/models failed: {result.get('stderr')}"
    data = json.loads(result["stdout"])
    models = data.get("data") or []
    assert models, f"no served models: {data}"
    return models[0]["id"]


def _stream_cmd(base_url: str, token: str, body: dict) -> str:
    """Build the in-DTU command: unbuffered curl piped through a per-line timestamp.

    Each emitted line becomes "<epoch.nanos>\\t<raw line>". curl's -w appends a
    trailing "__META__<http_code>\\t<content_type>" line after the body so we can
    assert status and content-type from the same stream.
    """
    payload = json.dumps(body)  # our payloads contain no single quotes
    return (
        f"curl -sN -X POST {base_url}/v1/chat/completions "
        f"-H 'Authorization: Bearer {token}' "
        f"-H 'Content-Type: application/json' "
        f"-w '\\n{_META}%{{http_code}}\\t%{{content_type}}\\n' "
        f"-d '{payload}' "
        f'| while IFS= read -r line; do printf \'%s\\t%s\\n\' "$(date +%s.%N)" "$line"; done'
    )


def _parse_stream(stdout: str) -> dict:
    """Parse the timestamped transcript into structured facts.

    Returns:
      meta:      {"status": str, "content_type": str}
      content:   [(ts: float, text: str)] for each choices[0].delta.content token
      saw_done:  True if a `data: [DONE]` frame was seen
      saw_stop:  True if a frame with finish_reason == "stop" was seen
      body_text: joined non-SSE lines (the JSON body of a stream:false response)
    """
    meta: dict[str, str] = {}
    content: list[tuple[float, str]] = []
    saw_done = False
    saw_stop = False
    body_lines: list[str] = []

    for raw in stdout.splitlines():
        if not raw.strip():
            continue
        ts_str, tab, payload = raw.partition("\t")
        if not tab:
            continue
        if payload.startswith(_META):
            status, _, ctype = payload[len(_META) :].partition("\t")
            meta = {"status": status.strip(), "content_type": ctype.strip()}
            continue
        try:
            ts = float(ts_str)
        except ValueError:
            continue
        if payload.startswith("data: "):
            data = payload[len("data: ") :].strip()
            if data == "[DONE]":
                saw_done = True
                continue
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue
            choices = obj.get("choices") or []
            if not choices:
                continue
            ch0 = choices[0]
            delta = ch0.get("delta") or {}
            text = delta.get("content")
            if isinstance(text, str) and text != "":
                content.append((ts, text))
            if ch0.get("finish_reason") == "stop":
                saw_stop = True
        elif payload.startswith(":"):
            continue  # SSE keepalive comment line
        else:
            body_lines.append(payload)

    return {
        "meta": meta,
        "content": content,
        "saw_done": saw_done,
        "saw_stop": saw_stop,
        "body_text": "\n".join(body_lines),
    }


def _assert_streamed(res: dict) -> None:
    meta = res["meta"]
    assert meta.get("status") == "200", f"status={meta.get('status')} meta={meta}"
    assert "text/event-stream" in meta.get("content_type", ""), (
        f"expected an SSE response, got content_type={meta.get('content_type')!r}"
    )
    assert res["saw_done"], "stream did not terminate with `data: [DONE]`"
    assert res["saw_stop"], "no frame with finish_reason == 'stop'"

    content = res["content"]
    assert len(content) >= 2, (
        f"expected multiple content frames (streaming), got {len(content)} "
        "-- looks like a single buffered content frame"
    )
    ts = [t for t, _ in content]
    spread = ts[-1] - ts[0]
    assert spread >= MIN_SPREAD_S, (
        f"content tokens arrived within {spread:.3f}s (< {MIN_SPREAD_S}s across "
        f"{len(content)} frames): looks buffered/all-at-once, not streamed"
    )


def _assert_buffered(res: dict) -> None:
    """Detector sanity: a stream:false request is NOT an SSE stream."""
    meta = res["meta"]
    assert meta.get("status") == "200", f"status={meta.get('status')} meta={meta}"
    ctype = meta.get("content_type", "")
    assert "text/event-stream" not in ctype, f"stream:false returned SSE: {ctype!r}"
    assert "application/json" in ctype, f"expected JSON body, got {ctype!r}"
    assert not res["content"], (
        f"stream:false yielded {len(res['content'])} SSE content frames; the detector must see this as non-streaming"
    )
    obj = json.loads(res["body_text"])
    msg = obj["choices"][0]["message"]["content"]
    assert isinstance(msg, str) and msg.strip(), "empty buffered completion"


@pytest.mark.parametrize("name,body_extra,expectation", CASES, ids=[c[0] for c in CASES])
def test_streaming(
    name: str,
    body_extra: dict,
    expectation: str,
    dtu_id: str,
    server: dict[str, str],
    model_id: str,
) -> None:
    body = {"model": model_id, **body_extra}
    cmd = _stream_cmd(server["base_url"], server["token"], body)
    result = dtu.exec_json(dtu_id, ["bash", "-lc", cmd])
    assert result.get("exit_code") == 0, f"curl pipeline failed: {result.get('stderr')}"

    res = _parse_stream(result.get("stdout", ""))
    if expectation == "buffered":
        _assert_buffered(res)
    else:
        _assert_streamed(res)


# ---------------------------------------------------------------------------
# CLI streaming (amplifier-agent run)
# ---------------------------------------------------------------------------
#
# The CLI surfaces streaming as `result/delta` wire events on stderr under
# `--display ndjson` (the same streaming hook the serve path turns into SSE).
# stdout carries only the final buffered reply, so the streaming signal lives on
# stderr. We discard stdout, timestamp each stderr line as it arrives, and assert
# the deltas trickle in over time rather than landing all at once. This is a
# different surface than the serve path above (no HTTP server), so it exercises
# whether the engine itself streams, independent of the serve layer.


def _cli_stream_cmd(prompt: str) -> str:
    """Build the in-DTU command: run the CLI, timestamp stderr wire events live.

    `{ ... >/dev/null; } 2>&1` discards the final-reply stdout and routes the
    ndjson wire-event stderr into the timestamp pipe. `set -o pipefail` makes a
    non-zero CLI exit propagate as the pipeline's exit code (the trailing
    while-loop would otherwise mask it). The `|| [ -n "$line" ]` guard emits a
    final line that lacks a trailing newline.
    """
    return (
        "set -o pipefail; "
        f"{{ amplifier-agent run -y --display ndjson --config {_CLI_CONFIG} "
        f"{shlex.quote(prompt)} >/dev/null ; }} 2>&1 "
        f'| while IFS= read -r line || [ -n "$line" ]; do '
        f'printf \'%s\\t%s\\n\' "$(date +%s.%N)" "$line"; done'
    )


def _parse_cli_deltas(stdout: str) -> list[float]:
    """Return arrival timestamps of `result/delta` wire events, in order."""
    stamps: list[float] = []
    for raw in stdout.splitlines():
        if not raw.strip():
            continue
        ts_str, tab, payload = raw.partition("\t")
        if not tab or "result/delta" not in payload:
            continue
        try:
            stamps.append(float(ts_str))
        except ValueError:
            continue
    return stamps


def test_cli_streaming(dtu_id: str) -> None:
    cmd = _cli_stream_cmd(PROMPT)
    result = dtu.exec_json(dtu_id, ["bash", "-lc", cmd])
    assert result.get("exit_code") == 0, f"CLI run failed: {result.get('stderr')}"

    stamps = _parse_cli_deltas(result.get("stdout", ""))
    assert len(stamps) >= 2, (
        f"expected multiple result/delta wire events (streaming), got {len(stamps)} "
        "-- the CLI did not emit incremental deltas"
    )
    spread = stamps[-1] - stamps[0]
    assert spread >= MIN_SPREAD_S, (
        f"result/delta events arrived within {spread:.3f}s (< {MIN_SPREAD_S}s across "
        f"{len(stamps)} events): looks buffered/all-at-once, not streamed"
    )

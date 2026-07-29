"""DTU-backed tests for opt-in raw LLM payload capture.

Contract under test: setting ``debug.rawLlmPayloads: true`` in the host config makes
amplifier-agent record the FULL LLM request and response on the ``llm:request`` and
``llm:response`` events, which hook-context-intelligence writes to the session's
``events.jsonl``. Default is off.

The assertions deliberately check for FULL payloads, not merely the presence of a
``raw`` key. A summary-shaped payload (counts, lengths, truncated schemas) would
satisfy "raw exists" while defeating the entire point of the feature, so each test
pins concrete evidence of fidelity:

  - request: a sentinel token from the prompt must appear verbatim, proving the full
    prompt text was captured rather than a ``prompt_length`` integer.
  - response: ``content`` blocks plus ``usage`` and ``stop_reason`` must be present,
    which only the accumulated final message carries.

The two capture tests were written before the feature as ``xfail(strict=True)`` and
the markers were removed once they went XPASS. ``test_raw_absent_by_default`` was
green throughout and guards the opt-in default: if it ever fails, capture has become
the default and every user is writing full conversation text to disk.
"""

from __future__ import annotations

import json
import shlex
import uuid

import pytest
from framework import dtu

from suites.raw_capture.events import first_named, read_turn_events, resolve_session_dir

pytestmark = pytest.mark.dtu

# Default host-config pushed by provisioning: no `debug` block, so capture is off.
CFG_DEFAULT = "/root/e2e/host-config.json"

# Appears nowhere else in this repository, so finding it inside a captured request
# payload cannot be explained by anything other than the prompt being captured whole.
PROMPT_SENTINEL = "RAWCAP-PROBE-Q9F3"
PROMPT = f"Reply with a short greeting. Ignore this token: {PROMPT_SENTINEL}"


def _sid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _run_cli_turn(dtu_id: str, config: str, session_id: str) -> dict:
    """Run one CLI turn inside the DTU and return the exec result."""
    return dtu.exec_json(
        dtu_id,
        [
            "amplifier-agent",
            "run",
            "-y",
            "--config",
            config,
            "--session-id",
            session_id,
            "--fresh",
            PROMPT,
        ],
    )


def _post_chat(dtu_id: str, base_url: str, token: str, model: str, session_id: str) -> tuple[str, str]:
    """POST one non-streaming completion from inside the DTU. Returns (status, body).

    ``X-Session-Id`` pins the on-disk session bucket to ``http-<session_id>``, which
    is how the test knows which session directory to read events from.
    """
    payload = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": PROMPT}],
            "stream": False,
        }
    )
    meta = "__HTTP_STATUS__"
    cmd = (
        f"curl -s -X POST {base_url}/v1/chat/completions "
        f"-H 'Authorization: Bearer {token}' "
        f"-H 'Content-Type: application/json' "
        f"-H 'X-Session-Id: {session_id}' "
        f"-w '\\n{meta}%{{http_code}}' "
        f"--data-binary {shlex.quote(payload)}"
    )
    result = dtu.exec_json(dtu_id, ["bash", "-lc", cmd])
    assert result.get("exit_code") == 0, f"curl failed: exit={result.get('exit_code')} stderr={result.get('stderr')}"

    raw = result.get("stdout", "")
    body, _, status = raw.rpartition(f"\n{meta}")
    return status.strip(), body


def _assert_full_request(request_data: dict) -> None:
    """Assert `llm:request` carries the complete outbound request, not a summary."""
    assert "raw" in request_data, (
        "llm:request has no 'raw' field. Keys present: "
        f"{sorted(request_data)}. With debug.rawLlmPayloads on, the provider must "
        "attach the full request kwargs."
    )
    raw = request_data["raw"]
    assert isinstance(raw, dict), f"llm:request raw must be a dict of request kwargs, got {type(raw).__name__}"
    assert "model" in raw, f"llm:request raw is missing 'model'. Keys: {sorted(raw)}"

    messages = raw.get("messages")
    assert isinstance(messages, list) and messages, (
        f"llm:request raw must carry a non-empty 'messages' list, got {messages!r}. "
        "A message_count integer is a summary, not a raw request."
    )

    serialized = json.dumps(raw)
    assert PROMPT_SENTINEL in serialized, (
        f"prompt sentinel {PROMPT_SENTINEL!r} is absent from the captured request. "
        "The payload is a summary (lengths/counts) rather than the full prompt text."
    )


def _assert_full_response(response_data: dict) -> None:
    """Assert `llm:response` carries the accumulated final message, not a summary."""
    assert "raw" in response_data, (
        "llm:response has no 'raw' field. Keys present: "
        f"{sorted(response_data)}. With debug.rawLlmPayloads on, the provider must "
        "attach the full response model_dump."
    )
    raw = response_data["raw"]
    assert isinstance(raw, dict), f"llm:response raw must be a dict, got {type(raw).__name__}"

    content = raw.get("content")
    assert isinstance(content, list) and content, (
        f"llm:response raw must carry non-empty 'content' blocks, got {content!r}. "
        "A content_block_count integer is a summary, not a raw response."
    )
    assert isinstance(raw.get("usage"), dict), f"llm:response raw is missing a 'usage' object. Keys: {sorted(raw)}"
    assert raw.get("stop_reason") is not None, f"llm:response raw is missing 'stop_reason'. Keys: {sorted(raw)}"


def test_cli_raw_capture_writes_full_payloads(dtu_id: str, raw_config: str) -> None:
    """CLI turn with capture on records the full request and response in events.jsonl."""
    session_id = _sid("rawcap-cli")

    result = _run_cli_turn(dtu_id, raw_config, session_id)
    assert result.get("exit_code") == 0, (
        f"turn failed (exit {result.get('exit_code')}).\n"
        f"stdout:\n{result.get('stdout', '')}\nstderr:\n{result.get('stderr', '')}"
    )

    session_dir = resolve_session_dir(dtu_id, session_id)
    events = read_turn_events(dtu_id, session_dir)

    _assert_full_request(first_named(events, "llm:request"))
    _assert_full_response(first_named(events, "llm:response"))


def test_http_raw_capture_writes_full_payloads(dtu_id: str, raw_server: dict[str, str], raw_model_id: str) -> None:
    """HTTP turn with capture on records the full request and response in events.jsonl.

    This is the path amplifier-app-opencode takes. It has a second failure mode beyond
    the loader: `_session_runner` clears `mount_plan["providers"]` before injecting, so
    the overlay the lifespan applied is discarded and `provider_config` must be threaded
    through explicitly. A green CLI test with a red one here means that thread broke.
    """
    session_id = _sid("rawcap-http")

    status, body = _post_chat(
        dtu_id,
        raw_server["base_url"],
        raw_server["token"],
        raw_model_id,
        session_id,
    )
    assert status == "200", f"expected HTTP 200, got {status}\nbody:\n{body}"

    session_dir = resolve_session_dir(dtu_id, f"http-{session_id}")
    events = read_turn_events(dtu_id, session_dir)

    _assert_full_request(first_named(events, "llm:request"))
    _assert_full_response(first_named(events, "llm:response"))


def test_raw_absent_by_default(dtu_id: str) -> None:
    """Without a debug block, no raw payload is written. Guards the opt-in default.

    This must stay green before AND after the feature lands. If it ever fails, capture
    has become the default and every user is writing full conversation text to disk.
    """
    session_id = _sid("rawcap-off")

    result = _run_cli_turn(dtu_id, CFG_DEFAULT, session_id)
    assert result.get("exit_code") == 0, (
        f"turn failed (exit {result.get('exit_code')}).\n"
        f"stdout:\n{result.get('stdout', '')}\nstderr:\n{result.get('stderr', '')}"
    )

    session_dir = resolve_session_dir(dtu_id, session_id)
    events = read_turn_events(dtu_id, session_dir)

    request_data = first_named(events, "llm:request")
    assert "raw" not in request_data, (
        f"llm:request carried a raw payload with no debug block in {CFG_DEFAULT}. Capture must be opt-in."
    )

    response_data = first_named(events, "llm:response")
    assert "raw" not in response_data, (
        f"llm:response carried a raw payload with no debug block in {CFG_DEFAULT}. Capture must be opt-in."
    )

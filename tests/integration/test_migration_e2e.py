"""End-to-end migration verification (D9). Real binary, mock LLM."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

# ---------------------------------------------------------------------------
# Helpers copied verbatim from tests/cli/test_mode_a_v2_real_binary.py (lines 25-131)
# ---------------------------------------------------------------------------


def _sse_message(reply_text: str) -> bytes:
    """Build a minimal but complete Anthropic SSE message stream."""
    msg_id = "msg_x"
    model = "claude-3-5-sonnet-20241022"
    events: list[tuple[str, dict]] = [
        (
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": msg_id,
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": model,
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 5, "output_tokens": 0},
                },
            },
        ),
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": reply_text},
            },
        ),
        (
            "content_block_stop",
            {"type": "content_block_stop", "index": 0},
        ),
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 3},
            },
        ),
        (
            "message_stop",
            {"type": "message_stop"},
        ),
    ]
    chunks: list[str] = []
    for event_name, payload in events:
        chunks.append(f"event: {event_name}\n")
        chunks.append(f"data: {json.dumps(payload)}\n\n")
    return "".join(chunks).encode("utf-8")


class _MockLLM(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", "0"))
        _ = self.rfile.read(length)
        body = _sse_message("real-binary-ok")
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-cache")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        # Models API (/v1/models) and other GET probes — return a tiny shape
        # rather than 404 in case the provider warms up the client.
        body = json.dumps({"data": [], "has_more": False}).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args, **kwargs):  # silence stderr noise
        return


@pytest.fixture()
def mock_llm():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = HTTPServer(("127.0.0.1", port), _MockLLM)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        server.shutdown()


def _binary_path() -> str:
    p = shutil.which("amplifier-agent")
    if p is None:
        pytest.skip("amplifier-agent binary not on PATH; run `uv tool install -e .` first")
    return p


# ---------------------------------------------------------------------------
# E4 — Migration moves legacy sessions on first post-upgrade boot
# ---------------------------------------------------------------------------


def test_legacy_sessions_migrated_on_first_boot(mock_llm, tmp_path) -> None:
    """A pre-existing flat sessions/<id>/ is moved to workspaces/_legacy/ on first run (D9)."""
    state_root = tmp_path / "state"
    legacy = state_root / "sessions" / "legacy-1"
    legacy.mkdir(parents=True)
    (legacy / "transcript.jsonl").write_text('{"role":"user","content":"old"}', encoding="utf-8")

    env = os.environ.copy()
    env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{mock_llm}"
    env["ANTHROPIC_API_KEY"] = "test-key"
    env["AMPLIFIER_AGENT_HOME"] = str(tmp_path)

    proc = subprocess.run(
        [
            _binary_path(),
            "run",
            "--session-id",
            "new-1",
            "--workspace",
            "current",
            "--fresh",
            "--output",
            "json",
            "--provider",
            "anthropic",
            "say hi",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )

    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    moved = state_root / "workspaces" / "_legacy" / "sessions" / "legacy-1" / "transcript.jsonl"
    assert moved.is_file(), f"legacy session not migrated to {moved}"
    assert moved.read_text(encoding="utf-8").strip() == '{"role":"user","content":"old"}'
    assert not (state_root / "sessions").exists()

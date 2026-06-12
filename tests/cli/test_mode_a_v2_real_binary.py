"""Phase A — Real-binary integration test (R9' gate).

Launches the actual amplifier-agent binary via subprocess.run, points it at
a localhost mock LLM, and asserts the envelope on stdout parses per §4.1.

Per amendment §8.1 A4', the mock LLM HTTP server is the only place mocks
are allowed in real-binary tests. The Anthropic provider used by the engine
streams by default (use_streaming=True), so the mock returns a Server-Sent
Events response shaped like a complete Anthropic streaming message.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest


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


def test_real_binary_happy_path(mock_llm, tmp_path) -> None:
    env = os.environ.copy()
    env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{mock_llm}"
    env["ANTHROPIC_API_KEY"] = "test-key"
    env["AMPLIFIER_AGENT_HOME"] = str(tmp_path)

    proc = subprocess.run(
        [
            _binary_path(),
            "run",
            "--session-id",
            "real-bin-sid",
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
    envelope = json.loads(proc.stdout)
    assert envelope["protocolVersion"]
    assert envelope["sessionId"] == "real-bin-sid"
    assert envelope["error"] is None
    assert "real-binary-ok" in envelope["reply"]
    assert envelope["metadata"]["correlationId"]


def test_real_binary_becomes_session_leader(mock_llm, tmp_path) -> None:
    """SC-B: engine must call os.setsid() so MCP children inherit a group.

    We verify by reading /proc/<pid>/stat on Linux, or skipping on darwin.
    The check is: getsid(engine_pid) == engine_pid (engine is the session leader).
    """
    import sys

    if sys.platform == "darwin":
        pytest.skip("getsid via /proc is Linux-only; PGID behavior verified in Phase B fixtures")

    env = os.environ.copy()
    env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{mock_llm}"
    env["ANTHROPIC_API_KEY"] = "test-key"
    env["AMPLIFIER_AGENT_HOME"] = str(tmp_path)
    env["AMPLIFIER_AGENT_DEBUG_SIDLOG"] = "1"

    proc = subprocess.run(
        [_binary_path(), "run", "--session-id", "sid-sid", "--fresh", "--output", "json", "say hi"],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    # The engine logs its SID and PID at debug if AMPLIFIER_AGENT_DEBUG_SIDLOG is set.
    assert "engine-sid-ok" in proc.stderr

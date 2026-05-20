# AaA v2 Phase 3 — CLI Mode B (stdio JSON-RPC) Implementation Plan

> **Execution:** Use the subagent-driven-development workflow to implement this plan.
> Use the `executing-plans` superpower for the review loop.

**Goal:** Implement `amplifier-agent run --stdio` (Mode B) — the multi-turn JSON-RPC-over-stdio loop — including framing, stdio-bridged protocol points, capability negotiation, idle timeout, graceful shutdown, and wire-level enforcement of the L14 `result/final` synthesis contract.

**Architecture:** A pure-framing JSON-RPC module (`jsonrpc.py`) handles newline-delimited NDJSON read/write with defensive "skip non-JSON" tolerance. Two `protocol_points` implementations (`StdioApprovalSystem`, `StdioDisplaySystem`) bridge the mode-agnostic engine's protocol points to the JSON-RPC wire. The CLI's `modes/stdio_loop.py` runs the async read loop: it dispatches incoming client requests to `Engine.dispatch()`, routes incoming responses to pending server-initiated requests (approvals), enforces idle timeout, and applies the L14 safety-net synthesis. The library never touches stdin/stdout directly; all I/O flows through injected protocol points or the loop itself.

**Tech Stack:** Python 3.11+, `asyncio` (native; no blocking I/O in Mode B), pytest + pytest-asyncio, `asyncio.create_subprocess_exec` for integration tests, JSON-RPC 2.0 (NDJSON framed).

---

## Context: where this plan fits

| Phase | Status | Provides |
|---|---|---|
| Phase 1 — `amplifier_agent_lib` foundation | Assumed complete | `Engine` class with async `dispatch(request: dict) -> dict`; abstract `ApprovalSystem` and `DisplaySystem` base classes in `protocol_points/`; `protocol/methods.py`, `protocol/notifications.py`, `protocol/errors.py`, `protocol/capabilities.py` (constants + shapes); `defaults_cli.py` for Mode A; `InternalSpawnManager` stub; bundle stub |
| Phase 2 — Mode A CLI | Assumed complete | `amplifier_agent_cli/__main__.py` with argparse, `modes/single_turn.py`, admin verbs (doctor, config show, cache clear), `defaults_cli.py` wired; **`run --stdio` raises `NotImplementedError`** (stub) |
| **Phase 3 — Mode B CLI (this plan)** | TO BUILD | `jsonrpc.py`, `defaults_stdio.py`, `modes/stdio_loop.py`, L14 enforcement, idle timeout, capability negotiation |
| Phase 4 — Bundle + cache | Future | Real vendored bundle; cold-start measurement; real sub-agent spawn |

**Reference plans (read first):**
- `docs/plans/2026-05-18-aaa-v2-phase-1-engine-lib.md` — for engine + protocol contracts
- `docs/plans/2026-05-18-aaa-v2-phase-2-cli-mode-a.md` — for CLI argv layout and the Mode B stub being replaced

**Authoritative design:** `/Users/mpaidiparthy/repos/AaA/opus-recon/aaa-source/docs/designs/aaa-v2-design-checkpoint.md` (commit `c74316c`). Read §5 (non-server design), §6 (approval+display wire), §8 (spawn library-internal), Appendix A (wire protocol), Appendix B (L14 synthesis contract), Appendix C (Mode A vs Mode B).

---

## Scope

**IN scope (Phase 3):**

| File (relative to repo root) | Purpose |
|---|---|
| `src/amplifier_agent_lib/jsonrpc.py` | NDJSON JSON-RPC 2.0 framing — `read_message(reader)`, `write_message(writer, msg)`, message classification helpers, "skip non-JSON" tolerance |
| `src/amplifier_agent_lib/protocol_points/defaults_stdio.py` | `StdioDisplaySystem` (writes notifications), `StdioApprovalSystem` (sends `approval/request`, awaits correlated response, enforces `timeoutMs`) |
| `src/amplifier_agent_cli/modes/stdio_loop.py` | Mode B entry: capability handshake, async read/dispatch loop, idle-timeout, L14 safety-net synthesis, graceful shutdown |
| `src/amplifier_agent_cli/__main__.py` | **Modify** — replace Mode B `NotImplementedError` stub with call to `stdio_loop.run()` |
| `tests/test_jsonrpc.py` | Framing tests |
| `tests/test_defaults_stdio.py` | Protocol-point bridge tests |
| `tests/test_l14_synthesis.py` | L14 contract conformance |
| `tests/cli/test_stdio_loop_*.py` | Stdio loop tests (handshake, dispatch, subprocess integration) |

**OUT of scope:**
- Real bundle prep / cold-start measurement → Phase 4
- Real sub-agent spawn → Phase 4 (Phase 3 uses Phase 1's stub)
- TS / Python wrapper packages → post-L4
- L2 adapters (NanoClaw, Paperclip) → post-L4
- `cache/info` admin RPC method (handled in Phase 2's `doctor` Mode A path)

---

## Conventions (apply to every task)

- **Python 3.11+ `asyncio`-native** — no `time.sleep`, no blocking `input()`, no threaded readers in production code
- **`shell: False`** for all subprocess spawn (only test rigs use subprocess here; `create_subprocess_exec` is shell-free by construction)
- **JSON-RPC 2.0 strictly**: every request has `id` (int or string), every response has matching `id`, notifications have NO `id`. Errors use `{"jsonrpc":"2.0","id":<id>,"error":{"code":<int>,"message":<str>,"data":{"code":<str>}}}`
- **Newline-delimited**: one JSON object per line, `\n` terminator. JSON strings naturally escape embedded newlines as `\\n` — do not post-process
- **"Skip non-JSON lines"** tolerance — defensive read accepts mixed stdout
- **Type hints everywhere** — `from __future__ import annotations` at top; functions annotated; use `TypedDict` for wire shapes
- **Test framework**: `pytest` + `pytest-asyncio` with `@pytest.mark.asyncio` markers (assume `asyncio_mode = "auto"` is set in `pyproject.toml` by Phase 1; if not, mark each test explicitly)
- **Conventional commits**: `feat(scope): …`, `test(scope): …`, `refactor(scope): …`. Phase 3 scope keywords: `jsonrpc`, `stdio`, `mode-b`, `l14`
- **Run command base**: from repo root `/Users/mpaidiparthy/repos/AaA/opus-recon/amplifier-agent/` use `uv run pytest <path> -v` (assume Phase 1 set up `uv` + `pyproject.toml`)

---

## Task list (9 tasks, each 2–5 minutes)

### Task 1: JSON-RPC framing module — read/write

**Files:**
- Create: `src/amplifier_agent_lib/jsonrpc.py`
- Test: `tests/test_jsonrpc.py`

**Step 1: Write the failing test**

Create `tests/test_jsonrpc.py`:

```python
"""Tests for amplifier_agent_lib.jsonrpc — NDJSON JSON-RPC 2.0 framing."""

from __future__ import annotations

import io
import json

import pytest

from amplifier_agent_lib import jsonrpc


@pytest.mark.asyncio
async def test_write_message_appends_newline_and_serializes_json():
    """write_message produces a single line of JSON terminated by \\n."""
    writer = _MemoryWriter()
    msg = {"jsonrpc": "2.0", "id": 1, "method": "agent/initialize", "params": {}}

    await jsonrpc.write_message(writer, msg)

    output = writer.getvalue()
    assert output.endswith(b"\n")
    assert output.count(b"\n") == 1
    assert json.loads(output.decode("utf-8")) == msg


@pytest.mark.asyncio
async def test_read_message_parses_one_line():
    """read_message reads one newline-terminated JSON object."""
    msg = {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}}
    reader = _MemoryReader(json.dumps(msg).encode("utf-8") + b"\n")

    parsed = await jsonrpc.read_message(reader)

    assert parsed == msg


class _MemoryWriter:
    """Minimal asyncio StreamWriter-like adapter backed by BytesIO."""

    def __init__(self) -> None:
        self._buf = io.BytesIO()

    def write(self, data: bytes) -> None:
        self._buf.write(data)

    async def drain(self) -> None:
        return None

    def getvalue(self) -> bytes:
        return self._buf.getvalue()


class _MemoryReader:
    """Minimal asyncio StreamReader-like adapter; serves data in chunks."""

    def __init__(self, data: bytes) -> None:
        self._buf = data
        self._pos = 0

    async def readline(self) -> bytes:
        if self._pos >= len(self._buf):
            return b""
        idx = self._buf.find(b"\n", self._pos)
        if idx == -1:
            line = self._buf[self._pos:]
            self._pos = len(self._buf)
            return line
        line = self._buf[self._pos:idx + 1]
        self._pos = idx + 1
        return line
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_jsonrpc.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'amplifier_agent_lib.jsonrpc'`

**Step 3: Write minimal implementation**

Create `src/amplifier_agent_lib/jsonrpc.py`:

```python
"""NDJSON JSON-RPC 2.0 framing — one JSON object per line, \\n terminated.

This module is pure framing/serialization. It contains NO transport policy,
NO dispatch logic, and NO knowledge of any specific method. Callers compose it
into a transport (stdio_loop) or a unit test harness.

Defensive read tolerates non-JSON lines on the input stream (MCP-fixed pattern):
a malformed or non-JSON line is skipped silently so accidental stdout pollution
in a sub-tool does not crash the protocol bridge.
"""

from __future__ import annotations

import json
from typing import Any, Protocol


class _Reader(Protocol):
    async def readline(self) -> bytes: ...


class _Writer(Protocol):
    def write(self, data: bytes) -> None: ...
    async def drain(self) -> None: ...


async def write_message(writer: _Writer, message: dict[str, Any]) -> None:
    """Serialize a JSON-RPC message and write it as one newline-terminated line.

    JSON serialization naturally escapes embedded newlines in string fields as
    \\n, so the resulting line contains no embedded newlines.
    """
    encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    writer.write(encoded + b"\n")
    await writer.drain()


async def read_message(reader: _Reader) -> dict[str, Any] | None:
    """Read one JSON-RPC message from a newline-delimited stream.

    Returns None on EOF.
    Skips any non-JSON line (defensive — does not crash on stdout pollution).
    """
    while True:
        line = await reader.readline()
        if not line:
            return None
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            # Skip non-JSON line; defensive against accidental stdout pollution.
            continue
        if not isinstance(parsed, dict):
            # Skip non-object JSON (JSON-RPC objects must be objects).
            continue
        return parsed
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_jsonrpc.py -v`
Expected: PASS (2 passed)

**Step 5: Commit**

```bash
git add src/amplifier_agent_lib/jsonrpc.py tests/test_jsonrpc.py
git commit -m "feat(jsonrpc): NDJSON JSON-RPC 2.0 framing with defensive read"
```

---

### Task 2: JSON-RPC framing — skip-non-JSON tolerance + classification helpers

**Files:**
- Modify: `src/amplifier_agent_lib/jsonrpc.py`
- Modify: `tests/test_jsonrpc.py`

**Step 1: Append failing tests**

Append to `tests/test_jsonrpc.py`:

```python
@pytest.mark.asyncio
async def test_read_message_skips_non_json_lines():
    """Defensive read: garbage lines mixed with valid JSON are skipped."""
    garbage = b"this is not json\n"
    msg = {"jsonrpc": "2.0", "id": 42, "result": {}}
    valid = json.dumps(msg).encode("utf-8") + b"\n"
    reader = _MemoryReader(garbage + valid)

    parsed = await jsonrpc.read_message(reader)

    assert parsed == msg


@pytest.mark.asyncio
async def test_read_message_returns_none_on_eof():
    reader = _MemoryReader(b"")
    assert await jsonrpc.read_message(reader) is None


@pytest.mark.asyncio
async def test_read_message_skips_non_object_json():
    """JSON-RPC frames must be objects. Arrays/scalars on stream are skipped."""
    msg = {"jsonrpc": "2.0", "id": 1, "result": "ok"}
    reader = _MemoryReader(
        b"[1,2,3]\n\"a-string\"\n42\n" + json.dumps(msg).encode("utf-8") + b"\n"
    )
    parsed = await jsonrpc.read_message(reader)
    assert parsed == msg


def test_classify_request():
    msg = {"jsonrpc": "2.0", "id": 1, "method": "turn/submit", "params": {}}
    assert jsonrpc.classify(msg) == "request"


def test_classify_response_result():
    assert jsonrpc.classify({"jsonrpc": "2.0", "id": 1, "result": {}}) == "response"


def test_classify_response_error():
    assert (
        jsonrpc.classify({"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "x"}})
        == "response"
    )


def test_classify_notification():
    msg = {"jsonrpc": "2.0", "method": "result/delta", "params": {"text": "hi"}}
    assert jsonrpc.classify(msg) == "notification"


def test_classify_invalid():
    assert jsonrpc.classify({"foo": "bar"}) == "invalid"


def test_make_response_result():
    r = jsonrpc.make_response(id=7, result={"ok": True})
    assert r == {"jsonrpc": "2.0", "id": 7, "result": {"ok": True}}


def test_make_error_with_data():
    r = jsonrpc.make_error(
        id=7,
        code=-32601,
        message="method not found",
        data={"code": "wire_protocol_violation"},
    )
    assert r == {
        "jsonrpc": "2.0",
        "id": 7,
        "error": {
            "code": -32601,
            "message": "method not found",
            "data": {"code": "wire_protocol_violation"},
        },
    }


def test_make_notification():
    n = jsonrpc.make_notification(method="result/delta", params={"text": "hi"})
    assert n == {"jsonrpc": "2.0", "method": "result/delta", "params": {"text": "hi"}}
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_jsonrpc.py -v`
Expected: FAIL on the new tests — `AttributeError: module 'amplifier_agent_lib.jsonrpc' has no attribute 'classify'` (the first three may already pass).

**Step 3: Extend the implementation**

Append to `src/amplifier_agent_lib/jsonrpc.py`:

```python
def classify(message: dict[str, Any]) -> str:
    """Classify a JSON-RPC message as 'request', 'response', 'notification', or 'invalid'.

    - request:      has 'method' AND 'id'
    - notification: has 'method' AND NOT 'id'
    - response:     has 'id' AND ('result' OR 'error')
    - invalid:      anything else
    """
    has_method = "method" in message
    has_id = "id" in message
    has_result_or_error = "result" in message or "error" in message
    if has_method and has_id:
        return "request"
    if has_method and not has_id:
        return "notification"
    if has_id and has_result_or_error:
        return "response"
    return "invalid"


def make_response(*, id: int | str, result: Any) -> dict[str, Any]:
    """Build a JSON-RPC success response."""
    return {"jsonrpc": "2.0", "id": id, "result": result}


def make_error(
    *,
    id: int | str | None,
    code: int,
    message: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a JSON-RPC error response.

    `data` carries the AaA structured error code (e.g. `{"code": "provider_not_configured"}`).
    See protocol/errors.py for the canonical code set.
    """
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": id, "error": err}


def make_notification(*, method: str, params: dict[str, Any]) -> dict[str, Any]:
    """Build a JSON-RPC notification (no id)."""
    return {"jsonrpc": "2.0", "method": method, "params": params}
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_jsonrpc.py -v`
Expected: PASS (11 passed)

**Step 5: Commit**

```bash
git add src/amplifier_agent_lib/jsonrpc.py tests/test_jsonrpc.py
git commit -m "feat(jsonrpc): message classification + constructor helpers"
```

---

### Task 3: `StdioDisplaySystem` — engine notifications → stdout

**Files:**
- Create: `src/amplifier_agent_lib/protocol_points/defaults_stdio.py`
- Create: `tests/test_defaults_stdio.py`

**Background.** Phase 1 defines an abstract `DisplaySystem` in `protocol_points/display.py`. The contract (per design §6, Appendix A): `emit(event_type: str, payload: dict)` is one-way; the engine emits the canonical 9 notification types. `StdioDisplaySystem` translates each `emit()` call into one JSON-RPC notification on stdout. It also tracks whether `result/final` was emitted in the current turn (for L14 safety-net in Task 5).

**Step 1: Write the failing test**

Create `tests/test_defaults_stdio.py`:

```python
"""Tests for protocol_points.defaults_stdio — JSON-RPC bridges."""

from __future__ import annotations

import asyncio
import io
import json

import pytest

from amplifier_agent_lib.protocol_points.defaults_stdio import StdioDisplaySystem


class _MemoryWriter:
    def __init__(self) -> None:
        self._buf = io.BytesIO()

    def write(self, data: bytes) -> None:
        self._buf.write(data)

    async def drain(self) -> None:
        return None

    def getvalue(self) -> bytes:
        return self._buf.getvalue()


def _lines(buf: bytes) -> list[dict]:
    return [json.loads(line) for line in buf.decode("utf-8").splitlines() if line.strip()]


@pytest.mark.asyncio
async def test_emit_writes_jsonrpc_notification():
    writer = _MemoryWriter()
    display = StdioDisplaySystem(writer)

    await display.emit("result/delta", {"text": "hi", "turnId": "t1"})

    msgs = _lines(writer.getvalue())
    assert len(msgs) == 1
    assert msgs[0] == {
        "jsonrpc": "2.0",
        "method": "result/delta",
        "params": {"text": "hi", "turnId": "t1"},
    }


@pytest.mark.asyncio
async def test_emit_result_final_sets_tracking_flag():
    """L14 safety-net depends on knowing whether result/final was emitted."""
    writer = _MemoryWriter()
    display = StdioDisplaySystem(writer)
    assert display.result_final_emitted is False

    await display.emit("result/final", {"text": "done", "turnId": "t1"})

    assert display.result_final_emitted is True


@pytest.mark.asyncio
async def test_reset_for_turn_clears_flag():
    writer = _MemoryWriter()
    display = StdioDisplaySystem(writer)
    await display.emit("result/final", {"text": "done", "turnId": "t1"})
    assert display.result_final_emitted is True

    display.reset_for_turn()

    assert display.result_final_emitted is False
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_defaults_stdio.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'amplifier_agent_lib.protocol_points.defaults_stdio'`

**Step 3: Write the implementation**

Create `src/amplifier_agent_lib/protocol_points/defaults_stdio.py`:

```python
"""Mode B protocol point bridges — stdio JSON-RPC.

`StdioDisplaySystem` translates engine display.emit() calls into one-way
JSON-RPC notifications written to stdout.

`StdioApprovalSystem` (added in Task 4) translates engine approval.request()
calls into server-initiated JSON-RPC `approval/request` and awaits a correlated
response with mandatory timeoutMs.

NOTE: These bridges write to a writer the *stdio_loop* owns. The engine itself
never touches the writer — it sees only the protocol-point interface.
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

from amplifier_agent_lib import jsonrpc


class _Writer(Protocol):
    def write(self, data: bytes) -> None: ...
    async def drain(self) -> None: ...


class StdioDisplaySystem:
    """Engine DisplaySystem bridge for Mode B — emits JSON-RPC notifications.

    Tracks whether `result/final` was emitted in the current turn so the
    stdio_loop can apply the L14 safety-net synthesis (Appendix B).

    Use `reset_for_turn()` before each turn dispatch.
    """

    def __init__(self, writer: _Writer) -> None:
        self._writer = writer
        self._result_final_emitted = False

    @property
    def result_final_emitted(self) -> bool:
        return self._result_final_emitted

    def reset_for_turn(self) -> None:
        self._result_final_emitted = False

    async def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        """Write one JSON-RPC notification for the given event."""
        notification = jsonrpc.make_notification(method=event_type, params=payload)
        await jsonrpc.write_message(self._writer, notification)
        if event_type == "result/final":
            self._result_final_emitted = True
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_defaults_stdio.py -v`
Expected: PASS (3 passed)

**Step 5: Commit**

```bash
git add src/amplifier_agent_lib/protocol_points/defaults_stdio.py tests/test_defaults_stdio.py
git commit -m "feat(stdio): StdioDisplaySystem bridges engine emits to JSON-RPC notifications"
```

---

### Task 4: `StdioApprovalSystem` — bidirectional with mandatory timeout

**Files:**
- Modify: `src/amplifier_agent_lib/protocol_points/defaults_stdio.py`
- Modify: `tests/test_defaults_stdio.py`

**Background.** The engine calls `await approval.request(kind=..., payload=..., timeout_ms=...)` and expects `{action, payload?}` back. `StdioApprovalSystem` must:
1. Assign a fresh request id, build `approval/request`, write to stdout
2. Register a pending Future keyed by that id
3. Await the Future with `asyncio.wait_for(timeout=timeout_ms/1000)`
4. On timeout: emit `approval/timeout` notification (defense in depth per §6) and return `{action: "cancel", reason: "timeout"}`
5. The stdio_loop calls `handle_response(message)` for any incoming JSON-RPC response message, which routes by id

**Step 1: Append failing tests**

Append to `tests/test_defaults_stdio.py`:

```python
from amplifier_agent_lib.protocol_points.defaults_stdio import StdioApprovalSystem


@pytest.mark.asyncio
async def test_approval_request_writes_then_waits_for_response():
    """Happy path: request goes out, response comes back via handle_response."""
    writer = _MemoryWriter()
    display = StdioDisplaySystem(writer)
    approval = StdioApprovalSystem(writer, display=display, id_seed=1000)

    async def respond_after_short_delay():
        await asyncio.sleep(0.01)
        # Inspect the written request to discover the id.
        msgs = _lines(writer.getvalue())
        req = next(m for m in msgs if m.get("method") == "approval/request")
        await approval.handle_response(
            {
                "jsonrpc": "2.0",
                "id": req["id"],
                "result": {"action": "accept", "payload": {"note": "ok"}},
            }
        )

    asyncio.get_event_loop().create_task(respond_after_short_delay())

    result = await approval.request(
        kind="tool_execution",
        payload={"tool": "shell", "command": "rm -rf /"},
        timeout_ms=5000,
    )

    assert result == {"action": "accept", "payload": {"note": "ok"}}
    msgs = _lines(writer.getvalue())
    req_msgs = [m for m in msgs if m.get("method") == "approval/request"]
    assert len(req_msgs) == 1
    assert req_msgs[0]["params"]["kind"] == "tool_execution"
    assert req_msgs[0]["params"]["timeoutMs"] == 5000


@pytest.mark.asyncio
async def test_approval_request_times_out_returns_cancel_and_emits_timeout_notification():
    writer = _MemoryWriter()
    display = StdioDisplaySystem(writer)
    approval = StdioApprovalSystem(writer, display=display, id_seed=2000)

    result = await approval.request(
        kind="tool_execution",
        payload={"tool": "shell"},
        timeout_ms=50,  # 50ms — too short to ever respond
    )

    assert result == {"action": "cancel", "reason": "timeout"}
    msgs = _lines(writer.getvalue())
    method_names = [m.get("method") for m in msgs]
    assert "approval/request" in method_names
    assert "approval/timeout" in method_names  # defense-in-depth notification


@pytest.mark.asyncio
async def test_approval_response_with_unknown_id_is_ignored():
    """Defensive: stray responses do not crash."""
    writer = _MemoryWriter()
    display = StdioDisplaySystem(writer)
    approval = StdioApprovalSystem(writer, display=display, id_seed=3000)

    # No pending request, but a response arrives. Should not raise.
    await approval.handle_response(
        {"jsonrpc": "2.0", "id": 99999, "result": {"action": "decline"}}
    )


@pytest.mark.asyncio
async def test_approval_decline_action_passed_through():
    writer = _MemoryWriter()
    display = StdioDisplaySystem(writer)
    approval = StdioApprovalSystem(writer, display=display, id_seed=4000)

    async def respond():
        await asyncio.sleep(0.01)
        msgs = _lines(writer.getvalue())
        req = next(m for m in msgs if m.get("method") == "approval/request")
        await approval.handle_response(
            {
                "jsonrpc": "2.0",
                "id": req["id"],
                "result": {"action": "decline", "payload": {"reason": "user said no"}},
            }
        )

    asyncio.get_event_loop().create_task(respond())

    result = await approval.request(kind="x", payload={}, timeout_ms=5000)
    assert result["action"] == "decline"
    assert result["payload"]["reason"] == "user said no"
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_defaults_stdio.py -v`
Expected: FAIL — `ImportError: cannot import name 'StdioApprovalSystem'`

**Step 3: Extend the implementation**

Append to `src/amplifier_agent_lib/protocol_points/defaults_stdio.py`:

```python
class StdioApprovalSystem:
    """Engine ApprovalSystem bridge for Mode B.

    On `request()`: emits a server-initiated JSON-RPC `approval/request` with a
    fresh id, awaits a correlated response via Future, and enforces the
    mandatory `timeoutMs` (no infinite-hang risk per §6).

    On `handle_response()`: routes incoming responses by id to the awaiting Future.
    The stdio_loop is responsible for calling handle_response() whenever a JSON-RPC
    response message arrives on stdin.

    On timeout: emits `approval/timeout` notification (defense-in-depth per §6)
    and returns `{"action": "cancel", "reason": "timeout"}`.
    """

    def __init__(
        self,
        writer: _Writer,
        *,
        display: StdioDisplaySystem,
        id_seed: int = 1,
    ) -> None:
        self._writer = writer
        self._display = display
        self._next_id = id_seed
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}

    def _allocate_id(self) -> int:
        cur = self._next_id
        self._next_id += 1
        return cur

    async def request(
        self,
        *,
        kind: str,
        payload: dict[str, Any],
        timeout_ms: int,
    ) -> dict[str, Any]:
        """Send approval/request, await response, enforce timeoutMs."""
        req_id = self._allocate_id()
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = future

        request_msg = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "approval/request",
            "params": {"kind": kind, "payload": payload, "timeoutMs": timeout_ms},
        }
        await jsonrpc.write_message(self._writer, request_msg)

        try:
            return await asyncio.wait_for(future, timeout=timeout_ms / 1000.0)
        except asyncio.TimeoutError:
            # Defense-in-depth: also emit approval/timeout notification.
            await self._display.emit("approval/timeout", {"kind": kind, "payload": payload})
            return {"action": "cancel", "reason": "timeout"}
        finally:
            self._pending.pop(req_id, None)

    async def handle_response(self, message: dict[str, Any]) -> None:
        """Route an incoming JSON-RPC response message to the awaiting Future.

        Unknown ids are ignored silently (defensive).
        Error responses are translated to {"action": "cancel", "reason": "error", ...}.
        """
        req_id = message.get("id")
        if not isinstance(req_id, int):
            return
        future = self._pending.get(req_id)
        if future is None or future.done():
            return
        if "error" in message:
            future.set_result(
                {"action": "cancel", "reason": "error", "error": message["error"]}
            )
            return
        result = message.get("result", {})
        if not isinstance(result, dict):
            future.set_result({"action": "cancel", "reason": "malformed"})
            return
        future.set_result(result)
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_defaults_stdio.py -v`
Expected: PASS (7 passed total)

**Step 5: Commit**

```bash
git add src/amplifier_agent_lib/protocol_points/defaults_stdio.py tests/test_defaults_stdio.py
git commit -m "feat(stdio): StdioApprovalSystem with mandatory timeoutMs and timeout notification"
```

---

### Task 5: L14 synthesis — wire-level safety net

**Files:**
- Create: `tests/test_l14_synthesis.py`
- Modify: `src/amplifier_agent_lib/protocol_points/defaults_stdio.py` (add `synthesize_result_final_if_needed`)

**Background (Appendix B).** The L14 contract requires that any non-empty `turn/submit` reply on the wire be preceded by a `result/final` notification carrying the same text. Phase 3 enforces this in two layers:

1. **Engine-side (primary path)** — `Engine.dispatch()` for `turn/submit` SHOULD emit `result/final` reliably. If Phase 1 didn't already make this guarantee, file a tracking note (do not modify engine in this task — that's an engine-internal change owned by Phase 1's owner).
2. **Wire-level safety-net** — `StdioDisplaySystem` tracks whether `result/final` was emitted; after dispatch returns, the stdio_loop checks `display.result_final_emitted` and, if false AND the reply contains non-empty text, synthesizes a `result/final` notification on the wire BEFORE writing the response.

This task adds the synthesis helper and its conformance tests. The wiring into the stdio_loop happens in Task 7.

**Step 1: Write the failing test**

Create `tests/test_l14_synthesis.py`:

```python
"""Conformance tests for the L14 result/final synthesis contract (design Appendix B)."""

from __future__ import annotations

import io
import json

import pytest

from amplifier_agent_lib.protocol_points.defaults_stdio import (
    StdioDisplaySystem,
    synthesize_result_final_if_needed,
)


class _MemoryWriter:
    def __init__(self) -> None:
        self._buf = io.BytesIO()

    def write(self, data: bytes) -> None:
        self._buf.write(data)

    async def drain(self) -> None:
        return None

    def getvalue(self) -> bytes:
        return self._buf.getvalue()


def _lines(buf: bytes) -> list[dict]:
    return [json.loads(line) for line in buf.decode("utf-8").splitlines() if line.strip()]


@pytest.mark.asyncio
async def test_synthesizes_when_engine_omitted_result_final():
    """If reply has text and no result/final was emitted, synthesize one."""
    writer = _MemoryWriter()
    display = StdioDisplaySystem(writer)
    display.reset_for_turn()
    # Engine did NOT emit result/final.

    reply = {"reply": "the answer is 42", "turnId": "t1"}

    synthesized = await synthesize_result_final_if_needed(display, reply=reply)

    assert synthesized is True
    msgs = _lines(writer.getvalue())
    assert len(msgs) == 1
    assert msgs[0]["method"] == "result/final"
    assert msgs[0]["params"]["text"] == "the answer is 42"
    assert msgs[0]["params"]["turnId"] == "t1"
    assert msgs[0]["params"].get("synthesized") is True  # debug marker per Appendix B


@pytest.mark.asyncio
async def test_does_not_synthesize_when_engine_already_emitted():
    """No double-emission if engine already emitted result/final."""
    writer = _MemoryWriter()
    display = StdioDisplaySystem(writer)
    await display.emit("result/final", {"text": "the answer is 42", "turnId": "t1"})

    before_count = len(_lines(writer.getvalue()))
    reply = {"reply": "the answer is 42", "turnId": "t1"}

    synthesized = await synthesize_result_final_if_needed(display, reply=reply)

    assert synthesized is False
    # No new line written.
    assert len(_lines(writer.getvalue())) == before_count


@pytest.mark.asyncio
async def test_does_not_synthesize_when_reply_text_empty():
    """Empty reply means no result/final to synthesize."""
    writer = _MemoryWriter()
    display = StdioDisplaySystem(writer)
    display.reset_for_turn()

    synthesized = await synthesize_result_final_if_needed(
        display, reply={"reply": "", "turnId": "t1"}
    )

    assert synthesized is False
    assert _lines(writer.getvalue()) == []


@pytest.mark.asyncio
async def test_does_not_synthesize_when_reply_text_none():
    writer = _MemoryWriter()
    display = StdioDisplaySystem(writer)
    display.reset_for_turn()

    synthesized = await synthesize_result_final_if_needed(
        display, reply={"reply": None, "turnId": "t1"}
    )
    assert synthesized is False
    assert _lines(writer.getvalue()) == []
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_l14_synthesis.py -v`
Expected: FAIL — `ImportError: cannot import name 'synthesize_result_final_if_needed'`

**Step 3: Add the helper**

Append to `src/amplifier_agent_lib/protocol_points/defaults_stdio.py`:

```python
async def synthesize_result_final_if_needed(
    display: StdioDisplaySystem,
    *,
    reply: dict[str, Any],
) -> bool:
    """L14 safety-net synthesis (design Appendix B).

    If the turn/submit reply carries non-empty text AND no `result/final`
    notification was emitted during the turn, synthesize one on the wire
    BEFORE the JSON-RPC response is written.

    Returns True if synthesis happened, False otherwise.

    The synthesized notification carries a `synthesized: true` marker so debug
    tooling can distinguish it from natural emissions.
    """
    if display.result_final_emitted:
        return False
    text = reply.get("reply")
    if not isinstance(text, str) or text == "":
        return False
    turn_id = reply.get("turnId")
    payload: dict[str, Any] = {"text": text, "synthesized": True}
    if turn_id is not None:
        payload["turnId"] = turn_id
    await display.emit("result/final", payload)
    return True
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_l14_synthesis.py -v`
Expected: PASS (4 passed)

**Step 5: Commit**

```bash
git add src/amplifier_agent_lib/protocol_points/defaults_stdio.py tests/test_l14_synthesis.py
git commit -m "feat(l14): wire-level safety-net synthesis for result/final (Appendix B)"
```

---

### Task 6: Stdio loop — capability handshake (initialize first)

**Files:**
- Create: `src/amplifier_agent_cli/modes/stdio_loop.py`
- Create: `tests/cli/__init__.py` (empty marker; create only if missing)
- Create: `tests/cli/test_stdio_loop_handshake.py`

**Background.** Per §6 + Appendix A, the first message in Mode B MUST be `agent/initialize`. If any other request arrives first, the engine returns a JSON-RPC error with `data.code = "agent_not_ready"`. The handshake response carries `protocolVersion`, `serverInfo`, and negotiated `capabilities`. This task implements only the handshake — the broader dispatch loop comes in Task 7.

This task tests with an in-process driver (no subprocess yet) — the subprocess integration test lives in Task 9.

**Step 1: Write the failing test**

Create `tests/cli/__init__.py` as an empty file if it does not exist.

Create `tests/cli/test_stdio_loop_handshake.py`:

```python
"""Tests for the Mode B stdio loop — capability negotiation handshake."""

from __future__ import annotations

import asyncio
import io
import json

import pytest

from amplifier_agent_cli.modes import stdio_loop


class _PipeReader:
    """Async StreamReader-like with feed() to push lines."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()

    def feed(self, line: bytes) -> None:
        self._queue.put_nowait(line)

    def feed_eof(self) -> None:
        self._queue.put_nowait(b"")

    async def readline(self) -> bytes:
        return await self._queue.get()


class _PipeWriter:
    def __init__(self) -> None:
        self._buf = io.BytesIO()

    def write(self, data: bytes) -> None:
        self._buf.write(data)

    async def drain(self) -> None:
        return None

    def getvalue(self) -> bytes:
        return self._buf.getvalue()


class _FakeEngine:
    """Minimal stand-in for amplifier_agent_lib.engine.Engine for handshake tests."""

    def __init__(self) -> None:
        self.advertised_capabilities: dict | None = None

    def attach_display(self, display) -> None:  # accept stdio_loop's wiring call
        self.display = display

    def attach_approval(self, approval) -> None:
        self.approval = approval

    async def initialize(self, *, client_capabilities, client_info):
        self.advertised_capabilities = client_capabilities
        return {
            "protocolVersion": "1.0.0",
            "serverInfo": {
                "name": "amplifier-agent",
                "version": "0.1.0",
                "bundle": "builtin",
            },
            "capabilities": {
                "approval": {"actions": ["accept", "decline", "cancel"]},
                "display": {
                    "events": [
                        "result/delta",
                        "result/final",
                        "tool/started",
                        "tool/completed",
                        "usage",
                        "error",
                    ]
                },
            },
        }

    async def dispatch(self, request):
        raise AssertionError("dispatch should not be called during handshake-only test")


def _read_lines(buf: bytes) -> list[dict]:
    return [json.loads(line) for line in buf.decode("utf-8").splitlines() if line.strip()]


@pytest.mark.asyncio
async def test_handshake_returns_capabilities_on_first_initialize():
    reader = _PipeReader()
    writer = _PipeWriter()
    engine = _FakeEngine()

    init_req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "agent/initialize",
        "params": {
            "capabilities": {
                "approval": {"actions": ["accept", "decline", "cancel"]},
                "display": {"events": ["result/delta", "result/final"]},
            },
            "clientInfo": {"name": "test-client", "version": "0.0.1"},
        },
    }
    reader.feed(json.dumps(init_req).encode("utf-8") + b"\n")
    reader.feed_eof()

    await stdio_loop.run(reader=reader, writer=writer, engine=engine, idle_timeout_s=5.0)

    responses = _read_lines(writer.getvalue())
    init_resp = next(r for r in responses if r.get("id") == 1)
    assert "result" in init_resp
    assert init_resp["result"]["protocolVersion"] == "1.0.0"
    assert init_resp["result"]["serverInfo"]["name"] == "amplifier-agent"
    assert engine.advertised_capabilities == init_req["params"]["capabilities"]


@pytest.mark.asyncio
async def test_handshake_rejects_non_initialize_first_request():
    reader = _PipeReader()
    writer = _PipeWriter()
    engine = _FakeEngine()

    bad_first = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "turn/submit",
        "params": {"prompt": "hi"},
    }
    reader.feed(json.dumps(bad_first).encode("utf-8") + b"\n")
    reader.feed_eof()

    await stdio_loop.run(reader=reader, writer=writer, engine=engine, idle_timeout_s=5.0)

    responses = _read_lines(writer.getvalue())
    err_resp = next(r for r in responses if r.get("id") == 1)
    assert "error" in err_resp
    assert err_resp["error"]["data"]["code"] == "agent_not_ready"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cli/test_stdio_loop_handshake.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'amplifier_agent_cli.modes.stdio_loop'`

**Step 3: Write minimal handshake implementation**

Create `src/amplifier_agent_cli/modes/stdio_loop.py`:

```python
"""Mode B entry — multi-turn JSON-RPC over stdio.

Drives the engine through an async read/dispatch loop:
  - reads JSON-RPC messages from a reader (typically asyncio stdin)
  - first message MUST be `agent/initialize` (returns agent_not_ready otherwise)
  - subsequent requests dispatch to engine
  - server-initiated approval responses route to StdioApprovalSystem
  - exits on EOF, `agent/shutdown`, idle timeout, or fatal error

The library is mode-agnostic — this module is the *only* file that knows about
stdin/stdout. The engine sees only injected protocol points.
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

from amplifier_agent_lib import jsonrpc
from amplifier_agent_lib.protocol_points.defaults_stdio import (
    StdioApprovalSystem,
    StdioDisplaySystem,
)


class _Reader(Protocol):
    async def readline(self) -> bytes: ...


class _Writer(Protocol):
    def write(self, data: bytes) -> None: ...
    async def drain(self) -> None: ...


class _EngineProtocol(Protocol):
    async def initialize(
        self,
        *,
        client_capabilities: dict[str, Any],
        client_info: dict[str, Any],
    ) -> dict[str, Any]: ...
    async def dispatch(self, request: dict[str, Any]) -> dict[str, Any]: ...
    def attach_display(self, display: StdioDisplaySystem) -> None: ...
    def attach_approval(self, approval: StdioApprovalSystem) -> None: ...


async def run(
    *,
    reader: _Reader,
    writer: _Writer,
    engine: _EngineProtocol,
    idle_timeout_s: float = 300.0,
) -> int:
    """Run the Mode B stdio loop. Returns exit code (0 on clean exit)."""
    display = StdioDisplaySystem(writer)
    approval = StdioApprovalSystem(writer, display=display, id_seed=1_000_000)
    engine.attach_display(display)
    engine.attach_approval(approval)

    initialized = False

    while True:
        try:
            message = await jsonrpc.read_message(reader)
        except asyncio.CancelledError:
            return 0

        if message is None:
            return 0

        kind = jsonrpc.classify(message)

        if kind != "request":
            # Notifications, responses, invalid frames are ignored in Task 6.
            # Task 7 wires response routing to the approval system.
            continue

        method = message.get("method")
        req_id = message.get("id")

        if not initialized:
            if method != "agent/initialize":
                err = jsonrpc.make_error(
                    id=req_id,
                    code=-32002,
                    message="Engine not initialized",
                    data={"code": "agent_not_ready"},
                )
                await jsonrpc.write_message(writer, err)
                continue
            params = message.get("params", {}) or {}
            try:
                result = await engine.initialize(
                    client_capabilities=params.get("capabilities", {}),
                    client_info=params.get("clientInfo", {}),
                )
            except Exception as e:
                err = jsonrpc.make_error(
                    id=req_id,
                    code=-32603,
                    message=str(e),
                    data={"code": "provider_init_failed"},
                )
                await jsonrpc.write_message(writer, err)
                continue
            await jsonrpc.write_message(
                writer, jsonrpc.make_response(id=req_id, result=result)
            )
            initialized = True
            continue

        # Task 6 stub: other methods rejected with wire_protocol_violation.
        # Task 7 implements dispatch.
        err = jsonrpc.make_error(
            id=req_id,
            code=-32601,
            message=f"Method not yet implemented: {method}",
            data={"code": "wire_protocol_violation"},
        )
        await jsonrpc.write_message(writer, err)
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/cli/test_stdio_loop_handshake.py -v`
Expected: PASS (2 passed)

**Step 5: Commit**

```bash
git add src/amplifier_agent_cli/modes/stdio_loop.py tests/cli/__init__.py tests/cli/test_stdio_loop_handshake.py
git commit -m "feat(mode-b): stdio_loop handshake — initialize-first capability negotiation"
```

---

### Task 7: Stdio loop — dispatch, shutdown, approval response routing, L14 wiring

**Files:**
- Modify: `src/amplifier_agent_cli/modes/stdio_loop.py`
- Create: `tests/cli/test_stdio_loop_dispatch.py`

**Step 1: Write the failing test**

Create `tests/cli/test_stdio_loop_dispatch.py`:

```python
"""Tests for the Mode B stdio loop — dispatch, shutdown, approval routing, L14 wiring."""

from __future__ import annotations

import asyncio
import io
import json

import pytest

from amplifier_agent_cli.modes import stdio_loop


class _PipeReader:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()

    def feed(self, line: bytes) -> None:
        self._queue.put_nowait(line)

    def feed_eof(self) -> None:
        self._queue.put_nowait(b"")

    async def readline(self) -> bytes:
        return await self._queue.get()


class _PipeWriter:
    def __init__(self) -> None:
        self._buf = io.BytesIO()

    def write(self, data: bytes) -> None:
        self._buf.write(data)

    async def drain(self) -> None:
        return None

    def getvalue(self) -> bytes:
        return self._buf.getvalue()


class _ScriptedEngine:
    """Engine that scripts dispatch behavior including emitting notifications.

    The stdio_loop attaches display and approval systems via the attach_* hooks
    (matching Phase 1's engine.attach_display / engine.attach_approval API).
    """

    def __init__(self, on_turn_submit) -> None:
        self._on_turn_submit = on_turn_submit
        self.display = None
        self.approval = None
        self.shutdown_called = False

    def attach_display(self, display) -> None:
        self.display = display

    def attach_approval(self, approval) -> None:
        self.approval = approval

    async def initialize(self, *, client_capabilities, client_info):
        return {
            "protocolVersion": "1.0.0",
            "serverInfo": {
                "name": "amplifier-agent",
                "version": "0.1.0",
                "bundle": "builtin",
            },
            "capabilities": {
                "approval": {"actions": ["accept", "decline", "cancel"]},
                "display": {"events": ["result/delta", "result/final"]},
            },
        }

    async def dispatch(self, request):
        method = request.get("method")
        if method == "turn/submit":
            return await self._on_turn_submit(request, self.display)
        if method == "agent/shutdown":
            self.shutdown_called = True
            return {}
        raise ValueError(f"unexpected method: {method}")


def _read_lines(buf: bytes) -> list[dict]:
    return [json.loads(line) for line in buf.decode("utf-8").splitlines() if line.strip()]


def _initialize_msg(id_: int = 1) -> bytes:
    return (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": id_,
                "method": "agent/initialize",
                "params": {
                    "capabilities": {},
                    "clientInfo": {"name": "t", "version": "0"},
                },
            }
        ).encode("utf-8")
        + b"\n"
    )


@pytest.mark.asyncio
async def test_turn_submit_with_engine_emitting_final_does_not_synthesize():
    """Engine emits result/final naturally — no synthesis."""
    reader = _PipeReader()
    writer = _PipeWriter()

    async def on_turn(request, display):
        await display.emit("result/final", {"text": "hi back", "turnId": "t1"})
        return {"reply": "hi back", "turnId": "t1"}

    engine = _ScriptedEngine(on_turn)

    reader.feed(_initialize_msg(1))
    reader.feed(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "turn/submit",
                "params": {"prompt": "hi"},
            }
        ).encode("utf-8")
        + b"\n"
    )
    reader.feed_eof()

    await stdio_loop.run(
        reader=reader, writer=writer, engine=engine, idle_timeout_s=5.0
    )

    msgs = _read_lines(writer.getvalue())
    finals = [m for m in msgs if m.get("method") == "result/final"]
    assert len(finals) == 1
    # The single final is the natural one — no synthesized marker.
    assert finals[0]["params"].get("synthesized") is not True


@pytest.mark.asyncio
async def test_turn_submit_with_engine_omitting_final_triggers_synthesis():
    """Engine did NOT emit result/final but reply has text — L14 synthesis fires."""
    reader = _PipeReader()
    writer = _PipeWriter()

    async def on_turn(request, display):
        # Engine forgets to emit result/final.
        return {"reply": "the synthesized answer", "turnId": "t1"}

    engine = _ScriptedEngine(on_turn)

    reader.feed(_initialize_msg(1))
    reader.feed(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "turn/submit",
                "params": {"prompt": "hi"},
            }
        ).encode("utf-8")
        + b"\n"
    )
    reader.feed_eof()

    await stdio_loop.run(
        reader=reader, writer=writer, engine=engine, idle_timeout_s=5.0
    )

    msgs = _read_lines(writer.getvalue())
    finals = [m for m in msgs if m.get("method") == "result/final"]
    assert len(finals) == 1
    assert finals[0]["params"]["synthesized"] is True
    assert finals[0]["params"]["text"] == "the synthesized answer"

    # And the synthesized notification appears BEFORE the response on the wire.
    final_idx = msgs.index(finals[0])
    resp_idx = next(
        i for i, m in enumerate(msgs) if m.get("id") == 2 and "result" in m
    )
    assert final_idx < resp_idx


@pytest.mark.asyncio
async def test_agent_shutdown_responds_and_exits():
    reader = _PipeReader()
    writer = _PipeWriter()

    async def on_turn(request, display):
        return {"reply": "", "turnId": "t1"}

    engine = _ScriptedEngine(on_turn)

    reader.feed(_initialize_msg(1))
    reader.feed(
        json.dumps(
            {"jsonrpc": "2.0", "id": 2, "method": "agent/shutdown", "params": {}}
        ).encode("utf-8")
        + b"\n"
    )
    # Note: no EOF — shutdown must cause exit on its own.

    exit_code = await stdio_loop.run(
        reader=reader, writer=writer, engine=engine, idle_timeout_s=5.0
    )
    assert exit_code == 0
    assert engine.shutdown_called is True

    msgs = _read_lines(writer.getvalue())
    shutdown_resp = next(m for m in msgs if m.get("id") == 2)
    assert "result" in shutdown_resp


@pytest.mark.asyncio
async def test_approval_response_routes_to_approval_system():
    """Server-initiated approval/request → client response routes back to engine."""
    reader = _PipeReader()
    writer = _PipeWriter()
    captured: dict = {}

    async def on_turn(request, display):
        # Engine asks for approval. stdio_loop attached `engine.approval`.
        result = await engine.approval.request(
            kind="tool_execution",
            payload={"tool": "shell"},
            timeout_ms=5000,
        )
        captured["received"] = result
        await display.emit("result/final", {"text": "done", "turnId": "t1"})
        return {"reply": "done", "turnId": "t1"}

    engine = _ScriptedEngine(on_turn)

    reader.feed(_initialize_msg(1))
    submit = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "turn/submit",
        "params": {"prompt": "hi"},
    }
    reader.feed(json.dumps(submit).encode("utf-8") + b"\n")

    async def reply_to_approval():
        """Watch writer buffer for outgoing approval/request, send back a response."""
        for _ in range(200):
            await asyncio.sleep(0.01)
            msgs = _read_lines(writer.getvalue())
            ap_req = next(
                (m for m in msgs if m.get("method") == "approval/request"), None
            )
            if ap_req:
                resp = {
                    "jsonrpc": "2.0",
                    "id": ap_req["id"],
                    "result": {"action": "accept", "payload": {}},
                }
                reader.feed(json.dumps(resp).encode("utf-8") + b"\n")
                return
        raise AssertionError("approval/request never emitted")

    async def feed_eof_after_submit_resolves():
        for _ in range(400):
            await asyncio.sleep(0.01)
            msgs = _read_lines(writer.getvalue())
            if any(m.get("id") == 2 and "result" in m for m in msgs):
                reader.feed_eof()
                return
        raise AssertionError("submit never responded")

    loop_task = asyncio.create_task(
        stdio_loop.run(
            reader=reader, writer=writer, engine=engine, idle_timeout_s=10.0
        )
    )
    replier = asyncio.create_task(reply_to_approval())
    eof_feeder = asyncio.create_task(feed_eof_after_submit_resolves())

    await asyncio.wait_for(loop_task, timeout=10.0)
    await replier
    await eof_feeder

    assert captured["received"]["action"] == "accept"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cli/test_stdio_loop_dispatch.py -v`
Expected: FAIL — first new test fails because Task 6's stub returns `wire_protocol_violation` for `turn/submit`.

**Step 3: Extend the stdio_loop**

Replace `src/amplifier_agent_cli/modes/stdio_loop.py` entirely:

```python
"""Mode B entry — multi-turn JSON-RPC over stdio.

Drives the engine through an async read/dispatch loop:
  - reads JSON-RPC messages from a reader (typically asyncio stdin)
  - first message MUST be `agent/initialize` (returns agent_not_ready otherwise)
  - subsequent requests dispatch to engine
  - server-initiated approval responses route to StdioApprovalSystem
  - L14 safety-net synthesis: every non-empty turn/submit reply is preceded by
    a `result/final` notification on the wire (Appendix B)
  - exits on EOF, `agent/shutdown`, idle timeout, or fatal error
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

from amplifier_agent_lib import jsonrpc
from amplifier_agent_lib.protocol_points.defaults_stdio import (
    StdioApprovalSystem,
    StdioDisplaySystem,
    synthesize_result_final_if_needed,
)


class _Reader(Protocol):
    async def readline(self) -> bytes: ...


class _Writer(Protocol):
    def write(self, data: bytes) -> None: ...
    async def drain(self) -> None: ...


class _EngineProtocol(Protocol):
    async def initialize(
        self,
        *,
        client_capabilities: dict[str, Any],
        client_info: dict[str, Any],
    ) -> dict[str, Any]: ...
    async def dispatch(self, request: dict[str, Any]) -> dict[str, Any]: ...
    def attach_display(self, display: StdioDisplaySystem) -> None: ...
    def attach_approval(self, approval: StdioApprovalSystem) -> None: ...


async def run(
    *,
    reader: _Reader,
    writer: _Writer,
    engine: _EngineProtocol,
    idle_timeout_s: float = 300.0,
) -> int:
    """Run the Mode B stdio loop. Returns exit code (0 on clean exit)."""
    display = StdioDisplaySystem(writer)
    approval = StdioApprovalSystem(writer, display=display, id_seed=1_000_000)
    engine.attach_display(display)
    engine.attach_approval(approval)

    initialized = False

    while True:
        try:
            message = await asyncio.wait_for(
                jsonrpc.read_message(reader), timeout=idle_timeout_s
            )
        except asyncio.TimeoutError:
            # Idle timeout — emit notification + clean exit.
            timeout_notif = jsonrpc.make_notification(
                method="error",
                params={
                    "code": "idle_timeout",
                    "message": f"Idle for {idle_timeout_s}s",
                    "recoverable": False,
                },
            )
            await jsonrpc.write_message(writer, timeout_notif)
            return 0
        except asyncio.CancelledError:
            return 0

        if message is None:
            # EOF — graceful exit.
            return 0

        kind = jsonrpc.classify(message)

        if kind == "response":
            # Route to approval system (only server-initiated requests today).
            await approval.handle_response(message)
            continue

        if kind == "notification" or kind == "invalid":
            # Engine accepts no notifications today; silently ignore.
            continue

        # kind == "request"
        method = message.get("method")
        req_id = message.get("id")

        if not initialized:
            if method != "agent/initialize":
                err = jsonrpc.make_error(
                    id=req_id,
                    code=-32002,
                    message="Engine not initialized",
                    data={"code": "agent_not_ready"},
                )
                await jsonrpc.write_message(writer, err)
                continue
            params = message.get("params", {}) or {}
            try:
                result = await engine.initialize(
                    client_capabilities=params.get("capabilities", {}),
                    client_info=params.get("clientInfo", {}),
                )
            except Exception as e:
                err = jsonrpc.make_error(
                    id=req_id,
                    code=-32603,
                    message=str(e),
                    data={"code": "provider_init_failed"},
                )
                await jsonrpc.write_message(writer, err)
                continue
            await jsonrpc.write_message(
                writer, jsonrpc.make_response(id=req_id, result=result)
            )
            initialized = True
            continue

        if method == "agent/shutdown":
            try:
                result = await engine.dispatch(message)
            except Exception as e:
                err = jsonrpc.make_error(
                    id=req_id,
                    code=-32603,
                    message=str(e),
                    data={"code": "wire_protocol_violation"},
                )
                await jsonrpc.write_message(writer, err)
                return 1
            await jsonrpc.write_message(
                writer, jsonrpc.make_response(id=req_id, result=result)
            )
            return 0

        if method == "turn/submit":
            display.reset_for_turn()
            try:
                result = await engine.dispatch(message)
            except Exception as e:
                err = jsonrpc.make_error(
                    id=req_id,
                    code=-32603,
                    message=str(e),
                    data={"code": "tool_execution_failed"},
                )
                await jsonrpc.write_message(writer, err)
                continue
            # L14 safety-net synthesis (Appendix B).
            await synthesize_result_final_if_needed(display, reply=result)
            await jsonrpc.write_message(
                writer, jsonrpc.make_response(id=req_id, result=result)
            )
            continue

        # Any other method: pass through to engine.dispatch (e.g. cache/info).
        try:
            result = await engine.dispatch(message)
        except Exception as e:
            err = jsonrpc.make_error(
                id=req_id,
                code=-32601,
                message=str(e),
                data={"code": "wire_protocol_violation"},
            )
            await jsonrpc.write_message(writer, err)
            continue
        await jsonrpc.write_message(
            writer, jsonrpc.make_response(id=req_id, result=result)
        )
```

**Step 4: Run tests to verify all pass**

Run: `uv run pytest tests/cli/ -v`
Expected: PASS — all handshake + dispatch tests green (Task 6's tests must still pass).

**Step 5: Commit**

```bash
git add src/amplifier_agent_cli/modes/stdio_loop.py tests/cli/test_stdio_loop_dispatch.py
git commit -m "feat(mode-b): dispatch loop with shutdown, approval routing, L14 wiring"
```

---

### Task 8: Idle timeout

**Files:**
- Modify: `tests/cli/test_stdio_loop_dispatch.py`

**Background.** Idle timeout is already implemented in Task 7's stdio_loop (`asyncio.wait_for` around `read_message` with `idle_timeout_s`). This task asserts the behavior via a dedicated test.

**Step 1: Append failing test**

Append to `tests/cli/test_stdio_loop_dispatch.py`:

```python
@pytest.mark.asyncio
async def test_idle_timeout_triggers_exit_with_error_notification():
    reader = _PipeReader()
    writer = _PipeWriter()

    async def on_turn(request, display):
        return {"reply": "", "turnId": "t1"}

    engine = _ScriptedEngine(on_turn)

    reader.feed(_initialize_msg(1))
    # Do NOT feed any further messages. Idle timeout should fire.

    exit_code = await stdio_loop.run(
        reader=reader, writer=writer, engine=engine, idle_timeout_s=0.1
    )

    assert exit_code == 0
    msgs = _read_lines(writer.getvalue())
    err_notifs = [
        m
        for m in msgs
        if m.get("method") == "error"
        and m.get("params", {}).get("code") == "idle_timeout"
    ]
    assert len(err_notifs) == 1
```

**Step 2: Run test to verify pass**

Run: `uv run pytest tests/cli/test_stdio_loop_dispatch.py::test_idle_timeout_triggers_exit_with_error_notification -v`
Expected: PASS (timeout fires after 0.1s, error notification written, exit 0).

**Step 3: Run the whole cli test suite to confirm no regression**

Run: `uv run pytest tests/cli/ -v`
Expected: ALL PASS.

**Step 4: Commit**

```bash
git add tests/cli/test_stdio_loop_dispatch.py
git commit -m "test(mode-b): idle timeout fires error notification and exits cleanly"
```

---

### Task 9: Wire `__main__.py` + subprocess integration test

**Files:**
- Modify: `src/amplifier_agent_cli/__main__.py`
- Create: `tests/cli/test_stdio_loop_subprocess.py`

**Background.** Replace the Phase 2 `NotImplementedError` stub for `run --stdio` with a call to `stdio_loop.run()` using real `asyncio.StreamReader`/`StreamWriter` over `sys.stdin`/`sys.stdout`. Then write a subprocess integration test that spawns the actual binary via `asyncio.create_subprocess_exec`, performs a full Mode B session, and verifies the wire.

**Step 1: Locate and inspect the Phase 2 stub**

Read `src/amplifier_agent_cli/__main__.py`. Find the Mode B branch — it should look like:

```python
if args.stdio:
    raise NotImplementedError("Mode B not yet implemented")  # Phase 2 stub
```

If the exact wording differs, find the `--stdio` branch by `grep -n "stdio" src/amplifier_agent_cli/__main__.py`.

**Step 2: Write the failing integration test**

Create `tests/cli/test_stdio_loop_subprocess.py`:

```python
"""Integration test: spawn the amplifier-agent binary, run a full Mode B session."""

from __future__ import annotations

import asyncio
import json
import os
import sys

import pytest


REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)


def _write_line(proc: asyncio.subprocess.Process, obj: dict) -> None:
    assert proc.stdin is not None
    proc.stdin.write(json.dumps(obj).encode("utf-8") + b"\n")


async def _read_one(
    proc: asyncio.subprocess.Process, timeout_s: float = 5.0
) -> dict:
    assert proc.stdout is not None
    line = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout_s)
    assert line, "EOF before message"
    return json.loads(line)


async def _read_until(
    proc: asyncio.subprocess.Process,
    predicate,
    timeout_s: float = 5.0,
) -> list[dict]:
    collected: list[dict] = []

    async def _inner():
        while True:
            msg = await _read_one(proc, timeout_s=timeout_s)
            collected.append(msg)
            if predicate(msg):
                return collected

    return await asyncio.wait_for(_inner(), timeout=timeout_s)


@pytest.mark.asyncio
async def test_subprocess_handshake_and_shutdown():
    """Full Mode B lifecycle via real subprocess: initialize → shutdown → exit 0."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "amplifier_agent_cli",
        "run",
        "--stdio",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    try:
        # initialize
        _write_line(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "agent/initialize",
                "params": {
                    "capabilities": {
                        "approval": {"actions": ["accept", "decline", "cancel"]},
                        "display": {"events": ["result/delta", "result/final"]},
                    },
                    "clientInfo": {"name": "integration-test", "version": "0"},
                },
            },
        )
        await proc.stdin.drain()
        init_resp = await _read_one(proc)
        assert init_resp["id"] == 1
        assert "result" in init_resp
        assert init_resp["result"]["serverInfo"]["name"] == "amplifier-agent"

        # shutdown
        _write_line(
            proc, {"jsonrpc": "2.0", "id": 2, "method": "agent/shutdown", "params": {}}
        )
        await proc.stdin.drain()
        msgs = await _read_until(proc, lambda m: m.get("id") == 2)
        shutdown_resp = msgs[-1]
        assert "result" in shutdown_resp

        exit_code = await asyncio.wait_for(proc.wait(), timeout=5.0)
        assert exit_code == 0
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


@pytest.mark.asyncio
async def test_subprocess_rejects_non_initialize_first():
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "amplifier_agent_cli",
        "run",
        "--stdio",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    try:
        _write_line(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "turn/submit",
                "params": {"prompt": "hi"},
            },
        )
        await proc.stdin.drain()

        err_resp = await _read_one(proc)
        assert err_resp["id"] == 1
        assert "error" in err_resp
        assert err_resp["error"]["data"]["code"] == "agent_not_ready"

        # Close stdin → expect clean exit on EOF.
        proc.stdin.close()
        exit_code = await asyncio.wait_for(proc.wait(), timeout=5.0)
        assert exit_code == 0
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


@pytest.mark.asyncio
async def test_subprocess_stdin_close_triggers_graceful_exit():
    """Closing stdin (EOF) before any request → graceful exit 0."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "amplifier_agent_cli",
        "run",
        "--stdio",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    try:
        proc.stdin.close()
        exit_code = await asyncio.wait_for(proc.wait(), timeout=5.0)
        assert exit_code == 0
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
```

**Step 3: Run test to verify it fails**

Run: `uv run pytest tests/cli/test_stdio_loop_subprocess.py -v`
Expected: FAIL — the Phase 2 `NotImplementedError` aborts the subprocess; `_read_one` raises `EOF before message` OR `asyncio.TimeoutError`.

**Step 4: Wire `__main__.py` to `stdio_loop`**

Read the current `src/amplifier_agent_cli/__main__.py`, find the Mode B branch, and replace it with the block below. The exact surrounding indentation/structure depends on Phase 2's argparse layout — preserve it:

```python
        if args.stdio:
            import asyncio
            import sys

            from amplifier_agent_cli.modes import stdio_loop
            from amplifier_agent_lib.engine import Engine

            async def _main_stdio() -> int:
                loop = asyncio.get_running_loop()

                # Connect real asyncio stdin → StreamReader.
                reader = asyncio.StreamReader()
                protocol = asyncio.StreamReaderProtocol(reader)
                await loop.connect_read_pipe(lambda: protocol, sys.stdin)

                # Connect real stdout → StreamWriter via FlowControlMixin.
                w_transport, w_protocol = await loop.connect_write_pipe(
                    asyncio.streams.FlowControlMixin, sys.stdout
                )
                writer = asyncio.StreamWriter(w_transport, w_protocol, None, loop)

                # Boot engine. The exact boot signature is Phase 1's responsibility;
                # adjust the kwargs below to match.
                engine = Engine.boot(
                    bundle=None,  # Phase 1 default (vendored stub)
                    provider_override=getattr(args, "provider", None),
                    cwd=getattr(args, "cwd", None),
                )
                idle_timeout_ms = getattr(args, "idle_timeout", None)
                idle_timeout_s = (idle_timeout_ms / 1000.0) if idle_timeout_ms else 300.0

                return await stdio_loop.run(
                    reader=reader,
                    writer=writer,
                    engine=engine,
                    idle_timeout_s=idle_timeout_s,
                )

            sys.exit(asyncio.run(_main_stdio()))
```

If `Engine.boot()` has a different signature in Phase 1, adjust the call. Use `grep -n "def boot" src/amplifier_agent_lib/engine.py` or `LSP goToDefinition` on `Engine` to confirm. If `--idle-timeout` is not yet in argparse (Phase 2 may have skipped it), add it with `parser_run.add_argument("--idle-timeout", type=int, default=None, help="Mode B only: idle timeout in ms")` near the other `run` flags.

**Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/cli/test_stdio_loop_subprocess.py -v`
Expected: PASS (3 passed).

If `Engine.boot()` cannot complete without a real bundle (Phase 4 dependency) and the subprocess crashes during initialize, gate the dependent tests with a fixture/skipif that detects the bundle stub. The `test_subprocess_rejects_non_initialize_first` and `test_subprocess_stdin_close_triggers_graceful_exit` tests do NOT call `initialize` and should always pass once `__main__.py` is wired.

**Step 6: Run the full Phase 3 suite to ensure nothing regressed**

Run: `uv run pytest tests/test_jsonrpc.py tests/test_defaults_stdio.py tests/test_l14_synthesis.py tests/cli/ -v`
Expected: ALL PASS.

**Step 7: Commit**

```bash
git add src/amplifier_agent_cli/__main__.py tests/cli/test_stdio_loop_subprocess.py
git commit -m "feat(mode-b): wire run --stdio to stdio_loop; subprocess integration tests"
```

---

## Phase 3 completion checklist

Before declaring Phase 3 done, verify:

- [ ] All 9 task commits land on the branch
- [ ] `uv run pytest tests/test_jsonrpc.py tests/test_defaults_stdio.py tests/test_l14_synthesis.py tests/cli/ -v` reports all green
- [ ] No `NotImplementedError` remains in `__main__.py` for `run --stdio` (`grep -n "NotImplementedError" src/amplifier_agent_cli/`)
- [ ] The L14 synthesis tests in BOTH `tests/test_l14_synthesis.py` AND `tests/cli/test_stdio_loop_dispatch.py::test_turn_submit_with_engine_omitting_final_triggers_synthesis` pass
- [ ] `python -m amplifier_agent_cli run --stdio < /dev/null` from a shell exits 0 immediately (EOF graceful exit)
- [ ] No blocking I/O (`time.sleep`, blocking `input()`, threaded readers) anywhere in Mode B production code: `grep -rn "time.sleep\|input(" src/amplifier_agent_cli/modes/ src/amplifier_agent_lib/protocol_points/defaults_stdio.py src/amplifier_agent_lib/jsonrpc.py` returns nothing
- [ ] No write to stdout from anywhere except via `jsonrpc.write_message(writer, ...)`: `grep -rn "sys.stdout\|print(" src/amplifier_agent_cli/modes/stdio_loop.py src/amplifier_agent_lib/jsonrpc.py src/amplifier_agent_lib/protocol_points/defaults_stdio.py` returns nothing (or only well-justified instances)

## What's deferred to later phases

| Item | Reason | Phase |
|---|---|---|
| Real vendored bundle / cold-start measurement | Engine boot stub is enough for Mode B handshake; real bundle is heavy and out of scope here | Phase 4 |
| `--idle-timeout` flag on argparse if Phase 2 didn't add it | Wire-level support exists; argparse plumbing is one line if missing | Phase 2 polish or Phase 4 |
| `cache/info` RPC | Used by `doctor` admin verb only; Mode B passes it through `engine.dispatch` already | Phase 4 |
| TS / Python wrapper packages | Wire is verified by Phase 3 subprocess test; wrappers consume this wire | post-L4 |
| Multi-process spawn (real sub-agent) | Library-internal spawn is stubbed in Phase 1 | Phase 4 |

## Why the wire-level L14 test is mandatory

Appendix B of the design checkpoint elevates the L14 contract from an undocumented V1 workaround to a named cross-language wire contract. The bug it prevents — a final reply scalar with no `result/final` notification — is the single highest-severity carryforward from V1's host-client-ts. Phase 3 server-side must be defensible against engine bugs that omit `result/final`; the safety-net synthesis IS that defense. The conformance test (`test_turn_submit_with_engine_omitting_final_triggers_synthesis`) is the regression gate. **A Phase 3 PR that doesn't include this test is incomplete.**

# Streaming Hook Enrichment — Phase 2: Hook Enrichment Implementation Plan

> **Execution:** Use the subagent-driven-development workflow to implement this plan. **Do not start Phase 2 until Phase 1 is fully green.**

**Goal:** Make `StreamingEmitter` extract the kernel data it currently discards, surface reasoning ("thinking") content, attribute actions to sub-agents, and emit a session-total cost rollup — all using the typed wire slots added in Phase 1.

**Architecture:** `src/amplifier_agent_lib/bundle/hook_streaming.py` defines `StreamingEmitter` (a class of async hook handlers) and a `mount()` function that registers them on the coordinator. Each handler reads a kernel event `data: dict` and emits a slash-separated wire event through the `display.emit` capability. This phase: (1) adds a `_parse_agent_name` helper, (2) enriches `on_llm_response`, (3) attaches `agentName` to tool events, (4) adds `on_thinking_delta` / `on_thinking_final` + extends `CANONICAL_WIRE_EVENTS`, (5) adds `on_orchestrator_complete` with an inline `Decimal` cost aggregator, (6) updates `mount()` to register 10 handlers and fixes the two structural tests.

**Tech Stack:** Python 3.12, pytest + pytest-asyncio (strict mode), `decimal.Decimal`, amplifier-core `HookResult`.

---

## Context the implementer must know

### Kernel `llm:response.data` shape (extract these — all defensively via `.get`)
- `data["duration_ms"]` — top-level `int`, LLM wall-clock time
- `data["model"]` — top-level `str`, e.g. `"claude-opus-4-20250514"`
- `data["provider"]` — top-level `str`, e.g. `"anthropic"`
- `data["usage"]["input_tokens"]` — already read
- `data["usage"]["output_tokens"]` — already read
- `data["usage"]["cache_read_tokens"]` — `int | None`
- `data["usage"]["cache_write_tokens"]` — `int | None`
- `data["usage"]["cost_usd"]` — **`str | None`** (kernel serializes its `Decimal` cost to a string; do not coerce to float)

**Defensive rule:** the kernel schema evolves. Always use `.get(key)` and only attach an optional wire field when the source value is present (not `None`). This respects the schema's `additionalProperties: false` + non-null typed slots — emitting `None` for an `int`/`str` slot would violate the schema.

### Session-id → agentName parsing
Format: `{parent}-{child}_{agent_name}` (e.g. `abc123-def456_explorer`). Root sessions contain **no underscore** → return `None`. Split on the first underscore; everything after it is the agent name.

### Session-total cost rollup
- `coordinator.collect_contributions("session.cost")` returns a `list[dict]` like `[{"cost_usd": "0.0142"}, {"cost_usd": "0.0031"}]`.
- Sum with an **inline** `Decimal` aggregator (~12 lines). Do NOT import `amplifier_foundation.bundle._prepared.sum_cost_usd` — keep the hook free of foundation coupling.
- `orchestrator:complete` fires per turn; this is the trigger.
- The session-total `usage` event still has the **required** `inputTokens` / `outputTokens` fields (schema requires them) — set both to `0` for the rollup event, and attach the total via `sessionCostTotal`.
- `collect_contributions` may not exist on every coordinator (e.g. minimal mocks) — guard with `getattr`.

### Thinking-block filter — decision (documented)
`on_content_block_end` line 174 (`if text and block_type in ("text", ""):`) deliberately drops thinking-type content blocks. **We keep this filter as-is.** Thinking content is routed exclusively through the dedicated `thinking:delta` / `thinking:final` kernel events — the canonical source — giving clean separation between "result text" and "reasoning text." Do not remove or modify the line 174 filter.

### Existing tests that WILL break and must be updated in this phase
- `tests/test_bundle_hook_streaming.py::test_mount_registers_seven_handlers` — becomes 10 handlers (Task 6).
- `tests/test_bundle_hook_streaming.py::test_canonical_wire_events_contains_required_types` — gains `thinking/delta`, `thinking/final` (Task 4).

---

### Task 1: Add the `_parse_agent_name` helper

**Files:**
- Modify: `src/amplifier_agent_lib/bundle/hook_streaming.py`
- Test: `tests/test_bundle_hook_streaming.py`

**Step 1: Write the failing test**

Append to `tests/test_bundle_hook_streaming.py`:

```python
# ---------------------------------------------------------------------------
# Sub-cycle 11F: _parse_agent_name helper
# ---------------------------------------------------------------------------


def test_parse_agent_name_extracts_sub_agent() -> None:
    """A delegated session id ({parent}-{child}_{agent}) yields the agent name."""
    from amplifier_agent_lib.bundle.hook_streaming import _parse_agent_name

    assert _parse_agent_name("abc123-def456_explorer") == "explorer"
    assert _parse_agent_name("0000-1111_superpowers-plan-writer") == "superpowers-plan-writer"


def test_parse_agent_name_returns_none_for_root_session() -> None:
    """A root session id (no underscore) yields None."""
    from amplifier_agent_lib.bundle.hook_streaming import _parse_agent_name

    assert _parse_agent_name("abc123-def456") is None
    assert _parse_agent_name("") is None
```

**Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_bundle_hook_streaming.py::test_parse_agent_name_extracts_sub_agent tests/test_bundle_hook_streaming.py::test_parse_agent_name_returns_none_for_root_session -v`
Expected: FAIL — `ImportError: cannot import name '_parse_agent_name'`.

**Step 3: Write the minimal implementation**

In `src/amplifier_agent_lib/bundle/hook_streaming.py`, add this module-level helper just below the existing `_block_id` function (after line 44):

```python
def _parse_agent_name(session_id: str) -> str | None:
    """Extract the sub-agent name from a delegated session id.

    Session id format: ``{parent}-{child}_{agent_name}`` for delegated
    (sub-agent) sessions.  Root sessions contain no underscore and return
    ``None``.
    """
    if "_" not in session_id:
        return None
    name = session_id.split("_", 1)[1]
    return name or None
```

**Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_bundle_hook_streaming.py -k parse_agent_name -v`
Expected: PASS (both tests).

**Step 5: Commit**

```bash
git add src/amplifier_agent_lib/bundle/hook_streaming.py tests/test_bundle_hook_streaming.py
git commit -m "feat(streaming-hook): add _parse_agent_name session-id helper

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

---

### Task 2: Enrich `on_llm_response` with cost, timing, model, provider, cache tokens, agentName

**Files:**
- Modify: `src/amplifier_agent_lib/bundle/hook_streaming.py:188-230`
- Test: `tests/test_bundle_hook_streaming.py`

**Step 1: Write the failing test**

Append to `tests/test_bundle_hook_streaming.py`:

```python
@pytest.mark.asyncio
async def test_llm_response_usage_includes_enrichment_fields() -> None:
    """on_llm_response attaches duration, model, provider, cache tokens, and cost to usage."""
    coord = _MockCoordinator()
    emitter = StreamingEmitter(coord)

    data = {
        "session_id": "root-1_explorer",
        "turn_id": "turn-1",
        "text": "",
        "duration_ms": 3200,
        "model": "claude-opus-4-20250514",
        "provider": "anthropic",
        "usage": {
            "input_tokens": 1247,
            "output_tokens": 892,
            "cache_read_tokens": 600,
            "cache_write_tokens": 47,
            "cost_usd": "0.0142",
        },
    }
    await emitter.on_llm_response("llm:response", data)

    usage_ev = next(ev for ev in coord.emitted if ev["type"] == "usage")
    assert usage_ev["inputTokens"] == 1247
    assert usage_ev["outputTokens"] == 892
    assert usage_ev["llmDurationMs"] == 3200
    assert usage_ev["model"] == "claude-opus-4-20250514"
    assert usage_ev["provider"] == "anthropic"
    assert usage_ev["cacheReadTokens"] == 600
    assert usage_ev["cacheWriteTokens"] == 47
    assert usage_ev["cost"] == "0.0142"  # string, not float
    assert isinstance(usage_ev["cost"], str)
    assert usage_ev["agentName"] == "explorer"


@pytest.mark.asyncio
async def test_llm_response_omits_absent_enrichment_fields() -> None:
    """Enrichment fields absent from kernel data are NOT attached (no None values)."""
    coord = _MockCoordinator()
    emitter = StreamingEmitter(coord)

    data = {
        "session_id": "root-1",  # root session: no agentName
        "turn_id": "turn-1",
        "input_tokens": 10,
        "output_tokens": 5,
    }
    await emitter.on_llm_response("llm:response", data)

    usage_ev = next(ev for ev in coord.emitted if ev["type"] == "usage")
    for absent in ("llmDurationMs", "model", "provider", "cacheReadTokens", "cacheWriteTokens", "cost", "agentName"):
        assert absent not in usage_ev, f"{absent} should be omitted when source is absent"
```

**Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_bundle_hook_streaming.py -k "enrichment_fields or omits_absent" -v`
Expected: FAIL — enrichment fields not yet attached (`KeyError`/assertion failure).

**Step 3: Write the minimal implementation**

In `src/amplifier_agent_lib/bundle/hook_streaming.py`, replace the body of `on_llm_response` (currently lines 196–230, from `session_id: str = ...` through `return HookResult(action="continue")`) with:

```python
        session_id: str = data.get("session_id", "")
        turn_id: str = data.get("turn_id", "")

        # Token counts: check both top-level (legacy) and nested usage dict (current).
        usage_dict: dict[str, Any] = data.get("usage", {}) or {}
        in_tok: int = int(data.get("input_tokens", 0) or usage_dict.get("input_tokens", 0) or 0)
        out_tok: int = int(data.get("output_tokens", 0) or usage_dict.get("output_tokens", 0) or 0)

        # Text: present in legacy kernels only; empty string in current kernels
        # (text was already delivered via content_block:end events).
        text: str = data.get("text", "") or ""

        if in_tok or out_tok:
            usage_ev: dict[str, Any] = {
                "type": "usage",
                "sessionId": session_id,
                "turnId": turn_id,
                "inputTokens": in_tok,
                "outputTokens": out_tok,
            }
            # Enrichment — attach each field only when the kernel supplied it, to
            # respect the schema's additionalProperties:false + non-null typed slots.
            duration_ms = data.get("duration_ms")
            if duration_ms is not None:
                usage_ev["llmDurationMs"] = int(duration_ms)
            model = data.get("model")
            if model:
                usage_ev["model"] = str(model)
            provider = data.get("provider")
            if provider:
                usage_ev["provider"] = str(provider)
            cache_read = usage_dict.get("cache_read_tokens")
            if cache_read is not None:
                usage_ev["cacheReadTokens"] = int(cache_read)
            cache_write = usage_dict.get("cache_write_tokens")
            if cache_write is not None:
                usage_ev["cacheWriteTokens"] = int(cache_write)
            cost = usage_dict.get("cost_usd")
            if cost is not None:
                # Kernel serializes Decimal cost to a string; keep it a string to
                # preserve monetary precision on the wire.
                usage_ev["cost"] = str(cost)
            agent_name = _parse_agent_name(session_id)
            if agent_name is not None:
                usage_ev["agentName"] = agent_name
            await self._emit(usage_ev)

        # Always emit result/final as the turn-completion signal.
        await self._emit(
            {
                "type": "result/final",
                "sessionId": session_id,
                "turnId": turn_id,
                "text": text,
            }
        )
        return HookResult(action="continue")
```

**Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_bundle_hook_streaming.py -k "llm_response" -v`
Expected: PASS — both new tests AND the three pre-existing `llm_response` tests stay green (the enrichment is additive; absent fields are omitted).

**Step 5: Commit**

```bash
git add src/amplifier_agent_lib/bundle/hook_streaming.py tests/test_bundle_hook_streaming.py
git commit -m "feat(streaming-hook): enrich llm:response usage with cost/timing/model/cache

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

---

### Task 3: Attach `agentName` to `tool/started` and `tool/completed`

**Files:**
- Modify: `src/amplifier_agent_lib/bundle/hook_streaming.py:80-110`
- Test: `tests/test_bundle_hook_streaming.py`

**Step 1: Write the failing test**

Append to `tests/test_bundle_hook_streaming.py`:

```python
@pytest.mark.asyncio
async def test_tool_events_include_agent_name_for_sub_agent() -> None:
    """tool/started and tool/completed carry agentName for delegated sessions."""
    coord = _MockCoordinator()
    emitter = StreamingEmitter(coord)

    pre_data = {
        "session_id": "root-1_coder",
        "turn_id": "t",
        "tool_call_id": "c1",
        "tool": "bash",
        "arguments": {"cmd": "ls"},
    }
    await emitter.on_tool_pre("tool:pre", pre_data)
    post_data = {
        "session_id": "root-1_coder",
        "turn_id": "t",
        "tool_call_id": "c1",
        "tool": "bash",
        "result": {"stdout": "x"},
        "duration_ms": 5,
    }
    await emitter.on_tool_post("tool:post", post_data)

    started = next(ev for ev in coord.emitted if ev["type"] == "tool/started")
    completed = next(ev for ev in coord.emitted if ev["type"] == "tool/completed")
    assert started["agentName"] == "coder"
    assert completed["agentName"] == "coder"


@pytest.mark.asyncio
async def test_tool_events_omit_agent_name_for_root_session() -> None:
    """Root sessions (no underscore) produce no agentName key on tool events."""
    coord = _MockCoordinator()
    emitter = StreamingEmitter(coord)

    await emitter.on_tool_pre(
        "tool:pre",
        {"session_id": "root-1", "turn_id": "t", "tool_call_id": "c1", "tool": "bash", "arguments": {}},
    )
    started = next(ev for ev in coord.emitted if ev["type"] == "tool/started")
    assert "agentName" not in started
```

**Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_bundle_hook_streaming.py -k "agent_name_for_sub_agent or omit_agent_name_for_root" -v`
Expected: FAIL — `agentName` not present on tool events.

**Step 3: Write the minimal implementation**

In `on_tool_pre`, after building the emit dict and before `await self._emit(...)`, change the method to attach `agentName` conditionally. Replace the body of `on_tool_pre` (lines 81–94) with:

```python
        tool_name: str = data.get("tool") or data.get("tool_name") or ""
        tool_args: dict = data.get("arguments") or data.get("tool_input") or {}
        session_id: str = data.get("session_id", "")
        event: dict[str, Any] = {
            "type": "tool/started",
            "sessionId": session_id,
            "turnId": data.get("turn_id", ""),
            "toolCallId": data.get("tool_call_id", ""),
            "name": tool_name,
            "args": tool_args,
        }
        agent_name = _parse_agent_name(session_id)
        if agent_name is not None:
            event["agentName"] = agent_name
        await self._emit(event)
        return HookResult(action="continue")
```

Replace the body of `on_tool_post` (lines 98–110) with:

```python
        tool_name: str = data.get("tool") or data.get("tool_name") or ""
        session_id: str = data.get("session_id", "")
        event: dict[str, Any] = {
            "type": "tool/completed",
            "sessionId": session_id,
            "turnId": data.get("turn_id", ""),
            "toolCallId": data.get("tool_call_id", ""),
            "name": tool_name,
            "result": data.get("result"),
            "durationMs": int(data.get("duration_ms", 0)),
        }
        agent_name = _parse_agent_name(session_id)
        if agent_name is not None:
            event["agentName"] = agent_name
        await self._emit(event)
        return HookResult(action="continue")
```

**Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_bundle_hook_streaming.py -k "tool_pre or tool_post or agent_name" -v`
Expected: PASS — new tests pass and the pre-existing `tool_pre` / `tool_post` tests stay green.

**Step 5: Commit**

```bash
git add src/amplifier_agent_lib/bundle/hook_streaming.py tests/test_bundle_hook_streaming.py
git commit -m "feat(streaming-hook): attach agentName to tool/started and tool/completed

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

---

### Task 4: Add thinking handlers and extend `CANONICAL_WIRE_EVENTS`

**Files:**
- Modify: `src/amplifier_agent_lib/bundle/hook_streaming.py:29-35` (tuple) and add two handlers
- Test: `tests/test_bundle_hook_streaming.py` (new tests + update `test_canonical_wire_events_contains_required_types`)

**Step 1: Write the failing tests**

First, update the existing `test_canonical_wire_events_contains_required_types` (lines 58–61) to include the thinking types:

```python
def test_canonical_wire_events_contains_required_types() -> None:
    """CANONICAL_WIRE_EVENTS must contain the required wire event types incl. thinking."""
    required = {
        "result/delta",
        "result/final",
        "tool/started",
        "tool/completed",
        "thinking/delta",
        "thinking/final",
        "usage",
    }
    assert required == set(CANONICAL_WIRE_EVENTS)
```

Then append new emission tests:

```python
# ---------------------------------------------------------------------------
# Sub-cycle 11G: thinking:delta / thinking:final -> thinking/delta / thinking/final
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_thinking_delta_emits_thinking_delta() -> None:
    """on_thinking_delta emits type='thinking/delta' with the reasoning text."""
    coord = _MockCoordinator()
    emitter = StreamingEmitter(coord)

    await emitter.on_thinking_delta(
        "thinking:delta",
        {"session_id": "s", "turn_id": "t", "text": "let me reason"},
    )

    assert len(coord.emitted) == 1
    ev = coord.emitted[0]
    assert ev["type"] == "thinking/delta"
    assert ev["sessionId"] == "s"
    assert ev["turnId"] == "t"
    assert ev["text"] == "let me reason"


@pytest.mark.asyncio
async def test_thinking_final_emits_thinking_final() -> None:
    """on_thinking_final emits type='thinking/final' with the full reasoning text."""
    coord = _MockCoordinator()
    emitter = StreamingEmitter(coord)

    await emitter.on_thinking_final(
        "thinking:final",
        {"session_id": "s", "turn_id": "t", "text": "final reasoning"},
    )

    assert len(coord.emitted) == 1
    ev = coord.emitted[0]
    assert ev["type"] == "thinking/final"
    assert ev["text"] == "final reasoning"


@pytest.mark.asyncio
async def test_thinking_final_reads_block_text_fallback() -> None:
    """on_thinking_final falls back to data['block']['text'] when top-level text is absent."""
    coord = _MockCoordinator()
    emitter = StreamingEmitter(coord)

    await emitter.on_thinking_final(
        "thinking:final",
        {"session_id": "s", "turn_id": "t", "block": {"type": "thinking", "text": "block reasoning"}},
    )

    ev = coord.emitted[0]
    assert ev["text"] == "block reasoning"
```

**Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_bundle_hook_streaming.py -k "thinking or canonical_wire_events" -v`
Expected: FAIL — `CANONICAL_WIRE_EVENTS` lacks thinking types and the handlers don't exist.

**Step 3: Write the minimal implementation**

(3a) Extend the `CANONICAL_WIRE_EVENTS` tuple (lines 29–35) to:

```python
CANONICAL_WIRE_EVENTS: tuple[str, ...] = (
    "result/delta",
    "result/final",
    "tool/started",
    "tool/completed",
    "thinking/delta",
    "thinking/final",
    "usage",
)
```

(3b) Add two handlers to `StreamingEmitter`, immediately after `on_content_block_end` (after line 186) and before `on_llm_response`:

```python
    async def on_thinking_delta(self, event: str, data: dict[str, Any]) -> HookResult:
        """Kernel ``thinking:delta`` → wire ``thinking/delta``."""
        await self._emit(
            {
                "type": "thinking/delta",
                "sessionId": data.get("session_id", ""),
                "turnId": data.get("turn_id", ""),
                "text": data.get("text", "") or "",
            }
        )
        return HookResult(action="continue")

    async def on_thinking_final(self, event: str, data: dict[str, Any]) -> HookResult:
        """Kernel ``thinking:final`` → wire ``thinking/final``.

        Reads ``data['text']`` when present; otherwise falls back to
        ``data['block']['text']`` (the current kernel delivers completed blocks
        in a ``block`` sub-dict, mirroring ``content_block:end``).
        """
        text: str = data.get("text", "") or ""
        if not text:
            block = data.get("block", {})
            if isinstance(block, dict):
                text = block.get("text", "") or ""
        await self._emit(
            {
                "type": "thinking/final",
                "sessionId": data.get("session_id", ""),
                "turnId": data.get("turn_id", ""),
                "text": text,
            }
        )
        return HookResult(action="continue")
```

**Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_bundle_hook_streaming.py -k "thinking or canonical_wire_events" -v`
Expected: PASS (all four).

**Step 5: Commit**

```bash
git add src/amplifier_agent_lib/bundle/hook_streaming.py tests/test_bundle_hook_streaming.py
git commit -m "feat(streaming-hook): emit thinking/delta and thinking/final wire events

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

---

### Task 5: Add `on_orchestrator_complete` session-total cost rollup

**Files:**
- Modify: `src/amplifier_agent_lib/bundle/hook_streaming.py`
- Test: `tests/test_bundle_hook_streaming.py` (and extend `_MockCoordinator`)

**Step 1: Write the failing test**

First, extend the shared `_MockCoordinator` (lines 36–50) to support contributions. Replace it with:

```python
class _MockCoordinator:
    """Mock coordinator that captures emitted display events."""

    def __init__(self) -> None:
        self.emitted: list[dict] = []
        self.hooks = _MockHooks()
        self.contributions: list[dict] = []

    def get_capability(self, name: str) -> object:
        if name == "display.emit":

            async def _emit(event: dict) -> None:
                self.emitted.append(event)

            return _emit
        raise KeyError(f"Unknown capability: {name!r}")

    def collect_contributions(self, channel: str) -> list[dict]:
        if channel == "session.cost":
            return self.contributions
        return []
```

Then append the rollup tests:

```python
# ---------------------------------------------------------------------------
# Sub-cycle 11H: orchestrator:complete -> session-total usage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orchestrator_complete_emits_session_cost_total() -> None:
    """on_orchestrator_complete sums session.cost contributions into a usage event."""
    coord = _MockCoordinator()
    coord.contributions = [{"cost_usd": "0.0142"}, {"cost_usd": "0.0031"}, {"cost_usd": None}]
    emitter = StreamingEmitter(coord)

    await emitter.on_orchestrator_complete("orchestrator:complete", {"session_id": "s", "turn_id": "t"})

    usage_ev = next(ev for ev in coord.emitted if ev["type"] == "usage")
    assert usage_ev["sessionCostTotal"] == "0.0173"  # Decimal precision preserved
    assert usage_ev["inputTokens"] == 0
    assert usage_ev["outputTokens"] == 0


@pytest.mark.asyncio
async def test_orchestrator_complete_emits_nothing_when_no_cost() -> None:
    """No contributions (or all None) → no usage event."""
    coord = _MockCoordinator()
    coord.contributions = [{"cost_usd": None}]
    emitter = StreamingEmitter(coord)

    await emitter.on_orchestrator_complete("orchestrator:complete", {"session_id": "s", "turn_id": "t"})

    assert not any(ev["type"] == "usage" for ev in coord.emitted)


@pytest.mark.asyncio
async def test_orchestrator_complete_safe_without_collect_capability() -> None:
    """A coordinator lacking collect_contributions does not raise."""

    class _Bare:
        def __init__(self) -> None:
            self.emitted: list[dict] = []

        def get_capability(self, name: str) -> object:
            async def _emit(event: dict) -> None:
                self.emitted.append(event)

            return _emit

    bare = _Bare()
    emitter = StreamingEmitter(bare)
    result = await emitter.on_orchestrator_complete("orchestrator:complete", {"session_id": "s", "turn_id": "t"})
    assert result.action == "continue"
    assert bare.emitted == []
```

**Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_bundle_hook_streaming.py -k orchestrator_complete -v`
Expected: FAIL — `on_orchestrator_complete` does not exist.

**Step 3: Write the minimal implementation**

(3a) Add a module-level inline aggregator near the top of the file, just below `_parse_agent_name` (added in Task 1). Also add the `Decimal` import at the top of the file (after the existing `from typing import ...` line):

```python
from decimal import Decimal, InvalidOperation
```

```python
def _sum_cost_usd(results: list[dict[str, Any]]) -> str | None:
    """Sum ``cost_usd`` contributions, preserving Decimal precision.

    Replicated inline (not imported) from
    ``amplifier_foundation.bundle._prepared.sum_cost_usd`` to keep this hook
    free of foundation coupling.  Contributions carry cost as a string (the
    kernel's Decimal-as-string convention).  Returns the total as a string, or
    ``None`` when no contributor reported a cost.
    """
    total: Decimal | None = None
    for entry in results:
        raw = entry.get("cost_usd")
        if raw is None:
            continue
        try:
            value = Decimal(str(raw))
        except (InvalidOperation, ValueError):
            continue
        total = value if total is None else total + value
    return str(total) if total is not None else None
```

(3b) Add the handler to `StreamingEmitter`, after `on_llm_response`:

```python
    async def on_orchestrator_complete(self, event: str, data: dict[str, Any]) -> HookResult:
        """Kernel ``orchestrator:complete`` → wire session-total ``usage``.

        Collects per-call ``session.cost`` contributions from the coordinator
        and emits a single ``usage`` event carrying ``sessionCostTotal``.  Token
        counts are zero on this rollup event (the required schema fields are
        satisfied; the meaningful payload is the cost total).
        """
        collect = getattr(self._coordinator, "collect_contributions", None)
        if collect is None:
            return HookResult(action="continue")
        results = collect("session.cost") or []
        total = _sum_cost_usd(results)
        if total is not None:
            await self._emit(
                {
                    "type": "usage",
                    "sessionId": data.get("session_id", ""),
                    "turnId": data.get("turn_id", ""),
                    "inputTokens": 0,
                    "outputTokens": 0,
                    "sessionCostTotal": total,
                }
            )
        return HookResult(action="continue")
```

**Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_bundle_hook_streaming.py -k orchestrator_complete -v`
Expected: PASS (all three).

**Step 5: Commit**

```bash
git add src/amplifier_agent_lib/bundle/hook_streaming.py tests/test_bundle_hook_streaming.py
git commit -m "feat(streaming-hook): roll up session cost on orchestrator:complete

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

---

### Task 6: Register the new handlers in `mount()` (10 handlers total)

**Files:**
- Modify: `src/amplifier_agent_lib/bundle/hook_streaming.py:233-256`
- Test: `tests/test_bundle_hook_streaming.py:69-86` (update `test_mount_registers_seven_handlers`)

**Step 1: Update the failing structural test**

Replace `test_mount_registers_seven_handlers` (lines 69–86) with:

```python
@pytest.mark.asyncio
async def test_mount_registers_ten_handlers() -> None:
    """mount() must register exactly 10 handlers on coordinator.hooks."""
    coord = _MockCoordinator()
    await mount(coord)

    registered_events = [evt for evt, _, _ in coord.hooks.registered]
    expected_events = {
        "tool:pre",
        "tool:post",
        "tool:error",
        "content_block:start",
        "content_block:delta",
        "content_block:end",
        "llm:response",
        "thinking:delta",
        "thinking:final",
        "orchestrator:complete",
    }
    assert len(coord.hooks.registered) == 10
    assert set(registered_events) == expected_events
```

**Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_bundle_hook_streaming.py::test_mount_registers_ten_handlers -v`
Expected: FAIL — only 7 handlers registered.

**Step 3: Write the minimal implementation**

In `mount()`, append three registrations after the existing `llm:response` registration (line 256), and update the docstring's event list to include the three new events:

```python
    hooks.register("llm:response", emitter.on_llm_response, name="streaming_hook")
    hooks.register("thinking:delta", emitter.on_thinking_delta, name="streaming_hook")
    hooks.register("thinking:final", emitter.on_thinking_final, name="streaming_hook")
    hooks.register("orchestrator:complete", emitter.on_orchestrator_complete, name="streaming_hook")
```

Also update the `mount()` docstring (lines 234–246) so its bulleted list reflects all 10 events (add `thinking:delta`, `thinking:final`, `orchestrator:complete`) and change "registers 7 handlers" to "registers 10 handlers".

**Step 4: Run the full hook test file to verify everything passes**

Run: `uv run pytest tests/test_bundle_hook_streaming.py -v`
Expected: PASS — all tests (original 14 minus the renamed one, plus the new ~13 from this phase).

**Step 5: Commit**

```bash
git add src/amplifier_agent_lib/bundle/hook_streaming.py tests/test_bundle_hook_streaming.py
git commit -m "feat(streaming-hook): mount thinking + orchestrator:complete handlers

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

**Phase 2 complete.** Proceed to Phase 3 only when `tests/test_bundle_hook_streaming.py` is fully green.

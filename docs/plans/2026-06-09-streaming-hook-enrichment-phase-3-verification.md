# Streaming Hook Enrichment — Phase 3: Verification Implementation Plan

> **Execution:** Use the subagent-driven-development workflow to implement this plan. **Do not start Phase 3 until Phases 1 and 2 are fully green.**

**Goal:** Prove end-to-end that a full simulated turn produces a wire stream carrying every new field (cost, timing, model, provider, cache tokens, agentName, thinking, session-total cost), then run the complete quality gate (pytest + ruff + pyright) and confirm the protocol artifacts are not stale.

**Architecture:** This phase adds one integration-style test that drives a `StreamingEmitter` through a realistic event sequence (tool calls, thinking, an enriched `llm:response`, and `orchestrator:complete`) and asserts the captured wire events. It then runs repo-wide verification. No production code changes are expected; if verification surfaces a defect, fix it under TDD before proceeding.

**Tech Stack:** Python 3.12, pytest + pytest-asyncio (strict mode), ruff, pyright.

---

## Context the implementer must know

- The hook emits events through the `display.emit` capability. The existing `_MockCoordinator` in `tests/test_bundle_hook_streaming.py` captures them into `coord.emitted` and (after Phase 2 Task 5) supports `collect_contributions` via `coord.contributions`.
- A realistic turn ordering is: `tool:pre` → `tool:post` → `content_block:start` → `content_block:end` (result text) → `thinking:delta` → `thinking:final` → `llm:response` (enriched usage + result/final) → `orchestrator:complete` (session-total usage).
- **`ISSUES.md` has no open entry** for cost / duration / thinking visibility (verified). There is nothing to close there — the original "close the debt item" step is intentionally omitted.

---

### Task 1: Add a full-turn integration wire-capture test

**Files:**
- Test: `tests/test_bundle_hook_streaming.py`

**Step 1: Write the failing test**

Append to `tests/test_bundle_hook_streaming.py`:

```python
# ---------------------------------------------------------------------------
# Sub-cycle 11I: full-turn integration — every enriched field reaches the wire
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_turn_wire_stream_carries_all_enrichment() -> None:
    """Drive a realistic sub-agent turn and assert the wire carries every new field.

    Sequence: tool call -> result text -> thinking -> enriched llm:response ->
    orchestrator:complete session-cost rollup.
    """
    coord = _MockCoordinator()
    coord.contributions = [{"cost_usd": "0.0142"}, {"cost_usd": "0.0031"}]
    emitter = StreamingEmitter(coord)

    sid = "root-1_explorer"  # delegated session -> agentName == "explorer"
    tid = "turn-1"

    # 1. Tool call
    await emitter.on_tool_pre(
        "tool:pre",
        {"session_id": sid, "turn_id": tid, "tool_call_id": "c1", "tool": "bash", "arguments": {"cmd": "ls"}},
    )
    await emitter.on_tool_post(
        "tool:post",
        {"session_id": sid, "turn_id": tid, "tool_call_id": "c1", "tool": "bash", "result": {"stdout": "x"}, "duration_ms": 5},
    )

    # 2. Result text via content_block
    await emitter.on_content_block_start("content_block:start", {"session_id": sid, "turn_id": tid, "block_id": "b1"})
    await emitter.on_content_block_end(
        "content_block:end",
        {"session_id": sid, "turn_id": tid, "block_id": "b1", "block": {"type": "text", "text": "Here is the answer"}},
    )

    # 3. Thinking
    await emitter.on_thinking_delta("thinking:delta", {"session_id": sid, "turn_id": tid, "text": "reasoning..."})
    await emitter.on_thinking_final("thinking:final", {"session_id": sid, "turn_id": tid, "text": "done reasoning"})

    # 4. Enriched llm:response
    await emitter.on_llm_response(
        "llm:response",
        {
            "session_id": sid,
            "turn_id": tid,
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
        },
    )

    # 5. Session-total rollup
    await emitter.on_orchestrator_complete("orchestrator:complete", {"session_id": sid, "turn_id": tid})

    types = [ev["type"] for ev in coord.emitted]
    # All canonical types present in the expected order-of-appearance.
    assert "tool/started" in types
    assert "tool/completed" in types
    assert "result/delta" in types
    assert "thinking/delta" in types
    assert "thinking/final" in types
    assert "result/final" in types
    assert types.count("usage") == 2  # per-call usage + session-total rollup

    # Sub-agent attribution propagated to tool + per-call usage events.
    started = next(ev for ev in coord.emitted if ev["type"] == "tool/started")
    assert started["agentName"] == "explorer"

    per_call_usage = next(
        ev for ev in coord.emitted if ev["type"] == "usage" and "llmDurationMs" in ev
    )
    assert per_call_usage["llmDurationMs"] == 3200
    assert per_call_usage["model"] == "claude-opus-4-20250514"
    assert per_call_usage["provider"] == "anthropic"
    assert per_call_usage["cacheReadTokens"] == 600
    assert per_call_usage["cacheWriteTokens"] == 47
    assert per_call_usage["cost"] == "0.0142"
    assert per_call_usage["agentName"] == "explorer"

    # Session-total rollup event.
    rollup = next(ev for ev in coord.emitted if ev["type"] == "usage" and "sessionCostTotal" in ev)
    assert rollup["sessionCostTotal"] == "0.0173"
```

**Step 2: Run the test to verify it passes**

Run: `uv run pytest tests/test_bundle_hook_streaming.py::test_full_turn_wire_stream_carries_all_enrichment -v`
Expected: PASS. (This is an integration test over code already implemented in Phase 2; it should pass immediately. If it does NOT pass, a Phase 2 task was implemented incorrectly — STOP, identify which field is missing, and fix the corresponding Phase 2 handler under TDD before continuing. Do not weaken this test to make it pass.)

**Step 3: Commit**

```bash
git add tests/test_bundle_hook_streaming.py
git commit -m "test(streaming-hook): full-turn integration wire-capture coverage

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

---

### Task 2: Run the full quality gate (format, lint, types, tests)

**Files:** none (verification only; fix-and-recommit if issues surface)

**Step 1: Format and lint the changed source + tests**

Run:
```bash
uv run ruff format src/amplifier_agent_lib/bundle/hook_streaming.py src/amplifier_agent_lib/protocol/notifications.py tests/test_bundle_hook_streaming.py tests/test_protocol_notifications.py
uv run ruff check src/amplifier_agent_lib/bundle/hook_streaming.py src/amplifier_agent_lib/protocol/notifications.py tests/test_bundle_hook_streaming.py tests/test_protocol_notifications.py
```
Expected: ruff check reports no errors. If `ruff format` rewrote any file, re-stage and amend: `git add -u && git commit --amend --no-edit` (only if the amend stays within this phase's commit; otherwise make a new `style:` commit).

**Step 2: Type-check the changed modules**

Run: `uv run pyright src/amplifier_agent_lib/bundle/hook_streaming.py src/amplifier_agent_lib/protocol/notifications.py`
Expected: no errors. (Watch specifically for any residual float-typed `cost` usage — Phase 1 Task 2 should have eliminated it.)

**Step 3: Run the full test suite**

Run: `uv run pytest -q`
Expected: all tests pass. Pay attention to these previously-touched or adjacent suites:
- `tests/test_bundle_hook_streaming.py` (renamed + new tests)
- `tests/test_protocol_notifications.py` (cost-str + new field tests)
- `tests/test_protocol_gen_staleness.py` (artifacts fresh)
- `tests/test_protocol_conformance_fixtures.py` / `tests/test_conformance_fixtures_freshness.py`
- `tests/test_cheatsheet_first_run_cost.py` (sanity — references cost; confirm unaffected)

If any test fails, STOP and report the exact failure. Do not mark this phase complete with a red suite.

---

### Task 3: Confirm protocol artifacts are not stale and finalize

**Files:** none (verification only)

**Step 1: Re-run the generator and confirm zero diff**

Run:
```bash
uv run python -m amplifier_agent_lib.protocol._gen --output-dir src/amplifier_agent_lib/protocol
git status --short
```
Expected: `git status --short` shows **no** changes to `spec.md` or `schemas/` (Phase 1 already regenerated and committed them). If it shows a diff, Phase 1's regeneration was incomplete — commit the regenerated artifacts now with a `feat(streaming-hook): regenerate protocol artifacts` message.

**Step 2: Final full-suite confirmation (the gate function)**

Run: `uv run pytest -q`
Expected: PASS. Read the summary line; confirm 0 failed, 0 errored.

**Step 3: Summarize evidence**

Confirm and record (for the PR description) the real evidence:
- Full `pytest -q` summary line (e.g. `NNN passed`)
- `ruff check` clean
- `pyright` clean
- `git status --short` clean after regeneration

**Note (intentionally skipped):** `ISSUES.md` has no cost/duration/thinking-visibility debt entry to close. State this explicitly in the PR description rather than fabricating a closed item.

**Phase 3 complete.** The enrichment is implemented, tested end-to-end, and verified. Ready for review / PR.

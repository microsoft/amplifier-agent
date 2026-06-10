# Streaming Hook Enrichment — Implementation Plan Index

> **Execution:** Use the subagent-driven-development workflow to implement these plans, phase by phase, in order. Phase 2 depends on Phase 1. Phase 3 depends on Phase 2.

**Goal:** Make the amplifier-agent wire stream answer "what is being done, for how long, and at what cost?" on every step — by extracting kernel data the streaming hook currently discards, surfacing reasoning ("thinking") content, attributing actions to sub-agents, and rolling up per-session cost.

**Why (user feedback):** A reviewer reported the wire stream is "too vague" — no per-step time on the expensive (LLM) step, no model/provider attribution, no visibility into reasoning, and no cost data for budget plumbing. All the missing data is *already produced by the kernel*; the hook simply drops it.

---

## Architecture

`src/amplifier_agent_lib/bundle/hook_streaming.py` subscribes to 7 kernel hook
events and translates them into slash-separated "wire" display events emitted
through the `display.emit` capability. Today it extracts only `input_tokens`
and `output_tokens` from `llm:response`, and it never subscribes to thinking or
orchestrator-completion events. The enrichment is **three additive parts**, with
no breaking changes to the wire taxonomy (it is already fixed at 9 canonical
display events; we only start *producing* two of them):

1. **Richer `llm:response` extraction.** Pull `duration_ms`, `model`,
   `provider`, `cache_read_tokens`, `cache_write_tokens`, and `cost_usd` out of
   the event (and its nested `usage` sub-dict) and attach them to the `usage`
   wire event as optional fields. Cost is carried as a **string** (the kernel
   serializes its `Decimal` cost to a string to preserve monetary precision).

2. **Session-total cost rollup.** Subscribe to `orchestrator:complete` and call
   `coordinator.collect_contributions("session.cost")`, summing the per-call
   contributions with an inline `Decimal` aggregator (replicated, not imported,
   to keep the hook free of foundation coupling). Emit a session-total `usage`
   event carrying `sessionCostTotal`.

3. **Thinking visibility + sub-agent attribution.** Subscribe to
   `thinking:delta` / `thinking:final` and emit the already-defined
   `thinking/delta` / `thinking/final` wire types. Parse the session id
   (`{parent}-{child}_{agent_name}`) to attach `agentName` to tool and usage
   events so consumers can tell root actions from sub-agent actions.

**Authoritative source of truth:** The Python TypedDicts in
`src/amplifier_agent_lib/protocol/notifications.py` are the canonical wire spec.
`schemas/*.schema.json` and `spec.md` are **generated** from them and must never
be hand-edited. A staleness CI gate (`tests/test_protocol_gen_staleness.py`)
fails any PR that edits a TypedDict without regenerating.

**Tech Stack:** Python 3.12 (uv-managed), pytest + pytest-asyncio (strict mode),
amplifier-core hooks, JSON-RPC NDJSON wire protocol, generated JSON Schema
(Draft 2020-12), `Decimal` for monetary precision.

---

## Phases

| Phase | Document | Tasks | Summary |
|---|---|---|---|
| 1 | [`2026-06-09-streaming-hook-enrichment-phase-1-protocol.md`](2026-06-09-streaming-hook-enrichment-phase-1-protocol.md) | 6 | Extend `UsageNotification` (cost `float`→`str` + 7 optional fields), add `agentName` to tool notifications, regenerate schemas + spec |
| 2 | [`2026-06-09-streaming-hook-enrichment-phase-2-hook.md`](2026-06-09-streaming-hook-enrichment-phase-2-hook.md) | 6 | Enrich the hook: `_parse_agent_name`, richer `on_llm_response`, agentName on tool events, thinking handlers, `on_orchestrator_complete`, mount 10 handlers |
| 3 | [`2026-06-09-streaming-hook-enrichment-phase-3-verification.md`](2026-06-09-streaming-hook-enrichment-phase-3-verification.md) | 3 | Full-turn integration wire-capture test, full quality gate (pytest + ruff + pyright), final staleness verification |

**Total: 15 tasks.** Implement strictly in order. Do not start Phase 2 until
Phase 1's full test suite is green; do not start Phase 3 until Phase 2's is.

---

## Scope boundaries

**In scope (v1):**
- All enriched fields on the `usage` wire event (`cost` as `str`, `llmDurationMs`,
  `model`, `provider`, `cacheReadTokens`, `cacheWriteTokens`, `sessionCostTotal`)
- `agentName` attribution on `tool/started`, `tool/completed`, `usage`
- Session-total cost via `orchestrator:complete`
- Thinking event subscription + `thinking/delta` / `thinking/final` emission
- `cost` type change `float` → `str` (precision fix)
- Test coverage matching the existing TDD sub-cycle pattern

**Deferred (do NOT implement here):**
- `provider:retry` / throttle subscription (no consumer asking yet)
- A new standalone streaming module (revisit only if a second consumer appears)
- Host adapter (Paperclip / NanoClaw) rendering changes (separate repo)
- Any new canonical wire event type beyond the 9 already in
  `CANONICAL_DISPLAY_EVENTS`

---

## Key files (verified)

- `src/amplifier_agent_lib/bundle/hook_streaming.py` — the hook (256 lines)
- `src/amplifier_agent_lib/protocol/notifications.py` — TypedDicts (source of truth)
- `src/amplifier_agent_lib/protocol/_gen.py` — schema/spec generator
- `src/amplifier_agent_lib/protocol/schemas/UsageNotification.schema.json` — generated
- `src/amplifier_agent_lib/protocol/spec.md` — generated
- `tests/test_bundle_hook_streaming.py` — 14 hook tests (sub-cycles 11A–11E)
- `tests/test_protocol_notifications.py` — TypedDict roundtrip tests
- `tests/test_protocol_gen_staleness.py` — staleness CI gate

**Regeneration command (memorize — used in Phases 1 and 3):**

```bash
uv run python -m amplifier_agent_lib.protocol._gen --output-dir src/amplifier_agent_lib/protocol
```

**Note on `ISSUES.md`:** Checked — there is no open debt entry for cost /
duration / thinking visibility. There is therefore **nothing to close** there;
the original "close the debt item" step is intentionally dropped.

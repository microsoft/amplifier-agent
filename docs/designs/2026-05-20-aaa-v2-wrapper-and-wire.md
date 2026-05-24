# AaA v2 — Wrapper Layer + Wire Protocol + Engine Gap Fixes

**Status:** LOCKED design — ready for Phase 2.0c implementation
**Author:** Manoj Prabhakar Paidiparthy (implementation lead)
**Reviewer (primary):** Brian Krabach
**Date locked:** 2026-05-20
**Supersedes / amends:** `docs/status/amplifier-as-agent-design-checkpoint.md` §4 (amended via Appendix A of this document)
**Related:** `docs/designs/2026-05-19-baked-in-bundle-decision.md`, `docs/designs/2026-05-19-baked-in-bundle-revisit.md`
**Empirical validation:** Paperclip codebase survey (`/Users/mpaidiparthy/repos/AaA/paperclip`, 2026-05-20); NanoClaw codebase survey (`/Users/mpaidiparthy/repos/AaA/nanoclaw`, 2026-05-20)
**Audience:** Implementation team; downstream adapter authors (NanoClaw, Paperclip, OpenClaw skill, CELA tool, CI scripts); operators

> ⚠️ **AMENDED by `docs/designs/2026-05-24-aaa-v2-mode-a-pivot-amendment.md`.** Mode B (stdio JSON-RPC) was specified here but never implemented. The amendment pivots the wire to Mode A (subprocess driver) for v1, deferring Mode B's capabilities to v1.x. The §4.1 mode topology and §5 flows in this document are obsolete for v1 wire purposes; the §4.2 wrapper layering and §8.1 decisions D5/D10 are preserved.

---

## Executive summary

This document locks the Layer 3 boundary of `microsoft/amplifier-agent` v2 as one coherent shipping unit: the TypeScript wrapper (`amplifier-agent-client-ts`), the Python wrapper (`amplifier-agent-client-py`), the wire contract they speak with the engine, the cross-language conformance suite that proves parity, the engine-side fixes required to make the wire shape clean, and the streaming hook in the vendored bundle that makes display events real. The wrapper public API from design checkpoint §4 is preserved with one amendment: `lifecycle` is locked to `'one-shot'` in v1, with `'burst'` reserved-but-rejected for a future minor-version-additive change. The amendment is grounded in empirical surveys of both downstream hosts — Paperclip's `codex-local` adapter spawns a fresh subprocess per `execute(ctx)` call (`packages/adapters/codex-local/src/server/execute.ts:710`), and NanoClaw's `add-codex` skill explicitly documents "no long-lived daemon to keep healthy across sessions" (`add-codex/SKILL.md:139`). No host on Brian's roadmap needs in-process burst.

Ten named decisions (D1–D10, §8) drive the design: Python TypedDicts are the authoritative wire-spec source with `protocol/_gen.py` generating `spec.md` + JSON schemas; engine gaps are fixed first then snapshotted into wire v0; cancel is reduced to `SIGTERM` of the subprocess (the `turn/cancel` JSON-RPC method is removed entirely); mid-engine death surfaces as `SessionHandle` done with a typed `AaaError`; binary discovery uses `PATH` plus `AMPLIFIER_AGENT_BIN`; version-skew is strict-refuse with a self-remediating error message; conformance is handwritten dual-language tests plus shared YAML wire-sequence fixtures; first-run cliff is removed via install-time `prepare`; sub-agent display events are leak-by-default with opt-out; lifecycle is one-shot only. The net engine-code change is approximately 600 LOC deleted (cancel routing infrastructure, lost-state machinery, burst-mode stdio framing that no longer needs to exist) against approximately 400 LOC added (streaming hook, init-param threading, prepare verb).

This design closes eight named failure modes (V1 F5, F7, F11, F13a, F15, plus NanoClaw L14, NanoClaw L16, plus critic finding CR-4) by structural construction rather than runtime guards. It accepts three named residual risks (R1 hook-coverage minimum-set being wrong; R2 install-time prepare reliability; R3 strict-refuse skew tripping on routine releases) each with a concrete monitoring signal in §11.

---

## 1. Problem framing

Ship the Layer 3 wrapper boundary of `microsoft/amplifier-agent` v2 as one coherent artifact:

- A **TypeScript wrapper** (`amplifier-agent-client-ts`) — primary day-one consumer (NanoClaw, Paperclip).
- A **Python wrapper** (`amplifier-agent-client-py`) — Brian's primary work harness; future Python adapters.
- The **wire contract** they speak with the engine — today exists only as Python `TypedDict`s scattered across `src/amplifier_agent_lib/protocol/{methods,notifications,errors,capabilities}.py`.
- A **cross-language conformance suite** that proves both wrappers and the engine agree.
- The **engine-side fixes** required to make the wire shape clean — five named gaps surfaced during scout, plus four findings from adversarial review (CR-1 through CR-4).
- A **streaming hook** in the vendored bundle that makes display events real — without it, gap (d) means production today emits zero display events to the wire, regardless of how good the wrapper is.

The problem is bounded by two facts the design pass surfaced:

1. **The wire-protocol-is-Python-only state is legacy, not design.** When Layer 4 was built, "engine is the only producer/consumer" was implicit. The instant a second consumer (TypeScript) enters, the contract must live in language-neutral form. Appendix A of the design checkpoint already names `protocol/spec.md` + `protocol/schemas/` as the intended artifacts; they just don't exist yet.

2. **Empirical surveys of Paperclip and NanoClaw show one-shot per turn is the in-production pattern.** Both Codex and Claude Code adapters in Paperclip spawn fresh subprocesses per `execute(ctx)`. NanoClaw's `add-codex` skill is explicit about preferring spawn-per-query over a long-lived daemon. NanoClaw's Claude SDK provider holds the `MessageStream` open across `push()` only because the SDK API requires it — and that lives entirely inside the container, not at the host boundary AaA sits at. No host on Brian's roadmap needs the engine to hold state across `submit()` calls.

The empirical findings collapse the v1 design surface: `lifecycle: 'burst'` is removed from v1, `turn/cancel` JSON-RPC is removed from the wire (SIGTERM the subprocess is the cancel), and lost-state machinery is removed (subprocess exit = `SessionHandle` done). Session continuity is preserved via `sessionId` + `resume` on `agent/initialize`, which works exactly the way Paperclip's `claude-local` adapter uses Claude Code's `--resume <session_id>` flag.

---

## 2. Explicit assumptions

| # | Assumption | Validation status | If wrong, design changes |
|---|---|---|---|
| **A0** | **One-shot per turn is sufficient for v1.** No host on Brian's roadmap (Paperclip, NanoClaw, OpenClaw skill, CELA tool, CI scripts) needs mid-turn `submit()` against the same subprocess. `'burst'` is reserved-but-unimplemented; adding it later is a minor-version-additive change per Appendix A of the design checkpoint. | **Validated empirically** (Paperclip + NanoClaw surveys, 2026-05-20) | If a future host needs in-process burst, add `'burst'` lifecycle in v1.x — additive, no breakage. |
| A1 | Single machine, single tenant, one engine subprocess per `submit()`, minutes-not-days lifetimes. | Confirmed by user. | If multi-session-per-process needed, wire and `SessionHandle` change. |
| A2 | Single implementation team (Manoj + AI assistance). Not multiple parallel implementers per wrapper. | Confirmed. | If split team owns each wrapper, conformance suite governance changes. |
| A3 | Weeks-not-months timeline. No hard external deadline. | Confirmed. | Tighter deadline → cut Python wrapper to follow-on release. |
| A4 | **Streaming hook can emit the minimum-coverage set (5 of 9 canonical events) from inside our vendored bundle.** Modeled on `microsoft/amplifier-module-hooks-streaming-ui`. Foundation kernel is stable enough to build a hook against. | **Riskiest assumption.** Mitigated by SC-1 kernel investigation as a Phase 2.0c prerequisite (see §6 / §10). | If kernel doesn't emit `tool/started` separately from `tool/completed`, minimum-set must be revised before conformance authoring. |
| A5 | No regulatory or compliance constraints that gate publishing schemas. Microsoft OSS compliance files already in repo (commit `0eec2a8`) cover what's required. | Confirmed. | Schema redaction or vetting pipeline if MSR-side concerns arise. |
| A6 | Linux and macOS are the gating platforms. Windows is best-effort, non-blocking. | Confirmed. | Add Windows CI matrix if a Windows-only consumer materializes. |
| A7 | `context-simple` (the vendored bundle's context module) handles `is_resumed=True` correctly for transcript replay. No production test currently validates this end-to-end. | **Unverified.** Tracked as a §10 implementation prerequisite. | If `context-simple` doesn't replay on resume, swap to `context-persistent` in the bundle (commit `654dfac` and `44db0f4` ruled this out for cold-start reasons; revisit only if forced). |
| A8 | Cold-start of the engine subprocess on primed steady-state lands < 500ms p95 (Phase 2.0e measurement; not yet run). | Unmeasured. | If steady-state p95 > 2s, `'burst'` lifecycle is reconsidered (§11 metric). |

---

## 3. System boundaries

```
                              ┌────────────────────────────────────────┐
                              │       Adapter / Host (L1 + L2)         │
                              │   Paperclip · NanoClaw · OpenClaw      │
                              │   skill · CELA tool · CI scripts       │
                              └──────────────────┬─────────────────────┘
                                                 │  (uses)
                                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    THIS DESIGN (Layer 3 boundary)                        │
│                                                                          │
│  ┌──────────────────────────┐         ┌──────────────────────────┐      │
│  │   amplifier-agent-       │         │   amplifier-agent-       │      │
│  │     client-ts (npm)      │         │     client-py (PyPI)     │      │
│  │  spawnAgent / SessionH.  │         │  spawn_agent / Session-  │      │
│  │  Transport · JsonRpc     │         │  Handle · Transport ·    │      │
│  │  Client · Hooks · Errors │         │  JsonRpcClient · Hooks   │      │
│  └─────────────┬────────────┘         └────────────┬─────────────┘      │
│                │                                   │                     │
│                └───────────────┬───────────────────┘                     │
│                                │  speaks wire v0                         │
│                                ▼                                         │
│           ┌──────────────────────────────────────────┐                  │
│           │   protocol/  (wire spec — wire v0)       │                  │
│           │   ──────────────────────────────────     │                  │
│           │   _gen.py  (TypedDicts → md + schemas)   │                  │
│           │   spec.md  (generated, language-neutral) │                  │
│           │   schemas/ (generated JSON Schema)       │                  │
│           │   fixtures/ (YAML wire-sequence fixtures)│                  │
│           └────────────────────┬─────────────────────┘                  │
│                                │                                         │
│           ┌────────────────────┴────────────────────┐                  │
│           │   conformance/  (shared test corpus)    │                  │
│           │   ──────────────────────────────────    │                  │
│           │   handwritten dual TS + Py harnesses    │                  │
│           │   YAML fixtures consumed by both        │                  │
│           │   filename + scenario-name parity lint  │                  │
│           └─────────────────────────────────────────┘                  │
└─────────────────────────────────────────┬────────────────────────────────┘
                                          │ JSON-RPC 2.0 over NDJSON over stdio
                                          ▼
              ┌──────────────────────────────────────────────────┐
              │       amplifier-agent (CLI binary, L4)           │
              │       PATH-resolved or AMPLIFIER_AGENT_BIN       │
              │                                                  │
              │   src/amplifier_agent_cli/                       │
              │     modes/single_turn.py  (Mode A, --stdio off)  │
              │     modes/stdio_loop.py   (Mode B, --stdio on)   │
              │     verbs/{prepare,doctor,cache_clear,...}       │
              │                                                  │
              │   src/amplifier_agent_lib/                       │
              │     engine.py · jsonrpc.py · _runtime.py         │
              │     protocol/{methods,notifications,errors,...}  │
              │     protocol_points/{base,defaults_cli,          │
              │                      defaults_stdio}.py          │
              │     bundle/  (vendored manifest + cache)         │
              │     bundle/hooks/streaming_emitter.py  ← NEW     │
              │                                                  │
              │   ENGINE GAPS FIXED IN-SCOPE: a · b · c · d · e  │
              └────────────────────────┬─────────────────────────┘
                                       │
                                       ▼
                        ┌──────────────────────────────┐
                        │   amplifier-foundation       │
                        │   (L5 kernel — external)     │
                        │   session.execute()          │
                        │   event-log → hook bridge    │
                        └──────────────────────────────┘
```

**In scope (this design):** wrappers, wire spec, conformance, engine gaps a–e, streaming hook in vendored bundle.
**Out of scope:** L1 host code, L2 host adapters, L4 engine internals not in (a–e), L5 foundation kernel changes.

---

## 4. Components and responsibilities

Eight named components. Each component has a single primary responsibility and a small, named contract surface.

### 4.1 `protocol/` — language-neutral wire spec (wire v0)

**Source of truth:** Python `TypedDict` definitions in `src/amplifier_agent_lib/protocol/{methods,notifications,errors,capabilities}.py`.

**Generator:** `src/amplifier_agent_lib/protocol/_gen.py` (NEW). Reads the TypedDicts, emits:

- `protocol/spec.md` — generated, language-neutral reference. Headed with a "DO NOT HAND-EDIT" banner. Regeneration is a CI step; PRs that change the file without regenerating are blocked.
- `protocol/schemas/*.json` — JSON Schema (Draft 2020-12) per wire object. Consumed by both wrappers' tests and by external schema-validating clients.
- `protocol/fixtures/*.yaml` — shared wire-sequence fixtures (see §4.6).

**Wire framing:** JSON-RPC 2.0 over NDJSON over stdio. Stdout is sacred (frames only); stderr is free (log lines, diagnostics).

**Protocol version:** `PROTOCOL_VERSION = "2026-05-aaa-v0"` (snapshotted on Phase 2.0c completion).

**Methods (client → engine):**
- `agent/initialize` — params now honored: `sessionId`, `resume`, `cwd`, `providerOverride`, `env` (gap c fix).
- `agent/shutdown`.
- `turn/submit` — single turn per subprocess lifetime in v1.

**Notifications (engine → client):**
- `display/event` — the 9 canonical event types (see §4.4).
- `result/final` — emitted at end of turn; if engine omits (legacy), wrapper synthesizes (the L14 path, kept as a safety net).
- `error/*` — typed errors with named codes.

**Server-initiated request (engine → client):**
- `approval/request` — engine asks client to approve a tool call; client replies with `approval/response`. Routed through a dedicated response channel to keep the dispatch loop deterministic.

**Removed from wire (D3):** `turn/cancel`. Replaced by SIGTERM of the subprocess.

### 4.2 `amplifier-agent-client-ts/` — TypeScript wrapper (`amplifier-agent-client-ts`)

**Public API:** locked in §8 below. Single entry point `spawnAgent(...) → Promise<SessionHandle>`.

**Internal layering:**

- `transport.ts` — child_process spawn, stdin/stdout pipes, stderr drain to optional sink, lifecycle (spawn / await exit / SIGTERM on `cancel()` or `dispose()`).
- `jsonrpc-client.ts` — NDJSON framing, message ID allocation, request/response correlation, notification fanout, server-initiated request handling for approvals.
- `session-handle.ts` — implements the public `SessionHandle` interface. Owns the `AsyncIterable<DisplayEvent>` returned from `submit()`.
- `errors.ts` — typed `AaaError` hierarchy with named codes: `engine_not_primed`, `protocol_version_mismatch`, `engine_crashed`, `approval_timeout`, `lifecycle_unsupported`, etc.
- `version.ts` — wrapper `PROTOCOL_VERSION` constant; compared strict-equal against engine's at `agent/initialize`.

**Distribution:** npm package, dual ESM/CJS exports, no native dependencies. Node 18+.

### 4.3 `amplifier-agent-client-py/` — Python wrapper (`amplifier-agent-client-py`)

**Public API:** symmetric to TS, native Python idioms. `spawn_agent(...) → SessionHandle`. `SessionHandle.submit(prompt) → AsyncIterator[DisplayEvent]`.

**Internal layering:** mirrors TS layering above. `asyncio.subprocess` for transport. `dataclasses` for `DisplayEvent`, `AaaError`, etc. Type-stubbed for `mypy --strict`.

**Distribution:** PyPI package, Python 3.11+, no native dependencies.

**Symmetry contract:** for every TS public method/type, an idiomatic Python counterpart exists with identical semantics. The conformance suite enforces this by scenario-name parity (§4.6).

### 4.4 Canonical display event taxonomy (9 events)

Defined in `src/amplifier_agent_lib/protocol/notifications.py`. The minimum-set (5 of 9) is what `verify --check-hooks` exits 0 against; the additional 4 are nice-to-have but not gating.

| # | Event | Minimum-set? | Notes |
|---|---|---|---|
| 1 | `turn/started` | ✅ | One per `submit()`. |
| 2 | `tool/started` | ✅ | One per tool invocation. **A4 risk:** depends on foundation kernel emitting separately from `tool/completed`. |
| 3 | `tool/completed` | ✅ | Includes result digest. |
| 4 | `assistant/text` | ✅ | Streaming chunks of assistant prose. |
| 5 | `turn/completed` | ✅ | One per `submit()`; carries result summary. |
| 6 | `subagent/started` | ⬜ | Leak-by-default, opt-out via `display.subagentEvents: 'none'` (D9). |
| 7 | `subagent/completed` | ⬜ | Same as above. |
| 8 | `error/recoverable` | ⬜ | Non-fatal warning from inside a turn. |
| 9 | `provider/throttled` | ⬜ | Provider rate-limit / backoff signal. |

All sub-agent events carry `parentTurnId` lineage in their payload so the host can correlate them to the originating turn.

### 4.5 `src/amplifier_agent_lib/bundle/hooks/streaming_emitter.py` — the streaming hook (NEW)

**Pattern reference:** `microsoft/amplifier-module-hooks-streaming-ui`, but tailored: the upstream hook writes to stdout (incompatible with our framing); ours writes through `ctx.display.emit(event: DisplayEvent)`.

**Responsibility:** observe foundation kernel module-event-log entries during `session.execute()`; translate them into `DisplayEvent`s; call `ctx.display.emit()` for each. Resolves engine gap (d).

**Coverage gate:** `amplifier-agent verify --check-hooks` walks the loaded bundle, confirms the streaming hook is mounted, and that it emits the minimum-set 5 events against a recorded fixture session. Hard CI gate on every PR.

**Sub-agent leak control:** the hook honors a `subagent_events: 'all' | 'none'` setting threaded through `ctx`. Default `'all'`; set to `'none'` when `display.subagentEvents === 'none'` on the wrapper side. Mechanism: capability negotiation flag passed in `agent/initialize`.

### 4.6 `conformance/` — cross-language test corpus

**Structure:**

- `conformance/scenarios/` — one scenario per protocol contract. Each scenario has parallel TS + Py test files with **identical filenames** (`approval-roundtrip.test.ts` ↔ `approval_roundtrip.test.py`). Filename-parity lint blocks PRs that add one without the other.
- `conformance/fixtures/*.yaml` — **shared YAML wire-sequence fixtures** (SC-2 from critic). Each fixture lists frames in order, with marked checkpoints. Both language harnesses load the same YAML and assert identical observable behavior. This addresses critic finding H6 (parallel suites green while logically diverging).

**Five critical contracts covered (D7):**

1. **L14 synthesis (both branches).** Engine emits `result/final` → wrapper does not synthesize. Engine omits → wrapper synthesizes with `synthesized: true` marker. Fixture covers both.
2. **Approval round-trip.** Server-initiated `approval/request` → client `approval/response` → tool resumes. Includes timeout branch.
3. **Initialization param handling.** All of `sessionId`, `resume`, `cwd`, `providerOverride`, `env` echoed back in engine info (gap c fix verified).
4. **Version-skew strict-refuse.** Mismatched `PROTOCOL_VERSION` → typed `protocol_version_mismatch` error with remediation message. Includes `--allow-protocol-skew` override.
5. **Subprocess death = handle done.** SIGKILL the engine mid-turn → `SessionHandle.submit()` iterator raises typed `AaaError(code='engine_crashed')`, exit code surfaced.

**CI gate:** TS + Py suites green on Linux + macOS, YAML fixture parity 100%, filename-parity lint zero violations. Blocks every PR.

### 4.7 Engine — gap fixes (Layer 4, in-scope)

Five named gaps, ordered by dependency (a→b→e→c→d). See Appendix C for the detailed sequencing table.

| Gap | What | Fix summary |
|---|---|---|
| (a) | `StdioDisplaySystem.emit` signature doesn't conform to `DisplaySystem` Protocol (sync vs async; `event_type+payload` vs `DisplayEvent`) | Pick **async + single `DisplayEvent` arg** as the Protocol surface. Update both default impls to conform. Blocks (d). |
| (b) | `StdioApprovalSystem.request` signature doesn't conform to `ApprovalSystem` Protocol | Same shape as (a). Reconcile. |
| (e) | `turn/cancel` is silently consumed by `_dispatch_with_response_routing` (CR-4) | **Remove `turn/cancel` from wire entirely (D3).** Cancel = SIGTERM the subprocess. Net engine-code: deletion of ~150 LOC of routing infrastructure that no longer needs to exist. |
| (c) | `single_turn.py` AND `stdio_loop.py` both drop `sessionId`/`resume`/`cwd`/`providerOverride` from `agent/initialize` | Widen `_EngineProtocol.initialize()` signature; thread params through. Promote `_StdioEngine` anonymous inner class to a named, importable module (SC-7). |
| (d) | `_runtime.py:make_turn_handler` doesn't bridge `ctx.display` / `ctx.approval` into foundation `session.execute()` — **the entire display pipeline is theoretical until this is fixed** (CR-1) | Add bundle-level streaming hook (§4.5) and thread `ctx.display.emit` / `ctx.approval.request` callbacks into the kernel hook bridge. Depends on (a) and (b). |

**Also fixed inline (zero-cost):**

- L14-synthesized notifications missing `sessionId` field (5-line fix).
- Engine doesn't actually check `PROTOCOL_VERSION` against client (added to `agent/initialize` handler).
- `_StdioEngine` anonymous inner class promoted to `src/amplifier_agent_cli/engines/stdio_engine.py`.

### 4.8 `prepare` verb + first-run UX

**New CLI verb:** `amplifier-agent prepare`. Runs the full bundle resolution + module clone + `uv pip install` + cache warm pass. Idempotent. Designed to be invoked from a turnkey installer (npm postinstall, brew formula, `uv tool install` hook).

**`doctor` verb:** reports the primed state (cache present, hook coverage verified, binary discoverable, protocol version). Does NOT itself prime — that's `prepare`'s job. Avoids the downstream-UX leak the critic flagged (if `doctor --prime` were mandatory, NC and PC adapter authors would have to communicate "run this other command first" to their users).

**Typed error:** if the wrapper invokes the engine before `prepare` has been run, the engine exits with a typed `engine_not_primed` error containing the exact remediation command. The wrapper surfaces it as `AaaError(code='engine_not_primed', remediation='...')` so the adapter can show or auto-run it.

---

## 5. Data and control flows

### 5.1 Happy path (one-shot turn, no approval)

```
adapter                wrapper                 transport             engine                bundle / kernel
  │                       │                       │                    │                       │
  │ spawnAgent({...})     │                       │                    │                       │
  │──────────────────────▶│                       │                    │                       │
  │                       │ spawn engine subproc  │                    │                       │
  │                       │──────────────────────▶│ exec amplifier-    │                       │
  │                       │                       │ agent --stdio      │                       │
  │                       │                       │───────────────────▶│                       │
  │                       │                       │                    │ load vendored bundle  │
  │                       │                       │                    │ from cache (warm)     │
  │                       │                       │                    │──────────────────────▶│
  │                       │                       │                    │◀──────────────────────│
  │                       │ agent/initialize      │                    │                       │
  │                       │ {sessionId, resume,   │                    │                       │
  │                       │  cwd, providerOver-   │                    │                       │
  │                       │  ride, env, version}  │                    │                       │
  │                       │──────────────────────▶│───────────────────▶│ create session        │
  │                       │                       │                    │ (resume if asked)     │
  │                       │ {engineInfo, version} │                    │──────────────────────▶│
  │                       │◀──────────────────────│◀───────────────────│◀──────────────────────│
  │                       │ version match? ───→ no → AaaError(protocol_version_mismatch)        │
  │ SessionHandle ready   │                       │                    │                       │
  │◀──────────────────────│                       │                    │                       │
  │                       │                       │                    │                       │
  │ handle.submit(prompt) │                       │                    │                       │
  │──────────────────────▶│ turn/submit {prompt}  │                    │                       │
  │                       │──────────────────────▶│───────────────────▶│ session.execute()     │
  │                       │                       │                    │──────────────────────▶│
  │                       │                       │                    │                       │
  │ for await event       │ display/event (turn/started)               │ kernel emits to       │
  │◀══════════════════════│◀══════════════════════│◀═══════════════════│ streaming hook ──┐    │
  │                       │                       │                    │                  ▼    │
  │                       │ display/event (assistant/text chunks)      │ ctx.display.emit │    │
  │◀══════════════════════│◀══════════════════════│◀═══════════════════│◀─────────────────┘    │
  │                       │ display/event (tool/started)               │                       │
  │◀══════════════════════│◀══════════════════════│◀═══════════════════│                       │
  │                       │ display/event (tool/completed)             │                       │
  │◀══════════════════════│◀══════════════════════│◀═══════════════════│                       │
  │                       │ display/event (turn/completed)             │                       │
  │◀══════════════════════│◀══════════════════════│◀═══════════════════│                       │
  │                       │ result/final          │                    │                       │
  │                       │◀──────────────────────│◀───────────────────│                       │
  │ iterator ends         │                       │                    │                       │
  │◀══════════════════════│                       │                    │                       │
  │                       │ agent/shutdown        │                    │                       │
  │                       │──────────────────────▶│───────────────────▶│ teardown              │
  │                       │                       │                    │ exit(0)               │
  │                       │ child exit            │                    │                       │
  │                       │◀──────────────────────│                    │                       │
  │ handle.dispose()      │                       │                    │                       │
  │ (no-op; already gone) │                       │                    │                       │
```

### 5.2 Approval round-trip

Mid-turn, before a tool executes:

1. Kernel hook → `ctx.approval.request(ApprovalRequest)`.
2. Engine emits server-initiated request `approval/request {id, tool, args}` to client.
3. Wrapper's JSON-RPC client routes through the dedicated approval-response channel (not the notification fanout) to the host-supplied `approval.onRequest` callback.
4. Host returns `ApprovalResponse({decision: 'allow' | 'deny', ...})` within `approval.timeoutMs`.
5. Wrapper sends `approval/response {id, ...}`; engine resumes the tool.
6. Timeout: wrapper sends typed `approval/response {id, decision: 'timeout'}`; engine treats as deny.

The dispatch loop is deterministic: approval responses are routed by id; display events continue flowing on the notification channel concurrently.

### 5.3 Resume flow

1. Adapter calls `spawnAgent({sessionId: 'sess-abc', resume: true, ...})`.
2. Wrapper spawns engine, sends `agent/initialize {sessionId: 'sess-abc', resume: true, ...}`.
3. Engine's `_runtime.py` passes `is_resumed=True` to `bundle.create_session(...)`.
4. Vendored bundle's `context-simple` (subject to A7 verification) loads prior transcript from XDG-state.
5. Turn proceeds against loaded transcript.

This mirrors Paperclip's `claude-local` adapter use of `claude --resume <session_id>` exactly: one-shot subprocess, opaque session id on disk, transcript replay on re-spawn.

### 5.4 Mid-turn failure / cancel

**Cancel (D3):** adapter calls `handle.cancel()` or `handle.dispose()`. Wrapper sends SIGTERM to subprocess (5s grace, then SIGKILL). Async iterator raises `AaaError(code='cancelled')`. No `turn/cancel` JSON-RPC message exists.

**Engine crash (D4):** subprocess exits non-zero mid-turn. Transport closes pipes. JsonRpcClient surfaces close to SessionHandle. Iterator raises `AaaError(code='engine_crashed', exitCode, stderrTail)`. No "lost-state respawn" — `SessionHandle` is done. Adapter decides whether to spawn a new one (typically yes, since one-shot is cheap).

---

## 6. Risks and failure modes

### 6.1 V1 / NanoClaw failure modes designed out by this design

| ID | Failure | How design eliminates it |
|---|---|---|
| **F5** | `this.active` race in V1 (concurrent submit() corrupts shared state) | One subprocess per `SessionHandle`. No shared mutable pointer. Structurally impossible. |
| **F7** | L14 fix-by-implicit-contract (engine guarantee never written down) | `result/final` synthesis is explicit, marked, and tested in YAML fixture (both branches). Becomes a test failure if regressed. |
| **F11** | Mid-burst death corrupts session state | Burst removed (D10). One-shot = subprocess death = handle done. No state to corrupt. |
| **F13a** | First-run cliff (5–30s install delay on first invocation) | `prepare` verb runs at install time; runtime path is always warm-cache (§4.8). |
| **F15** | Cancel races consume non-cancel messages from dispatch loop | `turn/cancel` removed from wire (D3). SIGTERM has no routing concerns. |
| **NC-L14** | NanoClaw V1 `this.active` shared pointer | Same as F5 — structural. |
| **NC-L16** | NanoClaw V1 implicit "engine always emits final result" assumption | Same as F7 — explicit + tested. |
| **CR-4** | `turn/cancel` silently consumed by `_dispatch_with_response_routing` (critic finding) | Message type removed from wire. ~150 LOC of routing infrastructure deleted. |

**Regression-detector for these:** §11 metric "Zero recurrence of F5, F7, F11, F13a, F15, NC-L14, NC-L16, CR-4 in production." Any recurrence triggers a design-failure investigation, not a quick patch.

### 6.2 Residual risks (accepted with monitoring)

| ID | Risk | Monitoring signal | Trigger to revisit |
|---|---|---|---|
| **R1** | Streaming hook minimum-set is wrong — `tool/started` might not exist as a separate kernel event (A4) | `verify --check-hooks` outcome on real bundle; SC-1 kernel investigation as Phase 2.0c prerequisite | Investigation result. If `tool/started` is not a discrete kernel event, revise minimum-set before conformance authoring. |
| **R2** | `prepare` reliability at install time | Failure rate from turnkey installer telemetry | >1% failure triggers installer review. |
| **R3** | Strict-refuse version skew trips on routine releases (wrapper updates on one schedule, engine on another) | Operator log scan: count of `protocol_version_mismatch` per week | >1/week triggers reconsideration of strict-refuse posture or coordinated release cadence. |
| **R4** | `context-simple` doesn't replay transcript correctly on `is_resumed=True` (A7) | First end-to-end resume test in conformance suite | If test fails, swap bundle context module (revisits the 2026-05-19 bundle decision). |
| **R5** | Conformance suite false positives (suites green while logically diverging) | 90-day false-positive rate; SC-2 YAML fixtures intended as primary mitigation | >5% false-positive rate triggers fixture-coverage review. |

### 6.3 Out-of-scope failure modes

| Failure | Why excluded |
|---|---|
| Multi-tenant interference | A1 — single tenant per wrapper instance. Not a v1 concern. |
| Windows-specific path quirks | A6 — Linux + macOS are gating; Windows tracked best-effort. |
| Burst-mode race conditions | D10 — burst removed from v1. Re-enters scope only if `'burst'` is added later. |
| Cross-language semantic divergence in display event payloads beyond schema | SC-2 YAML fixtures cover observable behavior. Anything not in a fixture is undefined cross-language. |

---

## 7. Tradeoffs

Eight-dimension assessment of the final design (Refined Hybrid, post one-shot pivot) against the two reference points considered during selection.

| Dimension | Candidate A (Simplest) | **This design (final)** | Candidate C (Most Robust) |
|---|---|---|---|
| **Latency** (time-to-adapter-readiness) | Good — fewest artifacts | **Good** — one-shot pivot deleted cancel-routing, lost-state, and burst framing. Net engine LOC change went from +400 to −200. Weeks-scale. | Poor — markdown-spec-first, gherkin + goldens, mandatory hook verification. Months-leaning. |
| **Complexity** (concepts adapter author learns) | Good (~7) | **Good** (~8) — `lifecycle` is a single value; cancel is `dispose()`; no lost-state. Pivot removed 2 concepts. | Poor (~13) — gherkin, lifecycle notifications, structured propagate, `allowProtocolSkew` escape hatch. |
| **Reliability** (production failure visibility + recovery) | Poor | **Good** — typed `AaaError` hierarchy, strict-refuse skew with self-remediating message, install-time priming kills first-run cliff. Mid-engine death is a typed iterator exception. | Good — marginally more state-transition observability (`session/burst-*` notifications). |
| **Cost** (artifacts to author + maintain) | Good | **Good** — Python types + generator + handwritten dual + YAML fixtures. One source, two generated targets, one test corpus. Pivot deleted the cancel + lost-state implementations. | Poor — markdown spec authoring as first-class discipline, golden frame fixtures, gherkin scenarios. |
| **Security** | Mixed | **Good** — strict-refuse on version skew, `env.allowlist` on subprocess env, no transparent respawn (no resurrection of a compromised state). | Good — same posture. |
| **Scalability** (add third language later, e.g. Go) | Poor — Python types are the only source | **Adequate** — JSON Schemas exist (generated), so a Go wrapper can codegen from them. Spec.md is human-readable reference. | Adequate — same artifact surface. |
| **Reversibility** | Good | **Good** — `'burst'` is reserved as a wire enum value (rejects in v1, additive in v1.x). Cancel can be added back as `turn/cancel` later if SIGTERM proves insufficient. Wire is versioned. | Mixed — strict-refuse skew is harder to soften without breaking deployed wrappers. |
| **Org fit** (single team, weeks) | Good | **Good** — handwritten dual suites are authored by the same person; codegen is one script; install-time prepare is one new verb. | Mixed — gherkin + goldens + structured propagate demand authoring discipline beyond a single-team weeks-scale ship. |

The final design lives at the "good enough on both critical axes (reliability + latency)" corner. It doesn't dominate either axis; it doesn't lose badly on either. The one-shot pivot, grounded in empirical PC + NC surveys, was the lever that moved the design from "adequate / adequate" to "good / good" on cost and complexity without sacrificing reliability.

---

## 8. Recommended design

### 8.1 The ten locked decisions

| # | Surface | Decision | Rationale |
|---|---|---|---|
| **D1** | Wire spec source of truth | **Python TypedDicts authoritative.** `protocol/_gen.py` generates `spec.md` + `schemas/*.json`. Generated files banner "DO NOT HAND-EDIT"; CI blocks PRs that edit without regenerating. | One source = no drift by construction. Python types already exist; promotes them to canonical. Avoids the "people edit the spec, codegen blows it away" anti-pattern. |
| **D2** | Sequencing | **Gaps-first.** Fix engine gaps in order a → b → e → c → d. Snapshot resulting Python types as wire v0. Then build wrappers + conformance against the snapshot. | Codifies engine reality, not engine aspiration. Wire describes what the engine actually does. (Appendix C is the detailed sequence.) |
| **D3** | Cancel | **SIGTERM the subprocess.** `turn/cancel` removed from wire. `SessionHandle.cancel()` and `dispose()` both send SIGTERM (5s grace, then SIGKILL). | One-shot per turn makes cancel === kill. ~150 LOC of routing infrastructure (CR-4) deleted instead of fixed. |
| **D4** | Mid-engine death | **Subprocess exit = `SessionHandle` done.** No lost-state machinery. Iterator raises `AaaError(code='engine_crashed', exitCode, stderrTail)`. Adapter re-spawns if it wants to. | One-shot is cheap to re-spawn. Lost-state would be ~200 LOC of synchronization for a state that's intentionally not retained. |
| **D5** | Binary discovery | **PATH first, then `AMPLIFIER_AGENT_BIN` env var.** `handle.getEngineInfo()` exposes the resolved binary path. No `binPath` constructor param. | Unix convention. Env var handles non-PATH installs. Exposing the resolved path makes debugging painless without expanding the API surface. |
| **D6** | Version skew | **Strict-refuse.** Wrapper compares its `PROTOCOL_VERSION` against engine's at `agent/initialize`. Mismatch → typed `AaaError(code='protocol_version_mismatch')` with self-remediating message: exact reinstall commands for both wrapper and engine, plus the `--allow-protocol-skew` flag and what it does. Override available as `spawnAgent({allowProtocolSkew: true, ...})` or the `--allow-protocol-skew` engine flag. | Silent skew = the worst class of bug. Loud refusal + self-remediating error is the operator-friendly posture. R3 monitors the flip side. |
| **D7** | Conformance | **Handwritten dual + shared YAML wire-sequence fixtures.** TS + Py test files with identical scenario filenames; both load the same YAML fixtures; filename + scenario-name parity lint blocks divergence. Five critical contracts covered (§4.6). | SC-2 from critic. Addresses H6 (parallel suites diverging silently) without the cost of full gherkin/spec-as-law. |
| **D8** | First-run UX | **Install-time `prepare`.** New CLI verb runs full bundle resolution + module install + cache warm. `doctor` reports primed state; doesn't itself prime. Typed `engine_not_primed` error if wrapper is invoked before prepare. | Kills the first-run cliff (F13a) without forcing adapter authors to communicate a separate "run this first" step to their users. |
| **D9** | Sub-agent events | **Leak-by-default, host opt-out.** `display.subagentEvents: 'all' \| 'none'` (default `'all'`). `parentTurnId` lineage in payload. Negotiated via capability flag on `agent/initialize`. | Per user direction. Maximum observability by default; hosts that don't want the noise can suppress. |
| **D10** | Lifecycle | **§4 amended.** `lifecycle: 'one-shot'` only in v1. `'burst'` reserved as a wire enum value but rejected at runtime with `AaaError(code='lifecycle_unsupported', requested: 'burst', supported: ['one-shot'])`. | Empirically grounded: PC + NC surveys show no host needs in-process burst at the AaA boundary. Adding `'burst'` later is a minor-version-additive change. |

### 8.2 The locked public API

```typescript
// amplifier-agent-client-ts — public surface

import { spawnAgent, SessionHandle, DisplayEvent, ApprovalRequest, ApprovalResponse, AaaError } from 'amplifier-agent-client-ts';

async function spawnAgent(params: {
  lifecycle: 'one-shot';                       // 'burst' reserved; throws AaaError(lifecycle_unsupported)
  sessionId: string;
  resume?: boolean;
  cwd?: string;
  env?: { allowlist: string[]; extra?: Record<string, string> };
  providerOverride?: string;
  approval?: {
    onRequest: (req: ApprovalRequest) => Promise<ApprovalResponse>;
    timeoutMs: number;
  };
  display?: {
    onEvent?: (event: DisplayEvent) => void;   // pull via submit() iterator OR push via callback
    subagentEvents?: 'all' | 'none';           // default 'all'
  };
  allowProtocolSkew?: boolean;                 // default false; opt out of D6 strict-refuse
}): Promise<SessionHandle>;

interface SessionHandle {
  submit(prompt: string): AsyncIterable<DisplayEvent>;  // single turn; iterator ends on result/final
  cancel(): Promise<void>;                              // SIGTERM the subprocess (D3)
  dispose(): Promise<void>;                             // graceful shutdown; SIGTERM if needed
  getEngineInfo(): {                                    // resolved metadata; D5
    binaryPath: string;
    protocolVersion: string;
    engineVersion: string;
    bundleDigest: string;
  };
}

interface DisplayEvent {
  type: 'turn/started' | 'tool/started' | 'tool/completed' | 'assistant/text'
      | 'turn/completed' | 'subagent/started' | 'subagent/completed'
      | 'error/recoverable' | 'provider/throttled';
  sessionId: string;
  turnId: string;
  parentTurnId?: string;          // present on subagent/* events
  synthesized?: boolean;          // true if wrapper-synthesized via L14 path
  payload: Record<string, unknown>;
}

class AaaError extends Error {
  code: 'engine_not_primed' | 'protocol_version_mismatch' | 'engine_crashed'
      | 'approval_timeout' | 'lifecycle_unsupported' | 'cancelled'
      | 'binary_not_found' | 'initialize_failed' | string;
  remediation?: string;
  details?: Record<string, unknown>;
}
```

Python wrapper surface is symmetric — `spawn_agent(...)` returns `SessionHandle`; `SessionHandle.submit(prompt)` returns `AsyncIterator[DisplayEvent]`; `AaaError` is a dataclass exception with the same code enum. Conformance suite enforces scenario-level behavioral symmetry.

---

## 9. Simplest credible alternative

**Candidate D — "Defer everything except wrappers."** Ship the two wrappers against the current Python TypedDicts as-is. Do not fix engine gaps. Do not write a generated spec. Do not write a conformance suite. Trust that the engine and wrappers will agree because Manoj authored all three.

**Why rejected:**

1. **Critic finding CR-1 makes this design DOA.** Without fixing gap (d), production today emits zero display events. Wrappers would ship a `submit() → AsyncIterable<DisplayEvent>` API that, in production, yields only `result/final` (via the L14 synthesis path). The headline feature of the wrapper would be theoretical. Adapter authors building NC + PC would discover this within days and lose trust in the boundary.

2. **No conformance suite means cross-language divergence is invisible.** TS and Py wrappers would drift the moment they're authored. The "wrappers are siblings with identical shape" property (§4 of the design checkpoint) is unverifiable. Adding a third language later would require re-authoring against unknown reality.

3. **Engine gaps (a), (b), (c) are bugs that the wrappers would have to work around silently.** Init params dropped (c) means `resume` doesn't work; the wrapper can't compensate. Signature mismatches (a, b) mean future engine refactors will break the wrappers without warning.

4. **Saves 1–2 weeks; costs months when downstream adapters land.** The boundary failures would surface at NC + PC integration time, with two consumers already in flight and harder to diagnose than during wrapper authoring.

Candidate D is the right shape if the goal were a throwaway proof-of-concept. It is the wrong shape for a published `amplifier-agent` Layer 3 that NC + PC depend on.

---

## 10. Migration and rollout plan

### 10.1 Phase 2.0c — engine gap fixes + streaming hook

**Prerequisite investigation (must run before authoring):**

- **SC-1:** Walk foundation kernel event-log emission. Confirm `tool/started` is a discrete event distinct from `tool/completed`. If not, revise the minimum-set (§4.4) before §10.3 begins.
- **A7 verification:** End-to-end resume test against current bundle. Confirm `context-simple` replays transcript on `is_resumed=True`. If not, swap context module (revisits the 2026-05-19 bundle decision; coordinate with bundle owner).

**Gap fix order (Appendix C):**

1. (a) Reconcile `DisplaySystem.emit` signature → async + single `DisplayEvent` arg.
2. (b) Reconcile `ApprovalSystem.request` signature.
3. (e) Remove `turn/cancel` from wire; delete routing infrastructure (~150 LOC).
4. (c) Widen `_EngineProtocol.initialize()` and thread params in both `single_turn.py` and `stdio_loop.py`; promote `_StdioEngine` to a named module.
5. (d) Add `src/amplifier_agent_lib/bundle/hooks/streaming_emitter.py`; thread `ctx.display.emit` / `ctx.approval.request` callbacks into kernel hook bridge via `_runtime.py`.

**Inline fixes:** L14 `sessionId` field, version-check on `agent/initialize`, `_StdioEngine` promotion.

**Gate:** `verify --check-hooks` exits 0 against minimum-set 5 events on a recorded fixture session. Snapshot Python TypedDicts as wire v0; `PROTOCOL_VERSION = "2026-05-aaa-v0"`.

### 10.2 Phase 2.0c.1 — `prepare` verb + install-time priming

Author `amplifier-agent prepare`. Wire into npm postinstall, brew formula, `uv tool install` post-hook. Add typed `engine_not_primed` error to engine. Update `doctor` to report primed state without itself priming.

### 10.3 Phase 2.1 — wire spec hardening

- Author `protocol/_gen.py`.
- Generate `protocol/spec.md` and `protocol/schemas/*.json` from the snapshot.
- Author shared YAML fixtures under `protocol/fixtures/` for the five critical contracts (§4.6).
- CI: spec-regeneration check, schema validity check.

### 10.4 Phase 2.2 — TypeScript wrapper

Author `wrappers/typescript/` package. Implement `transport.ts`, `jsonrpc-client.ts`, `session-handle.ts`, `errors.ts`, `version.ts`. Wire to npm packaging (dual ESM/CJS, no native deps, Node 18+).

### 10.5 Phase 2.3 — Python wrapper

Author `wrappers/python/` package. Symmetric to TS. PyPI packaging, Python 3.11+, `mypy --strict`.

### 10.6 Phase 2.3.1 — conformance suite

Author handwritten dual scenarios under `conformance/scenarios/`. Wire scenario-filename and scenario-name parity lints. Both suites consume the shared YAML fixtures. CI gate: both green on Linux + macOS, parity lint zero violations.

### 10.7 Phase 2.4 / 2.5 — downstream adapters (out of this design, consumers of it)

- **NanoClaw:** author an adapter using `amplifier-agent-client-ts` that fits the in-container `AgentProvider` shape. One-shot per query maps directly. `push()` is not used (no in-process burst).
- **Paperclip:** author a `ServerAdapterModule` using `amplifier-agent-client-ts` whose `execute(ctx)` spawns AaA per call. Approval via PC's `wakeReason: 'approval_callback'` indirection (inter-heartbeat); the wrapper's `approval.onRequest` callback returns a sentinel that PC translates into its own approval flow.

### 10.8 Design checkpoint amendments

The design checkpoint `docs/status/amplifier-as-agent-design-checkpoint.md` is amended as follows. Full amendment text in Appendix A; factual corrections from empirical surveys in Appendix B.

- **§4 (Wrapper public API):** `lifecycle: 'one-shot' | 'burst'` → `lifecycle: 'one-shot'` in v1, with `'burst'` reserved.
- **§4 (Wire methods):** `turn/cancel` removed from the listed methods.
- **§6 (Streaming hook):** new sub-section locking the bundle-level streaming emitter.
- **§9 (Execution plan):** Phase 2.0c added (engine gap fixes + streaming hook + prepare verb) before Phase 2.1 (wire spec hardening).

### 10.9 Downstream notification

When wire v0 is snapshotted, notify NC and PC implementation owners with: protocol version string, generated `spec.md` link, generated schemas link, sample fixture, and the locked TypeScript public API. Provide adapter-author runbook covering: install-time `prepare`, `engine_not_primed` handling, version-skew remediation, and approval-callback wiring.

---

## 11. Success metrics

| Metric | Target | Gate / Cadence |
|---|---|---|
| **Wire conformance** | TS + Py suites green; YAML fixture parity 100%; CI parity lint zero violations | Block every PR; CI on Linux + macOS |
| **L14 synthesis** | Both branches (engine emits / engine omits) tested in YAML fixture; `synthesized: true` marker visible in observable cases | Block any wrapper PR that doesn't pass both |
| **Concurrency design-out** | Zero `this.active`-style or per-session-race bug reports in first 90 days post-ship | Track via bug tags; escalate if any recur |
| **Hook coverage** | `verify --check-hooks` exits 0 against minimum-set 5 events; runs in CI on every PR | Hard gate before Phase 2.1 |
| **Cold-start, primed steady-state** | p95 < 500ms; aspirational < 200ms; measured on Linux x86 + macOS Apple Silicon | Phase 2.0e gate; >2s p95 triggers `'burst'` reconsideration |
| **Cold-start, first run** | ≤ 30s p95 (manifest resolution + module clone + bundle prepare) | Track; goal is to remove from runtime path via install-time `prepare` |
| **First-run cliff (install-time)** | `amplifier-agent prepare` succeeds ≥ 99% in turnkey installer | Failure rate >1% triggers installer review |
| **Version skew** | < 1 strict-refuse skew error per week in production logs | Operator log scan; >1/week reconsiders strict-refuse policy |
| **Cross-platform** | Linux + macOS green every PR; Windows tracked best-effort, non-gating | CI matrix |
| **Adapter onboarding** | NC + PC adapters authored end-to-end in ≤ 5 working days each, without spec questions to L3 team | Time-to-first-green NC/PC integration test |
| **Conformance suite stability** | Conformance suite false-positive rate < 5% over 90 days | Flakiness tracking |
| **Regression-detector for designed-out failure modes** | Zero recurrence of F5, F7, F11, F13a, F15, NC-L14, NC-L16, CR-4 in production | Separate tag; any recurrence triggers design-failure investigation (not a quick patch) |

---

## Appendix A — §4 amendment to design checkpoint

The following replaces the `lifecycle` and wire-method passages in `docs/status/amplifier-as-agent-design-checkpoint.md` §4.

> **Lifecycle (v1):** `lifecycle: 'one-shot'` is the only supported value in wire v0 (`2026-05-aaa-v0`). The wire enum reserves `'burst'` as a future value; the v1 engine rejects it at `agent/initialize` with `AaaError(code='lifecycle_unsupported', requested: 'burst', supported: ['one-shot'])`. Adding `'burst'` support in a later version is a minor-version-additive change requiring no breaking change to the public API.
>
> Empirical grounding: Paperclip's `codex-local` adapter (`packages/adapters/codex-local/src/server/execute.ts:710`) and `claude-local` adapter (`packages/adapters/claude-local/src/server/execute.ts:739`) both spawn fresh subprocesses per `execute(ctx)` call. NanoClaw's `add-codex` skill (`add-codex/SKILL.md:139`) explicitly documents "no long-lived daemon to keep healthy across sessions" as the rationale for spawn-per-query. The Claude SDK provider in NanoClaw holds the `MessageStream` open across `push()` only because the SDK API requires it; that lives inside the container's agent-runner, not at the host boundary where AaA sits.
>
> **Wire methods (client → engine):** `agent/initialize`, `agent/shutdown`, `turn/submit`. **Removed:** `turn/cancel`. Cancel is performed by the wrapper sending SIGTERM to the engine subprocess (5s grace, then SIGKILL). One-shot lifecycle makes this equivalent in effect to a per-turn cancel without the routing complexity (~150 LOC of dispatch infrastructure deleted).
>
> **`handle.getEngineInfo()` (new):** returns resolved metadata `{binaryPath, protocolVersion, engineVersion, bundleDigest}`. Binary discovery is `PATH` first, then `AMPLIFIER_AGENT_BIN` env var. No `binPath` constructor param.
>
> **First-run UX:** new `amplifier-agent prepare` verb runs at install time (npm postinstall / brew formula / `uv tool install` post-hook) to populate the bundle cache. `doctor` reports primed state but does not itself prime. Engine returns typed `engine_not_primed` error if invoked against an unprimed cache.

---

## Appendix B — Design checkpoint factual corrections

Empirical surveys of `/Users/mpaidiparthy/repos/AaA/paperclip` and `/Users/mpaidiparthy/repos/AaA/nanoclaw` (2026-05-20) surfaced four factual corrections to the design checkpoint. Logged here for the record; not material to the locked design.

1. **`idleHeartbeat: true` does not exist in NanoClaw.** The design checkpoint describes NC as a `query()` + `idleHeartbeat: true` + multi-`push()` host. In reality, NC's host has no provider abstraction at all — the host manages Docker containers (warm up to 30 minutes). The `AgentProvider` interface (which does have `push()`) lives inside each container's agent-runner at `container/agent-runner/src/providers/types.ts`. The "burst-like" behavior the checkpoint attributed to NC at the AaA boundary is actually the container lifetime, not an in-process subprocess pattern.

2. **NanoClaw's V1 `amplifier_local` provider does not exist.** Zero references across the entire NC repo — no code, no docs, no archived history. May never have existed; checkpoint citation appears to have been from speculative integration sketches.

3. **NanoClaw's `cachedClient` does not exist.** Zero occurrences in the NC repo. The `this.active` race referenced in checkpoint failure mode NC-L14 was a real concern from the V1 design notes but was never implemented in code that shipped.

4. **Codex in NanoClaw is spawn-per-query, not a burst daemon.** The checkpoint correctly identified `codex app-server --listen stdio://` as the daemon-style invocation. In the actual NC `add-codex` skill, the lifecycle decision was made the other way: "no long-lived daemon to keep healthy across sessions" (`add-codex/SKILL.md:139`). Spawn-per-query is the production pattern.

Net implication for this design: NC at the host level is one-shot, same as PC. The one-shot pivot (D10) is grounded in both hosts' actual implementations, not just PC's.

---

## Appendix C — Engine gap fix sequencing

The eight-step engine fix sequence for Phase 2.0c. Steps 1–5 are the named gaps; steps 6–8 are inline fixes from the adversarial review.

| # | Step | Files touched | Approx LOC | Blocks |
|---|---|---|---|---|
| 1 | **Gap (a):** reconcile `DisplaySystem.emit` signature to async + single `DisplayEvent` arg | `src/amplifier_agent_lib/protocol_points/base.py`, `defaults_cli.py`, `defaults_stdio.py` | +30 / −15 | Blocks step 5 |
| 2 | **Gap (b):** reconcile `ApprovalSystem.request` signature, same shape as (a) | `protocol_points/base.py`, `defaults_cli.py`, `defaults_stdio.py` | +20 / −10 | Blocks step 5 |
| 3 | **Gap (e):** remove `turn/cancel` from wire | `protocol/methods.py`, `jsonrpc.py`, `_runtime.py`, `modes/stdio_loop.py` | +0 / −150 | Independent |
| 4 | **Gap (c):** widen `_EngineProtocol.initialize()`; thread `sessionId` / `resume` / `cwd` / `providerOverride` through both modes; promote `_StdioEngine` to named module | `src/amplifier_agent_cli/engines/stdio_engine.py` (NEW), `modes/single_turn.py`, `modes/stdio_loop.py`, `_runtime.py` | +120 / −60 | Independent |
| 5 | **Gap (d):** add streaming emitter hook in vendored bundle; bridge `ctx.display.emit` / `ctx.approval.request` into kernel hook surface in `_runtime.py` | `src/amplifier_agent_lib/bundle/hooks/streaming_emitter.py` (NEW), `bundle/bundle.md`, `_runtime.py` | +200 / −20 | Gates §11 hook-coverage metric |
| 6 | Inline: add `sessionId` to L14 synthesized notifications | `modes/stdio_loop.py` | +5 / −0 | Independent |
| 7 | Inline: engine actually checks `PROTOCOL_VERSION` against client on `agent/initialize`; emit typed `protocol_version_mismatch` if differing | `engine.py`, `protocol/errors.py`, `modes/stdio_loop.py`, `modes/single_turn.py` | +40 / −5 | Independent |
| 8 | New verb: `amplifier-agent prepare`; `doctor` reports primed state; `engine_not_primed` typed error | `src/amplifier_agent_cli/verbs/prepare.py` (NEW), `verbs/doctor.py`, `bundle/cache.py`, `protocol/errors.py` | +180 / −10 | Independent |

**Net engine LOC change:** approximately **+595 / −270 = +325 net** at the engine layer. (The one-shot pivot took a pre-pivot estimate of +400 net down by deleting the cancel-routing and lost-state machinery that step 3 represents.)

**Phase 2.0c gate:** all eight steps green, `verify --check-hooks` exits 0 against minimum-set 5 events, `prepare` succeeds on a clean machine. Snapshot Python TypedDicts as wire v0 (`PROTOCOL_VERSION = "2026-05-aaa-v0"`). Proceed to Phase 2.1.

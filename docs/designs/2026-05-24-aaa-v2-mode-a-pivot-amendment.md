# AaA v2 — Mode A Pivot Amendment to the 2026-05-22 NC Provider Design

| Field | Value |
|---|---|
| **Status** | DRAFT — pending review |
| **Author** | Manoj Prabhakar Paidiparthy (implementation lead) |
| **Reviewer (primary)** | Brian Krabach |
| **Date drafted** | 2026-05-24 |
| **Amends** | `docs/designs/2026-05-22-aaa-v2-amplifier-agent-nc-provider.md` (re-litigates D1, D3, D4, D6, D9, D12 of §8; preserves D2, D5, D7, D8, D10, D11 unchanged) |
| **Builds on** | `docs/designs/2026-05-20-aaa-v2-wrapper-and-wire.md` (the locked wire+wrapper this amendment narrows) |
| **Empirical trigger** | DTU instance `aaa-nc-verify-v2` (host `mpaidiparthy@MacBook-Pro`, 2026-05-23). Confirmed: (a) `amplifier-agent run --session-id X [--fresh\|--resume] "<prompt>"` works end-to-end against the real Anthropic API and yields `{reply, turnId, sessionId}` JSON; (b) resume continuity verified across two invocations ("purple" recall); (c) `amplifier-agent run --stdio` returns `Error: No such option '--stdio'` — Mode B was never implemented and the wrapper's `spawnAgent` (`wrappers/typescript/src/index.ts:170-173`) hangs trying to drive it. |
| **Audience** | NC team (in-container adapter implementers); L3 team (this repo's contributors); downstream host-adapter authors (Paperclip, OpenCode, Claude Code) |

---

## Executive summary

The 2026-05-22 NC provider design locked a wire surface assuming Mode B (stdio JSON-RPC over NDJSON with bidirectional `display/event` notifications and mid-turn `approval/request`/`approval/response` round-trips) would be the per-turn delivery vehicle. Empirical verification in DTU `aaa-nc-verify-v2` surfaced two facts that re-open six of the twelve locked decisions:

1. **Mode B is unimplemented.** `src/amplifier_agent_cli/__init__.py` acknowledges Mode B as "stubbed; full implementation in Phase 3"; the `run` subcommand (`src/amplifier_agent_cli/modes/single_turn.py`) does not recognize `--stdio`. The pre-existing plan `docs/plans/2026-05-18-aaa-v2-phase-3-cli-mode-b.md` was authored but never executed; the three Phase 1/2/3 plans authored alongside the 2026-05-22 design focus on engine internals, the wrapper, the bundle, and the NC adapter — none implements the engine's stdio JSON-RPC dispatcher.
2. **NC does not consume the wire surface Mode B was designed to carry.** Audit of NC's `AgentProvider` consumer paths (`container/agent-runner/src/poll-loop.ts`) shows it uses only four `ProviderEvent` shapes (`init` for `sessionId`, `activity` for stuck-detection heartbeat at `poll-loop.ts:359-361`, `result` for channel delivery via `dispatchResultText` at `poll-loop.ts:431-471`, and `error`). Mid-turn `display/event` streaming (assistant text deltas, granular `tool/started`/`tool/completed`) is **not surfaced**; NC delivers the final result only. Mid-turn `approval/request`/`approval/response` is **not invoked**; NC auto-allows per A10 (Claude `bypassPermissions` parity). Sub-agent progress was already deferred to v1.x (SC-5).

The locked design over-fit to a heavier wire pattern than the four queued v1 hosts (NC, Paperclip, OpenCode, Claude Code — each of which operates one-shot-per-turn at the host boundary per the 2026-05-20 design Appendix A) actually warrant. Implementing Mode B as specced would be ~1-2 weeks of new engine + wrapper code for a capability NC does not exercise. Mode A — argv in, structured JSON out — already exists, works against the real provider in DTU, and delivers everything NC's adapter needs in ~2-3 days of glue.

This amendment narrows the v1 wire to **one delivery mode: Mode A**. The engine becomes a per-turn subprocess that takes session config as argv and emits a single structured JSON envelope on stdout. The wrapper becomes a thin subprocess driver that spawns the engine per `submit()`, synthesizes activity heartbeats while it runs, parses the JSON envelope when it exits, and yields the equivalent `DisplayEvent`s. **Six of the twelve 2026-05-22 locked decisions** (D1 `agent/initialize`-as-JSON-RPC, D3 `display/event` streaming, D4 `approval/request` bidirectional round-trip, D6 protocol-skew-via-handshake, D9 `mcpServers`-as-initialize-field, D12 `host.capabilities`-as-initialize-field) are re-litigated: each shifts from a JSON-RPC mechanism to an argv flag while preserving the underlying intent (closing the same v1 problem). **The other six** (D2 adapter scope, D5 chain vocabulary, D7 session-state placement, D8 `AaaError` taxonomy, D10 binary install, D11 `prepare` placement) are **unchanged**. The four-host runway analysis is **unchanged** — capability negotiation remains the multi-host carrier; its transport just becomes `--host-capabilities '<json>'` instead of a JSON-RPC field.

What this amendment costs the design: mid-turn `display/event` streaming and mid-turn approval round-trips become deferred capabilities rather than locked v1 features. Both were already not consumed by any v1 host; nominees for v1.x revival are listed in §6. Per-turn MCP server respawn becomes a real (small) latency tax — accepted with monitoring at the same R-level disposition as the 2026-05-22 spawn-cost risk.

What this amendment gains: ~2-3 days to a working NC integration instead of ~1-2 weeks of new wire+engine code; ~600-800 LOC deleted from the engine (the stdio JSON-RPC dispatcher, event emitter, and approval request channel that no v1 host needs); a wire that exactly matches the Claude Code analogy (`container/agent-runner/src/providers/claude.ts` invokes the Claude SDK in-process per turn; amplifier-agent's analogous "per-turn subprocess with argv config and JSON output" is the same shape transposed onto a separate Python process); and a v1 shipping pace that gets the four-host runway moving.

The amendment preserves all four CR closures (CR-1 session persistence via context-simple + SessionStore + IncrementalSaveHook; CR-2 typed approval error contract; CR-3 stderrTail redaction; CR-4 visible buffer drop). It preserves all eight SC closures (SC-1 init-before-activity ordering, SC-3 BLOCKED_ENV_KEYS, SC-7 async probeEngineVersion all transpose cleanly to the new wire). It preserves the v1.x deferral table with three additions (D-v1.x-13, D-v1.x-14, D-v1.x-15 covering the capabilities Mode B would have carried).

End-to-end effort delta: critical path drops from **~31 working days / ~8 weeks** (2026-05-22 §10.4) to **~10-12 working days / ~3 weeks** for the engine + wrapper + NC adapter sequence. The reduction is concentrated in the engine layer (no stdio JSON-RPC dispatcher) and the wrapper layer (no event-stream consumer; no JsonRpcClient).

---

## §1 Problem framing  <a id="s1-problem"></a>

### 1.1 Why the locked design is being amended

The 2026-05-22 locked design's §1.4 named two facts the design pass had surfaced — NC's `poll-loop.ts` calls `push()` (closed by D3 / B1 buffer chaining at the adapter), and the locked wire had no `mcpServers` field (closed by D9 additive field on `agent/initialize`). DTU verification of the locked design surfaced a **third fact** that re-opens both closures and four others:

> The locked design specifies Mode B as the wire's delivery vehicle. Mode B does not exist in the engine. None of the four queued v1 hosts consume the bidirectional event streaming Mode B was designed to carry.

The locked design's §1 implicitly assumed that the wire surface described in `docs/designs/2026-05-20-aaa-v2-wrapper-and-wire.md` §4.1 — JSON-RPC 2.0 over NDJSON over stdio, with server-initiated `approval/request` notifications and a `display/event` notification stream — was either already implemented or in active flight. The 2026-05-20 design's §10.1 in fact called out Phase 2.0c as "engine gap fixes + streaming hook" as a Phase 2.0c prerequisite, with steps 5 ("Gap (d): add streaming emitter hook in vendored bundle; bridge `ctx.display.emit` / `ctx.approval.request` callbacks into the kernel hook bridge") and 3 ("Gap (e): remove `turn/cancel` from wire; delete routing infrastructure") explicitly enumerated as engine work.

What actually shipped between 2026-05-20 and 2026-05-22:
- The wire types (`wrappers/typescript/src/types.ts`, `wrappers/python/src/types.py`) were generated from Python TypedDicts per D1.
- The TypeScript wrapper (`wrappers/typescript/src/spawn.ts`, `index.ts`, `session.ts`, `jsonrpc.ts`, `transport.ts`) was authored and ships the locked `spawnAgent`/`SessionHandle` surface.
- The engine `_runtime.py` was threaded for CR-1 (session persistence via `SessionStore`/`IncrementalSaveHook`).
- The Mode A `run` subcommand (`src/amplifier_agent_cli/modes/single_turn.py`) was authored and works end-to-end against the real provider.

What did **not** ship:
- The engine's stdio JSON-RPC dispatcher (`amplifier-agent run --stdio`). `src/amplifier_agent_cli/__init__.py` documents the gap verbatim: *"Mode B (stdio JSON-RPC) — stubbed; full implementation in Phase 3."*
- The bundle-level `streaming_emitter.py` hook (2026-05-20 §4.5).
- The `WireApprovalProvider` shim's wire round-trip (2026-05-22 §4.7) was authored as a Python class but its `wire_send_request` is unreachable without Mode B's dispatcher.

### 1.2 What NC actually consumes from the wire

Audit of NC's `AgentProvider` consumer surface (`container/agent-runner/src/poll-loop.ts`, the relevant `ProviderEvent` consumers):

| Wire feature in locked design | NC's consumer path | Used? |
|---|---|---|
| `agent/initialize` with `sessionId`, `resume`, `mcpServers`, `host.capabilities` | NC's adapter `makeEvents()` (2026-05-22 §4.1.4) constructs the call; uses returned `sessionId` for `continuation` | **Yes** — but only the sessionId and the act of passing the config matters; can be argv flags |
| `turn/submit` with `prompt` | Yields events from the resulting iterator | **Yes** — already a CLI positional argument in Mode A |
| `display/event` notifications (`tool/started`, `tool/completed`) | Translates to `{type:'activity'}` only (2026-05-22 §4.2 translator table) | Used only as activity heartbeat source for stuck-detection at `poll-loop.ts:359-361` |
| `display/event` notifications (`assistant/text` chunks) | **Not surfaced**. NC delivers `result.text` (final) only via `dispatchResultText` at `poll-loop.ts:431-471` | **No** |
| `display/event` notifications (`subagent/started`, `subagent/completed`) | Already collapsed to `{type:'activity'}` per SC-5 (deferred to v1.x) | **No** |
| `approval/request` ↔ `approval/response` mid-turn round-trip | NC's adapter sets `onRequest: () => ({decision: 'allow'})` (2026-05-22 §4.1.4) — auto-allow per A10 | **No** |
| `result/final` | NC's `result` event consumes `text` for channel delivery | **Yes** — but a single JSON envelope at subprocess exit carries the same payload |
| `AaaError` with `severity`, `correlationId`, `classification`, `stderrTail` | NC's `event-translator.ts` maps to `ProviderEvent.error` | **Yes** — but the error can be a field in the JSON envelope or a non-zero exit + JSON error |

The wire features NC actually consumes are: **sessionId** (echoed back), **resume** (the act of passing it), the **final assistant text**, an **activity heartbeat** synthesized by the adapter every 2s, and **structured errors at turn-end**. Everything else is either unused, ignored, or auto-handled.

### 1.3 The Claude Code analogy (and why it grounds Mode A)

NC's existing in-container provider for Claude (`container/agent-runner/src/providers/claude.ts`) uses Claude SDK **in-process**. The SDK's streaming, tool round-trips, and approval prompts all happen via in-process JavaScript callbacks — no subprocess boundary to bridge. NC just sees the SDK's `MessageStream` and yields `ProviderEvent`s into its async iterable.

For amplifier-agent — which is a separate Python process from Node's agent-runner, not an in-process SDK — the **analogous** pattern is: spawn `amplifier-agent run` per turn, pass session config as argv (the host owns the config; the engine has no implicit state besides what's in the session-store volume), get a structured JSON envelope on stdout, exit. Each turn = one subprocess. No long-lived process. No stdio JSON-RPC. No bidirectional channel.

This is exactly the pattern Claude Code's own CLI uses for its `--resume <session_id>` flow (PC's `claude-local` adapter in `packages/adapters/claude-local/src/server/execute.ts:739`, referenced verbatim in 2026-05-20 design §10.7): subprocess per turn, opaque session id, transcript replay on re-spawn. The 2026-05-20 design §1 stated this empirical finding explicitly:

> Both Codex and Claude Code adapters in Paperclip spawn fresh subprocesses per `execute(ctx)`. NanoClaw's `add-codex` skill is explicit about preferring spawn-per-query over a long-lived daemon. […] No host on Brian's roadmap needs the engine to hold state across `submit()` calls.

This finding underpinned the locked D10 (lifecycle one-shot). The amendment extends the same finding to its natural conclusion: **if no host needs the engine to hold state across `submit()` calls, no host needs the engine to hold a stdio JSON-RPC channel open during a turn either**. The bidirectional wire was machinery to support an interaction model no v1 host wants.

### 1.4 What the amendment is not

- It is **not** a re-design of the four-host runway. Capability negotiation remains the load-bearing infrastructure for adding Paperclip, OpenCode, Claude Code; only its transport changes (argv `--host-capabilities` instead of JSON-RPC `initialize.host.capabilities` field).
- It is **not** a re-design of the engine's internal layer. CR-1 session persistence, the `WireApprovalProvider` shim (with mechanism shift), `tool-mcp.mount(config={...})` threading, and `hooks-approval` bundle mount all carry forward.
- It is **not** a re-design of the adapter's public API surface. NC's `AgentProvider`/`AgentQuery`/`ProviderEvent` contract (locked design §1.2) is unchanged. The wrapper's `SpawnAgentParams` and `SessionHandle` entry points are preserved. The `DisplayEvent` type emitted by `SessionHandle.submit()` is **simplified** to match what NC's existing `ProviderEvent` consumer expects, removing fields (`turnId`, `parentTurnId`, `synthesized`, `payload`) that the Mode A wire cannot meaningfully populate. See §5.2 for the new shape. This is a breaking change to the type and requires updating `wrappers/typescript/src/session.ts:26-37` in A3'. The `AaaError` taxonomy (locked design §8.14) is preserved verbatim — only the **transport** that carries it changes.
- It is **not** an argument that Mode B is wrong as a future capability. It is an argument that Mode B is wrong as a **v1** capability because no v1 host needs it, and committing to it now would lock the engine into a heavier shape than reality warrants.

### 1.5 Out of scope for this amendment

- Implementation plan details (test scaffolding, commit sequencing, fixture authoring). The plan is a separate artifact authored after this amendment is locked.
- Re-running the 8-dimension tradeoff matrix from 2026-05-22 §7. The dominant tradeoff ("the wire is the irreversible commitment; everything above it is per-host implementation choice") still applies — the amendment narrows what the wire commits to, not which dimensions matter.
- Re-litigating the rejection of Candidate A from 2026-05-22 §9. The four-host runway lock is unchanged; capability negotiation is preserved (mechanism shift only).
- Bundle composition changes. The bundle still mounts `context-simple`, `tool-mcp@latest`, `hooks-approval@v0.1.0`, and the existing hooks. (CR-1 fix unchanged; SC-2 removal of `hooks-logging` unchanged.)

---

## §2 What changes — re-litigation of six locked decisions  <a id="s2-changes"></a>

This section addresses the six 2026-05-22 locked decisions that change. For each: the original locked position is quoted verbatim from §8 of the predecessor design; the amended position is stated; the rationale for the amendment is given; and the v1 cost/v1.x deferral is named.

The six decisions that change all share one structural property: they specified a JSON-RPC mechanism for carrying a piece of session config or a mid-turn round-trip across the wire. Each shifts to a different transport (argv, structured JSON output, or deferral) while preserving the underlying intent.

### 2.1 D1 — `agent/initialize` as JSON-RPC method  <a id="d1"></a>

**Original locked position** (from 2026-05-22 §4.10.1, quoted verbatim):

> Add two optional fields to `InitializeParams`:
>
> ```typescript
> // wrappers/typescript/src/types.ts (additions)
> export interface InitializeParams {
>   // ... existing fields ...
>   mcpServers?: Record<string, McpServerConfig>
>   host?: { capabilities?: HostCapabilities }
> }
> ```

The locked design assumed `agent/initialize` was a JSON-RPC method the engine receives as the first frame after subprocess spawn, with `InitializeParams` carrying `sessionId`, `resume`, `cwd`, `providerOverride`, `env`, `mcpServers`, and `host.capabilities`. The engine's `handle_initialize` (locked design §4.8) was specified to read these and configure the session, returning `InitializeResult`.

**Amended position**: `agent/initialize` is removed from the wire as a JSON-RPC method. All `InitializeParams` fields become CLI argv flags on `amplifier-agent run`. The engine validates and processes them at startup, before any prompt is consumed.

The full argv surface is enumerated in §3 below. The flag mapping:

| Original `InitializeParams` field | Amended argv flag | Format |
|---|---|---|
| `sessionId` | `--session-id <str>` | string |
| `resume` | `--resume` / `--fresh` | boolean flag pair |
| `cwd` | `--cwd <path>` | path |
| `providerOverride` | `--provider <name>` | string (already exists in current Mode A as `--provider`) |
| `env.allowlist` | `--env-allowlist <comma-list>` | comma-separated keys |
| `env.extra` | `--env-extra '<json>'` | JSON object |
| `mcpServers` | `--mcp-servers '<json-or-@path>'` | JSON object or `@/path/to/file.json` |
| `host.capabilities` | `--host-capabilities '<json>'` | JSON object |
| `protocolVersion` | `--protocol-version <str>` | string |

**Why the amendment**:

1. **Mode B's dispatcher is the only thing requiring `agent/initialize` to be a JSON-RPC method.** Without a long-lived process to send JSON-RPC frames into, there is no reason the engine should parse `InitializeParams` from a frame on stdin. argv is the natural transport for one-shot CLI invocations (this is how `curl`, `git`, `claude`, `codex`, and every other CLI shipping today handles its session config).
2. **The information NC needs to pass on every turn is small and bounded.** sessionId, resume flag, optional cwd, optional MCP config, optional host capabilities. None of it is large enough to make argv awkward. (MCP config can be 1-10KB in pathological cases; `--mcp-servers @/path/file.json` covers that.)
3. **argv is text-first and inspectable.** The 2026-05-20 KERNEL_PHILOSOPHY's "text-first, inspectable surfaces" tenet applies more strongly to argv than to JSON-RPC frames — a developer debugging a failed turn can `ps aux | grep amplifier-agent` and see exactly what config the engine was invoked with. JSON-RPC frames are gone after they're consumed.

**What this enables**: a 2-3 day path to working NC integration. The engine's current Mode A already accepts most of these flags (see `src/amplifier_agent_cli/modes/single_turn.py:155-191` for the existing surface: `--session-id`, `--resume`, `--fresh`, `--provider`, `--cwd`, `--allow-protocol-skew`). The amendment adds five new flags (`--mcp-servers`, `--host-capabilities`, `--env-allowlist`, `--env-extra`, `--protocol-version`) and a new output mode (`--output text|json`). No new JSON-RPC dispatcher. No new event emitter. No new bidirectional channel.

**What this costs**: nothing structural that v1 hosts need. The locked design's `agent/initialize` round-trip carried back `InitializeResult` with `capabilities`, `serverInfo`, and `sessionState`. Under the amendment, this metadata is folded into the structured JSON output envelope (§4 below) as the `metadata` field. The wrapper's `SessionHandle.getEngineInfo()` (locked design §8.14) reads it from that envelope.

### 2.2 D3 — `display/event` JSON-RPC notifications  <a id="d3"></a>

**Original locked position** (from 2026-05-22 §4.1.4 and the locked 2026-05-20 design §4.1, quoted verbatim from the latter):

> **Notifications (engine → client):**
> - `display/event` — the 9 canonical event types (see §4.4).
> - `result/final` — emitted at end of turn; if engine omits (legacy), wrapper synthesizes (the L14 path, kept as a safety net).
> - `error/*` — typed errors with named codes.

The locked design specified that during a turn the engine would emit a stream of `display/event` notifications carrying granular `turn/started`, `tool/started`, `tool/completed`, `assistant/text` (chunks), `turn/completed`, and (deferred) `subagent/*` and `error/recoverable` events. NC's adapter translated these to `ProviderEvent`s (locked design §4.2): `{type:'activity'}` for tool events, `{type:'result', text}` for the final message, `{type:'activity'}` for sub-agent events (SC-5), and an `error` event for `AaaError`s.

**Amended position**: `display/event` is eliminated from the v1 wire. The engine emits no mid-turn events on stdout. At subprocess exit, the engine writes a single structured JSON envelope (§4 below) carrying `{reply, turnId, sessionId, error, metadata}`. The wrapper synthesizes `{type:'activity'}` ticks every 2 seconds while the subprocess is running (preserving NC's stuck-detection signal at `poll-loop.ts:359-361`). When the subprocess exits, the wrapper parses the JSON envelope and yields `{type:'result', text: reply}` on success or `{type:'error', ...}` on failure. Sub-agent progress events remain deferred per SC-5 (unchanged).

The `DisplayEvent` taxonomy from the 2026-05-20 design §4.4 (9 events) is **deprecated for v1**. It is preserved in the v1.x deferral table (§6 below) as `D-v1.x-13` so a future streaming-capable wire can reintroduce it without re-litigating the canonical event names.

**Why the amendment**:

1. **NC consumes none of these events as themselves.** The translator table in 2026-05-22 §4.2 collapses every mid-turn `display/event` to either `{type:'activity'}` (for tool events) or no surfacing at all (for `assistant/text` chunks — NC delivers final results only). A 2-second wrapper-side ticker carries the identical information NC actually uses.
2. **Without a Mode B dispatcher, there is no place for the events to flow.** The 2026-05-20 design's gap (d) — *"add bundle-level streaming hook (§4.5) and thread `ctx.display.emit` / `ctx.approval.request` callbacks into the kernel hook bridge"* — required Mode B's stdio output channel to be open during the turn. Without that, there is no recipient for the events on the host side.
3. **The wrapper's synthesized 2-second ticker is operationally identical to what NC needs.** NC's stuck-detection threshold is 10s (referenced in 2026-05-22 §4.1.5: "Prevents NC's stuck-detection from firing during long tool runs that emit no display events for >10s"). A 2-second wrapper-side tick gives 5× margin against the threshold without requiring any engine-side emission.
4. **This is also what Claude Code does** when its `--print` mode invokes the CLI (the closest Claude analogy to amplifier-agent Mode A). The CLI emits no event stream on stdout; final output is the result text. Mid-turn, the host has no signal beyond "the subprocess is still alive".

**What this enables**: eliminates the bundle-level `streaming_emitter.py` hook (~200 LOC from the 2026-05-20 design Appendix C step 5) entirely. Eliminates the engine's `ctx.display.emit` bridge plumbing (~50 LOC). Eliminates the wrapper's `display.onEvent` callback consumption path. Removes A4 ("streaming hook can emit the minimum-coverage set of 5 of 9 canonical events") from the assumption surface — the riskiest assumption in the 2026-05-20 design per its own §2 A4 footnote.

**What this costs**: future hosts that need mid-turn granular event surfacing (a streaming chat UI showing live `tool/started`/`tool/completed` events as they happen) must wait for v1.x. The deferral nominee is `--output streaming-json` (NDJSON of events written to stdout while the engine continues running; wrapper parses line-by-line). The locked 9-event taxonomy from the 2026-05-20 design §4.4 is the obvious starting schema for that future surface — preserved as v1.x deferral, not deleted.

### 2.3 D4 — `approval/request`/`approval/response` bidirectional round-trip  <a id="d4"></a>

**Original locked position** (from 2026-05-22 §4.7 and §5.2 — locked design §5.2 quoted verbatim from 2026-05-20):

> Mid-turn, before a tool executes:
>
> 1. Kernel hook → `ctx.approval.request(ApprovalRequest)`.
> 2. Engine emits server-initiated request `approval/request {id, tool, args}` to client.
> 3. Wrapper's JSON-RPC client routes through the dedicated approval-response channel […]
> 4. Host returns `ApprovalResponse({decision: 'allow' | 'deny', ...})` within `approval.timeoutMs`.
> 5. Wrapper sends `approval/response {id, ...}`; engine resumes the tool.

The locked design routed mid-turn approval requests via a server-initiated JSON-RPC request from engine to host, with the host's `approval.onRequest` callback returning an `ApprovalResponse` within `timeoutMs`. The 2026-05-22 design's CR-2 closure added three typed error codes (`approval_translation_failed`, `approval_timeout`, `approval_protocol_violation`) and a `WireApprovalProvider` shim (`src/amplifier_agent_lib/wire_approval_provider.py`) that translated `ApprovalRequest` ↔ wire shape.

**Amended position**: the mid-turn `approval/request`/`approval/response` round-trip is eliminated from v1. The bundle's `hooks-approval@v0.1.0` mount is preserved (CR-2 closure remains); its **policy** (which tools trigger approval) is configured at the bundle layer via `hooks-approval`'s default-mode pattern matching and tool metadata gating. The wrapper's `SpawnAgentParams.approval.onRequest` field is preserved in the public API for forward compatibility, but the callback is **not invoked in v1** (the Mode A wire has no mechanism to send mid-turn requests to the host).

NC's existing posture is auto-allow per A10 (locked design §1.4 Q6 / §8.7 D6); under the amendment, this is achieved by either (a) configuring `hooks-approval` in the bundle to auto-allow all gated tools (the bundle is vendored and amplifier-agent owns it, so this is a one-line bundle config change), or (b) NC declaring a host capability `auto_approve_all: true` via `--host-capabilities` that the bundle's hook reads to suppress prompting. Option (a) is simpler for v1; option (b) is the path to v1.x mid-turn approval revival.

The three typed approval error codes (`approval_translation_failed`, `approval_timeout`, `approval_protocol_violation`) from CR-2 are preserved as turn-end errors — if the bundle's `hooks-approval` raises during a turn, the engine catches and emits via the JSON envelope's `error.classification = "approval"` (§4 schema below). They remain greppable, just not mid-stream.

**Why the amendment**:

1. **No v1 host invokes the round-trip.** NC sets `onRequest: () => ({decision: 'allow'})` (locked design §4.1.4). Paperclip's adapters route approval through `wakeReason: 'approval_callback'` inter-heartbeat (locked design §10.7) — i.e., Paperclip's approval is also out-of-band relative to the engine subprocess. OpenCode and Claude Code adapters have not been authored; the four-host runway analysis explicitly preserved capability flags to gate per-host policy at the wire boundary, but no host yet defined mid-turn UI approval prompts as a v1 requirement.
2. **Without Mode B's dispatcher, there is no transport for the server-initiated request.** The `WireApprovalProvider.wire_send_request` (2026-05-22 §4.7) requires a long-lived JSON-RPC channel.
3. **The bundle is the right policy point for "which tools trigger approval".** The 2026-05-22 design's D6 (D-NC: "Bundle defines mechanism, wire carries it, host decides policy") split policy across three layers — bundle (mechanism), wire (carrier), host (yes/no). With the wire as carrier eliminated, the natural simplification is: bundle holds policy + mechanism. Host policy in v1 is uniform "auto-allow" because no v1 host wants prompts. v1.x can re-introduce host-level overrides when a host actually needs them.

**What this enables**: eliminates the `WireApprovalProvider.wire_send_request` plumbing from `_runtime.py`. Eliminates the wrapper's server-initiated request routing in `jsonrpc-client.ts`. Eliminates the dedicated approval-response channel from the wire spec. Net engine LOC: ~80 LOC deleted (the shim's wire-round-trip implementation) but the class itself is preserved as a config-time approval provider for the bundle. Net wrapper LOC: ~120 LOC deleted (the JsonRpcClient's server-request fan-in path).

**What this costs**: hosts that need mid-turn UI approval prompts (an operator clicking "Allow" / "Deny" while the agent is running) must wait for v1.x. Two nominees for v1.x revival:

- **(a) Out-of-band subprocess approval.** Engine pauses on approval-gated tool, writes pending-approval state to the session-store volume, exits with a typed code; host sees the exit code, presents UI, invokes `amplifier-agent approve --session-id X --decision <allow|deny>`; engine resumes on next `amplifier-agent run --session-id X` invocation. This requires no wire round-trip and fits the per-turn subprocess model exactly.
- **(b) Webhook callback flag.** Engine receives `--approval-callback-url https://host/approve` via argv; on approval-gated tool, POSTs `{tool, args, sessionId, requestId}`; waits up to `timeoutMs` for `200 {decision}`. Simpler than (a) but requires the host to run an HTTP listener.

Both are deferrable; neither is needed for any of the four v1 hosts.

### 2.4 D6 — Strict-refuse protocol version skew (handshake mechanism)  <a id="d6"></a>

**Original locked position** (from 2026-05-22 §8 D6 referencing 2026-05-20 §8.1 D6, quoted verbatim from the latter):

> **Strict-refuse.** Wrapper compares its `PROTOCOL_VERSION` against engine's at `agent/initialize`. Mismatch → typed `AaaError(code='protocol_version_mismatch')` with self-remediating message: exact reinstall commands for both wrapper and engine, plus the `--allow-protocol-skew` flag and what it does.

The locked design checked protocol version compatibility during the JSON-RPC `agent/initialize` round-trip — the wrapper sent its `PROTOCOL_VERSION` in `InitializeParams.protocolVersion`; the engine compared and rejected on mismatch by returning a JSON-RPC error response.

**Amended position**: the strict-refuse semantics are preserved unchanged in behavior; the mechanism shifts from JSON-RPC handshake to argv validation. The engine accepts `--protocol-version <str>` on `amplifier-agent run`. At startup, before any prompt processing, the engine compares against its compiled-in `PROTOCOL_VERSION` constant. On mismatch, it emits the structured JSON error envelope (§4 schema) with `error.code = "protocol_version_mismatch"` and exits non-zero. The `--allow-protocol-skew` flag (already present in the current Mode A surface per `src/amplifier_agent_cli/modes/single_turn.py:169-175`) suppresses the check.

The wrapper does the corresponding work: at `spawnAgent` time, it could probe the engine's version via a fast `amplifier-agent version --json` invocation (this is what `probeEngineVersion` in `wrappers/typescript/src/spawn.ts` already does); or it passes `--protocol-version` on the `run` invocation and lets the engine self-validate. The amendment chooses the latter for transport symmetry — same flag carries the same data through the same mechanism.

**Why the amendment**:

1. **Without `agent/initialize` as a JSON-RPC handshake, the natural place for the check is argv validation.** Same intent, same behavior, mechanically simpler.
2. **It eliminates one extra subprocess invocation per `spawnAgent`.** The TypeScript wrapper currently invokes `amplifier-agent version --json` before spawning the engine (per `probeEngineVersion`); under the amendment, the engine self-validates `--protocol-version` so the wrapper can skip the probe. (Optional: the wrapper can still probe for diagnostic purposes; it just doesn't need to gate on it.)
3. **`--allow-protocol-skew` is already implemented in the current Mode A** (see `src/amplifier_agent_cli/modes/single_turn.py:169-175`). The amendment leverages the existing surface.

**What this enables**: removes the JSON-RPC error response path for `protocol_version_mismatch` (already not implemented under the amendment, since `agent/initialize` is gone). Self-remediating error message is preserved — the engine emits it via the JSON error envelope's `error.message` field (§4 below).

**What this costs**: nothing. Behavior is identical.

### 2.5 D9 — `mcpServers` as additive `agent/initialize` field  <a id="d9"></a>

**Original locked position** (from 2026-05-22 §4.10.1 and §8.10, quoted verbatim from §8.10):

> - **Wire**: additive `mcpServers?: Record<string, McpServerConfig>` on `InitializeParams` (§4.10.1).
> - **Engine**: `_runtime.py` reads `params['mcpServers']` and threads to `tool-mcp.mount(coordinator, config={**bundle_static, "servers": params['mcpServers']})` per A13 verification.
> - **Wrapper**: identity-pass the field; no shape transformation.
> - **Adapter (NC)**: `mcp-translator.ts` shape-validates, identity-passes.

The locked design carried per-session MCP server config via an additive field on `InitializeParams`.

**Amended position**: `mcpServers` becomes the `--mcp-servers '<json-or-@path>'` argv flag on `amplifier-agent run`. Two acceptable forms:

```
# Inline JSON (suitable for small configs)
amplifier-agent run --mcp-servers '{"nanoclaw_send_message":{"transport":"stdio","command":"node","args":["/usr/local/lib/nanoclaw-mcp/send-message.js"],"env":{"CHANNEL_ID":"slack-C123"}}}' ...

# @path form (suitable for large configs or configs containing secrets)
amplifier-agent run --mcp-servers @/tmp/nc-mcp-config.json ...
```

When `@path` form is used, the engine reads the file at startup. Path resolution: relative paths are relative to `--cwd` (or process CWD if unset); `~` is expanded per implementation philosophy ("Always call `.expanduser()` on paths that may contain `~`").

The engine's `_runtime.py` threading to `tool-mcp.mount(config={**bundle_static, "servers": parsed_mcp_servers})` is **preserved verbatim** from 2026-05-22 §4.8. The A13 verification (mount runtime config has highest priority per `amplifier_module_tool_mcp/config.py:35-53,56-61`) still holds — only the source of `parsed_mcp_servers` changes from `params['mcpServers']` to the argv flag's parsed value.

The wrapper's `SpawnAgentParams.mcpServers?: Record<string, McpServerConfig>` field is preserved verbatim (already exists per `wrappers/typescript/src/index.ts:67`); the wrapper translates it to the `--mcp-servers` argv flag at subprocess spawn time.

**Secret-aware spill policy** (CR-A — tightened from a size-only threshold to a secret-bearing rule):

- **Spill whenever any `mcpServers.<name>.env` block is non-empty**, regardless of total argv size. The original ">8KB" threshold solved overflow, not secret leakage. An MCP server config carrying a 200-byte API key in `env` is well under any argv-size threshold but still leaks the secret to every process that can read `/proc/<pid>/cmdline` or `ps -ef`. The size-based spill is preserved as a secondary trigger for overflow protection (>8KB inline payloads still spill), but the secret-bearing rule is now the primary trigger.
- **Tmpfile location**: `${XDG_RUNTIME_DIR:-/tmp}/amplifier-agent/<session-id>/mcp.json`. Mode `0600`. Owned by the invoking user. The session-id-scoped path prevents concurrent sessions from colliding on the same tmpfile.
- **Tmpfile lifecycle**: the wrapper creates the tmpfile pre-spawn and passes `--mcp-servers @/path` to the engine. The wrapper deletes the tmpfile on subprocess exit (success or failure) via `try/finally`. The engine treats the tmpfile as authoritative input; it does NOT modify or persist it. (Belt-and-suspenders: the wrapper's deletion is unconditional. If the wrapper crashes mid-turn before exit, the next `spawnAgent` call for the same session-id will overwrite the tmpfile; orphaned tmpfiles older than ~24h should be reaped by an out-of-band cleanup script — listed as a Phase 4 follow-up in the migration plan.)
- **When `mcpServers` is null/empty**, no flag is passed and no tmpfile is created.
- **When `mcpServers` is present but every server's `env` block is empty** (rare in practice; covers pure stdio MCP servers like `npx -y @modelcontextprotocol/server-postgres` that hardcode all arguments and read credentials from the filesystem), inline argv is acceptable — no secrets to leak.

**Threat model** (CR-A):
- Shared-PID-namespace co-tenancy: any process in the same PID namespace that can call `getprocs()`/`ps`/read `/proc/<pid>/cmdline` sees the full argv. Inline secrets in `--mcp-servers` are exposed for the lifetime of the engine subprocess. NC's own container is single-tenant at v1 (one agent per container) so this is not currently exploited, but future hosts may run multi-tenant. The same exposure pattern motivates Kubernetes pod sidecar argv hygiene, shared CI runner secret handling, and the Docker `--env-file` vs `-e KEY=VAL` distinction.
- Operator observability: an operator inspecting a running engine via `ps aux | grep amplifier-agent` for diagnostic purposes should be able to see *what* MCP servers are configured (names, transports, arg shapes) without seeing the credentials. The spill policy achieves this — argv shows `--mcp-servers @/run/.../mcp.json`; the file itself is `0600` to the operator's account, not world-readable.

NC's `mcp-translator.ts` (locked design §4.3) is **unchanged** — it still shape-validates and identity-passes, just now into the wrapper's `mcpServers` field which translates to argv (with secret-aware spill) at spawn time.

**Why the amendment**:

1. **The transport is the only thing that changes; the rest of the plumbing is preserved.** This is a pure mechanism shift, exactly like D6.
2. **MCP env-secret protection (CR-3 stderrTail redaction) is unchanged.** The CR-3 mitigation operates at the NC `event-translator.ts` layer; it redacts based on the keys NC declared. Whether those keys came in via JSON-RPC or argv doesn't affect the redaction logic. The new spill policy adds a complementary in-flight protection: secrets never enter the argv string at all when an `env` block is non-empty.
3. **argv-size for typical MCP configs is small enough to fit inline (when no secrets are present).** NC's `nanoclaw_send_message` MCP server config without env is ~200-400 bytes JSON.

**What this enables**: eliminates the JSON-RPC plumbing for `mcpServers` field-passing while preserving every other piece of the closure, and closes the CR-A argv-exposure regression that a naive size-only spill would have introduced.

**What this costs**: marginal — a tmpfile write on any MCP config carrying an `env` block. Operationally invisible at NC's v1 cadence; the tmpfile lives at most one turn.

### 2.6 D12 — `host.capabilities` as additive `agent/initialize` field  <a id="d12"></a>

**Original locked position** (from 2026-05-22 §8 D12, quoted verbatim):

> Additive `host?: { capabilities?: HostCapabilities }` field on `InitializeParams`. v1 capabilities:
> - `supports_steering?: boolean` — false for NC (B1 buffer is host-side; engine doesn't need to know).
> - `supports_structured_errors?: boolean` — true for NC.

**Amended position**: `host.capabilities` becomes the `--host-capabilities '<json>'` argv flag on `amplifier-agent run`. The v1 capabilities (`supports_steering`, `supports_structured_errors`) are unchanged; the engine still stores them on `SessionState.metadata["host_capabilities"]` (2026-05-22 §4.8). The wrapper's `SpawnAgentParams.host?.capabilities?: HostCapabilities` field is preserved verbatim (already exists per `wrappers/typescript/src/index.ts:69`).

The wrapper translates the field to `--host-capabilities '<serialized-json>'` at subprocess spawn time. Size for typical capability bags is well under any argv threshold (the v1 surface is two booleans; ~50 bytes JSON).

**Why the amendment**: same as D9 — pure mechanism shift, behavior identical.

The four-host runway lock is preserved. Adding a new host (Paperclip, OpenCode, Claude Code) still requires only an additive boolean field on `HostCapabilities` (with documented default for older engines that ignore unknown fields, per the TypedDict tolerance pattern). R7 ("Multi-host capability-flag sprawl") is preserved unchanged.

**What this enables**: removes one JSON-RPC field's plumbing.

**What this costs**: nothing.

### 2.7 Decisions preserved unchanged

The amendment leaves six of the twelve 2026-05-22 locked decisions **unchanged**. They are restated here for completeness:

| # | Decision name | Status under amendment |
|---|---|---|
| **D2** | Adapter scope (provider class + 2 pure-function helpers + minimal host registration) | **Unchanged.** ~470 LOC NC code budget unchanged. |
| **D5** | Chain vocabulary (one NC `query()` = one wire-session; each chain link = wire-level turn within session) | **Unchanged.** Each chain link is now a `spawnAgent({sessionId: same, resume: true})` invocation that becomes one `amplifier-agent run --session-id same --resume "<buffered text>"` subprocess. The chain-link concept and the wire vocabulary survive the transport shift. |
| **D7** | Session state placement (host-mounted volume at `/home/node/.local/state/amplifier-agent/`) | **Unchanged.** Engine's `SessionStore` and `IncrementalSaveHook` (CR-1) carry over verbatim. |
| **D8** | `AaaError` taxonomy (add `severity`, `correlationId`, `stderrTail` to existing `code` + `classification`; classification enum expanded to include `'approval'`) | **Unchanged.** The taxonomy is preserved on the wrapper's TypeScript `AaaError` class. The engine emits the same fields via the JSON error envelope (§4 schema) instead of via mid-stream JSON-RPC errors. |
| **D10** | Binary install (`UV_TOOL_BIN_DIR=/usr/local/bin uv tool install amplifier-agent==$VER` at image build) | **Unchanged.** |
| **D11** | `prepare` placement (image-build time as `node` user; adapter-side lazy fallback on `engine_not_primed`) | **Unchanged.** |

### 2.8 Summary of the change set

| Decision | Original mechanism | Amended mechanism | Cost |
|---|---|---|---|
| D1 | JSON-RPC `agent/initialize` method | argv flags on `amplifier-agent run` | Lose JSON-RPC handshake; gain text-first inspectable surface |
| D3 | JSON-RPC `display/event` notification stream | Wrapper-synthesized 2-second `{type:'activity'}` ticker; final `reply` in JSON envelope | Lose mid-turn granular events (no v1 host uses them); gain ~250 LOC deleted from engine+bundle |
| D4 | JSON-RPC `approval/request`/`approval/response` round-trip | Bundle-layer auto-approve config (or future `--host-capabilities` opt-in) | Lose mid-turn UI approval prompts (no v1 host uses them); two v1.x deferral nominees identified |
| D6 | Strict-refuse on `InitializeParams.protocolVersion` | Strict-refuse on `--protocol-version` argv | Zero behavioral change |
| D9 | Additive `mcpServers` field on `InitializeParams` | `--mcp-servers '<json-or-@path>'` argv flag | Zero behavioral change; tmpfile spill for >8KB configs |
| D12 | Additive `host.capabilities` field on `InitializeParams` | `--host-capabilities '<json>'` argv flag | Zero behavioral change |

The six preserved decisions (D2, D5, D7, D8, D10, D11) carry forward verbatim with mechanism shifts in their JSON-RPC dependencies (D8's wire-level error stream becomes the JSON error envelope; D5's chain links become subprocess invocations).

---

## §3 Mode A v2 CLI specification  <a id="s3-cli"></a>

The amended `amplifier-agent run` subcommand. Spec format mirrors the existing click decorator surface in `src/amplifier_agent_cli/modes/single_turn.py`.

### 3.1 Command shape

```
amplifier-agent run \
  --session-id <str> \
  [--resume | --fresh] \
  [--cwd <path>] \
  [--provider <name>] \
  [--mcp-servers '<json-or-@path>'] \
  [--host-capabilities '<json>'] \
  [--env-allowlist '<comma-list>'] \
  [--env-extra '<json>'] \
  [--protocol-version <str>] \
  [--output text|json] \
  [--allow-protocol-skew] \
  [-y | -n] \
  [-v | --debug] \
  [--quiet] \
  "<prompt>"
```

### 3.2 Per-flag spec

#### Argument

`prompt` (positional, required for `--output json`; required-for-non-TTY-stdin under `--output text` for backward compat)
- Type: string
- Default: none
- Maps to: the user message passed to the engine for this turn
- Example: `"What time is it?"`

#### Session control (existing in current Mode A)

`--session-id <str>` (required for `--output json`; optional under `--output text` for backward compat with the current ad-hoc Mode A)
- Type: string
- Default: none
- Maps to: original `InitializeParams.sessionId`
- Notes: must be filesystem-safe per the Windows-compatibility rule in IMPLEMENTATION_PHILOSOPHY. The wrapper sanitizes; the engine treats as opaque.

`--resume` (flag, exclusive with `--fresh`)
- Type: boolean
- Default: `false`
- Maps to: original `InitializeParams.resume`
- Behavior: engine loads transcript from `$XDG_STATE_HOME/amplifier-agent/sessions/<sessionId>/transcript.jsonl` via `SessionStore`. Already implemented in current Mode A.

`--fresh` (flag, exclusive with `--resume`)
- Type: boolean
- Default: `false`
- Behavior: engine discards any existing transcript for `<sessionId>` before starting. Already implemented in current Mode A (see `src/amplifier_agent_cli/modes/single_turn.py:104-111`).

If both `--resume` and `--fresh` are absent, default is fresh-but-non-destructive (engine starts a new turn; does not load existing transcript; does not delete it either). The default mirrors current Mode A behavior.

#### Working directory and provider (existing)

`--cwd <path>` (optional)
- Type: path
- Default: process CWD
- Maps to: original `InitializeParams.cwd`
- Notes: `.expanduser()` applied per implementation philosophy. Already implemented.

`--provider <name>` (optional)
- Type: string
- Default: detected via `provider_detect.detect_provider()`
- Maps to: original `InitializeParams.providerOverride`
- Notes: already exists in current Mode A as `--provider`. Amendment preserves the name (not renamed to `--provider-override`).

#### Session config (new)

`--mcp-servers '<json-or-@path>'` (optional)
- Type: string (JSON object) or `@<path>` (path to JSON file)
- Default: none (engine uses only bundle-static MCP config)
- Maps to: original `InitializeParams.mcpServers`
- Schema: `Record<string, McpServerConfig>` per 2026-05-22 §4.10.1. McpServerConfig accepts `transport: 'stdio'|'sse'|'streamable_http'` + transport-specific fields.
- Example (inline, no `env` block on any server): `--mcp-servers '{"nc_send":{"transport":"stdio","command":"node","args":["/x.js"]}}'`
- Example (@path, used whenever any server has a non-empty `env` block): `--mcp-servers @/run/user/1000/amplifier-agent/sess-abc-001/mcp.json`
- Behavior: engine parses; threads to `tool-mcp.mount(coordinator, config={**bundle_static, "servers": parsed})`. CR-3 stderrTail redaction at NC's `event-translator.ts` operates on the keys declared here (unchanged from 2026-05-22).
- **Wrapper spill policy (CR-A)**: the wrapper inspects the parsed `mcpServers` value at spawn time. If any server's `env` block is non-empty, the wrapper writes the full JSON to a tmpfile at `${XDG_RUNTIME_DIR:-/tmp}/amplifier-agent/<session-id>/mcp.json` (mode `0600`, owned by invoking user) and passes `--mcp-servers @<path>` to the engine. The tmpfile is deleted on subprocess exit (success or failure) via `try/finally`. Inline argv is used only when every server's `env` block is empty (no secrets to leak). The legacy >8KB size threshold remains as a secondary trigger for overflow protection. See §2.5 for the threat model.

`--host-capabilities '<json>'` (optional)
- Type: string (JSON object)
- Default: `{}` (all capabilities default-false per receiver-default semantics)
- Maps to: original `InitializeParams.host.capabilities`
- Schema: `HostCapabilities` per 2026-05-22 §4.10.1. v1 capabilities: `supports_steering`, `supports_structured_errors`. Receiver-tolerant: unknown fields are stored on `SessionState.metadata["host_capabilities"]` without error.
- Example: `--host-capabilities '{"supports_steering":false,"supports_structured_errors":true}'`

`--env-allowlist '<comma-list>'` (optional)
- Type: comma-separated string of env var names
- Default: built-in default allowlist (engine ships an opinionated set: `PATH`, `HOME`, `USER`, `LANG`, `LC_*`, `TZ`, `XDG_*`, `TMPDIR`)
- Maps to: original `InitializeParams.env.allowlist` (locked design §4.10.1 `env?: { extra?: Record<string, string> }` — the `allowlist` was implicit on the wrapper side via `DEFAULT_ALLOWLIST` in `wrappers/typescript/src/spawn.ts`)
- Behavior: the wrapper's `buildEnv` already applies the allowlist when constructing the subprocess env (2026-05-22 §4.12.1 / SC-3). Under the amendment, the wrapper still applies it at spawn time; the engine receives the resulting env and does not re-validate. The flag exists for diagnostic transparency: an operator can inspect the running process's argv to confirm which keys were passed through.
- Example: `--env-allowlist "PATH,HOME,USER,LANG,ANTHROPIC_API_KEY"`

`--env-extra '<json>'` (optional)
- Type: string (JSON object of env-key → value pairs)
- Default: `{}`
- Maps to: original `InitializeParams.env.extra`
- Behavior: the wrapper's `buildEnv` merges these onto the subprocess env after applying `BLOCKED_ENV_KEYS` validation (SC-3, preserved unchanged). The amendment carries `--env-extra` for diagnostic transparency; the wrapper passes the validated subset, not the raw input.
- Example: `--env-extra '{"AMPLIFIER_AGENT_LOG_LEVEL":"debug"}'`

#### Protocol version (mechanism shift from D6)

`--protocol-version <str>` (optional)
- Type: string
- Default: none (no check)
- Maps to: original `InitializeParams.protocolVersion`
- Behavior: engine compares against compiled-in `amplifier_agent_lib.protocol.PROTOCOL_VERSION`. Mismatch → JSON error envelope with `error.code = "protocol_version_mismatch"` and self-remediating message; exit code non-zero.
- Suppressed by: `--allow-protocol-skew` (existing in current Mode A; see `src/amplifier_agent_cli/modes/single_turn.py:169-175`).
- Example: `--protocol-version 0.1.0`

#### Output mode (new)

`--output text|json` (optional)
- Type: enum (`text` | `json`)
- Default: `json`
- Behavior:
  - `json`: wrapper-consumer form (default). Emits the structured JSON envelope from §4 below on stdout. Used by `amplifier-agent-client-ts` and any host adapter consuming the engine programmatically. Also what existing scripts (DTU validation harnesses, smoke tests, lint scripts) parsing the current Mode A output expect — the current `single_turn.py` already emits JSON unconditionally, so this default preserves their behavior.
  - `text`: opt-in human-readable form. Emits the reply text on stdout, one line per paragraph, no JSON envelope. For developers running `amplifier-agent run "..."` interactively from a terminal and not wanting to pipe through `jq`.
- Note: the wrapper always passes `--output json` explicitly (good practice; no behavior change). Humans wanting plain text pass `--output text`.

#### Existing controls (preserved verbatim)

`--allow-protocol-skew` (flag) — already exists; behavior preserved.

`-y, --yes` / `-n, --no` (flag pair, mutually exclusive) — already exists; routes through `CliApprovalSystem(mode='yes'|'no'|'prompt')`. Under the amendment with D4 amended (no mid-turn host approval round-trip), these flags configure the **bundle's** approval policy via the CLI's existing protocol-points layer. Behavior unchanged for direct-CLI use; the wrapper sets `--yes` to mirror NC's auto-allow posture.

`-v, --verbose` / `--debug` / `--quiet` (flag set) — already exists. Controls `CliDisplaySystem` verbosity on stderr. Stdout discipline preserved: under `--output json`, stdout is exclusively the JSON envelope; stderr carries diagnostics.

### 3.3 Backward compatibility

The current Mode A surface (per `src/amplifier_agent_cli/modes/single_turn.py:155-191`) is a subset of the amended surface. The eight existing flags (`--session-id`, `--resume`, `--fresh`, `--provider`, `--cwd`, `-v`, `--debug`, `-y`, `-n`, `--quiet`, `--allow-protocol-skew`) carry forward unchanged. Five new flags are added (`--mcp-servers`, `--host-capabilities`, `--env-allowlist`, `--env-extra`, `--protocol-version`) plus `--output`.

Existing scripts that invoke `amplifier-agent run "<prompt>"` continue to work:
- The current `single_turn.py` emits JSON unconditionally. The amendment preserves that as the default (`--output json`), so DTU validation harnesses, the empirical-verification scripts at `scripts/`, the smoke-test recipes, and any lint scripts that parse the engine's stdout continue working without modification.
- Humans wanting plain-text output for interactive CLI use opt in via `--output text`. This is the only behavior change in the surface.
- The previous draft of this amendment proposed `--output text` as the default. The CR-E adversarial review found this would silently break the existing consumers above; the default has been reversed.

---

## §4 Mode A v2 JSON output schema  <a id="s4-schema"></a>

The structured JSON envelope the engine emits on stdout when `--output json` is set. One envelope per `amplifier-agent run` invocation, emitted at subprocess exit just before the engine terminates.

### 4.0 Stdout discipline (CR-B)

The engine MUST guarantee that, under `--output json`, stdout contains **exactly one** JSON document: the envelope, written at exit. Any other writer (bundle modules calling `print()`, provider diagnostics, `warnings.warn(...)` defaulting to stderr-via-stdout on some Pythons, hook output) would corrupt the envelope the wrapper parses.

The engine enforces this structurally — not by convention — via the following pattern in the `run` subcommand entry:

```python
# src/amplifier_agent_cli/modes/single_turn.py (sketch)
def run(...):
    if output_mode == "json":
        _real_stdout = sys.stdout                          # save FD
        with contextlib.redirect_stdout(sys.stderr):       # redirect everything else
            result = _execute_turn(...)                    # all bundle/provider/tool writes land on stderr
        # Only the envelope writes to the real stdout, at the very end:
        _real_stdout.write(json.dumps(_build_envelope(result)) + "\n")
        _real_stdout.flush()
        _real_stdout.close()
```

This is enforced regardless of whether the bundle/provider/tool is well-behaved. A future bundle author who calls `print("debug")` inside a hook gets their output on stderr; the envelope on stdout remains parseable.

The CR-B fixture `mode-a-stdout-discipline.yaml` (see §8.1 A4') verifies this by mounting a bundle module that prints 50 lines mid-turn and asserting the wrapper's `JSON.parse` succeeds on the engine's stdout.

### 4.1 Schema (TypeScript form)

```typescript
interface ModeARunOutput {
  protocolVersion: string;           // engine's PROTOCOL_VERSION
  sessionId: string;                 // echoed back; matches --session-id if provided, else minted
  turnId: string;                    // engine-minted turn identifier ("turn-1", "turn-2", ...)
  reply: string;                     // the assistant's final text response; empty string if error
  error: ErrorEnvelope | null;       // null on success; populated on failure
  metadata: ResultMetadata;          // observability + diagnostics
}

interface ErrorEnvelope {
  code: string;                                   // e.g. "approval_translation_failed", "engine_crashed"
  classification: 'transport' | 'protocol' | 'engine' | 'approval' | 'unknown';
  severity: 'error' | 'warning';
  correlationId: string;                          // engine-minted UUID; greppable in audit logs (same value as metadata.correlationId)
  message: string;                                // human-readable explanation
  stderrTail?: string;                            // last ~4KB of stderr; redacted by wrapper per CR-3
  remediation?: string;                           // self-remediating instructions (e.g. for protocol_version_mismatch)
  details?: Record<string, unknown>;              // typed extra (e.g. supported/requested for lifecycle_unsupported)
}

interface ResultMetadata {
  tokensIn: number;                  // input tokens charged
  tokensOut: number;                 // output tokens charged
  durationMs: number;                // wall-clock duration of the turn
  bundleDigest: string;              // SHA of the prepared bundle (R6 enablement; from doctor --emit-sha)
  engineVersion: string;             // semver of amplifier-agent binary
  protocolVersion: string;           // same as top-level; duplicated for convenience
  hostCapabilities?: Record<string, unknown>;  // echoes back what was passed via --host-capabilities
  correlationId: string;             // SC-G: engine-minted UUID v4 at run start; identical to error.correlationId on failure paths
  // Reserved for future fields (extensibility slot per ecosystem convention)
}
```

**`correlationId` lifecycle (SC-G)**: the engine mints one UUID v4 at the very start of the `run` subcommand (before any argv parsing beyond the bare minimum needed to determine `--output` mode). It is logged at stderr-debug as the first line. It is included in **every** envelope this invocation emits — success path under `metadata.correlationId`; failure path under both `metadata.correlationId` AND `error.correlationId` (duplicated for backward compatibility with code that reads from the AaaError shape, and so a host doing audit-log correlation on failed turns has the same field name regardless of how it reaches the error). The wrapper surfaces it to NC under the `correlationId` field on `error` ProviderEvents; NC's adapter additionally logs `metadata.correlationId` as a structured stderr line on every successful turn so an operator grep'ing the audit trail can correlate engine logs ↔ AaA wrapper logs ↔ NC events even on the success path.

The `error: ErrorEnvelope | null` shape mirrors the 2026-05-22 D8 `AaaError` taxonomy verbatim (severity, correlationId, stderrTail are the 2026-05-22 additions; classification was preserved from 2026-05-20). The wrapper's TypeScript `AaaError` (per `wrappers/typescript/src/index.ts:9` and the locked surface in 2026-05-22 §8.14) reads from this envelope when constructing the exception to throw.

### 4.2 Concrete example — success

```json
{
  "protocolVersion": "0.1.0",
  "sessionId": "sess-abc-001",
  "turnId": "turn-1",
  "reply": "It is 2:15pm Pacific time.",
  "error": null,
  "metadata": {
    "tokensIn": 1247,
    "tokensOut": 89,
    "durationMs": 1832,
    "bundleDigest": "sha256:7f3a9e2b4c5d6e8f1a3b5c7d9e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f",
    "engineVersion": "0.2.0",
    "protocolVersion": "0.1.0",
    "hostCapabilities": {"supports_steering": false, "supports_structured_errors": true}
  }
}
```

### 4.3 Concrete example — failure

```json
{
  "protocolVersion": "0.1.0",
  "sessionId": "sess-abc-001",
  "turnId": "turn-1",
  "reply": "",
  "error": {
    "code": "approval_translation_failed",
    "classification": "approval",
    "severity": "error",
    "correlationId": "01HXYZ123ABC456DEF789",
    "message": "failed to translate ApprovalRequest to bundle hook shape: unknown approval action 'review'",
    "stderrTail": "Traceback (most recent call last):\n  File \"...wire_approval_provider.py\", line 42, in...\n",
    "remediation": null,
    "details": {"action_received": "review", "actions_supported": ["allow", "deny"]}
  },
  "metadata": {
    "tokensIn": 0,
    "tokensOut": 0,
    "durationMs": 247,
    "bundleDigest": "sha256:7f3a9e2b...",
    "engineVersion": "0.2.0",
    "protocolVersion": "0.1.0",
    "hostCapabilities": {"supports_steering": false, "supports_structured_errors": true}
  }
}
```

### 4.4 Exit code policy and envelope precedence (SC-D)

**Exit code semantics** — informational; not the authoritative source on failure:
- Exit `0`: `error` is `null`. Reply is the authoritative result.
- Exit `1`: `error.classification` is `engine`, `transport`, or `unknown`. Reply is empty string.
- Exit `2`: `error.classification` is `protocol` (skew, schema violation, malformed argv). Distinguishable from exit 1 for CI gating.
- Exit `3`: `error.classification` is `approval`. Distinguishable so hosts can implement deferral-style approval flows (v1.x option (a) per §2.3) without parsing the envelope.

**Precedence rule** (used by the wrapper at subprocess exit; addresses SC-D race between exit-code and envelope semantics):

1. **Envelope parseable per §4.1 schema** → the envelope is authoritative. The `error` field (null or populated) drives the wrapper's `AaaError` synthesis. The exit code is logged at debug-stderr for diagnostic correlation but does NOT override the envelope. (Example: an engine that hits an internal bug after building a valid envelope but before reaching the normal-exit code path may return exit 1 with `error: null` — the wrapper trusts the envelope and yields `result`.)

2. **Envelope absent or unparseable** (stdout empty; truncated JSON; not the §4.1 shape) → wrapper synthesizes the `AaaError`:
   - **Exit 0 + no envelope**: `{code: "envelope_missing", classification: "protocol", severity: "error", message: "Engine exited 0 without emitting an envelope. Stdout was: <truncated_to_512b>", stderrTail: <captured>}`. This is a protocol violation by the engine; covered by the CR-B fixture.
   - **Non-zero exit + no envelope**: `{code: "engine_exit_<code>", classification: "engine", severity: "error", message: <stderr-derived>, stderrTail: <captured>}`.
   - **Non-zero exit + partial JSON**: same as "no envelope" — the wrapper does NOT attempt to half-parse. Belt-and-suspenders: if any required §4.1 field is missing, treat as unparseable.

3. **Subprocess hangs** (no exit within timeout; timeout configurable via wrapper options, default 10min for chat turns): wrapper SIGTERMs the engine, waits 5s for graceful exit, then SIGKILLs (the SC-B PGID-aware cleanup is used so MCP children die with the engine), then synthesizes `{code: "engine_hung", classification: "engine", severity: "error", message: "Engine did not exit within <timeout>ms"}`.

The conformance fixture `mode-a-envelope-precedence.yaml` (§8.1 A4') exercises rule 1: engine deliberately returns exit 1 with `error: null` in envelope; wrapper must yield `result`, not `error`. The fixture `mode-a-stdout-discipline.yaml` exercises rule 2 negatively (envelope is parseable despite stdout noise; rule 1 wins).

### 4.5 JSON Schema file location

`schemas/run-output.json` — JSON Schema (Draft 2020-12) form of the above. Generated alongside the existing `schemas/agent-initialize.json` via the `wrappers/_gen.py` mechanism (2026-05-20 §4.1) if the Python TypedDict source is updated to include `RunOutput` and `ErrorEnvelope` types; otherwise hand-authored. The generator path is preferred for D1-equivalence reasons (single source of truth) but is not blocking — the schema is small enough to maintain by hand if the gen tool doesn't natively cover this surface.

### 4.6 `--output text` behavior

When `--output text`:
- On success: the engine writes `reply` to stdout (no envelope). Newlines preserved as-is. Exit 0.
- On failure: the engine writes a single line `[error] <code>: <message>` to stderr (matching the current Mode A error-emission style at `src/amplifier_agent_cli/modes/single_turn.py:204-209`); exit code per §4.4.

`--output text` is the default for direct-CLI invocation. The wrapper always passes `--output json`.

---

## §5 The amended wrapper architecture  <a id="s5-wrapper"></a>

Walks through the changes to `amplifier-agent-client-ts` (and the parity `amplifier-agent-client-py`) implied by the amendment. The public API surface is **preserved verbatim** from 2026-05-22 §8.14; only the implementation under the surface changes.

### 5.1 `spawnAgent` becomes synchronous (no subprocess at spawn time)

**Current** (per `wrappers/typescript/src/index.ts:115-251`): `spawnAgent` is `async`; spawns the engine subprocess, runs the JSON-RPC `agent/initialize` handshake, returns a `SessionHandle` that holds the live subprocess + rpc client + transport.

**Amended**: `spawnAgent` becomes synchronous in spirit (returns a `Promise<SessionHandle>` for API compatibility, but the promise resolves without doing any subprocess work). It records the constructor params into the `SessionHandle`'s state. The subprocess is not spawned until `submit()` is called.

```typescript
export async function spawnAgent(params: SpawnAgentParams): Promise<SessionHandle> {
  // 1. Lifecycle guard (D10) — unchanged.
  if (params.lifecycle !== "one-shot") {
    throw new AaaError("lifecycle_unsupported", "...");
  }
  // 2. Resolve binary path — unchanged.
  const binaryPath = params._binaryResolver?.() ?? resolveBinaryPath({ env: process.env });
  // 3. Validate env (BLOCKED_ENV_KEYS) — unchanged from SC-3.
  const subprocessEnv = buildEnv({ processEnv: process.env, allowlist: params.env?.allowlist ?? DEFAULT_ALLOWLIST, extra: params.env?.extra ?? {} });
  // 4. Mint or accept sessionId. Mint a UUID if --session-id is absent (engine accepts and echoes back).
  const sessionId = params.sessionId ?? mintSessionId();
  // 5. Construct the SessionHandle without spawning anything.
  return new SessionHandle({
    binaryPath,
    subprocessEnv,
    sessionId,
    resume: params.resume,
    cwd: params.cwd,
    mcpServers: params.mcpServers,
    hostCapabilities: params.host?.capabilities,
    providerOverride: params.providerOverride,
    approval: params.approval,           // preserved for v1.x; not invoked in v1
    allowProtocolSkew: params.allowProtocolSkew,
  });
}
```

### 5.2 `SessionHandle.submit(prompt)` returns `AsyncIterable<DisplayEvent>`

**Current**: under the locked design, `submit` would send `turn/submit` JSON-RPC over the live transport and yield `DisplayEvent`s from incoming `display/event` notifications until `turn/completed`.

**Amended**: each `submit()` call spawns a fresh `amplifier-agent run` subprocess with the assembled argv. The async iterable yields events of the simplified `DisplayEvent` type below.

**Revised `DisplayEvent` shape (CR-C)** — breaking change to `wrappers/typescript/src/session.ts:26-37`:

```typescript
// wrappers/typescript/src/session.ts (revised for Mode A)
// Breaking change: removes turnId, parentTurnId, synthesized, payload — the Mode A
// wire cannot meaningfully populate these. The shape now matches what NC's existing
// ProviderEvent consumer (container/agent-runner/src/poll-loop.ts) actually uses.
export type DisplayEvent =
  | { type: 'init';     sessionId: string }
  | { type: 'activity' }
  | { type: 'result';   text: string }
  | { type: 'error';    code: string;
                        classification: 'transport' | 'protocol' | 'engine' | 'approval' | 'unknown';
                        severity: 'error' | 'warning';
                        correlationId: string;
                        message: string;
                        stderrTail?: string;
                        retryable: boolean }
```

The `payload` slot from the locked design (an extensibility hook for future event types) is intentionally removed. When v1.x reintroduces streaming events (WG-3 deferral nominee `--output streaming-json`), a new event type can be added to the discriminated union without retrofitting `payload` onto every existing case. The shape is small, fixed, and exhaustively matchable.

**Iterable behavior:**

1. **Immediately yields `{type:'init', sessionId}`.** The sessionId came from the constructor (caller-supplied or minted). This satisfies SC-1 (init-before-activity ordering) — the init event is emitted synchronously before the subprocess is spawned, so it can never race the activity ticker.

2. **Spawns the subprocess.** Assembles argv from the SessionHandle's state (per §3 above): `[binaryPath, "run", "--session-id", sessionId, ...resumeFlag, "--cwd", cwd, "--provider", providerOverride, "--mcp-servers", mcpServersFlag, "--host-capabilities", serialized(hostCapabilities), "--env-allowlist", allowlist.join(","), "--env-extra", serialized(envExtra), "--protocol-version", PROTOCOL_VERSION_REQUIRED_BY_WRAPPER, "--output", "json", "-y", prompt]`. `mcpServersFlag` is `@<tmpfile>` if any server has a non-empty `env` block (CR-A secret-aware spill, §2.5) or the inline JSON otherwise. The subprocess is spawned **in a new process group** (`detached: true` on Node's `child_process.spawn`, which calls `setsid()` on POSIX) so all MCP child processes the engine launches inherit the same group ID. See SC-B disposition below.

3. **Starts a 2-second activity ticker.** `setInterval(() => yieldQueue.push({type:'activity'}), 2000)`. The ticker yields `{type:'activity'}` events into the iterable's queue while the subprocess is running. This preserves NC's stuck-detection signal (`poll-loop.ts:359-361`) without any engine-side cooperation.

4. **Accumulates stdout into a string buffer.** The wrapper does **not** parse stdout line-by-line (the engine emits the JSON envelope only at exit, so there is nothing to stream). Stderr is captured into a separate buffer for diagnostic surfacing on failure. The §4.0 stdout-discipline guarantee means the wrapper can trust stdout to contain exactly one JSON document; it does not need to deal with interleaved log lines.

5. **On subprocess exit** (per the SC-D envelope-precedence rule in §4.4):
   - Stops the activity ticker.
   - **If stdout parses as a valid §4.1 envelope**: trust the envelope. If `envelope.error == null`, yield `{type:'result', text: envelope.reply}`. If `envelope.error != null`, synthesize the wrapper-side `AaaError` from the envelope's `error` field and yield `{type:'error', ...mapped fields, correlationId: envelope.error.correlationId, stderrTail: redactedStderrTail}`. The exit code is logged at debug-stderr for correlation but does not override the envelope (e.g., engine returning exit 1 with `error: null` → yield `result`).
   - **If stdout is empty or unparseable**: synthesize an `AaaError` from the exit code per §4.4 rule 2 (e.g., exit 0 + no envelope → `code: "envelope_missing"`, classification `protocol`; non-zero + no envelope → `code: "engine_exit_<code>"`, classification `engine`); yield `{type:'error', ...}`.
   - **If subprocess hangs** past the timeout: invoke `cancel()` (SC-B PGID-aware kill); synthesize `{type:'error', code: 'engine_hung', classification: 'engine', ...}`; yield.
   - The iterator returns (ends).

**Subprocess group lifecycle (SC-B)**:

The engine launches MCP servers via `tool-mcp.mount()`. Each MCP server is itself a child subprocess (stdio MCP servers spawn `node`/`python`/etc.). If the wrapper kills only the engine PID, the engine's children are orphaned and become zombies — they keep listening on file descriptors, keep holding ports/sockets, and leak resources until the OS reaper cleans them up minutes later.

The fix is signal-the-group-not-the-pid:

```typescript
// Wrapper side
this.subprocess = spawn(binaryPath, argv, {
  detached: true,                      // create new session group (POSIX setsid)
  stdio: ['ignore', 'pipe', 'pipe'],
  env: this.subprocessEnv,
  cwd: this.cwd,
});
// Do NOT call .unref() — we want to wait for exit synchronously in cancel().

async cancel(): Promise<void> {
  if (!this.subprocess || this.subprocess.exitCode !== null) return;
  const pgid = this.subprocess.pid;    // pid == pgid because detached: true
  try {
    process.kill(-pgid, 'SIGTERM');    // negative pid = signal the whole group
  } catch (e) { /* ESRCH if already dead — ignore */ }
  await waitForExitOrTimeout(this.subprocess, 5000);
  if (this.subprocess.exitCode === null) {
    try {
      process.kill(-pgid, 'SIGKILL');
    } catch (e) { /* ESRCH — ignore */ }
  }
}
```

Engine side: the engine's `run` subcommand calls `os.setsid()` at entry (if not already the session leader; idempotent check via `os.getsid(0) == os.getpid()`). All MCP child processes the engine spawns via `tool-mcp.mount()` inherit this session group. On normal exit, `tool-mcp.unmount()` SIGTERMs all child MCP processes via the same group-signal pattern. An `atexit` handler ensures stragglers are reaped even on uncaught exception.

The conformance fixture `mode-a-orphan-cleanup.yaml` (§8.1 A4') verifies: spawn engine with an MCP server configured; SIGTERM the wrapper's process; assert (via `pgrep -P` or `ps`) that all MCP child processes are dead within the grace window.

```typescript
class SessionHandle {
  // ... constructor stores state from spawnAgent ...

  submit(prompt: string): AsyncIterable<DisplayEvent> {
    if (this.lifecycleCompleted) {
      throw new AaaError("lifecycle_unsupported", "submit() called twice on a one-shot SessionHandle");
    }
    this.lifecycleCompleted = true;

    return makeSubmissionIterable({
      sessionId: this.sessionId,
      argv: this.assembleArgv(prompt),
      env: this.subprocessEnv,
      cwd: this.cwd,
      mcpSpillPath: this.mcpSpillPath,   // set if CR-A spill was used; used by try/finally cleanup
    });
  }

  async cancel(): Promise<void> {
    // SC-B: signal the process group, not just the engine PID.
    // SIGTERM → 5s grace → SIGKILL pattern preserved from D3 (2026-05-20).
    if (this.subprocess && this.subprocess.exitCode === null) {
      const pgid = this.subprocess.pid;
      try { process.kill(-pgid, 'SIGTERM'); } catch (e) { /* ESRCH */ }
      await waitForExitOrTimeout(this.subprocess, 5000);
      if (this.subprocess.exitCode === null) {
        try { process.kill(-pgid, 'SIGKILL'); } catch (e) { /* ESRCH */ }
      }
    }
    // CR-A: clean up the tmpfile if one was created for MCP spill.
    if (this.mcpSpillPath) {
      try { await fs.unlink(this.mcpSpillPath); } catch (e) { /* ENOENT — already gone */ }
    }
  }

  async dispose(): Promise<void> { return this.cancel(); }

  getEngineInfo(): EngineInfo {
    // Populated lazily after first submit() exits — reads from the parsed JSON envelope's metadata.
    return this.engineInfo;
  }
}
```

### 5.3 The approval-handler API surface — rejected loudly in v1 (SC-C)

`SpawnAgentParams.approval?.onRequest` (per `wrappers/typescript/src/index.ts:56-59`) remains in the type for forward compatibility with v1.x revival paths, but **passing a non-null `onRequest` callback at `spawnAgent` time is rejected loudly in v1**. The earlier draft of this amendment had the wrapper accept the callback and log a stderr warning; the SC-C adversarial review found that warning-only acceptance ships silent auto-allow to a host author who believed their callback was wired up. That is exactly the failure mode the v1 architecture is trying to avoid.

The amended behavior:

```typescript
export async function spawnAgent(params: SpawnAgentParams): Promise<SessionHandle> {
  // ... lifecycle / binary / env validation as before ...

  // SC-C: reject mid-turn approval callback before any subprocess work is done.
  if (params.approval?.onRequest !== undefined) {
    throw new AaaError({
      code: 'approval_not_supported_in_v1',
      classification: 'protocol',
      severity: 'error',
      message:
        "Mid-turn approval callbacks (params.approval.onRequest) are not supported in v1. " +
        "The Mode A wire has no mid-turn request channel. The bundle's hooks-approval mount " +
        "is the v1 policy point — auto-approve by default, configurable per-tool via the " +
        "bundle's hooks-approval default-mode and gating settings. To customize approval " +
        "policy in v1, configure the bundle; do not pass an onRequest callback. " +
        "Mid-turn callbacks will return in v1.x — track WG-4 (§6).",
    });
  }

  // ... rest of spawnAgent: mint sessionId, construct SessionHandle ...
}
```

The throw happens **before** the subprocess is spawned and before any resources are allocated. The host author sees the failure immediately at `spawnAgent()` call time — not at some downstream point where a tool was supposed to prompt but silently auto-allowed.

For hosts that genuinely want auto-approve (NC's posture per A10), the v1 path is: configure the bundle's `hooks-approval` to allow-all in its default mode, do NOT pass `params.approval`. The wrapper documents this in the user-facing JSDoc on `SpawnAgentParams`:

```typescript
export interface SpawnAgentParams {
  // ... other fields ...

  /**
   * Mid-turn approval callback.
   *
   * **NOT SUPPORTED IN v1.** Passing a non-null `onRequest` throws
   * `AaaError(approval_not_supported_in_v1)` at spawnAgent() time. The v1 wire
   * is Mode A (per-turn subprocess); there is no mid-turn host channel.
   *
   * For v1, configure approval policy at the bundle layer via hooks-approval's
   * default-mode and per-tool gating. Mid-turn callbacks return in v1.x — see
   * the amendment §6 WG-4 deferral nominees for the planned revival path.
   */
  approval?: { onRequest?: (req: ApprovalRequest) => Promise<ApprovalResponse> };
}
```

The conformance fixture `mode-a-approval-callback-rejected.yaml` (§8.1 A4') verifies: caller passes `approval.onRequest`; assert `spawnAgent` throws `AaaError` with `code === 'approval_not_supported_in_v1'`; assert no subprocess is spawned (check no `amplifier-agent` process started).

This preserves the four-host runway analysis exactly — when a future host needs mid-turn approval, the callback's *type* is in place and the v1.x revival just removes the throw and wires up the transport (option (a) or (b) per §2.3 / §6 WG-4) without API churn at the type signature.

### 5.4 Buffer chain (D5) preserved with subprocess re-spawn

The B1 buffer chain logic at NC's adapter layer (locked design §4.1.3 and §4.1.4) is **unchanged**. Each chain link calls `spawnAgent({sessionId: same, resume: true})` followed by one `submit(bufferedText)`. Under the amendment, this becomes one `amplifier-agent run --session-id same --resume "<bufferedText>" --output json` subprocess per chain link.

The chain vocabulary from D5 ("one NC `query()` = one wire-session; each chain link = wire-level turn within session") is preserved. The wire-level turn now happens to be one subprocess invocation; the session continuity comes from the engine's `SessionStore` (CR-1) reading `transcript.jsonl` on `--resume`.

### 5.5 Net wrapper LOC change

Removed:
- `wrappers/typescript/src/jsonrpc.ts` (the JsonRpcClient) — ~250 LOC deleted. The amendment doesn't need request/response correlation, notification fanout, or server-initiated request routing.
- `wrappers/typescript/src/version.ts` — `checkProtocolVersion` becomes unused at the wrapper level (engine self-validates via `--protocol-version`); the file is kept but `checkProtocolVersion` is no longer called from `spawnAgent`. ~50 LOC effectively dead.

Added:
- `wrappers/typescript/src/run-output-parser.ts` — JSON envelope parsing + error synthesis. ~100 LOC.
- `wrappers/typescript/src/mcp-spill.ts` — large-payload tmpfile spill for `--mcp-servers`. ~40 LOC.
- `wrappers/typescript/src/argv-builder.ts` — `assembleArgv` and argument escaping. ~80 LOC.

Net wrapper delta: ~200 LOC deleted, ~220 LOC added — roughly net-zero but the shape changes from "stream consumer" to "subprocess driver". The Python wrapper parity changes are symmetric.

### 5.6 Transport.ts becomes simpler

`wrappers/typescript/src/transport.ts` already wraps Node's `child_process.spawn`. The amendment keeps it; removes the stdin/stdout JSON-RPC framing logic (no longer needed). The transport's `terminate()` (SIGTERM + 5s grace + SIGKILL) is the D3 cancel mechanism (locked design 2026-05-20 D3) — preserved verbatim.

### 5.7 Python wrapper parity

All TS wrapper changes mirror onto `wrappers/python/src/*.py`. The `asyncio.subprocess` machinery already exists. The Python parity changes are symmetric to the TS changes above.

---

## §6 What's deferred to v1.x — expanded wire-gap log  <a id="s6-deferrals"></a>

Replaces and extends Appendix D of the 2026-05-22 design with new entries covering the capabilities Mode B would have carried.

| ID | Gap | v1 disposition | Future nominee |
|---|---|---|---|
| **WG-1** | Steering mid-turn (NC `push()` cannot interrupt an in-flight turn at the wire level). | **Carried forward unchanged from 2026-05-22.** Adapter workaround: B1 buffer + multi-turn-in-session chaining (D5 preserved). Latency cost: wait for current turn boundary. Monitored via R2. | `turn/inject` JSON-RPC notification (D-v1.x-01). Requires a long-lived dispatcher (i.e., re-introducing Mode B). Promotion gated by R2 trigger OR any second host requesting it. |
| **WG-2** | Host-supplied MCP servers. | **Closed in v1** by `--mcp-servers '<json-or-@path>'` argv flag (D9 amended; mechanism shift only). | (Closed — not a future deferral.) |
| **WG-3** ⚠ **NEW (amendment)** | Mid-turn `display/event` streaming (granular `assistant/text` deltas, separately-emitted `tool/started`/`tool/completed`). | **Eliminated from v1 wire** (D3 amended). Wrapper synthesizes `{type:'activity'}` every 2s. NC does not surface granular events; no v1 host does. | `--output streaming-json` flag emitting NDJSON of events to stdout while the engine continues running; wrapper parses line-by-line. The locked 9-event taxonomy from 2026-05-20 §4.4 is the starting schema. Promotion gated by any host needing live mid-turn surfacing (e.g., a chat UI that wants to show tool calls as they happen). |
| **WG-4** ⚠ **NEW (amendment)** | Mid-turn `approval/request`/`approval/response` round-trip. | **Eliminated from v1 wire** (D4 amended). Bundle's `hooks-approval@v0.1.0` auto-approves in v1; NC's `bypassPermissions` parity (A10) is preserved at the bundle layer. | **Two nominees, both compatible with the per-turn subprocess model:** (a) Out-of-band subprocess approval — engine pauses on gated tool, writes pending-approval state to session-store, exits with code 3; host invokes `amplifier-agent approve --session-id X --decision <allow\|deny>` then re-runs. (b) Webhook callback flag — engine receives `--approval-callback-url https://host/approve` via argv; POSTs `{tool, args, sessionId, requestId}` on gated tool; waits up to `timeoutMs` for `200 {decision}`. Promotion gated by any host needing mid-turn UI approval prompts. |
| **WG-5** ⚠ **NEW (amendment)** | Sub-agent progress events. | **Deferred to v1.x** (preserved unchanged from SC-5; gap moves from "collapsed to activity" to "eliminated entirely" but the operator-facing outcome is identical). | Same as WG-3 — would ride on `--output streaming-json`. Promotion gated by NC UX request to show sub-agent activity. |
| **WG-6** ⚠ **NEW (amendment)** | Long-running interactive (REPL-style) sessions where a single subprocess accepts multiple prompts and emits events between them. | **Out of scope for v1 and v1.x.** Would require re-introducing Mode B or its equivalent. | Re-evaluate if a host needs it. The amendment's per-turn-subprocess model preserves session continuity via on-disk transcript replay; this is sufficient for chat-cadence use. REPL-cadence use is a different system. |

### 6.1 Why these are safe deferrals

All four amendment-introduced deferrals (WG-3, WG-4, WG-5, WG-6) share one property: **no host on the four-host v1 runway consumes them**. NC's auditable behavior at `container/agent-runner/src/poll-loop.ts:343-471` doesn't surface mid-turn events or invoke mid-turn approval. PC's `claude-local` and `codex-local` adapters spawn fresh subprocesses per `execute(ctx)` (2026-05-20 design Appendix B B4) — they don't have a persistent channel for mid-turn events either. OpenCode and Claude Code adapters haven't been authored; the four-host runway analysis preserved capability flags so each can opt-in to whatever subset it needs.

If a host actually needs WG-3 or WG-4 in the future, the amendment's wrapper-layer surface is already set up for it — the `SpawnAgentParams.approval.onRequest` callback is preserved; the `DisplayEvent` taxonomy is preserved at the type level. The transport under those types can be rebuilt without API churn at the host-adapter boundary.

### 6.2 Other 2026-05-22 v1.x deferrals carried forward unchanged

The 2026-05-22 Appendix A table of 12 v1.x deferrals (D-v1.x-01 through D-v1.x-12) is preserved unchanged. The amendment adds three new entries (D-v1.x-13 = WG-3, D-v1.x-14 = WG-4, D-v1.x-15 = WG-5/WG-6) but does not modify the existing entries. The promotion triggers and capability flag count (R7) policy carry over.

---

## §7 Impact on the locked design's Phase 1-7 dispositions  <a id="s7-impact"></a>

Walks each major disposition from the 2026-05-22 design and states whether it's preserved, preserved-with-shift, obsolete, or modified.

### 7.1 Critical risks (CRs)

| ID | Original disposition | Status under amendment |
|---|---|---|
| **CR-1** | Bundle pointed at wrong context module; canonical pattern is context-simple + app-layer SessionStore + IncrementalSaveHook (per amplifier-app-cli). Closed in 2026-05-22 §4.6. | ✅ **Preserved unchanged.** The session storage work is at the engine's internal layer, completely independent of the wire transport. SessionStore loads transcript at `amplifier-agent run` startup; IncrementalSaveHook flushes after every `tool:post`. Both happen exactly the same way under the amendment. **Note**: the Defect-A and Defect-C wiring fixes from debug cycle 2 (commits `79d8726`, `c3f9177`) still apply unchanged; they live in `_runtime.py`, not in any wire-shaped surface and not in `DisplayEvent` shape. The CR-C breaking change to `DisplayEvent` is orthogonal to CR-1's resume-wiring repair. |
| **CR-2** | `WireApprovalProvider` shim with three typed error codes (`approval_translation_failed`, `approval_timeout`, `approval_protocol_violation`). Closed in 2026-05-22 §4.7. | ✅ **Preserved with mechanism shift.** The shim class is preserved; the three error codes are preserved; the typed surface is preserved. What changes: the codes are surfaced via the JSON output envelope's `error.classification = "approval"` (§4 schema) rather than as JSON-RPC error responses mid-stream. NC's `event-translator.ts` mapping table (locked design §4.2) operates on `AaaError.code` regardless of how the wrapper synthesized it; no NC-side change. |
| **CR-3** | stderrTail redaction at NC's `event-translator.ts` when `mcp-translator.ts` declared MCP config was supplied. Closed in 2026-05-22 §4.2. | ✅ **Preserved unchanged.** Redaction operates on the `stderrTail` field of any `AaaError`-shaped object NC receives. Whether the wrapper synthesized that from a mid-stream JSON-RPC error or from a JSON envelope's `error` field, the redaction logic is identical. The keys NC redacts against are still the MCP env keys it declared. |
| **CR-4** | B1 buffer cap 256 with visible drop; overflow emits `progress` event. Closed in 2026-05-22 §4.1.3. | ✅ **Preserved with mechanism shift.** The buffer is now a TypeScript array accumulated between `push()` calls on the SessionHandle. Overflow still emits `{type:'progress', message:'buffer overflow: N messages dropped'}` from the wrapper synthesizing the event into the iterable's queue at chain-link boundary. R8 monitor is preserved. |

All four critical risks remain closed. None depended on Mode B as such — they depended on layers (engine internals, the typed shim, the redaction logic, the buffer policy) that are orthogonal to the wire transport.

### 7.2 Significant concerns (SCs)

| ID | Original disposition | Status under amendment |
|---|---|---|
| **SC-1** | Activity ticker could fire before `init` if generator order was wrong. Adapter generator gates ticker until after `init` yield (2026-05-22 §4.1.4). | ✅ **Preserved.** Now even simpler: the wrapper yields `init` synchronously before spawning the subprocess. There is no race window — `init` is the first thing the iterable yields, ticker doesn't start until after. |
| **SC-2** | `hooks-logging` mount removed from bundle (ephemeral in-container path). | ✅ **Preserved unchanged.** Bundle composition is unchanged. |
| **SC-3** | `BLOCKED_ENV_KEYS` validation in `buildEnv()` rejects `PYTHONPATH`/`LD_PRELOAD`-style injections. Closed in 2026-05-22 §4.12.1. | ✅ **Preserved unchanged.** `buildEnv` still runs at wrapper spawn time, before subprocess launch. The amendment's `--env-extra` flag is diagnostic only — the wrapper passes the validated subset. |
| **SC-4** | `doctor --emit-sha` for future SHA-pinning. | ✅ **Preserved unchanged.** |
| **SC-5** | Sub-agent progress collapsed to `activity`. | ✅ **Preserved with mechanism shift.** Sub-agent events are now eliminated entirely from the v1 wire (per WG-3/WG-5 in §6). NC's translator already collapses them to `{type:'activity'}`; the wrapper's 2-second ticker substitutes equivalently. Operator-facing outcome identical. |
| **SC-6** | Conformance fixtures (4 new). | 🔄 **Modified, expanded.** The four 2026-05-22 fixtures (`initialize-with-mcpservers.json`, `initialize-with-host-capabilities.json`, `approval-shim-three-error-codes.json`, `resume-with-session-store.json`) assumed Mode B's JSON-RPC fixture format and a mock JsonRpcServer; they are deleted. The amendment replaces them with **ten subprocess-driver fixtures, all launching the real `amplifier-agent` binary** (CR-D — see §8.1 A4' roster for the full list). Mocks are permitted only at the provider-LLM HTTP boundary. This closes the R9' "specced but unimplemented" failure mode that the earlier draft's "at least one fixture" wording would not have caught. |
| **SC-7** | Async `probeEngineVersion`. | ✅ **Preserved unchanged.** Still useful for diagnostic surfacing; no longer load-bearing for protocol-skew detection (engine self-validates), but harmless to retain. |

### 7.3 R-class risks (R1-R8)

| ID | Original | Status |
|---|---|---|
| **R1** | Spawn-time latency cliff dominates short-turn UX (per 2026-05-22 §6.1). Trigger: `aaa.turn.spawn_ms` P50 > 5s. | ✅ **Preserved.** Under the amendment, spawn cost is per turn (same as locked design). MCP server cold-start now happens per turn too — small added cost. |
| **R2** | Buffer-chain latency drift on long steering bursts. | ✅ **Preserved unchanged.** B1 chain semantics carry forward. |
| **R3** | MCP secrets leak through engine stderr. | ✅ **Preserved unchanged.** CR-3 redaction logic unchanged. |
| **R4** | Session-file durability across container restart. | ✅ **Preserved unchanged.** |
| **R5** | Protocol version skew between wrapper pin and binary install. | ✅ **Preserved with mechanism shift.** Engine self-validates `--protocol-version`. CI lint at NC (`scripts/lint-aaa-version.ts`) remains the build-time gate. |
| **R6** | Bundle module sources pinned to `@main`. | ✅ **Preserved unchanged.** |
| **R7** | Capability flag sprawl. | ✅ **Preserved unchanged.** Capability flags now travel via `--host-capabilities`; count budget identical. |
| **R8** | B1 buffer cap reached repeatedly. | ✅ **Preserved unchanged.** |

### 7.4 New risks specific to the amendment

Three new R-class risks introduced by the Mode A pivot. Each is monitored at the same disposition level as R1-R8.

| # | Risk | Trigger / monitoring signal | Disposition | Trigger to revisit |
|---|---|---|---|---|
| **R6'** ⚠ NEW | Per-turn MCP server respawn cost. Each turn pays 100ms-2s for MCP server cold start. NC's `nanoclaw_send_message` server is a Node script that boots quickly; others (e.g., a Python-based MCP server) could be slower. | `aaa.turn.mcp_spawn_ms` per-server P50 > 1s for 3+ days | Accept; monitor. v1 host (NC) uses fast-spawning servers. | P50 > 2s sustained → introduce long-lived MCP gateway (engine-side persistent MCP daemon that survives across turns). v1.x option. |
| **R7'** ⚠ NEW | argv overflow on large MCP configs. OS argv limits vary (Linux: 128KB-2MB; macOS: 1MB; Windows: 32KB pre-long-paths). | `--mcp-servers` payload size > 8KB. | Closed by `@path` form (tmpfile spill); wrapper auto-spills above 8KB threshold. | Any "argument list too long" runtime errors → tighten spill threshold. |
| **R8'** ⚠ NEW | Caller config drift between turns within a single NC `query()`. The B1 chain semantics mean turn N and turn N+1 are different subprocess invocations; if NC's adapter assembles different `--mcp-servers` or `--host-capabilities` between links, the second turn sees a different engine config. | **Primary signal source: the per-turn audit files written by A2.1' (SC-H).** Each turn writes `$XDG_STATE_HOME/amplifier-agent/sessions/<sid>/audits/turn-<turnId>.json` containing digests of argv, MCP config, host capabilities, env. Comparing successive audit files within one session surfaces drift. Secondary signal: wrapper logs a stderr-warning if the assembled-argv hash changes between `submit()` calls on the same SessionHandle. | Accept; document "caller is source of truth on every turn"; wrapper warns on hash change. | v1.x: `--validate-config-stability` flag that makes the engine reject if a resumed session sees different config. Promotion gated by any observed drift in the audit-file diff. |
| **R9'** ⚠ NEW | Re-locking the design without an integration test gate. The 2026-05-22 design locked Mode B as a wire surface; the implementation didn't ship; no test caught the gap until DTU verification weeks later. The amendment must not repeat this failure mode. | Any DTU verification failure on the amended Mode A wire surface. | Closed by adding a real-binary integration fixture (at least one) to SC-6's conformance suite; SDD recipe gate includes "real binary launch passed". | Any future DTU finding of a "specced but unimplemented" surface → escalate to design-process audit. |

### 7.5 Empirical-grounding assumptions (A1-A14)

All 14 assumptions from 2026-05-22 §2 carry forward, with one strengthening:

- **A0 (from 2026-05-20)**: One-shot per turn is sufficient for v1. **Strengthened**: not just at the host boundary, but at the wire transport too. The wire commits to one-shot per subprocess invocation; mid-turn bidirectional channels are deferred.
- A1-A14: unchanged.

---

## §8 Migration plan  <a id="s8-migration"></a>

Replaces §10.2 (amplifier-agent stages) and §10.3 (NC stages) of the 2026-05-22 design with the amended sequence. Stages are stated at the strategic level; implementation-plan details (test scaffolding, commit sequencing, per-file LOC) live in the separate implementation plan authored after this amendment is locked.

### 8.1 Amplifier-agent repo stages (ships first)

| ID | Stage | What |
|---|---|---|
| **A1'** | Extend Mode A CLI surface | Add `--mcp-servers`, `--host-capabilities`, `--env-allowlist`, `--env-extra`, `--protocol-version`, `--output {text\|json}` argv flags to `src/amplifier_agent_cli/modes/single_turn.py`. Parse and validate per §3. Backward compat: `--output` defaults to `json` (preserves existing scripts; see §3.3); existing flags preserved verbatim. Sub-tasks: (1) implement click JSON-parse callbacks for `--host-capabilities`, `--env-extra`, and the inline form of `--mcp-servers` — malformed JSON maps to typed `AaaError(argv_json_malformed)` envelope with exit 2 (O2'). (2) Pin to the existing `run` entry point — the amendment extends `single_turn.py`, no new module created (O4'). (3) Emit a stderr-debug counter recording the actual argv size in bytes on every spawn (O3' instrumentation). (4) PGID setup at engine entry: call `os.setsid()` if not already session leader; this ensures MCP child processes spawned via `tool-mcp.mount()` inherit the engine's session group so the wrapper's group-signal cleanup (SC-B) reaps them. (5) On `tool-mcp.unmount()` and the engine's `atexit` handler, send `SIGTERM` to the session group to reap any stragglers — belt-and-suspenders against MCP servers that ignore individual `terminate()` calls. |
| **A2'** | Mode A v2 JSON output | When `--output json`, emit the structured envelope per §4. Map engine internal state (sessionId, turnId, reply, error, metadata) to the schema. Implement stdout-discipline guard (CR-B / §4.0): save the real stdout FD at entry, wrap turn execution in `contextlib.redirect_stdout(sys.stderr)`, write the envelope only via the saved FD. Mint `metadata.correlationId` as UUID v4 at run start; log it at stderr-debug; thread it onto every envelope (success and failure paths) per SC-G. Hand-author `schemas/run-output.json` (or extend `wrappers/_gen.py` if practical). Exit code policy per §4.4. |
| **A2.1'** | Per-turn audit trail (SC-H) | After the envelope is written (success or failure), the engine writes a structured audit file to `$XDG_STATE_HOME/amplifier-agent/sessions/<sessionId>/audits/turn-<turnId>.json` before process exit. The audit contains: argv digest (sha256 of canonicalized argv with secret values masked), MCP servers digest (sha256 of the parsed config), host capabilities, env allowlist + env extra digest (sha256 of the value strings), protocol version, exit code, envelope `metadata.correlationId`, start timestamp, end timestamp. **Secrets are digested, never persisted as literals.** This gives an operator the forensic trail to answer "what did session X run with at turn N" without capturing argv at the wrapper layer. The audit is also the primary signal source for R8' caller-config-drift detection — comparing successive audit files within a session surfaces drift. |
| **A3'** | Rewrite wrapper as subprocess driver | TypeScript: replace `wrappers/typescript/src/jsonrpc.ts` consumers with subprocess spawn + JSON parse per §5. Apply the CR-C breaking change to `DisplayEvent` in `wrappers/typescript/src/session.ts:26-37` per the type defined in §5.2. Apply the SC-C reject-loudly logic to `spawnAgent` per §5.3. Apply the SC-B PGID-group spawn + group-signal cleanup per §5.2 (Node's `child_process.spawn({detached: true})`). Apply the CR-A secret-aware MCP spill per §2.5 and §3.2 (`wrappers/typescript/src/mcp-spill.ts`). Preserve `SpawnAgentParams`/`SessionHandle` entry-point names and `AaaError` taxonomy verbatim. Python parity in `wrappers/python/src/*.py`. |
| **A4'** | Conformance fixtures + integration gate (CR-D) | **Every conformance fixture launches the real `amplifier-agent` binary as a subprocess.** No mocks of the wrapper's transport layer. No mocks of the engine's argument parsing or envelope emission. Mocks are permitted only at the provider-LLM HTTP boundary — a deterministic mock LLM server that responds to Anthropic-shaped requests with scripted completions. The four 2026-05-22 mock-based fixtures are deleted. The ten fixtures listed below are authored. Estimate: each fixture costs ~30-60s of CI time; +1 day to the migration window (A4' grows from ~1 day to ~2 days). |
| **A5'** | Release v0.2.0-amend | Tag; PyPI publish (engine); npm publish (`amplifier-agent-client-ts`). Smoke test from clean install. |

**A4' conformance fixture roster** (CR-D — required, all real-binary):

| Fixture | Asserts |
|---|---|
| `mode-a-happy-path.yaml` | Clean turn with no MCP, no resume. Envelope schema valid; `error: null`; reply matches mock LLM's scripted response. |
| `mode-a-mcp-injection.yaml` | Turn with two MCP servers — one with empty `env` (inline form), one with non-empty `env` (CR-A spilled form). Assert: (a) inline server's argv is visible via `ps`; (b) `env`-bearing server's argv shows `@<path>` not the secret; (c) the tmpfile exists at `${XDG_RUNTIME_DIR:-/tmp}/amplifier-agent/<sid>/mcp.json` with mode `0600`; (d) the tmpfile is deleted after subprocess exit (try/finally cleanup). |
| `mode-a-resume-continuity.yaml` | Turn 1 plants a fact via mock LLM ("remember purple"); turn 2 resumes the same session, asks for recall; envelope's reply contains "purple". Verifies SessionStore wiring through the real engine binary. |
| `mode-a-host-capabilities.yaml` | Turn with `--host-capabilities '{"supports_steering":false,"supports_structured_errors":true}'`; assert the envelope's `metadata.hostCapabilities` echoes back what was passed; assert the engine internal state reflects it (via a probe event or audit file). |
| `mode-a-protocol-skew.yaml` | Wrapper passes `--protocol-version 9.9.9-NOT-REAL` without `--allow-protocol-skew`; assert envelope's `error.code === "protocol_version_mismatch"`, `classification === "protocol"`, `error.remediation` populated with self-remediating reinstall commands; exit code 2. |
| `mode-a-error-taxonomy.yaml` | Bundle hook deliberately raises a non-approval exception; assert envelope's `error.classification === "engine"`, `error.code` matches the expected mapping, `error.correlationId` is a UUID v4, `error.stderrTail` populated. |
| `mode-a-stdout-discipline.yaml` (CR-B) | A bundle module emits 50 lines of `print()` during the turn (simulating a noisy module). Assert: (a) the wrapper's `JSON.parse(stdout)` succeeds; (b) the 50 print lines appear in the captured stderr buffer, not stdout. Real engine binary; NO mocks of the redirect_stdout machinery. |
| `mode-a-approval-callback-rejected.yaml` (SC-C) | Caller invokes `spawnAgent({approval: {onRequest: () => ...}})`; assert `spawnAgent` throws `AaaError({code: 'approval_not_supported_in_v1'})`; assert no `amplifier-agent` process is spawned (`pgrep -f 'amplifier-agent run'` returns nothing). |
| `mode-a-orphan-cleanup.yaml` (SC-B) | Wrapper spawns engine with a stdio MCP server configured (a slow-to-shutdown server that ignores `SIGTERM` for 10s); wrapper invokes `cancel()` mid-turn. Assert: (a) all MCP child processes (`pgrep -P <engine-pid>`) are dead within the 5s+grace window via group SIGKILL; (b) no orphan zombies remain after the wrapper exits. |
| `mode-a-envelope-precedence.yaml` (SC-D) | Engine is configured (via a test-only flag wired through the bundle) to exit with code 1 while emitting a valid envelope where `error: null`. Assert the wrapper yields `{type:'result', text: ...}` based on the envelope, NOT `{type:'error'}` based on the exit code. Verifies envelope-wins precedence. |

Internal effort estimate: ~8-10 working days for a single engineer (was ~7-9; +1 day for the larger fixture set).

### 8.2 NanoClaw repo stages (consumes amplifier-agent v0.2.0-amend)

| ID | Stage | What |
|---|---|---|
| **N1'** | Rebuild NC adapter against new wrapper | NC's `container/agent-runner/src/providers/amplifier-agent.ts` consumes the unchanged `SpawnAgentParams`/`SessionHandle` surface. The adapter file itself **does not change** because the wrapper's public API is preserved verbatim. Only the runtime behavior under that API changes (the wrapper now spawns subprocess per submit() instead of holding one open). |
| **N2'** | Update `package.json` pin | Pin `amplifier-agent-client-ts` to `^0.2.0-amend`. |
| **N3'** | Re-run NC's bun:test suite | Existing unit tests for translator helpers should pass unchanged (pure functions). |
| **N4'** | Re-DTU end-to-end | Launch DTU profile, point NC at the amended amplifier-agent, run a real conversation through Slack channel + NC + `amplifier-agent`. Verify: (a) reply reaches user via `mcp__nanoclaw__send_message`; (b) resume continuity; (c) MCP server passthrough; (d) buffer chain on `push()` mid-turn. |

Internal effort estimate: ~3 working days.

### 8.3 Critical path

```
A1' → A2' → A3' → A4' → A5' → N2' → N3' → N4'
                  ↑
                  └── A4' includes real-binary integration gate (R9')
```

Total: **~10-12 working days, single engineer, ~3 weeks calendar**. Compared to the 2026-05-22 design's ~31 working days / ~8 weeks (which assumed Mode B was being implemented), the amendment cuts critical path by 2-3×. The reduction is concentrated in the engine layer (no stdio JSON-RPC dispatcher to author; no streaming hook to author) and the wrapper layer (no JsonRpcClient; no event-stream consumer).

### 8.4 Rollback plan

Same as 2026-05-22 §10.5: the amendment's changes are additive at the CLI argv surface (new flags) and at the wrapper internals (new subprocess driver). If a regression appears post-release:
- Revert `wrappers/typescript/` to the prior version pinned in `package.json`. NC's adapter is unchanged at the API level; reverting the wrapper restores prior behavior.
- The engine's new argv flags are optional; reverting the engine to a prior version that doesn't recognize `--mcp-servers` / `--host-capabilities` / `--env-allowlist` / `--env-extra` / `--protocol-version` / `--output` breaks the wrapper's spawn but is recoverable by pinning the wrapper to a prior version that doesn't pass those flags.

### 8.5 What does not change

- Bundle composition (CR-1, SC-2 already applied) — unchanged.
- Engine's internal layer (`session_store.py`, `incremental_save.py`, `_runtime.py` resume wiring, `tool-mcp.mount(config=...)` threading) — unchanged.
- The NC adapter file (`container/agent-runner/src/providers/amplifier-agent.ts` and helpers) — unchanged at the source level because the wrapper API is preserved.
- The Dockerfile (`UV_TOOL_BIN_DIR=/usr/local/bin uv tool install` per D10; `prepare`+`doctor --strict` per D11) — unchanged.
- The CI lint (`scripts/lint-aaa-version.ts` per A4) — unchanged.

---

## §9 Risks of the amendment  <a id="s9-risks"></a>

Three risks specific to the Mode A pivot. The full risk register (R1-R8 from 2026-05-22, R9-R11 added here) and the four CR closures (CR-1 through CR-4) are stated in §7 above.

### 9.1 R6' — Per-turn MCP server respawn cost

**The risk**: Each turn pays 100ms-2s for MCP server cold start. NC's `nanoclaw_send_message` server is a Node script that boots quickly (~50ms observed); MCP servers that wrap heavier runtimes (Python-based servers, servers with first-call network setup) could be slower. Locked Mode B would have kept the engine subprocess alive across turns, amortizing MCP server cold start across many submits.

**Mitigation**: at v1 scale (chat cadence — minutes between turns) the per-turn MCP cold start is acceptable. NC's reply-channel MCP server is fast. v1.x option: introduce a long-lived MCP gateway — engine-side persistent MCP daemon, indexed by sessionId, that survives across turns and is invalidated when the bundle digest changes.

**Monitoring**: `aaa.turn.mcp_spawn_ms` per-server P50; alert at > 2s sustained over 3 days.

### 9.2 R7' — Large CLI invocations with many MCP servers

**The risk**: OS argv limits are bounded. Linux: typically 128KB-2MB. macOS: ~1MB. Windows: 32KB without long-path support. A pathological MCP config with 50+ servers could exceed argv limits.

**Mitigation**: `--mcp-servers @<path>` form. Wrapper auto-spills payloads > 8KB to a temp file with mode 0600; passes `@<tmpfile>`; deletes after subprocess exit. Threshold (8KB) is well below the tightest OS limit and well above any realistic v1 config.

**Monitoring**: `aaa.mcp_spawn.spill_count`; alert at > 1% spill rate (indicates configs are larger than expected).

### 9.3 R8' — Caller config drift between turns

**The risk**: B1 chain semantics mean each chain link is a separate subprocess. If the host's adapter (NC's `AmplifierAgentQuery.makeEvents`) assembles different `--mcp-servers` between links of the same `query()` call, the second turn sees a different engine config than the first. The engine has no way to detect this — it accepts whatever it's invoked with.

**Mitigation**: documented as "caller is source of truth on every turn" (the same posture Claude Code's CLI takes for `--resume` invocations). Wrapper logs a stderr warning if the assembled-argv hash changes between `submit()` calls on the same SessionHandle. v1.x option: `--validate-config-stability` flag that makes the engine reject if a resumed session sees a substantively different config.

**Monitoring**: stderr scan for the wrapper-side warning; alert if observed.

### 9.4 R9' — Re-locking without integration test gate

**The risk**: the 2026-05-22 design locked Mode B without an integration test that exercised it against the real engine binary. The four conformance fixtures (2026-05-22 §4.14) were JSON-RPC fixtures that would have run against a mock JsonRpcServer, not the real `amplifier-agent run --stdio` subprocess. DTU verification weeks later exposed the gap. The amendment must not repeat this.

**Mitigation**: at least one A4' conformance fixture must launch the real engine binary (not a mock) — typically a resume-continuity scenario (two sequential invocations on the same sessionId, assert transcript replay). SDD recipe gate updated to include "real binary launch passed" as a check before declaring a phase complete.

**Monitoring**: any future "specced but unimplemented" finding triggers a design-process audit, not a quick patch.

### 9.5 Risks the amendment does not introduce

Risks named in 2026-05-22 §6.2 ("Failure modes eliminated by structural construction") remain eliminated. Specifically:
- "Host fork" (host-specific wire incompatibility) — capability flags travel via `--host-capabilities` and gate additively. Preserved.
- "Broken bundle reaches production" — `doctor --strict` Dockerfile RUN step preserved.
- "env.extra injection attack" — BLOCKED_ENV_KEYS preserved at wrapper layer.
- "Approval bypass via missing handler" — closed by bundle-layer policy; v1 host's auto-allow is intentional, not a bypass.
- "context-persistent-shaped bug class" — closed by CR-1; pattern is preserved verbatim.
- "stderrTail MCP-secret leak" — closed by CR-3; preserved.
- "Buffer silent drop" — closed by CR-4; preserved.
- "Initialize race" — closed by SC-1; structurally guaranteed by the amendment (init emitted synchronously before subprocess spawn).

---

## §10 Catalytic question — what would have to be true for this amendment to be wrong?  <a id="s10-catalytic"></a>

Per the system-design tradeoff frame: applied to the recommendation.

Three propositions, with monitoring signals that would falsify each:

### 10.1 "Per-turn subprocess cost dominates the latency budget"

**The proposition**: spawn cost (engine cold start + bundle warm-load + MCP server boots) exceeds 5s P50 in production. At chat cadence (minutes-scale turns) this is invisible; at sub-second-cadence turns it would be a UX cliff.

**What would falsify the amendment**: any v1 host adopting a sub-second-cadence interaction model. None of NC, PC, OpenCode, or Claude Code do — all are chat-cadence or batch.

**Monitoring**: `aaa.turn.spawn_ms` P50 and P95 metrics from 2026-05-22 §11.1 (M2, M3). Existing dashboards already track these. Trigger to revisit amendment: P50 > 5s for 3+ days OR any host requests sub-second-cadence support.

### 10.2 "A host needs mid-turn UI approval prompts within v1 window"

**The proposition**: an operator-facing UI shows "Allow / Deny" prompts in real-time while the agent is executing a tool. Without the mid-turn `approval/request`/`approval/response` round-trip in v1, this UX is not achievable.

**What would falsify the amendment**: any v1 host with a product requirement for live approval prompts. NC explicitly does not (A10: bypassPermissions parity); PC routes approval through `wakeReason: 'approval_callback'` inter-heartbeat (also out-of-band relative to the engine subprocess); OpenCode and Claude Code adapters haven't been authored but the four-host runway analysis has not surfaced a live-approval requirement.

**Monitoring**: any host PR proposing `approval.onRequest` callback usage; any product requirement explicitly mentioning "live approval prompt during agent execution". Trigger to revisit: any concrete host with this requirement → promote v1.x deferral nominee (a) or (b) from §6 WG-4.

### 10.3 "A host needs mid-turn streaming surfacing of granular events"

**The proposition**: an operator-facing UI shows live `tool/started`/`tool/completed` events as the agent works through a multi-step turn. Without `display/event` streaming in v1, this is not achievable.

**What would falsify the amendment**: any v1 host that surfaces granular events to its UI. NC explicitly does not (translator collapses tool events to `{type:'activity'}`); PC's adapters return final results only; OpenCode and Claude Code adapters haven't surfaced this requirement.

**Monitoring**: any host PR consuming `assistant/text` chunks or granular `tool/*` events; any product requirement explicitly mentioning "live tool progress UI". Trigger to revisit: any concrete host with this requirement → promote v1.x deferral nominee from §6 WG-3 (`--output streaming-json`).

### 10.4 The thing that would actually make this amendment wrong

The amendment is grounded in the empirical observation that the four queued v1 hosts (NC, PC, OpenCode, Claude Code) all operate one-shot-per-turn at the host boundary, do not surface granular mid-turn events, and do not invoke mid-turn UI approval. If any one of these three claims is empirically wrong — if a host already in flight needs one of these capabilities at v1 — then the amendment is locking in a wire that doesn't fit that host.

**The deepest unknown**: have we audited PC, OpenCode, and Claude Code with the same rigor we audited NC? The 2026-05-22 design's empirical-validation block (locked design header) cites NC and amplifier-app-cli as fully validated; PC is cited in 2026-05-20 design but only at the adapter boundary (`packages/adapters/codex-local/src/server/execute.ts:710`); OpenCode and Claude Code adapters do not yet exist.

**Pre-lock action**: before this amendment locks, the L3 team should grep each of PC, OpenCode (such as it exists), and Claude Code (such as it exists in the design literature) for: (a) `assistant/text` chunk consumption; (b) live approval prompt UI; (c) any code path that surfaces `tool/started` events to a user. If any are found, the amendment promotes the corresponding v1.x deferral nominee into v1 scope — or at minimum, into Phase 2 scope to ship shortly after v1.

**Post-lock monitoring**: 2026-05-22 §11 dashboards M1-M13 plus the three new monitors above (R6', R7', R8') plus a `host.unmet_capability_request_count` audit (any time a host adapter tries to use a `SpawnAgentParams` field that's not invoked in v1 — currently only `approval.onRequest` qualifies — log a warning; weekly review of the count).

---

---

## Observations (footnotes — no required refinement work)  <a id="s11-observations"></a>

Notes captured from the adversarial review that did not rise to disposition-level changes but are tracked here so the implementation plan and post-lock monitoring picks them up.

- **O1' — MCP cold-start P50 alarm threshold is unmeasured.** §9.1 sets the R6' alarm at `aaa.turn.mcp_spawn_ms` P50 > 2s sustained over 3 days. This value is provisional. It should be tuned against the first 30 days of measured production data from NC's actual MCP server cold-start distribution (most servers are `nanoclaw_send_message` — a Node script — but `nanoclaw_ask` and any future MCP servers may have different curves). Update the threshold in §9.1 with the empirically observed P95 + headroom once data exists.

- **O2' — Click JSON-parse callbacks for argv-borne JSON.** The A1' task list (§8.1) includes implementing click JSON-parse callbacks for `--host-capabilities`, `--env-extra`, and the inline form of `--mcp-servers`. Malformed JSON must NOT reach the engine's downstream parsing — it must map to a typed `AaaError(argv_json_malformed)` envelope with exit code 2 at the click decorator layer. Default click behavior (raise `UsageError`) is unacceptable because the wrapper expects the §4 envelope; a click `UsageError` writes a non-envelope error string to stderr and exits with click's own conventions. Wrap the parser to emit the envelope.

- **O3' — Argv-size instrumentation.** The engine emits a stderr-debug counter on every spawn recording the actual argv size in bytes (sum of `len(arg) + 1` across `sys.argv`). Track the distribution over the first 30 days post-launch. If the P99 stays below 4KB, the >8KB size-based spill threshold in CR-A is over-provisioned and can be lowered (which never triggers because the secret-aware rule dominates anyway). If P99 creeps above 8KB, the size-based spill becomes load-bearing for overflow protection.

- **O4' — Existing entry point, not a new module.** A1' (§8.1) extends the existing `src/amplifier_agent_cli/modes/single_turn.py` `run` command — it does NOT create a new module or a new entry point. The current Mode A is a working foundation; the amendment adds flags, an output mode, and the stdout-discipline + audit + correlationId machinery on top of it. The implementation plan must not pivot to a fresh module out of "tidy" instincts; the diff should be additive on the existing file.

- **O5' — Pre-lock audit of PC/OpenCode/Claude Code consumer surfaces.** §10.4 names "have we audited PC/OpenCode/Claude Code with the same rigor we audited NC?" as the deepest unknown. Before this amendment is committed-as-locked, the L3 team should audit at least one of PC/OpenCode/Claude Code's expected consumer surface against the new `DisplayEvent` shape (§5.2 CR-C) and the new `--output json` envelope (§4). The audit is part of the *migration plan*, not a blocker on the *amendment text*: if the audit surfaces a hard requirement (e.g. PC needs the `payload` field that CR-C removed), the amendment gets a follow-up edit; if the audit surfaces no blocker, the amendment locks unchanged.

---

## Design integrity check (Phase 7)  <a id="s12-integrity"></a>

Does the refined amendment contradict any of the original locked Phase 1-7 dispositions in the 2026-05-22 design?

**Critical-risk closures (CR-1 — CR-4):** All four remain closed. The CR-A, CR-B, CR-C, CR-D, CR-E findings in this refinement pass are *adversarial-review* outcomes on the *amendment text*, not new CRs against the 2026-05-22 design. None of them re-opens any 2026-05-22 closure. CR-1 (resume wiring), CR-2 (typed approval errors), CR-3 (stderrTail redaction), CR-4 (B1 visible-drop) all carry through at the same disposition level.

**Significant-concern closures (SC-1 — SC-7):** All seven remain closed. SC-1 init-before-activity ordering is strengthened (init now emitted synchronously before subprocess spawn). SC-2 (`hooks-logging` removal), SC-3 (`BLOCKED_ENV_KEYS`), SC-4 (`doctor --emit-sha`), SC-5 (sub-agent collapse), SC-7 (async probe) are unchanged. SC-6 (conformance fixtures) is *strengthened* — the amendment expands the fixture roster to 10 real-binary fixtures (CR-D), making the integration gate harder, not weaker.

**Twelve locked decisions:** Six (D1, D3, D4, D6, D9, D12) are explicitly re-litigated by the amendment with mechanism shifts; this is the amendment's explicit purpose. Six (D2, D5, D7, D8, D10, D11) are explicitly preserved. The refinement pass touches none of D2/D5/D7/D8/D10/D11.

**Risk register (R1-R8):** All eight preserved. The refinement adds detail to R6' (per-turn MCP cold-start), R7' (argv overflow), R8' (caller config drift) — the audit trail (SC-H) is named as the primary signal source for R8'. R9' (re-locking without integration gate) is strengthened from "at least one real-binary fixture" to "every conformance fixture is real-binary".

**Assumptions (A0 — A14):** All carry through. A0 is strengthened (one-shot per turn at the wire transport too).

**Net effect of the refinement on amendment soundness:** the refinement strengthens the amendment against the adversarial-review findings without reopening any prior closure. The Phase 7 integrity check passes.

---

*End of amendment document.*

*Companion artifacts to produce separately:*
- *Implementation plan: maps A1' through N4' to commit-level tasks; specifies the ten amended conformance fixtures (CR-D) and the real-binary integration gate; includes the A2.1' audit-trail writer (SC-H) and the engine-side PGID setup (SC-B).*
- *2026-05-22 design redline: marks the six re-litigated decisions (D1, D3, D4, D6, D9, D12) and the three deferral additions (WG-3, WG-4, WG-5/WG-6) as superseded by this amendment. (The banner header added to that design points readers here; the redline is the fuller artifact.)*
- *NC adapter author runbook update: notes that `SpawnAgentParams.approval.onRequest` throws `AaaError(approval_not_supported_in_v1)` in v1 (SC-C); documents the bundle-layer auto-approve configuration NC inherits by default; documents how to grep `metadata.correlationId` across NC audit logs and engine audit files for forensic correlation.*

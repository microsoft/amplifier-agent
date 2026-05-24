# AaA v2 — NanoClaw In-Container `amplifier-agent` Provider Adapter (v1)

| Field | Value |
|---|---|
| **Status** | LOCKED — ready for implementation |
| **Author** | Manoj Prabhakar Paidiparthy (implementation lead) |
| **Reviewer (primary)** | Brian Krabach |
| **Date locked** | 2026-05-22 |
| **Supersedes / amends** | none (greenfield design) |
| **Related** | `docs/designs/2026-05-20-aaa-v2-wrapper-and-wire.md` (the locked wire + wrapper this design consumes); `docs/designs/2026-05-19-baked-in-bundle-decision.md`; `docs/designs/2026-05-19-baked-in-bundle-revisit.md` |
| **Empirical validation** | NanoClaw fresh clone at `/Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh` (upstream `main@0683c6e`); `amplifier-app-cli@main` (canonical session-storage reference, `session_store.py` + `incremental_save.py`); `microsoft/amplifier-module-hooks-approval@v0.1.0` (USAGE_GUIDE.md verified); `microsoft/amplifier-module-tool-mcp@latest` (config-loading semantics verified against `amplifier_module_tool_mcp/config.py`); locked wire types at `wrappers/typescript/src/types.ts` and `wrappers/python/src/types.py` |
| **Audience** | NC team (in-container adapter implementers); L3 team (this repo's contributors, wire/engine/wrapper authors); downstream host-adapter authors (Paperclip, OpenCode, Claude Code) |

---

## Executive summary

This design specifies the **NanoClaw in-container provider adapter** that makes `amplifier-agent` available as a first-class engine alongside Claude SDK and Codex inside NanoClaw containers. The adapter consumes the locked `amplifier-agent-client-ts` wrapper (per `docs/designs/2026-05-20-aaa-v2-wrapper-and-wire.md`) and translates NanoClaw's `AgentProvider` lifecycle — `query()`, `push()`, `end()`, `abort()`, async-iterable `events` of `ProviderEvent` — into spawned subprocess turns over the locked wire. The bounded scope is: (a) one new NC in-container provider plus two pure-function helpers; (b) a small NC host-side mount/capability registration; (c) two surgical, additive wire surface changes that we own as the L3 team; (d) two new engine files and ~50 lines of `_runtime.py` threading to wire session persistence and approval correctly; (e) bundle, wrapper, and CI lint changes that fall out of the above. **No locked wire method is added** — both wire changes are additive fields on existing methods.

The design archetype is the **Phase 5 B+selected-C hybrid**: Candidate B's multi-host runway (capability negotiation via `initialize.host.capabilities`) plus three operationally-load-bearing pieces from Candidate C (engine `doctor --strict` image-build gate, structured-error taxonomy on `AaaError`, audit-logged adapter B1 buffer with visible drop). Candidate A (single ~280-LOC file mirroring `claude.ts`) is the documented Simplest Credible Alternative; it was rejected because the user locked a **four-host runway** (NC → Paperclip → OpenCode → Claude Code), which makes capability-flag retrofits cross-repo migration work and turns Candidate A's "reversible now" into "irreversible later." The dominant tradeoff Phase 5 identified — *"the wire is the irreversible commitment; everything above it is per-host implementation choice"* — drives every B-vs-C dimension where wire surface is identical (it always is) and adapter-side richness is the only variable.

The seven **Group A decisions** locked during Phase 1 conversation are: Q4 (binary via `uv tool install` system-wide), Q5 (push semantics = B1 buffer chain *inside* a single NC `query()` call, mapped to wire-native session/turn vocabulary), Q6 (approval = `hooks-approval` module + `WireApprovalProvider` shim, default-mode pattern + metadata gating, with NC host adapter auto-allowing per parity with Claude's `bypassPermissions`), Q7 (session state = host-mounted volume at `/home/node/.local/state/amplifier-agent/`), Q8 (resume strategy = adapter persists `sessionId` in NC's `continuation`, always passes `resume:true` when present, surfaces stale-session errors via `isSessionInvalid`), Q9 (MCP = additive `mcpServers` field on `agent/initialize`, threaded engine → `tool-mcp.mount(config={...})` per verified module API), Q10 (`prepare` runs at image-build time as the `node` user, with adapter-side lazy fallback on `engine_not_primed`).

Phase 6 adversarial review surfaced **4 Critical Risks (CR-1..CR-4), 7 Significant Concerns (SC-1..SC-7), and 8 Observations**. All four CRs are **closed** in this design: CR-1 (the bundle pointed at the wrong context module — `context-persistent` does not exist in foundation; canonical pattern is `context-simple` + app-layer `SessionStore`/`IncrementalSaveHook` per `amplifier-app-cli`); CR-2 (the `WireApprovalProvider` shim now carries an explicit three-code error contract — `approval_translation_failed`, `approval_timeout`, `approval_protocol_violation` — surfaced via `AaaError.classification = 'approval'`); CR-3 (`AaaError.stderrTail` redaction in NC's `event-translator.ts` whenever `mcp-translator.ts` declared MCP config was supplied, closing the secrets-via-traceback leak); CR-4 (B1 buffer cap raised 32 → 256 with **visible drop** policy — overflow emits a `progress` event "buffer overflow: N messages dropped" rather than silent loss). Three new risks were added to the register and accepted with monitoring: **R6** (deferred SHA-pinning of bundle module sources — current `@main` pins leave a supply-chain audit gap), **R7** (multi-host capability-flag sprawl over time), **R8** (B1 buffer chain cumulative latency on long steering bursts). Twelve **v1.x deferrals** are catalogued in Appendix A with explicit promotion triggers.

End-to-end effort: **~31 working days critical path, ~8 weeks calendar** assuming serial integration between amplifier-agent and NanoClaw repos. Total LOC added/changed: ~1,400 LOC across two repos. The migration plan (Phase 8, §10 here) sequences amplifier-agent ships first (wire v0.1.0 + engine + bundle + wrappers + doctor + conformance), NanoClaw consumes second (Dockerfile + adapter + helpers + host registration + CI lint), with bundle-source-compromise emergency runbook and a phased rollout R0→R3.

---

## §1 Problem framing  <a id="s1-problem"></a>

### 1.1 The boundary this design closes

NanoClaw runs language-model agents inside Docker containers, talking to humans via Slack/Telegram/Discord channels. Each agent runs through a **provider** — a TypeScript class implementing `AgentProvider` — that translates NanoClaw's chat-style lifecycle into whatever the underlying engine SDK looks like. Two providers exist today: `ClaudeProvider` (wraps Anthropic's `@anthropic-ai/claude-agent-sdk`) and `CodexProvider`.

This design specifies the third provider: **`AmplifierAgentProvider`**, which wraps the locked `amplifier-agent-client-ts` wrapper (defined by `docs/designs/2026-05-20-aaa-v2-wrapper-and-wire.md`) and exposes `amplifier-agent` as an interchangeable NanoClaw engine.

### 1.2 The contract NanoClaw expects

`AmplifierAgentProvider` must satisfy NanoClaw's `AgentProvider` interface, defined in `container/agent-runner/src/providers/types.ts`. The relevant excerpt (verbatim):

```typescript
// container/agent-runner/src/providers/types.ts (NC main @0683c6e)
export interface AgentProvider {
  query(input: QueryInput): AgentQuery
}

export interface AgentQuery {
  events: AsyncIterable<ProviderEvent>
  push(message: string): void
  end(): void
  abort(): void
}

export type ProviderEvent =
  | { type: 'init';     sessionId: string }
  | { type: 'activity' }
  | { type: 'progress'; message: string }
  | { type: 'result';   text: string }
  | { type: 'error';    message: string;
                        classification?: 'transport'|'protocol'|'engine'|'approval'|'unknown';
                        retryable?: boolean;
                        correlationId?: string }
```

(The `classification`/`retryable`/`correlationId` fields are added by this design — see §4.1 and §8 D8.)

The relevant excerpt of `QueryInput`:

```typescript
// container/agent-runner/src/providers/types.ts
export interface QueryInput {
  prompt: string
  continuation?: string  // opaque persisted-by-host token; for resume
  mcpServers?: Record<string, McpServerConfig>
  options?: ProviderOptions
}
```

### 1.3 The wire contract this design consumes

The locked `SpawnAgentParams` shape (from `docs/designs/2026-05-20-aaa-v2-wrapper-and-wire.md` §8.2):

```typescript
// wrappers/typescript/src/types.ts (locked v0.0.x; this design extends to v0.1.0 additively)
export interface SpawnAgentParams {
  sessionId?: string
  resume?: boolean
  lifecycle?: 'one-shot' | 'burst'
  protocolVersion?: string
  env?: { extra?: Record<string, string> }
  approval?: { onRequest?: ApprovalRequestHandler }
  // v0.1.0 additions (this design):
  mcpServers?: Record<string, McpServerConfig>
  host?: { capabilities?: HostCapabilities }
}
```

Locked decision §10.7 of `2026-05-20-aaa-v2-wrapper-and-wire.md` explicitly anticipates this design:

> NanoClaw: author an adapter using `amplifier-agent-client-ts` that fits the in-container `AgentProvider` shape. One-shot per query maps directly. `push()` is not used (no in-process burst).

This design **revises** the `push()` statement in light of NC's actual `poll-loop.ts` behavior (see §1.4).

### 1.4 Two facts the design pass surfaced

#### 1.4.1 NC's `poll-loop.ts` does call `push()`

The Phase 1 system map (architect, grep across `container/agent-runner/src/poll-loop.ts`) confirmed that `poll-loop.ts:343` invokes `query.push(message)` when a new user message arrives mid-turn. `ClaudeProvider` handles this by feeding the message into the SDK's open `MessageStream`. The locked AaA wire is **one-shot per subprocess** (locked design D10): a second `submit()` on the same `SessionHandle` throws `lifecycle_unsupported`. The wire has no `turn/inject` notification.

This design closes the gap **at the adapter layer** (B1 buffer chained across same-session turns — Q5, §8 D5) and **logs the wire deficiency** for v1.x consideration (Appendix D, D-v1.x-01 → `turn/inject` JSON-RPC notification). The user explicitly accepted this disposition: *"We will log this as a wire gap and build the workaround in the adapter."*

#### 1.4.2 The locked design did not anticipate per-session host-supplied MCP servers

`ClaudeProvider` passes `mcpServers` from `ProviderOptions` straight into the SDK. `amplifier-agent`'s tool surface comes from the **vendored bundle** (baked at image-build) — there is no wire-level way for the host to add MCP servers to a running engine, and the locked `SpawnAgentParams` has no `mcpServers` field.

Without closing this gap, NC's agent inside a container with `amplifier-agent` literally cannot reply: NC's reply mechanism is `mcp__nanoclaw__send_message`, an in-container MCP server NC injects per session into the SDK. This is a **v1 blocker**.

This design closes the gap **at the wire layer** (additive `mcpServers` field on `agent/initialize`, threaded through `amplifier-agent-client-ts` → engine `_runtime.py` → `tool-mcp.mount(config={...})`). The user is the L3 team and explicitly accepted this scope: *"We are the L3 team, We should plan to make necessary changes in all layers as part of this run."*

### 1.5 Out of scope for this design

- Adapter authoring for hosts other than NC (Paperclip, OpenCode, Claude Code) — those are downstream consumers of the same wire surface.
- Wire-level steering (`turn/inject` notification) — logged as D-v1.x-01.
- Sub-agent `progress` event surfacing — deferred to v1.x per user direction (SC-5 disposition; Appendix A D-v1.x-09).
- Per-tenant MCP allowlist policy in NC — the adapter exposes a filter seam (no-op default) for v1.x.
- Foundation kernel changes — none required.
- Bundle module internals (`hooks-approval`, `tool-mcp`) — consumed as-is.

---

## §2 Explicit assumptions  <a id="s2-assumptions"></a>

Every assumption baked into the locked decisions, surfaced explicitly. Each row: validation status (validated empirically by Phase 1 architect | plausible/unmeasured | implementation-time check | runtime invariant) and *what changes if wrong*.

### 2.1 Scale & lifecycle

| # | Assumption | Status | If wrong → design changes |
|---|---|---|---|
| **A1** | Turns are minutes-scale (not sub-second). Spawn cost ~3s per turn is acceptable overhead per NC turn. | Plausible (matches Slack/Telegram chat cadence); unmeasured at NC scale. | Spawn dominates latency budget; D10 reconsideration triggers — revisit `lifecycle: 'burst'`. |
| **A2** | NC's poll-loop never submits concurrent turns on the same provider instance; each `query()` call is serialized per session. | **Validated empirically** by Phase 1 architect (grep across `container/agent-runner/src/poll-loop.ts`). | Wrapper's one-shot per-`SessionHandle` constraint fails; would require per-handle parallelism or burst lifecycle. |

### 2.2 Network & runtime

| # | Assumption | Status | If wrong → design changes |
|---|---|---|---|
| **A3a** | Container does NOT need to install software or clone modules at runtime — image-build `amplifier-agent prepare` warms all module caches. | Plausible per design; verified by `prepare` semantics in 2026-05-20 design §5.5. | Image becomes runtime-dependent on network; lazy install fallback (already designed) carries more weight. |
| **A3b** | Container DOES need outbound network at runtime for LLM API calls and tool-initiated network ops (`web_search`, `web_fetch`, MCP servers reaching out). NC's existing Claude posture already permits this. | Inherited runtime requirement, not an assumption (NC's Claude provider already needs it). | Web tools fail, LLM calls fail; bundle tool-allowlist or "blocked" fallback would be needed. Out of scope for v1. |

### 2.3 Version coordination

| # | Assumption | Status | If wrong → design changes |
|---|---|---|---|
| **A4** | NC's CI enforces wrapper-engine version coordination — both `package.json` `amplifier-agent-client-ts` pin and Dockerfile `ARG AMPLIFIER_AGENT_VERSION` move together. | Implementation-time check; closed by `scripts/lint-aaa-version.ts` (§4.13). | Strict-refuse skew (locked design D6) fires at runtime on every turn. User-visible failure across all amplifier-agent sessions. |
| **A5** | Image rebuild cadence matches amplifier-agent release cadence (weeks-to-months, not days). | Plausible for v1; depends on amplifier-agent release tempo. | Image rebuild becomes operational bottleneck; may trigger move to runtime install with version pin. |
| **A6** | The L3 team (this design's authors) owns wire evolution, wrapper changes, engine changes, and bundle changes. Cross-repo coordination is in-scope. | **Confirmed** by user direction; rejected as external dependency. | (No alternative needed — we are L3.) |
| **A7** | The locked wire's `protocolVersion` strict-refuse semantics (locked design D6) tolerate **additive** field changes within a major+minor band when the receiving end implements the new fields; for cross-minor compatibility we bump `PROTOCOL_VERSION` to `0.1.0` and require both ends to match exactly. | Locked design D6 (strict-refuse). | If we needed cross-minor tolerance, would re-open D6 with a "additive-tolerant minor band" rule. Not pursued v1. |

### 2.4 Storage & persistence

| # | Assumption | Status | If wrong → design changes |
|---|---|---|---|
| **A8** | Engine session files at `$XDG_STATE_HOME/amplifier-agent/sessions/<id>/transcript.jsonl` are written via `amplifier_foundation.write_with_backup()` (atomic, sibling `.backup`). Durability is at-least-once across container restart. | Inherited from `amplifier-app-cli` canonical pattern; foundation-expert verified. Unverified for NC's specific volume driver. | Resume sees partial transcript; turn N+1 sees inconsistent state. Mitigation already in design: `IncrementalSaveHook` flushes after every `tool:post`. Worst case: lose last tool result. |
| **A9** | NC's container runtime supports declarative per-group host-mounted volumes (`hostStateDir/${groupId} → /home/node/.local/state/amplifier-agent/`). | **Validated empirically** by Phase 1 architect read of `nanoclaw-fresh/src/container-runtime.ts`. | Would need fallback to bundle-private volume or workspace-pollution alternative (rejected as Q7 option iii). |

### 2.5 Security & policy

| # | Assumption | Status | If wrong → design changes |
|---|---|---|---|
| **A10** | NC's trust model is **container-level**, not per-tool. Claude inside NC runs with `bypassPermissions: true` and `allowDangerouslySkipPermissions: true`. The faithful mirror for amplifier-agent is **adapter-side auto-allow**, using the wire mechanism (not bypassing it). | **Validated empirically** (Phase 1 grep of `container/agent-runner/src/providers/claude.ts:303-304`). | If NC adopts per-tool runtime approval later, adapter swaps `onRequest` to a real handler. No wire/bundle/engine change. |
| **A11** | The B1 buffer's cap=256 (visible-drop) is never reached in steady-state operation. Cap exists to prevent memory explosion in pathological steering bursts. | Plausible; depends on user-message rate vs. turn duration. Tracked via `aaa.buffer.overflow` metric (§11). | Cap reached repeatedly → trigger re-evaluation. v1.x options: dynamic cap based on memory pressure, or B2 fallback ("wait for next turn") as steering escape. |

### 2.6 Module stability

| # | Assumption | Status | If wrong → design changes |
|---|---|---|---|
| **A12** | The `hooks-approval@v0.1.0` module's `ApprovalProvider` registration shape is stable enough that the ~20-LOC `WireApprovalProvider` shim doesn't churn weekly. Module is "reference-impl" status per its repo. | Plausible (v0.1.0 release, MIT, has tests). Pinned in bundle. Drift detected by conformance fixtures. | Shim becomes a maintenance liability; promote to typed-import or upstream stability commitment. Tracked via R6. |
| **A13** | The `amplifier-module-tool-mcp` module's `mount(coordinator, config={"servers": {...}})` runtime config dict has **highest priority** and accepts the wire-supplied shape directly (no env file or transient path needed). | **Validated empirically** by foundation-expert investigation against `amplifier_module_tool_mcp/config.py:35-53,56-61`. | Would fall back to env-var path (E2 from Q9 alternatives); adds transient-file management to `_runtime.py`. |
| **A14** | The locked wrapper API additive surface (`mcpServers`, `host.capabilities`) does not require revisiting locked-design D10 (one-shot per `SessionHandle`). | Locked design D10 unchanged. | Would require wire-level steering, re-opens D10. |

---

## §3 System boundaries  <a id="s3-boundaries"></a>

### 3.1 Topology

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  NanoClaw host process                                          [OUT-OF-SCOPE]  │
│  • Channels (Slack/Telegram/Discord) • Session persistence                      │
│  • src/container-runtime.ts ◄── [IN-SCOPE: declare new mount]                   │
│  • src/providers/amplifier-agent.ts ◄── [IN-SCOPE: register provider + env]    │
│  • scripts/lint-aaa-version.ts ◄── [IN-SCOPE: CI lint]                          │
└────────────┬────────────────────────────────────────────────────────────────────┘
             │ spawn container per session group (existing NC mechanism)
             ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  NanoClaw container  (Docker image, built from container/Dockerfile)            │
│                                                                                 │
│  ┌──────────────────────────────────────────────────────────────────────────┐  │
│  │  agent-runner  (Node 22, slim, runs as user `node`)                      │  │
│  │                                                                          │  │
│  │  container/agent-runner/src/providers/                                   │  │
│  │    ├─ claude.ts                            (existing)                    │  │
│  │    ├─ codex.ts                             (existing)                    │  │
│  │    ├─ amplifier-agent.ts                   [IN-SCOPE: ~300 LOC]          │  │
│  │    └─ amplifier-agent/                     [IN-SCOPE: helpers dir]       │  │
│  │       ├─ event-translator.ts               [~110 LOC]                    │  │
│  │       └─ mcp-translator.ts                 [~60 LOC]                     │  │
│  │                                                                          │  │
│  │  imports:                                                                │  │
│  │    └─ amplifier-agent-client-ts            (locked package, v0.1.0)      │  │
│  └──────────────────────────────────────────────────────────────────────────┘  │
│           │ spawnAgent({sessionId, resume, mcpServers, host, ...})              │
│           │ JSON-RPC over stdio                                                 │
│           ▼                                                                    │
│  ┌──────────────────────────────────────────────────────────────────────────┐  │
│  │  amplifier-agent subprocess  (Python, installed via `uv tool install`    │  │
│  │                               at image build, binary at                  │  │
│  │                               /usr/local/bin/amplifier-agent)            │  │
│  │                                                                          │  │
│  │  src/amplifier_agent_lib/                                                │  │
│  │    ├─ _runtime.py                          [IN-SCOPE: +~50 LOC]          │  │
│  │    ├─ session_store.py                     [IN-SCOPE: ~180 LOC, NEW]     │  │
│  │    ├─ incremental_save.py                  [IN-SCOPE: ~120 LOC, NEW]     │  │
│  │    ├─ wire_approval_provider.py            [IN-SCOPE: ~80 LOC, NEW]      │  │
│  │    ├─ cli/doctor.py                        [IN-SCOPE: ~150 LOC, NEW]     │  │
│  │    └─ bundle/bundle.md                     [IN-SCOPE: 4-line edit]       │  │
│  │                                                                          │  │
│  │  vendored bundle:                                                        │  │
│  │    ├─ context-simple                       (kept; was: context-persistent│  │
│  │    │                                        — CR-1 fix)                  │  │
│  │    ├─ hooks-approval @v0.1.0               [IN-SCOPE: NEW mount]         │  │
│  │    ├─ tool-mcp @latest                     [IN-SCOPE: NEW mount]         │  │
│  │    └─ hooks-{status,redaction,…}           (existing)                    │  │
│  │                                                                          │  │
│  │  prepared cache:                                                         │  │
│  │    /home/node/.local/share/amplifier-agent/prepared/                     │  │
│  │    (warmed at image-build; SHA snapshot for R6 future SHA-pin)           │  │
│  └──────────────────────────────────────────────────────────────────────────┘  │
│                                                                                 │
│  host-mounted volume (NC declares per-group):                                  │
│    $HOST_STATE_DIR/$GROUP_ID  →  /home/node/.local/state/amplifier-agent/      │
│      sessions/<sessionId>/                                                     │
│        ├─ transcript.jsonl       (IncrementalSaveHook target)                   │
│        ├─ transcript.jsonl.backup (atomic write sibling)                        │
│        ├─ metadata.json           (sessionId, created, last_turn, …)            │
│        └─ config.snapshot.md      (bundle snapshot for diagnostics)             │
└─────────────────────────────────────────────────────────────────────────────────┘

OUT-OF-SCOPE:
  • NC host code beyond mount + provider-registration + lint
  • Foundation kernel (`amplifier_core.ApprovalProvider`) — used as-is
  • `hooks-approval` internals — consumed at v0.1.0
  • `tool-mcp` internals — consumed at HEAD
```

### 3.2 In-scope artifacts inventory

Per repo, by responsibility:

**amplifier-agent (this repo)** — wire/wrapper/engine/bundle:
- `wrappers/typescript/src/types.ts` (regen, +`HostCapabilities`, +`mcpServers` field, +`AaaError.severity`/`correlationId`)
- `wrappers/python/src/types.py` (regen)
- `wrappers/typescript/src/spawn.ts` (`BLOCKED_ENV_KEYS`, async `probeEngineVersion`)
- `wrappers/python/src/spawn.py` (parity)
- `wrappers/_gen.py` (regen sources)
- `schemas/agent-initialize.json` (regen)
- `PROTOCOL_VERSION` constant → `"0.1.0"`
- `src/amplifier_agent_lib/_runtime.py` (resume + shim + MCP threading)
- `src/amplifier_agent_lib/session_store.py` (NEW)
- `src/amplifier_agent_lib/incremental_save.py` (NEW)
- `src/amplifier_agent_lib/wire_approval_provider.py` (NEW)
- `src/amplifier_agent_lib/cli/doctor.py` (NEW, subcommand)
- `src/amplifier_agent_lib/bundle/bundle.md` (edits — §4.11)
- `wrappers/conformance/fixtures/*` (4 new fixtures)

**nanoclaw (downstream consumer)** — adapter/host/CI:
- `container/agent-runner/src/providers/amplifier-agent.ts` (NEW)
- `container/agent-runner/src/providers/amplifier-agent/event-translator.ts` (NEW)
- `container/agent-runner/src/providers/amplifier-agent/mcp-translator.ts` (NEW)
- `container/agent-runner/src/providers/index.ts` (register)
- `src/providers/amplifier-agent.ts` (host-side registration)
- `src/providers/index.ts` (register)
- `src/container-runtime.ts` (mount declaration)
- `container/Dockerfile` (install uv, install amplifier-agent, run prepare, run doctor --strict)
- `container/agent-runner/package.json` (pin `amplifier-agent-client-ts`)
- `scripts/lint-aaa-version.ts` (CI lint)

---

## §4 Components and responsibilities  <a id="s4-components"></a>

### 4.1 Adapter: `container/agent-runner/src/providers/amplifier-agent.ts` (NC repo, ~300 LOC)

**Responsibility (single sentence):** Orchestrate the per-turn `SessionHandle` lifecycle, hold the B1 buffer, declare `hostCapabilities`, and produce a `ProviderEvent` async iterable from `DisplayEvent` streams across one or more chained subprocess turns within a single NC `query()` call.

#### 4.1.1 Class shape

```typescript
import { spawnAgent, SessionHandle, AaaError } from 'amplifier-agent-client-ts'
import type { AgentProvider, AgentQuery, ProviderEvent, QueryInput } from '../types'
import { translate } from './amplifier-agent/event-translator'
import { translateMcp } from './amplifier-agent/mcp-translator'

const NC_HOST_CAPABILITIES = {
  supports_steering: false,
  supports_structured_errors: true,
} as const

export class AmplifierAgentProvider implements AgentProvider {
  query(input: QueryInput): AgentQuery {
    return new AmplifierAgentQuery(input)
  }
}

class AmplifierAgentQuery implements AgentQuery {
  private buffer: string[] = []                  // B1 buffer (push-arrivals)
  private overflowDropped = 0
  private static readonly BUFFER_CAP = 256
  private aborted = false
  private active?: SessionHandle
  private sessionId: string | null = null
  private initEmitted = false
  private mcpServersProvided: boolean

  constructor(private readonly input: QueryInput) {
    this.mcpServersProvided = !!input.mcpServers && Object.keys(input.mcpServers).length > 0
  }

  // ── public surface (AgentQuery) ──────────────────────────────────────────
  push(message: string): void {
    if (this.aborted) return
    if (this.buffer.length >= AmplifierAgentQuery.BUFFER_CAP) {
      this.overflowDropped++
      return  // visible-drop signal emitted from generator (§4.1.4)
    }
    this.buffer.push(message)
  }
  end(): void { this.aborted = true; this.buffer.length = 0 }
  abort(): void { this.aborted = true; this.active?.cancel(); /* see §5.4 */ }

  // ── async iterable ───────────────────────────────────────────────────────
  events: AsyncIterable<ProviderEvent> = this.makeEvents()

  private async *makeEvents(): AsyncIterable<ProviderEvent> { /* §4.1.2 + §4.1.4 */ }
}
```

#### 4.1.2 Locked `AgentProvider` contract — what we implement

(Reproduced verbatim from §1.2 for direct reference.)

```typescript
export interface AgentProvider { query(input: QueryInput): AgentQuery }
export interface AgentQuery {
  events: AsyncIterable<ProviderEvent>
  push(message: string): void
  end(): void
  abort(): void
}
```

#### 4.1.3 Buffer policy (B1, cap=256, visible drop)

- **Cap**: 256 messages (CR-4 disposition; raised from initial 32 after parallel-subagent concern).
- **Drop policy**: visible — first overflow message in any turn emits a single `progress` event: `"buffer overflow: N messages dropped"` where N is the count for the current turn. Subsequent overflows in the same turn increment N without re-emitting.
- **Chain semantics**: turn-N completes → drain buffer → if non-empty, mint next `SessionHandle` with same `sessionId`, `resume: true`, prompt = `buffer.join('\n\n')` (then clear buffer). Each chain link is a wire-level "turn within session" (matches the locked wire vocabulary, §5.3 of locked design).

#### 4.1.4 Generator skeleton

```typescript
private async *makeEvents(): AsyncIterable<ProviderEvent> {
  let prompt = this.input.prompt
  this.sessionId = this.input.continuation ?? null

  while (!this.aborted) {
    const handle = await spawnAgent({
      sessionId: this.sessionId ?? undefined,
      resume: this.sessionId != null,
      protocolVersion: '0.1.0',
      env: { extra: redactBlocked(this.input.options?.env) },
      approval: { onRequest: () => ({ decision: 'allow' }) },   // A10: NC auto-allow
      mcpServers: this.input.mcpServers,                        // identity passthrough
      host: { capabilities: NC_HOST_CAPABILITIES },
    })
    this.active = handle

    if (!this.initEmitted) {
      yield { type: 'init', sessionId: handle.sessionId }
      this.initEmitted = true
      this.sessionId = handle.sessionId
    }

    // Synthetic activity ticker starts AFTER init (SC-1)
    const ticker = startActivityTicker(this)        // §4.1.5

    try {
      const submission = handle.submit(prompt)
      for await (const ev of submission.events) {
        const translated = translate(ev, {
          mcpServersProvided: this.mcpServersProvided,
          sessionId: handle.sessionId,
        })
        for (const t of translated) yield t
      }
    } catch (e) {
      if (e instanceof AaaError && e.code === 'engine_not_primed') {
        // Lazy-prepare fallback (Q10)
        await runPrepare()
        continue   // retry without bumping turn
      }
      yield translateError(e, { sessionId: this.sessionId })
      return
    } finally {
      ticker.stop()
      this.active = undefined
    }

    if (this.overflowDropped > 0) {
      yield { type: 'progress', message: `buffer overflow: ${this.overflowDropped} messages dropped` }
      this.overflowDropped = 0
    }

    if (this.buffer.length === 0) return
    prompt = this.buffer.join('\n\n')
    this.buffer.length = 0
    // loop: chain next turn within same session
  }
}
```

#### 4.1.5 Synthetic activity ticker

A 2-second `setInterval` that emits `{ type: 'activity' }` while a subprocess turn is in flight. **Does not start until after the first `init` event is yielded** (SC-1: init must precede activity per NC's poll-loop expectations). Stops when the turn ends or on `abort`. Prevents NC's stuck-detection (`poll-loop.ts:359-361`) from firing during long tool runs that emit no display events for >10s.

### 4.2 `event-translator.ts` (NC repo, ~110 LOC, pure function)

**Responsibility:** Translate a single `DisplayEvent` (wire output, see locked design §6.1) into zero or more `ProviderEvent`s.

```typescript
export interface TranslateCtx {
  mcpServersProvided: boolean
  sessionId: string
}

export function translate(ev: DisplayEvent, ctx: TranslateCtx): ProviderEvent[] {
  switch (ev.type) {
    case 'message':
      return [{ type: 'activity' }, { type: 'result', text: ev.text }]
    case 'tool_use':
    case 'tool_result':
      return [{ type: 'activity' }]
    case 'progress':
      return [{ type: 'progress', message: ev.message }]
    case 'subagent_progress':
      return [{ type: 'activity' }]   // SC-5: no surfacing in v1
    case 'error':
      return [translateError(ev, ctx)]
    default:
      return [{ type: 'activity' }]
  }
}
```

**`AaaError` mapping** (consumes the locked-design D8 `AaaError` taxonomy plus this design's `severity` and `correlationId` additions):

| `AaaError.code` | `ProviderEvent.error.classification` | `retryable` |
|---|---|---|
| `engine_not_primed` | `engine` | `true` (adapter handles internally) |
| `spawn_failed`, `transport_*`, `stdio_closed` | `transport` | `true` |
| `protocol_mismatch`, `unsupported_method`, `schema_violation` | `protocol` | `false` |
| `approval_translation_failed`, `approval_timeout`, `approval_protocol_violation` | `approval` | `false` |
| `engine_crashed`, `bundle_failed`, `module_failed` | `engine` | `false` |
| (any other) | `unknown` | `false` |

**`stderrTail` redaction (CR-3)**: When `ctx.mcpServersProvided === true`, the translator scrubs any `AaaError.stderrTail` field before emitting — specifically: replaces any value matching MCP `env` keys (case-insensitive substring match against the keys NC supplied) with `[REDACTED]`. The redaction is conservative; the source of truth for what is sensitive is the keys NC declared.

**Init-before-activity enforcement (SC-1)**: The translator never emits `{type:'activity'}` if it has not previously seen (or the caller has not previously emitted) an `init`. Enforced by the caller (§4.1.4 init-emit gate); translator itself is stateless.

### 4.3 `mcp-translator.ts` (NC repo, ~60 LOC, pure function)

**Responsibility:** Validate the shape of NC-supplied `mcpServers` and pass through identity-mapped to the wire. No in-band redaction (kept as identity to preserve test-fixture parity); secret-leak protection lives in §4.2's `stderrTail` redaction.

```typescript
import type { McpServerConfig } from '../types'

const VALID_TRANSPORTS = new Set(['stdio', 'sse', 'streamable_http'])

export function translateMcp(
  input: Record<string, McpServerConfig> | undefined,
): Record<string, McpServerConfig> | undefined {
  if (!input) return undefined
  for (const [name, cfg] of Object.entries(input)) {
    if (!VALID_TRANSPORTS.has(cfg.transport)) {
      throw new Error(`mcp-translator: server '${name}' unknown transport '${cfg.transport}'`)
    }
  }
  return input   // identity passthrough (the wire field accepts this shape)
}
```

### 4.4 NC host-side `src/providers/amplifier-agent.ts` (~25 LOC)

**Responsibility:** Register the host-side provider config — declare the host-mounted state volume per Q7, declare the env passthrough whitelist, declare provider name `amplifier-agent`.

```typescript
import { registerProviderContainerConfig } from '../container-runtime'

registerProviderContainerConfig('amplifier-agent', ({ groupId, hostStateDir }) => ({
  env: {
    AMPLIFIER_AGENT_LOG_LEVEL: 'info',
  },
  mounts: [
    {
      source: `${hostStateDir}/amplifier-agent/${groupId}`,
      target: '/home/node/.local/state/amplifier-agent',
      mode: 'rw',
    },
  ],
}))
```

### 4.5 `container-runtime.ts` mount declaration (NC repo)

**Responsibility:** Surface the new per-group mount path declared by the amplifier-agent provider config. One mount entry added; no broader runtime changes.

### 4.6 Engine: `session_store.py` + `incremental_save.py` (CR-1)

**Responsibility:** Persist and restore conversation transcripts at the application layer, using `context-simple`'s `get_messages()` / `set_messages()` hooks. **The pattern is lifted near-verbatim from `amplifier-app-cli` (canonical foundation-ecosystem reference).**

#### `session_store.py` (~180 LOC, ported from app-cli)

```python
# src/amplifier_agent_lib/session_store.py
from pathlib import Path
import json, os
from amplifier_foundation import write_with_backup

class SessionStore:
    def __init__(self, root: Path):
        self.root = root

    def session_dir(self, session_id: str) -> Path:
        return self.root / "sessions" / session_id

    async def save(self, session_id: str, transcript: list[dict], metadata: dict) -> None:
        d = self.session_dir(session_id); d.mkdir(parents=True, exist_ok=True)
        await write_with_backup(d / "transcript.jsonl",
            "\n".join(json.dumps(m) for m in transcript))
        await write_with_backup(d / "metadata.json", json.dumps(metadata, indent=2))

    async def load(self, session_id: str) -> tuple[list[dict], dict] | None:
        d = self.session_dir(session_id)
        if not (d / "transcript.jsonl").exists(): return None
        transcript = [json.loads(line) for line in
                      (d / "transcript.jsonl").read_text().splitlines() if line.strip()]
        metadata = json.loads((d / "metadata.json").read_text())
        return transcript, metadata
```

#### `incremental_save.py` (~120 LOC)

```python
# src/amplifier_agent_lib/incremental_save.py
from amplifier_core import HookPriority

class IncrementalSaveHook:
    priority = HookPriority(900)
    event = "tool:post"

    def __init__(self, store, session_id: str, ctx):
        self.store, self.session_id, self.ctx = store, session_id, ctx

    async def __call__(self, hook_ctx):
        transcript = await self.ctx.get_messages()
        await self.store.save(self.session_id, transcript,
                              metadata={"last_tool": hook_ctx.tool_name})
```

### 4.7 Engine: `wire_approval_provider.py` shim (CR-2 with full error contract)

**Responsibility:** Implement `amplifier_core.ApprovalProvider` by forwarding requests over the wire to the host adapter. Explicit error contract — no silent crashes.

```python
# src/amplifier_agent_lib/wire_approval_provider.py
from amplifier_core import ApprovalProvider, ApprovalRequest, ApprovalResponse
from .errors import AaaWireError  # local error class

APPROVAL_TIMEOUT_SECONDS = 30.0

class WireApprovalProvider(ApprovalProvider):
    def __init__(self, wire_send_request):
        self.wire_send_request = wire_send_request  # JSON-RPC bridge

    async def request_approval(self, req: ApprovalRequest) -> ApprovalResponse:
        try:
            wire_payload = self._translate_request(req)
        except Exception as e:
            raise AaaWireError(
                code="approval_translation_failed",
                severity="error",
                classification="approval",
                message=f"failed to translate ApprovalRequest to wire shape: {e}",
            )
        try:
            wire_response = await asyncio.wait_for(
                self.wire_send_request("approval/request", wire_payload),
                timeout=APPROVAL_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            raise AaaWireError(
                code="approval_timeout",
                severity="error",
                classification="approval",
                message=f"host did not respond to approval/request within {APPROVAL_TIMEOUT_SECONDS}s",
            )
        try:
            return self._translate_response(wire_response)
        except Exception as e:
            raise AaaWireError(
                code="approval_protocol_violation",
                severity="error",
                classification="approval",
                message=f"approval/response did not conform to schema: {e}",
            )
```

Each error code maps via `_runtime.py`'s wire-error surface to an `AaaError` with `classification: 'approval'`. NC's event-translator catches that classification and emits a typed `error` event the operator can grep for.

### 4.8 Engine: `_runtime.py` threading (~50 LOC added)

**Responsibility:** Wire together the new components — resume path, approval shim registration, MCP threading.

```python
# src/amplifier_agent_lib/_runtime.py (excerpt of additions)

async def handle_initialize(params: InitializeParams) -> InitializeResult:
    bundle = await prepared_bundle()

    # ── Q9: thread MCP into tool-mcp.mount() ─────────────────────────────────
    static = bundle.config.get("tools", {}).get("tool-mcp", {}).get("config", {})
    tool_mcp_config = {**static, "servers": params.get("mcpServers", {})}

    # ── CR-1: session persistence wiring ─────────────────────────────────────
    session_id = params["sessionId"]
    is_resumed = bool(params.get("resume"))
    store = SessionStore(Path(os.environ.get("XDG_STATE_HOME",
                              Path.home() / ".local/state")) / "amplifier-agent")
    transcript, metadata = ((await store.load(session_id)) or ([], {}))

    session = await bundle.create_session(
        session_id=session_id,
        is_resumed=is_resumed,
        tool_overrides={"tool-mcp": {"config": tool_mcp_config}},
    )
    await session.context.set_messages(transcript)
    session.hooks.register(IncrementalSaveHook(store, session_id, session.context))

    # ── CR-2: approval shim ──────────────────────────────────────────────────
    session.coordinator._approval_hook.register_provider(
        WireApprovalProvider(wire_send_request=session.wire.send_request)
    )

    # ── host capabilities (B+C hybrid) ───────────────────────────────────────
    session.metadata["host_capabilities"] = params.get("host", {}).get("capabilities", {})
    return InitializeResult(protocolVersion="0.1.0", capabilities={...})
```

### 4.9 Engine: `amplifier-agent doctor` subcommand (~150 LOC, NEW)

**Responsibility:** Pre-launch sanity check, run at image-build time with `--strict` to gate Dockerfile build.

Surface:
- `amplifier-agent doctor` — human-readable summary; exit 0 if healthy.
- `amplifier-agent doctor --strict` — exit non-zero on **any** warning; intended for CI/image-build.
- `amplifier-agent doctor --quick` — minimal check (binary present, prepared cache exists, can JSON-RPC handshake); intended for adapter health probe (deferred to v1.x per SC-5 disposition adjacent; see Appendix A D-v1.x-08).
- `amplifier-agent doctor --emit-sha` — emit current bundle source SHAs (R6 future SHA-pin enablement).

Checks performed:
1. `amplifier-agent` binary on PATH; version matches `PROTOCOL_VERSION`.
2. Prepared cache exists at `$XDG_DATA_HOME/amplifier-agent/prepared/`.
3. Every bundle module mounts cleanly (dry-run `prepared_bundle()` init).
4. `tool-mcp`, `hooks-approval`, `context-simple` present in mounted modules.
5. `wire_approval_provider` shape-check against `hooks-approval` reference module version.
6. `session_store` write/read roundtrip in a tempdir.
7. (`--emit-sha` only) computes content SHA of every bundle module source.

### 4.10 Wire / protocol additions (additive only; bumps `PROTOCOL_VERSION` to `"0.1.0"`)

**No new wire methods.** Three additive field changes:

#### 4.10.1 `agent/initialize` params

Add two optional fields to `InitializeParams`:

```typescript
// wrappers/typescript/src/types.ts (additions)
export interface HostCapabilities {
  supports_steering?: boolean
  supports_structured_errors?: boolean
}
export interface InitializeParams {
  // ... existing fields ...
  mcpServers?: Record<string, McpServerConfig>
  host?: { capabilities?: HostCapabilities }
}
export interface McpServerConfig {
  transport: 'stdio' | 'sse' | 'streamable_http'
  // Variants per transport — match `amplifier-module-tool-mcp` config shape verified
  // at amplifier_module_tool_mcp/config.py:35-53
  command?: string; args?: string[]; env?: Record<string, string>
  url?: string; headers?: Record<string, string>
}
```

#### 4.10.2 `AaaError` additions

```typescript
export class AaaError extends Error {
  code: string
  // existing
  classification?: 'transport'|'protocol'|'engine'|'approval'|'unknown'
  // additions:
  severity?: 'error'|'warning'
  correlationId?: string
  stderrTail?: string
}
```

#### 4.10.3 `PROTOCOL_VERSION` bump to `"0.1.0"`

Both ends strict-refuse on mismatch (locked design D6 unchanged).

#### 4.10.4 Schema regen

`wrappers/_gen.py` regenerates `wrappers/typescript/src/types.ts`, `wrappers/python/src/types.py`, `schemas/agent-initialize.json`, and `docs/designs/2026-05-20-aaa-v2-wrapper-and-wire.md`'s schema reference block. **Verification step:** `pnpm typecheck` + `uv run pytest wrappers/conformance/parity-lint.test` must both pass (these gates already exist).

### 4.11 Bundle changes: `src/amplifier_agent_lib/bundle/bundle.md`

Four-line edit, each with explicit rationale:

```yaml
# context: was `context-persistent` (CR-1) — corrected to canonical pattern.
context:
  module: context-simple
  source: git+https://github.com/microsoft/amplifier-module-context-simple@main
  config:
    max_tokens: 300000

# tools: ADD tool-mcp (Q9 closes the v1 reply-channel blocker).
tools:
  - module: tool-mcp
    source: git+https://github.com/microsoft/amplifier-module-tool-mcp@main
    config:
      verbose_servers: false
      max_content_size: 65536

# hooks: ADD hooks-approval (Q6 — mechanism for per-tool gating). Default mode
# (NOT policy_driven_only) so built-in pattern + tool-metadata gating fires.
hooks:
  # ... existing five hooks ...
  - module: hooks-approval
    source: git+https://github.com/microsoft/amplifier-module-hooks-approval@v0.1.0

# REMOVE hooks-logging mount (SC-2): the existing config writes to an ephemeral
# in-container path; with host-mounted session storage carrying all needed
# audit, the duplicate ephemeral log is operationally misleading.
```

(SC-2 disposition: the prose block at original `bundle.md` lines 213-216 referring to "deferred session-state work" is removed; the canonical pattern now ships in `_runtime.py`.)

### 4.12 Wrapper changes (TS + Py)

#### 4.12.1 `BLOCKED_ENV_KEYS` (SC-3)

```typescript
// wrappers/typescript/src/spawn.ts
const BLOCKED_ENV_KEYS = new Set([
  'PYTHONPATH','LD_PRELOAD','LD_LIBRARY_PATH','PYTHONSTARTUP','PATH',
  'PYTHONHOME','PYTHONNOUSERSITE','DYLD_INSERT_LIBRARIES','DYLD_LIBRARY_PATH',
])

function buildEnv(extra?: Record<string,string>): NodeJS.ProcessEnv {
  if (extra) {
    for (const k of Object.keys(extra)) {
      if (BLOCKED_ENV_KEYS.has(k)) {
        throw new AaaError({
          code: 'env_injection_rejected',
          classification: 'protocol',
          severity: 'error',
          message: `env.extra key '${k}' is blocked (security)`,
        })
      }
    }
  }
  return { ...process.env, ...(extra ?? {}) }
}
```

Same shape in `wrappers/python/src/spawn.py`.

#### 4.12.2 Async `probeEngineVersion` (SC-7)

`probeEngineVersion()` becomes `async` to allow timeout and structured error propagation. Existing call sites in `spawn.ts` updated to `await`.

#### 4.12.3 `HostCapabilities` interface

See §4.10.1.

### 4.13 NC CI lint: `scripts/lint-aaa-version.ts` (~40 LOC)

**Responsibility:** Read `container/agent-runner/package.json` to extract the `amplifier-agent-client-ts` pin; read `container/Dockerfile` to extract the `AMPLIFIER_AGENT_VERSION` ARG default; fail CI if they don't match. Closes A4.

### 4.14 New conformance fixtures (`wrappers/conformance/fixtures/`)

Four new replay fixtures:
1. `initialize-with-mcpservers.json` — exercises the new field plumbing into `tool-mcp.mount()`.
2. `initialize-with-host-capabilities.json` — exercises `HostCapabilities` round-trip.
3. `approval-shim-three-error-codes.json` — drives each of the three `wire_approval_provider` error paths.
4. `resume-with-session-store.json` — turn 1 with tool, kill subprocess, turn 2 resume, assert transcript continuity.

---

## §5 Data and control flows  <a id="s5-flows"></a>

### 5.1 Happy path — first turn, no resume

```
User (Slack)            NC poll-loop              AmplifierAgentQuery       spawnAgent / engine
   │                          │                          │                          │
   │  msg "what time is it?"  │                          │                          │
   │─────────────────────────►│                          │                          │
   │                          │  provider.query({prompt, │                          │
   │                          │    continuation=null})   │                          │
   │                          │─────────────────────────►│                          │
   │                          │                          │  spawnAgent({sessionId:  │
   │                          │                          │    undef, resume: false, │
   │                          │                          │    mcpServers: {nano…},  │
   │                          │                          │    host:{capabilities}}) │
   │                          │                          │─────────────────────────►│
   │                          │                          │                          │  fork python subprocess
   │                          │                          │                          │  agent/initialize ↔ ack
   │                          │                          │  SessionHandle (id="s_…")│
   │                          │                          │◄─────────────────────────│
   │                          │                          │                          │
   │                          │                          │  yield {type:'init',      │
   │                          │                          │         sessionId:"s_…"}  │
   │                          │                          │── start activity ticker ─│
   │                          │                          │                          │
   │                          │                          │  handle.submit(prompt)   │
   │                          │                          │─────────────────────────►│
   │                          │                          │     turn/submit          │
   │                          │                          │                          │  display/event: tool_use…
   │                          │                          │  ◄── DisplayEvents ──────│  display/event: message
   │                          │                          │                          │  turn/completed
   │                          │  ProviderEvent stream:   │                          │
   │                          │  activity, activity,     │                          │
   │                          │  result("It is 2:15pm")  │                          │
   │                          │◄─────────────────────────│                          │
   │                          │  ── stop ticker          │                          │
   │  "It is 2:15pm" (via     │                          │                          │
   │   mcp__nanoclaw__send… ) │                          │                          │
   │◄─────────────────────────────────────────────────────────────────────────────────│
   │                          │  persist continuation =  │                          │
   │                          │    "s_…"                 │                          │
```

Key invariants:
- `init` is emitted **before** any `activity`.
- The activity ticker starts only after `init` has fired (SC-1).
- The session id NC persists is the wire-level `sessionId` returned by the engine.

### 5.2 Steering case — B1 buffer + chained turns in one session

```
User                     NC poll-loop          AmplifierAgentQuery        engine (subprocess)
  │                           │                       │                        │
  │ msg1 "summarize repo"     │                       │                        │
  │──────────────────────────►│                       │                        │
  │                           │ provider.query(msg1)  │                        │
  │                           │──────────────────────►│                        │
  │                           │                       │ spawn + init → "s_x"   │
  │                           │                       │ submit(msg1) ─────────►│
  │                           │ ◄── init, activity… ──│ ◄── DisplayEvents ─────│
  │ (15s elapsed)             │                       │                        │
  │ msg2 "oh skip README"     │                       │                        │
  │──────────────────────────►│                       │                        │
  │                           │ query.push(msg2)      │                        │
  │                           │──────────────────────►│ buffer=[msg2]          │
  │                           │ ◄── activity ── (no   │                        │
  │                           │     init re-emit)     │                        │
  │                           │                       │ ◄── result(msg1 ans) ──│
  │                           │                       │ ◄── turn/completed ────│
  │                           │ ◄── result… ──────────│ buffer non-empty:      │
  │                           │     (turn-1 result    │  spawn new SessionHandle│
  │                           │     to channel)       │  (sessionId="s_x",     │
  │                           │                       │   resume=true)         │
  │                           │                       │ submit(msg2) ─────────►│
  │                           │ ◄── activity… ────────│ ◄── DisplayEvents ─────│
  │                           │ ◄── result(msg2 ans) ─│ ◄── turn/completed ────│
  │ "Final summary…" (msg2)   │                       │                        │
  │◄──────────────────────────│ ◄── (events end) ─────│ buffer empty: return   │
```

Key invariants:
- **One `init` per NC `query()` call** — not per chain link (the wrapper-level `sessionId` is the same across links).
- **One `result` per chain link** — NC's `dispatchResultText` runs per result; one NC `query()` may produce multiple results.
- Each chain link is a wire-level "turn within session" — `spawnAgent` with same `sessionId` and `resume: true`.
- Buffer drained atomically at chain-link boundary; new pushes during the next link land in a fresh buffer.

### 5.3 Buffer overflow visible-drop sequence

```
… mid-turn …
push(msg_257)  →  buffer full (256), overflowDropped=1
push(msg_258)  →  overflowDropped=2
push(msg_259)  →  overflowDropped=3
… turn N completes …
yield {type:'progress', message:'buffer overflow: 3 messages dropped'}
… chain to turn N+1 with first 256 messages …
```

### 5.4 Cancel / mid-turn failure

`abort()` does:
1. Set `this.aborted = true`.
2. Call `this.active?.cancel()` — wrapper sends `turn/cancel` notification; engine SIGTERMs current turn (per locked design §5.4).
3. Flush remaining buffer entries to the audit log (R6/operational signal) before clearing.
4. Generator's `finally` stops ticker, sets `active = undefined`.
5. Yield a final `{type:'progress', message:'cancelled by host'}` if the iterator has not already returned.

Mid-turn engine crash (`AaaError(code='engine_crashed')`):
1. Generator's `catch` translates to `{type:'error', classification:'engine', retryable:false, ...}`.
2. `return` from generator — async iterable terminates.
3. NC's poll-loop's next message starts a fresh `query()`; resume kicks in if NC has persisted the `continuation`.

```
abort flow:
  NC                AmplifierAgentQuery       SessionHandle           engine
   │                       │                       │                       │
   │  abort()              │                       │                       │
   │──────────────────────►│ aborted=true          │                       │
   │                       │ handle.cancel() ─────►│ turn/cancel ─────────►│
   │                       │                       │                       │  SIGTERM current turn
   │                       │ flush buffer→audit    │                       │
   │                       │ ticker.stop()         │                       │
   │                       │ yield progress("can…")│                       │
   │                       │ return                │                       │
```

---

## §6 Risks and failure modes  <a id="s6-risks"></a>

### 6.1 Refined risks register (R1–R8)

| # | Risk | Trigger / monitoring signal | Disposition | Trigger to revisit |
|---|---|---|---|---|
| **R1** | Spawn-time latency cliff dominates short-turn UX. | `aaa.turn.spawn_ms` P50 > 5s for 3+ days | Accept; monitor | P50 > 8s → revisit `lifecycle: 'burst'` (locked-design D10) |
| **R2** | Buffer-chain latency drift on long steering bursts. | `aaa.buffer.chain_links` P95 > 4 per query() over 7 days | Accept; monitor | P95 > 6 → escalate `turn/inject` (D-v1.x-01) |
| **R3** | MCP secrets leak through engine stderr / traceback. | Audit: any `AaaError.stderrTail` non-empty when `mcpServersProvided=true` AND post-redaction string contains any declared MCP env key value | Closed in v1 by CR-3 redaction; residual ≈ unknown-shape leaks | Any leak observed → escalate to engine-side redaction |
| **R4** | Session-file durability across container restart. | Resume failure rate > 0.5% over 7 days | Inherits from locked-design R4; mitigated by `IncrementalSaveHook` flushing per `tool:post` | Observed > 1% → fsync escalation |
| **R5** | `protocolVersion` skew between wrapper pin and binary install. | Build-time CI lint (`scripts/lint-aaa-version.ts`) or any runtime `protocol_mismatch` error | Closed at build time; runtime fallback already exists | Any runtime occurrence in canary → CI lint gap |
| **R6** ⚠ NEW | Bundle module sources pinned to `@main` rather than SHA — supply-chain audit gap. | Audit: bundle source diff between two image builds with no version bump. | Accept v1; `amplifier-agent doctor --emit-sha` ships ready for promotion. | Any unexpected module behavior change in canary → SHA-pin promotion |
| **R7** ⚠ NEW | `initialize.host.capabilities` flag sprawl as more hosts onboard. | Capability flag count > 6 within 6 months. | Accept; document promotion policy | Flag count > 6 → consolidate to `host.tier` enum |
| **R8** ⚠ NEW | B1 buffer cap=256 reached repeatedly in production (parallel sub-agent dispatch storms). | `aaa.buffer.overflow_count` > 0 in any rolling 24h. | Accept v1 (visible-drop signal); promote B2 ("wait for next turn") fallback if overflow > 5/day | Overflow > 5/day → re-open Q5 |

### 6.2 Failure modes eliminated by structural construction

These risks the design **does not have**, and why:

- **"Host fork"** (each new host re-asking the wire for incompatible changes): closed by `initialize.host.capabilities` being **the** escape valve for host-specific policy. New hosts add flags additively; the wire stays stable.
- **"Broken bundle reaches production"**: closed by `amplifier-agent doctor --strict` running at image-build time as a Dockerfile `RUN` step. Image build fails before push.
- **`env.extra` injection attack**: closed by `BLOCKED_ENV_KEYS` validation in `buildEnv()` (SC-3 / §4.12.1). Any blocked key throws at `spawnAgent` call time with `code: 'env_injection_rejected'`.
- **Approval bypass via missing handler**: closed by `WireApprovalProvider`'s explicit three-code error contract + foundation kernel's default-deny when no provider is registered. There is no "silent allow" path.
- **`context-persistent`-shaped bug class** (resume corruption from a module that doesn't exist): closed by CR-1 — design now grounded against the actual canonical pattern.
- **`stderrTail` MCP-secret leak**: closed by CR-3 redaction in `event-translator.ts`.
- **Buffer silent drop** (operator never knows): closed by CR-4 visible-drop policy.
- **Initialize race** (activity ticker firing before init reaches NC): closed by SC-1 init-emit gate.

### 6.3 Residual risks accepted with monitoring

- **R6** (SHA-pinning deferred) — accepted; `--emit-sha` ships ready.
- **A8** (session-file durability under uncommon NC volume drivers) — accepted; inherited from locked design R4.
- **R8** (buffer overflow under parallel-subagent storms) — accepted; visible-drop signal exists.
- **A12** (`hooks-approval@v0.1.0` reference-impl status) — accepted; conformance fixtures will catch shape drift.

---

## §7 Tradeoffs  <a id="s7-tradeoffs"></a>

### 7.1 8-dimension matrix (from Phase 5)

| Dimension | **A — Simplest** (rejected) | **B — Scalable** | **C — Robust** | **B+C hybrid** (chosen) |
|---|---|---|---|---|
| **Latency** | Good — ~3s spawn baseline, zero adapter overhead | Good — capability negotiation one-shot, ~5ms parse | Adequate — audit + drift ~5-25ms/turn | Good — adopt C's audit only on session boundaries |
| **Complexity (code)** | Good — 1 file ~280 LOC | Adequate — 3 files ~460 LOC | Poor — 8 files, junior reviewers need a map | Adequate — 3 files (NC) + 4 small additions (engine) |
| **Complexity (ops)** | Poor — flat error strings | Adequate — structured taxonomy | Good — named handlers + audit + drift | Adequate→Good — taxonomy + doctor --strict |
| **Reliability** | Poor — opaque failures | Good — validation + activity ticks + classified errors | Good+ — circuit breaker + doctor + drift | Good+ — adopt doctor --strict from C |
| **Cost (implement)** | Good — ~1 sprint | Adequate — ~1.5 sprints | Poor — ~3 sprints | Adequate — ~1.5 sprints + 0.5 sprint (selected C) |
| **Cost (onboard next host)** | Poor — retrofit cost | Good — multi-host-ready wire | Good — same wire | Good — same wire |
| **Cost (diagnose)** | Poor — no correlation IDs | Adequate — structured classification | Good — corr IDs + audit + named paths | Good — corrId + classification + doctor |
| **Security** | Poor — passthrough, no redaction | Adequate — filter seam, redaction TODO | Good — secret redaction enforced | Good — CR-3 redaction + BLOCKED_ENV_KEYS |
| **Scalability (hosts)** | Poor — ruled out by 4-host lock | Good — capability flag surface | Good — same wire | Good |
| **Reversibility** | Good now / Poor long-term | Adequate — additive fields deprecatable | Adequate — CLI + mount config rollback | Adequate |
| **Org fit** | Poor — ops ownership concern | Good — L3 owns; parity with Claude | Adequate — scope creep risk | Good |

### 7.2 The dominant tradeoff

From the Phase 5 analysis:

> The wire is the irreversible commitment; everything above it is per-host implementation choice.

This insight drives the design's center of gravity: **invest heavily in the wire's right shape now** (capability flags + `mcpServers` + error taxonomy), keep adapter-side per-host complexity at the **B+selected-C minimum** that hits operational thresholds (doctor --strict gating + buffer visible-drop + corrId + structured errors), and explicitly defer per-host operational maximalism (audit-log mount, drift digest snapshot, background `doctor --quick` probe) to v1.x where production signal can drive the decision.

### 7.3 What this design optimizes for

1. **Multi-host runway** — 4 hosts queued; capability flags load-bearing.
2. **Wire correctness** — additive-only, no method add, strict-refuse preserved.
3. **Operator's 2am experience for the critical-path failures** — typed errors, `doctor --strict` gate, visible buffer drops.
4. **L3 team change ownership** — every layer's edit is well-bounded.

### 7.4 What this design sacrifices

1. **Operational maximalism** — no audit-log mount, no drift digest, no background health probe (all deferred to v1.x with named promotion triggers).
2. **Sub-agent observability** — `subagent_progress` collapsed to `activity` (SC-5/D-v1.x-09).
3. **Per-tenant MCP policy** — no filter seam beyond the no-op default.
4. **Wire-level steering** — accepted as workaround (B1 buffer); D-v1.x-01 logged.

---

## §8 Recommended design — the locked decisions  <a id="s8-decisions"></a>

### 8.1 The 12-decision table

| # | Decision name | Locked position | Rationale (one sentence) |
|---|---|---|---|
| **D1** | Adapter location | In-container at `container/agent-runner/src/providers/amplifier-agent.ts` + `amplifier-agent/` helpers dir | Sibling to `claude.ts`/`codex.ts`; matches NC's architectural shape exactly |
| **D2** | Adapter scope | Provider class + 2 pure-function helpers + minimal host-side mount/registration | B+C hybrid; capability negotiation load-bearing for 4-host runway |
| **D3** | `push()` semantics | B1 — buffer at adapter, chain as multi-turn-in-session (D5 below details vocabulary) | Wrapper API locked one-shot per `SessionHandle`; wire gap (D-v1.x-01) logged |
| **D4** | Buffer cap + drop | cap=256, visible-drop (overflow emits `progress`) | CR-4 — cap=32 broke parallel sub-agent dispatch; visible drop preserves operator signal |
| **D5** | Chain vocabulary | One NC `query()` = one wire-session; each chain link = wire-level turn within session | Uses wire's locked vocabulary (§5.3 of locked design); zero new concepts |
| **D6** | Approval | `hooks-approval@v0.1.0` mounted in bundle (default mode, not `policy_driven_only`) + `WireApprovalProvider` shim + NC adapter auto-allow | Mechanism-vs-policy split: bundle decides what's gated, host decides yes/no |
| **D7** | Session state placement | Host-mounted volume `$HOST_STATE_DIR/$GROUP_ID → /home/node/.local/state/amplifier-agent/` | Q7 — survives restart and image rebuild without polluting workspace |
| **D8** | `AaaError` taxonomy | Add `severity`, `correlationId` to existing `code` + `classification` (additive) | CR-2 — explicit fields the typed approval errors require |
| **D9** | MCP wire | Additive `mcpServers` field on `agent/initialize`; engine threads to `tool-mcp.mount(config={…})`; runtime config has highest priority per A13 | Q9 — closes v1 blocker; uses the module's verified runtime API; no new wire method |
| **D10** | Binary install | `UV_TOOL_BIN_DIR=/usr/local/bin uv tool install amplifier-agent==$VER` at image build | Q4 — matches amplifier-agent's actual shipping model; PATH discovery (locked D5) |
| **D11** | `prepare` placement | Image-build time as `node` user + adapter-side lazy-on-engine_not_primed fallback | Q10 — no first-turn latency cliff; safety net for dev images |
| **D12** | Capability negotiation | Additive `initialize.host.capabilities` field; NC declares `{supports_steering:false, supports_structured_errors:true}` | B+C hybrid; multi-host runway lock; future hosts add flags additively |

### 8.2 D1 — Adapter location

**Surface constrained:** repository file layout; NC build pipeline; where ops looks for adapter issues.

**Locked position:** `container/agent-runner/src/providers/amplifier-agent.ts` (main class) + `container/agent-runner/src/providers/amplifier-agent/` (helpers dir with `event-translator.ts` and `mcp-translator.ts`). Host-side: `src/providers/amplifier-agent.ts` (registration only).

**Alternative rejected:** Host-level provider (parallel to NC's `skills/add-codex`). Rejected because amplifier-agent is wire-uniform across hosts; the adapter belongs where the SDK call happens (in-container).

**Rationale:** Mirrors `claude.ts`/`codex.ts` so adapter authors and reviewers don't context-switch between provider models.

**Monitoring signal:** none direct (structural).

### 8.3 D2 — Adapter scope

**Surface constrained:** how much complexity lives in the adapter; LOC budget for the per-provider boundary.

**Locked position:** ~470 LOC across 3 NC files (300 main + 110 event-translator + 60 mcp-translator). Two pure-function helpers, single state-bearing class.

**Alternative rejected:** Candidate A (single ~280 LOC file, no helpers). Rejected per 4-host lock (§9).

**Rationale:** Smallest surface that supports forward-compat hooks and isolated testability of translation logic.

**Monitoring signal:** `complexity:nc-amplifier-adapter` (LOC count per file in CI metrics).

### 8.4 D3 — `push()` semantics

**Surface constrained:** what happens when NC sends a follow-up while a turn is in flight; user-visible steering latency.

**Locked position:** B1 — adapter buffers `push()` arrivals; at the boundary of the current turn (`turn/completed`), if the buffer is non-empty, the adapter mints a new `SessionHandle` with the same `sessionId` and `resume: true`, calls `submit(buffer.join('\n\n'))`, and continues yielding events on the same NC `AgentQuery`.

**Alternative rejected:** B2 ("wait for next NC `query()` call, prepend buffered text"). Rejected because B2's latency is unacceptable: a buffered follow-up could wait minutes for the next user message. B1 latency = duration of current turn only.

**Rationale:** Closest semantic approximation to Claude's `query.push()` behavior achievable within the locked wire's one-shot constraint.

**Monitoring signal:** `aaa.buffer.chain_links` (R2 trigger).

### 8.5 D4 — Buffer cap + drop

**Surface constrained:** worst-case memory pressure under steering storms; observability of dropped messages.

**Locked position:** cap=256, **visible drop** (overflow emits one `{type:'progress', message:'buffer overflow: N messages dropped'}` per turn at chain boundary).

**Alternative rejected:** cap=32 silent drop (CR-4 disposition input). Rejected: parallel sub-agent dispatch (documented bundle pattern) can saturate 32 quickly; silent drop hides the signal.

**Rationale:** 256 is 4-8x typical parallel-subagent burst observed in app-cli; visible drop preserves operator's ability to detect overflow.

**Monitoring signal:** `aaa.buffer.overflow_count` (R8 trigger).

### 8.6 D5 — Chain vocabulary

**Surface constrained:** how the adapter's internal model maps to the wire's locked concepts.

**Locked position:** One NC `query()` call = one wire-level session (one `sessionId`, persisted as NC's `continuation`). Each chain link = one wire-level **turn** within that session — a fresh `spawnAgent({sessionId: same, resume: true})` followed by one `handle.submit(buffered_text)`.

**Alternative rejected:** Inventing adapter-only terms ("chain link", "buffer flush turn"). Rejected: the wire already names these concepts (locked design §5.3); using wire vocabulary eliminates a glossary.

**Rationale:** Uses the wire's resume mechanism (§5.3 of locked design) exactly as designed; no novel concept enters the implementation.

**Monitoring signal:** none direct.

### 8.7 D6 — Approval

**Surface constrained:** how dangerous tool calls are gated; how host policy attaches; what happens on host disconnect during approval.

**Locked position:**
- **Bundle**: mount `hooks-approval@v0.1.0` in default mode (NOT `policy_driven_only: true`). Default mode = built-in pattern matching + tool metadata gating; sufficient for v1.
- **Engine**: register `WireApprovalProvider` shim (§4.7) on `coordinator._approval_hook` in `_runtime.py`.
- **Wire**: existing `approval/request`/`approval/response` method pair from locked design D7 (no changes).
- **Adapter (NC)**: `onRequest: () => ({decision: 'allow'})` — auto-allow, mirroring Claude's `bypassPermissions` posture per A10.

**Alternative rejected:** Bundle-side bypass (configure `hooks-approval` to never ask). Rejected per user direction: "Well the wire is not just for NC, its for all the hosts. … host specific adapters can turn them on or off." Each host owns its policy.

**Rationale:** Mechanism-vs-policy split applied consistently: bundle defines mechanism, wire carries it, host decides policy.

**Monitoring signal:** `aaa.approval.error_rate` by code (`approval_translation_failed`, `approval_timeout`, `approval_protocol_violation`).

### 8.8 D7 — Session state placement

**Surface constrained:** what persists across container restart; what survives image rebuild.

**Locked position:** Host-mounted volume `$HOST_STATE_DIR/amplifier-agent/$GROUP_ID → /home/node/.local/state/amplifier-agent/` (mode `rw`). Declared in NC's `src/providers/amplifier-agent.ts` per §4.4. Engine writes via `SessionStore` (CR-1 / §4.6).

**Alternative rejected:**
- (i) Container's own filesystem — rejected: doesn't survive restart.
- (iii) Workspace mount — rejected: pollutes user's workspace with state files.

**Rationale:** Standard NanoClaw pattern; matches how Claude transcript persistence works in NC.

**Monitoring signal:** `aaa.session.resume_failure_rate` (R4 trigger).

### 8.9 D8 — `AaaError` taxonomy

**Surface constrained:** what fields NC's adapter can grep against; what operators see in NC's UI.

**Locked position:** Add to existing `AaaError`:
- `severity?: 'error'|'warning'` (CR-2 — approval errors carry severity)
- `correlationId?: string` (operator audit grep target)
- `stderrTail?: string` (redacted by event-translator per CR-3)

`classification` enum expanded to include `'approval'`.

**Alternative rejected:** Flat string error events (Candidate A). Rejected by 4-host runway lock.

**Rationale:** Multi-host capability — typed errors compose naturally with `supports_structured_errors: true` capability flag.

**Monitoring signal:** error rate by `classification` × `severity`.

### 8.10 D9 — MCP wire

**Surface constrained:** how a host adds MCP servers to a per-session agent; how secrets in MCP env reach (or don't reach) operator logs.

**Locked position:**
- **Wire**: additive `mcpServers?: Record<string, McpServerConfig>` on `InitializeParams` (§4.10.1).
- **Engine**: `_runtime.py` reads `params['mcpServers']` and threads to `tool-mcp.mount(coordinator, config={**bundle_static, "servers": params['mcpServers']})` per A13 verification.
- **Wrapper**: identity-pass the field; no shape transformation.
- **Adapter (NC)**: `mcp-translator.ts` shape-validates, identity-passes. No in-band redaction; secret protection is in `event-translator.ts`'s `stderrTail` redaction (CR-3).

**Alternative rejected:**
- (a) env-extra injection — rejected: blocked by `BLOCKED_ENV_KEYS`.
- (b) Per-session config file — rejected: more moving parts; A13 verification confirmed `mount(config=…)` works.
- (d) Add MCP servers to vendored bundle directly — rejected: doesn't compose per-session.

**Rationale:** Closes the v1 reply-channel blocker; uses the verified-stable module API.

**Monitoring signal:** Successful `mcp__nanoclaw__send_message` invocation count (NC-side metric).

### 8.11 D10 — Binary install

**Surface constrained:** Dockerfile complexity; binary location; user permissions in container.

**Locked position:**
```dockerfile
RUN curl -LsSf https://astral.sh/uv/install.sh | sh -s -- --no-modify-path && \
    mv /root/.local/bin/uv /usr/local/bin/uv
ARG AMPLIFIER_AGENT_VERSION
# uv 0.11+ removed the --bin-dir flag; use UV_TOOL_BIN_DIR env var instead (DTU finding F2)
RUN UV_TOOL_BIN_DIR=/usr/local/bin uv tool install "amplifier-agent==${AMPLIFIER_AGENT_VERSION}"
```

Binary lands at `/usr/local/bin/amplifier-agent`. PATH discovery (locked-design D5).

**Alternative rejected:** `pnpm install -g` (no Node wrapper); static binary (no shiv/pyoxidizer artifacts upstream).

**Rationale:** Matches amplifier-agent's actual distribution; one-line install.

**Monitoring signal:** Image-build success rate.

### 8.12 D11 — `prepare` placement

**Surface constrained:** first-turn latency; container start time; failure modes when prepare hasn't run.

**Locked position:**
```dockerfile
USER node
RUN amplifier-agent prepare
RUN amplifier-agent doctor --strict
```

Adapter-side lazy fallback (§4.1.4 generator catches `code: 'engine_not_primed'`, runs `amplifier-agent prepare`, retries `spawnAgent` once).

**Alternative rejected:** Container-start prepare (5–30s wait every container restart); lazy-only (first-turn latency cliff visible to user).

**Rationale:** Production = pre-warmed; dev/debug = lazy fallback.

**Monitoring signal:** `aaa.prepare.lazy_fallback_count` (should be 0 in production).

### 8.13 D12 — Capability negotiation

**Surface constrained:** how future hosts (Paperclip, OpenCode, Claude Code) declare what they support; what fields the engine and bundle can branch on.

**Locked position:** Additive `host?: { capabilities?: HostCapabilities }` field on `InitializeParams`. v1 capabilities:
- `supports_steering?: boolean` — false for NC (B1 buffer is host-side; engine doesn't need to know).
- `supports_structured_errors?: boolean` — true for NC.

NC declares `NC_HOST_CAPABILITIES = { supports_steering: false, supports_structured_errors: true }` (§4.1.1).

**Alternative rejected:** No capability negotiation (Candidate A). Rejected by 4-host runway lock.

**Rationale:** Mechanism for per-host policy without per-host wire forks.

**Monitoring signal:** Capability flag count over time (R7 trigger).

### 8.14 The locked public API additions

Reproduced verbatim:

```typescript
// Additive — no breaking change, no method add.
export interface SpawnAgentParams {
  // ... locked fields from 2026-05-20 design §8.2 unchanged ...
  mcpServers?: Record<string, McpServerConfig>
  host?: { capabilities?: HostCapabilities }
}

export interface HostCapabilities {
  supports_steering?: boolean
  supports_structured_errors?: boolean
}

export interface McpServerConfig {
  transport: 'stdio' | 'sse' | 'streamable_http'
  command?: string
  args?: string[]
  env?: Record<string, string>
  url?: string
  headers?: Record<string, string>
}

export class AaaError extends Error {
  code: string
  classification?: 'transport'|'protocol'|'engine'|'approval'|'unknown'
  severity?: 'error'|'warning'   // NEW
  correlationId?: string          // NEW
  stderrTail?: string             // NEW (redacted by NC adapter when MCP supplied)
}
```

**`PROTOCOL_VERSION` bumps `"0.0.x"` → `"0.1.0"`** (strict-refuse per locked-design D6 unchanged).

**No wire methods added.**

---

## §9 Simplest credible alternative  <a id="s9-simplest"></a>

### 9.1 Candidate A — "Simplest viable" (rejected destination, kept as reference)

Single-file ~280-LOC adapter (`container/agent-runner/src/providers/amplifier-agent.ts`) that mirrors `claude.ts` line-for-line. One class. Closures hold `SessionHandle`, `string[]` push buffer, "current-turn-active" boolean. No helper modules. No `event-translator.ts`. No `mcp-translator.ts`. No `host.capabilities`. No `AaaError.severity` / `correlationId`. No `doctor --strict` image-build gate. Flat-string error events. Synthetic activity ticks fire whenever; no init-emit gate. Buffer cap chosen ad-hoc; silent drop.

LOC budget: ~280 LOC NC + ~120 LOC engine (CR-1 session persistence still required) + 4 lines bundle. Total ~400 LOC vs. ~1,400 for B+C.

### 9.2 Why Candidate A was rejected

1. **4-host runway lock.** User locked: "Multi host is the way, rest of hosts coming soon as soon as we prove NC, paperclip, opencode, claude code are all queued up." Capability flags are load-bearing infrastructure for that, not speculation.
2. **Retrofit cost asymmetry.** Adding `initialize.host.capabilities` after 3 more hosts have shipped against a flag-less wire is cross-repo migration work — each host's adapter must be updated, each host's tests re-run, each host's deployment cadence respected. Candidate A's "reversible at the wire now" becomes "irreversibly compatible-broken across 4 hosts later." The Phase 5 dominant-tradeoff insight applies precisely: wire is irreversible.
3. **Structured-error retrofit cost.** Once NC ships with flat-string errors and operators build alert dashboards on those strings, swapping in structured errors requires NC alert migration. By contrast, shipping with structured errors and letting operators consume only the strings they need is reversible at the consumer's pace.
4. **Critic-surfaced bugs.** Even Candidate A has to address CR-1 (context-simple + SessionStore), CR-3 (stderrTail redaction), CR-4 (visible buffer drop). Once those are added back, A's LOC budget approaches B+C's anyway; the only true savings are `event-translator` and `mcp-translator` extractions (~170 LOC).

### 9.3 What Candidate A would teach if we shipped it

Operationally: nothing. The B+C hybrid is a strict superset of A's user-visible behavior on the happy path. A's only advantage is review-surface size; that advantage is bought by paying retrofit cost in 6 months.

### 9.4 If Candidate A is the right answer in a future iteration

Trigger conditions: (a) the 4-host runway is abandoned or de-prioritized indefinitely; (b) operational data shows zero use of `correlationId` or `classification` in 6 months; (c) the team decides Paperclip/OpenCode/Claude Code adapters will not consume `host.capabilities`.

Under any of those conditions, Candidate A becomes the right answer; this design's helpers can be inlined and the capability surface deprecated. None of those conditions hold at design lock.

---

## §10 Migration and rollout plan  <a id="s10-migration"></a>

### 10.1 Repo and branch context

- **amplifier-agent** (this repo): `feat/phase-2-2-2-3-2-5-wrappers-and-conformance` (current branch). Target release: **`v0.2.0`** with `PROTOCOL_VERSION = "0.1.0"`.
- **nanoclaw**: fresh clone at `/Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh` (upstream main `0683c6e`). Target: new feature branch `feat/amplifier-agent-provider`.

### 10.2 Stages — amplifier-agent repo (ships first)

| ID | Stage | Artifacts | Blocking deps | Acceptance gate | Effort |
|---|---|---|---|---|---|
| **A1** | Wire/protocol bump | `wrappers/{typescript,python}/src/types.{ts,py}`; `wrappers/_gen.py` regen; `schemas/agent-initialize.json`; `PROTOCOL_VERSION` const → `"0.1.0"` | — | TypedDicts compile; `_gen.py` produces matching TS/Py; schema validates; `wrappers/conformance/parity-lint.test` green | **2-3 days** |
| **A2** | SessionStore + IncrementalSaveHook + `_runtime.py` threading (CR-1) | `src/amplifier_agent_lib/session_store.py`, `src/amplifier_agent_lib/incremental_save.py`, `_runtime.py` resume wiring + `register_incremental_save` call | independent of A1 types | Local test: 3-tool turn, restart, resume, transcript matches; `transcript.jsonl` + `metadata.json` + `.backup` siblings present | **3-5 days** |
| **A3** | `WireApprovalProvider` shim with CR-2 error contract | `src/amplifier_agent_lib/wire_approval_provider.py`; `_runtime.py` shim registration | A1 (needs `severity`+`correlationId` on `AaaError`) | Three error codes exercised by fixture `approval-shim-three-error-codes.json`; conformance green | **2-3 days** |
| **A4** | Bundle changes | `src/amplifier_agent_lib/bundle/bundle.md` (4-line edit + new mounts for `hooks-approval`, `tool-mcp`) | A2, A3 | `amplifier-agent prepare` succeeds; `amplifier-agent doctor` reports all four mounts present | **1 day** |
| **A5** | `tool-mcp` threading in `_runtime.py` | `_runtime.py` reads `params['mcpServers']`, threads to `tool-mcp.mount(config=...)` per A13 | A1, A4 | Fixture `initialize-with-mcpservers.json` green; integration test: invoke a mock MCP server through `tool-mcp` | **2 days** |
| **A6** | Wrapper: `BLOCKED_ENV_KEYS` + async `probeEngineVersion` | `wrappers/typescript/src/spawn.ts`, `wrappers/python/src/spawn.py` | A1 | Unit tests: blocked-key throw; `probeEngineVersion` timeout path; existing fixtures still green | **1-2 days** |
| **A7** | `amplifier-agent doctor` subcommand | `src/amplifier_agent_lib/cli/doctor.py`, CLI registration | A2-A5 | `doctor` exits 0 on healthy bundle; `doctor --strict` exits non-zero on any warn; `doctor --emit-sha` produces deterministic output | **2-3 days** |
| **A8** | Conformance fixtures (4 new) | `wrappers/conformance/fixtures/{initialize-with-mcpservers,initialize-with-host-capabilities,approval-shim-three-error-codes,resume-with-session-store}.json` + runners | A1-A7 | All 4 fixtures green in both TS and Py runners; parity lint green | **2-3 days** |
| **A9** | Release `v0.2.0` | tag; PyPI publish; npm publish for `amplifier-agent-client-ts` | A1-A8 | PyPI install works; `npm i amplifier-agent-client-ts@0.2.0` resolves; smoke test passes | **1 day** |

**Amplifier-agent subtotal: ~17–22 working days.**

### 10.3 Stages — nanoclaw repo (consumes amplifier-agent v0.2.0)

| ID | Stage | Artifacts | Blocking deps | Acceptance gate | Effort |
|---|---|---|---|---|---|
| **N1** | Dockerfile changes | `container/Dockerfile` (install uv, `uv tool install amplifier-agent==$VER`, `prepare`, `doctor --strict`) | A9 | Image builds clean; `doctor --strict` passes in build | **1-2 days** |
| **N2** | CI lint | `scripts/lint-aaa-version.ts` | — | Lint catches mismatch between `package.json` pin and Dockerfile `ARG`; CI gate active | **1 day** |
| **N3** | NC host-side provider registration | `src/providers/amplifier-agent.ts`, `src/providers/index.ts`, `src/container-runtime.ts` mount declaration | N1 | `amplifier-agent` appears in `agent_groups.provider` enum; mount visible in container; env passthrough works | **1-2 days** |
| **N4** | In-container adapter + helpers | `container/agent-runner/src/providers/amplifier-agent.ts` + `amplifier-agent/event-translator.ts` + `amplifier-agent/mcp-translator.ts`; `container/agent-runner/src/providers/index.ts` registration | A9, N1 | Unit tests for translator pure-functions green; adapter compiles | **3-5 days** |
| **N5** | E2E happy-path | adapter consumed end-to-end in a real container | N1-N4 | `agent_groups.provider='amplifier-agent'`; user message reaches engine; engine reply reaches user via `mcp__nanoclaw__send_message` | **2-3 days** |
| **N6** | E2E steering (B1 chain) | live test of chained turns via `push()` | N5 | Two-turn chain in single `query()`; both results delivered; transcript persists; buffer overflow signal observable | **1-2 days** |
| **N7** | Phased rollout | enabled-flag plumbing; canary group | N5, N6 | R0 internal canary → R1 5% → R2 default-for-new → R3 fleet | **2 days dev, weeks calendar** |

**NanoClaw subtotal: ~11–17 working days (excluding rollout calendar).**

### 10.4 Critical path

```
A1 ──► A2 ──► A4 ──► A5 ──► A7 ──► A8 ──► A9 ──► N1 ──► N3 ──► N4 ──► N5 ──► N6 ──► N7
       │      ▲                                          ▲
       ▼      │                                          │
       A3 ───┘                                       (A6 needed by N4)
                                                                ▲
                                                                │
                                                              A6 ── A1
```

Critical path: **A1 → A2 → A4 → A5 → A7 → A8 → A9 → N1 → N3 → N4 → N5 → N6 → N7**, total ~31 working days (parallel work in A3 and A6 saves ~3 days off naive sum). Calendar ~8 weeks accounting for review cycles, conformance runs, canary observation windows.

### 10.5 Rollback plan

**amplifier-agent rollback (per stage):**
- A1-A8: revert PR; conformance fixtures will catch shape regressions.
- A9 (published): tag `v0.2.1-rollback` republishing previous `types.ts` (additive fields preserved in schema but not consumed); pin downstream consumers to `^0.1`.
- The wire-level changes are **additive**; rollback at the wire is a no-op for unaware consumers (they ignore unknown fields).

**NanoClaw rollback (per stage):**
- N1: revert Dockerfile; rebuild image; previous Claude/Codex providers unaffected.
- N3, N4: revert provider registration; `provider='amplifier-agent'` becomes invalid value; existing sessions on Claude/Codex unaffected.
- N7 rollback: disable feature flag; new sessions fall back to default provider; in-flight `amplifier-agent` sessions drain naturally (no kill).

### 10.6 Bundle-source-compromise emergency runbook

If a vendored bundle module source repo is compromised (e.g., a malicious commit to `microsoft/amplifier-module-hooks-approval@main`):

1. **Detect**: `amplifier-agent doctor --emit-sha` baseline diff (run daily in CI on a known-good build).
2. **Halt builds**: pause amplifier-agent CI; disable NC image-rebuild pipeline.
3. **Identify**: `git log` on suspect module repo; verify SHA against last known good.
4. **Pin**: edit `bundle.md` to pin the suspect module to the last-known-good SHA (`@<sha>` instead of `@main`).
5. **Rebuild**: amplifier-agent CI rebuilds with pinned SHA; `doctor --strict` validates.
6. **Roll forward**: NC pulls the new amplifier-agent version; image rebuild.
7. **Audit**: review production logs for tool-call patterns matching known-bad behavior.

R6 (deferred SHA-pinning) is the residual exposure; `--emit-sha` is the enablement for future-default SHA pinning.

### 10.7 Phased NC rollout

| Phase | Audience | Duration | Advance criteria |
|---|---|---|---|
| **R0** | Internal canary (L3 + NC team session groups) | 1-2 weeks | E2E happy-path + steering both green; no `engine_crashed` errors |
| **R1** | 5% of new session groups | 1 week | `aaa.turn.spawn_ms` P95 < 6s; `aaa.session.resume_failure_rate` < 0.5%; no R3/R6/R8 signals |
| **R2** | Default for **new** sessions; existing sessions unchanged | 2 weeks | Operator confirms no regression vs Claude/Codex on shared metrics |
| **R3** | Fleet ramp (existing sessions migrate by group cycle) | 4-6 weeks | Steady-state metrics within bands; no rollback signals |

Rollback gates at every transition (R0→R1, R1→R2, R2→R3): any of R1-R8 trigger condition or any CR-class regression → revert to previous phase, root-cause, retry.

### 10.8 Known constraints and build-time prerequisites

#### `file:` package reference for `amplifier-agent-client-ts`

**DTU verification finding F3.** The dev-time pin in `container/agent-runner/package.json` (`"amplifier-agent-client-ts": "file:../../../amplifier-agent/wrappers/typescript"`) works in repo-sibling dev layouts and in DTU, but does **NOT** work in a standalone `docker build` because the Dockerfile build context (typically `container/`) does not include the sibling amplifier-agent tree. To produce a buildable image, EITHER (a) publish `amplifier-agent-client-ts@0.2.0` to npm and switch the pin to `^0.2.0`, OR (b) add a `COPY` step in the Dockerfile that brings the wrapper source into the build context (requires running `docker build` from the parent directory or using `--build-context wrapper=../amplifier-agent/wrappers/typescript`). This is tracked as DTU verification finding F3.

---

## §11 Success metrics  <a id="s11-metrics"></a>

### 11.1 Production metrics

| # | Metric | Definition | Data source | Advance gate (R1→R2) | Rollback gate | Cadence |
|---|---|---|---|---|---|---|
| **M1** | Turn success rate | `(turns reaching turn/completed) / (turns started)` | engine logs | ≥ 99.0% | < 97% over 24h | continuous |
| **M2** | Spawn latency P50 | `spawn_ms` median | adapter metric | ≤ 4s | > 6s sustained 24h | continuous |
| **M3** | Spawn latency P95 | `spawn_ms` P95 | adapter metric | ≤ 6s | > 10s sustained 24h | continuous |
| **M4** | Resume failure rate | `(resume sessions ending in stale-session error) / (resume sessions)` | adapter + engine | ≤ 0.5% | > 1% (R4 trigger) | continuous |
| **M5** | Buffer overflow rate | overflows per 24h | adapter audit | 0 expected | > 5/day (R8 trigger) | continuous |
| **M6** | Chain length P95 | chain links per NC `query()` | adapter metric | ≤ 2 | > 6 (R2 trigger) | weekly |
| **M7** | Approval shim error rate by code | counts per code per 24h | engine `AaaError` audit | 0 (`approval_translation_failed`) / 0 (`approval_protocol_violation`) | any non-zero > 24h → investigate | continuous |
| **M8** | Approval timeout rate | `approval_timeout` / `approval/request` total | engine | ≤ 0.1% | > 1% sustained | continuous |
| **M9** | Lazy prepare invocation count | adapter-side `engine_not_primed` retries | adapter metric | 0 in R1+ | any in R2+ | continuous |
| **M10** | MCP-secret stderr leak | post-redaction `AaaError.stderrTail` containing known MCP env values | adapter audit | 0 | any (R3 trigger) | continuous |
| **M11** | Doctor-strict CI pass rate | image builds where `doctor --strict` exits 0 | CI | 100% | < 100% → investigate | per build |
| **M12** | Wire skew incident count | runtime `protocol_mismatch` errors | engine | 0 | any (R5 trigger — CI lint gap) | continuous |
| **M13** | Capability flag count | distinct flags in `host.capabilities` across all hosts | wire telemetry | ≤ 4 at v1; ≤ 6 over 6 months | > 6 (R7 trigger) | monthly |

### 11.2 Failure-signal early indicators

| Signal | Source | Threshold | Action |
|---|---|---|---|
| `aaa.buffer.chain_links` P95 climbing | adapter | +50% week-over-week | investigate steering-burst pattern; possible parallel-subagent hotspot |
| `AaaError.classification='engine'` rate climbing | engine | +50% wow | possible bundle module regression; check recent module updates |
| `aaa.session.resume_failure_rate` climbing | adapter | +50% wow | possible volume-driver issue (A8 / A9); check NC infra |
| `doctor --emit-sha` daily diff | CI | any change without amplifier-agent version bump | possible upstream module compromise (R6 enablement) |

### 11.3 Operational dashboards

NC ops dashboard adds an "amplifier-agent" panel with M1–M13. Engine team's amplifier-agent dashboard adds the four early-indicator signals. Both link cross-correlated via `correlationId` (D8).

---

## Appendix A — v1.x deferrals  <a id="appx-a-deferrals"></a>

Twelve items deferred from v1, each with promotion trigger.

| ID | Item | Source disposition | Promotion trigger |
|---|---|---|---|
| **D-v1.x-01** | Wire-level steering (`turn/inject` JSON-RPC notification) | D3 / R2 wire-gap log | R2 trigger: chain P95 > 6 |
| **D-v1.x-02** | SHA-pinning bundle module sources by default | R6 | Any module-source compromise detected, or `--emit-sha` daily-diff baseline mature |
| **D-v1.x-03** | Per-tenant MCP allowlist (filter seam in `mcp-translator.ts`) | Phase 4 Candidate B deferral | NC product requirement for tenant isolation |
| **D-v1.x-04** | `amplifier-agent doctor --quick` background health probe | Phase 5 C-not-selected | Reliability dashboard shows undetected-failure latency > 5min |
| **D-v1.x-05** | Drift-digest detection (mid-session bundle change) | Phase 5 C-not-selected | Any mid-session image-rebuild observed |
| **D-v1.x-06** | Audit log per-session-group on host-mounted volume | Phase 5 C-not-selected | Operator forensic-capability request |
| **D-v1.x-07** | Circuit breaker on lazy prepare (max 1 retry / 5min window) | Phase 5 C-not-selected | M9 lazy-fallback > 0 in R2+ |
| **D-v1.x-08** | `doctor --quick` adapter-side liveness probe | SC adjacent | M-class staleness signal |
| **D-v1.x-09** | Sub-agent `progress` event surfacing | SC-5 (user direction) | NC UX request to show sub-agent activity to users |
| **D-v1.x-10** | `lifecycle: 'burst'` mode for short-turn UX | R1 / D10 | R1 trigger: spawn P50 > 8s |
| **D-v1.x-11** | B2 fallback ("wait for next turn") steering policy | D3 | R8 trigger: buffer overflow > 5/day |
| **D-v1.x-12** | Engine-side stderr redaction (defense-in-depth beyond adapter CR-3) | CR-3 residual | Any post-redaction MCP-value leak observed |

---

## Appendix B — Phase 6 critic review summary  <a id="appx-b-critic"></a>

### B.1 Critical Risks (CR) — 4 total, all closed in v1

| ID | Finding | Resolution |
|---|---|---|
| **CR-1** | Bundle pointed at `context-persistent` (does not exist in foundation); canonical pattern is `context-simple` + app-layer `SessionStore`/`IncrementalSaveHook` (per `amplifier-app-cli`). | **Closed.** Bundle edited (§4.11). `session_store.py` and `incremental_save.py` added (§4.6). `_runtime.py` threaded (§4.8). Verified by foundation-expert against `amplifier-app-cli@main`. |
| **CR-2** | `WireApprovalProvider` shim had no error contract — failures would surface as opaque `engine_crashed`. | **Closed.** Three typed error codes (`approval_translation_failed`, `approval_timeout`, `approval_protocol_violation`) with explicit `classification: 'approval'`, surfaced via `AaaError.severity` + `correlationId` (D8). Conformance fixture `approval-shim-three-error-codes.json` exercises each. |
| **CR-3** | MCP secrets could reach NC operator console via `AaaError.stderrTail` (Python tracebacks include local vars). | **Closed.** `event-translator.ts` redacts `stderrTail` whenever `mcp-translator.ts` declared MCP config was supplied (§4.2). Defense-in-depth: engine-side redaction deferred as D-v1.x-12. |
| **CR-4** | B1 buffer cap=32 silent-drop broke documented parallel-subagent dispatch pattern. | **Closed.** Cap raised to 256, drop policy → visible (overflow emits `progress`). R8 monitors. |

### B.2 Significant Concerns (SC) — 7 total

| ID | Finding | Resolution |
|---|---|---|
| **SC-1** | Activity ticker could fire before `init` if generator order was wrong. | Adapter generator gates ticker start until after `init` yield (§4.1.4). |
| **SC-2** | `hooks-logging` mount in bundle wrote to ephemeral in-container path. | Removed from bundle (§4.11). |
| **SC-3** | `env.extra` accepted any key — `PYTHONPATH`/`LD_PRELOAD` injection risk. | `BLOCKED_ENV_KEYS` validation in `buildEnv()` (§4.12.1). |
| **SC-4** | `doctor --strict` didn't emit bundle SHAs needed for future SHA-pinning. | `doctor --emit-sha` added (§4.9). |
| **SC-5** | Sub-agent progress event surfacing — design ambiguity. | User direction: defer to v1.x. Decision #7 collapses to `{type:'activity'}`. Capability `supports_subagent_progress` removed (D12 simplification). |
| **SC-6** | Conformance fixtures didn't cover the new wire surface. | Four new fixtures added (§4.14). |
| **SC-7** | `probeEngineVersion()` was synchronous — couldn't timeout cleanly. | Made async (§4.12.2). |

### B.3 Observations (O) — 8 total

- **O-1**: 2026-05-20 design doc's references section needed an update to reflect `mcpServers` capability. → Addressed in A1 stage migration.
- **O-2**: `bundle.md` prose at lines 213-216 referenced deferred work now in-scope. → Removed in A4.
- **O-3**: Conformance fixture naming convention could be more discoverable. → Adopted `{topic}-{scenario}.json` convention going forward.
- **O-4**: NC's `provider.options` shape under-documented. → Out of scope; flagged for NC team.
- **O-5**: `amplifier-agent prepare` doesn't currently emit progress. → Out of scope.
- **O-6**: Locked-design D6 doesn't define behavior for additive field unknowns on receivers. → Existing TypedDict tolerance is sufficient; clarification noted.
- **O-7**: Phase 5 mentioned "circuit breaker on lazy prepare" without sizing it. → D-v1.x-07 carries the sizing question forward.
- **O-8**: NC's `continuation` semantics around expiry are implicit. → Carry-forward observation for NC documentation team.

---

## Appendix C — Deployment checklist  <a id="appx-c-checklist"></a>

### C.1 amplifier-agent release (v0.2.0)

- [ ] A1 — `PROTOCOL_VERSION` = `"0.1.0"` committed; types regenerated TS+Py; schemas regenerated; parity-lint green.
- [ ] A2 — `session_store.py` + `incremental_save.py` committed; `_runtime.py` resume wiring committed; local resume test passes.
- [ ] A3 — `wire_approval_provider.py` committed; three error codes exercised in test.
- [ ] A4 — `bundle.md` edits committed (context-simple, hooks-approval added, tool-mcp added, hooks-logging removed); `amplifier-agent prepare` succeeds.
- [ ] A5 — `_runtime.py` MCP threading committed; `tool-mcp.mount(config={...})` works.
- [ ] A6 — Wrapper `BLOCKED_ENV_KEYS` + async `probeEngineVersion` committed; tests green.
- [ ] A7 — `amplifier-agent doctor --strict` committed; healthy bundle exits 0.
- [ ] A8 — 4 new conformance fixtures green in TS and Py runners.
- [ ] A9 — Tag `v0.2.0`; PyPI publish; npm publish; smoke test from clean install.

### C.2 NanoClaw consumption

- [ ] N1 — Dockerfile updated: uv install, `uv tool install amplifier-agent==0.2.0`, `prepare`, `doctor --strict`; image builds clean.
- [ ] N2 — `scripts/lint-aaa-version.ts` committed; CI gate active.
- [ ] N3 — Host-side `src/providers/amplifier-agent.ts` committed; mount declared in `container-runtime.ts`.
- [ ] N4 — In-container adapter + helpers committed; unit tests green; `package.json` pin matches Dockerfile ARG.
- [ ] N5 — E2E happy-path test passes: user → channel → engine → reply via `mcp__nanoclaw__send_message`.
- [ ] N6 — E2E steering test passes: B1 chain delivers two results in single `query()`.
- [ ] N7 — Feature flag plumbing active; R0 canary group enabled; R1 5% canary configured.

### C.3 Post-deployment monitoring (first 7 days of R1)

- [ ] M1-M13 dashboards configured and visible.
- [ ] On-call playbook updated with R1-R8 trigger conditions.
- [ ] Bundle-source-compromise runbook accessible.
- [ ] `correlationId` cross-system linking verified end-to-end (adapter → engine → audit).
- [ ] Phased rollout gate review scheduled at end of R1 window.

---

## Appendix D — Wire-gap log  <a id="appx-d-wire-gaps"></a>

| ID | Gap | v1 disposition | Future wire fix nominee |
|---|---|---|---|
| **WG-1** | **Steering mid-turn.** Wire is one-shot per subprocess; NC `push()` cannot interrupt an in-flight turn at the wire level. | Adapter workaround: B1 buffer + multi-turn-in-session chaining (D3, D5). Latency cost: wait for current turn boundary. Monitored via R2. | `turn/inject` JSON-RPC notification (D-v1.x-01). Wire-level handover of additional user input mid-turn; engine merges into current iteration's context. Promotion: R2 trigger or any second host requesting it. |
| **WG-2** | **Host-supplied MCP servers.** Locked design did not anticipate per-session host MCP config; engine bundle was static-only. | **Closed in v1** by additive `mcpServers` field on `agent/initialize` (D9 / §4.10.1). `tool-mcp.mount(config={...})` runtime API verified (A13). No wire method added. | (Closed — not a future deferral.) |

---

*End of design document.*

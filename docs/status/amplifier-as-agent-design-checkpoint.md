# Amplifier as Agent (AaA) v2 — Design Checkpoint

**Status:** CHECKPOINT (not final). Layer 4 sections are locked and ready for implementation. Layer 1–3 sections that depend on Layer 4 evidence (cold-start measurement, bundle cache behavior, L14 cross-language contract validation) are deferred by design.

**Audience (primary):** Brian Krabach
**Audience (secondary):** Manoj (designer / implementation lead)
**Sources of authority:**
- `docs/presentations/Amplifier as Agent Sync - Summary.md` (Sync of 2026-05-15 — directive)
- Sync transcript at `/tmp/aaa-transcript.txt` (validated against summary)
- `docs/designs/aaa-architecture.md` (V1, "Comprehending Existing" lens)
- `docs/designs/aaa-phase-2-brainstorm.md` (superseded; reused where applicable)
- `https://github.com/microsoft/amplifier-foundation/blob/.../docs/APPLICATION_INTEGRATION_GUIDE.md` (foundation contract, §6 + §7)
- `microsoft/amplifier-app-openclaw` (sibling design, validates one-shot stateful via logical replay)
- `microsoft/amplifier-foundation` kernel (unchanged by this design)

**Target repository:** `github.com/microsoft/amplifier-agent` (new, fresh-build)

---

## Table of contents

1. Executive summary
2. Background and context
3. Architectural overview
4. Repository structure
5. Naming
6. Brian's 9-section deliverable
   - §1 Host adapters — **LOCKED** (NanoClaw + Paperclip POC)
   - §2 Install paths — **DEFERRED**
   - §3 CLI — **LOCKED**
   - §4 Language wrappers — **LOCKED** (design)
   - §5 Non-server design + naming — **LOCKED**
   - §6 Approval and display bubble-up — **LOCKED**
   - §7 Containers — **DEFERRED**
   - §8 Spawn — **LOCKED** (library-internal)
   - §9 Execution plan — **Layer-4-first**
7. Success metrics
8. Risks and monitoring signals
9. What's deferred and why
10. Open items
11. Appendices
    - A. Wire protocol — methods, notifications, errors, capabilities
    - B. L14 synthesis contract
    - C. Mode A vs Mode B comparison
    - D. V1 carryforwards and V1 drops
    - E. Glossary

---

## 1. Executive summary

**What AaA v2 is.** A new repository (`github.com/microsoft/amplifier-agent`) that ships the Amplifier kernel as a callable from arbitrary host applications. Five layers:

```
L1  Host harnesses (NanoClaw, Paperclip, future)            EXISTING
L2  Host-specific adapters (TS packages, one per host)      REBUILD
L3  Per-language wrappers (TS + Python, day-one)            NEW (split from V1's TS-only client)
L4  amplifier_agent_lib + amplifier_agent_cli (Python)      NEW (replaces V1 serve mode)
L5  amplifier-foundation kernel                             UNCHANGED
```

**The shipping artifact at L4:** one Python package containing (a) a transport-agnostic library `amplifier_agent_lib` and (b) a thin CLI binary `amplifier_agent_cli` exposing two invocation modes:

- **Mode A** — `amplifier-agent run "prompt" --session-id X [--resume]` — single-turn from argv, exits after the final result. For shell-out callers (OpenClaw skills, bash, Claude Code, Codex CLI, Gemini CLI, Copilot, CI scripts, anything Brian described at 57:18 as "anything that can shell out").
- **Mode B** — `amplifier-agent run --stdio` — multi-turn JSON-RPC over stdin, exits on EOF or `agent/shutdown`. For wrapper-driven hosts that amortize bundle load across a conversation burst (NanoClaw today; future Python hosts).

**Key strategic decisions.**

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | **Rebuild from scratch.** Not an evolution of V1. | V1's `cachedClient`, `serve` verb, and adapter-owned spawn directly contradict Brian's D1/D2/D3. Rebuild eliminates the critic's HIGH-severity carry-forward bugs (the `session.ts` `this.active` race; the L14 synthesis with no cross-language contract). |
| 2 | **Vendored opinionated manifest.** | Manifest text and four sub-session agent files are vendored in the wheel; modules referenced by the manifest live in their own repos at `@main` and are git-cloned on first invocation. First-run cost: 5–30 s. Subsequent runs hit the warm XDG pickle in <1 s. Per Strategy 1 of `docs/designs/2026-05-19-baked-in-bundle-decision.md`. |
| 3 | **One reactive stdio coprocess per invocation. Not a server.** | Eight definitional axes distinguish this from a server (no daemon, no listener, no supervisor, no port, single-client, parent-owned, dies with caller, no state across clients). The honest taxonomy term is "stdio coprocess." |
| 4 | **Two modes, one engine.** | The library `amplifier_agent_lib` is mode-agnostic — it never touches stdin/stdout directly. The CLI binary wraps it twice. This is the layer that absorbs the duality cleanly. |
| 5 | **Day-one languages: TypeScript + Python.** | Brian 33:46. Wrappers are siblings, not stacked. CLI is a third sibling — all three are thin layers over the library. |
| 6 | **Spawn library-internal.** | Reverses V1 Decision #6. No adapter override. Three protocol points (Approval, Display, Streaming-folded-into-Display) remain externally exposed; spawn is removed from the external surface per Brian's D3. |
| 7 | **Logical replay for session continuity.** | Per OpenClaw's validated pattern. State = transcript on disk (owned by `amplifier-module-context-persistent`, not by AaA). AaA does not require new kernel serialization APIs. |
| 8 | **Lifecycle policy lives in the wrapper layer.** | `lifecycle: 'one-shot' | 'burst'` is a wrapper parameter. The engine cannot tell the difference; the CLI binary maps argv shape onto the policy. |

**What's locked vs deferred.**

| Section | Status | Rationale |
|---|---|---|
| §1 Host adapters (NC + PC POC) | **LOCKED** design | Both shapes fully specified |
| §2 Install paths | **DEFERRED** | Awaits L4 packaging shape |
| §3 CLI | **LOCKED** | |
| §4 Wrappers | **LOCKED** design; implementation post-L4 | Wire spec is the day-one artifact |
| §5 Non-server + naming | **LOCKED** | |
| §6 Approval + display | **LOCKED** | |
| §7 Containers | **DEFERRED** | Awaits L4 install-time hook behavior |
| §8 Spawn | **LOCKED** | Library-internal |
| §9 Execution plan | **Layer-4-first** | Full multi-phase plan refines post-L4 evidence |

**Honest framing for Brian** (recommended verbatim into the executive summary you carry into our next sync):

> Built-in bundle gives us near-instant cold-start AFTER FIRST INVOCATION as you predicted (first run pays the documented 5–30 s install cliff); we'll measure and confirm. The architectural pattern we're proposing is two modes: per-call (`run "prompt"` — exactly what you described) and per-burst (`run --stdio` — the same wire your team's existing host already uses for the Codex provider in NanoClaw). The per-burst extension goes beyond "per call" literally, but stays inside your "MCP pattern" framing and your 27:08 escape clause. If cold-start lands under 200ms, we drop per-burst and ship only per-call. Calling either of these "a server" would be sloppy taxonomy: no daemon, no listener, no supervisor, no port, single-client, parent-owned, dies with the caller. The honest term is "stdio coprocess."

---

## 2. Background and context

### 2.1 Brian's six locked architectural decisions (Sync §1)

| # | Topic | Decision |
|---|---|---|
| D1 | Run as a service? | **No.** Mirror MCP pattern: JSON-RPC over stdio, host spawns Amplifier session, session stand-up near-instant after first invocation (first-run pays 5–30 s; warm cache is sub-second). *("It literally just launched, ran, shut down, and so you didn't have a running service" — Brian 25:21.)* |
| D2 | Internal flexibility | **Seal it.** Bundle, spawn, delegation — internal, opinionated, not exposed. *("we've taken an opinionated stand, like we've packaged up like this and it's pretty sealed" — Brian 10:00.)* |
| D3 | Spawn / delegation | **Bake in, no host customization.** Use the App CLI session spawner. |
| D4 | Bundle | Stays internal. **Buildup bundle as default.** |
| D5 | Provider selection | Auto-detect env vars; prompt if missing; never user-edited config files. |
| D6 | Approval / display / streaming | Two patterns exposed (callback-hook and message-back); pick per host. **Approval and display are the two edges that must bubble up through every layer.** |

### 2.2 Naming directives (Sync §2)

- **"Amplifier Agent"** = the CLI-level package (`amplifier-agent`). The thing a user installs and runs.
- **Per-language wrappers get a different name.** They are host-adapter-author libraries, not the CLI. *("those should not be named amplifier agent either. The individual language wrappers" — Brian 59:26.)*
- **Day-one languages: TypeScript + Python.** Go deferred. *("just the two that we need right off the bat. And the reason for Python is because I want to also use this whole thing in other places that we're using in this primary [work]" — Brian 33:46.)*
- **CLI, TS wrapper, Python wrapper are siblings** — all three are "thin layers in front of the engine" (Brian 58:14, 58:45). The wrappers do NOT sit on top of the CLI.

### 2.3 V1 evidence base

The V1 architecture (`docs/designs/aaa-architecture.md`) shipped with NanoClaw and Paperclip in production. Captured learnings:

- **NC-L1..L5** — container env contract; PATH, SSL_CERT_FILE, AaA workdir, mount roots
- **NC-L6..L13** — provider integration; idleHeartbeat, AgentQuery shape, continuation strings
- **NC-L14, NC-L15** — `result/final` synthesis (apex bug; current fix lives in `host-client-ts/src/session.ts`)
- **NC-L16** — concurrent-submit race in `session.ts` (`this.active` overwrite — see §3 below for v2 disposition)
- **PC-L1..L10** — Paperclip lifecycle: `ServerAdapterModule.execute(ctx)`, `wakeReason`, `sessionParams`, `ctx.onLog` JSONL → post-hoc UI parser
- **PC-L11..L18** — install plumbing: wheel automation, SHA verification, install-skill staleness, `tool-delegate` trap, PATH-loss-on-sparse-env (= MCP-fixed pattern)

These are the bugs and contracts v2 either preserves (as working host contracts) or designs out (as workarounds replaced by L3 lifecycle policies). See Appendix D.

### 2.4 Critical research inputs

| Input | What it settled |
|---|---|
| **MCP cross-check** | Wire framing, subprocess spawn contract, capability negotiation, and elicitation accept/decline/cancel pattern are copy-paste-grade wins. MCP's lifecycle (per-session stateful) is NOT what Brian described — we adopt the wire mechanics, not the lifecycle. |
| **Host provider lifecycle research** | NanoClaw / Paperclip / OpenClaw use three fundamentally different lifecycle models. **No single lifecycle fits all hosts.** Lifecycle must be a wrapper-level policy. |
| **OpenClaw pattern (`amplifier-app-openclaw`)** | Validates one-shot stateful via logical replay. Microsoft ships it in production on the same kernel. State = transcript on disk; rebuild from bundle each invocation. **Resolves the systems-design-critic's CRITICAL finding** about kernel serialization not existing. |
| **Codex-in-NanoClaw pattern** | NanoClaw's `AgentProvider` contract empirically accommodates subprocess-backed providers. Codex provider spawns `codex app-server --listen stdio://` per `query()` invocation — medium-lived subprocess, multi-turn. This is the precise pattern Mode B mirrors. |
| **Application Integration Guide §6 + §7** | Five lifecycle patterns (A–E); session-ID reuse + context-persistent module = the built-in mechanism for stateful continuity. **PreparedBundle is singleton; sessions are ephemeral.** The four protocol points (Approval, Display, Streaming, Spawn) are named as THE integration contract — we reduce to three (Spawn is library-internal) per Brian's D3. |
| **Systems-design-critic adversarial review** | Surfaced (a) the "build resident-capable, get one-shot free" claim was inverted; (b) `session.ts` `this.active` race; (c) load-bearing unmeasured cold-start. (a) and (b) drove the rebuild-from-scratch decision. (c) drives the Phase 2.0d cold-start measurement gate. |

---

## 3. Architectural overview

### 3.1 The five layers (v2 modifications inline)

```
Layer 1  Host Harnesses (external)
         NanoClaw · Paperclip · future
                ↓ host contract (preserved from V1 per host)
Layer 2  Host-Specific Adapters (TS packages)
         amplifier-nanoclaw-adapter · amplifier-paperclip-adapter
         · lifecycle adapter        · context translation
         · approval routing          · display routing
                ↓ wrapper API (amplifier-agent-client-{ts,py})
Layer 3  Per-Language Wrappers (NEW: split from V1 single-language client)
         amplifier-agent-client-ts · amplifier-agent-client-py
         · spawnAgent({lifecycle, sessionId, ...})
         · Transport (subprocess + readline + framing)
         · JsonRpcClient (request/response correlation, per-id routing — designs out NC-L16)
         · SessionHandle (submit→AsyncIterable; L14 contract enforced)
         · concurrency contract: one in-flight submit per session
                ↓  PROCESS BOUNDARY — NDJSON JSON-RPC 2.0 over stdio
Layer 4  amplifier_agent_lib + amplifier_agent_cli  (NEW, replaces V1 serve mode)
         · amplifier_agent_lib (transport-agnostic library)
           · Engine: boot, submit_turn, dispatch, shutdown
           · protocol_points/: ApprovalSystem, DisplaySystem, Spawn (lib-internal)
           · persistence: XDG cache + bundle prepare
           · spawn: CLISpawnManager-equivalent for delegate/recipe/sub-agents
           · Built-in vendored bundle (foundation, opinionated)
         · amplifier_agent_cli (thin I/O adapter)
           · Mode A: run "prompt" → single-turn → exit
           · Mode B: run --stdio → JSON-RPC loop → exit on EOF
           · Admin: doctor, config show, cache clear
                ↓
Layer 5  Amplifier Kernel  (UNCHANGED)
         amplifier-foundation: PreparedBundle, AmplifierSession,
         ModuleCoordinator, Orchestrator, providers
                ↓
External Anthropic API · OpenAI · Azure OpenAI · Ollama
```

### 3.2 What survives from V1, what's discarded

**Survives (working contracts to preserve):**
- NanoClaw host contract: `AgentProvider.query(input) → AgentQuery` with `idleHeartbeat: true`, `continuation` string
- Paperclip host contract: `ServerAdapterModule.execute(ctx)` with `wakeReason`, `sessionParams`, `ctx.onLog` JSONL, `idleHeartbeat: false`
- Adapter package names (`amplifier-nanoclaw-adapter`, `amplifier-paperclip-adapter`) — avoids V1-C7 persisted `amplifier_local` type-identifier migration cost
- NDJSON JSON-RPC 2.0 framing
- The capability-negotiation-at-initialize shape
- L14 `result/final` synthesis logic (now elevated to a named cross-language contract — see Appendix B)
- NC-L1..L5 container env contract (when containers re-enter the design at §7)

**Discarded (workarounds replaced by v2 structure):**
- V1 `serve` verb. Gone.
- V1 `cachedClient` per-container pattern. Replaced by L3 `lifecycle: 'burst'` policy.
- V1's adapter-owned spawn (`spawn_fn` parameter on `AmplifierAgentConfig`). Library-internal in v2 per D3.
- V1's `prepared.create_session()` second factory path. Single path: `Engine.boot()` returns one session per process.
- V1 `mount_plan` truthy-vs-semantic bug (NC-L8). Replaced by the vendored opinionated manifest (D4 + Strategy 1) — no host-facing mount plan to validate; the manifest text is sealed per release, modules are at @main.
- V1 `this.active` race (NC-L16). Designed out via per-request-id routing in the wrapper.
- V1's `tool-delegate` trap (PC-L17). Replaced by library-internal spawn (D3 + §8 lock).
- V1's manual wheel automation, SHA verification, install-skill staleness (PC-L11/L12/L10). Replaced by `uv tool install amplifier-agent` and a vendored bundle.

### 3.3 Why rebuild rather than evolve

The systems-design-critic's adversarial review surfaced two HIGH-severity findings about evolving V1:

1. The `session.ts` concurrent-submit race exists today and would carry forward as a latent bug in v2 if we ported the V1 client.
2. The L14 `result/final` synthesis has no cross-language contract — a v2 evolution would force us to port the workaround into Python and pray we caught every edge case.

Rebuild collapses both into "designed out on day one." A written wire contract with conformance tests in both TS and Python eliminates the port-the-bug risk by construction. The engineering effort is comparable to V1 evolution; the output is materially cleaner.

---

## 4. Repository structure

Target structure at `github.com/microsoft/amplifier-agent`:

```
amplifier-agent/
├── README.md
├── pyproject.toml                          # publishes amplifier-agent CLI
├── package.json                            # workspace root for TS packages
│
├── amplifier_agent_lib/                    # L4 library (Python, importable, NO transport)
│   ├── __init__.py
│   ├── engine.py                           # Engine class: boot, submit_turn, dispatch, shutdown
│   ├── persistence.py                      # XDG cache + bundle prepare
│   ├── spawn.py                            # CLISpawnManager equivalent (library-internal)
│   ├── protocol_points/
│   │   ├── __init__.py
│   │   ├── approval.py                     # ApprovalSystem interface
│   │   ├── display.py                      # DisplaySystem interface (incl. streaming)
│   │   ├── defaults_cli.py                 # Mode A defaults (stderr, tty-prompt, etc.)
│   │   └── defaults_stdio.py               # Mode B defaults (JSON-RPC bridge)
│   ├── protocol/                           # wire shapes (single source of truth)
│   │   ├── methods.py
│   │   ├── notifications.py
│   │   ├── errors.py
│   │   └── capabilities.py
│   └── _bundle/                            # VENDORED built-in bundle (source)
│       └── ...
│
├── amplifier_agent_cli/                    # L4 thin CLI binary
│   ├── __init__.py
│   ├── __main__.py                         # entry point: dispatch run/doctor/config/cache
│   ├── modes/
│   │   ├── single_turn.py                  # Mode A: run "prompt" → JSON → exit
│   │   └── stdio_loop.py                   # Mode B: run --stdio → loop → exit on EOF
│   └── admin/
│       ├── doctor.py
│       ├── config_show.py
│       └── cache_clear.py
│
├── protocol/                               # cross-language wire spec + conformance tests
│   ├── spec.md                             # the authoritative wire contract
│   ├── schemas/                            # JSON Schema for methods, notifications, errors
│   └── conformance/
│       ├── ts/                             # conformance suite in TS
│       └── py/                             # conformance suite in Python
│
├── wrappers/
│   ├── typescript/                         # amplifier-agent-client-ts (npm)
│   │   ├── package.json
│   │   ├── src/
│   │   │   ├── index.ts                    # spawnAgent(...) public API
│   │   │   ├── transport.ts                # subprocess + readline + framing
│   │   │   ├── jsonrpc.ts                  # client (per-request-id routing)
│   │   │   ├── session.ts                  # SessionHandle, L14 enforcement
│   │   │   └── concurrency.ts              # per-session submit serialization
│   │   └── test/
│   └── python/                             # amplifier-agent-client-py (PyPI)
│       └── ...                             # mirror structure
│
├── adapters/
│   ├── nanoclaw/                           # amplifier-nanoclaw-adapter (TS)
│   │   ├── host-side/                      # env config, mount metadata, wizard injection
│   │   └── container-side/                 # provider implementation, idleHeartbeat: true
│   └── paperclip/                          # amplifier-paperclip-adapter (TS)
│       └── ...                             # ctx.onLog JSONL, idleHeartbeat: false
│
└── docs/
    ├── designs/
    │   └── aaa-v2-design-checkpoint.md     # this document
    └── ...
```

**Critical invariant:** `amplifier_agent_lib` never reads from stdin or writes to stdout. ALL I/O goes through `ProtocolPoints` injected at `Engine.boot()`. This is the single rule that makes the two-mode CLI possible with one engine implementation.

---

## 5. Naming

Per Brian's directive at 59:26 ("those should not be named amplifier agent either. The individual language wrappers. They should be named…") — "Amplifier Agent" is reserved for the CLI-level user-facing package; wrappers get distinct names.

| Component | Name | Confidence |
|---|---|---|
| Repository | `microsoft/amplifier-agent` | LOCKED |
| CLI binary | `amplifier-agent` | LOCKED (Brian 59:26) |
| Engine library (Python, internal) | `amplifier_agent_lib` | LOCKED (Manoj — "I don't like engine, call it amplifier_agent_lib") |
| Thin CLI binary code | `amplifier_agent_cli` | LOCKED |
| TS wrapper package | `amplifier-agent-client-ts` | LOCKED |
| Python wrapper package | `amplifier-agent-client-py` | LOCKED |
| NanoClaw adapter package | `amplifier-nanoclaw-adapter` | LOCKED (V1 carryforward) |
| Paperclip adapter package | `amplifier-paperclip-adapter` | LOCKED (V1 carryforward) |
| Session identifier (wire field) | `sessionId` | LOCKED (Manoj — change from `sessionName`) |

---

## 6. Brian's 9-section deliverable

---

### §1 Host adapter list — **LOCKED** (NanoClaw + Paperclip POC)

**Problem framing.** Brian asked us to enumerate the host adapters we plan to build. We are narrowing Phase 2 POC to **NanoClaw + Paperclip only**. The generic-CELA-tool and OpenClaw targets are deferred — they consume the CLI binary directly (via `amplifier-agent run "prompt"`) without an L2 adapter, so they are out of scope for §1 even though they are in scope for the broader Phase 2 deliverable.

**Why only these two:** Brian at 49:55 prioritized "hosts we'll use ourselves"; NanoClaw is "won for sure" (50:15); Paperclip is the active V1 host with a different lifecycle model. POC-ing both validates that one wire contract serves materially different host shapes.

**Assumptions:**
- Both adapters are TypeScript packages (both hosts are Node-based)
- Both consume `amplifier-agent-client-ts` (L3) — no direct CLI invocation
- V1's working contracts preserved (per Appendix D)
- V1's workarounds dropped (per Appendix D)
- Each adapter ships in the monorepo at `adapters/nanoclaw/` and `adapters/paperclip/`

**Adapter shapes, side by side:**

| Aspect | NanoClaw adapter | Paperclip adapter |
|---|---|---|
| Host contract | `AgentProvider.query(input) → AgentQuery` (events: AsyncIterable<ProviderEvent>) | `ServerAdapterModule.execute(ctx) → Promise<AdapterExecutionResult>` |
| Idle heartbeat | `idleHeartbeat: true` (mandatory) | `idleHeartbeat: false` |
| L3 lifecycle policy | `'burst'` — wrapper holds subprocess across multiple `push()` calls within one `query()` | `'one-shot'` — wrapper spawns per `execute()`, drains, exits |
| Approval routing | In-container `ask_user_question` MCP tool (V1 pattern, preserved) | Async via `wakeReason: 'approval_callback'` + `approvalStatus` on next wake (V1 pattern, preserved) |
| Display routing | Pass `ProviderEvent` straight through; one-to-one mapping of canonical taxonomy → ProviderEvent | `ctx.onLog()` writes JSONL lines; UI parses post-hoc (V1 pattern, preserved) |
| Container required? | YES (NanoClaw isolation premise) — see §7 | OPTIONAL (default no) — see §7 |
| Spawn override surface | None (library-internal per §8) | None |
| Concurrency | `AgentQuery.push()` may be called multiple times per query; wrapper serializes per-session | One-call-per-execute; no concurrency concern |
| V1 carryforward | NC-L1..L13 working contracts | PC-L1..L9 working contracts |
| V1 drop | NC-L14/L15/L16 fixes elevated into wire/wrapper contracts; `cachedClient` replaced by `'burst'` lifecycle | PC-L10/L11/L12/L17 install / spawn workarounds replaced by `uv tool install` + library-internal spawn |

**Components and responsibilities (NanoClaw):**

| Component | Responsibility |
|---|---|
| `host-side/env.ts` | Compose container env contract (NC-L1..L5): PATH inheritance, SSL_CERT_FILE, AaA workdir mount metadata |
| `host-side/wizard.ts` | NanoClaw setup wizard hooks; defer turnkey installer wiring to §2 (post-L4) |
| `container-side/provider.ts` | Implements `AgentProvider.query()`; under the hood calls `spawnAgent({lifecycle: 'burst', ...})`; translates `ProviderEvent` ↔ canonical taxonomy; routes `ask_user_question` MCP tool as approval |
| `container-side/heartbeat.ts` | Ensures `idleHeartbeat: true` semantics (NC-L7 carryforward) |
| `container-side/continuation.ts` | Maps `QueryInput.continuation` ↔ `sessionId` |

**Components and responsibilities (Paperclip):**

| Component | Responsibility |
|---|---|
| `adapter.ts` | Implements `ServerAdapterModule.execute(ctx)`; calls `spawnAgent({lifecycle: 'one-shot', ...})` per execute |
| `session-params.ts` | Reads/writes opaque `sessionParams` blob; maps to `sessionId` + `resume: true` |
| `wake-reason.ts` | Handles `wakeReason: 'approval_callback'`; on approval wake, attaches `approvalStatus` to next submit |
| `log-emitter.ts` | Maps canonical display taxonomy → `ctx.onLog()` JSONL lines (PC-L8 carryforward) |
| `budget.ts` | Adapter-policy budget guard via `ctx.abortSignal` (PC-L9 carryforward) |

**Risks (POC scope):**
- The NanoClaw `'burst'` lifecycle is only justified if cold-start is high enough that per-`push()` spawn-then-die is unacceptably slow. The Phase 2.0d cold-start measurement gate (§9) determines whether NC ships on `'burst'` or `'one-shot'`.
- Paperclip's `wakeReason` approval pattern depends on Paperclip's wake mechanism being reliable; PC-L2 carryforward applies.

**Failure modes** are detailed in §6 (approval/display) and §9 (execution plan).

---

### §2 Install paths per host — **DEFERRED to post-Layer-4 implementation**

**What we know now:**

| Aspect | Locked |
|---|---|
| Pattern per host | Two install paths each — **turnkey** (one-line installer) + **add-on** (skill drop-in / "register as provider") (Brian 48:25, 49:06) |
| NanoClaw | Turnkey installer ships in parallel; don't block on NanoClaw PR merge (Brian 50:33) |
| OpenClaw add-on | Skill drop-in via "dial-a-friend" / "register as provider" (Brian 49:55, 15:44) |
| Reference design | Brian's own one-prompt-installer in his DTU is the model (Sync §7) |
| Base mechanism | `uv tool install amplifier-agent` for the CLI binary itself |

**Why deferred.** Concrete installer scripts, host-specific config wiring, version-pinning strategy, and the post-install hook (for prepared-bundle cache priming) all depend on:
1. The L4 packaging shape produced during implementation (single wheel? wheel + sdist? what's vendored vs downloaded at install?).
2. The cold-start measurement gate (§9) — if cold-start is fast enough, the post-install hook can be optional rather than mandatory.
3. NanoClaw-specific install timing relative to the turnkey installer PR.

**Evidence Layer 4 implementation will provide:**
- Final packaging artifact shape
- First-invocation cache priming cost
- Whether the cache must be primed at install-time for containerized installs (likely YES; see §7)

**What follows post-L4:** A dedicated §2 design pass producing (a) install script per host, (b) version-pinning policy, (c) upgrade path, (d) uninstall path.

---

### §3 CLI (`amplifier-agent`) — **LOCKED**

**Problem framing.** Brian's deliverable #3: *"the interface, CLI options, any config files that a user would need to know about."* The CLI must work for two distinct audiences simultaneously: (a) shell-out callers (OpenClaw skills, Codex CLI, Claude Code, Gemini CLI, Copilot, bash, CI) using single-turn argv; (b) wrapper-driven callers (TS wrapper, Python wrapper) using stdio JSON-RPC. The CLI is the user-visible product.

**Locked decisions:**

| Surface | Spec |
|---|---|
| Binary name | `amplifier-agent` |
| Default action (no subcommand) | `amplifier-agent run "prompt"` is the default invocation |
| Subcommands | `run`, `doctor`, `config show`, `cache clear` |
| Top-level flags | `--version`, `--help` |

**`run` flags:**

| Flag | Purpose |
|---|---|
| `--session-id <id>` | Logical session identifier; persisted across invocations for stateful continuity |
| `--resume` | Logical replay: load transcript from disk for `--session-id` before submitting |
| `--fresh` | Force-discard any existing transcript for `--session-id` |
| `--stdio` | Switch from Mode A (single-turn argv) to Mode B (stdio JSON-RPC loop) |
| `--idle-timeout <ms>` | Mode B only: exit after N ms of inactivity |
| `--provider <name>` | Override auto-detect (`anthropic` / `openai` / `azure-openai` / `ollama`) |
| `--bundle <name>` | HIDDEN. Dev/testing only. Defaults to vendored built-in bundle. |
| `--config <path>` | Override XDG config path |
| `--cwd <path>` | Working directory for tool execution |
| `-v` / `--verbose` | Verbose stderr diagnostics |
| `--debug` | Debug-level stderr diagnostics |
| `-y` / `--yes` | Mode A only: auto-approve all approval requests |
| `-n` / `--no` | Mode A only: auto-deny all approval requests |
| `--quiet` | Mode A only: suppress canonical display output to stderr |

**Config resolution precedence** (standard CLI convention, documented via `amplifier-agent config show`):

1. CLI flags
2. Environment variables (`AMPLIFIER_AGENT_*`)
3. XDG config file at `$XDG_CONFIG_HOME/amplifier-agent/config.toml` (default `~/.config/amplifier-agent/config.toml`)
4. Compiled defaults

**Provider auto-detection** (Brian D5): precedence is `ANTHROPIC_API_KEY` > `OPENAI_API_KEY` > `AZURE_OPENAI_KEY` > `OLLAMA_HOST`. If none set, fail with structured error code `provider_not_configured` and an actionable message pointing to documentation.

**stdin discipline:**
- Mode A: if stdin is a TTY in default mode (no `--stdio`), proceed normally
- Mode A: if stdin is non-TTY in default mode, **do not** read prompt from stdin; fail with `prompt_required` and message: *"Pass prompt as argument: `amplifier-agent run \"...\"`. For stdio JSON-RPC, use `--stdio`."*
- Mode B: `--stdio` is the explicit opt-in to JSON-RPC framing

**stdout/stderr discipline** (Linux philosophy + CLI-tool best practices):
- Mode A: stdout receives ONLY the final JSON result (parseable). stderr is free for diagnostics, prompts, and the canonical display output (prefixed `[type]`).
- Mode B: stdout receives ONLY JSON-RPC frames. stderr is free for diagnostics. (Wrappers tolerate non-JSON stderr; MCP-style protection against accidental stdout pollution.)

**Exit codes** (semver-protected; structured error codes carried inside JSON-RPC errors):

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | General error (turn failed, model rejected, etc.) |
| 2 | Usage error (bad flags, missing prompt, etc.) |

**Admin verbs (locked):**

| Verb | Purpose |
|---|---|
| `amplifier-agent doctor` | Self-diagnostic: provider keys, cache state, vendored bundle integrity, Python env |
| `amplifier-agent config show` | Print resolved config + sources (which flag/env/file each value came from) |
| `amplifier-agent cache clear` | Clear XDG cache (forces re-prepare of bundle on next run) |
| `amplifier-agent --version` | Print package version |
| `amplifier-agent --help` | Print help |

**Mode A approval default** (decision-checkpointed):

Default is `prompt-when-tty, deny-otherwise` — apt/git-commit/npm-install style. Rationale:

- When a human runs `amplifier-agent run "..."` interactively, they ARE the user; surfacing to them via TTY readline is the correct primitive
- When called from a non-TTY context (CI, bash scripts, OpenClaw skill, daemon), default to deny — caller must explicitly opt in via `-y` or wire an adapter
- `-y` and `-n` are explicit overrides
- The previous proposal of unconditional `deny` was rejected because it silently fails interactive users (model retries → gives up → user sees nothing)

This matches Unix CLI convention; no surprise.

**Config files a user needs to know about:**

| File | Purpose |
|---|---|
| `$XDG_CONFIG_HOME/amplifier-agent/config.toml` (optional) | Provider override, default flags |
| `$XDG_CACHE_HOME/amplifier-agent/prepared/<version>/` (managed) | Prepared bundle cache; user does not edit |
| `$XDG_STATE_HOME/amplifier-agent/sessions/<sessionId>/` | Transcript via `amplifier-module-context-persistent` (when present); user does not edit |

No user-edited `settings.yaml` (Brian 47:48 directive — D5).

---

### §4 Language wrappers (TS + Python) — **LOCKED** (design)

**Problem framing.** Brian's deliverable #4: *"Layer 3 (language wrappers) — which packages, and the host-adapter-author-facing interface. Back-end less important."* These packages are the host-adapter-author surface — they MUST be ergonomic for adapter authors and identical in shape across TS and Python.

**Locked packages:**

| Package | Registry | Purpose |
|---|---|---|
| `amplifier-agent-client-ts` | npm | TS wrapper; consumed by NC and PC adapters |
| `amplifier-agent-client-py` | PyPI | Python wrapper; consumed by future Python hosts (Brian's "primary work" use-case) |

**Public API (single shape across languages):**

```ts
// TypeScript signature; Python mirrors with snake_case
spawnAgent({
  lifecycle: 'one-shot' | 'burst',
  sessionId: string,
  resume?: boolean,
  cwd?: string,
  env?: { allowlist: string[], extra?: Record<string, string> },
  approval?: {
    onRequest: (req: ApprovalRequest) => Promise<ApprovalResponse>,
    timeoutMs: number,  // mandatory
  },
  display?: {
    onEvent: (event: DisplayEvent) => void,
  },
  idleTimeoutMs?: number,  // burst only
}) => SessionHandle

interface SessionHandle {
  submit(prompt: string): AsyncIterable<DisplayEvent>
  close(): Promise<void>
}
```

**Lifecycle policy semantics:**

- `lifecycle: 'one-shot'` — wrapper spawns subprocess per `submit()`, runs single turn, drains, exits. Mirrors Paperclip's pattern and OpenClaw's default.
- `lifecycle: 'burst'` — wrapper holds subprocess across submits within one `SessionHandle` lifetime. Spawns on first `submit()`, reuses for subsequent submits, kills on `close()` or `idleTimeoutMs`. Mirrors NanoClaw's Codex provider pattern (validated; see §2.4).

**Wrapper-level invariants** (designed in, not retrofitted):

| Invariant | Enforcement |
|---|---|
| One in-flight `submit()` per `SessionHandle` at a time | Queue or reject (caller-configurable); never overwrite an in-flight handler. Designs out V1's NC-L16 `this.active` overwrite race. |
| L14 `result/final` synthesis | If `turn/submit` returns a non-null reply scalar and no `result/final` notification arrived, synthesize one before closing the iterable. Cross-language contract — see Appendix B. |
| env allowlist | NEVER pass `{...process.env}` blindly. Use explicit allowlist (defaults: PATH, HOME, USER, LANG, LC_*, TZ, plus per-provider keys). Designs out PC-L13 PATH-loss-on-sparse-env class. |
| Subprocess spawn hardening | `shell: false`, `windowsHide: true` (TS), curated env, "skip non-JSON lines" tolerance on stdout reader. (MCP-fixed pattern.) |
| Capability negotiation | `agent/initialize` exchange at handle creation; wrapper advertises what it implements; engine respects only what wrapper advertised. |
| Per-request-id routing | JSON-RPC client routes responses by `id` to per-call handlers; no shared mutable "active" pointer. |

**External protocol points (three, not four):**

1. **Approval** — `approval.onRequest` callback. Wrapper bridges JSON-RPC server-initiated `approval/request` ↔ adapter-supplied handler. Returns `{action: 'accept' | 'decline' | 'cancel', payload?: any}`. Adapter is responsible for accept/decline/cancel/timeout mediation.
2. **Display** — `display.onEvent` callback. Receives canonical display taxonomy (Appendix A) one-way (no response). Streaming is folded into Display — the iterable returned from `submit()` produces the same events.
3. (Spawn — **library-internal**, no public API; see §8.)

**Mode of consumption:**
- An L2 adapter typically calls `spawnAgent` once per host-session (Paperclip per `execute()`; NanoClaw per `query()`); the resulting `SessionHandle` mediates the conversation.
- Lifecycle policy is the adapter's choice based on host shape.

**Why both wrappers are siblings (not stacked on the CLI):**

Brian's transcript at 58:14 and 58:45 makes this explicit: *"if we put these all these thin layers behind in front of it, including one of them is a CELA, you would call that instead of amplifier agent direct. … from a naming convention. If it's that layer above that becomes amplifier agent, then we've got a Python version, we've got a TypeScript version, we've got a [CLI version]."* The CLI is "one of them," not their parent. Both wrappers, like the CLI binary, are thin layers wrapping `amplifier_agent_lib` — they spawn the engine via subprocess but they don't shell out through the CLI binary.

(However: in operation, the TS and Python wrappers DO spawn the `amplifier-agent` binary as a subprocess and speak JSON-RPC to it. This is a packaging implementation detail — the wrappers are siblings *in mental model and developer interface*; they happen to use the binary as the subprocess image because shipping a separate Python embed wouldn't change the architecture.)

**Implementation deferred to post-Layer-4.** The wire spec is the day-one artifact; the wrapper implementations are written against it once L4 is built and the wire is verified.

---

### §5 Non-server design + naming — **LOCKED**

**Problem framing.** Brian D1: *"What it would take to not have to run it as a server."* Brian 26:04: *"you don't have to have a service running, or you know, dozens of services sitting listening."* Brian 25:21: *"It literally just launched, ran, shut down."* This section locks the structural shape that delivers D1.

**Definitional reality.** A systems-design-critic adversarial review walked eight standard axes of "server":

| Axis | Classical "server" | AaA stdio coprocess (`run --stdio`) |
|---|---|---|
| Lifetime | Long-lived (days/months) | Conversation burst (~minutes; idle-killed) |
| Wait pattern | Listens on socket/port | Reads stdin from one parent |
| Client cardinality | 1:N | 1:1 |
| Process relationship | Daemon, orphaned to init | Child of caller, dies with parent |
| Transport | TCP/UDP/Unix socket | stdin/stdout pipes |
| Discovery | DNS/port/registry | Inherited fd from spawn |
| Supervisor | systemd/launchd/k8s | None |
| State across clients | Yes (multi-tenant) | None (1:1, dies with burst) |

**Under all eight standard distinctions, this is not a server.** Honest taxonomy: **stdio coprocess.** Same category as LSP servers, MCP servers as commonly deployed, `dap-mode`, `git fast-import`, `codex app-server --listen stdio://`. The systems-design-critic verified this.

**Honest framing for Brian** (carry verbatim into next sync):

> Built-in bundle gives us near-instant cold-start AFTER FIRST INVOCATION as you predicted (first run pays the documented 5–30 s install cliff); we'll measure and confirm. The architectural pattern we're proposing is two modes: per-call (`run "prompt"` — exactly what you described) and per-burst (`run --stdio` — the same wire your team's existing host already uses for the Codex provider in NanoClaw). The per-burst extension goes beyond "per call" literally, but stays inside your "MCP pattern" framing and your 27:08 escape clause. If cold-start lands under 200ms, we drop per-burst and ship only per-call. Calling either of these "a server" would be sloppy taxonomy: no daemon, no listener, no supervisor, no port, single-client, parent-owned, dies with the caller. The honest term is "stdio coprocess."

**The two invocation modes:**

| Mode | Invocation | Process lifetime | Audience |
|---|---|---|---|
| **A** | `amplifier-agent run "prompt" --session-id X [--resume]` | One turn; exits after `result/final` | Shell-out callers: OpenClaw skills, bash, Codex CLI, Claude Code, Gemini CLI, Copilot, CI scripts. Brian 57:18: *"anything that can shell out."* |
| **B** | `amplifier-agent run --stdio` | Burst (~minutes); exits on EOF or `agent/shutdown` or `--idle-timeout` | Wrapper-driven hosts: NanoClaw burst, future Python hosts |

**Crucially:** **both modes use the same `Engine` class, same protocol points, same dispatch logic.** The CLI binary is the layer that absorbs the duality — `amplifier_agent_cli` has two thin entrypoints (`modes/single_turn.py` ~80 LOC, `modes/stdio_loop.py` ~150 LOC) each constructing the Engine with mode-appropriate `ProtocolPoints` defaults.

**Why the library is mode-agnostic** (the critical invariant from §4 above):
- `amplifier_agent_lib.engine.Engine` accepts `ProtocolPoints` at boot
- All I/O flows through ProtocolPoints
- `defaults_cli.py` provides Mode A defaults (tty-prompt approval, stderr display)
- `defaults_stdio.py` provides Mode B defaults (JSON-RPC bridges)
- Engine cannot tell which mode it's in. This is exactly what makes "two modes, one engine" tractable.

**Lifecycle stewardship at L3.** The wrapper (`amplifier-agent-client-{ts,py}`) chooses `'one-shot'` (per-`submit()` subprocess) or `'burst'` (held subprocess across submits) based on host shape. The engine doesn't know which is being used; the difference is when the caller closes stdin.

**Built-in bundle.** Vendored with the package. First invocation prepares and caches to `$XDG_CACHE_HOME/amplifier-agent/prepared/<version>/`. Subsequent invocations check the cache and load. This is the engineering work behind Brian's "near-instant" claim — we honor it on warm cache (sub-second). First invocation pays the 5–30 s manifest-resolve + module-install cost; the post-install hook (`amplifier-agent-post-install`) is an opt-in amortizer for users who want fast first-run.

**Lifecycle policies are NOT modes.** Mode = invocation shape (argv vs stdio). Lifecycle = wrapper subprocess policy (one-shot vs burst). They are independent axes; a Mode A invocation is always `'one-shot'` by definition (subprocess dies after one turn). Mode B can be either, depending on wrapper config.

**Sub-agent / delegate spawning.** Per OpenClaw's validated precedent (`CLISpawnManager`): sub-agents are **in-process** `AmplifierSession` instances created within the parent invocation, NOT new subprocesses. This is §8's library-internal spawn.

**Cold-start gate** (Phase 2.0d in §9). If steady-state cold-start is:
- **<200ms** → drop Mode B; ship only Mode A. The cost amortization rationale evaporates.
- **200–500ms** → contested; ship Mode B but prioritize Mode A for hosts that can use it
- **>500ms** → Mode B is well-justified for NanoClaw `'burst'` lifecycle

---

### §6 Approval and display bubble-up — **LOCKED**

**Problem framing.** Brian's deliverable #6: *"how are you going to bubble up the approval system, the display system?"* Both must flow L4 (engine) → L3 (wrapper) → L2 (adapter) → L1 (host) with defaults that work when no adapter is wired (Mode A). Spawn is library-internal (§8); three protocol points remain externally exposed.

**Brian on approval — two patterns + creative reuse:**

| Source | Pattern | When |
|---|---|---|
| Brian 21:14 | **Message-back** (recipes-style) | When AaA is "model replacement at the core" — pause execution, emit a message asking for approval, resume when caller responds |
| Manoj 22:07 (Brian acknowledged) | **Callback hook** | When host provides infrastructure for callback registration (Paperclip prototype) |
| Brian 23:19 | **Creative reuse** | When host has no callback primitive but has an alternative channel (e.g., Paperclip email) |
| Brian 23:36 | **(Key framing)** | *"we're creatively thinking of like how to wire that up per host"* — pattern is per-host, the wire is one shape |

**Locked: one wire, many adapter patterns.**

- Wire shape: MCP elicitation pattern — server-initiated `approval/request` with structured payload, mandatory `timeoutMs`; client responds with `{action: 'accept' | 'decline' | 'cancel'}` + optional payload
- Relaxed schema vs MCP: AaA's trust boundary is symmetric (host ↔ engine, both trusted) — content schema is richer than MCP's flat-primitives-only restriction
- Mediation patterns at L2 are unbounded: callback, message-back, email, host-UI, none — adding a new pattern is an adapter-only change

**Brian on display — common base + adapter-specific, keep adapters thin:**

Brian's framing (paraphrased from Sync §3) is that there's a canonical set of display events the engine emits, and adapter translation kicks in only when host shape diverges from the canonical taxonomy.

**Canonical display taxonomy (9 notification types):**

| Notification | Direction | Payload sketch |
|---|---|---|
| `result/delta` | engine → caller | Streaming text fragment for current turn |
| `result/final` | engine → caller | Final result for current turn; ends iterable |
| `tool/started` | engine → caller | Tool invocation begun (name, args) |
| `tool/completed` | engine → caller | Tool invocation finished (name, result, duration) |
| `progress` | engine → caller | Long-running operation progress signal (no payload schema; opaque message) |
| `thinking/delta` | engine → caller | Streaming "thinking" / reasoning text fragment |
| `thinking/final` | engine → caller | Final reasoning text for current turn |
| `usage` | engine → caller | Token usage / cost signal |
| `error` | engine → caller | Non-fatal error during turn (fatal errors come back as JSON-RPC error responses) |

**The engine emits ONLY these.** Adapter translates only where the host's preferred display shape diverges (Brian's "common base + adapter-specific, keep adapters thin"). Capability negotiation at `agent/initialize` lets the wrapper opt out of types it doesn't want (e.g., a host that doesn't care about thinking deltas can suppress).

**Streaming, folded into display.** No separate `StreamingHook` protocol point. The iterable returned by `submit()` produces the same events that flow to `display.onEvent`. This unification is intentional — a single display channel covers both UI rendering and programmatic stream consumption.

**Mode A defaults (no adapter wired):**

| Protocol point | Mode A default |
|---|---|
| Approval | `prompt-when-tty, deny-otherwise` (apt/git-commit/npm-install style); `-y` and `-n` overrides |
| Display | Stderr line per event, prefixed `[result/delta]`, `[tool/started]`, etc.; `--quiet` suppresses |
| Display verbosity | Default suppresses `thinking/*` and `progress`; `--verbose` enables; `--debug` adds JSON dumps |

**Capability negotiation at `agent/initialize`:**

```jsonc
// wrapper → engine
{
  "method": "agent/initialize",
  "params": {
    "capabilities": {
      "approval": { "actions": ["accept", "decline", "cancel"] },
      "display": { "events": ["result/delta", "result/final", "tool/started", "tool/completed", "usage", "error"] }
    },
    "clientInfo": { "name": "amplifier-agent-client-ts", "version": "0.1.0" }
  }
}
```

Engine respects only what was advertised. If wrapper omits `thinking/*`, engine suppresses those notifications.

**Approval timeout discipline** (designs out adversarial review's "infinite-hang" risk):

- `timeoutMs` is **mandatory** on approval. No default-infinite.
- On timeout, engine emits `approval/timeout` notification and proceeds as if `cancel` was received.
- Wrapper enforces; engine also enforces as defense-in-depth.

**Smoke-test:** the canonical taxonomy is verified during NC + PC adapter wiring; if a real-world event type doesn't map, we add to the taxonomy (versioned per Appendix A) rather than letting adapters invent custom event types.

---

### §7 Containers — **DEFERRED to post-Layer-4 implementation**

**What we know now:**

| Host | Container requirement |
|---|---|
| NanoClaw | **REQUIRED** — NanoClaw's isolation premise is container-per-conversation; container env contract NC-L1..L5 preserved |
| Paperclip | **OPTIONAL** — default no container; some host deployments may container-ize |
| Default (CLI direct, OpenClaw skill, bash) | **NO** container |
| Containerized installs | Need cache prep at install-time (XDG home is ephemeral without explicit volumes) |

**Why deferred.** Container-specific work depends on L4 packaging and install-time hook behavior:
- Dockerfile content depends on the final wheel layout
- CA mirror specifics for SSL_CERT_FILE depend on which provider keys are configured
- Post-install hook implementation (cache priming) depends on whether install-time bundle prepare is fast enough
- The XDG cache strategy in containers depends on whether the AaA workdir is bind-mounted

**What we'll need post-L4:**
- Dockerfile for NanoClaw container with vendored bundle + primed cache
- SSL_CERT_FILE / CA mirror setup
- Volume strategy for `$XDG_CACHE_HOME` and `$XDG_STATE_HOME` in containers
- Container env contract test suite (NC-L1..L5 carryforward)

---

### §8 Spawn — **LOCKED** (library-internal, no adapter override)

**Problem framing.** Brian D3: *"bake in, no host customization."* Brian's Sync §1 D3 is the directional reversal of V1's Decision #6 (which made spawn "adapter-owned policy"). This section locks v2's inversion of that V1 decision.

**Locked:**

- Spawn capability lives in `amplifier_agent_lib.spawn` (library-internal)
- No adapter override. No public API surface for spawn.
- Used by `delegate`, `recipe`, sub-agent creation tools — all in-process via a `CLISpawnManager`-equivalent (per OpenClaw's validated precedent at `spawn.py:340–381`)
- Sub-agents are **in-process `AmplifierSession` instances within one engine invocation**, not new subprocesses
- The Application Integration Guide §1 names four protocol boundary points (Approval, Display, Streaming, Spawn). **V2 reduces to three external protocol points** — Spawn is removed from the external surface per Brian's D3.

**Why this matters for v2:**

| V1 (now reversed) | V2 |
|---|---|
| `spawn_fn` parameter on `AmplifierAgentConfig` — adapters could supply their own spawn function | No such parameter exists; spawn is the engine's responsibility |
| `tool-delegate` trap (PC-L17) — adapter-supplied spawn could be misconfigured | Designed out; spawn is opinionated and tested in one place |
| Spawner library was "opt-in convenience" (V1 Decision #6) | Spawner is the only spawn path; opinionated; the bundle's manifest text is sealed per release |

**Sub-agent ID convention.** Sub-agents inherit `parent_id` and get fresh `session_id`s within the parent engine's process scope. Transcript persistence (when enabled via `amplifier-module-context-persistent`) follows the parent's pattern.

**This is the "App CLI session spawner" Brian named at D3.**

---

### §9 Execution plan — **Layer-4-first sequencing**

**Problem framing.** Brian's deliverable #9: *"what will be built in what order."* The execution plan is **Layer-4-first**: build the library + CLI + built-in bundle, measure cold-start and verify the wire, then sequence wrappers and adapters with empirical evidence in hand.

**Rationale for Layer-4-first** (from the structured-design comparison in the parent conversation):
- Layer 4 is fully specified; pending sections (§1 adapters, §2 install, §7 containers, §9 timeline beyond Phase 2.0) all live above Layer 4 in the stack — they consume L4 but don't constrain it
- Layer 4 implementation validates the load-bearing assumptions: cold-start time, bundle cache behavior, L14 cross-language contract
- Cold-start measurement is a decision gate for whether Mode B ships at all in Phase 2.0
- Brian's posture (*"I want to see what you come up with"*) favors the iterative empirical path

**Phase 2.0 — Layer 4 (engine + CLI):**

| Sub-phase | Deliverable | Exit criterion |
|---|---|---|
| **2.0a** | `amplifier_agent_lib` library — mode-agnostic engine, protocol points (Approval, Display, Spawn-internal), persistence, vendored built-in bundle | Importable; passes unit tests for engine boot, submit_turn, dispatch, shutdown; vendored bundle loads |
| **2.0b** | `amplifier_agent_cli` Mode A — `run "prompt"` single-turn + admin verbs (doctor, config show, cache clear) | End-to-end: `amplifier-agent run "say hi"` returns final JSON on stdout; admin verbs work |
| **2.0c** | `amplifier_agent_cli` Mode B — `run --stdio` JSON-RPC loop | End-to-end: send `agent/initialize` + `turn/submit` via stdin, receive notifications + `result/final`, EOF triggers clean exit |
| **2.0d** | Built-in bundle vendoring + XDG cache + post-install hook + cold-start measurement | Empirical measurement: first-invocation latency, cached-invocation latency, p50/p95/p99 over N=100 runs on representative hardware |
| **2.0e (gate)** | **Cold-start decision** | If steady-state <200ms: drop Mode B from Phase 2.0 ship; if >500ms: Mode B is justified for NanoClaw; if 200–500ms: ship both with documented tradeoff |

**Phase 2.1+ (post-L4, sequencing pending Phase 2.0 evidence):**

| Phase | Deliverable (provisional sequence) |
|---|---|
| 2.1 | Wire protocol spec hardened + conformance suite (TS + Python) |
| 2.2 | `amplifier-agent-client-ts` wrapper — full public API; passes conformance suite |
| 2.3 | `amplifier-agent-client-py` wrapper — full public API; passes conformance suite |
| 2.4 | `amplifier-nanoclaw-adapter` v2 — built on TS wrapper, lifecycle `'burst'` or `'one-shot'` per 2.0e gate |
| 2.5 | `amplifier-paperclip-adapter` v2 — built on TS wrapper, lifecycle `'one-shot'` |
| 2.6 | §2 install paths: turnkey installers per host + add-on skill drops |
| 2.7 | §7 containers: NanoClaw container with primed cache + env contract suite |
| 2.8 | OpenClaw add-on (skill drop) + generic CELA tool exposure |

**Full execution plan is refined post-L4** with measurement evidence in hand.

---

## 7. Success metrics

**Phase 2.0 (Layer 4):**

| Metric | Target |
|---|---|
| Cold-start (cached, default bundle, single-turn) | <500ms p95; <200ms aspirational |
| Cold-start (first invocation, prepare from vendored source) | <30s p95 |
| Mode A end-to-end ("run hi" → final JSON) | Works on macOS + Linux; CI green |
| Mode B end-to-end (initialize + submit + receive notifications + final) | Works on macOS + Linux; CI green |
| stdout discipline | 100% — only JSON on stdout in either mode; diagnostics flow to stderr |
| Admin verbs | doctor, config show, cache clear all functional with helpful output |

**Phase 2.1+ (wrappers + adapters):**

| Metric | Target |
|---|---|
| Wire conformance | TS and Python conformance suites both pass against same engine |
| L14 contract | Synthesized `result/final` in 100% of cases where engine returns reply scalar but omits notification |
| Concurrency contract | No `this.active`-style races; per-request-id routing verified by test |
| NC adapter | NanoClaw integration green; container env contract NC-L1..L5 preserved |
| PC adapter | Paperclip integration green; `wakeReason` approval flow preserved |

**Strategic (Brian-facing):**

| Signal | Target |
|---|---|
| Naming clarity | Brian agrees with locked names (amplifier-agent CLI, amplifier-agent-client-{ts,py} wrappers) without bikeshed |
| "Not a server" framing | Brian agrees stdio coprocess characterization matches his intent |
| Per-call vs per-burst extension | Brian acknowledges the extension explicitly; not silently substituted |
| One-shot stateful via logical replay | Brian agrees OpenClaw precedent is the right pattern (not new kernel work) |

---

## 8. Risks and monitoring signals

| Risk | Severity | Signal | Mitigation |
|---|---|---|---|
| Cold-start lands >500ms even with cached bundle | HIGH | Phase 2.0d measurement | Mode B retained; revisit cache strategy; consider precompiling bytecode |
| Cold-start lands <200ms | MEDIUM (opportunity) | Phase 2.0d measurement | Drop Mode B from Phase 2.0; simpler ship |
| Brian rejects "stdio coprocess" framing | LOW (anticipated) | Next sync | Honest framing already prepared (§5); fallback is to ship Mode A only |
| Per-language wire divergence (TS vs Python wrappers drift) | MEDIUM | Conformance suite failures | Wire spec is authoritative; conformance gates merge of either wrapper |
| `amplifier-module-context-persistent` module schema changes | MEDIUM | Foundation Integration Guide updates | Pin foundation version; track upstream schema; logical replay isolates us from internal session state |
| Containerized install cache priming fails silently | MEDIUM | Container E2E tests | Post-install hook with explicit verification; doctor verb checks cache state |
| V1 NC-L14 / NC-L16 patterns recur in v2 by accident | LOW (designed out) | Conformance suite + concurrency tests | Per-request-id routing + L14 cross-language contract make recurrence a test failure |
| Approval infinite-hang | LOW (designed out) | E2E tests | `timeoutMs` mandatory; engine and wrapper both enforce; `approval/timeout` notification |
| OpenClaw integration drift (their pattern evolves) | LOW | Periodic sibling-design check | OpenClaw is sibling, not dependency; we mirror pattern, not code |
| Brian discovers Mode B looks "too much like a server" | MEDIUM | Next sync | Documented eight-axis analysis ready; honest "per-burst extension" framing prepared |

---

## 9. What's deferred and why

### §2 Install paths — DEFERRED
- **Why:** Concrete installers depend on L4 packaging shape (single wheel? sdist? what's vendored?) and Phase 2.0d cold-start results (post-install hook mandatory or optional?)
- **Evidence L4 provides:** Final wheel layout; install-time bundle-prepare cost; XDG cache priming behavior
- **Post-L4 work:** Per-host install script; version-pinning; upgrade path; uninstall

### §7 Containers — DEFERRED
- **Why:** Dockerfile content and post-install hooks depend on L4 packaging shape and cache prep behavior
- **Evidence L4 provides:** Whether install-time cache prep is fast enough; what `$XDG_CACHE_HOME` strategy works in containers
- **Post-L4 work:** Dockerfile + CA mirror + volume strategy + NC-L1..L5 env contract test suite

### §9 Full execution plan — Layer-4-first
- **Why:** Sequencing past 2.0e gate depends on cold-start measurement
- **Evidence L4 provides:** Whether Mode B ships in Phase 2.0 or defers to a later phase; whether NanoClaw adapter lifecycle is `'burst'` or `'one-shot'`
- **Post-L4 work:** Phase 2.1+ refined with empirical inputs

### §4 Wrapper implementation — DEFERRED (design locked, implementation post-L4)
- **Why:** The wire spec is the day-one artifact; wrappers implement against it once L4 is built
- **Evidence L4 provides:** Wire shape verified end-to-end; conformance suite can be authored against a working engine

---

## 10. Open items

None blocking the checkpoint. Items flagged for next-sync discussion:

1. **Honest framing acceptance.** Confirm with Brian that the "stdio coprocess, two modes (per-call + per-burst)" framing matches his intent — specifically the per-burst extension.
2. **Phase 2.0e gate threshold.** Currently 200ms / 500ms. Confirm thresholds with Brian or update with empirical reference points.
3. **Provider list in Mode A auto-detect.** Currently `anthropic > openai > azure-openai > ollama`. Verify this precedence matches deployment expectations.
4. **Sub-agent transcript persistence.** When `amplifier-module-context-persistent` is wired, do sub-agents (in-process, parent_id-linked) persist their own transcripts? OpenClaw pattern is yes-with-parent-link; verify this is what we want.
5. **Provider not configured** behavior in Mode B — fail at `agent/initialize` or at first `turn/submit`? Currently spec says `agent/initialize`; verify this is the right surfacing point.

---

## 11. Appendices

### Appendix A — Wire protocol (methods, notifications, errors, capabilities)

The wire is JSON-RPC 2.0 over newline-delimited NDJSON. UTF-8. No embedded newlines (encoded if present). stdout sacred (engine emits only frames); stderr free (diagnostics).

**Methods (client → engine):**

| Method | Params | Result | Notes |
|---|---|---|---|
| `agent/initialize` | `{capabilities, clientInfo, sessionId, resume?, providerOverride?, cwd?}` | `{capabilities, serverInfo, sessionState}` | Mandatory first call in Mode B; implicit in Mode A |
| `turn/submit` | `{prompt, attachments?}` | `{reply, finalEvent?}` | Reply scalar non-null + missing `result/final` triggers L14 synthesis |
| `agent/shutdown` | `{}` | `{}` | Mode B clean shutdown; engine exits after response |
| `cache/info` | `{}` | `{cachePath, preparedBundles}` | For doctor verb |

**Methods (engine → client, server-initiated):**

| Method | Params | Response | Notes |
|---|---|---|---|
| `approval/request` | `{kind, payload, timeoutMs}` | `{action: 'accept'|'decline'|'cancel', payload?}` | MCP elicitation shape; mandatory timeout |

**Notifications (engine → client, one-way):**

| Notification | Params | Notes |
|---|---|---|
| `result/delta` | `{text, turnId}` | Streaming reply fragment |
| `result/final` | `{text, turnId, usage?}` | Ends turn iterable |
| `tool/started` | `{name, args, toolCallId}` | |
| `tool/completed` | `{name, result, durationMs, toolCallId}` | |
| `progress` | `{message, percent?}` | Opaque progress |
| `thinking/delta` | `{text, turnId}` | Streaming reasoning |
| `thinking/final` | `{text, turnId}` | |
| `usage` | `{inputTokens, outputTokens, cost?}` | |
| `error` | `{code, message, turnId?, recoverable}` | Non-fatal turn-scoped |
| `approval/timeout` | `{kind, payload}` | Engine-side defense-in-depth |

**Error codes (JSON-RPC error.data.code):**

| Code | Meaning |
|---|---|
| `provider_not_configured` | No provider env var detected; no override |
| `provider_init_failed` | Provider client failed to initialize (bad credentials, network, etc.) |
| `bundle_load_failed` | Vendored bundle failed to prepare or load |
| `session_not_found` | `--resume` against unknown sessionId without `--fresh` |
| `prompt_required` | Mode A: stdin not TTY, no prompt argument |
| `approval_denied` | Approval declined or cancelled or timed out |
| `tool_execution_failed` | Tool raised; usually delivered as `error` notification, but fatal cases use this |
| `wire_protocol_violation` | Client sent malformed frame or wrong method |

**Capability shape (negotiated at `agent/initialize`):**

```jsonc
{
  "capabilities": {
    "approval": { "actions": ["accept", "decline", "cancel"] },
    "display": {
      "events": ["result/delta", "result/final", "tool/started",
                 "tool/completed", "progress", "thinking/delta",
                 "thinking/final", "usage", "error"]
    },
    "experimental": {}
  }
}
```

Either side may omit categories; engine respects only what was advertised by client.

**Versioning:** wire is semver. Method signatures, capability shape, notification taxonomy, and error code set are an API contract. Breaking changes → major version bump.

### Appendix B — L14 synthesis contract

The L14 contract codifies the bug fix currently in `host-client-ts/src/session.ts` as a cross-language named requirement.

**Statement:** For any `turn/submit` request that returns a non-null reply scalar but does NOT produce a `result/final` notification before the response arrives, the wrapper MUST synthesize a `result/final` notification before closing the iterable returned to the caller.

**Synthesis:**
- `text`: extracted from the reply scalar
- `turnId`: matched to the in-flight turn
- `usage`: omitted (signals synthesized)
- A wrapper-side debug marker indicates synthesis happened

**Conformance:** Both TS and Python conformance suites include a test that exercises the synthesis path. A wrapper that fails the synthesis test fails the conformance suite.

**Why elevated to wire contract:** The V1 bug existed because the obligation was implicit — engine could omit the notification, wrapper had to know when to compensate. v2 makes this explicit; either the engine emits `result/final` reliably (preferred long-term) or wrappers synthesize per this contract. Currently we ship the wrapper-synthesis side as the safety net; the engine is expected to emit reliably but the contract tolerates omission.

### Appendix C — Mode A vs Mode B comparison

| Dimension | Mode A (`run "prompt"`) | Mode B (`run --stdio`) |
|---|---|---|
| Invocation | argv | stdin/stdout JSON-RPC |
| Process lifetime | 1 turn; exits after `result/final` | Burst (~min); exits on EOF / shutdown / idle |
| Cold-start | Paid per call | Paid once per burst |
| Stateful continuity | Via `--session-id` + `--resume` (logical replay) | Via session within burst; logical replay if process restarts |
| Concurrency | N/A (single turn) | One in-flight `turn/submit` per session; wrapper serializes |
| Approval default | `prompt-when-tty, deny-otherwise` | Bridged to wrapper-supplied callback |
| Display default | Stderr `[type]` prefix; `--quiet` suppresses; `--verbose` adds thinking/progress | Bridged to wrapper-supplied callback |
| Audience | Shell-out callers (OpenClaw, bash, Codex, Claude Code, Gemini CLI, Copilot, CI) | Wrapper-driven hosts (NanoClaw burst, future Python hosts) |
| Engine code path | Same Engine class, same dispatch | Same Engine class, same dispatch |
| Differentiation locus | `ProtocolPoints` injected at boot (`defaults_cli.py` vs `defaults_stdio.py`) | Same |
| Gate condition | Always ships in Phase 2.0 | Conditional on Phase 2.0e cold-start measurement |

### Appendix D — V1 carryforwards and V1 drops

**Carryforwards (working contracts preserved):**

| Item | Source | Why preserved |
|---|---|---|
| NanoClaw host contract (`AgentProvider.query`, idleHeartbeat:true) | V1 | Working production contract; no host-side migration |
| Paperclip host contract (`ServerAdapterModule.execute`, wakeReason, sessionParams, ctx.onLog) | V1 | Working production contract |
| Adapter package names | V1-C7 | Avoids persisted `amplifier_local` type-identifier migration |
| NDJSON JSON-RPC 2.0 framing | V1 wire | Working wire shape; MCP-validated |
| Capability negotiation at initialize | V1 + MCP | Working pattern; extended in v2 |
| NC-L1..L5 container env contract | V1 NC | Working pattern, re-enters at §7 |
| NC-L6..L13 NanoClaw provider integration details | V1 NC | Working; preserved |
| PC-L1..L9 Paperclip lifecycle details | V1 PC | Working; preserved |
| `idleHeartbeat: true` (NC) / `false` (PC) | V1 | Per-host invariant; preserved |

**Drops (V1 workarounds replaced by v2 structure):**

| Item | Source | Replacement |
|---|---|---|
| `serve` verb | V1 L4 | `--stdio` flag on `run` (Mode B); no separate serve verb |
| `cachedClient` per-container pattern | V1 L2 NC | L3 `lifecycle: 'burst'` policy |
| Adapter-owned spawn (`spawn_fn` parameter) | V1 L4 + V1 Decision #6 | Library-internal spawn (§8) per Brian D3 |
| `prepared.create_session()` second factory path | V1 L4 | Single path: `Engine.boot()` per process |
| `mount_plan` truthy-vs-semantic bug (NC-L8) | V1 L4 | Sealed manifest text (D4 + Strategy 1); no host-facing mount plan |
| `this.active` overwrite race (NC-L16) | V1 L3 TS client | Per-request-id routing in wrapper |
| `tool-delegate` trap (PC-L17) | V1 L4 | Library-internal spawn (§8) |
| Manual wheel automation (PC-L11) | V1 install | `uv tool install amplifier-agent` |
| SHA verification workaround (PC-L12) | V1 install | Vendored bundle in wheel |
| Install-skill staleness (PC-L10) | V1 install | Vendored bundle; post-install hook for cache priming |
| PATH-loss-on-sparse-env (PC-L13) | V1 L3 TS client | env allowlist in wrappers (MCP-fixed pattern) |
| L14/L15 ad-hoc fix in `session.ts` | V1 L3 TS client | Elevated to cross-language wire contract (Appendix B) |

### Appendix E — Glossary

| Term | Meaning |
|---|---|
| **AaA** | Amplifier as Agent |
| **L1..L5** | Architectural layers (see §3.1) |
| **Mode A** | `amplifier-agent run "prompt"` — argv single-turn |
| **Mode B** | `amplifier-agent run --stdio` — JSON-RPC stdio loop |
| **Lifecycle policy** | Wrapper-level choice: `'one-shot'` vs `'burst'` |
| **stdio coprocess** | Honest taxonomy for v2's process model — not a server |
| **PreparedBundle** | Foundation kernel concept; expensive to construct, cheap to use |
| **AmplifierSession** | Foundation kernel session object; ephemeral per Integration Guide |
| **Logical replay** | OpenClaw's pattern: persist transcript; rebuild session from bundle each invocation |
| **L14 contract** | Synthesize `result/final` if engine omits but returns reply scalar (Appendix B) |
| **Canonical display taxonomy** | The fixed 9 notification types engine emits (§6) |
| **Three protocol points** | External integration surface: Approval, Display, (Spawn is library-internal) |
| **D1..D6** | Brian's six locked architectural decisions (§2.1) |
| **NC-L*, PC-L*** | V1 NanoClaw / Paperclip learnings (Appendix D) |

---

**End of checkpoint.** Next step per §9: begin Phase 2.0a — `amplifier_agent_lib` library implementation in `github.com/microsoft/amplifier-agent`.

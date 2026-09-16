# Behavioral Model: agent-adapter-designer

**Bundle**: `agent-adapter-designer`  
**Version**: 1.0.0  
**Generated**: 2026-06-24  
**Status**: Pre-implementation verification artifact

---

## 1. Overview

### Bundle Identity

| Field | Value |
|-------|-------|
| Bundle name / namespace | `agent-adapter-designer` |
| Primary entry point | `/mode amplifier-agent-adapter-designer` |
| Secondary entry point | Delegate to `agent-adapter-designer:adapter-design-expert` |

### Component Inventory

| Mechanism | Name | File |
|-----------|------|------|
| Mode | `amplifier-agent-adapter-designer` | `modes/amplifier-agent-adapter-designer.md` |
| Agent | `adapter-design-expert` | `agents/adapter-design-expert.md` |
| Context (thin) | `adapter-design-awareness.md` | `context/adapter-design-awareness.md` |
| Context (heavy) | `integration-reference.md` | `context/integration-reference.md` |
| Behavior | `agent-adapter-designer-behavior` | `behaviors/agent-adapter-designer.yaml` |
| Bundle | `agent-adapter-designer` | `bundle.md` |

### Objectives Served

1. **Self-sufficient design workspace**: A developer entering the mode needs no prior knowledge of `amplifier-agent` to begin productive adapter design.
2. **Opinionated surface guidance**: The bundle carries a full evidence base (three surfaces, three case studies) and gives concrete recommendations — not "it depends" hedging.
3. **Concrete deliverable**: The design journey ends with a written adapter design document.
4. **Context-sink discipline**: Root sessions stay thin; heavy reference material is loaded only when the expert agent is spawned.

---

## 2. Tool Governance

### Mode: `amplifier-agent-adapter-designer`

| Tool | Policy | Rationale |
|------|--------|-----------|
| `read_file` | safe | Reading host code and integration docs is core to design |
| `glob` | safe | File exploration for host codebase context |
| `grep` | safe | Searching for patterns in host code |
| `delegate` | safe | Essential — route deep questions to `adapter-design-expert` |
| `web_fetch` | safe | Looking up SDK docs, version info |
| `todo` | safe | Tracking design decisions and open questions |
| `load_skill` | safe | Loading relevant design skills on demand |
| `mode` | safe | Mode transitions (exit the design mode when done) |
| `bash` | warn | Shell probes (e.g. `amplifier-agent version --json`) useful but require acknowledgment |
| `write_file` | warn | Design doc production requires acknowledgment — prevents accidental writes |
| `edit_file` | warn | Editing design artifact requires acknowledgment |
| All others | block (default) | No code execution, no package installs during design |

### Out-of-mode (root session, no mode active)

All tools operate normally per foundation defaults. The awareness context file provides discovery without restricting behavior.

---

## 3. Mode Behaviors

### Mode: `amplifier-agent-adapter-designer`

**Activation**: User runs `/mode amplifier-agent-adapter-designer` or `mode(operation="set", name="amplifier-agent-adapter-designer")`.

**What activates**: The mode's markdown body is injected as an ephemeral `system-reminder` on every LLM call. The developer immediately has:
- A summary of the three integration surfaces (Python SDK, TypeScript SDK, HTTP server)
- Pointers to the three case studies (opencode, paperclip, nanoclaw)
- The delegation target for deep questions (`adapter-design-expert`)
- A structured design journey (5 steps: host runtime → surface selection → pattern borrowing → cross-cutting checklist → design artifact)
- A template for the design document output

**What does NOT activate**: The full integration reference (~2500 tokens) is not injected. The mode body is a thin pointer that delegates heavy questions to the expert agent.

**Tool policy in effect**: `warn` on bash and write operations; `block` on default. This is a design conversation — the developer explores, delegates to the expert, and eventually writes one document.

**Allowed transitions**: Any mode (no `allowed_transitions` restriction). `allow_clear: true` — developer exits by running `/mode clear` or `mode(operation="clear")`.

**Exit behavior**: When the developer clears the mode, the design document (if written) remains on disk. No cleanup required.

---

## 4. Agent Behaviors

### Agent: `adapter-design-expert`

**Role**: Authoritative on all aspects of `amplifier-agent` integration. Carries the complete integration reference via `@mention` — all three surfaces, three case studies, ten cross-cutting concerns.

**Model role**: `[reasoning, general]` — deep technical recommendations require reasoning-class models; `general` is the fallback.

**Context loaded in agent session**: `context/integration-reference.md` (~2500 tokens) via `@mention`. This is the only mechanism that loads this file — it never appears in the root session or mode injection.

**Invocation pattern**: Parent session delegates via `delegate` tool. The agent receives a specific question and returns a structured answer with:
- Direct answer
- Specific names (API, env var, function, constant)
- Trade-offs section (when recommending a surface)
- Gotchas section (when cross-cutting concerns apply)

**Not invoked for**: Simple orientation questions the mode body already answers (e.g., "what are my three options?" — the mode prompt covers this).

**Turn budget**: 8–12 turns. Questions are specific; answers should be precise, not exhaustive.

---

## 5. Skill Behaviors

**None.** The expert agent covers all reference and reasoning needs. A skill would duplicate the agent's function at higher per-turn visibility cost with no gain in isolation or capability. The agent is the correct mechanism: it isolates the expensive reference context in a disposable child session.

---

## 6. Context and Cross-Cutting Concerns

### Token Floor (per-turn, with mode active)

| Component | Tokens | Notes |
|-----------|--------|-------|
| Foundation base context | ~12,000 | Common-agent-base, delegation instructions, etc. |
| `adapter-design-awareness.md` | ~200 | Thin pointer — always loaded when bundle is composed |
| Mode body (`amplifier-agent-adapter-designer.md`) | ~900 | Ephemeral, only while mode is active |
| Skills L1 visibility | ~1,200 | Standard per-skill overhead |
| Hook injections | ~300 | Status context, git |
| Reserved | ~6,000 | Output buffer + safety margin |
| **Total (mode active)** | **~20,600** | ~10% of 200K window |
| **Total (mode inactive)** | **~19,700** | Slightly lighter |

### Context Lifecycle

| Content | Loaded when | Lifecycle |
|---------|-------------|-----------|
| `adapter-design-awareness.md` | Every turn (system prompt) | Permanent — immune to compaction |
| Mode body | Every turn mode is active | Ephemeral — re-created each LLM call |
| `integration-reference.md` | Agent session only | Disposable — discarded after agent completes |
| Expert agent response | Message history after delegation | Compactable — subject to truncation |

### Delegation Chain

```
User enters mode
  → LLM uses mode body to orient developer
  → Technical question arises
  → LLM delegates to adapter-design-expert
      → Agent loads integration-reference.md (~2500 tokens, agent-only)
      → Agent answers with precision
      → Agent session completes, context discarded
  → Parent sees ~400 token result summary
  → Design continues
  → write_file produces adapter-design.md
  → Developer clears mode
```

### Context Isolation

The `integration-reference.md` is never loaded into the root session. If a developer composes this bundle but does not activate the mode and does not delegate to the expert agent, the full integration reference is never loaded. Only the thin awareness file (~200 tokens) is always present.

---

## 7. Recipe Workflows

**No recipes in v1.** The design conversation is inherently interactive — the developer describes their host, the LLM (using the mode guidance) asks clarifying questions, delegates to the expert when needed, and converges on decisions. A rigid multi-step recipe would reduce this flexibility without adding meaningful structure.

The design document production is guided by the mode discipline (template in mode body + write_file with warn policy). This is conventional enforcement, which is appropriate: the user may want to iterate before writing, or may want to copy the template to their own file manually. Structural enforcement via a recipe would be premature for v1.

**Candidate for v2**: A staged recipe — `design-interview` → user approval → `design-document-generation` — if usage shows developers benefit from more structured step-by-step guidance.

---

## 8. Behavioral Scenarios

### Scenario A: Fresh Activation — Self-Sufficient Orientation

**User**: `/mode amplifier-agent-adapter-designer`

**Expected**: Mode activates. LLM receives mode body with surface summary, case study list, expert delegation pointer, and design journey steps. LLM greets developer and asks about their host.

**LLM response**: "You're now in the amplifier-agent adapter design workspace. To get started, tell me about your host application: what language/runtime (Python, Node.js, other)? Single process or containerized? Do you already have an OpenAI-compatible API client in the host?"

**Verification**: Developer does NOT need to explain what amplifier-agent is. The mode provides full orientation. ✓ Self-sufficient.

---

### Scenario B: Surface Selection — Python Host

**User** (after mode activation): "My host is a FastAPI service. Which integration surface should I use?"

**Expected**: LLM recognizes this as a deep technical question and delegates to `adapter-design-expert`. Expert loads integration-reference.md and returns structured answer.

**Expert answer structure**:
- Direct: Python Client SDK (`amplifier-agent-py`)
- API: `await spawn_agent(session_id=..., display_mode="ndjson")`, `async for event in handle.submit(...)`
- Trade-offs: wrong for Node hosts, multi-turn burst in one subprocess, mid-turn approval callbacks
- Gotcha: not yet on PyPI, install from git source; protocol pinned to 0.3.0 via `PROTOCOL_VERSION_REQUIRED_BY_WRAPPER`

**Verification**: Specific API names cited. Wrong-case listed. Cross-cutting gotcha surfaced. ✓ Opinionated and evidence-backed.

---

### Scenario C: Case Study Reference — nanoclaw Docker Pattern

**User**: "I'm building a container product. How did nanoclaw handle the cold-start problem?"

**Expected**: LLM delegates to `adapter-design-expert`. Expert returns nanoclaw case study detail.

**Expert answer structure**:
- Pattern: `uv tool install amplifier-agent` at image build; `amplifier-agent prepare` + `doctor --strict` as Dockerfile RUN steps
- Effect: bundle cache cost paid once at `docker build`, not at first user request
- Additional patterns from nanoclaw: MCP tmpfile passthrough, host-mounted state volume, push buffering (cap=256)
- Gotcha: CI version-lint gate to detect engine upgrades that invalidate cached bundles

**Verification**: Specific commands cited. Pattern rationale clear. ✓ Evidence-backed with nanoclaw as attribution.

---

### Scenario D: Cross-Cutting Concern — MCP Injection

**User**: "My host uses MCP. How do I pass MCP config to amplifier-agent?"

**Expected**: LLM delegates to expert (or may answer from mode body if already oriented). Expert provides exact pattern.

**Expert answer**:
- Never a CLI flag
- Pattern: write to a 0600 tempfile; set `AMPLIFIER_MCP_CONFIG=/path/to/tmpfile`
- SDKs handle automatically when using spawnAgent/spawn_agent
- HTTP face: server-level injection only in v1 (per-request injection not supported)
- Nanoclaw citation: exactly this pattern in production

**Verification**: Exact env var named. HTTP face limitation surfaced. ✓ Complete and precise.

---

### Scenario E: Surface Selection — Correcting a Wrong Choice

**User**: "I'm writing a Python host but I want to use the TypeScript SDK because the code examples look cleaner."

**Expected**: LLM delegates to expert. Expert surfaces the trade-off clearly without dismissing the developer.

**Expert answer**:
- TypeScript SDK (`amplifier-agent-ts`) is designed for Node >=20 hosts only
- For Python hosts, the right choice is the Python SDK (`amplifier-agent-py`)
- Why: the SDK manages subprocess lifecycle in the host's runtime; cross-runtime use means a Node subprocess managing a Python host's lifecycle — incorrect architecture
- If the developer has a mixed Python/Node architecture, expert explains how to structure the two surfaces

**Verification**: Wrong choice corrected with evidence. Developer not left confused. ✓ Opinionated.

---

### Scenario F: Env Allowlist Question

**User**: "I need to pass `LD_PRELOAD` to amplifier-agent for a custom malloc implementation."

**Expected**: Expert provides the allowlist and clearly flags the blocker.

**Expert answer**:
- `LD_PRELOAD` is explicitly blocked in `env.extra` — throws `env_injection_rejected`
- Full blocklist: `PYTHONPATH LD_PRELOAD LD_LIBRARY_PATH PYTHONSTARTUP PYTHONHOME PYTHONNOUSERSITE DYLD_INSERT_LIBRARIES DYLD_LIBRARY_PATH`
- Always-allowed: `PATH HOME USER LANG TERM TMPDIR`, all `AMPLIFIER_*`, all `LC_*`
- Workaround: set `LD_PRELOAD` in the process that invokes the SDK (not passed as env.extra), so the subprocess inherits it via `PATH`

**Verification**: Exact error name cited. Concrete workaround provided. ✓ Complete.

---

### Scenario G: Design Document Production

**User**: "I've decided: TypeScript SDK, following the paperclip adapter registry pattern. Help me write my design doc."

**Expected**: LLM uses mode body template to produce structured design document. Writes to `adapter-design.md` via `write_file`. Mode warns once; developer confirms.

**Document includes**:
- Chosen surface: TypeScript Client SDK, rationale
- Why not Python SDK, why not HTTP server
- Architecture overview: adapter registry, per-turn spawn, workspace slug
- Pattern borrowing from paperclip: `registerServerAdapter`, `registerUIAdapter`, workspace-per-agent `<prefix>-<company-id>-<agent-id>`
- Cross-cutting decisions table
- Risk register: cold-start cliff (HIGH), protocol skew (MEDIUM), workspace slug collisions (LOW)

**After write**: LLM tells developer the doc is saved. Suggests `/mode clear` to exit.

**Verification**: Document matches template structure. ✓ Concrete deliverable produced.

---

### Scenario H: Design Review

**User** (has existing draft): "I have a draft adapter design. Can you review it for gaps?"

**Expected**: LLM asks developer to share the draft (or read it via read_file). Delegates to expert with draft content. Expert checks against cross-cutting checklist.

**Expert review outputs**:
- Which cross-cutting concerns are addressed / missing
- Whether the chosen surface is consistent with host runtime stated
- Any protocol-sensitive gotchas not covered in the risk register
- Specific gaps (e.g., "draft doesn't mention bundle cache priming — add to risk register")

**Verification**: Review is systematic against known cross-cutting concerns. ✓ Actionable.

---

### Scenario I: Bundle Composed Without Mode Activated

**User composes the bundle but never activates the mode**

**Expected**: 
- Root session has `adapter-design-awareness.md` in system prompt (~200 tokens)
- LLM knows: "This session can design amplifier-agent host adapter integrations. Activate `/mode amplifier-agent-adapter-designer` to begin."
- Full integration reference NOT loaded (zero context poisoning)
- Expert agent available for direct delegation if needed

**Verification**: Thin awareness. No bloat. ✓ Zero context poisoning.

---

### Scenario J: Protocol Version Mismatch (Cross-Cutting Edge Case)

**User**: "My spawnAgent call is throwing a `protocol_version_mismatch` error."

**Expected**: Expert explains Design D6: strict-refuse on protocol skew. Self-remediating error.

**Expert answer**:
- SDKs probe engine via `amplifier-agent version --json`
- Compiled constant: `PROTOCOL_VERSION_REQUIRED_BY_WRAPPER = "0.3.0"`
- Mismatch → `AaaError(protocol_version_mismatch)` with exact reinstall commands in the error message
- Fix: reinstall `amplifier-agent` to the version the SDK expects (commands in error body)
- Override (temporary): `allowProtocolSkew: true` in SDK config, or env var
- Note: README in `amplifier-agent-ts` says 0.1.0 but source says 0.3.0 — trust source

**Verification**: Correct constant named. Override mechanism explained. Trust-source guidance. ✓

---

## 9. Assumptions and Gaps

### Assumptions Made

1. **Foundation includes modes support.** This bundle relies on the modes bundle being composed into foundation (hooks-mode, tool-mode). Mode discovery via "Composed bundle `modes/` dirs (lazy discovery)" is assumed to work without explicit `search_paths` configuration. If modes are not discovered, add `hooks-mode` config update to the behavior YAML.

2. **`amplifier-agent-py` not on PyPI.** The reference states this at time of writing. If it publishes to PyPI, update the install note in `integration-reference.md`.

3. **Protocol version 0.3.0.** The `PROTOCOL_VERSION_REQUIRED_BY_WRAPPER` constant is 0.3.0 at time of writing. This is embedded in `integration-reference.md`. Update when the SDK version changes.

4. **TypeScript SDK has zero npm runtime deps.** Stated in source material. Verify against `amplifier-agent-ts` `package.json` before publishing.

5. **Lazy mode discovery works for composed bundles.** This is listed as point 5 in the modes discovery order. If it doesn't work in practice, the fix is to add explicit `hooks-mode` config in `behaviors/agent-adapter-designer.yaml`.

### Known Gaps (v1)

1. **No structured interview recipe.** A multi-step guided interview (host runtime → requirements → constraints → recommendation → design doc) would reduce the burden on the mode's conventional guidance. Candidate for v2 if usage shows developers want more structure.

2. **No design review recipe.** Scenario H (design review) is handled conversationally. A recipe that systematically checks a draft against all cross-cutting concerns would be more thorough. v2 candidate.

3. **Mode cannot structurally enforce document production.** The design doc is guided by mode discipline (conventional enforcement), not a recipe gate (structural enforcement). A developer can exit the mode without writing a doc. This is intentional for v1 — the conversation may be the deliverable (e.g., the developer is only exploring options, not committing to an implementation).

4. **Binary discovery order in Python SDK.** The reference says binary discovery is `AMPLIFIER_AGENT_BIN` → `which amplifier-agent` with no constructor param. However, the Python SDK's exact `env.extra` API surface (compared to the TypeScript SDK's `ChildProcessFactory`) needs verification against the actual `amplifier-agent-py` source.

5. **HTTP face auth flow for multi-tenant hosts.** The `AMPLIFIER_AGENT_HTTP_API_KEY` env var is documented, but the full auth flow for hosts with per-request auth is not in the source material. If a developer needs this, the expert agent should direct them to the HTTP server docs rather than speculate.

### Open Questions for User Approval

See **Deliverable 4** in the design summary for the full list of questions that require user sign-off before publishing.

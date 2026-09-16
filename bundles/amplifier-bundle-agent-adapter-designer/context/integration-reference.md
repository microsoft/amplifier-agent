# amplifier-agent Integration Reference

Complete reference for host adapter engineers integrating `amplifier-agent` into a
host application. Covers all three integration surfaces, all three host adapter
case studies, and all cross-cutting concerns.

---

## Integration Surfaces

### 1. Python Client SDK (`amplifier-agent-py`)

**Summary**: Spawns `amplifier-agent` as a single-turn subprocess from a Python host.
The SDK manages process lifecycle; the host yields `DisplayEvent` objects from the
`submit()` call.

#### API

**Async (primary)**:
```python
from amplifier_agent_py import spawn_agent

handle = await spawn_agent(
    session_id="my-session-id",
    display_mode="ndjson",      # "ndjson" for JSON events; default is human text
    workspace="my-app-<slug>",  # optional workspace slug
)
async for event in handle.submit("User prompt here"):
    # event is a DisplayEvent object
    process(event)
```

**Sync (context-manager)**:
```python
from amplifier_agent_py import spawn_agent_sync

with spawn_agent_sync(session_id="...", display_mode="ndjson") as handle:
    for event in handle.submit("User prompt"):
        process(event)
```

#### When Right
- Python hosts: Django, Flask, FastAPI, Celery workers, scripts, CLI tools
- Single-turn request/response model
- Need sync-compatible interface (context manager variant)

#### When Wrong
- Node.js hosts (use TypeScript SDK instead)
- Multi-turn burst within a single subprocess call
- Mid-turn HITL approval callbacks
- Bidirectional streaming while the agent runs

#### Protocol
Pinned to `0.3.0` via compiled constant `PROTOCOL_VERSION_REQUIRED_BY_WRAPPER`.
SDK probes engine with `amplifier-agent version --json` at startup.

#### Install
Not yet on PyPI. Install from git source:
```bash
pip install git+https://github.com/microsoft/amplifier-agent-py.git
# or:
uv add git+https://github.com/microsoft/amplifier-agent-py.git
```

---

### 2. TypeScript Client SDK (`amplifier-agent-ts`)

**Summary**: Equivalent subprocess model for Node.js hosts. Zero npm runtime
dependencies. Ships a `ChildProcessFactory` injection point for testing.

#### API

```typescript
import { spawnAgent } from 'amplifier-agent-ts';
import { randomUUID } from 'crypto';

const session = await spawnAgent({
  lifecycle: 'one-shot',
  sessionId: randomUUID(),
  workspace: 'my-app-<slug>',  // optional
});

for await (const ev of session.submit("User prompt here")) {
  // ev is a DisplayEvent discriminated union
  switch (ev.type) {
    case 'text': handleText(ev); break;
    case 'tool_call': handleToolCall(ev); break;
    // ...
  }
}
```

#### ChildProcessFactory (for testing/sandboxing)

```typescript
import { spawnAgent, ChildProcessFactory } from 'amplifier-agent-ts';

const session = await spawnAgent({
  lifecycle: 'one-shot',
  sessionId: randomUUID(),
  processFactory: new MockChildProcessFactory(), // injected in tests
});
```

#### When Right
- Node.js hosts (version >=20)
- TypeScript/JavaScript codebases
- Need process isolation with zero npm runtime dependencies
- Test-time subprocess injection via `ChildProcessFactory`

#### When Wrong
- Python hosts (use Python SDK instead)
- In-process burst without subprocess overhead
- Mid-turn HITL approval callbacks (not supported in v1)

#### Protocol
README in the repo says 0.1.0 — **this is outdated**. The source code says 0.3.0.
Trust the source: `PROTOCOL_VERSION_REQUIRED_BY_WRAPPER = "0.3.0"`.

---

### 3. HTTP Chat-Completions Server

**Summary**: OpenAI-compatible HTTP server. Start it as a sidecar; host connects to
it as any OpenAI client. Amortizes bundle-load cost across requests.

#### Start

```bash
amplifier-agent serve chat-completions \
  --port 9099 \
  --config /path/to/host_config.json
```

#### Endpoints

| Endpoint | Method | Response | Notes |
|----------|--------|----------|-------|
| `/v1/chat/completions` | POST | SSE or JSON | Standard OpenAI shape |
| `/v1/models` | GET | OpenAI shape + extensions | Includes amplifier-specific metadata |

#### Key Environment Variables

| Var | Purpose |
|-----|---------|
| `AMPLIFIER_AGENT_HTTP_API_KEY` | Auth key required in `Authorization: Bearer` header |
| `AMPLIFIER_AGENT_HTTP_PORT` | Port override (default 9099) |
| `AMPLIFIER_AGENT_HTTP_BIND` | Bind address |
| `AMPLIFIER_AGENT_HTTP_WORKSPACE` | Workspace slug for all requests |
| `AMPLIFIER_AGENT_HTTP_CONFIG_PATH` | Override config file path |

#### host_config.json requirement

The `providers` block is **required**. Missing providers block → exit code 2.

```json
{
  "providers": [
    { "name": "anthropic", "api_key": "${ANTHROPIC_API_KEY}" }
  ]
}
```

#### When Right
- Host already speaks OpenAI API (minimal adaptation code)
- Multi-provider routing from a single endpoint
- Long-lived server amortizes bundle-load cost over many requests
- Host is language-agnostic or polyglot

#### When Wrong
- Per-turn MCP injection (server-level only in v1; no per-request MCP)
- HITL approval (HTTP face auto-approves all tool confirmations in v1)
- Per-request workspace isolation (process-scope only; all requests share one workspace in v1)

---

## Host Adapter Case Studies

### opencode — HTTP Face

**Integration surface**: HTTP chat-completions server

**Architecture**: The opencode CLI spawns `amplifier-agent serve chat-completions`
as a background process, waits for it to become ready, then execs `opencode`.

#### Pattern Layers

1. **Auto-start + readiness poll**: CLI starts server, polls `GET /v1/models` until 200.
2. **Model discovery**: Reads model list from `/v1/models` endpoint.
3. **Config write**: Writes opencode `provider` block from discovered models (no manual config).
4. **Credential auto-detect**: Automatically detects 4 providers: Anthropic, OpenAI, Azure OpenAI, Ollama.
5. **Session correlation**: Client sends `X-Client-Session-Id` header; server returns `X-Session-Id`.

#### Key Lesson

When the host already speaks OpenAI API, HTTP face integration is nearly free. The host
needs almost zero adaptation code — it talks to amplifier-agent the same way it talks to
any OpenAI-compatible provider. Model discovery + config-write automation means the
developer doesn't even need to manually configure the opencode provider block.

---

### paperclip — TypeScript SDK

**Integration surface**: TypeScript Client SDK (`amplifier-agent-ts`)

**Architecture**: Per-turn subprocess via `spawnAgent()`. amplifier-agent is one
provider in a mutable adapter registry — hosts can register/unregister adapters at
runtime without forking core.

#### Pattern Layers

1. **Adapter registry**: `registerServerAdapter()` / `registerUIAdapter()` at startup.
   amplifier-agent is registered as one entry in this registry alongside other providers.
2. **Runtime validation**: Adapters are validated at registration time, not at call time.
3. **Per-turn spawn**: Each agent turn creates a fresh `spawnAgent()` subprocess. Stateless.
4. **Workspace-per-agent**: Workspace slug format: `pc-<company-id>-<agent-id>`.
   Per-agent isolation prevents state cross-contamination.

#### Key Lesson

The adapter-registry pattern lets a host treat amplifier-agent as one provider among many
without touching core dispatch logic. When an agent is selected, the registry finds the
right adapter and calls it. amplifier-agent is just another adapter — no special-casing.
Workspace slug discipline (`pc-<company-id>-<agent-id>`) provides clean per-agent isolation.

---

### nanoclaw — TypeScript SDK Inside Docker

**Integration surface**: TypeScript Client SDK inside a Docker container product.

**Architecture**: `AmplifierAgentProvider` implements NanoClaw's `AgentProvider`
interface. amplifier-agent is installed at image build time.

#### Pattern Layers

1. **Binary install at build**: `uv tool install amplifier-agent` in `Dockerfile`.
   Binary is baked into the image.
2. **Bundle priming at build**: `amplifier-agent prepare` + `amplifier-agent doctor --strict`
   as `Dockerfile RUN` steps. Bundle cache is warm before any user request.
3. **MCP passthrough**: Write MCP config to a 0600 tempfile; set `AMPLIFIER_MCP_CONFIG`.
4. **Host-mounted state volume**: amplifier-agent state directory is a Docker volume.
   State persists across container restarts and upgrades.
5. **Push buffering**: Buffered event queue with cap=256. Visible-drop on overflow
   (log + discard) to avoid backpressure deadlock.
6. **Chained turns with `resume: true`**: Multi-turn conversations resume previous session.
7. **Auto-allow approval**: HITL gates auto-approved for automated container flows.
8. **CI version-lint gate**: CI pipeline checks `amplifier-agent version --json` against
   pinned version. Build fails if version drifts.

#### Key Lesson

Container integration means bundle cache cost is paid once at `docker build` (or image
pull) time, not at first user request. A warm `docker pull` starts instantly; a cold
`amplifier-agent` would take 5–30s. The CI version-lint gate catches silent engine upgrades
before they reach production.

---

## Cross-Cutting Concerns

### 1. Credential Management

Provider keys are passed via environment variables:

| Provider | Environment Variable(s) |
|----------|------------------------|
| Anthropic | `ANTHROPIC_API_KEY` |
| OpenAI | `OPENAI_API_KEY` |
| Azure OpenAI | `AZURE_OPENAI_API_KEY` + `AZURE_OPENAI_ENDPOINT` |
| Ollama | `OLLAMA_HOST` |

**HTTP face**: `providers` block in `host_config.json` is **required**. Absent → exit code 2
with a clear error. The `providers` block must list the keys explicitly (no env-var auto-detect
in v1 HTTP face).

---

### 2. MCP Config Injection

**Rule: Never a CLI flag.**

```
1. Write MCP config JSON to a 0600 tempfile
2. Set AMPLIFIER_MCP_CONFIG=/path/to/tmpfile
3. Launch amplifier-agent (SDK handles this; HTTP face: server-level only)
```

Python and TypeScript SDKs pass the tempfile path automatically when you use their
`mcpConfig` option. For the HTTP face in v1, MCP config is server-level only — you
cannot inject different MCP configs per-request.

**nanoclaw pattern** (production-verified):
```typescript
const tmpfile = writeTempFile(JSON.stringify(mcpConfig), { mode: 0o600 });
process.env.AMPLIFIER_MCP_CONFIG = tmpfile.path;
const session = await spawnAgent({ ... });
```

---

### 3. Bundle Cache Priming

**Cold-start cliff**: 5–30 seconds on first call (git clone ~11 module repos + pip install).

**Cache location**: `~/.cache/amplifier-agent/prepared/<aaa_version>/<sha256(bundle.md)>/`

**Solutions by deployment type**:

| Deployment | Pattern |
|------------|---------|
| `uv tool install` | `amplifier-agent-post-install` hook runs `prepare` automatically |
| Manual install | Run `amplifier-agent prepare` after install |
| Docker | Add `RUN amplifier-agent prepare && amplifier-agent doctor --strict` to Dockerfile |
| CI | Add prepare step after install in CI pipeline |

After priming, subsequent starts are near-instant (bundle already materialized).

---

### 4. Protocol Version Pinning

SDKs verify engine compatibility at startup:

```bash
amplifier-agent version --json
# → { "protocol_version": "0.3.0", ... }
```

Compiled constant in SDKs: `PROTOCOL_VERSION_REQUIRED_BY_WRAPPER = "0.3.0"`

**Mismatch behavior** (Design D6 — strict refuse):
- Throws `AaaError(protocol_version_mismatch)`
- Error body contains exact reinstall commands (self-remediating error)
- No silent degradation

**Override** (use sparingly):
```python
# Python
handle = await spawn_agent(..., allow_protocol_skew=True)
```
```typescript
// TypeScript
const session = await spawnAgent({ ..., allowProtocolSkew: true });
```
Or set the env var (exact name varies by SDK version — check README).

---

### 5. Binary Discovery Order

**Resolution sequence** (first match wins):
1. `AMPLIFIER_AGENT_BIN` environment variable (absolute path)
2. `which amplifier-agent` (PATH lookup)

No constructor parameter — binary path is NOT configurable in the SDK API.
Inspect the resolved path after startup:
```python
info = await handle.get_engine_info()
print(info.binary_path)
```
```typescript
const info = await session.getEngineInfo();
console.log(info.binaryPath);
```

---

### 6. Env Allowlist

The subprocess only receives a restricted set of environment variables:

**Always inherited**: `PATH HOME USER LANG TERM TMPDIR`, all `AMPLIFIER_*`, all `LC_*`

**Extend with** `env.extra` in SDK config (key–value pairs).

**Blocked in `env.extra`** (throws `env_injection_rejected`):
```
PYTHONPATH
LD_PRELOAD
LD_LIBRARY_PATH
PYTHONSTARTUP
PYTHONHOME
PYTHONNOUSERSITE
DYLD_INSERT_LIBRARIES
DYLD_LIBRARY_PATH
```

If you need to pass these to the subprocess, set them in the parent process before
spawning the SDK — they will be inherited via the OS (not via `env.extra`).

---

### 7. Workspace Isolation

Route per-agent state via `--workspace <slug>`:

**State path**: `~/.amplifier-agent/state/workspaces/<slug>/sessions/<id>/`

**Slug grammar**: `[a-z0-9][a-z0-9-]{0,63}` (starts with alphanumeric, up to 64 chars)

**Rule**: Multi-agent hosts **MUST** set per-agent workspace slugs to prevent cross-contamination.

| Host | Slug pattern | Example |
|------|-------------|---------|
| paperclip | `pc-<company-id>-<agent-id>` | `pc-acme-7f3a` |
| nanoclaw | host-mounted volume at workspace path | `nc-session-<id>` |

**Docker**: Mount the workspace directory as a Docker volume to persist state across
container restarts:
```dockerfile
VOLUME /root/.amplifier-agent/state/workspaces/
```

---

### 8. Sync vs Async Ergonomics

| SDK | Async | Sync |
|-----|-------|------|
| Python (`amplifier-agent-py`) | ✓ `await spawn_agent(...)` | ✓ `spawn_agent_sync(...)` context manager |
| TypeScript (`amplifier-agent-ts`) | ✓ `await spawnAgent(...)` | ✗ async-only |

Neither SDK supports mid-turn approval callbacks in v1. Approval gates either
auto-approve (nanoclaw pattern, HTTP face default) or block the turn.

---

### 9. DisplayEvent / Notification Stream

**Default** (no displayMode set): Human-readable text to stderr. Not machine-parseable.

**JSON mode**: `display_mode="ndjson"` (Python) / `displayMode: "ndjson"` (TypeScript).
Switches to JSON-RPC `DisplayEvent` objects on stdout.

**DisplayEvent types** (discriminated union in TypeScript, typed objects in Python):
- `text` — agent output text
- `tool_call` — tool invocation start
- `tool_result` — tool invocation result
- `notification` — status/progress notifications
- (more — exact set in SDK source)

**HTTP face**: Translates events to SSE format automatically. Host receives
`data: <json>` lines in the SSE stream.

**Push buffering** (nanoclaw pattern for container hosts):
```typescript
const BUFFER_CAP = 256;
const buffer: DisplayEvent[] = [];

for await (const ev of session.submit(prompt)) {
  if (buffer.length >= BUFFER_CAP) {
    logger.warn('amplifier-agent buffer overflow — dropping event');
    continue; // visible drop
  }
  buffer.push(ev);
}
```

Visible-drop is preferred over backpressure deadlock for async-to-sync bridging.

---

### 10. Multi-Turn / Chained Turns

Single SDK call = single turn. For multi-turn conversations:

**Python/TypeScript SDK**: Call `submit()` multiple times on the same `session_id`
with `resume=True`. Each call resumes the previous session.

```typescript
// Turn 1
for await (const ev of session.submit("First message")) { ... }

// Turn 2 (resuming same session)
const session2 = await spawnAgent({ sessionId: sameSessionId, resume: true });
for await (const ev of session2.submit("Follow-up message")) { ... }
```

**HTTP face**: Standard chat-completions multi-turn — include the full `messages`
array in each request (assistant's previous response in the history).

---

## Surface Selection Decision Tree

```
What is your host runtime?
  ├─ Python → Python Client SDK (amplifier-agent-py)
  │
  ├─ Node.js >=20 → TypeScript Client SDK (amplifier-agent-ts)
  │
  └─ Other / Polyglot / Already OpenAI-shaped
       ├─ Host already calls OpenAI API? → HTTP Chat-Completions Server
       ├─ Long-lived server process acceptable? → HTTP Chat-Completions Server
       └─ Need per-request workspace isolation or per-turn MCP? → Re-evaluate
          (HTTP face does not support these in v1; consider wrapping SDK in a sidecar)
```

---

## Risk Register Template

| Risk | Affected Surfaces | Severity | Mitigation |
|------|-------------------|----------|------------|
| Cold-start cliff (5–30s first call) | All | High | Run `amplifier-agent prepare` at install/build |
| Protocol skew after engine upgrade | Python SDK, TS SDK | Medium | Pin version in CI; `allowProtocolSkew: false` (default) |
| State cross-contamination (multi-agent) | All | High | Unique workspace slug per agent |
| MCP secrets leaked via CLI args | All | High | Always use tmpfile + `AMPLIFIER_MCP_CONFIG` |
| HITL approval bypassed silently | HTTP face | Medium | HTTP face auto-approves; design for it or avoid HTTP face if HITL needed |
| Per-turn MCP injection not supported | HTTP face | Medium | Server-level only in v1; per-turn needs SDK |
| Blocked env var in `env.extra` | Python SDK, TS SDK | Low-Medium | Check allowlist before adding to `env.extra` |
| Push buffer overflow under load | All (streaming) | Medium | Implement visible-drop with logging (nanoclaw pattern) |
| Binary not found at startup | All | Medium | Set `AMPLIFIER_AGENT_BIN`; run `amplifier-agent doctor` at install |
| Bundle cache invalidated by upgrade | All | Medium | Hash check in CI; re-run `amplifier-agent prepare` on upgrade |

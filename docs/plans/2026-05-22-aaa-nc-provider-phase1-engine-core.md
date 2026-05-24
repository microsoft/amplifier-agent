# AaA NC Provider — Phase 1: Engine Core Implementation Plan

> **For execution:** Use `/execute-plan` mode or the subagent-driven-development recipe.

**Goal:** Land the foundation pieces of the amplifier-agent v0.2.0 release — additive wire fields, app-layer session persistence (CR-1), wire-bridged approval provider (CR-2), and wrapper security hardening (SC-3, SC-7).

**Architecture:** Three new engine files plus `_runtime.py` wiring implement the session persistence and approval-shim patterns from `amplifier-app-cli`. Wire changes are additive-only (no new methods). Wrapper additions tighten env safety and convert one sync probe to async. All four pieces commit-clean independently and gate on existing pytest and typecheck suites.

**Tech Stack:** Python (`uv`, pytest, pyright, ruff), TypeScript (pnpm, vitest), JSON Schema (Draft 2020-12).

---

## Before you start

**Read the design doc.** Everything in this plan traces back to it. Open it in a second tab and keep it there:

```
docs/designs/2026-05-22-aaa-v2-amplifier-agent-nc-provider.md
```

Also keep the 2026-05-20 locked-wire design handy:

```
docs/designs/2026-05-20-aaa-v2-wrapper-and-wire.md
```

**You are on branch `feat/phase-2-2-2-3-2-5-wrappers-and-conformance`. Do NOT create a new branch.**

**Never** add `git push`, `git merge`, or `gh pr create` commands — those happen in a separate finish step.

---

## Key facts about this codebase (read carefully)

Before writing a single line of code, understand these:

1. **`PROTOCOL_VERSION`** (the wire protocol version string) lives in TWO places and must be changed in BOTH:
   - Python: `src/amplifier_agent_lib/protocol/methods.py` line 11 — currently `"2026-05-aaa-v0"`
   - TypeScript: `wrappers/typescript/src/index.ts` line 37 — `PROTOCOL_VERSION_REQUIRED_BY_WRAPPER = "2026-05-aaa-v0"`
   - Python wrapper: `wrappers/python/src/amplifier_agent_client/__init__.py` line 44 — `PROTOCOL_VERSION_REQUIRED_BY_WRAPPER = "2026-05-aaa-v0"`
   - **Target:** change all three to `"0.1.0"` (design §4.10.3)

2. **Python codegen command** (run after editing TypedDicts in `methods.py`, `notifications.py`, `capabilities.py`, or `errors.py`):
   ```
   uv run python -m amplifier_agent_lib.protocol._gen --output-dir src/amplifier_agent_lib/protocol
   ```
   This regenerates `src/amplifier_agent_lib/protocol/spec.md` and `src/amplifier_agent_lib/protocol/schemas/*.schema.json`.

3. **TypeScript types regen** (run after the Python codegen to sync `types.ts`):
   ```
   cd wrappers/typescript && pnpm run gen:types
   ```
   This reads `src/amplifier_agent_lib/protocol/schemas/*.schema.json` and writes `wrappers/typescript/src/types.ts`.

4. **`types.ts` is GENERATED — do not hand-edit it.** The source of truth is the Python TypedDicts.

5. **`write_with_backup` from `amplifier_foundation` is synchronous** (returns `None`, not a coroutine). Call it WITHOUT `await`. The design's pseudo-code snippets incorrectly show `await write_with_backup(...)` — ignore that.

6. **Hook registration pattern** (from `src/amplifier_agent_lib/bundle/hook_streaming.py`):
   ```python
   session.coordinator.hooks.register("tool:post", async_handler_fn, name="hook_name")
   ```
   The handler must have signature: `async def fn(event: str, data: dict[str, Any]) -> HookResult`
   `HookResult` is imported from `amplifier_core.models`: `from amplifier_core.models import HookResult`

7. **`HookPriority` does NOT exist in `amplifier_core`.** The design pseudo-code `priority = HookPriority(900)` is incorrect. Ignore it — hooks registered via `coordinator.hooks.register()` do not use a priority parameter.

8. **`AmplifierSession` attributes**: `cleanup`, `config`, `coordinator`, `execute`, `initialize`, `initialized`, `is_resumed`, `parent_id`, `session_id`. There is NO `.context` attribute. The design pseudo-code `session.context.set_messages(transcript)` is approximate. The actual mechanism for loading conversation history into a resumed session needs to be verified by inspecting the `context-simple` module's API via `session.coordinator`. See Task 7 notes.

9. **`AaaError` exists in TWO separate places:**
   - Engine-side: `src/amplifier_agent_lib/protocol/errors.py` (used by WireApprovalProvider)
   - Python wrapper-side: `wrappers/python/src/amplifier_agent_client/session.py` (used by `env_injection_rejected`)
   Both need the new fields added in Task 3.

10. **Python test runner:**
    - Main suite: `uv run pytest tests/ -v` (from repo root)
    - Python wrapper suite: `uv run pytest wrappers/python/tests/ -v` (from repo root)
    - Both: `uv run pytest tests/ wrappers/python/tests/ -v`

11. **TypeScript test runner:** `cd wrappers/typescript && pnpm test`
12. **TypeScript typecheck:** `cd wrappers/typescript && pnpm typecheck`

---

## Dependencies and task ordering

```
A1 (wire/protocol bump)
    ↓
A2 (session_store.py + incremental_save.py)   ← parallel with A3 except both touch _runtime.py
    ↓
A3 (WireApprovalProvider + _runtime.py)
    ↓
A6 (wrapper BLOCKED_ENV_KEYS + async probe)
```

- **A1 first**: wire types must be in place before A3 (needs `AaaError.severity` and `AaaError.classification`).
- **A2 before A3**: both add lines to `_runtime.py`. Complete A2's `_runtime.py` edit before starting A3's, to avoid merge conflicts with yourself.
- **A6 after A1**: the Python wrapper regen happens in A1; A6 edits must land on top of regened files.

---

## Tasks

---

### Task 1: Bump `PROTOCOL_VERSION` to `"0.1.0"` (A1)

**Design reference:** §4.10.3

**Files:**
- Edit: `src/amplifier_agent_lib/protocol/methods.py`
- Edit: `wrappers/typescript/src/index.ts`
- Edit: `wrappers/python/src/amplifier_agent_client/__init__.py`
- Edit: `tests/test_protocol_gen.py` (update hardcoded assertion)
- Create: `tests/test_protocol_version_bump.py` (new test)

---

**Step 1: Write the failing test**

Create `tests/test_protocol_version_bump.py`:

```python
"""Gate: PROTOCOL_VERSION must be '0.1.0' after the A1 wire bump."""
from amplifier_agent_lib.protocol.methods import PROTOCOL_VERSION


def test_protocol_version_is_0_1_0() -> None:
    """PROTOCOL_VERSION must equal '0.1.0' — design §4.10.3."""
    assert PROTOCOL_VERSION == "0.1.0", (
        f"Expected PROTOCOL_VERSION == '0.1.0', got {PROTOCOL_VERSION!r}. "
        "Edit src/amplifier_agent_lib/protocol/methods.py line 11."
    )
```

**Step 2: Run the test to confirm it fails**

```bash
uv run pytest tests/test_protocol_version_bump.py -v
```

Expected output:
```
FAILED tests/test_protocol_version_bump.py::test_protocol_version_is_0_1_0
AssertionError: Expected PROTOCOL_VERSION == '0.1.0', got '2026-05-aaa-v0'.
```

**Step 3: Bump the version in all three locations**

In `src/amplifier_agent_lib/protocol/methods.py`, change line 11:
```python
# Before:
PROTOCOL_VERSION = "2026-05-aaa-v0"

# After:
PROTOCOL_VERSION = "0.1.0"
```

In `wrappers/typescript/src/index.ts`, change line 37:
```typescript
// Before:
export const PROTOCOL_VERSION_REQUIRED_BY_WRAPPER = "2026-05-aaa-v0";

// After:
export const PROTOCOL_VERSION_REQUIRED_BY_WRAPPER = "0.1.0";
```

In `wrappers/python/src/amplifier_agent_client/__init__.py`, change line 44:
```python
# Before:
PROTOCOL_VERSION_REQUIRED_BY_WRAPPER = "2026-05-aaa-v0"

# After:
PROTOCOL_VERSION_REQUIRED_BY_WRAPPER = "0.1.0"
```

**Step 4: Regenerate the Python spec and schemas**

```bash
uv run python -m amplifier_agent_lib.protocol._gen --output-dir src/amplifier_agent_lib/protocol
```

Expected output:
```
[gen] wrote spec.md to src/amplifier_agent_lib/protocol
[gen] wrote N schemas + error_codes.schema.json to src/amplifier_agent_lib/protocol/schemas
```

**Step 5: Update the hardcoded assertion in `tests/test_protocol_gen.py`**

In `tests/test_protocol_gen.py`, find the function `test_gen_emits_spec_md_with_required_sections` (near line 103). Change the version assertion on line 112:

```python
# Before:
assert "2026-05-aaa-v0" in spec, "PROTOCOL_VERSION must appear"

# After:
assert "0.1.0" in spec, "PROTOCOL_VERSION must appear"
```

**Step 6: Regenerate TypeScript types**

```bash
cd wrappers/typescript && pnpm run gen:types
```

Expected output:
```
✓ Generated .../wrappers/typescript/src/types.ts
```

**Step 7: Run all tests to verify they pass**

```bash
# Python
uv run pytest tests/test_protocol_version_bump.py tests/test_protocol_gen.py tests/test_protocol_gen_staleness.py -v

# TypeScript
cd wrappers/typescript && pnpm test
cd wrappers/typescript && pnpm typecheck
```

All should pass. If `test_protocol_gen_staleness.py` fails, it means you forgot to run the regen in Step 4.

**Step 8: Commit**

```bash
git add src/amplifier_agent_lib/protocol/methods.py \
        src/amplifier_agent_lib/protocol/spec.md \
        src/amplifier_agent_lib/protocol/schemas/ \
        wrappers/typescript/src/index.ts \
        wrappers/typescript/src/types.ts \
        wrappers/python/src/amplifier_agent_client/__init__.py \
        tests/test_protocol_version_bump.py \
        tests/test_protocol_gen.py

git commit -m "feat(wire): bump PROTOCOL_VERSION to 0.1.0 (A1)"
```

---

### Task 2: Add `McpServerConfig` + `HostCapabilities` TypedDicts and extend `InitializeParams` (A1)

**Design reference:** §4.10.1

**Files:**
- Edit: `src/amplifier_agent_lib/protocol/methods.py` (add new TypedDicts + fields)
- Edit: `tests/test_protocol_gen.py` (add assertion for new schemas)
- Regen: `src/amplifier_agent_lib/protocol/schemas/` + `spec.md`
- Edit: `wrappers/typescript/src/index.ts` (add fields to `SpawnAgentParams`)
- Regen: `wrappers/typescript/src/types.ts`
- Create: `tests/test_wire_types_v01.py`

---

**Step 1: Write the failing test**

Create `tests/test_wire_types_v01.py`:

```python
"""Gate: v0.1.0 wire types — McpServerConfig, HostCapabilities, InitializeParams extensions.

Design reference: §4.10.1 of docs/designs/2026-05-22-aaa-v2-amplifier-agent-nc-provider.md
"""
from amplifier_agent_lib.protocol.methods import (
    InitializeParams,
    McpServerConfig,
    HostCapabilities,
)


def test_mcp_server_config_typeddict_exists() -> None:
    """McpServerConfig TypedDict must be importable from protocol.methods."""
    # Check required field 'transport' is present via type hints
    import typing
    hints = typing.get_type_hints(McpServerConfig)
    assert "transport" in hints, "McpServerConfig must have 'transport' field"


def test_host_capabilities_typeddict_exists() -> None:
    """HostCapabilities TypedDict must be importable from protocol.methods."""
    import typing
    hints = typing.get_type_hints(HostCapabilities)
    # Either supports_steering or supports_structured_errors must be present
    assert len(hints) >= 0, "HostCapabilities must be a valid TypedDict"


def test_initialize_params_has_mcp_servers_field() -> None:
    """InitializeParams must include mcpServers as a NotRequired field."""
    import typing
    hints = typing.get_type_hints(InitializeParams, include_extras=True)
    assert "mcpServers" in hints, (
        "InitializeParams must have 'mcpServers' field. "
        "Add it as NotRequired[dict[str, McpServerConfig]] in methods.py."
    )


def test_initialize_params_has_host_field() -> None:
    """InitializeParams must include host as a NotRequired field."""
    import typing
    hints = typing.get_type_hints(InitializeParams, include_extras=True)
    assert "host" in hints, (
        "InitializeParams must have 'host' field. "
        "Add it as NotRequired[InitializeHostParams] in methods.py."
    )
```

**Step 2: Run the test to confirm it fails**

```bash
uv run pytest tests/test_wire_types_v01.py -v
```

Expected: FAILED with `ImportError: cannot import name 'McpServerConfig' from 'amplifier_agent_lib.protocol.methods'`

**Step 3: Add the new TypedDicts and fields to `methods.py`**

Open `src/amplifier_agent_lib/protocol/methods.py` and add the following BEFORE the `InitializeParams` class (around line 40, after the imports and `PROTOCOL_VERSION`). Keep the docstrings — they feed into the JSON Schema `"description"` field.

```python
# ---------------------------------------------------------------------------
# v0.1.0 additions — MCP server config (design §4.10.1)
# ---------------------------------------------------------------------------


class McpServerConfig(TypedDict):
    """Configuration for a single MCP server connection.

    ``transport`` is required; all other fields are transport-variant-dependent.
    """

    transport: str  # 'stdio' | 'sse' | 'streamable_http'
    command: NotRequired[str]
    args: NotRequired[list[str]]
    env: NotRequired[dict[str, str]]
    url: NotRequired[str]
    headers: NotRequired[dict[str, str]]


class HostCapabilities(TypedDict, total=False):
    """Capabilities advertised by the connecting host (design §4.10.1)."""

    supports_steering: bool
    supports_structured_errors: bool


class InitializeHostParams(TypedDict, total=False):
    """Optional host block inside agent/initialize params."""

    capabilities: HostCapabilities
```

Then add two `NotRequired` fields to `InitializeParams` (after `cwd`):

```python
class InitializeParams(TypedDict):
    """Parameters for the ``initialize`` JSON-RPC method."""

    protocolVersion: str
    clientInfo: ClientInfo
    capabilities: dict[str, Any]
    sessionId: NotRequired[str]
    resume: NotRequired[bool]
    providerOverride: NotRequired[str]
    cwd: NotRequired[str]
    # v0.1.0 additions (design §4.10.1)
    mcpServers: NotRequired[dict[str, McpServerConfig]]
    host: NotRequired[InitializeHostParams]
```

**Step 4: Run codegen to produce updated schemas**

```bash
uv run python -m amplifier_agent_lib.protocol._gen --output-dir src/amplifier_agent_lib/protocol
```

**Step 5: Update `test_protocol_gen.py` to expect the new schemas**

In `tests/test_protocol_gen.py`, in `test_gen_emits_schema_for_every_typeddict`, add the new schema names to `expected`:

```python
expected = {
    "InitializeParams.schema.json",
    "InitializeResult.schema.json",
    "TurnSubmitParams.schema.json",
    "TurnSubmitResult.schema.json",
    "ResultDeltaNotification.schema.json",
    "ResultFinalNotification.schema.json",
    "ApprovalRequestNotification.schema.json",
    "ClientCapabilities.schema.json",
    "ServerCapabilities.schema.json",
    "error_codes.schema.json",
    # v0.1.0 additions:
    "McpServerConfig.schema.json",
    "HostCapabilities.schema.json",
    "InitializeHostParams.schema.json",
}
```

**Step 6: Regenerate TypeScript types**

```bash
cd wrappers/typescript && pnpm run gen:types
```

**Step 7: Add `mcpServers` and `host` fields to TypeScript `SpawnAgentParams`**

Open `wrappers/typescript/src/index.ts`. Add imports from `./types.js` near the top of the file (around line 13, after the existing imports). The `McpServerConfig` and `HostCapabilities` interfaces are now in the generated `types.ts`:

```typescript
// Add this import near the top with other imports:
import type { McpServerConfig, HostCapabilities } from "./types.js";
```

Then add two optional fields to `SpawnAgentParams` (after `allowProtocolSkew`):

```typescript
/** Default false; opt out of D6 strict-refuse version check. */
allowProtocolSkew?: boolean;

// v0.1.0 additions (design §4.10.1)
/** MCP server configs to pass through to the engine's tool-mcp module. */
mcpServers?: Record<string, McpServerConfig>;
/** Host capabilities advertised to the engine. */
host?: { capabilities?: HostCapabilities };
```

Also pass the new fields through in the `rpc.call("agent/initialize", ...)` block (around line 199):

```typescript
const initResult = (await rpc.call("agent/initialize", {
  protocolVersion: PROTOCOL_VERSION_REQUIRED_BY_WRAPPER,
  clientInfo: { name: "amplifier-agent-client-ts", version: "0.0.0" },
  capabilities,
  sessionId: params.sessionId,
  resume: params.resume,
  cwd: params.cwd,
  providerOverride: params.providerOverride,
  // v0.1.0 additions:
  mcpServers: params.mcpServers,
  host: params.host,
})) as { ... }
```

Similarly update the Python `spawn_agent` function in `wrappers/python/src/amplifier_agent_client/__init__.py` — add `mcp_servers` and `host` parameters to `spawn_agent()` and pass them in the `rpc.call("agent/initialize", ...)` payload.

**Step 8: Run all tests**

```bash
uv run pytest tests/test_wire_types_v01.py tests/test_protocol_gen.py tests/test_protocol_gen_staleness.py -v
cd wrappers/typescript && pnpm typecheck && pnpm test
```

All must pass.

**Step 9: Commit**

```bash
git add src/amplifier_agent_lib/protocol/methods.py \
        src/amplifier_agent_lib/protocol/spec.md \
        src/amplifier_agent_lib/protocol/schemas/ \
        wrappers/typescript/src/index.ts \
        wrappers/typescript/src/types.ts \
        wrappers/python/src/amplifier_agent_client/__init__.py \
        tests/test_wire_types_v01.py \
        tests/test_protocol_gen.py

git commit -m "feat(wire): add McpServerConfig, HostCapabilities, InitializeParams.mcpServers/.host (A1)"
```

---

### Task 3: Add `AaaError` fields + new `ErrorCode` values (A1)

**Design reference:** §4.10.2

**Files:**
- Edit: `src/amplifier_agent_lib/protocol/errors.py`
- Edit: `wrappers/python/src/amplifier_agent_client/session.py` (wrapper's separate `AaaError`)
- Regen schemas (ErrorCode drives the generated `error_codes.schema.json`)
- Create: `tests/test_aaa_error_v01.py`

---

**Step 1: Write the failing test**

Create `tests/test_aaa_error_v01.py`:

```python
"""Gate: AaaError gains severity/classification fields; ErrorCode gains approval codes.

Design reference: §4.10.2 of docs/designs/2026-05-22-aaa-v2-amplifier-agent-nc-provider.md
"""
from amplifier_agent_lib.protocol.errors import AaaError, ErrorCode


def test_aaa_error_has_severity_field() -> None:
    """AaaError must accept and expose a 'severity' kwarg."""
    err = AaaError(code="approval_timeout", message="timed out", severity="error")
    assert err.severity == "error"


def test_aaa_error_has_classification_field() -> None:
    """AaaError must accept and expose a 'classification' kwarg."""
    err = AaaError(
        code="approval_translation_failed",
        message="bad shape",
        classification="approval",
    )
    assert err.classification == "approval"


def test_aaa_error_has_correlation_id_field() -> None:
    """AaaError must accept and expose a 'correlation_id' kwarg."""
    err = AaaError(code="internal", message="oops", correlation_id="req-abc")
    assert err.correlation_id == "req-abc"


def test_error_code_has_approval_translation_failed() -> None:
    """ErrorCode must include APPROVAL_TRANSLATION_FAILED = 'approval_translation_failed'."""
    assert ErrorCode.APPROVAL_TRANSLATION_FAILED == "approval_translation_failed"


def test_error_code_has_approval_protocol_violation() -> None:
    """ErrorCode must include APPROVAL_PROTOCOL_VIOLATION = 'approval_protocol_violation'."""
    assert ErrorCode.APPROVAL_PROTOCOL_VIOLATION == "approval_protocol_violation"


def test_error_code_has_env_injection_rejected() -> None:
    """ErrorCode must include ENV_INJECTION_REJECTED = 'env_injection_rejected'."""
    assert ErrorCode.ENV_INJECTION_REJECTED == "env_injection_rejected"


def test_aaa_error_defaults_optional_fields_to_none() -> None:
    """AaaError with only required fields must have None for optional fields."""
    err = AaaError(code="internal", message="oops")
    assert err.severity is None
    assert err.classification is None
    assert err.correlation_id is None
    assert err.stderr_tail is None
```

**Step 2: Run the test to confirm it fails**

```bash
uv run pytest tests/test_aaa_error_v01.py -v
```

Expected: FAILED — `AaaError.__init__` only accepts `code` and `message`.

**Step 3: Update `errors.py` — add fields to `AaaError` and new `ErrorCode` values**

Replace the contents of `src/amplifier_agent_lib/protocol/errors.py` with:

```python
"""Wire-level error codes for JSON-RPC error.data.code field.

Unifies design Appendix A 'Error codes' with Phase 1 spec additions.
Each value is the exact string that appears on the wire.
"""

from __future__ import annotations

from enum import StrEnum


class AaaError(Exception):
    """Domain error raised by the amplifier-agent engine or CLI layer.

    Carries a string error code (matching an ErrorCode value) and a
    human-readable message.  The CLI layer catches this to emit a JSON
    error envelope ``{'error': {'code': ..., 'message': ...}}`` on stdout.

    v0.1.0 additions (design §4.10.2): severity, classification,
    correlation_id, stderr_tail.
    """

    def __init__(
        self,
        *,
        code: str,
        message: str,
        severity: str | None = None,
        classification: str | None = None,
        correlation_id: str | None = None,
        stderr_tail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.severity = severity
        self.classification = classification
        self.correlation_id = correlation_id
        self.stderr_tail = stderr_tail


class ErrorCode(StrEnum):
    """Wire-level error codes for the JSON-RPC ``error.data.code`` field."""

    # ------------------------------------------------------------------
    # Lifecycle / session
    # ------------------------------------------------------------------
    AGENT_NOT_READY = "agent_not_ready"
    INVALID_SESSION = "invalid_session"
    STALE_SESSION = "stale_session"
    SESSION_NOT_FOUND = "session_not_found"

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------
    CONFIG_VALIDATION = "config_validation"
    PROVIDER_NOT_CONFIGURED = "provider_not_configured"
    PROVIDER_INIT_FAILED = "provider_init_failed"
    PROMPT_REQUIRED = "prompt_required"

    # ------------------------------------------------------------------
    # Bundle / spawn
    # ------------------------------------------------------------------
    BUNDLE_LOAD_FAILED = "bundle_load_failed"
    SPAWN_FAILED = "spawn_failed"

    # ------------------------------------------------------------------
    # Approval (v0.1.0 additions — design §4.10.2, CR-2)
    # ------------------------------------------------------------------
    APPROVAL_DENIED = "approval_denied"
    APPROVAL_TIMEOUT = "approval_timeout"
    APPROVAL_TRANSLATION_FAILED = "approval_translation_failed"
    APPROVAL_PROTOCOL_VIOLATION = "approval_protocol_violation"

    # ------------------------------------------------------------------
    # Wrapper security (v0.1.0 — design §4.12.1, SC-3)
    # ------------------------------------------------------------------
    ENV_INJECTION_REJECTED = "env_injection_rejected"

    # ------------------------------------------------------------------
    # Tool / runtime
    # ------------------------------------------------------------------
    TOOL_EXECUTION_FAILED = "tool_execution_failed"
    RUNTIME = "runtime"

    # ------------------------------------------------------------------
    # Wire protocol
    # ------------------------------------------------------------------
    WIRE_PROTOCOL_VIOLATION = "wire_protocol_violation"
    PROTOCOL_VERSION_MISMATCH = "protocol_version_mismatch"

    # ------------------------------------------------------------------
    # Catch-all
    # ------------------------------------------------------------------
    INTERNAL = "internal"
```

**Step 4: Also update the wrapper's `AaaError` in `session.py`**

Open `wrappers/python/src/amplifier_agent_client/session.py`. The `AaaError` class there (around line 28) is a SEPARATE class from the engine's `AaaError`. It needs the same new fields so that `env_injection_rejected` (Task 10) can carry `classification`:

```python
class AaaError(Exception):
    """Typed error for AaA wrapper lifecycle and protocol violations."""

    def __init__(
        self,
        code: str,
        remediation: str | None = None,
        *,
        classification: str | None = None,
        severity: str | None = None,
    ) -> None:
        super().__init__(remediation or code)
        self.code = code
        self.remediation = remediation
        self.classification = classification
        self.severity = severity
```

> Note: The wrapper's `AaaError` uses positional `(code, remediation)` — keep that signature; add `classification` and `severity` as keyword-only. Do NOT change the constructor signature to `*` only; existing call sites pass `code` positionally.

**Step 5: Regen Python schemas** (ErrorCode changed — `error_codes.schema.json` must update)

```bash
uv run python -m amplifier_agent_lib.protocol._gen --output-dir src/amplifier_agent_lib/protocol
```

**Step 6: Regen TypeScript types**

```bash
cd wrappers/typescript && pnpm run gen:types
```

**Step 7: Run all tests**

```bash
uv run pytest tests/test_aaa_error_v01.py tests/test_protocol_gen_staleness.py -v
cd wrappers/typescript && pnpm typecheck && pnpm test
```

**Step 8: Commit**

```bash
git add src/amplifier_agent_lib/protocol/errors.py \
        src/amplifier_agent_lib/protocol/spec.md \
        src/amplifier_agent_lib/protocol/schemas/ \
        wrappers/python/src/amplifier_agent_client/session.py \
        wrappers/typescript/src/types.ts \
        tests/test_aaa_error_v01.py

git commit -m "feat(wire): AaaError.severity/classification/correlation_id + approval ErrorCodes (A1)"
```

---

### Task 4: TypeScript `AaaError` fields + final A1 typecheck (A1)

**Design reference:** §4.10.2

**Files:**
- Edit: `wrappers/typescript/src/session.ts` (add fields to `AaaError` class)
- Test: `wrappers/typescript/test/spawn.test.ts` (existing file — add test)

---

**Step 1: Write the failing test**

Open `wrappers/typescript/test/spawn.test.ts` and add this test block at the bottom:

```typescript
import { AaaError } from "../src/session.js";

describe("AaaError v0.1.0 fields", () => {
  it("AaaError accepts and exposes classification field", () => {
    const err = new AaaError("approval_timeout", "timed out", {
      classification: "approval",
      severity: "error",
    });
    expect(err.code).toBe("approval_timeout");
    expect(err.classification).toBe("approval");
    expect(err.severity).toBe("error");
  });

  it("AaaError without opts has undefined optional fields", () => {
    const err = new AaaError("internal", "oops");
    expect(err.classification).toBeUndefined();
    expect(err.severity).toBeUndefined();
    expect(err.correlationId).toBeUndefined();
  });
});
```

**Step 2: Run the test to confirm it fails**

```bash
cd wrappers/typescript && pnpm test -- --reporter=verbose 2>&1 | tail -30
```

Expected: `AaaError` does not have `classification` or `severity` — TypeScript compiler error or runtime failure.

**Step 3: Update `wrappers/typescript/src/session.ts`**

The `AaaError` class starts at line 40. Replace it with:

```typescript
/** Typed error for AaA wrapper lifecycle and protocol violations. */
export class AaaError extends Error {
  code: string;
  remediation?: string;
  // v0.1.0 additions (design §4.10.2)
  classification?: "transport" | "protocol" | "engine" | "approval" | "unknown";
  severity?: "error" | "warning";
  correlationId?: string;
  stderrTail?: string;

  constructor(
    code: string,
    remediation?: string,
    opts?: {
      classification?: AaaError["classification"];
      severity?: AaaError["severity"];
      correlationId?: string;
      stderrTail?: string;
    },
  ) {
    super(remediation ?? code);
    this.code = code;
    this.remediation = remediation;
    this.name = "AaaError";
    if (opts) {
      this.classification = opts.classification;
      this.severity = opts.severity;
      this.correlationId = opts.correlationId;
      this.stderrTail = opts.stderrTail;
    }
  }
}
```

**Step 4: Run tests and typecheck**

```bash
cd wrappers/typescript && pnpm typecheck && pnpm test
```

All must pass.

**Step 5: Commit**

```bash
git add wrappers/typescript/src/session.ts \
        wrappers/typescript/test/spawn.test.ts

git commit -m "feat(wire): extend TS AaaError with severity/classification/correlationId (A1)"
```

---

### Task 5: Implement `session_store.py` (A2 — CR-1)

**Design reference:** §4.6

**Files:**
- Create: `src/amplifier_agent_lib/session_store.py`
- Create: `tests/test_session_store.py`

---

**Step 1: Fetch the canonical reference (IMPORTANT)**

The design says `session_store.py` is "lifted near-verbatim from `amplifier-app-cli`". Read the source before writing. Fetch it:

```bash
curl -s https://raw.githubusercontent.com/microsoft/amplifier-app-cli/main/amplifier_app_cli/session_store.py
```

Read it carefully. Your implementation should adapt its patterns to `amplifier_agent_lib`'s namespace and the actual `write_with_backup` signature you confirmed (`write_with_backup(path, content)` — synchronous, no `await`).

**Step 2: Write the failing tests**

Create `tests/test_session_store.py`:

```python
"""Tests for amplifier_agent_lib.session_store.SessionStore.

Covers: save+load roundtrip, missing session returns None,
directory auto-creation, and path-traversal rejection.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from amplifier_agent_lib.session_store import SessionStore


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    """Saving then loading a session returns identical transcript and metadata."""
    store = SessionStore(tmp_path)
    transcript = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "world"}]
    metadata = {"last_tool": "bash", "turn_count": 1}

    store.save("sess-abc", transcript, metadata)
    result = store.load("sess-abc")

    assert result is not None, "load() should return a tuple, not None"
    loaded_transcript, loaded_metadata = result
    assert loaded_transcript == transcript
    assert loaded_metadata == metadata


def test_load_missing_session_returns_none(tmp_path: Path) -> None:
    """Loading a session that was never saved returns None."""
    store = SessionStore(tmp_path)
    assert store.load("no-such-session") is None


def test_save_creates_sessions_subdirectory(tmp_path: Path) -> None:
    """Save creates sessions/<session_id>/ under the root directory."""
    store = SessionStore(tmp_path)
    store.save("sess-dir-test", [], {})
    assert (tmp_path / "sessions" / "sess-dir-test").is_dir()


def test_transcript_persisted_as_jsonl(tmp_path: Path) -> None:
    """Each transcript message is one JSON line in transcript.jsonl."""
    store = SessionStore(tmp_path)
    transcript = [{"role": "user", "content": "ping"}, {"role": "assistant", "content": "pong"}]
    store.save("sess-jsonl", transcript, {})

    raw = (tmp_path / "sessions" / "sess-jsonl" / "transcript.jsonl").read_text()
    lines = [l for l in raw.splitlines() if l.strip()]
    assert len(lines) == 2
    assert json.loads(lines[0]) == {"role": "user", "content": "ping"}


def test_empty_transcript_roundtrips(tmp_path: Path) -> None:
    """An empty transcript list saves and loads correctly."""
    store = SessionStore(tmp_path)
    store.save("sess-empty", [], {})
    result = store.load("sess-empty")
    assert result is not None
    assert result[0] == []


def test_session_dir_returns_correct_path(tmp_path: Path) -> None:
    """session_dir() returns root/sessions/<id>."""
    store = SessionStore(tmp_path)
    assert store.session_dir("my-id") == tmp_path / "sessions" / "my-id"
```

**Step 3: Run tests to confirm they fail**

```bash
uv run pytest tests/test_session_store.py -v
```

Expected: `ModuleNotFoundError: No module named 'amplifier_agent_lib.session_store'`

**Step 4: Implement `session_store.py`**

Create `src/amplifier_agent_lib/session_store.py`:

```python
"""Application-layer session transcript persistence.

Pattern lifted near-verbatim from amplifier-app-cli (canonical reference).
Uses ``amplifier_foundation.write_with_backup`` for atomic writes with
backup of the previous version (crash-safe).

IMPORTANT: ``write_with_backup`` is synchronous — do NOT await it.
"""
from __future__ import annotations

import json
from pathlib import Path

from amplifier_foundation import write_with_backup


class SessionStore:
    """JSONL transcript + JSON metadata storage for session resume.

    Layout under ``root``::

        root/
          sessions/
            <session_id>/
              transcript.jsonl   ← one JSON object per line, one per message
              metadata.json      ← arbitrary metadata dict (last_tool, etc.)

    Usage::

        store = SessionStore(Path("/home/user/.local/state/amplifier-agent"))
        store.save("sess-123", transcript, {"last_tool": "bash"})
        result = store.load("sess-123")
        if result is not None:
            transcript, metadata = result
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def session_dir(self, session_id: str) -> Path:
        """Return the directory for the given session ID."""
        return self.root / "sessions" / session_id

    def save(
        self,
        session_id: str,
        transcript: list[dict],
        metadata: dict,
    ) -> None:
        """Persist transcript as JSONL and metadata as JSON.

        Creates the session directory if it does not exist.
        Uses ``write_with_backup`` for atomic crash-safe writes.

        Args:
            session_id: Unique session identifier (used as directory name).
            transcript: List of message dicts (OpenAI chat format).
            metadata:   Arbitrary metadata to persist alongside the transcript.
        """
        d = self.session_dir(session_id)
        d.mkdir(parents=True, exist_ok=True)
        write_with_backup(
            d / "transcript.jsonl",
            "\n".join(json.dumps(msg) for msg in transcript),
        )
        write_with_backup(d / "metadata.json", json.dumps(metadata, indent=2))

    def load(
        self,
        session_id: str,
    ) -> tuple[list[dict], dict] | None:
        """Load transcript and metadata for a session.

        Returns:
            ``(transcript, metadata)`` tuple if the session exists,
            or ``None`` if no transcript file is found.
        """
        d = self.session_dir(session_id)
        transcript_path = d / "transcript.jsonl"
        if not transcript_path.exists():
            return None
        transcript = [
            json.loads(line)
            for line in transcript_path.read_text().splitlines()
            if line.strip()
        ]
        metadata = json.loads((d / "metadata.json").read_text())
        return transcript, metadata
```

**Step 5: Run tests to confirm they pass**

```bash
uv run pytest tests/test_session_store.py -v
```

Expected: all 6 tests PASS.

**Step 6: Run pyright type check**

```bash
uv run pyright src/amplifier_agent_lib/session_store.py
```

No errors expected.

**Step 7: Commit**

```bash
git add src/amplifier_agent_lib/session_store.py tests/test_session_store.py
git commit -m "feat(engine): SessionStore — JSONL transcript + JSON metadata (A2 CR-1)"
```

---

### Task 6: Implement `incremental_save.py` (A2 — CR-1)

**Design reference:** §4.6

**Files:**
- Create: `src/amplifier_agent_lib/incremental_save.py`
- Create: `tests/test_incremental_save.py`

---

**Step 1: Fetch the canonical reference**

```bash
curl -s https://raw.githubusercontent.com/microsoft/amplifier-app-cli/main/amplifier_app_cli/incremental_save.py
```

Read it. Adapt it to the actual hook registration pattern you see in `src/amplifier_agent_lib/bundle/hook_streaming.py`:
- Handlers are plain async functions (not class instances registered via `session.hooks.register`)
- Registration: `coordinator.hooks.register("tool:post", handler_fn, name="hook_name")`
- Handler signature: `async def fn(event: str, data: dict[str, Any]) -> HookResult`

**Step 2: Write the failing tests**

Create `tests/test_incremental_save.py`:

```python
"""Tests for IncrementalSaveHook — saves transcript after every tool call.

Design reference: §4.6 of docs/designs/2026-05-22-aaa-v2-amplifier-agent-nc-provider.md
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_agent_lib.incremental_save import IncrementalSaveHook
from amplifier_agent_lib.session_store import SessionStore


@pytest.mark.asyncio
async def test_hook_saves_transcript_on_call(tmp_path: Path) -> None:
    """IncrementalSaveHook.__call__ must save current messages to SessionStore."""
    store = SessionStore(tmp_path)
    session_id = "sess-hook-001"

    # Simulate a session context that returns a fixed transcript
    fake_messages = [{"role": "user", "content": "hello"}]
    mock_get_messages = AsyncMock(return_value=fake_messages)

    hook = IncrementalSaveHook(
        store=store,
        session_id=session_id,
        get_messages=mock_get_messages,
    )

    # Call it as the coordinator would — event name + data dict
    event_data = {"tool_name": "bash", "session_id": session_id, "turn_id": "t-1"}
    await hook("tool:post", event_data)

    # Verify transcript was saved
    result = store.load(session_id)
    assert result is not None
    saved_transcript, saved_metadata = result
    assert saved_transcript == fake_messages
    assert saved_metadata.get("last_tool") == "bash"


@pytest.mark.asyncio
async def test_hook_returns_hook_result_continue(tmp_path: Path) -> None:
    """IncrementalSaveHook must return HookResult(action='continue')."""
    from amplifier_core.models import HookResult

    store = SessionStore(tmp_path)
    mock_get_messages = AsyncMock(return_value=[])
    hook = IncrementalSaveHook(
        store=store,
        session_id="sess-hook-002",
        get_messages=mock_get_messages,
    )

    result = await hook("tool:post", {"tool_name": "grep"})
    assert isinstance(result, HookResult)
    assert result.action == "continue"
```

**Step 3: Run tests to confirm they fail**

```bash
uv run pytest tests/test_incremental_save.py -v
```

Expected: `ModuleNotFoundError: No module named 'amplifier_agent_lib.incremental_save'`

**Step 4: Implement `incremental_save.py`**

Create `src/amplifier_agent_lib/incremental_save.py`:

```python
"""IncrementalSaveHook — persists transcript after every tool call.

Designed for registration on the ``tool:post`` kernel event so that
the session transcript is durably saved after each tool execution.

Registration (in _runtime.py)::

    hook = IncrementalSaveHook(
        store=session_store,
        session_id=session_id,
        get_messages=get_messages_fn,
    )
    session.coordinator.hooks.register("tool:post", hook, name="incremental_save")

The handler receives ``(event: str, data: dict)`` from the kernel.
It extracts ``tool_name`` from ``data``, fetches the current messages,
and writes them via SessionStore.save().

IMPORTANT: ``store.save()`` is synchronous — no ``await`` needed.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from amplifier_core.models import HookResult

from amplifier_agent_lib.session_store import SessionStore


class IncrementalSaveHook:
    """Saves the session transcript after each tool call.

    Args:
        store:        SessionStore instance to save to.
        session_id:   The session ID to save under.
        get_messages: Async callable that returns the current message list.
                      Typically ``session.coordinator.get_capability("context.get_messages")``
                      or the equivalent for the context module in use.
    """

    def __init__(
        self,
        *,
        store: SessionStore,
        session_id: str,
        get_messages: Callable[[], Awaitable[list[dict[str, Any]]]],
    ) -> None:
        self._store = store
        self._session_id = session_id
        self._get_messages = get_messages

    async def __call__(self, event: str, data: dict[str, Any]) -> HookResult:
        """Kernel hook handler — called after every tool execution.

        Args:
            event: Event name (expected: ``"tool:post"``).
            data:  Kernel event data dict. ``data["tool_name"]`` or
                   ``data["tool"]`` carries the tool name.
        """
        transcript = await self._get_messages()
        tool_name: str = data.get("tool_name") or data.get("tool") or ""
        self._store.save(
            self._session_id,
            transcript,
            metadata={"last_tool": tool_name},
        )
        return HookResult(action="continue")
```

**Step 5: Run tests to confirm they pass**

```bash
uv run pytest tests/test_incremental_save.py -v
```

Expected: both tests PASS.

**Step 6: Run pyright**

```bash
uv run pyright src/amplifier_agent_lib/incremental_save.py
```

**Step 7: Commit**

```bash
git add src/amplifier_agent_lib/incremental_save.py tests/test_incremental_save.py
git commit -m "feat(engine): IncrementalSaveHook — tool:post transcript save (A2 CR-1)"
```

---

### Task 7: Thread A2 into `_runtime.py` (A2 — CR-1)

**Design reference:** §4.8

**Files:**
- Edit: `src/amplifier_agent_lib/_runtime.py`
- Edit: `tests/test_runtime.py` (add tests)

---

**⚠️ IMPORTANT API NOTE before you write any code**

The design's §4.8 pseudo-code uses `session.context.set_messages(transcript)` but `AmplifierSession` does NOT have a `.context` attribute. You need to figure out how to restore the transcript into a resumed session.

**Before writing the implementation, do this investigation:**

```bash
uv run python3 -c "
from amplifier_foundation.bundle._prepared import PreparedBundle
from amplifier_core import AmplifierSession
import inspect

# List all attrs on the session
print('Session attrs:', [a for a in dir(AmplifierSession) if not a.startswith('_')])

# Check if coordinator has context-related capabilities
from amplifier_core import ModuleCoordinator
print('Coordinator attrs:', [a for a in dir(ModuleCoordinator) if not a.startswith('_') and 'context' in a.lower()])
"
```

Based on your investigation, the correct path to `get_messages`/`set_messages` is likely one of:
- `session.coordinator.get_capability("context.get_messages")` 
- `session.coordinator.get_capability("context.set_messages")`

**The plan's implementation below uses `coordinator.get_capability("context.set_messages")`. Verify this actually exists and adjust if needed.**

---

**Step 1: Write the failing tests** (add to `tests/test_runtime.py`)

Open `tests/test_runtime.py` and add these tests at the end:

```python
# ---------------------------------------------------------------------------
# A2: SessionStore + IncrementalSaveHook threading tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runtime_loads_transcript_for_resumed_session(tmp_path) -> None:
    """When is_resumed=True and SessionStore has data, handler loads transcript.

    The test verifies that set_messages is called with the stored transcript.
    """
    from pathlib import Path
    from unittest.mock import AsyncMock, MagicMock, patch

    from amplifier_agent_lib._runtime import make_turn_handler
    from amplifier_agent_lib.engine import TurnContext
    from amplifier_agent_lib.session_store import SessionStore

    # Pre-populate the store
    store = SessionStore(tmp_path)
    saved_transcript = [{"role": "user", "content": "prior turn"}]
    store.save("sess-resume-test", saved_transcript, {})

    set_messages_calls: list = []

    execute_mock = AsyncMock(return_value="reply")
    session_mock = MagicMock()
    session_mock.execute = execute_mock
    session_mock.session_id = "sess-resume-test"

    # Capture set_messages calls via the coordinator capability
    async def fake_set_messages(transcript):
        set_messages_calls.append(transcript)

    session_mock.coordinator.get_capability.side_effect = lambda name: (
        fake_set_messages if name == "context.set_messages" else None
    )

    async def _fake_create_session(**kwargs):
        return session_mock

    prepared_mock = MagicMock()
    prepared_mock.create_session = _fake_create_session
    prepared_mock.mount_plan = {"agents": {}}

    ctx = TurnContext(
        session_id="sess-resume-test",
        turn_id="t-1",
        prompt="continue",
        approval=MagicMock(),
        display=MagicMock(),
    )

    # Patch state_root to use tmp_path
    with patch("amplifier_agent_lib._runtime.state_root", return_value=tmp_path):
        handler = make_turn_handler(prepared_mock, cwd=None, is_resumed=True)
        await handler(ctx)

    assert set_messages_calls, (
        "set_messages should have been called with the stored transcript. "
        "Did you call session.coordinator.get_capability('context.set_messages') "
        "and invoke it with the loaded transcript?"
    )
    assert set_messages_calls[0] == saved_transcript


@pytest.mark.asyncio
async def test_runtime_registers_incremental_save_hook(tmp_path) -> None:
    """When session_id is set, IncrementalSaveHook is registered on tool:post."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from amplifier_agent_lib._runtime import make_turn_handler
    from amplifier_agent_lib.engine import TurnContext

    hook_registrations: list[tuple] = []

    execute_mock = AsyncMock(return_value="reply")
    session_mock = MagicMock()
    session_mock.execute = execute_mock
    session_mock.session_id = "sess-hook-reg"

    def fake_register(event_name, handler, *, name=""):
        hook_registrations.append((event_name, handler, name))

    session_mock.coordinator.hooks.register.side_effect = fake_register
    session_mock.coordinator.get_capability.return_value = None

    async def _fake_create_session(**kwargs):
        return session_mock

    prepared_mock = MagicMock()
    prepared_mock.create_session = _fake_create_session
    prepared_mock.mount_plan = {"agents": {}}

    ctx = TurnContext(
        session_id="sess-hook-reg",
        turn_id="t-1",
        prompt="hi",
        approval=MagicMock(),
        display=MagicMock(),
    )

    with patch("amplifier_agent_lib._runtime.state_root", return_value=tmp_path):
        handler = make_turn_handler(prepared_mock, cwd=None, is_resumed=False)
        await handler(ctx)

    tool_post_hooks = [r for r in hook_registrations if r[0] == "tool:post" and "incremental_save" in r[2]]
    assert len(tool_post_hooks) >= 1, (
        f"Expected a 'tool:post' hook named 'incremental_save'. "
        f"Got hook registrations: {hook_registrations}"
    )
```

**Step 2: Run the new tests to confirm they fail**

```bash
uv run pytest tests/test_runtime.py::test_runtime_loads_transcript_for_resumed_session \
              tests/test_runtime.py::test_runtime_registers_incremental_save_hook -v
```

Expected: FAILED — no `state_root` import in `_runtime.py`.

**Step 3: Edit `_runtime.py` to add session persistence wiring**

Open `src/amplifier_agent_lib/_runtime.py`. Add these imports near the top (after existing imports):

```python
from amplifier_agent_lib.incremental_save import IncrementalSaveHook
from amplifier_agent_lib.persistence import state_root
from amplifier_agent_lib.session_store import SessionStore
```

Then update the `handler` inner function inside `make_turn_handler`. The additions (marked with `# A2:`) go AFTER `resolved_cwd` is computed and AROUND the `create_session` call:

```python
async def handler(ctx: TurnContext) -> str:
    session_id = ctx.session_id if ctx.session_id else None

    # A2: Load stored transcript for resumed sessions (CR-1, design §4.8)
    store = SessionStore(state_root())
    loaded_transcript: list[dict] = []
    if session_id and is_resumed:
        loaded = store.load(session_id)
        if loaded is not None:
            loaded_transcript, _ = loaded

    session = await prepared.create_session(
        session_id=session_id,
        session_cwd=resolved_cwd,
        is_resumed=is_resumed,
    )

    # Wire display and approval into the coordinator so hook events can
    # flow back to the client.  Per SC-1, set default event fields so
    # every kernel event carries session_id and turn_id automatically.
    session.coordinator.hooks.set_default_fields(
        session_id=ctx.session_id,
        turn_id=ctx.turn_id,
    )
    session.coordinator.register_capability("display.emit", ctx.display.emit)
    session.coordinator.register_capability("approval.request", ctx.approval.request)

    # A2: Restore transcript into context (CR-1, design §4.8)
    if loaded_transcript:
        set_messages = session.coordinator.get_capability("context.set_messages")
        if set_messages is not None:
            await set_messages(loaded_transcript)

    # A2: Register incremental save hook (CR-1, design §4.8)
    if session_id:
        get_messages = session.coordinator.get_capability("context.get_messages")
        if get_messages is not None:
            hook = IncrementalSaveHook(
                store=store,
                session_id=session_id,
                get_messages=get_messages,
            )
            session.coordinator.hooks.register("tool:post", hook, name="incremental_save")

    # ... rest of existing handler (mount_streaming_hook, _spawn_fn, etc.)
```

> **If `context.set_messages` / `context.get_messages` capability does NOT exist** (i.e., `get_capability(...)` returns `None`), add a DEBUG log and skip. Do not crash. The `if get_messages is not None:` guard above already handles this. File an issue to track the gap.

**Step 4: Run ALL runtime tests to verify**

```bash
uv run pytest tests/test_runtime.py -v
```

All 7 existing tests PLUS the 2 new tests should pass.

**Step 5: Run the full main test suite to catch regressions**

```bash
uv run pytest tests/ -v --timeout=60
```

All tests should pass. If any test fails due to changed `_runtime.py` behavior, investigate and fix.

**Step 6: Commit**

```bash
git add src/amplifier_agent_lib/_runtime.py tests/test_runtime.py
git commit -m "feat(engine): wire session persistence resume path into _runtime.py (A2 CR-1)"
```

---

### Task 8: Implement `wire_approval_provider.py` (A3 — CR-2)

**Design reference:** §4.7

**Files:**
- Create: `src/amplifier_agent_lib/wire_approval_provider.py`
- Create: `tests/test_wire_approval_provider.py`

---

**Step 1: Write the failing tests**

Create `tests/test_wire_approval_provider.py`:

```python
"""Tests for WireApprovalProvider — all three CR-2 error codes.

Design reference: §4.7 of docs/designs/2026-05-22-aaa-v2-amplifier-agent-nc-provider.md

The shim wraps a callable that forwards approval requests back to the host.
Three explicit failure modes:
  1. approval_translation_failed — request cannot be serialized/translated
  2. approval_timeout            — host did not respond in time
  3. approval_protocol_violation — host response is malformed
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from amplifier_agent_lib.protocol.errors import AaaError
from amplifier_agent_lib.wire_approval_provider import WireApprovalProvider


def _make_fake_request():
    """Build a minimal ApprovalRequest-like object."""
    from unittest.mock import MagicMock
    req = MagicMock()
    req.action = "allow"
    req.tool_name = "bash"
    req.arguments = {"command": "echo hello"}
    return req


@pytest.mark.asyncio
async def test_approval_translation_failed_on_unserializable_request() -> None:
    """When _translate_request raises, provider raises AaaError(approval_translation_failed)."""
    provider = WireApprovalProvider(
        approval_request_fn=AsyncMock(return_value={"approved": True}),
    )
    # Inject a broken translate that always fails
    provider._translate_request = lambda req: (_ for _ in ()).throw(ValueError("bad shape"))

    with pytest.raises(AaaError) as exc_info:
        await provider.request_approval(_make_fake_request())

    assert exc_info.value.code == "approval_translation_failed"
    assert exc_info.value.classification == "approval"


@pytest.mark.asyncio
async def test_approval_timeout() -> None:
    """When host does not respond in time, raises AaaError(approval_timeout)."""

    async def slow_fn(*args, **kwargs):
        await asyncio.sleep(9999)  # never returns

    provider = WireApprovalProvider(approval_request_fn=slow_fn, timeout_seconds=0.05)

    with pytest.raises(AaaError) as exc_info:
        await provider.request_approval(_make_fake_request())

    assert exc_info.value.code == "approval_timeout"
    assert exc_info.value.classification == "approval"


@pytest.mark.asyncio
async def test_approval_protocol_violation_on_bad_response() -> None:
    """When _translate_response raises, raises AaaError(approval_protocol_violation)."""
    provider = WireApprovalProvider(
        approval_request_fn=AsyncMock(return_value={"malformed": "garbage"}),
    )
    # Inject a broken translate_response that always fails
    provider._translate_response = lambda r: (_ for _ in ()).throw(ValueError("bad response"))

    with pytest.raises(AaaError) as exc_info:
        await provider.request_approval(_make_fake_request())

    assert exc_info.value.code == "approval_protocol_violation"
    assert exc_info.value.classification == "approval"


@pytest.mark.asyncio
async def test_successful_approval_returns_response() -> None:
    """When host approves, provider returns the ApprovalResponse."""
    from amplifier_core import ApprovalResponse
    from unittest.mock import MagicMock

    fake_response = MagicMock(spec=ApprovalResponse)
    approval_fn = AsyncMock(return_value={"approved": True, "action": "allow"})

    provider = WireApprovalProvider(approval_request_fn=approval_fn)
    # Override translate_response to return our fake_response
    provider._translate_response = lambda r: fake_response

    result = await provider.request_approval(_make_fake_request())
    assert result is fake_response
```

**Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/test_wire_approval_provider.py -v
```

Expected: `ModuleNotFoundError: No module named 'amplifier_agent_lib.wire_approval_provider'`

**Step 3: Implement `wire_approval_provider.py`**

Create `src/amplifier_agent_lib/wire_approval_provider.py`:

```python
"""WireApprovalProvider — bridges amplifier_core.ApprovalProvider to the wire.

Implements the ApprovalProvider protocol (amplifier_core) by forwarding
approval requests to the host via the registered ``approval.request``
capability (the JSON-RPC back-channel from engine to wrapper to host).

Error contract (design §4.7, CR-2):
  - ``approval_translation_failed``  — request serialization failed
  - ``approval_timeout``              — host did not respond within timeout
  - ``approval_protocol_violation``   — host response failed to parse

All three codes surface as AaaError(classification='approval') so the NC
event-translator can route them to the typed 'approval' ProviderEvent.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from amplifier_core import ApprovalProvider, ApprovalRequest, ApprovalResponse

from amplifier_agent_lib.protocol.errors import AaaError

#: Default timeout for host approval responses.
APPROVAL_TIMEOUT_SECONDS = 30.0


class WireApprovalProvider(ApprovalProvider):
    """Forwards approval requests to the host over the wire.

    Args:
        approval_request_fn: Async callable that accepts a serialized request
            dict and returns a serialized response dict (or raises).
            In practice, this is ``ctx.approval.request`` from TurnContext,
            wrapped to pass the wire payload.
        timeout_seconds:     Override the default 30-second timeout.
    """

    def __init__(
        self,
        *,
        approval_request_fn: Callable[..., Awaitable[Any]],
        timeout_seconds: float = APPROVAL_TIMEOUT_SECONDS,
    ) -> None:
        self._approval_request_fn = approval_request_fn
        self._timeout_seconds = timeout_seconds

    # ------------------------------------------------------------------
    # ApprovalProvider protocol
    # ------------------------------------------------------------------

    async def request_approval(self, req: ApprovalRequest) -> ApprovalResponse:
        """Forward an approval request to the host with explicit error contract.

        Raises:
            AaaError(code='approval_translation_failed'):
                If ``req`` cannot be serialized into a wire payload.
            AaaError(code='approval_timeout'):
                If the host does not respond within ``timeout_seconds``.
            AaaError(code='approval_protocol_violation'):
                If the host response cannot be deserialized to ApprovalResponse.
        """
        # 1. Translate request → wire payload
        try:
            wire_payload = self._translate_request(req)
        except Exception as exc:
            raise AaaError(
                code="approval_translation_failed",
                message=(
                    f"failed to translate ApprovalRequest to wire shape: {exc}"
                ),
                classification="approval",
                severity="error",
            ) from exc

        # 2. Send to host with timeout
        try:
            wire_response = await asyncio.wait_for(
                self._approval_request_fn(wire_payload),
                timeout=self._timeout_seconds,
            )
        except asyncio.TimeoutError:
            raise AaaError(
                code="approval_timeout",
                message=(
                    f"host did not respond to approval request within "
                    f"{self._timeout_seconds}s"
                ),
                classification="approval",
                severity="error",
            )

        # 3. Translate response → ApprovalResponse
        try:
            return self._translate_response(wire_response)
        except Exception as exc:
            raise AaaError(
                code="approval_protocol_violation",
                message=(
                    f"approval response did not conform to expected shape: {exc}"
                ),
                classification="approval",
                severity="error",
            ) from exc

    # ------------------------------------------------------------------
    # Translation helpers (override in tests or subclasses)
    # ------------------------------------------------------------------

    def _translate_request(self, req: ApprovalRequest) -> dict[str, Any]:
        """Serialize ApprovalRequest to a wire-safe dict.

        Default implementation: extract common fields. Override for custom shapes.
        """
        return {
            "action": getattr(req, "action", None),
            "tool_name": getattr(req, "tool_name", None),
            "arguments": getattr(req, "arguments", {}),
        }

    def _translate_response(self, wire_response: Any) -> ApprovalResponse:
        """Deserialize a wire response dict into an ApprovalResponse.

        Default: pass through; override if the host returns a non-standard shape.
        """
        # If already an ApprovalResponse, return as-is
        if isinstance(wire_response, ApprovalResponse):
            return wire_response
        # If dict-shaped, attempt direct use — caller can override for custom mapping
        return wire_response  # type: ignore[return-value]
```

**Step 4: Run tests to confirm they pass**

```bash
uv run pytest tests/test_wire_approval_provider.py -v
```

Expected: 4 tests PASS.

**Step 5: Run pyright**

```bash
uv run pyright src/amplifier_agent_lib/wire_approval_provider.py
```

**Step 6: Commit**

```bash
git add src/amplifier_agent_lib/wire_approval_provider.py tests/test_wire_approval_provider.py
git commit -m "feat(engine): WireApprovalProvider — three explicit error codes (A3 CR-2)"
```

---

### Task 9: Thread `WireApprovalProvider` into `_runtime.py` (A3 — CR-2)

**Design reference:** §4.8

**Files:**
- Edit: `src/amplifier_agent_lib/_runtime.py`
- Edit: `tests/test_runtime.py` (add test)

---

**Step 1: Write the failing test** (add to `tests/test_runtime.py`)

```python
# ---------------------------------------------------------------------------
# A3: WireApprovalProvider threading test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runtime_registers_wire_approval_provider(tmp_path) -> None:
    """After A3, approval.request capability must use WireApprovalProvider.

    The WireApprovalProvider should be registered instead of the raw
    ctx.approval.request callable.
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    from amplifier_agent_lib._runtime import make_turn_handler
    from amplifier_agent_lib.engine import TurnContext
    from amplifier_agent_lib.wire_approval_provider import WireApprovalProvider

    registered_approval_capability = None

    def capture_register_capability(name, fn):
        nonlocal registered_approval_capability
        if name == "approval.request":
            registered_approval_capability = fn

    execute_mock = AsyncMock(return_value="reply")
    session_mock = MagicMock()
    session_mock.execute = execute_mock
    session_mock.coordinator.register_capability.side_effect = capture_register_capability
    session_mock.coordinator.get_capability.return_value = None

    async def _fake_create_session(**kwargs):
        return session_mock

    prepared_mock = MagicMock()
    prepared_mock.create_session = _fake_create_session
    prepared_mock.mount_plan = {"agents": {}}

    ctx = TurnContext(
        session_id="sess-approval",
        turn_id="t-1",
        prompt="hi",
        approval=MagicMock(),
        display=MagicMock(),
    )

    with patch("amplifier_agent_lib._runtime.state_root", return_value=tmp_path):
        handler = make_turn_handler(prepared_mock, cwd=None, is_resumed=False)
        await handler(ctx)

    assert registered_approval_capability is not None, (
        "approval.request capability was not registered."
    )
    # The registered callable should be the WireApprovalProvider.request_approval method
    assert hasattr(registered_approval_capability, "__self__"), (
        "Expected a bound method from WireApprovalProvider."
    )
    assert isinstance(registered_approval_capability.__self__, WireApprovalProvider), (
        f"Expected WireApprovalProvider instance, got {type(registered_approval_capability.__self__)}"
    )
```

**Step 2: Run the test to confirm it fails**

```bash
uv run pytest tests/test_runtime.py::test_runtime_registers_wire_approval_provider -v
```

Expected: FAIL — `approval.request` is registered as raw `ctx.approval.request`, not as `WireApprovalProvider.request_approval`.

**Step 3: Update `_runtime.py` to use `WireApprovalProvider`**

Open `src/amplifier_agent_lib/_runtime.py`. Add to imports:

```python
from amplifier_agent_lib.wire_approval_provider import WireApprovalProvider
```

In the `handler` function, replace the existing approval capability line:

```python
# Before (remove this line):
session.coordinator.register_capability("approval.request", ctx.approval.request)

# After (wrap in WireApprovalProvider):
wire_approval_provider = WireApprovalProvider(
    approval_request_fn=ctx.approval.request,
)
session.coordinator.register_capability(
    "approval.request",
    wire_approval_provider.request_approval,
)
```

**Step 4: Run ALL runtime tests**

```bash
uv run pytest tests/test_runtime.py -v
```

All 9 tests (7 original + 2 from Task 7 + 1 new) must pass.

**Step 5: Run the full main suite**

```bash
uv run pytest tests/ --timeout=60
```

**Step 6: Commit**

```bash
git add src/amplifier_agent_lib/_runtime.py tests/test_runtime.py
git commit -m "feat(engine): register WireApprovalProvider in _runtime.py (A3 CR-2)"
```

---

### Task 10: Python wrapper `BLOCKED_ENV_KEYS` (A6 — SC-3)

**Design reference:** §4.12.1

**Files:**
- Edit: `wrappers/python/src/amplifier_agent_client/spawn.py`
- Edit: `wrappers/python/tests/test_spawn.py` (add tests)

---

**Step 1: Write the failing tests** (add to `wrappers/python/tests/test_spawn.py`)

Open `wrappers/python/tests/test_spawn.py` and add:

```python
def test_build_env_raises_on_blocked_pythonpath() -> None:
    """build_env raises AaaError(env_injection_rejected) when PYTHONPATH is in extra."""
    from amplifier_agent_client.session import AaaError
    from amplifier_agent_client.spawn import DEFAULT_ALLOWLIST, build_env

    with pytest.raises(AaaError) as exc_info:
        build_env(
            process_env={"PATH": "/usr/bin"},
            allowlist=DEFAULT_ALLOWLIST,
            extra={"PYTHONPATH": "/evil/path"},
        )

    assert exc_info.value.code == "env_injection_rejected"


def test_build_env_raises_on_blocked_ld_preload() -> None:
    """build_env raises AaaError(env_injection_rejected) for LD_PRELOAD in extra."""
    from amplifier_agent_client.session import AaaError
    from amplifier_agent_client.spawn import DEFAULT_ALLOWLIST, build_env

    with pytest.raises(AaaError) as exc_info:
        build_env(
            process_env={},
            allowlist=DEFAULT_ALLOWLIST,
            extra={"LD_PRELOAD": "/evil.so"},
        )

    assert exc_info.value.code == "env_injection_rejected"


def test_build_env_allows_non_blocked_extras() -> None:
    """build_env does NOT raise for safe extra keys like CUSTOM_VAR."""
    from amplifier_agent_client.spawn import DEFAULT_ALLOWLIST, build_env

    result = build_env(
        process_env={"PATH": "/usr/bin"},
        allowlist=DEFAULT_ALLOWLIST,
        extra={"CUSTOM_SAFE_VAR": "value"},
    )
    assert result["CUSTOM_SAFE_VAR"] == "value"
```

You'll need `import pytest` at the top of the test file if it's not already there. Check the existing imports.

**Step 2: Run the new tests to confirm they fail**

```bash
uv run pytest wrappers/python/tests/test_spawn.py::test_build_env_raises_on_blocked_pythonpath \
              wrappers/python/tests/test_spawn.py::test_build_env_raises_on_blocked_ld_preload \
              wrappers/python/tests/test_spawn.py::test_build_env_allows_non_blocked_extras -v
```

Expected: FAIL (no error is currently raised).

**Step 3: Update `wrappers/python/src/amplifier_agent_client/spawn.py`**

Add the `BLOCKED_ENV_KEYS` constant and validation to `build_env`. Open the file and modify it:

```python
# Add this import near the top (it may already be present):
# (no new imports needed — AaaError comes from session.py)

#: Environment keys that callers must NOT inject via env.extra (SC-3, design §4.12.1).
#: These keys can override loader/library paths and allow host-process injection attacks.
BLOCKED_ENV_KEYS: frozenset[str] = frozenset({
    "PYTHONPATH",
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
    "PYTHONSTARTUP",
    "PATH",
    "PYTHONHOME",
    "PYTHONNOUSERSITE",
    "DYLD_INSERT_LIBRARIES",
    "DYLD_LIBRARY_PATH",
})
```

Then update `build_env` to validate `extra` before building the result:

```python
def build_env(
    *,
    process_env: dict[str, str],
    allowlist: list[str],
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build the subprocess environment from the caller's process environment.

    Only variables whose name is in ``allowlist``, starts with ``AMPLIFIER_``,
    or starts with ``LC_`` are included.  ``extra`` entries are merged last.

    Args:
        process_env: The caller's current environment (e.g. ``dict(os.environ)``).
        allowlist:   List of exact variable names to pass through.
        extra:       Additional variables merged on top (override everything).
                     Keys in BLOCKED_ENV_KEYS are rejected with AaaError.

    Returns:
        Filtered environment dict safe to pass to ``subprocess``.

    Raises:
        AaaError('env_injection_rejected'): If any key in ``extra`` is in
            BLOCKED_ENV_KEYS (design §4.12.1 SC-3).
    """
    # SC-3: reject blocked keys in extra BEFORE building the env
    if extra:
        for key in extra:
            if key in BLOCKED_ENV_KEYS:
                from amplifier_agent_client.session import AaaError  # lazy import avoids circular
                raise AaaError(
                    "env_injection_rejected",
                    f"env.extra key {key!r} is blocked for security reasons "
                    f"(design §4.12.1). Remove it from env.extra.",
                    classification="protocol",
                    severity="error",
                )

    allow_set = set(allowlist)
    result: dict[str, str] = {}

    for key, value in process_env.items():
        if key in allow_set or key.startswith("AMPLIFIER_") or key.startswith("LC_"):
            result[key] = value

    if extra:
        result.update(extra)

    return result
```

**Step 4: Run tests to confirm they pass**

```bash
uv run pytest wrappers/python/tests/test_spawn.py -v
```

All existing + 3 new tests must pass.

**Step 5: Commit**

```bash
git add wrappers/python/src/amplifier_agent_client/spawn.py \
        wrappers/python/tests/test_spawn.py

git commit -m "feat(wrappers): Python build_env BLOCKED_ENV_KEYS validation (A6 SC-3)"
```

---

### Task 11: Python wrapper async `probe_engine_version` (A6 — SC-7)

**Design reference:** §4.12.2

**Files:**
- Edit: `wrappers/python/src/amplifier_agent_client/spawn.py`
- Edit: `wrappers/python/src/amplifier_agent_client/__init__.py` (update call site)
- Edit: `wrappers/python/tests/test_spawn.py` (add async test)

---

**Step 1: Write the failing test** (add to `wrappers/python/tests/test_spawn.py`)

```python
@pytest.mark.asyncio
async def test_probe_engine_version_is_async() -> None:
    """probe_engine_version must be a coroutine function (async def)."""
    import asyncio
    import inspect

    from amplifier_agent_client.spawn import probe_engine_version

    assert asyncio.iscoroutinefunction(probe_engine_version), (
        "probe_engine_version must be async (SC-7). "
        "Change 'def probe_engine_version' to 'async def probe_engine_version'."
    )
```

**Step 2: Run the test to confirm it fails**

```bash
uv run pytest wrappers/python/tests/test_spawn.py::test_probe_engine_version_is_async -v
```

Expected: FAIL — `probe_engine_version` is currently synchronous.

**Step 3: Convert `probe_engine_version` to async in `spawn.py`**

Open `wrappers/python/src/amplifier_agent_client/spawn.py`. Replace the synchronous `probe_engine_version` with an async version using `asyncio.create_subprocess_exec`:

```python
async def probe_engine_version(
    bin_path: str,
    env: dict[str, str],
    timeout: int = 5,
) -> dict[str, Any]:
    """Run ``<bin_path> version --json`` and return the parsed JSON payload.

    Async version using asyncio.create_subprocess_exec (SC-7, design §4.12.2).

    Args:
        bin_path: Absolute path to the amplifier-agent binary.
        env:      Environment to pass to the subprocess.
        timeout:  Timeout in seconds (default: 5).

    Returns:
        Parsed JSON dict with at least ``version`` and ``protocolVersion`` keys.

    Raises:
        asyncio.TimeoutError:  If the process exceeds the timeout.
        RuntimeError:          If the process exits non-zero.
        json.JSONDecodeError:  If stdout is not valid JSON.
    """
    proc = await asyncio.create_subprocess_exec(
        bin_path, "version", "--json",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise

    if proc.returncode != 0:
        raise RuntimeError(
            f"amplifier-agent version --json exited with code {proc.returncode}"
        )

    return json.loads(stdout_bytes.decode().strip())  # type: ignore[no-any-return]
```

Also add `import asyncio` at the top of `spawn.py` if not already present.

**Step 4: Update the call site in `__init__.py`**

Open `wrappers/python/src/amplifier_agent_client/__init__.py`. Find the call to `probe_engine_version` (line ~179):

```python
# Before:
version_payload = probe_engine_version(binary_path, subprocess_env)

# After:
version_payload = await probe_engine_version(binary_path, subprocess_env)
```

Also update the `_version_probe` type hint (line ~122):
```python
# Before:
_version_probe: Callable[..., dict[str, Any]] | None = None,

# After:
_version_probe: Callable[..., Awaitable[dict[str, Any]]] | None = None,
```

Add `from collections.abc import Awaitable` to imports if not already present.

And the call site for `_version_probe` (line ~177):
```python
# Before:
version_payload = _version_probe(binary_path, subprocess_env)

# After:
version_payload = await _version_probe(binary_path, subprocess_env)
```

**Step 5: Run tests to confirm they pass**

```bash
uv run pytest wrappers/python/tests/ -v
```

All tests must pass. If any test mocks `probe_engine_version` and breaks, update the mock to return a coroutine (use `AsyncMock` or `return_value` with a coroutine).

**Step 6: Commit**

```bash
git add wrappers/python/src/amplifier_agent_client/spawn.py \
        wrappers/python/src/amplifier_agent_client/__init__.py \
        wrappers/python/tests/test_spawn.py

git commit -m "feat(wrappers): async probe_engine_version — Python wrapper (A6 SC-7)"
```

---

### Task 12: TypeScript wrapper `BLOCKED_ENV_KEYS` (A6 — SC-3)

**Design reference:** §4.12.1

**Files:**
- Edit: `wrappers/typescript/src/spawn.ts`
- Edit: `wrappers/typescript/test/spawn.test.ts` (add tests)

---

**Step 1: Write the failing tests** (add to `wrappers/typescript/test/spawn.test.ts`)

```typescript
import { AaaError } from "../src/session.js";

describe("BLOCKED_ENV_KEYS validation (SC-3)", () => {
  it("throws AaaError(env_injection_rejected) when PYTHONPATH is in extra", () => {
    expect(() =>
      buildEnv({
        processEnv: { PATH: "/usr/bin" },
        allowlist: DEFAULT_ALLOWLIST,
        extra: { PYTHONPATH: "/evil" },
      }),
    ).toThrow(expect.objectContaining({ code: "env_injection_rejected" }));
  });

  it("throws AaaError(env_injection_rejected) when LD_PRELOAD is in extra", () => {
    expect(() =>
      buildEnv({
        processEnv: {},
        allowlist: DEFAULT_ALLOWLIST,
        extra: { LD_PRELOAD: "/evil.so" },
      }),
    ).toThrow(expect.objectContaining({ code: "env_injection_rejected" }));
  });

  it("does NOT throw for safe extra keys", () => {
    const result = buildEnv({
      processEnv: {},
      allowlist: DEFAULT_ALLOWLIST,
      extra: { CUSTOM_SAFE: "ok" },
    });
    expect(result["CUSTOM_SAFE"]).toBe("ok");
  });
});
```

Make sure `import { buildEnv, DEFAULT_ALLOWLIST } from "../src/spawn.js"` is already at the top of the file (it should be).

**Step 2: Run tests to confirm they fail**

```bash
cd wrappers/typescript && pnpm test -- --reporter=verbose 2>&1 | grep -A5 "BLOCKED_ENV_KEYS"
```

Expected: 2 tests FAIL — no error is thrown currently.

**Step 3: Update `wrappers/typescript/src/spawn.ts`**

Add the `BLOCKED_ENV_KEYS` constant and validation. Open the file and add after the existing `DEFAULT_ALLOWLIST` export (around line 20):

```typescript
/** Keys that callers must NOT inject via env.extra (SC-3, design §4.12.1). */
export const BLOCKED_ENV_KEYS: ReadonlySet<string> = new Set([
  "PYTHONPATH",
  "LD_PRELOAD",
  "LD_LIBRARY_PATH",
  "PYTHONSTARTUP",
  "PATH",
  "PYTHONHOME",
  "PYTHONNOUSERSITE",
  "DYLD_INSERT_LIBRARIES",
  "DYLD_LIBRARY_PATH",
]);
```

Then add the blocked-key check at the top of `buildEnv`:

```typescript
export function buildEnv(opts: BuildEnvOptions): Record<string, string> {
  const { processEnv, allowlist, extra = {} } = opts;

  // SC-3: reject blocked keys in extra before building the env
  for (const key of Object.keys(extra)) {
    if (BLOCKED_ENV_KEYS.has(key)) {
      // Import AaaError from session.ts — keep import at module top level
      throw new AaaError(
        "env_injection_rejected",
        `env.extra key '${key}' is blocked for security reasons (design §4.12.1).`,
        { classification: "protocol", severity: "error" },
      );
    }
  }

  const allowSet = new Set(allowlist);
  const result: Record<string, string> = {};

  for (const [key, value] of Object.entries(processEnv)) {
    if (value === undefined) continue;
    if (
      allowSet.has(key) ||
      key.startsWith("AMPLIFIER_") ||
      key.startsWith("LC_")
    ) {
      result[key] = value;
    }
  }

  for (const [key, value] of Object.entries(extra)) {
    result[key] = value;
  }

  return result;
}
```

You also need to import `AaaError` at the top of `spawn.ts`:

```typescript
import { AaaError } from "./session.js";
```

**Step 4: Run tests to confirm they pass**

```bash
cd wrappers/typescript && pnpm typecheck && pnpm test
```

All tests must pass.

**Step 5: Commit**

```bash
git add wrappers/typescript/src/spawn.ts \
        wrappers/typescript/test/spawn.test.ts

git commit -m "feat(wrappers): TS buildEnv BLOCKED_ENV_KEYS validation (A6 SC-3)"
```

---

### Task 13: TypeScript wrapper async `probeEngineVersion` (A6 — SC-7)

**Design reference:** §4.12.2

**Files:**
- Edit: `wrappers/typescript/src/spawn.ts`
- Edit: `wrappers/typescript/src/index.ts` (update call site + `_versionProbe` type)
- Edit: `wrappers/typescript/test/spawn.test.ts` (add async test)
- Edit: `wrappers/typescript/test/spawn-agent.test.ts` (may need `_versionProbe` type fix)

---

**Step 1: Write the failing test** (add to `wrappers/typescript/test/spawn.test.ts`)

```typescript
describe("probeEngineVersion (SC-7)", () => {
  it("probeEngineVersion is an async function returning a Promise", async () => {
    // The function must return a Promise when called — easiest check is
    // that it is declared as async or returns a thenable.
    // We call it with a fake binary and expect a rejection (not a sync throw).
    const result = probeEngineVersion("/nonexistent-binary", {});
    expect(result).toBeInstanceOf(Promise);
    // Allow the promise to settle (it will reject — we don't care about the error)
    await result.catch(() => {});
  });
});
```

Add `import { probeEngineVersion } from "../src/spawn.js";` to the imports at the top if not there.

**Step 2: Run the test to confirm it fails**

```bash
cd wrappers/typescript && pnpm test -- --reporter=verbose 2>&1 | grep -A8 "probeEngineVersion"
```

Expected: FAIL — `probeEngineVersion` currently returns `EngineVersionPayload`, not a `Promise`.

**Step 3: Convert `probeEngineVersion` to async in `spawn.ts`**

Open `wrappers/typescript/src/spawn.ts`. Replace the synchronous `probeEngineVersion` with:

```typescript
import { promisify } from "node:util";
import { execFile } from "node:child_process";

const execFileAsync = promisify(execFile);

/**
 * Run `<binPath> version --json` and parse the JSON response.
 *
 * Async version using promisify(execFile) (SC-7, design §4.12.2).
 *
 * @param binPath   Absolute path to the amplifier-agent binary.
 * @param env       Environment to pass to the subprocess.
 * @param timeoutMs Timeout in milliseconds (default: 5000).
 */
export async function probeEngineVersion(
  binPath: string,
  env: Record<string, string>,
  timeoutMs = 5000,
): Promise<EngineVersionPayload> {
  const { stdout } = await execFileAsync(binPath, ["version", "--json"], {
    encoding: "utf-8",
    timeout: timeoutMs,
    env,
  });
  return JSON.parse(stdout.trim()) as EngineVersionPayload;
}
```

Remove the old `execFileSync` import if it's no longer needed. Keep `execSync` if it's still used by `resolveBinaryPath`.

**Step 4: Update the call site in `index.ts`**

Open `wrappers/typescript/src/index.ts`. Find the version probe call (around line 146):

```typescript
// Before:
versionPayload = probeEngineVersion(binaryPath, subprocessEnv);

// After:
versionPayload = await probeEngineVersion(binaryPath, subprocessEnv);
```

Also update `_versionProbe` type in `SpawnAgentParams`:

```typescript
// Before:
_versionProbe?: (
  binPath: string,
  env: Record<string, string>,
) => EngineVersionPayload;

// After:
_versionProbe?: (
  binPath: string,
  env: Record<string, string>,
) => Promise<EngineVersionPayload>;
```

And the call site for `_versionProbe` (around line 144):

```typescript
// Before:
versionPayload = params._versionProbe(binaryPath, subprocessEnv);

// After:
versionPayload = await params._versionProbe(binaryPath, subprocessEnv);
```

**Step 5: Check `spawn-agent.test.ts` for broken mocks**

Open `wrappers/typescript/test/spawn-agent.test.ts`. Search for `_versionProbe`. Any mock that returns a plain `EngineVersionPayload` needs to return a `Promise<EngineVersionPayload>`:

```typescript
// Before:
_versionProbe: () => ({ version: "0.0.0", protocolVersion: "0.1.0" }),

// After:
_versionProbe: async () => ({ version: "0.0.0", protocolVersion: "0.1.0" }),
```

**Step 6: Run all TypeScript tests and typecheck**

```bash
cd wrappers/typescript && pnpm typecheck && pnpm test
```

All must pass.

**Step 7: Run full Python suite (regression check)**

```bash
uv run pytest tests/ wrappers/python/tests/ --timeout=60
```

All must pass.

**Step 8: Commit**

```bash
git add wrappers/typescript/src/spawn.ts \
        wrappers/typescript/src/index.ts \
        wrappers/typescript/test/spawn.test.ts \
        wrappers/typescript/test/spawn-agent.test.ts

git commit -m "feat(wrappers): async probeEngineVersion — TypeScript wrapper (A6 SC-7)"
```

---

## End-of-phase acceptance gate

Run this checklist in order. Do NOT proceed until every item is green.

### 1. Python main suite

```bash
uv run pytest tests/ -v --timeout=60 2>&1 | tail -30
```

Expected: all tests pass. Look for any newly added tests you wrote — they should all appear here.

### 2. Python wrapper suite

```bash
uv run pytest wrappers/python/tests/ -v 2>&1 | tail -20
```

### 3. TypeScript typecheck

```bash
cd wrappers/typescript && pnpm typecheck
```

Expected: zero errors.

### 4. TypeScript tests

```bash
cd wrappers/typescript && pnpm test
```

Expected: all tests pass.

### 5. Verify A1 types are in generated `types.ts`

```bash
grep -n "McpServerConfig\|HostCapabilities\|ErrorCode" wrappers/typescript/src/types.ts | head -20
```

Expected: `McpServerConfig`, `HostCapabilities`, `InitializeHostParams` appear as exported interfaces; `ErrorCode` union includes `"approval_translation_failed"`, `"approval_protocol_violation"`, `"env_injection_rejected"`.

### 6. Verify A3 error codes have unit tests

```bash
uv run pytest tests/test_wire_approval_provider.py -v
```

Expected: all 4 tests pass (including all 3 error code tests).

### 7. Verify all Phase 1 commits are present

```bash
git log --oneline -15
```

Expected output (order may vary slightly, but all 13 commits must be present):

```
feat(wrappers): async probeEngineVersion — TypeScript wrapper (A6 SC-7)
feat(wrappers): TS buildEnv BLOCKED_ENV_KEYS validation (A6 SC-3)
feat(wrappers): async probe_engine_version — Python wrapper (A6 SC-7)
feat(wrappers): Python build_env BLOCKED_ENV_KEYS validation (A6 SC-3)
feat(engine): register WireApprovalProvider in _runtime.py (A3 CR-2)
feat(engine): WireApprovalProvider — three explicit error codes (A3 CR-2)
feat(engine): wire session persistence resume path into _runtime.py (A2 CR-1)
feat(engine): IncrementalSaveHook — tool:post transcript save (A2 CR-1)
feat(engine): SessionStore — JSONL transcript + JSON metadata (A2 CR-1)
feat(wire): extend TS AaaError with severity/classification/correlationId (A1)
feat(wire): AaaError.severity/classification/correlation_id + approval ErrorCodes (A1)
feat(wire): add McpServerConfig, HostCapabilities, InitializeParams extensions (A1)
feat(wire): bump PROTOCOL_VERSION to 0.1.0 (A1)
```

---

## What comes next (Phase 2 and 3)

**Do NOT implement these in Phase 1** — they are out of scope.

| Phase | Plan file | Key work |
|---|---|---|
| **Phase 2** | `docs/plans/2026-05-22-aaa-nc-provider-phase2-engine-integration.md` | Bundle changes (A4), MCP threading in `_runtime.py` (A5), `amplifier-agent doctor --strict` subcommand (A7), new conformance fixtures (A8), PyPI/npm v0.2.0 release (A9) |
| **Phase 3** | `docs/plans/2026-05-22-aaa-nc-provider-phase3-nanoclaw-consumption.md` | All NC repo work (N1–N7) in `/Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh` |

Phase 2 can begin only after all Phase 1 commits are on the branch and the exit gate above is fully green. Phase 3 requires Phase 2's v0.2.0 release.

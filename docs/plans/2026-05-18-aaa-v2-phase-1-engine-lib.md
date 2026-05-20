# AaA v2 Phase 1 — `amplifier_agent_lib` Engine Library Implementation Plan

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Build the mode-agnostic Python engine library `amplifier_agent_lib` — the transport-free core that both Mode A (single-turn argv) and Mode B (stdio JSON-RPC) CLI binaries will wrap in later phases.

**Architecture:** A flat Python package under `src/amplifier_agent_lib/` containing four concerns: (1) `protocol/` — wire-protocol TypedDicts that are the cross-language source of truth; (2) `protocol_points/` — `Protocol` abstractions for Approval and Display plus Mode A CLI defaults; (3) `persistence.py` — XDG cache/config/state path resolution keyed by package version; (4) `engine.py` — the `Engine` class with `boot/dispatch/submit_turn/shutdown` lifecycle that consumes injected protocol points and never touches stdin/stdout directly. Spawn (`spawn.py`) is library-internal per design §8 — a Phase 1 stub establishes its module boundary.

**Tech Stack:** Python 3.11+, `uv` for dependency management, `pytest` + `pytest-asyncio` for tests, `ruff` for lint/format, `pyright` for type checking, `hatchling` for build backend, `src/` layout.

**Phase context:** This is Phase 1 of 4. Phases 2–4 (CLI Mode A / CLI Mode B / vendored bundle + cache) build on this library. The measurement phase (cold-start) is deferred and not part of these four plans.

**Source of authority:** `/Users/mpaidiparthy/repos/AaA/opus-recon/aaa-source/docs/designs/aaa-v2-design-checkpoint.md` (commit `c74316c`). Especially §5 (non-server design), §6 (approval + display + 9 notification types), §8 (spawn is library-internal), Appendix A (wire protocol), Appendix B (L14 synthesis contract).

**Reference repo for patterns:** `/Users/mpaidiparthy/repos/AaA/opus-recon/amplifier-app-openclaw/` (sibling Python+CLI package using the same kernel; see its `pyproject.toml` for dependency shape, its `tests/` layout for test conventions). Do NOT copy structure blindly — AaA is more neutral than OpenClaw. Use it as a reference, not a template.

**Working directory for all commands:** `/Users/mpaidiparthy/repos/AaA/opus-recon/amplifier-agent/`

**Critical invariant (enforced by Task 12):** `amplifier_agent_lib` MUST NOT contain any `print()` or `sys.stdout.write()` calls in its source modules. Output happens only through injected `ProtocolPoints` defaults — and defaults that write to streams (Task 8's `defaults_cli.py`) write to the injected stream, not raw stdout. Task 12 is a static check that fails the build if this is violated.

**Conventions throughout:**
- All commit messages use Conventional Commits (`feat:`, `test:`, `chore:`, `refactor:`).
- Every implementation step has a preceding failing test (RED → verify-fail → GREEN → verify-pass → commit).
- Type hints required throughout (`from __future__ import annotations` at the top of every module).
- Snake_case modules and functions; PascalCase classes.
- Each task is a single atomic commit.

**Task dependencies:**
- Task 2 depends on Task 1 (needs `pyproject.toml` for `uv sync` to work).
- Tasks 3–6 (protocol types) depend on Task 2 (need the package to exist).
- Task 7 (`protocol_points/base.py`) is independent of protocol types but logically follows.
- Task 8 (`defaults_cli.py`) depends on Task 7 (uses `ApprovalSystem` / `DisplaySystem` protocols) and Task 3 (uses `ErrorCode`).
- Task 9 (`persistence.py`) is independent.
- Task 10 (`spawn.py`) is independent (stub).
- Task 11 (`engine.py`) depends on Tasks 3–7 (consumes all protocol types and protocol-point abstractions).
- Task 12 (stdout discipline test) depends on the full library tree being in place.

---

## Task 1: Repo Bootstrap — `pyproject.toml`, `.gitignore`, test scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `tests/__init__.py`
- Create: `tests/test_smoke.py`

**Step 1: Write the failing smoke test**

Create `tests/__init__.py` as an empty file.

Create `tests/test_smoke.py`:

```python
"""Smoke test: package is importable and exposes a version string."""

from __future__ import annotations


def test_package_importable() -> None:
    import amplifier_agent_lib

    assert isinstance(amplifier_agent_lib.__version__, str)
    assert amplifier_agent_lib.__version__  # non-empty
```

**Step 2: Create `pyproject.toml`**

Write the following to `pyproject.toml` at the repo root. Dependency pins follow the sibling `amplifier-app-openclaw/pyproject.toml` pattern.

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "amplifier-agent"
version = "0.0.1"
description = "Amplifier as Agent — mode-agnostic engine library and CLI"
readme = "README.md"
requires-python = ">=3.11"
license = { text = "MIT" }
dependencies = [
    "amplifier-core>=1.0.0",
    "amplifier-foundation>=1.0.0",
    "amplifier-module-context-persistent>=1.0.0",
]

[tool.uv.sources]
amplifier-core = { git = "https://github.com/microsoft/amplifier-core", branch = "main" }
amplifier-foundation = { git = "https://github.com/microsoft/amplifier-foundation", branch = "main" }
amplifier-module-context-persistent = { git = "https://github.com/microsoft/amplifier-module-context-persistent", branch = "main" }

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "pytest-cov>=5.0",
    "ruff>=0.6",
    "pyright>=1.1.380",
]

[tool.hatch.build.targets.wheel]
packages = ["src/amplifier_agent_lib"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
addopts = "-ra"

[tool.ruff]
line-length = 120
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "N", "RUF"]
ignore = ["E501"]

[tool.ruff.format]
quote-style = "double"

[tool.pyright]
include = ["src", "tests"]
pythonVersion = "3.11"
typeCheckingMode = "standard"
reportMissingImports = "warning"
```

**Step 3: Create `.gitignore`**

Write to `.gitignore`:

```gitignore
# Python
__pycache__/
*.py[cod]
*$py.class
*.egg-info/
.eggs/
dist/
build/

# Virtual envs
.venv/
venv/

# Test / type / lint caches
.pytest_cache/
.mypy_cache/
.ruff_cache/
.pyright/
.coverage
coverage.xml
htmlcov/

# uv
uv.lock

# Editor
.idea/
.vscode/
*.swp
.DS_Store
```

**Step 4: Run smoke test to verify it fails**

```bash
cd /Users/mpaidiparthy/repos/AaA/opus-recon/amplifier-agent
uv sync --extra dev
uv run pytest tests/test_smoke.py -v
```

Expected: `uv sync` succeeds. `pytest` FAILS with `ModuleNotFoundError: No module named 'amplifier_agent_lib'`.

**Step 5: Commit**

```bash
git add pyproject.toml .gitignore tests/__init__.py tests/test_smoke.py
git commit -m "chore: bootstrap pyproject.toml, .gitignore, and failing smoke test"
```

---

## Task 2: Package skeleton — `src/amplifier_agent_lib/__init__.py`

**Files:**
- Create: `src/amplifier_agent_lib/__init__.py`

**Depends on:** Task 1.

**Step 1: Write the implementation**

Create `src/amplifier_agent_lib/__init__.py`:

```python
"""amplifier_agent_lib — mode-agnostic Amplifier agent engine library.

This package is transport-free: it never reads from stdin or writes to stdout
directly. All I/O flows through ProtocolPoints injected at Engine.boot().

See: docs/designs/aaa-v2-design-checkpoint.md §5.
"""

from __future__ import annotations

__version__ = "0.0.1"

__all__ = ["__version__"]
```

**Step 2: Run smoke test to verify it now passes**

```bash
uv run pytest tests/test_smoke.py -v
```

Expected: PASS — `test_package_importable PASSED`.

**Step 3: Commit**

```bash
git add src/amplifier_agent_lib/__init__.py
git commit -m "feat: add amplifier_agent_lib package skeleton with version"
```

---

## Task 3: Protocol error codes — `protocol/errors.py`

**Files:**
- Create: `tests/test_protocol_errors.py`
- Create: `src/amplifier_agent_lib/protocol/__init__.py` (empty for now; re-exports added in Task 6)
- Create: `src/amplifier_agent_lib/protocol/errors.py`

**Depends on:** Task 2.

Error codes unify design Appendix A "Error codes" with Phase 1 spec additions.

**Step 1: Write the failing test**

Create `tests/test_protocol_errors.py`:

```python
"""Tests for protocol/errors.py — wire-level error code enum."""

from __future__ import annotations

from amplifier_agent_lib.protocol.errors import ErrorCode


def test_error_code_is_str_enum() -> None:
    """ErrorCode members are strings for JSON-RPC error.data.code field."""
    assert isinstance(ErrorCode.INVALID_SESSION.value, str)
    assert ErrorCode.INVALID_SESSION == "invalid_session"


def test_error_code_values_unique() -> None:
    """Every error code has a unique string value."""
    values = [member.value for member in ErrorCode]
    assert len(values) == len(set(values)), "duplicate ErrorCode values"


def test_error_code_required_members_present() -> None:
    """All design-required codes are present (Appendix A + Phase 1 task spec)."""
    required = {
        "stale_session",
        "invalid_session",
        "config_validation",
        "runtime",
        "spawn_failed",
        "internal",
        "agent_not_ready",
        "provider_not_configured",
        "approval_timeout",
        "provider_init_failed",
        "bundle_load_failed",
        "session_not_found",
        "prompt_required",
        "approval_denied",
        "tool_execution_failed",
        "wire_protocol_violation",
    }
    actual = {member.value for member in ErrorCode}
    missing = required - actual
    assert not missing, f"missing required error codes: {sorted(missing)}"


def test_error_code_lookup_by_value() -> None:
    """ErrorCode('invalid_session') resolves to the enum member."""
    assert ErrorCode("invalid_session") is ErrorCode.INVALID_SESSION
```

**Step 2: Run test to verify it fails**

```bash
mkdir -p src/amplifier_agent_lib/protocol
touch src/amplifier_agent_lib/protocol/__init__.py
uv run pytest tests/test_protocol_errors.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'amplifier_agent_lib.protocol.errors'`.

**Step 3: Write minimal implementation**

Create `src/amplifier_agent_lib/protocol/errors.py`:

```python
"""Wire-level error codes for JSON-RPC error.data.code field.

These codes are part of the cross-language wire contract (see design Appendix A).
Engine and wrappers both reference this enum.
"""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    """JSON-RPC structured error codes (carried in error.data.code)."""

    # Lifecycle / session errors
    AGENT_NOT_READY = "agent_not_ready"
    INVALID_SESSION = "invalid_session"
    STALE_SESSION = "stale_session"
    SESSION_NOT_FOUND = "session_not_found"

    # Configuration errors
    CONFIG_VALIDATION = "config_validation"
    PROVIDER_NOT_CONFIGURED = "provider_not_configured"
    PROVIDER_INIT_FAILED = "provider_init_failed"
    PROMPT_REQUIRED = "prompt_required"

    # Bundle / spawn errors
    BUNDLE_LOAD_FAILED = "bundle_load_failed"
    SPAWN_FAILED = "spawn_failed"

    # Approval errors
    APPROVAL_DENIED = "approval_denied"
    APPROVAL_TIMEOUT = "approval_timeout"

    # Tool / runtime errors
    TOOL_EXECUTION_FAILED = "tool_execution_failed"
    RUNTIME = "runtime"

    # Wire protocol errors
    WIRE_PROTOCOL_VIOLATION = "wire_protocol_violation"

    # Catch-all
    INTERNAL = "internal"
```

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_protocol_errors.py -v
```

Expected: All 4 tests PASS.

**Step 5: Commit**

```bash
git add src/amplifier_agent_lib/protocol/__init__.py src/amplifier_agent_lib/protocol/errors.py tests/test_protocol_errors.py
git commit -m "feat(protocol): add ErrorCode StrEnum for JSON-RPC error.data.code"
```

---

## Task 4: Protocol method types — `protocol/methods.py`

**Files:**
- Create: `tests/test_protocol_methods.py`
- Create: `src/amplifier_agent_lib/protocol/methods.py`

**Depends on:** Task 3.

Wire shape source: design Appendix A "Methods (client → engine)". `PROTOCOL_VERSION` constant lives here.

**Step 1: Write the failing test**

Create `tests/test_protocol_methods.py`:

```python
"""Tests for protocol/methods.py — request/response shapes for JSON-RPC methods."""

from __future__ import annotations

import json

from amplifier_agent_lib.protocol.methods import (
    PROTOCOL_VERSION,
    AgentShutdownParams,
    CacheInfoParams,
    CacheInfoResult,
    ClientInfo,
    InitializeParams,
    InitializeResult,
    ServerInfo,
    SessionState,
    TurnSubmitParams,
    TurnSubmitResult,
)


def test_protocol_version_constant() -> None:
    assert isinstance(PROTOCOL_VERSION, str)
    assert PROTOCOL_VERSION


def test_initialize_params_roundtrip() -> None:
    params: InitializeParams = {
        "protocolVersion": PROTOCOL_VERSION,
        "clientInfo": {"name": "amplifier-agent-client-ts", "version": "0.1.0"},
        "capabilities": {},
        "sessionId": "sess-abc",
        "resume": False,
    }
    encoded = json.dumps(params)
    decoded = json.loads(encoded)
    assert decoded["sessionId"] == "sess-abc"
    assert decoded["clientInfo"]["name"] == "amplifier-agent-client-ts"


def test_initialize_result_roundtrip() -> None:
    result: InitializeResult = {
        "capabilities": {},
        "serverInfo": {"name": "amplifier-agent", "version": "0.0.1"},
        "sessionState": {"sessionId": "sess-abc", "resumed": False},
    }
    decoded = json.loads(json.dumps(result))
    assert decoded["serverInfo"]["name"] == "amplifier-agent"


def test_turn_submit_params_minimal() -> None:
    params: TurnSubmitParams = {"prompt": "hello", "turnId": "turn-1", "sessionId": "sess-abc"}
    assert json.loads(json.dumps(params)) == params


def test_turn_submit_result_shape() -> None:
    result: TurnSubmitResult = {"reply": "hi back", "turnId": "turn-1"}
    decoded = json.loads(json.dumps(result))
    assert decoded["reply"] == "hi back"


def test_agent_shutdown_params_empty() -> None:
    params: AgentShutdownParams = {}
    assert json.loads(json.dumps(params)) == {}


def test_cache_info_shapes() -> None:
    p: CacheInfoParams = {}
    r: CacheInfoResult = {"cachePath": "/tmp/cache", "preparedBundles": ["builtin@0.0.1"]}
    assert json.loads(json.dumps(p)) == {}
    assert json.loads(json.dumps(r))["preparedBundles"] == ["builtin@0.0.1"]


def test_client_info_and_server_info() -> None:
    ci: ClientInfo = {"name": "x", "version": "1"}
    si: ServerInfo = {"name": "y", "version": "2"}
    assert ci["name"] == "x" and si["version"] == "2"


def test_session_state_optional_resumed() -> None:
    s: SessionState = {"sessionId": "sess-x", "resumed": True}
    assert s["resumed"] is True
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_protocol_methods.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'amplifier_agent_lib.protocol.methods'`.

**Step 3: Write minimal implementation**

Create `src/amplifier_agent_lib/protocol/methods.py`:

```python
"""TypedDict shapes for JSON-RPC method requests and responses.

Source of truth for the cross-language wire contract. See design Appendix A.
"""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict

PROTOCOL_VERSION = "2026-05-aaa-v0"
"""Wire protocol version. Bump on breaking changes; semver applies."""


class ClientInfo(TypedDict):
    name: str
    version: str


class ServerInfo(TypedDict):
    name: str
    version: str


class SessionState(TypedDict):
    sessionId: str
    resumed: bool


class InitializeParams(TypedDict):
    protocolVersion: str
    clientInfo: ClientInfo
    capabilities: dict[str, Any]
    sessionId: NotRequired[str]
    resume: NotRequired[bool]
    providerOverride: NotRequired[str]
    cwd: NotRequired[str]


class InitializeResult(TypedDict):
    capabilities: dict[str, Any]
    serverInfo: ServerInfo
    sessionState: SessionState


class TurnSubmitParams(TypedDict):
    sessionId: str
    turnId: str
    prompt: str
    attachments: NotRequired[list[dict[str, Any]]]


class TurnSubmitResult(TypedDict):
    reply: str | None
    turnId: str
    finalEvent: NotRequired[dict[str, Any]]


class TurnCancelParams(TypedDict):
    sessionId: str
    turnId: str


class TurnCancelResult(TypedDict):
    cancelled: bool


class SessionCreateParams(TypedDict):
    sessionId: str
    resume: NotRequired[bool]


class SessionCreateResult(TypedDict):
    sessionState: SessionState


class SessionEndParams(TypedDict):
    sessionId: str


class SessionEndResult(TypedDict):
    ended: bool


class AgentShutdownParams(TypedDict):
    pass


class AgentShutdownResult(TypedDict):
    pass


class CacheInfoParams(TypedDict):
    pass


class CacheInfoResult(TypedDict):
    cachePath: str
    preparedBundles: list[str]
```

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_protocol_methods.py -v
```

Expected: All 9 tests PASS.

**Step 5: Commit**

```bash
git add src/amplifier_agent_lib/protocol/methods.py tests/test_protocol_methods.py
git commit -m "feat(protocol): add TypedDict shapes for JSON-RPC method requests/responses"
```

---

## Task 5: Protocol notification types — `protocol/notifications.py`

**Files:**
- Create: `tests/test_protocol_notifications.py`
- Create: `src/amplifier_agent_lib/protocol/notifications.py`

**Depends on:** Task 4.

The 9-notification canonical taxonomy from design §6, plus `approval/request` and `approval/timeout`. Module docstring documents the L14 synthesis contract (Phase 1 task spec §11).

**Step 1: Write the failing test**

Create `tests/test_protocol_notifications.py`:

```python
"""Tests for protocol/notifications.py — engine→client notification shapes."""

from __future__ import annotations

import json

from amplifier_agent_lib.protocol import notifications as N


def test_result_delta_roundtrip() -> None:
    n: N.ResultDeltaNotification = {"sessionId": "s", "turnId": "t", "text": "hello"}
    assert json.loads(json.dumps(n))["text"] == "hello"


def test_result_final_roundtrip() -> None:
    n: N.ResultFinalNotification = {"sessionId": "s", "turnId": "t", "text": "done"}
    assert json.loads(json.dumps(n))["text"] == "done"


def test_tool_started_and_completed() -> None:
    s: N.ToolStartedNotification = {
        "sessionId": "s",
        "turnId": "t",
        "toolCallId": "tc-1",
        "name": "read_file",
        "args": {"path": "/etc/hosts"},
    }
    c: N.ToolCompletedNotification = {
        "sessionId": "s",
        "turnId": "t",
        "toolCallId": "tc-1",
        "name": "read_file",
        "result": {"content": "..."},
        "durationMs": 12,
    }
    assert json.loads(json.dumps(s))["toolCallId"] == "tc-1"
    assert json.loads(json.dumps(c))["durationMs"] == 12


def test_thinking_delta_and_final() -> None:
    d: N.ThinkingDeltaNotification = {"sessionId": "s", "turnId": "t", "text": "..."}
    f: N.ThinkingFinalNotification = {"sessionId": "s", "turnId": "t", "text": "..."}
    assert json.loads(json.dumps(d))["text"] == "..."
    assert json.loads(json.dumps(f))["text"] == "..."


def test_progress() -> None:
    p: N.ProgressNotification = {"sessionId": "s", "turnId": "t", "message": "working..."}
    assert json.loads(json.dumps(p))["message"] == "working..."


def test_usage() -> None:
    u: N.UsageNotification = {
        "sessionId": "s",
        "turnId": "t",
        "inputTokens": 100,
        "outputTokens": 50,
    }
    assert json.loads(json.dumps(u))["inputTokens"] == 100


def test_error_notification() -> None:
    e: N.ErrorNotification = {
        "sessionId": "s",
        "turnId": "t",
        "code": "tool_execution_failed",
        "message": "boom",
        "recoverable": True,
    }
    assert json.loads(json.dumps(e))["recoverable"] is True


def test_approval_request_and_timeout() -> None:
    req: N.ApprovalRequestNotification = {
        "sessionId": "s",
        "turnId": "t",
        "approvalId": "ap-1",
        "kind": "tool",
        "payload": {"toolName": "delete_file"},
        "timeoutMs": 30000,
    }
    tmo: N.ApprovalTimeoutNotification = {
        "sessionId": "s",
        "turnId": "t",
        "approvalId": "ap-1",
        "kind": "tool",
    }
    assert json.loads(json.dumps(req))["timeoutMs"] == 30000
    assert json.loads(json.dumps(tmo))["approvalId"] == "ap-1"


def test_canonical_taxonomy_constant_complete() -> None:
    """CANONICAL_DISPLAY_EVENTS lists exactly the 9 display notifications from design §6."""
    expected = {
        "result/delta",
        "result/final",
        "tool/started",
        "tool/completed",
        "progress",
        "thinking/delta",
        "thinking/final",
        "usage",
        "error",
    }
    assert set(N.CANONICAL_DISPLAY_EVENTS) == expected
    assert len(N.CANONICAL_DISPLAY_EVENTS) == 9


def test_l14_synthesis_contract_documented() -> None:
    """L14 synthesis contract is named in the module docstring."""
    assert N.__doc__ is not None
    assert "L14" in N.__doc__
    assert "result/final" in N.__doc__
    assert "synthesi" in N.__doc__.lower()
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_protocol_notifications.py -v
```

Expected: FAIL with `ModuleNotFoundError`.

**Step 3: Write minimal implementation**

Create `src/amplifier_agent_lib/protocol/notifications.py`:

```python
"""TypedDict shapes for engine→client notifications (one-way, no response).

Canonical display taxonomy (9 events) per design §6:

    result/delta, result/final, tool/started, tool/completed,
    progress, thinking/delta, thinking/final, usage, error

Plus two approval-related notifications:
    approval/request (server-initiated, expects response), approval/timeout

L14 synthesis contract (Appendix B):
    For any turn/submit response that returns a non-null reply scalar but
    does NOT produce a result/final notification before the response arrives,
    wrappers MUST synthesize a result/final before closing the iterable
    returned to the caller. The synthesized event:
        - text:   extracted from the reply scalar
        - turnId: matched to the in-flight turn
        - usage:  omitted
    Wrappers in TS and Python both implement this contract; conformance suites
    enforce it. Phase 1 captures the contract here in documentation; the
    wire-level implementation lives in Phase 3 (defaults_stdio.py) and in the
    language wrappers' synthesis logic.
"""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict


CANONICAL_DISPLAY_EVENTS: tuple[str, ...] = (
    "result/delta",
    "result/final",
    "tool/started",
    "tool/completed",
    "progress",
    "thinking/delta",
    "thinking/final",
    "usage",
    "error",
)
"""The fixed taxonomy. Adapters translate; they do NOT invent new types."""


class ResultDeltaNotification(TypedDict):
    sessionId: str
    turnId: str
    text: str


class ResultFinalNotification(TypedDict):
    sessionId: str
    turnId: str
    text: str
    usage: NotRequired[dict[str, Any]]


class ToolStartedNotification(TypedDict):
    sessionId: str
    turnId: str
    toolCallId: str
    name: str
    args: dict[str, Any]


class ToolCompletedNotification(TypedDict):
    sessionId: str
    turnId: str
    toolCallId: str
    name: str
    result: Any
    durationMs: int


class ThinkingDeltaNotification(TypedDict):
    sessionId: str
    turnId: str
    text: str


class ThinkingFinalNotification(TypedDict):
    sessionId: str
    turnId: str
    text: str


class ProgressNotification(TypedDict):
    sessionId: str
    turnId: str
    message: str
    percent: NotRequired[float]


class UsageNotification(TypedDict):
    sessionId: str
    turnId: str
    inputTokens: int
    outputTokens: int
    cost: NotRequired[float]


class ErrorNotification(TypedDict):
    sessionId: str
    turnId: NotRequired[str]
    code: str
    message: str
    recoverable: bool


class ApprovalRequestNotification(TypedDict):
    sessionId: str
    turnId: str
    approvalId: str
    kind: str
    payload: dict[str, Any]
    timeoutMs: int


class ApprovalTimeoutNotification(TypedDict):
    sessionId: str
    turnId: str
    approvalId: str
    kind: str
```

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_protocol_notifications.py -v
```

Expected: All 10 tests PASS.

**Step 5: Commit**

```bash
git add src/amplifier_agent_lib/protocol/notifications.py tests/test_protocol_notifications.py
git commit -m "feat(protocol): add notification TypedDicts + L14 synthesis contract docs"
```

---

## Task 6: Protocol capabilities + package re-exports

**Files:**
- Create: `tests/test_protocol_capabilities.py`
- Create: `src/amplifier_agent_lib/protocol/capabilities.py`
- Modify: `src/amplifier_agent_lib/protocol/__init__.py`

**Depends on:** Tasks 3, 4, 5.

Capability negotiation per design §6 — engine respects only what the client advertised.

**Step 1: Write the failing test**

Create `tests/test_protocol_capabilities.py`:

```python
"""Tests for protocol/capabilities.py — capability negotiation shapes + logic."""

from __future__ import annotations

from amplifier_agent_lib.protocol import (
    CANONICAL_DISPLAY_EVENTS,
    PROTOCOL_VERSION,
    ClientCapabilities,
    ErrorCode,
    ServerCapabilities,
    negotiate_capabilities,
    server_default_capabilities,
)


def test_protocol_version_reexported() -> None:
    assert isinstance(PROTOCOL_VERSION, str)


def test_error_code_reexported() -> None:
    assert ErrorCode.INVALID_SESSION.value == "invalid_session"


def test_server_default_capabilities_includes_all_9_events() -> None:
    caps = server_default_capabilities()
    advertised = set(caps["display"]["events"])
    assert advertised == set(CANONICAL_DISPLAY_EVENTS)
    assert set(caps["approval"]["actions"]) == {"accept", "decline", "cancel"}


def test_negotiate_intersects_display_events() -> None:
    client: ClientCapabilities = {
        "approval": {"actions": ["accept", "decline", "cancel"]},
        "display": {"events": ["result/delta", "result/final", "tool/started", "tool/completed"]},
    }
    server: ServerCapabilities = server_default_capabilities()
    negotiated = negotiate_capabilities(client=client, server=server)
    assert set(negotiated["display"]["events"]) == {
        "result/delta",
        "result/final",
        "tool/started",
        "tool/completed",
    }


def test_negotiate_intersects_approval_actions() -> None:
    client: ClientCapabilities = {
        "approval": {"actions": ["accept", "decline"]},
        "display": {"events": list(CANONICAL_DISPLAY_EVENTS)},
    }
    server: ServerCapabilities = server_default_capabilities()
    negotiated = negotiate_capabilities(client=client, server=server)
    assert set(negotiated["approval"]["actions"]) == {"accept", "decline"}


def test_negotiate_handles_missing_client_sections() -> None:
    client: ClientCapabilities = {}
    server: ServerCapabilities = server_default_capabilities()
    negotiated = negotiate_capabilities(client=client, server=server)
    assert negotiated["display"]["events"] == []
    assert negotiated["approval"]["actions"] == []
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_protocol_capabilities.py -v
```

Expected: FAIL — re-exports not in place yet.

**Step 3: Write minimal implementation**

Create `src/amplifier_agent_lib/protocol/capabilities.py`:

```python
"""Capability negotiation shapes and logic.

At agent/initialize, client and server exchange capabilities; the engine then
respects only the intersection. See design §6 + Appendix A.
"""

from __future__ import annotations

from typing import NotRequired, TypedDict

from amplifier_agent_lib.protocol.notifications import CANONICAL_DISPLAY_EVENTS


class ApprovalCapability(TypedDict):
    actions: list[str]


class DisplayCapability(TypedDict):
    events: list[str]


class ClientCapabilities(TypedDict, total=False):
    approval: ApprovalCapability
    display: DisplayCapability
    experimental: NotRequired[dict[str, object]]


class ServerCapabilities(TypedDict, total=False):
    approval: ApprovalCapability
    display: DisplayCapability
    experimental: NotRequired[dict[str, object]]


def server_default_capabilities() -> ServerCapabilities:
    """Default server capabilities — engine advertises everything by default."""
    return {
        "approval": {"actions": ["accept", "decline", "cancel"]},
        "display": {"events": list(CANONICAL_DISPLAY_EVENTS)},
    }


def negotiate_capabilities(
    *,
    client: ClientCapabilities,
    server: ServerCapabilities,
) -> ServerCapabilities:
    """Return the intersection of client and server capabilities."""
    client_actions = set(client.get("approval", {}).get("actions", []))
    server_actions = set(server.get("approval", {}).get("actions", []))
    client_events = set(client.get("display", {}).get("events", []))
    server_events = set(server.get("display", {}).get("events", []))

    return {
        "approval": {"actions": sorted(client_actions & server_actions)},
        "display": {"events": sorted(client_events & server_events)},
    }
```

Replace `src/amplifier_agent_lib/protocol/__init__.py`:

```python
"""Protocol wire shapes — single source of truth for cross-language types.

See design Appendix A for the JSON-RPC wire contract.
"""

from __future__ import annotations

from amplifier_agent_lib.protocol.capabilities import (
    ApprovalCapability,
    ClientCapabilities,
    DisplayCapability,
    ServerCapabilities,
    negotiate_capabilities,
    server_default_capabilities,
)
from amplifier_agent_lib.protocol.errors import ErrorCode
from amplifier_agent_lib.protocol.methods import (
    PROTOCOL_VERSION,
    AgentShutdownParams,
    AgentShutdownResult,
    CacheInfoParams,
    CacheInfoResult,
    ClientInfo,
    InitializeParams,
    InitializeResult,
    ServerInfo,
    SessionCreateParams,
    SessionCreateResult,
    SessionEndParams,
    SessionEndResult,
    SessionState,
    TurnCancelParams,
    TurnCancelResult,
    TurnSubmitParams,
    TurnSubmitResult,
)
from amplifier_agent_lib.protocol.notifications import (
    CANONICAL_DISPLAY_EVENTS,
    ApprovalRequestNotification,
    ApprovalTimeoutNotification,
    ErrorNotification,
    ProgressNotification,
    ResultDeltaNotification,
    ResultFinalNotification,
    ThinkingDeltaNotification,
    ThinkingFinalNotification,
    ToolCompletedNotification,
    ToolStartedNotification,
    UsageNotification,
)

__all__ = [
    "PROTOCOL_VERSION",
    "CANONICAL_DISPLAY_EVENTS",
    "ErrorCode",
    "ApprovalCapability",
    "ClientCapabilities",
    "DisplayCapability",
    "ServerCapabilities",
    "negotiate_capabilities",
    "server_default_capabilities",
    "AgentShutdownParams",
    "AgentShutdownResult",
    "CacheInfoParams",
    "CacheInfoResult",
    "ClientInfo",
    "InitializeParams",
    "InitializeResult",
    "ServerInfo",
    "SessionCreateParams",
    "SessionCreateResult",
    "SessionEndParams",
    "SessionEndResult",
    "SessionState",
    "TurnCancelParams",
    "TurnCancelResult",
    "TurnSubmitParams",
    "TurnSubmitResult",
    "ApprovalRequestNotification",
    "ApprovalTimeoutNotification",
    "ErrorNotification",
    "ProgressNotification",
    "ResultDeltaNotification",
    "ResultFinalNotification",
    "ThinkingDeltaNotification",
    "ThinkingFinalNotification",
    "ToolCompletedNotification",
    "ToolStartedNotification",
    "UsageNotification",
]
```

**Step 4: Run all protocol tests to verify they pass**

```bash
uv run pytest tests/test_protocol_capabilities.py tests/test_protocol_errors.py tests/test_protocol_methods.py tests/test_protocol_notifications.py -v
```

Expected: All tests across all four files PASS.

**Step 5: Commit**

```bash
git add src/amplifier_agent_lib/protocol/capabilities.py src/amplifier_agent_lib/protocol/__init__.py tests/test_protocol_capabilities.py
git commit -m "feat(protocol): add capability negotiation + package re-exports"
```

---

## Task 7: Protocol points base — `protocol_points/base.py`

**Files:**
- Create: `tests/test_protocol_points_base.py`
- Create: `src/amplifier_agent_lib/protocol_points/__init__.py`
- Create: `src/amplifier_agent_lib/protocol_points/base.py`

**Depends on:** Task 5.

`ApprovalSystem` and `DisplaySystem` Protocols per design §6. Spawn is NOT a protocol point — it is library-internal per §8.

**Step 1: Write the failing test**

Create `tests/test_protocol_points_base.py`:

```python
"""Tests for protocol_points/base.py — ApprovalSystem and DisplaySystem protocols."""

from __future__ import annotations

import pytest

from amplifier_agent_lib.protocol_points.base import (
    ApprovalRequest,
    ApprovalResponse,
    ApprovalSystem,
    DisplayEvent,
    DisplaySystem,
)


class _RecordingDisplay:
    def __init__(self) -> None:
        self.events: list[DisplayEvent] = []

    def emit(self, event: DisplayEvent) -> None:
        self.events.append(event)


class _AlwaysAcceptApproval:
    async def request(self, req: ApprovalRequest) -> ApprovalResponse:
        return {"action": "accept"}


def test_display_system_is_protocol_conformant() -> None:
    d: DisplaySystem = _RecordingDisplay()
    d.emit({"type": "progress", "sessionId": "s", "turnId": "t", "message": "hi"})
    assert isinstance(d, _RecordingDisplay)
    assert d.events[0]["type"] == "progress"  # type: ignore[index]


@pytest.mark.asyncio
async def test_approval_system_is_protocol_conformant() -> None:
    a: ApprovalSystem = _AlwaysAcceptApproval()
    resp = await a.request(
        {
            "approvalId": "ap-1",
            "kind": "tool",
            "payload": {"toolName": "delete_file"},
            "timeoutMs": 1000,
            "sessionId": "s",
            "turnId": "t",
        }
    )
    assert resp["action"] == "accept"


def test_approval_response_action_values() -> None:
    accept: ApprovalResponse = {"action": "accept"}
    decline: ApprovalResponse = {"action": "decline"}
    cancel: ApprovalResponse = {"action": "cancel", "payload": {"reason": "user"}}
    assert accept["action"] in ("accept", "decline", "cancel")
    assert decline["action"] in ("accept", "decline", "cancel")
    assert cancel["action"] in ("accept", "decline", "cancel")


def test_display_event_carries_type_discriminator() -> None:
    e: DisplayEvent = {"type": "result/delta", "sessionId": "s", "turnId": "t", "text": "x"}
    assert e["type"] == "result/delta"


def test_no_spawn_protocol_exported() -> None:
    """Spawn is library-internal (design §8) — no Spawn Protocol type may exist here."""
    import amplifier_agent_lib.protocol_points.base as mod

    forbidden = {"SpawnSystem", "Spawn", "SpawnProtocol"}
    actual = set(dir(mod))
    assert not (forbidden & actual)
```

**Step 2: Run test to verify it fails**

```bash
mkdir -p src/amplifier_agent_lib/protocol_points
touch src/amplifier_agent_lib/protocol_points/__init__.py
uv run pytest tests/test_protocol_points_base.py -v
```

Expected: FAIL with `ModuleNotFoundError`.

**Step 3: Write minimal implementation**

Create `src/amplifier_agent_lib/protocol_points/base.py`:

```python
"""Protocol-point abstractions injected into Engine.boot().

Two protocol points are externally exposed (design §6):
    - ApprovalSystem — interactive, request/response
    - DisplaySystem — one-way, event stream (folds in streaming)

Spawn is NOT a protocol point. It is library-internal per design §8 / Brian D3.
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, Protocol, TypedDict, runtime_checkable


class DisplayEvent(TypedDict):
    """One-way envelope from engine to display sink.

    The 'type' field is one of CANONICAL_DISPLAY_EVENTS. Payload fields vary
    by type and are defined by the corresponding *Notification TypedDict in
    protocol/notifications.py.
    """

    type: str
    sessionId: str
    turnId: NotRequired[str]


@runtime_checkable
class DisplaySystem(Protocol):
    """Structural protocol for display sinks.

    Synchronous on purpose: the engine emits events in tight loops. The stdio
    bridge (Phase 3) queues events for async write internally.
    """

    def emit(self, event: DisplayEvent) -> None: ...


ApprovalAction = Literal["accept", "decline", "cancel"]


class ApprovalRequest(TypedDict):
    sessionId: str
    turnId: str
    approvalId: str
    kind: str
    payload: dict[str, Any]
    timeoutMs: int


class ApprovalResponse(TypedDict):
    action: ApprovalAction
    payload: NotRequired[dict[str, Any]]


@runtime_checkable
class ApprovalSystem(Protocol):
    """Structural protocol for approval mediation.

    Implementations MUST honor timeoutMs and return {'action': 'cancel'} on
    timeout (defense-in-depth; engine enforces too).
    """

    async def request(self, req: ApprovalRequest) -> ApprovalResponse: ...


class ProtocolPoints(TypedDict):
    """The bundle of protocol points injected at Engine.boot()."""

    approval: ApprovalSystem
    display: DisplaySystem
```

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_protocol_points_base.py -v
```

Expected: All 5 tests PASS.

**Step 5: Commit**

```bash
git add src/amplifier_agent_lib/protocol_points/__init__.py src/amplifier_agent_lib/protocol_points/base.py tests/test_protocol_points_base.py
git commit -m "feat(protocol_points): add ApprovalSystem and DisplaySystem Protocols"
```

---

## Task 8: CLI mode defaults — `protocol_points/defaults_cli.py`

**Files:**
- Create: `tests/test_protocol_points_defaults_cli.py`
- Create: `src/amplifier_agent_lib/protocol_points/defaults_cli.py`
- Modify: `src/amplifier_agent_lib/protocol_points/__init__.py`

**Depends on:** Task 7, Task 3.

Per design §6 Mode A defaults: approval is `prompt-when-tty, deny-otherwise` with `-y`/`-n` overrides; display writes `[type]` prefixed lines to an injected stream.

**Step 1: Write the failing test**

Create `tests/test_protocol_points_defaults_cli.py`:

```python
"""Tests for protocol_points/defaults_cli.py — Mode A CLI defaults."""

from __future__ import annotations

import io

import pytest

from amplifier_agent_lib.protocol_points.base import ApprovalRequest, DisplayEvent
from amplifier_agent_lib.protocol_points.defaults_cli import (
    ApprovalOverride,
    CliApprovalSystem,
    CliDisplaySystem,
    DisplayVerbosity,
)


def _evt(t: str, **extra: object) -> DisplayEvent:
    base: dict[str, object] = {"type": t, "sessionId": "s", "turnId": "t1"}
    base.update(extra)
    return base  # type: ignore[return-value]


def test_display_default_emits_prefixed_lines() -> None:
    buf = io.StringIO()
    d = CliDisplaySystem(stream=buf, verbosity=DisplayVerbosity.DEFAULT)
    d.emit(_evt("result/delta", text="hello"))
    d.emit(_evt("tool/started", name="read_file", args={"path": "/etc/hosts"}))
    out = buf.getvalue()
    assert "[result/delta]" in out
    assert "hello" in out
    assert "[tool/started]" in out
    assert "read_file" in out


def test_display_quiet_suppresses_all_events() -> None:
    buf = io.StringIO()
    d = CliDisplaySystem(stream=buf, verbosity=DisplayVerbosity.QUIET)
    d.emit(_evt("result/final", text="done"))
    d.emit(_evt("error", code="x", message="y", recoverable=True))
    assert buf.getvalue() == ""


def test_display_default_suppresses_thinking_and_progress() -> None:
    """Per design §6: default verbosity suppresses thinking/* and progress."""
    buf = io.StringIO()
    d = CliDisplaySystem(stream=buf, verbosity=DisplayVerbosity.DEFAULT)
    d.emit(_evt("thinking/delta", text="..."))
    d.emit(_evt("thinking/final", text="..."))
    d.emit(_evt("progress", message="..."))
    assert buf.getvalue() == ""


def test_display_verbose_enables_thinking_and_progress() -> None:
    buf = io.StringIO()
    d = CliDisplaySystem(stream=buf, verbosity=DisplayVerbosity.VERBOSE)
    d.emit(_evt("thinking/delta", text="reasoning..."))
    d.emit(_evt("progress", message="loading"))
    out = buf.getvalue()
    assert "[thinking/delta]" in out
    assert "[progress]" in out


def test_display_debug_includes_json_dump() -> None:
    buf = io.StringIO()
    d = CliDisplaySystem(stream=buf, verbosity=DisplayVerbosity.DEBUG)
    d.emit(_evt("tool/started", name="read_file", args={"path": "/tmp"}))
    out = buf.getvalue()
    assert "[tool/started]" in out
    assert '"path"' in out and '"/tmp"' in out


def _make_req() -> ApprovalRequest:
    return {
        "sessionId": "s",
        "turnId": "t1",
        "approvalId": "ap-1",
        "kind": "tool",
        "payload": {"toolName": "delete_file"},
        "timeoutMs": 1000,
    }


@pytest.mark.asyncio
async def test_approval_override_yes_returns_accept() -> None:
    a = CliApprovalSystem(override=ApprovalOverride.YES, is_tty=False)
    resp = await a.request(_make_req())
    assert resp["action"] == "accept"


@pytest.mark.asyncio
async def test_approval_override_no_returns_decline() -> None:
    a = CliApprovalSystem(override=ApprovalOverride.NO, is_tty=False)
    resp = await a.request(_make_req())
    assert resp["action"] == "decline"


@pytest.mark.asyncio
async def test_approval_no_tty_no_override_returns_decline() -> None:
    """Non-TTY default behavior: deny (apt/npm-style; design §6)."""
    a = CliApprovalSystem(override=None, is_tty=False)
    resp = await a.request(_make_req())
    assert resp["action"] == "decline"


@pytest.mark.asyncio
async def test_approval_tty_prompt_accept() -> None:
    prompts: list[str] = []

    def fake_prompt(message: str) -> str:
        prompts.append(message)
        return "y"

    a = CliApprovalSystem(override=None, is_tty=True, prompt_fn=fake_prompt)
    resp = await a.request(_make_req())
    assert resp["action"] == "accept"
    assert len(prompts) == 1
    assert "delete_file" in prompts[0] or "tool" in prompts[0]


@pytest.mark.asyncio
async def test_approval_tty_prompt_decline() -> None:
    a = CliApprovalSystem(override=None, is_tty=True, prompt_fn=lambda _: "n")
    resp = await a.request(_make_req())
    assert resp["action"] == "decline"


@pytest.mark.asyncio
async def test_approval_tty_prompt_blank_declines() -> None:
    a = CliApprovalSystem(override=None, is_tty=True, prompt_fn=lambda _: "")
    resp = await a.request(_make_req())
    assert resp["action"] == "decline"
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_protocol_points_defaults_cli.py -v
```

Expected: FAIL with `ModuleNotFoundError`.

**Step 3: Write minimal implementation**

Create `src/amplifier_agent_lib/protocol_points/defaults_cli.py`:

```python
"""Mode A CLI defaults for ApprovalSystem and DisplaySystem.

- CliApprovalSystem: prompt-when-tty, deny-otherwise; -y/-n overrides.
- CliDisplaySystem: stderr-style "[type] ..." prefix per event; verbosity gated.

Both classes are pure: they take their stream / tty-detection / prompt-fn as
constructor args so tests can drive them deterministically. The CLI binary
(Phase 2) wires these to sys.stderr, sys.stdin.isatty(), and builtins.input().
"""

from __future__ import annotations

import enum
import json
from collections.abc import Callable
from typing import TextIO

from amplifier_agent_lib.protocol_points.base import (
    ApprovalRequest,
    ApprovalResponse,
    DisplayEvent,
)


class DisplayVerbosity(enum.Enum):
    QUIET = "quiet"
    DEFAULT = "default"
    VERBOSE = "verbose"
    DEBUG = "debug"


_SUPPRESSED_AT_DEFAULT: frozenset[str] = frozenset(
    {"thinking/delta", "thinking/final", "progress"}
)


class CliDisplaySystem:
    """Writes display events as "[type] ..." lines to an injected stream.

    NOTE: Per the stdout-discipline invariant, this class does NOT default to
    sys.stdout. Callers (the CLI binary in Phase 2) pass sys.stderr explicitly.
    """

    def __init__(self, *, stream: TextIO, verbosity: DisplayVerbosity = DisplayVerbosity.DEFAULT) -> None:
        self._stream = stream
        self._verbosity = verbosity

    def emit(self, event: DisplayEvent) -> None:
        if self._verbosity == DisplayVerbosity.QUIET:
            return
        evt_type = event.get("type", "?")
        if self._verbosity == DisplayVerbosity.DEFAULT and evt_type in _SUPPRESSED_AT_DEFAULT:
            return

        summary = self._summarize(event)
        line = f"[{evt_type}] {summary}"
        if self._verbosity == DisplayVerbosity.DEBUG:
            line = f"{line}  {json.dumps(dict(event), sort_keys=True)}"
        self._stream.write(line + "\n")
        self._stream.flush()

    @staticmethod
    def _summarize(event: DisplayEvent) -> str:
        t = event.get("type", "")
        if t in ("result/delta", "result/final", "thinking/delta", "thinking/final"):
            return str(event.get("text", ""))
        if t == "tool/started":
            return str(event.get("name", ""))
        if t == "tool/completed":
            ms = event.get("durationMs", "")
            return f"{event.get('name', '')} ({ms}ms)"
        if t == "progress":
            return str(event.get("message", ""))
        if t == "usage":
            return f"in={event.get('inputTokens', '?')} out={event.get('outputTokens', '?')}"
        if t == "error":
            return f"{event.get('code', '?')}: {event.get('message', '')}"
        return ""


class ApprovalOverride(enum.Enum):
    YES = "yes"
    NO = "no"


class CliApprovalSystem:
    """Mode A approval: prompt-when-tty, deny-otherwise.

    Override precedence:
        ApprovalOverride.YES  -> always accept (no prompt)
        ApprovalOverride.NO   -> always decline (no prompt)
        None + is_tty=True    -> prompt via prompt_fn
        None + is_tty=False   -> decline (apt/npm-style safer default)
    """

    def __init__(
        self,
        *,
        override: ApprovalOverride | None = None,
        is_tty: bool = False,
        prompt_fn: Callable[[str], str] | None = None,
    ) -> None:
        self._override = override
        self._is_tty = is_tty
        self._prompt_fn = prompt_fn

    async def request(self, req: ApprovalRequest) -> ApprovalResponse:
        if self._override == ApprovalOverride.YES:
            return {"action": "accept"}
        if self._override == ApprovalOverride.NO:
            return {"action": "decline"}
        if not self._is_tty:
            return {"action": "decline"}

        if self._prompt_fn is None:  # pragma: no cover
            return {"action": "decline"}

        kind = req.get("kind", "?")
        payload_summary = self._summarize_payload(req)
        message = f"Approve [{kind}] {payload_summary} [y/N]: "
        answer = self._prompt_fn(message).strip().lower()
        if answer in ("y", "yes"):
            return {"action": "accept"}
        return {"action": "decline"}

    @staticmethod
    def _summarize_payload(req: ApprovalRequest) -> str:
        payload = req.get("payload", {})
        if "toolName" in payload:
            return str(payload["toolName"])
        return json.dumps(payload, sort_keys=True)[:80]
```

Replace `src/amplifier_agent_lib/protocol_points/__init__.py`:

```python
"""Protocol-point abstractions and default implementations.

External protocol points (design §6):
    - ApprovalSystem
    - DisplaySystem

Mode A defaults (this package): CliApprovalSystem, CliDisplaySystem.
Mode B defaults (Phase 3): defaults_stdio.py — JSON-RPC bridges.
"""

from __future__ import annotations

from amplifier_agent_lib.protocol_points.base import (
    ApprovalAction,
    ApprovalRequest,
    ApprovalResponse,
    ApprovalSystem,
    DisplayEvent,
    DisplaySystem,
    ProtocolPoints,
)
from amplifier_agent_lib.protocol_points.defaults_cli import (
    ApprovalOverride,
    CliApprovalSystem,
    CliDisplaySystem,
    DisplayVerbosity,
)

__all__ = [
    "ApprovalAction",
    "ApprovalRequest",
    "ApprovalResponse",
    "ApprovalSystem",
    "DisplayEvent",
    "DisplaySystem",
    "ProtocolPoints",
    "ApprovalOverride",
    "CliApprovalSystem",
    "CliDisplaySystem",
    "DisplayVerbosity",
]
```

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_protocol_points_defaults_cli.py -v
```

Expected: All 11 tests PASS.

**Step 5: Commit**

```bash
git add src/amplifier_agent_lib/protocol_points/defaults_cli.py src/amplifier_agent_lib/protocol_points/__init__.py tests/test_protocol_points_defaults_cli.py
git commit -m "feat(protocol_points): add CLI Mode A defaults (TTY approval + stderr display)"
```

---

## Task 9: Persistence — `persistence.py`

**Files:**
- Create: `tests/test_persistence.py`
- Create: `src/amplifier_agent_lib/persistence.py`

**Depends on:** Task 2.

Per design §3 CLI section: cache at `$XDG_CACHE_HOME/amplifier-agent/prepared/<version>/`, config at `$XDG_CONFIG_HOME/amplifier-agent/`, state at `$XDG_STATE_HOME/amplifier-agent/sessions/<sessionId>/`.

**Step 1: Write the failing test**

Create `tests/test_persistence.py`:

```python
"""Tests for persistence.py — XDG path resolution + version-keyed cache."""

from __future__ import annotations

from pathlib import Path

import pytest

from amplifier_agent_lib.persistence import (
    APP_NAME,
    cache_root,
    config_root,
    prepared_bundle_dir,
    session_state_dir,
    state_root,
)


def test_app_name_constant() -> None:
    assert APP_NAME == "amplifier-agent"


def test_cache_root_uses_xdg_cache_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert cache_root() == tmp_path / APP_NAME


def test_cache_root_falls_back_to_home_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cache_root() == tmp_path / ".cache" / APP_NAME


def test_config_root_uses_xdg_config_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert config_root() == tmp_path / APP_NAME


def test_config_root_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert config_root() == tmp_path / ".config" / APP_NAME


def test_state_root_uses_xdg_state_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert state_root() == tmp_path / APP_NAME


def test_state_root_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert state_root() == tmp_path / ".local" / "state" / APP_NAME


def test_prepared_bundle_dir_includes_version(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Cache key is the package version; bumping version invalidates the cache."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    p = prepared_bundle_dir()
    parts = p.parts
    assert parts[-3] == APP_NAME
    assert parts[-2] == "prepared"
    from amplifier_agent_lib import __version__

    assert parts[-1] == __version__


def test_prepared_bundle_dir_with_explicit_version(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    p = prepared_bundle_dir(version="9.9.9")
    assert p == tmp_path / APP_NAME / "prepared" / "9.9.9"


def test_session_state_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert session_state_dir("sess-abc-123") == tmp_path / APP_NAME / "sessions" / "sess-abc-123"


def test_session_state_dir_rejects_path_traversal() -> None:
    with pytest.raises(ValueError):
        session_state_dir("../etc")
    with pytest.raises(ValueError):
        session_state_dir("a/b")
    with pytest.raises(ValueError):
        session_state_dir("")
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_persistence.py -v
```

Expected: FAIL with `ModuleNotFoundError`.

**Step 3: Write minimal implementation**

Create `src/amplifier_agent_lib/persistence.py`:

```python
"""XDG-compliant filesystem paths for cache, config, and state.

Per design §3 CLI section:
    cache:  $XDG_CACHE_HOME/amplifier-agent/prepared/<version>/
    config: $XDG_CONFIG_HOME/amplifier-agent/
    state:  $XDG_STATE_HOME/amplifier-agent/sessions/<sessionId>/

Cache key is the package __version__; bumping the version invalidates all
previously-prepared bundles automatically — no migration logic required.

Fallbacks (when XDG_* unset) follow the XDG Base Directory Spec:
    ~/.cache, ~/.config, ~/.local/state

This module is pure path computation. It does NOT create directories;
callers create them on first write (see Phase 4 bundle prep).
"""

from __future__ import annotations

import os
from pathlib import Path

from amplifier_agent_lib import __version__

APP_NAME = "amplifier-agent"


def _home() -> Path:
    return Path(os.environ.get("HOME", os.path.expanduser("~")))


def cache_root() -> Path:
    """Return $XDG_CACHE_HOME/amplifier-agent (or ~/.cache/amplifier-agent)."""
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else _home() / ".cache"
    return base / APP_NAME


def config_root() -> Path:
    """Return $XDG_CONFIG_HOME/amplifier-agent (or ~/.config/amplifier-agent)."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else _home() / ".config"
    return base / APP_NAME


def state_root() -> Path:
    """Return $XDG_STATE_HOME/amplifier-agent (or ~/.local/state/amplifier-agent)."""
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg) if xdg else _home() / ".local" / "state"
    return base / APP_NAME


def prepared_bundle_dir(*, version: str | None = None) -> Path:
    """Path to the version-keyed prepared bundle cache directory."""
    v = version if version is not None else __version__
    return cache_root() / "prepared" / v


def session_state_dir(session_id: str) -> Path:
    """Path to per-session transcript directory.

    Rejects sessionIds that would escape the state root (path traversal guard).
    """
    if not session_id:
        raise ValueError("session_id must be non-empty")
    if "/" in session_id or "\\" in session_id or ".." in session_id:
        raise ValueError(f"invalid session_id (path-traversal characters): {session_id!r}")
    return state_root() / "sessions" / session_id
```

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_persistence.py -v
```

Expected: All 11 tests PASS.

**Step 5: Commit**

```bash
git add src/amplifier_agent_lib/persistence.py tests/test_persistence.py
git commit -m "feat: add persistence.py with XDG cache/config/state paths"
```

---

## Task 10: Spawn stub — `spawn.py`

**Files:**
- Create: `tests/test_spawn.py`
- Create: `src/amplifier_agent_lib/spawn.py`

**Depends on:** Task 2.

Per design §8: spawn is library-internal. Phase 1 establishes the module boundary; the full implementation depending on amplifier-foundation lands in Phase 4.

**Step 1: Write the failing test**

Create `tests/test_spawn.py`:

```python
"""Tests for spawn.py — library-internal spawn manager (Phase 1 stub)."""

from __future__ import annotations

import pytest

from amplifier_agent_lib.spawn import InternalSpawnManager, SpawnNotReadyError


def test_internal_spawn_manager_constructs() -> None:
    mgr = InternalSpawnManager()
    assert isinstance(mgr, InternalSpawnManager)


def test_internal_spawn_manager_is_not_yet_ready() -> None:
    """Phase 1 stub: spawn_session raises SpawnNotReadyError."""
    mgr = InternalSpawnManager()
    with pytest.raises(SpawnNotReadyError):
        mgr.spawn_session(parent_session_id="parent", config={})


def test_spawn_module_does_not_expose_public_external_api() -> None:
    """Spawn is library-internal — no factory or override hook is exported.

    Per design §8, V2 explicitly inverts V1's adapter-owned spawn. Any future
    additions here must NOT introduce a spawn_fn parameter or external override.
    """
    import amplifier_agent_lib.spawn as mod

    public = set(mod.__all__)
    allowed = {"InternalSpawnManager", "SpawnNotReadyError"}
    extras = public - allowed
    assert not extras, f"spawn.py exports unexpected public names: {extras}"
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_spawn.py -v
```

Expected: FAIL with `ModuleNotFoundError`.

**Step 3: Write minimal implementation**

Create `src/amplifier_agent_lib/spawn.py`:

```python
"""Library-internal spawn manager for delegate/recipe/sub-agent creation.

Per design §8 (Brian's D3 directive): spawn is LIBRARY-INTERNAL. There is no
adapter override surface; no spawn_fn parameter exists on any public API.

Sub-agents are in-process AmplifierSession instances within the parent engine's
process — NOT new subprocesses. This mirrors OpenClaw's validated CLISpawnManager
precedent (see amplifier-app-openclaw/src/amplifier_app_openclaw/spawn.py).

Phase 1 ships the module boundary as a stub. The body — config merge, child
session creation against PreparedBundle, parent_id inheritance — depends on
amplifier-foundation integration and lands in Phase 4.
"""

from __future__ import annotations

from typing import Any

__all__ = ["InternalSpawnManager", "SpawnNotReadyError"]


class SpawnNotReadyError(RuntimeError):
    """Raised when spawn is invoked before Phase 4 integration is in place."""


class InternalSpawnManager:
    """In-process spawn manager (Phase 1 stub).

    Phase 4 will add:
        - prepared bundle reference (PreparedBundle from amplifier-foundation)
        - config merge / module list overlay
        - child AmplifierSession creation with parent_id link
        - cancellation propagation from parent to children
    """

    def __init__(self) -> None:
        pass

    def spawn_session(self, *, parent_session_id: str, config: dict[str, Any]) -> Any:
        """Spawn an in-process child session. Phase 1: not implemented."""
        raise SpawnNotReadyError(
            "InternalSpawnManager.spawn_session is a Phase 1 stub; "
            "real implementation lands in Phase 4 alongside amplifier-foundation integration."
        )
```

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_spawn.py -v
```

Expected: All 3 tests PASS.

**Step 5: Commit**

```bash
git add src/amplifier_agent_lib/spawn.py tests/test_spawn.py
git commit -m "feat: add InternalSpawnManager stub (library-internal, no external API)"
```

---

## Task 11: Engine — `engine.py`

**Files:**
- Create: `tests/test_engine.py`
- Create: `src/amplifier_agent_lib/engine.py`

**Depends on:** Tasks 3, 4, 5, 6, 7, 8, 9, 10.

The Engine class. Phase 1 scope:
- `Engine` accepts injected `ProtocolPoints` at construction.
- `boot()` is async and idempotent; performs capability negotiation.
- `dispatch()` is the single entry-point the CLI calls per method.
- `submit_turn()` is the high-level helper used by Mode A directly.
- `shutdown()` is async and idempotent.
- All model invocation is deferred to Phase 4 — Phase 1 tests use a mocked `turn_handler` injected at construction.

**Step 1: Write the failing test**

Create `tests/test_engine.py`:

```python
"""Tests for engine.py — Engine boot/dispatch/submit_turn/shutdown lifecycle.

Phase 1 uses a mock turn-handler injected into the Engine to avoid pulling
amplifier-foundation. Real session integration lands in Phase 4.
"""

from __future__ import annotations

import io
from collections.abc import Awaitable, Callable

import pytest

from amplifier_agent_lib.engine import (
    Engine,
    EngineNotBootedError,
    EngineShutdownError,
    TurnContext,
)
from amplifier_agent_lib.protocol import (
    CANONICAL_DISPLAY_EVENTS,
    PROTOCOL_VERSION,
    InitializeParams,
    TurnSubmitParams,
)
from amplifier_agent_lib.protocol_points import (
    ApprovalRequest,
    ApprovalResponse,
    CliApprovalSystem,
    CliDisplaySystem,
    DisplayEvent,
    DisplayVerbosity,
    ProtocolPoints,
)


class _RecordingDisplay:
    def __init__(self) -> None:
        self.events: list[DisplayEvent] = []

    def emit(self, event: DisplayEvent) -> None:
        self.events.append(event)


class _AcceptApproval:
    async def request(self, req: ApprovalRequest) -> ApprovalResponse:
        return {"action": "accept"}


def _make_points() -> ProtocolPoints:
    return {"approval": _AcceptApproval(), "display": _RecordingDisplay()}


async def _echo_turn_handler(ctx: TurnContext) -> str:
    """Mock turn handler that emits two display events and returns a reply."""
    ctx.display.emit({"type": "result/delta", "sessionId": ctx.session_id, "turnId": ctx.turn_id, "text": "hi"})
    ctx.display.emit(
        {"type": "result/final", "sessionId": ctx.session_id, "turnId": ctx.turn_id, "text": f"echo: {ctx.prompt}"}
    )
    return f"echo: {ctx.prompt}"


def _make_engine(
    *,
    points: ProtocolPoints | None = None,
    handler: Callable[[TurnContext], Awaitable[str]] = _echo_turn_handler,
) -> Engine:
    return Engine(turn_handler=handler, protocol_points=points or _make_points())


@pytest.mark.asyncio
async def test_engine_boots_with_initialize_and_returns_capabilities() -> None:
    eng = _make_engine()
    params: InitializeParams = {
        "protocolVersion": PROTOCOL_VERSION,
        "clientInfo": {"name": "test-client", "version": "0.0.1"},
        "capabilities": {
            "approval": {"actions": ["accept", "decline", "cancel"]},
            "display": {"events": list(CANONICAL_DISPLAY_EVENTS)},
        },
        "sessionId": "sess-1",
    }
    result = await eng.boot(params)
    assert result["serverInfo"]["name"] == "amplifier-agent"
    assert result["sessionState"]["sessionId"] == "sess-1"
    assert result["sessionState"]["resumed"] is False
    assert set(result["capabilities"]["display"]["events"]) == set(CANONICAL_DISPLAY_EVENTS)


@pytest.mark.asyncio
async def test_engine_boot_is_idempotent() -> None:
    eng = _make_engine()
    params: InitializeParams = {
        "protocolVersion": PROTOCOL_VERSION,
        "clientInfo": {"name": "t", "version": "0"},
        "capabilities": {},
        "sessionId": "sess-1",
    }
    r1 = await eng.boot(params)
    r2 = await eng.boot(params)
    assert r1 == r2


@pytest.mark.asyncio
async def test_submit_turn_before_boot_raises() -> None:
    eng = _make_engine()
    with pytest.raises(EngineNotBootedError):
        await eng.submit_turn({"sessionId": "sess-1", "turnId": "t1", "prompt": "hi"})


@pytest.mark.asyncio
async def test_submit_turn_emits_display_events_and_returns_reply() -> None:
    points = _make_points()
    eng = _make_engine(points=points)
    await eng.boot(
        {
            "protocolVersion": PROTOCOL_VERSION,
            "clientInfo": {"name": "t", "version": "0"},
            "capabilities": {
                "approval": {"actions": ["accept", "decline", "cancel"]},
                "display": {"events": list(CANONICAL_DISPLAY_EVENTS)},
            },
            "sessionId": "sess-1",
        }
    )
    params: TurnSubmitParams = {"sessionId": "sess-1", "turnId": "t1", "prompt": "hello"}
    result = await eng.submit_turn(params)
    assert result["reply"] == "echo: hello"
    assert result["turnId"] == "t1"

    display = points["display"]
    assert isinstance(display, _RecordingDisplay)
    types = [e["type"] for e in display.events]
    assert "result/delta" in types
    assert "result/final" in types


@pytest.mark.asyncio
async def test_shutdown_marks_engine_unusable() -> None:
    eng = _make_engine()
    await eng.boot(
        {
            "protocolVersion": PROTOCOL_VERSION,
            "clientInfo": {"name": "t", "version": "0"},
            "capabilities": {},
            "sessionId": "sess-1",
        }
    )
    await eng.shutdown()
    with pytest.raises(EngineShutdownError):
        await eng.submit_turn({"sessionId": "sess-1", "turnId": "t2", "prompt": "x"})


@pytest.mark.asyncio
async def test_shutdown_is_idempotent() -> None:
    eng = _make_engine()
    await eng.boot(
        {
            "protocolVersion": PROTOCOL_VERSION,
            "clientInfo": {"name": "t", "version": "0"},
            "capabilities": {},
            "sessionId": "sess-1",
        }
    )
    await eng.shutdown()
    await eng.shutdown()


@pytest.mark.asyncio
async def test_dispatch_routes_known_methods() -> None:
    eng = _make_engine()
    init_result = await eng.dispatch(
        "agent/initialize",
        {
            "protocolVersion": PROTOCOL_VERSION,
            "clientInfo": {"name": "t", "version": "0"},
            "capabilities": {},
            "sessionId": "sess-1",
        },
    )
    assert init_result is not None and "serverInfo" in init_result

    turn_result = await eng.dispatch(
        "turn/submit", {"sessionId": "sess-1", "turnId": "t1", "prompt": "hi"}
    )
    assert turn_result is not None and turn_result["reply"] == "echo: hi"

    shutdown_result = await eng.dispatch("agent/shutdown", {})
    assert shutdown_result == {}


@pytest.mark.asyncio
async def test_dispatch_unknown_method_raises() -> None:
    eng = _make_engine()
    with pytest.raises(ValueError) as exc:
        await eng.dispatch("does/not/exist", {})
    assert "does/not/exist" in str(exc.value)


@pytest.mark.asyncio
async def test_engine_writes_only_via_injected_display(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Even during a full boot->submit->shutdown cycle, nothing reaches real stdout."""
    buf = io.StringIO()
    points: ProtocolPoints = {
        "approval": CliApprovalSystem(is_tty=False),
        "display": CliDisplaySystem(stream=buf, verbosity=DisplayVerbosity.DEFAULT),
    }
    eng = _make_engine(points=points)
    await eng.boot(
        {
            "protocolVersion": PROTOCOL_VERSION,
            "clientInfo": {"name": "t", "version": "0"},
            "capabilities": {
                "approval": {"actions": ["accept", "decline", "cancel"]},
                "display": {"events": list(CANONICAL_DISPLAY_EVENTS)},
            },
            "sessionId": "sess-1",
        }
    )
    await eng.submit_turn({"sessionId": "sess-1", "turnId": "t1", "prompt": "hi"})
    await eng.shutdown()

    captured = capsys.readouterr()
    assert captured.out == "", f"engine wrote to stdout: {captured.out!r}"
    assert "[result/final]" in buf.getvalue()
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_engine.py -v
```

Expected: FAIL with `ModuleNotFoundError`.

**Step 3: Write minimal implementation**

Create `src/amplifier_agent_lib/engine.py`:

```python
"""Engine — the mode-agnostic core of amplifier_agent_lib.

Lifecycle:
    Engine(turn_handler, protocol_points)
        .boot(params)              -> InitializeResult       # idempotent
        .dispatch(method, params)  -> result | None          # single CLI entry-point
        .submit_turn(params)       -> TurnSubmitResult       # Mode A helper
        .shutdown()                -> None                   # idempotent

Critical invariant: this module NEVER reads from stdin or writes to stdout
directly. All output flows through the DisplaySystem injected at construction.
Task 12 enforces this with a static check.

Phase 1 takes a mockable `turn_handler` so the engine lifecycle can be tested
without amplifier-foundation. Phase 4 will replace the handler with real
AmplifierSession.execute() integration.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from amplifier_agent_lib import __version__
from amplifier_agent_lib.protocol import (
    AgentShutdownParams,
    AgentShutdownResult,
    InitializeParams,
    InitializeResult,
    TurnSubmitParams,
    TurnSubmitResult,
    negotiate_capabilities,
    server_default_capabilities,
)
from amplifier_agent_lib.protocol_points import ApprovalSystem, DisplaySystem, ProtocolPoints


class EngineNotBootedError(RuntimeError):
    """Raised when an operation requires boot() to have been called first."""


class EngineShutdownError(RuntimeError):
    """Raised when an operation is attempted after shutdown()."""


@dataclass
class TurnContext:
    """Context passed to the turn handler (Phase 1 mockable boundary)."""

    session_id: str
    turn_id: str
    prompt: str
    approval: ApprovalSystem
    display: DisplaySystem


TurnHandler = Callable[[TurnContext], Awaitable[str]]


class Engine:
    """Mode-agnostic engine. Construct once per process; never touches stdio."""

    SERVER_NAME = "amplifier-agent"

    def __init__(
        self,
        *,
        turn_handler: TurnHandler,
        protocol_points: ProtocolPoints,
    ) -> None:
        self._turn_handler = turn_handler
        self._points = protocol_points
        self._booted: bool = False
        self._shutdown: bool = False
        self._session_id: str | None = None
        self._init_result: InitializeResult | None = None

    async def boot(self, params: InitializeParams) -> InitializeResult:
        """Negotiate capabilities and mark the engine ready. Idempotent."""
        self._guard_not_shutdown()
        if self._booted and self._init_result is not None:
            return self._init_result

        client_caps = params.get("capabilities", {})  # type: ignore[arg-type]
        server_caps = server_default_capabilities()
        negotiated = negotiate_capabilities(client=client_caps, server=server_caps)  # type: ignore[arg-type]

        session_id = params.get("sessionId", "")
        resumed = bool(params.get("resume", False))
        self._session_id = session_id

        result: InitializeResult = {
            "capabilities": dict(negotiated),
            "serverInfo": {"name": self.SERVER_NAME, "version": __version__},
            "sessionState": {"sessionId": session_id, "resumed": resumed},
        }
        self._init_result = result
        self._booted = True
        return result

    async def submit_turn(self, params: TurnSubmitParams) -> TurnSubmitResult:
        """Run a single turn via the injected turn_handler."""
        self._guard_booted()
        self._guard_not_shutdown()
        ctx = TurnContext(
            session_id=params["sessionId"],
            turn_id=params["turnId"],
            prompt=params["prompt"],
            approval=self._points["approval"],
            display=self._points["display"],
        )
        reply = await self._turn_handler(ctx)
        return {"reply": reply, "turnId": params["turnId"]}

    async def shutdown(self, _params: AgentShutdownParams | None = None) -> AgentShutdownResult:
        """Mark engine unusable. Idempotent."""
        self._shutdown = True
        return {}

    async def dispatch(self, method: str, params: dict[str, Any]) -> Any:
        """Route a method name to its handler.

        The CLI binary (Phase 2/3) calls this for every JSON-RPC request.
        """
        if method == "agent/initialize":
            return await self.boot(params)  # type: ignore[arg-type]
        if method == "turn/submit":
            return await self.submit_turn(params)  # type: ignore[arg-type]
        if method == "agent/shutdown":
            return await self.shutdown(params)  # type: ignore[arg-type]
        raise ValueError(f"unknown method: {method!r}")

    def _guard_booted(self) -> None:
        if not self._booted:
            raise EngineNotBootedError("Engine.boot() must be called before this operation")

    def _guard_not_shutdown(self) -> None:
        if self._shutdown:
            raise EngineShutdownError("Engine has been shut down")
```

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_engine.py -v
```

Expected: All 9 tests PASS.

**Step 5: Commit**

```bash
git add src/amplifier_agent_lib/engine.py tests/test_engine.py
git commit -m "feat: add Engine class with boot/dispatch/submit_turn/shutdown lifecycle"
```

---

## Task 12: stdout discipline invariant — static check test

**Files:**
- Create: `tests/test_stdout_discipline.py`

**Depends on:** Tasks 2–11.

Per the critical invariant: `amplifier_agent_lib` MUST NOT call `print(` or reference `sys.stdout` in any source module. This test scans package source files (executable lines, excluding docstrings/comments).

`CliDisplaySystem` (Task 8) is compliant — it writes to an injected stream, never to a hardcoded `sys.stdout`. The Phase 2 CLI binary wires `sys.stderr` to it.

**Step 1: Write the test**

Create `tests/test_stdout_discipline.py`:

```python
"""Critical invariant: amplifier_agent_lib never touches stdout directly.

All output flows through injected ProtocolPoints (DisplaySystem). This test
scans every Python source file in the package and fails if it finds:
    - print(                       (builtin print)
    - sys.stdout                   (direct stdout reference)

Docstrings and comments are excluded — only executable lines are checked.

If this test fails: route the offending write through self._points["display"]
or a callable provided to the offender's constructor. See engine.py and
defaults_cli.py for the established pattern.
"""

from __future__ import annotations

import ast
import io
import tokenize
from pathlib import Path

import amplifier_agent_lib

PKG_ROOT = Path(amplifier_agent_lib.__file__).parent


def _executable_source(path: Path) -> str:
    """Return source with docstring + comment tokens stripped."""
    src = path.read_text(encoding="utf-8")
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except tokenize.TokenizeError:
        return src

    tree = ast.parse(src, filename=str(path))
    docstring_lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                ds = body[0]
                start = ds.lineno
                end = ds.end_lineno or start
                for ln in range(start, end + 1):
                    docstring_lines.add(ln)

    out: list[str] = []
    for tok in tokens:
        tok_type, tok_str, (srow, _), _, _ = tok
        if tok_type == tokenize.COMMENT:
            continue
        if tok_type == tokenize.STRING and srow in docstring_lines:
            continue
        out.append(tok_str)
    return " ".join(out)


def _iter_py_files() -> list[Path]:
    return sorted(p for p in PKG_ROOT.rglob("*.py"))


def test_no_print_in_lib_sources() -> None:
    """No `print(` calls anywhere in amplifier_agent_lib executable code."""
    violations: list[str] = []
    for path in _iter_py_files():
        code = _executable_source(path)
        if "print(" in code:
            violations.append(str(path.relative_to(PKG_ROOT.parent)))
    assert not violations, (
        f"print() found in library code (route output through DisplaySystem): {violations}"
    )


def test_no_sys_stdout_in_lib_sources() -> None:
    """No `sys.stdout` references anywhere in amplifier_agent_lib executable code."""
    violations: list[str] = []
    for path in _iter_py_files():
        code = _executable_source(path)
        if "sys.stdout" in code:
            violations.append(str(path.relative_to(PKG_ROOT.parent)))
    assert not violations, (
        f"sys.stdout reference found in library code (use injected DisplaySystem stream): {violations}"
    )


def test_library_files_scanned_nonempty() -> None:
    """Sanity: the scanner actually found library source files."""
    files = _iter_py_files()
    assert len(files) >= 5, f"expected at least 5 library files, found {len(files)}: {files}"
```

**Step 2: Run test to verify result**

```bash
uv run pytest tests/test_stdout_discipline.py -v
```

Expected: PASS if Tasks 2–11 were implemented as specified. If a test FAILS:
- Identify the offending file from the assertion message.
- Route the write through the injected DisplaySystem stream (see `defaults_cli.py:CliDisplaySystem.emit` for the established pattern).
- Re-run until green.

**Step 3: Run the entire suite to verify the full library is green**

```bash
uv run pytest -v
```

Expected: All tests across all 11 test files PASS. Approximate tally:
- `test_smoke.py` — 1
- `test_protocol_errors.py` — 4
- `test_protocol_methods.py` — 9
- `test_protocol_notifications.py` — 10
- `test_protocol_capabilities.py` — 6
- `test_protocol_points_base.py` — 5
- `test_protocol_points_defaults_cli.py` — 11
- `test_persistence.py` — 11
- `test_spawn.py` — 3
- `test_engine.py` — 9
- `test_stdout_discipline.py` — 3

~72 tests total.

**Step 4: Run lint and type check**

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pyright src/ tests/
```

Expected: ruff check clean; ruff format clean; pyright emits at most warnings on missing imports of `amplifier-core` / `amplifier-foundation` (configured to warning level).

If `ruff format --check` reports files would be reformatted, run `uv run ruff format src/ tests/` and amend the relevant prior commit (or commit as a `chore: ruff format` follow-up).

**Step 5: Commit**

```bash
git add tests/test_stdout_discipline.py
git commit -m "test: enforce stdout-discipline invariant on amplifier_agent_lib sources"
```

---

## Phase 1 done — summary

At the end of Task 12 the repo state is:

```
amplifier-agent/
├── README.md                      # MS OSS boilerplate (untouched)
├── LICENSE                        # MS OSS boilerplate (untouched)
├── CODE_OF_CONDUCT.md             # MS OSS boilerplate (untouched)
├── SECURITY.md                    # MS OSS boilerplate (untouched)
├── SUPPORT.md                     # MS OSS boilerplate (untouched)
├── pyproject.toml                 # NEW (Task 1)
├── .gitignore                     # NEW (Task 1)
├── docs/
│   └── plans/
│       └── 2026-05-18-aaa-v2-phase-1-engine-lib.md   # this plan
├── src/
│   └── amplifier_agent_lib/
│       ├── __init__.py
│       ├── engine.py
│       ├── persistence.py
│       ├── spawn.py
│       ├── protocol/
│       │   ├── __init__.py
│       │   ├── capabilities.py
│       │   ├── errors.py
│       │   ├── methods.py
│       │   └── notifications.py
│       └── protocol_points/
│           ├── __init__.py
│           ├── base.py
│           └── defaults_cli.py
└── tests/
    ├── __init__.py
    ├── test_smoke.py
    ├── test_engine.py
    ├── test_persistence.py
    ├── test_protocol_capabilities.py
    ├── test_protocol_errors.py
    ├── test_protocol_methods.py
    ├── test_protocol_notifications.py
    ├── test_protocol_points_base.py
    ├── test_protocol_points_defaults_cli.py
    ├── test_spawn.py
    └── test_stdout_discipline.py
```

**Commits expected: ~12 atomic conventional commits.**

**Library shape ready for Phase 2 (CLI Mode A).** The CLI binary will:
1. Construct an `InternalSpawnManager` (placeholder until Phase 4).
2. Construct `CliApprovalSystem(override=..., is_tty=sys.stdin.isatty(), prompt_fn=input)`.
3. Construct `CliDisplaySystem(stream=sys.stderr, verbosity=...)`.
4. Construct `Engine(turn_handler=<real handler — Phase 4>, protocol_points={...})`.
5. Call `await engine.boot({...})`, then `await engine.submit_turn({...})`, write the final reply JSON to `sys.stdout`, then `await engine.shutdown()`.

Phases 2, 3, and 4 do not modify any Phase 1 module signature — they only add new modules (`amplifier_agent_cli/`, `protocol_points/defaults_stdio.py`, `_bundle/`).

**Open carry-over items for next phases (already noted in the design checkpoint §10):**
- Provider auto-detect precedence verified during Phase 2 wiring.
- L14 synthesis wire implementation in Phase 3.
- Real amplifier-foundation integration in Phase 4 (replaces Phase 1's mock `turn_handler`).

**End of Phase 1 plan.**

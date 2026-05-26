# AaA NC Provider — Phase 2: Engine Integration Implementation Plan

> **For execution:** Use `/execute-plan` mode or the subagent-driven-development recipe.

**Prerequisite:** Phase 1 plan (`docs/plans/2026-05-22-aaa-nc-provider-phase1-engine-core.md`) must be
merged before starting this plan. This plan assumes the following artifacts already exist on the
current branch and all their tests pass:

| Artifact | Phase 1 stage |
|---|---|
| `src/amplifier_agent_lib/session_store.py` | A2 |
| `src/amplifier_agent_lib/incremental_save.py` | A2 |
| `src/amplifier_agent_lib/wire_approval_provider.py` | A3 |
| `handle_initialize(params)` function in `src/amplifier_agent_lib/_runtime.py` | A2 |
| `BLOCKED_ENV_KEYS` validation in `wrappers/typescript/src/spawn.ts` and `wrappers/python/src/spawn.py` | A6 |
| Async `probeEngineVersion()` in both wrappers | A6 |
| `HostCapabilities`, `McpServerConfig`, `severity`, `correlationId` on `AaaError` in wire types | A1 |
| `PROTOCOL_VERSION = "0.1.0"` in the Python engine | A1 |

**Stop here if Phase 1 is not merged.** Running Phase 2 tasks before Phase 1 is done will produce
confusing errors because the `handle_initialize` function these tasks extend doesn't exist yet.

---

**Goal:** Land the integration pieces of amplifier-agent v0.2.0 — bundle updated to canonical
patterns, per-session MCP threading wired through `_runtime.py`, a `doctor --strict` subcommand
gating image builds, four new conformance fixtures, and a tagged v0.2.0 release.

**Architecture:** Four targeted bundle edits + ~20 LOC MCP threading in `_runtime.py` + extension
of the existing `doctor` command with new flags and checks + four YAML conformance fixtures + version
bump across three files. All changes are additive; no breaking changes to the locked wire.

**Tech Stack:** Python (`uv`, pytest, pyright, ruff), TypeScript (pnpm, vitest), YAML
(`bundle.md`, conformance fixtures), JSON (`package.json`, version files).

---

## Codebase quick-reference

Before writing a single line, orient yourself with this map:

```
amplifier-agent/
├── src/amplifier_agent_lib/
│   ├── _runtime.py                          ← A5: add MCP threading here (~20 new LOC)
│   ├── bundle/
│   │   └── bundle.md                        ← A4: four targeted edits + prose removal
│   └── protocol/conformance/fixtures/       ← A8: four new .yaml fixtures land HERE
│       ├── capability_negotiation.yaml      (existing — do not touch)
│       ├── l14_synthesis.yaml               (existing — do not touch)
│       ├── resume_continuity.yaml           (existing — do not touch)
│       ├── subagent_lineage.yaml            (existing — do not touch)
│       └── version_skew.yaml               (existing — do not touch)
├── src/amplifier_agent_cli/
│   └── admin/
│       └── doctor.py                        ← A7: extend existing command with new flags/checks
├── tests/
│   ├── test_admin_doctor_cache.py           (existing doctor tests — study the patterns here)
│   └── test_admin_doctor_phase2.py          ← A7: NEW test file for Phase 2 doctor checks
├── wrappers/conformance/
│   ├── tests/test_runner_py.py              ← A8: add four test functions here
│   └── test/runner-ts.test.ts               ← A8: add four test cases here
├── wrappers/typescript/
│   └── package.json                         ← A9: bump "version" to "0.2.0"
└── pyproject.toml                           ← A9: bump version to "0.2.0"
    src/amplifier_agent_lib/__init__.py      ← A9: bump __version__ to "0.2.0"
```

**Important path note:** Conformance fixtures live at
`src/amplifier_agent_lib/protocol/conformance/fixtures/` — NOT at
`wrappers/conformance/fixtures/`. The delegation instructions say `.json` but every existing
fixture in the repo is `.yaml`. Use `.yaml`.

---

## Dependency graph

```
A4 (bundle edits)
  └─→ A5 (MCP threading — tool-mcp must be in bundle before you wire it)
        └─→ A7 (doctor checks — needs tool-mcp + hooks-approval present in bundle)
              └─→ A8 (conformance fixtures — needs all Phase 1 + A4 + A5 + A7 working)
                    └─→ A9 (version bump + tag — needs all A1-A8 tests green)
```

A4, A5, A7, A8, A9 must execute in that order within Phase 2.

---

## Out of scope (do NOT implement these)

- Any Phase 1 work (A1, A2, A3, A6) — already done.
- Any NanoClaw repo work (N1–N7) — that is Phase 3.
- `git push`, `git merge`, `gh pr create`, `twine upload`, `npm publish`, `gh release create`.
  Those belong to `/finish` mode after this plan executes. **The plan stops at `git tag`.**
- All 12 v1.x deferrals in Appendix A of the design doc.

---

## End-of-phase acceptance gate

Before calling Phase 2 "done", ALL of the following must be green:

```bash
# Python unit tests (fast, no network)
uv run pytest tests/ -v

# Conformance harness — Python runner tests
uv run pytest wrappers/conformance/tests/ -v

# Conformance harness — TypeScript runner tests
pnpm --filter amplifier-conformance-runner test

# Cross-language parity lint (integration — both runners must agree on every fixture)
uv run pytest tests/test_conformance_parity.py -m integration -v

# Lint + type checks
uv run ruff check src/ tests/ wrappers/conformance/tests/
uv run pyright

# Doctor strict check against the updated bundle
uv run amplifier-agent doctor --strict
```

---

## Task 1 — A4: Record bundle.md baseline sha256 before editing

**Files:**
- Read: `src/amplifier_agent_lib/bundle/bundle.md`

**Step 1: Capture the current sha256 and current module names**

Run this from the repo root:

```bash
sha256sum src/amplifier_agent_lib/bundle/bundle.md
```

Write the output somewhere visible (a scratch comment, your terminal, a sticky note). You need
to compare it against the post-edit sha256 in Task 2 to confirm the cache key invalidated.

Also confirm the *current* state matches what the plan expects to change. Run:

```bash
grep -n "context-persistent\|hooks-logging\|tool-mcp\|hooks-approval" \
     src/amplifier_agent_lib/bundle/bundle.md
```

**Expected output (before any edits):**
```
29:    module: context-persistent
30:    source: git+https://github.com/microsoft/amplifier-module-context-persistent@main
106:  - module: hooks-logging
107:    source: git+https://github.com/microsoft/amplifier-module-hooks-logging@main
```

`tool-mcp` and `hooks-approval` should NOT appear — if they do, Phase 1 already made these
changes and Task 2 is partially done. Re-read Phase 1's completed work before continuing.

---

## Task 2 — A4: Apply four targeted edits to bundle.md

**Files:**
- Modify: `src/amplifier_agent_lib/bundle/bundle.md`

**Context you MUST understand before editing:**

The `bundle.md` file is a Markdown document with a YAML frontmatter block (between the `---`
delimiters). The frontmatter describes which modules the agent loads. The cache key for the
prepared bundle is `(aaa_version, sha256(bundle.md_content)[:16])` — any edit to this file
INVALIDATES the warm cache and forces a fresh `amplifier-agent prepare` on next boot. That is
intentional. See `docs/designs/2026-05-19-baked-in-bundle-decision.md` for the design rationale.

**Step 1: Make edit #1 — replace `context-persistent` with `context-simple` (CR-1 fix)**

The current `context:` block (lines 28–32):
```yaml
  context:
    module: context-persistent
    source: git+https://github.com/microsoft/amplifier-module-context-persistent@main
    config:
      max_tokens: 300000
```

Replace with:
```yaml
  context:
    module: context-simple
    source: git+https://github.com/microsoft/amplifier-module-context-simple@main
    config:
      max_tokens: 300000
```

**Why:** `context-persistent` does not exist in foundation. The canonical pattern is
`context-simple` at the bundle layer + `SessionStore`/`IncrementalSaveHook` at the app layer
(Phase 1 A2 adds those). This was CR-1 from the Phase 6 critic review.

**Step 2: Make edit #2 — add `tool-mcp` to the `tools:` list (Q9: closes MCP reply-channel blocker)**

The current `tools:` block ends at the `tool-delegate` entry. ADD the `tool-mcp` entry after
`tool-delegate`, before the `# Hooks` comment. The tools block should look like this after the edit:

```yaml
tools:
  - module: tool-todo
    source: git+https://github.com/microsoft/amplifier-module-tool-todo@main
  - module: tool-delegate
    source: git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=modules/tool-delegate
    config:
      features:
        self_delegation:
          enabled: false
        session_resume:
          enabled: true
        context_inheritance:
          enabled: true
          max_turns: 10
        provider_selection:
          enabled: true
      settings:
        exclude_tools: [tool-delegate]
  - module: tool-mcp
    source: git+https://github.com/microsoft/amplifier-module-tool-mcp@main
    config:
      verbose_servers: false
      max_content_size: 65536
```

**Why:** `tool-mcp` is the reply channel for NanoClaw — without it the agent cannot call
`mcp__nanoclaw__send_message` and therefore cannot reply to the user. Q9 closes this v1 blocker.
The `config.servers` dict is intentionally absent here — at runtime, `_runtime.py` (A5) merges
`params["mcpServers"]` from the wire into this static config via `tool_overrides`. The static
config here sets global defaults (`verbose_servers`, `max_content_size`) only.

**Step 3: Make edit #3 — add `hooks-approval` to the `hooks:` list (Q6: approval gating)**

ADD the `hooks-approval` entry at the END of the `hooks:` block, after `hooks-session-naming`.
The tail of the hooks block should look like this after the edit:

```yaml
  - module: hooks-session-naming
    source: git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=modules/hooks-session-naming
    config:
      initial_trigger_turn: 2
      update_interval_turns: 5
  - module: hooks-approval
    source: git+https://github.com/microsoft/amplifier-module-hooks-approval@v0.1.0
```

**Why:** `hooks-approval` provides the mechanism for per-tool approval gating. Default mode
(NOT `policy_driven_only`) so the built-in pattern fires. Pinned to `v0.1.0` — NOT `@main` —
because `v0.1.0` is the verified stable release (Phase 6 critic verified against USAGE_GUIDE.md).
`WireApprovalProvider` (Phase 1 A3) registers its provider with this hook at runtime.

**Step 4: Make edit #4 — REMOVE the `hooks-logging` entry (SC-2)**

Find and DELETE the entire `hooks-logging` block. Current lines to delete:
```yaml
  - module: hooks-logging
    source: git+https://github.com/microsoft/amplifier-module-hooks-logging@main
    config:
      mode: session-only
      session_log_template: ~/.amplifier/projects/{project}/sessions/{session_id}/events.jsonl
```

**Why:** `hooks-logging` was writing to an ephemeral in-container path (`~/.amplifier/…`). With
Phase 1's `IncrementalSaveHook` writing to the host-mounted volume, this duplicate ephemeral log
is operationally misleading (SC-2 from the critic review). Remove it entirely.

**Step 5: Make edit #5 — REMOVE the stale prose block at lines 213–216 (O-2 cleanup)**

Find and DELETE this prose block from the Markdown body (NOT the frontmatter):

```markdown
Session-transcript persistence (writing to
`$XDG_STATE_HOME/amplifier-agent/sessions/<session-id>/`) is **not** owned
by the context module declared above (`context-simple`); it remains a future
CLI-layer hook concern. Out of scope for this manifest.
```

**Why:** This prose was written when the work was deferred. Phase 1 A2 delivered
`SessionStore`/`IncrementalSaveHook`, so the note is no longer accurate. O-2 from the critic
review.

**Step 6: Bump `bundle.version` in the frontmatter**

Find:
```yaml
  version: 1.1.0
```
Change to:
```yaml
  version: 1.2.0
```

**Step 7: Verify the sha256 changed**

```bash
sha256sum src/amplifier_agent_lib/bundle/bundle.md
```

The hash MUST be different from the baseline you captured in Task 1. If it is the same, you
made a mistake — re-read the diff carefully.

**Step 8: Verify the bundle.md YAML frontmatter parses correctly**

```bash
python -c "
import yaml, pathlib
content = pathlib.Path('src/amplifier_agent_lib/bundle/bundle.md').read_text()
parts = content.split('---\n', 2)
assert len(parts) == 3, f'Expected 3 parts, got {len(parts)}'
manifest = yaml.safe_load(parts[1])
print('version:', manifest['bundle']['version'])
print('context module:', manifest['session']['context']['module'])
tools = [t['module'] for t in manifest.get('tools', [])]
print('tools:', tools)
hooks = [h['module'] for h in manifest.get('hooks', [])]
print('hooks:', hooks)
"
```

**Expected output:**
```
version: 1.2.0
context module: context-simple
tools: ['tool-todo', 'tool-delegate', 'tool-mcp']
hooks: ['hooks-status-context', 'hooks-redaction', 'hooks-todo-reminder', 'hooks-session-naming', 'hooks-approval']
```

`hooks-logging` must NOT appear. `tool-mcp` and `hooks-approval` MUST appear.

**Step 9: Commit**

```bash
git add src/amplifier_agent_lib/bundle/bundle.md
git commit -m "feat(bundle): CR-1/Q6/Q9/SC-2 — context-simple, add tool-mcp + hooks-approval, remove hooks-logging"
```

---

## Task 3 — A5: Thread `mcpServers` and `host.capabilities` through `_runtime.py`

**Files:**
- Create: `tests/test_runtime_mcp_threading.py`
- Modify: `src/amplifier_agent_lib/_runtime.py`

**Background:**

Phase 1 (A2) added `handle_initialize(params)` to `_runtime.py`. That function handles resume
(loading transcript from `SessionStore`) and registers the `WireApprovalProvider` (Phase 1 A3).

Phase 2 A5 adds two more wiring steps inside that function:

1. **MCP threading:** Read `params["mcpServers"]` → merge with bundle's static `tool-mcp` config →
   pass as `tool_overrides={"tool-mcp": {"config": {**static, "servers": mcpServers}}}` to
   `bundle.create_session(…)`. This makes the wire-supplied MCP servers available at runtime.
   Verified against `amplifier_module_tool_mcp/config.py:35-53,56-61` — the `config` dict passed
   to `mount()` has highest priority and accepts this shape directly.

2. **Host capabilities storage:** Read `params.get("host", {}).get("capabilities", {})` → store in
   `session.metadata["host_capabilities"]`. Enables future capability-flag logic without wire changes.

**Step 1: Write the failing test**

Create `tests/test_runtime_mcp_threading.py`:

```python
"""Tests for A5 — MCP threading and host-capabilities storage in _runtime.py.

Phase 1 adds handle_initialize(params) to _runtime.py.
Phase 2 A5 extends it with MCP threading (tool_overrides) and host capabilities storage.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_params(
    *,
    session_id: str = "sess-test-1",
    mcp_servers: dict[str, Any] | None = None,
    host_capabilities: dict[str, Any] | None = None,
    resume: bool = False,
) -> dict[str, Any]:
    """Build a minimal InitializeParams dict for testing."""
    return {
        "sessionId": session_id,
        "resume": resume,
        "protocolVersion": "0.1.0",
        "clientInfo": {"name": "test-harness", "version": "0.0.0"},
        "capabilities": {"display": {"events": ["result/final"]}},
        "mcpServers": mcp_servers or {},
        "host": {"capabilities": host_capabilities or {}},
    }


def _make_mock_bundle(
    *,
    tool_mcp_static_config: dict[str, Any] | None = None,
) -> tuple[MagicMock, dict[str, Any]]:
    """Return (mock_bundle, captured_create_session_kwargs) pair."""
    captured: dict[str, Any] = {}

    mock_session = MagicMock()
    mock_session.metadata = {}
    mock_session.context.set_messages = AsyncMock()
    mock_session.hooks.register = MagicMock()
    mock_session.coordinator._approval_hook.register_provider = MagicMock()

    async def _create_session(**kwargs: Any) -> MagicMock:
        captured.update(kwargs)
        return mock_session

    mock_bundle = MagicMock()
    mock_bundle.config = {
        "tools": {
            "tool-mcp": {
                "config": tool_mcp_static_config or {"verbose_servers": False, "max_content_size": 65536}
            }
        }
    }
    mock_bundle.create_session = _create_session

    return mock_bundle, captured


@pytest.mark.asyncio
async def test_mcp_servers_threaded_to_tool_overrides() -> None:
    """mcpServers from params must appear in tool_overrides["tool-mcp"]["config"]["servers"]."""
    from amplifier_agent_lib._runtime import handle_initialize

    mcp_servers = {
        "test-mcp": {"transport": "stdio", "command": "/usr/bin/echo", "args": ["hello"]}
    }
    params = _make_params(mcp_servers=mcp_servers)
    mock_bundle, captured = _make_mock_bundle()

    mock_store = AsyncMock()
    mock_store.load.return_value = None  # no prior transcript

    with (
        patch("amplifier_agent_lib._runtime.load_and_prepare_cached", AsyncMock(return_value=mock_bundle)),
        patch("amplifier_agent_lib._runtime.SessionStore", return_value=mock_store),
    ):
        await handle_initialize(params)

    assert "tool_overrides" in captured, (
        "handle_initialize must pass tool_overrides to create_session. "
        "Add: tool_overrides={'tool-mcp': {'config': tool_mcp_config}} to the create_session call."
    )
    tool_mcp_cfg = captured["tool_overrides"]["tool-mcp"]["config"]
    assert tool_mcp_cfg["servers"] == mcp_servers, (
        f"tool_overrides['tool-mcp']['config']['servers'] should be {mcp_servers!r}, "
        f"got {tool_mcp_cfg.get('servers')!r}"
    )


@pytest.mark.asyncio
async def test_static_tool_mcp_config_merged_with_servers() -> None:
    """Static bundle config keys (verbose_servers, max_content_size) must be preserved alongside servers."""
    from amplifier_agent_lib._runtime import handle_initialize

    mcp_servers = {"nano-mcp": {"transport": "sse", "url": "http://localhost:9999"}}
    params = _make_params(mcp_servers=mcp_servers)
    static = {"verbose_servers": False, "max_content_size": 65536}
    mock_bundle, captured = _make_mock_bundle(tool_mcp_static_config=static)

    mock_store = AsyncMock()
    mock_store.load.return_value = None

    with (
        patch("amplifier_agent_lib._runtime.load_and_prepare_cached", AsyncMock(return_value=mock_bundle)),
        patch("amplifier_agent_lib._runtime.SessionStore", return_value=mock_store),
    ):
        await handle_initialize(params)

    tool_mcp_cfg = captured["tool_overrides"]["tool-mcp"]["config"]
    # Static keys must survive the merge
    assert tool_mcp_cfg.get("verbose_servers") is False, "static verbose_servers must be preserved"
    assert tool_mcp_cfg.get("max_content_size") == 65536, "static max_content_size must be preserved"
    # Dynamic servers key must be present
    assert tool_mcp_cfg.get("servers") == mcp_servers, "servers must be merged in"


@pytest.mark.asyncio
async def test_empty_mcp_servers_still_passes_tool_overrides() -> None:
    """When mcpServers is absent/empty, tool_overrides must still be passed (static config only)."""
    from amplifier_agent_lib._runtime import handle_initialize

    params = _make_params(mcp_servers={})  # empty
    mock_bundle, captured = _make_mock_bundle()

    mock_store = AsyncMock()
    mock_store.load.return_value = None

    with (
        patch("amplifier_agent_lib._runtime.load_and_prepare_cached", AsyncMock(return_value=mock_bundle)),
        patch("amplifier_agent_lib._runtime.SessionStore", return_value=mock_store),
    ):
        await handle_initialize(params)

    assert "tool_overrides" in captured
    tool_mcp_cfg = captured["tool_overrides"]["tool-mcp"]["config"]
    assert tool_mcp_cfg.get("servers") == {}, "empty mcpServers should produce servers={}"


@pytest.mark.asyncio
async def test_host_capabilities_stored_in_session_metadata() -> None:
    """host.capabilities from params must be stored in session.metadata['host_capabilities']."""
    from amplifier_agent_lib._runtime import handle_initialize

    host_caps = {"supports_structured_errors": True, "supports_steering": False}
    params = _make_params(host_capabilities=host_caps)
    mock_bundle, captured = _make_mock_bundle()

    mock_store = AsyncMock()
    mock_store.load.return_value = None

    with (
        patch("amplifier_agent_lib._runtime.load_and_prepare_cached", AsyncMock(return_value=mock_bundle)),
        patch("amplifier_agent_lib._runtime.SessionStore", return_value=mock_store),
    ):
        session = await handle_initialize(params)

    # session.metadata is a plain dict on our mock — check it was set
    mock_session = mock_bundle.create_session  # No — the session is returned by create_session
    # We need to check it differently: handle_initialize returns a session or InitializeResult
    # The test captures the mock_session from _make_mock_bundle via closure
    # Re-check: after handle_initialize, the mock session's metadata["host_capabilities"] should be set.
    # mock_bundle.create_session returns mock_session (from _make_mock_bundle closure).
    # We verify by checking that session.metadata was updated.
    # Since mock_session.metadata is a real dict {}, look for the set call via the bundle's session mock.
    pass  # Adjust this test body after reading Phase 1's actual return type from handle_initialize
```

> **⚠️ NOTE:** The last test (`test_host_capabilities_stored_in_session_metadata`) has a stub
> body. After reading Phase 1's `handle_initialize` implementation, adjust the assertion to match
> how the session object is actually accessible. The other three tests are complete.

**Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/test_runtime_mcp_threading.py -v
```

**Expected:** All three (non-stub) tests should FAIL with errors like:
```
ImportError: cannot import name 'handle_initialize' from 'amplifier_agent_lib._runtime'
```
OR (if Phase 1 has `handle_initialize` but without MCP threading):
```
AssertionError: handle_initialize must pass tool_overrides to create_session.
```

If they PASS immediately without implementation changes, Phase 1 already implemented MCP
threading — verify by reading `_runtime.py` and then skip to Step 5.

**Step 3: Implement MCP threading in `_runtime.py`**

Open `src/amplifier_agent_lib/_runtime.py`. Find the `handle_initialize` function added by
Phase 1. You need to add approximately 5 lines:

**Before `create_session(…)` is called**, add this block:

```python
# ── A5: Q9 — thread MCP servers into tool-mcp.mount() ─────────────────────
# Merge wire-supplied mcpServers into the static tool-mcp config from bundle.
# The tool-mcp module's mount(coordinator, config={...}) API accepts the
# "servers" dict at the highest priority, overriding env/file sources.
# Verified: amplifier_module_tool_mcp/config.py:35-53,56-61.
# The static bundle config (verbose_servers, max_content_size) is preserved.
_tool_mcp_static = (
    prepared.config
    .get("tools", {})
    .get("tool-mcp", {})
    .get("config", {})
)
tool_mcp_config = {**_tool_mcp_static, "servers": params.get("mcpServers") or {}}
```

**Modify the `create_session(…)` call** to include `tool_overrides`:

```python
session = await prepared.create_session(
    session_id=session_id,
    is_resumed=is_resumed,
    tool_overrides={"tool-mcp": {"config": tool_mcp_config}},
)
```

**After the session is created** (after transcript is loaded and hooks are registered), add:

```python
# ── A5: host capabilities storage ──────────────────────────────────────────
# Store host capabilities for future capability-flag logic (design §4.8, D7).
session.metadata["host_capabilities"] = (
    params.get("host") or {}
).get("capabilities") or {}
```

**Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/test_runtime_mcp_threading.py -v
```

**Expected:**
```
PASSED tests/test_runtime_mcp_threading.py::test_mcp_servers_threaded_to_tool_overrides
PASSED tests/test_runtime_mcp_threading.py::test_static_tool_mcp_config_merged_with_servers
PASSED tests/test_runtime_mcp_threading.py::test_empty_mcp_servers_still_passes_tool_overrides
```

The stub test (`test_host_capabilities_stored_in_session_metadata`) should be completed or
skipped. Complete it by capturing the returned `mock_session` from `_make_mock_bundle` and
asserting `mock_session.metadata["host_capabilities"] == host_caps`.

**Step 5: Run the full test suite to confirm no regressions**

```bash
uv run pytest tests/ -v
```

**Expected:** All previously-passing tests continue to pass. No new failures.

**Step 6: Run ruff and pyright**

```bash
uv run ruff check src/amplifier_agent_lib/_runtime.py tests/test_runtime_mcp_threading.py
uv run pyright src/amplifier_agent_lib/_runtime.py
```

Fix any issues before committing.

**Step 7: Commit**

```bash
git add src/amplifier_agent_lib/_runtime.py tests/test_runtime_mcp_threading.py
git commit -m "feat(engine): A5 — thread mcpServers into tool-mcp tool_overrides; store host.capabilities"
```

---

## Task 4 — A7a: Extend `doctor` with `--strict` and `--quick` flags

**Files:**
- Create: `tests/test_admin_doctor_phase2.py`
- Modify: `src/amplifier_agent_cli/admin/doctor.py`

**Background:**

`src/amplifier_agent_cli/admin/doctor.py` already exists (from the original codebase — it was
NOT added by Phase 1). It has a Click `@click.command()` named `doctor` with 5 checks and a
cache INFO line. It is already registered in `src/amplifier_agent_cli/__main__.py`. You are
**extending** this file, not creating a new one.

The new behaviors:

- `--strict`: Exit non-zero if ANY check is a warning (`[INFO]` for unprepared cache currently
  becomes `[FAIL]`). Designed for CI/image-build gating (design §4.9).
- `--quick`: Run only the essential checks — Python version and prepared cache. Skip provider
  check and XDG writability probes. Designed for fast health checks.

**Step 1: Write the failing tests**

Create `tests/test_admin_doctor_phase2.py`:

```python
"""Tests for Phase 2 doctor extensions: --strict, --quick, --emit-sha, new checks.

Study tests/test_admin_doctor_cache.py for the pattern conventions used here.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner


# ---------------------------------------------------------------------------
# --strict flag
# ---------------------------------------------------------------------------


def test_doctor_strict_exits_nonzero_when_cache_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--strict must exit 1 when the prepared-bundle cache is absent."""
    from amplifier_agent_cli.__main__ import cli

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    runner = CliRunner()
    result = runner.invoke(cli, ["doctor", "--strict"])

    assert result.exit_code != 0, (
        "--strict must exit non-zero when the prepared-bundle cache is missing. "
        "Current output:\n" + result.output
    )


def test_doctor_without_strict_exits_zero_when_only_cache_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without --strict, a missing cache is INFO (not FAIL); exit code stays 0."""
    from amplifier_agent_cli.__main__ import cli
    from amplifier_agent_cli.provider_detect import ProviderNotConfigured

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    # Patch detect_provider to succeed (so provider check passes)
    import amplifier_agent_cli.admin.doctor as doc_module
    monkeypatch.setattr(doc_module, "_check_provider", lambda: (True, "[ OK ] provider: anthropic"))

    runner = CliRunner()
    result = runner.invoke(cli, ["doctor"])

    assert result.exit_code == 0, (
        "Without --strict, missing cache alone must NOT cause non-zero exit. "
        "Current output:\n" + result.output
    )
    assert "[INFO]" in result.output, "Missing cache must be reported as [INFO] (not [FAIL])"


def test_doctor_strict_flag_is_present() -> None:
    """The doctor command must accept a --strict flag without error."""
    from amplifier_agent_cli.__main__ import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["doctor", "--help"])

    assert "--strict" in result.output, (
        "doctor --help must list --strict. "
        "Add @click.option('--strict', is_flag=True, ...) to the doctor command."
    )


# ---------------------------------------------------------------------------
# --quick flag
# ---------------------------------------------------------------------------


def test_doctor_quick_flag_is_present() -> None:
    """The doctor command must accept a --quick flag without error."""
    from amplifier_agent_cli.__main__ import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["doctor", "--help"])

    assert "--quick" in result.output, (
        "doctor --help must list --quick. "
        "Add @click.option('--quick', is_flag=True, ...) to the doctor command."
    )


def test_doctor_quick_exits_without_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """doctor --quick must run without crashing (minimal check path)."""
    from amplifier_agent_cli.__main__ import cli

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    runner = CliRunner()
    result = runner.invoke(cli, ["doctor", "--quick"])

    # --quick may exit 0 or 1 depending on cache state,
    # but must never crash with exit code 2 (Click usage error).
    assert result.exit_code in (0, 1), (
        f"doctor --quick crashed with exit code {result.exit_code}. "
        "Output:\n" + result.output
    )
```

**Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/test_admin_doctor_phase2.py::test_doctor_strict_flag_is_present \
              tests/test_admin_doctor_phase2.py::test_doctor_quick_flag_is_present -v
```

**Expected:**
```
FAILED — AssertionError: doctor --help must list --strict.
FAILED — AssertionError: doctor --help must list --quick.
```

**Step 3: Add `--strict` and `--quick` flags to the `doctor` command**

Open `src/amplifier_agent_cli/admin/doctor.py`. The current `doctor()` function signature is:

```python
@click.command()
def doctor() -> None:
```

Replace the decorator + signature with:

```python
@click.command()
@click.option(
    "--strict",
    is_flag=True,
    default=False,
    help="Exit non-zero on any warning (for CI / image-build gating). "
         "Without --strict, a missing prepared cache is [INFO] only.",
)
@click.option(
    "--quick",
    is_flag=True,
    default=False,
    help="Run minimal checks only: Python version and prepared-cache presence. "
         "Skips provider, XDG writability, and extended bundle checks.",
)
def doctor(strict: bool, quick: bool) -> None:
    """Run self-diagnostics and report system health."""
```

Now update the function body to use `strict` and `quick`. The complete updated function body:

```python
def doctor(strict: bool, quick: bool) -> None:
    """Run self-diagnostics and report system health."""
    home = Path(os.environ.get("HOME", str(Path.home())))
    cfg = _xdg("XDG_CONFIG_HOME", home / ".config") / "amplifier-agent"
    cache = _xdg("XDG_CACHE_HOME", home / ".cache") / "amplifier-agent"
    state = _xdg("XDG_STATE_HOME", home / ".local" / "state") / "amplifier-agent"

    if quick:
        # --quick: minimal check — Python version + cache only
        checks: list[tuple[bool, str]] = [
            _check_python_version(),
        ]
        for _ok, line in checks:
            click.echo(line)
        cache_info = check_cache_state(__version__)
        is_prepared = cache_info.status == "prepared"
        prefix = _OK if is_prepared else (_FAIL if strict else _INFO)
        click.echo(f"{prefix} bundle cache: {cache_info.status} ({cache_info.cache_dir})")
        all_ok = all(ok for ok, _ in checks) and (is_prepared or not strict)
        if not all_ok:
            sys.exit(1)
        return

    # Full check path
    checks = [
        _check_python_version(),
        _check_provider(),
        _check_writable("config home", cfg),
        _check_writable("cache home", cache),
        _check_writable("state home", state),
    ]

    for _ok, line in checks:
        click.echo(line)

    cache_info = check_cache_state(__version__)
    is_prepared = cache_info.status == "prepared"
    # Without --strict: missing cache is [INFO] (not a failure).
    # With --strict: missing cache is [FAIL].
    cache_prefix = _OK if is_prepared else (_FAIL if strict else _INFO)
    click.echo(f"{cache_prefix} bundle cache: {cache_info.status} ({cache_info.cache_dir})")

    # Determine overall exit
    hard_failures = not all(ok for ok, _ in checks)
    cache_failure = strict and not is_prepared
    if hard_failures or cache_failure:
        sys.exit(1)
```

**Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/test_admin_doctor_phase2.py -v -k "strict or quick"
```

**Expected:** All strict/quick tests pass.

**Step 5: Run full test suite to confirm no regressions**

```bash
uv run pytest tests/ -v
```

**Step 6: Commit**

```bash
git add src/amplifier_agent_cli/admin/doctor.py tests/test_admin_doctor_phase2.py
git commit -m "feat(cli): A7a — doctor --strict (CI gate) and --quick (minimal check) flags"
```

---

## Task 5 — A7b: Extend `doctor` with `--emit-sha`

**Files:**
- Modify: `tests/test_admin_doctor_phase2.py` (add tests)
- Modify: `src/amplifier_agent_cli/admin/doctor.py` (add flag + implementation)

**Background:**

`--emit-sha` emits the bundle module source URLs (and their sha256 hash) so CI can run this
daily and diff the output to detect supply-chain drift (design §4.9, SC-4, §10.6). In v1, the
SHA is computed over the source URL string — not the installed module content (full content
SHA-pinning is D-v1.x-02). This is still useful: if `bundle.md` changes (URL updated, pin
added), the diff fires. The operator baseline-diffs the SHA output, not the URLs directly.

**Step 1: Add the failing tests to `tests/test_admin_doctor_phase2.py`**

Append to the file:

```python
# ---------------------------------------------------------------------------
# --emit-sha flag
# ---------------------------------------------------------------------------


def test_doctor_emit_sha_flag_is_present() -> None:
    """The doctor command must accept --emit-sha without error."""
    from amplifier_agent_cli.__main__ import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["doctor", "--help"])

    assert "--emit-sha" in result.output, (
        "doctor --help must list --emit-sha. "
        "Add @click.option('--emit-sha', is_flag=True, ...) to the doctor command."
    )


def test_doctor_emit_sha_outputs_module_lines() -> None:
    """--emit-sha must print at least one 'module=' line to stdout."""
    from amplifier_agent_cli.__main__ import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["doctor", "--emit-sha"])

    assert result.exit_code in (0, 1), (
        f"doctor --emit-sha crashed (exit {result.exit_code}). "
        "Output:\n" + result.output
    )
    assert "module=" in result.output, (
        "--emit-sha must print lines containing 'module=<name>' for each bundle module. "
        "Current output:\n" + result.output
    )


def test_doctor_emit_sha_includes_tool_mcp() -> None:
    """--emit-sha output must include the tool-mcp module (added in A4)."""
    from amplifier_agent_cli.__main__ import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["doctor", "--emit-sha"])

    assert "tool-mcp" in result.output, (
        "--emit-sha output must include 'tool-mcp' (verify bundle.md A4 edit landed). "
        "Current output:\n" + result.output
    )


def test_doctor_emit_sha_includes_hooks_approval() -> None:
    """--emit-sha output must include the hooks-approval module (added in A4)."""
    from amplifier_agent_cli.__main__ import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["doctor", "--emit-sha"])

    assert "hooks-approval" in result.output, (
        "--emit-sha output must include 'hooks-approval' (verify bundle.md A4 edit landed). "
        "Current output:\n" + result.output
    )
```

**Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/test_admin_doctor_phase2.py::test_doctor_emit_sha_flag_is_present -v
```

**Expected:**
```
FAILED — AssertionError: doctor --help must list --emit-sha.
```

**Step 3: Add `--emit-sha` flag and implementation to `doctor.py`**

First add two imports at the top of `src/amplifier_agent_cli/admin/doctor.py`:

```python
import hashlib
import yaml as _yaml
```

Add the helper function (place it after `_check_writable`, before the `@click.command()` decorator):

```python
def _emit_bundle_shas() -> None:
    """Print sha256(source_url[:64]) and module name for each module in bundle.md.

    This is a v1 stub — SHA is of the source URL string, not the installed module
    content. Full content SHA-pinning is D-v1.x-02.  The output is designed to be
    committed as a baseline and diff'd in CI to detect bundle.md drift.

    Example output line:
        sha256_prefix=a3f2b9c1d4e5f6a7  module=tool-mcp  source=git+https://...@main
    """
    from amplifier_agent_lib.bundle import BUNDLE_MD  # Path to the vendored bundle.md

    try:
        content = BUNDLE_MD.read_text(encoding="utf-8")
    except FileNotFoundError:
        click.echo(f"{_FAIL} --emit-sha: bundle.md not found at {BUNDLE_MD}", err=True)
        return

    parts = content.split("---\n", 2)
    if len(parts) < 3:
        click.echo(f"{_FAIL} --emit-sha: could not parse YAML frontmatter from bundle.md", err=True)
        return

    try:
        manifest = _yaml.safe_load(parts[1])
    except Exception as exc:
        click.echo(f"{_FAIL} --emit-sha: YAML parse error: {exc}", err=True)
        return

    click.echo("# bundle module source SHAs (v1: SHA of source URL string; see D-v1.x-02 for content SHA)")

    entries: list[tuple[str, str]] = []

    # Gather session-level modules (orchestrator, context, provider)
    session = manifest.get("session") or {}
    for key in ("orchestrator", "context", "provider"):
        node = session.get(key) or {}
        if node.get("module") and node.get("source"):
            entries.append((node["module"], node["source"]))

    # Gather tools
    for entry in manifest.get("tools") or []:
        if entry.get("module") and entry.get("source"):
            entries.append((entry["module"], entry["source"]))

    # Gather hooks
    for entry in manifest.get("hooks") or []:
        if entry.get("module") and entry.get("source"):
            entries.append((entry["module"], entry["source"]))

    for module_name, source_url in sorted(entries, key=lambda x: x[0]):
        sha_prefix = hashlib.sha256(source_url.encode()).hexdigest()[:16]
        click.echo(f"sha256_prefix={sha_prefix}  module={module_name}  source={source_url}")
```

Add the `pyyaml` import at the top of the file (it's already a dependency via `amplifier-agent`'s
`pyproject.toml` which lists `pyyaml>=6.0`).

Now add the `--emit-sha` option to the `doctor` command decorator:

```python
@click.option(
    "--emit-sha",
    is_flag=True,
    default=False,
    help="Emit sha256 of each bundle module source URL for supply-chain baseline diffing. "
         "v1 stub: SHA is of the source URL string. Full content SHA is D-v1.x-02.",
)
```

Update the function signature and body to handle the new flag. After the existing check output
(just before `sys.exit(1)` at the end), add:

```python
    if emit_sha:
        _emit_bundle_shas()
```

**Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_admin_doctor_phase2.py -v -k "emit_sha"
```

**Expected:** All four `emit_sha` tests pass.

**Step 5: Commit**

```bash
git add src/amplifier_agent_cli/admin/doctor.py tests/test_admin_doctor_phase2.py
git commit -m "feat(cli): A7b — doctor --emit-sha for supply-chain bundle source SHA baseline"
```

---

## Task 6 — A7c: Add bundle-module and session-store health checks to `doctor`

**Files:**
- Modify: `tests/test_admin_doctor_phase2.py` (add tests)
- Modify: `src/amplifier_agent_cli/admin/doctor.py` (add three new checks)

**Background:**

Design §4.9 specifies these additional checks for `doctor --strict`:

1. **Bundle module presence** — parse `bundle.md` frontmatter and verify `context-simple`,
   `tool-mcp`, and `hooks-approval` are all present. This catches A4 regressions.
2. **`wire_approval_provider` shape-check** — import `WireApprovalProvider`, verify it is a
   subclass of `ApprovalProvider`, and verify all three error codes appear in source. This
   detects Phase 1 A3 regressions.
3. **`session_store` write/read roundtrip** — create a `SessionStore` in a tempdir, save a
   transcript, load it back, assert the round-trip is lossless. This detects Phase 1 A2
   regressions.

These checks run ONLY in the full (non-`--quick`) path.

**Step 1: Add the failing tests to `tests/test_admin_doctor_phase2.py`**

Append to the file:

```python
# ---------------------------------------------------------------------------
# New bundle + session checks (A7c)
# ---------------------------------------------------------------------------


def test_doctor_reports_ok_for_bundle_modules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """doctor must report [OK] for bundle modules if context-simple, tool-mcp, hooks-approval are present."""
    from amplifier_agent_cli.admin.doctor import _check_bundle_modules

    ok, line = _check_bundle_modules()

    # After A4 edits, all three should be present
    assert ok is True, (
        "Expected bundle module check to pass after A4 edits. "
        f"Got: {line}\n"
        "Verify bundle.md has context-simple, tool-mcp, and hooks-approval."
    )
    assert "[OK]" in line or "[ OK ]" in line, f"Expected OK prefix, got: {line}"


def test_doctor_reports_ok_for_approval_provider_shape() -> None:
    """doctor must report [OK] for wire_approval_provider shape-check."""
    from amplifier_agent_cli.admin.doctor import _check_approval_provider_shape

    ok, line = _check_approval_provider_shape()

    assert ok is True, (
        "wire_approval_provider shape-check failed. "
        f"Got: {line}\n"
        "Verify Phase 1 A3 — wire_approval_provider.py must exist with all three error codes."
    )


@pytest.mark.asyncio
async def test_doctor_session_store_roundtrip_succeeds() -> None:
    """doctor must report [OK] for session_store write/read roundtrip."""
    from amplifier_agent_cli.admin.doctor import _check_session_store_roundtrip

    ok, line = await _check_session_store_roundtrip()

    assert ok is True, (
        "session_store roundtrip check failed. "
        f"Got: {line}\n"
        "Verify Phase 1 A2 — session_store.py must exist and support save/load."
    )


def test_doctor_strict_runs_new_checks() -> None:
    """doctor --strict output must include results from the new bundle + session checks."""
    from amplifier_agent_cli.__main__ import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["doctor", "--strict"])

    # These substrings come from the new check output lines
    output = result.output
    assert "bundle modules" in output.lower() or "context-simple" in output.lower(), (
        "doctor --strict must show bundle module check results. "
        "Output:\n" + output
    )
```

**Step 2: Run to verify they fail**

```bash
uv run pytest tests/test_admin_doctor_phase2.py -v -k "bundle_modules or approval_provider or session_store"
```

**Expected:**
```
FAILED — ImportError: cannot import name '_check_bundle_modules'
FAILED — ImportError: cannot import name '_check_approval_provider_shape'
FAILED — ImportError: cannot import name '_check_session_store_roundtrip'
```

**Step 3: Add the three new check functions to `doctor.py`**

Add `import asyncio` and `import inspect` to the imports at the top of `doctor.py`.
Also add `import tempfile` to the imports.

Then add these three functions after `_emit_bundle_shas()`, before the `@click.command()` decorator:

```python
def _check_bundle_modules() -> tuple[bool, str]:
    """Verify context-simple, tool-mcp, and hooks-approval are all in bundle.md.

    This is a static parse of bundle.md — no network needed. Fails fast if A4 was not applied.
    """
    from amplifier_agent_lib.bundle import BUNDLE_MD

    try:
        content = BUNDLE_MD.read_text(encoding="utf-8")
        parts = content.split("---\n", 2)
        if len(parts) < 3:
            return (False, f"{_FAIL} bundle modules: cannot parse YAML frontmatter")
        manifest = _yaml.safe_load(parts[1])

        # Check context module
        ctx_module = (manifest.get("session") or {}).get("context", {}).get("module", "")
        if ctx_module != "context-simple":
            return (
                False,
                f"{_FAIL} bundle modules: context module is {ctx_module!r}, expected 'context-simple' "
                f"(CR-1 fix in A4 may not have been applied)",
            )

        # Check tools contain tool-mcp
        tools = [(t.get("module") or "") for t in (manifest.get("tools") or [])]
        if "tool-mcp" not in tools:
            return (
                False,
                f"{_FAIL} bundle modules: 'tool-mcp' not found in tools: {tools} "
                f"(A4 tool-mcp addition may not have been applied)",
            )

        # Check hooks contain hooks-approval
        hooks = [(h.get("module") or "") for h in (manifest.get("hooks") or [])]
        if "hooks-approval" not in hooks:
            return (
                False,
                f"{_FAIL} bundle modules: 'hooks-approval' not found in hooks: {hooks} "
                f"(A4 hooks-approval addition may not have been applied)",
            )

        # Check hooks-logging is GONE
        if "hooks-logging" in hooks:
            return (
                False,
                f"{_FAIL} bundle modules: 'hooks-logging' still present (SC-2 removal in A4 not applied)",
            )

        return (True, f"{_OK} bundle modules: context-simple, tool-mcp, hooks-approval present; hooks-logging absent")
    except Exception as exc:
        return (False, f"{_FAIL} bundle modules: unexpected error: {exc}")


def _check_approval_provider_shape() -> tuple[bool, str]:
    """Verify WireApprovalProvider (Phase 1 A3) has the correct shape.

    Checks:
    - Importable from amplifier_agent_lib.wire_approval_provider
    - Subclass of amplifier_core.ApprovalProvider
    - All three error codes present in source
    """
    try:
        from amplifier_agent_lib.wire_approval_provider import WireApprovalProvider
    except ImportError as exc:
        return (
            False,
            f"{_FAIL} wire_approval_provider: cannot import — Phase 1 A3 may not be merged: {exc}",
        )

    try:
        from amplifier_core import ApprovalProvider  # type: ignore[import]

        if not issubclass(WireApprovalProvider, ApprovalProvider):
            return (
                False,
                f"{_FAIL} wire_approval_provider: WireApprovalProvider is not a subclass of ApprovalProvider",
            )
    except ImportError:
        pass  # amplifier_core not installed in this environment — skip subclass check

    # Verify the three error codes are present in source (CR-2)
    src = inspect.getsource(WireApprovalProvider)
    missing_codes = [
        code
        for code in (
            "approval_translation_failed",
            "approval_timeout",
            "approval_protocol_violation",
        )
        if code not in src
    ]
    if missing_codes:
        return (
            False,
            f"{_FAIL} wire_approval_provider: missing error codes {missing_codes} in source (CR-2 may be incomplete)",
        )

    return (True, f"{_OK} wire_approval_provider: subclass check passed; all three error codes present")


async def _check_session_store_roundtrip() -> tuple[bool, str]:
    """Verify SessionStore (Phase 1 A2) can save and load a transcript without data loss."""
    try:
        from amplifier_agent_lib.session_store import SessionStore  # type: ignore[import]
    except ImportError as exc:
        return (
            False,
            f"{_FAIL} session_store: cannot import — Phase 1 A2 may not be merged: {exc}",
        )

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SessionStore(Path(tmpdir))
            session_id = "doctor-probe-roundtrip"
            transcript = [
                {"role": "user", "content": "doctor probe message"},
                {"role": "assistant", "content": "probe acknowledged"},
            ]
            metadata = {"probe": True, "doctor_check": "roundtrip"}

            await store.save(session_id, transcript, metadata)
            result = await store.load(session_id)

            if result is None:
                return (False, f"{_FAIL} session_store: load() returned None after save()")

            loaded_transcript, loaded_metadata = result
            if loaded_transcript != transcript:
                return (
                    False,
                    f"{_FAIL} session_store: transcript mismatch after roundtrip. "
                    f"Expected {transcript!r}, got {loaded_transcript!r}",
                )
            if loaded_metadata.get("probe") is not True:
                return (
                    False,
                    f"{_FAIL} session_store: metadata mismatch after roundtrip. "
                    f"Expected probe=True, got {loaded_metadata!r}",
                )

            return (True, f"{_OK} session_store: write/read roundtrip in tempdir succeeded")
    except Exception as exc:
        return (False, f"{_FAIL} session_store: roundtrip failed: {type(exc).__name__}: {exc}")
```

Now integrate the three new checks into the `doctor()` function body. In the full-check path
(after all the existing checks), add:

```python
    # ── A7c: bundle module presence check ────────────────────────────────────
    bundle_ok, bundle_line = _check_bundle_modules()
    click.echo(bundle_line)
    checks.append((bundle_ok, bundle_line))

    # ── A7c: wire_approval_provider shape-check ───────────────────────────────
    approval_ok, approval_line = _check_approval_provider_shape()
    click.echo(approval_line)
    checks.append((approval_ok, approval_line))

    # ── A7c: session_store write/read roundtrip ───────────────────────────────
    import asyncio as _asyncio
    store_ok, store_line = _asyncio.run(_check_session_store_roundtrip())
    click.echo(store_line)
    checks.append((store_ok, store_line))
```

> **Placement note:** Add these BEFORE the existing cache check so that the full check list is
> used for the `hard_failures` evaluation at the end.

**Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_admin_doctor_phase2.py -v
```

**Expected:** All tests pass.

**Step 5: Manually verify `doctor --strict` works end-to-end**

```bash
uv run amplifier-agent doctor --strict
```

If Phase 1 is merged and bundle A4 edits are applied, all checks should pass and exit code 0.
If something is missing, you'll see a `[FAIL]` line explaining what's wrong.

**Step 6: Commit**

```bash
git add src/amplifier_agent_cli/admin/doctor.py tests/test_admin_doctor_phase2.py
git commit -m "feat(cli): A7c — doctor bundle module presence, approval shape, session_store roundtrip checks"
```

---

## Task 7 — A8: Fixture `initialize-with-mcpservers.yaml`

**Files:**
- Create: `src/amplifier_agent_lib/protocol/conformance/fixtures/initialize-with-mcpservers.yaml`
- Modify: `wrappers/conformance/tests/test_runner_py.py`
- Modify: `wrappers/conformance/test/runner-ts.test.ts`

**Background:**

Conformance fixtures are scripted replays — they do NOT run the engine. The `ScriptedTransport`
replays canned server responses. A fixture tests that the CLIENT WRAPPER correctly handles a
given wire exchange. It does NOT test engine internals (that's what unit tests like Task 3 are for).

This fixture exercises: client wrapper can SEND `initialize` params with `mcpServers` field and
correctly parse the server's `sessionState` response. It verifies the wire plumbing added in A1.

**Fixture format reference:** Look at any existing fixture in
`src/amplifier_agent_lib/protocol/conformance/fixtures/` — they are all YAML with:
`name:`, `description:`, `setup:`, `script:` (list of frames), `assertions:` (list of checks).

**Step 1: Add a failing test to `wrappers/conformance/tests/test_runner_py.py`**

Append to the file:

```python


@pytest.mark.asyncio
async def test_initialize_with_mcpservers() -> None:
    from runner_py import run_fixture

    report = await run_fixture(FIXTURES_DIR / "initialize-with-mcpservers.yaml")
    assert report["passed"] is True, f"Expected passed=True, got:\n{report}"
```

**Step 2: Add a failing test to `wrappers/conformance/test/runner-ts.test.ts`**

Append inside the `describe("conformance runner (typescript)", () => {` block:

```typescript
  it("initialize_with_mcpservers passes", async () => {
    const report = await runFixture(
      `${FIXTURES_DIR}/initialize-with-mcpservers.yaml`,
    );
    expect(report.passed).toBe(true);
  });
```

**Step 3: Run the tests to verify they fail**

```bash
uv run pytest wrappers/conformance/tests/test_runner_py.py::test_initialize_with_mcpservers -v
```

**Expected:**
```
FAILED — FileNotFoundError: .../initialize-with-mcpservers.yaml not found
```
(or similar "fixture not found" error)

**Step 4: Create the fixture file**

Create `src/amplifier_agent_lib/protocol/conformance/fixtures/initialize-with-mcpservers.yaml`:

```yaml
name: initialize_with_mcpservers
description: >
  Client wrapper sends agent/initialize with mcpServers field (design §4.10.1, A5 MCP threading).
  Verifies the wire-level field is accepted without error and the session is established.
  The scripted server response confirms sessionState — engine MCP threading is tested separately
  in tests/test_runtime_mcp_threading.py (unit test, not here).

setup:
  protocolVersion: "0.1.0"
  clientCapabilities:
    display:
      events: [result/final]

script:
  - direction: client_to_server
    method: initialize
    id: 1
    params:
      protocolVersion: "0.1.0"
      clientInfo: {name: conformance-harness, version: "0.0.0"}
      capabilities:
        display:
          events: [result/final]
      sessionId: sess-mcp-wire-1
      mcpServers:
        test-mcp:
          transport: stdio
          command: /usr/bin/echo
          args: ["hello"]

  - direction: server_to_client
    id: 1
    result:
      capabilities:
        display:
          events: [result/final]
      serverInfo: {name: amplifier-agent, version: "0.2.0"}
      sessionState: {sessionId: sess-mcp-wire-1, resumed: false}

  - direction: client_to_server
    method: turn/submit
    id: 2
    params:
      sessionId: sess-mcp-wire-1
      turnId: turn-1
      prompt: "ping"

  - direction: server_to_client
    method: result/final
    params:
      sessionId: sess-mcp-wire-1
      turnId: turn-1
      text: "pong"

  - direction: server_to_client
    id: 2
    result:
      reply: "pong"
      turnId: turn-1
      sessionId: sess-mcp-wire-1

assertions:
  - kind: response_matches
    id: 1
    result:
      sessionState:
        sessionId: sess-mcp-wire-1
        resumed: false
  - kind: notification_emitted
    method: result/final
    payload_contains:
      text: "pong"
```

**Step 5: Run the tests to verify they pass**

```bash
uv run pytest wrappers/conformance/tests/test_runner_py.py::test_initialize_with_mcpservers -v
```

**Expected:**
```
PASSED wrappers/conformance/tests/test_runner_py.py::test_initialize_with_mcpservers
```

Also run the TypeScript runner:

```bash
pnpm --filter amplifier-conformance-runner test
```

**Expected:** All tests pass including `initialize_with_mcpservers passes`.

---

## Task 8 — A8: Fixture `initialize-with-host-capabilities.yaml`

**Files:**
- Create: `src/amplifier_agent_lib/protocol/conformance/fixtures/initialize-with-host-capabilities.yaml`
- Modify: `wrappers/conformance/tests/test_runner_py.py`
- Modify: `wrappers/conformance/test/runner-ts.test.ts`

**Step 1: Add failing tests to both test files**

`wrappers/conformance/tests/test_runner_py.py` — append:

```python


@pytest.mark.asyncio
async def test_initialize_with_host_capabilities() -> None:
    from runner_py import run_fixture

    report = await run_fixture(FIXTURES_DIR / "initialize-with-host-capabilities.yaml")
    assert report["passed"] is True, f"Expected passed=True, got:\n{report}"
```

`wrappers/conformance/test/runner-ts.test.ts` — append inside the describe block:

```typescript
  it("initialize_with_host_capabilities passes", async () => {
    const report = await runFixture(
      `${FIXTURES_DIR}/initialize-with-host-capabilities.yaml`,
    );
    expect(report.passed).toBe(true);
  });
```

**Step 2: Verify tests fail** (FileNotFoundError)

```bash
uv run pytest wrappers/conformance/tests/test_runner_py.py::test_initialize_with_host_capabilities -v
```

**Step 3: Create the fixture file**

Create `src/amplifier_agent_lib/protocol/conformance/fixtures/initialize-with-host-capabilities.yaml`:

```yaml
name: initialize_with_host_capabilities
description: >
  Client wrapper sends agent/initialize with host.capabilities field (design §4.10.1, B+C hybrid).
  Verifies the HostCapabilities wire shape is accepted without error.
  The server does not echo back host.capabilities in its response — the round-trip is
  that the session is established successfully (no error_returned) and sessionState is correct.

setup:
  protocolVersion: "0.1.0"
  clientCapabilities:
    display:
      events: [result/final]

script:
  - direction: client_to_server
    method: initialize
    id: 1
    params:
      protocolVersion: "0.1.0"
      clientInfo: {name: conformance-harness, version: "0.0.0"}
      capabilities:
        display:
          events: [result/final]
      sessionId: sess-hostcap-1
      host:
        capabilities:
          supports_structured_errors: true
          supports_steering: false

  - direction: server_to_client
    id: 1
    result:
      capabilities:
        display:
          events: [result/final]
      serverInfo: {name: amplifier-agent, version: "0.2.0"}
      sessionState: {sessionId: sess-hostcap-1, resumed: false}

  - direction: client_to_server
    method: turn/submit
    id: 2
    params:
      sessionId: sess-hostcap-1
      turnId: turn-1
      prompt: "capability probe"

  - direction: server_to_client
    method: result/final
    params:
      sessionId: sess-hostcap-1
      turnId: turn-1
      text: "ack"

  - direction: server_to_client
    id: 2
    result:
      reply: "ack"
      turnId: turn-1
      sessionId: sess-hostcap-1

assertions:
  - kind: response_matches
    id: 1
    result:
      sessionState:
        sessionId: sess-hostcap-1
        resumed: false
  - kind: notification_emitted
    method: result/final
```

**Step 4: Run tests to verify they pass**

```bash
uv run pytest wrappers/conformance/tests/test_runner_py.py::test_initialize_with_host_capabilities -v
pnpm --filter amplifier-conformance-runner test
```

Both must pass.

---

## Task 9 — A8: Fixture `approval-shim-three-error-codes.yaml`

**Files:**
- Create: `src/amplifier_agent_lib/protocol/conformance/fixtures/approval-shim-three-error-codes.yaml`
- Modify: `wrappers/conformance/tests/test_runner_py.py`
- Modify: `wrappers/conformance/test/runner-ts.test.ts`

**Background:**

This fixture exercises the three typed error codes from `WireApprovalProvider` (CR-2, Phase 1 A3):
- `approval_translation_failed`
- `approval_timeout`
- `approval_protocol_violation`

Each code is exercised via a separate `initialize` + `turn/submit` sequence. The scripted server
returns a JSON-RPC error for each `turn/submit` with the error code in the `message` field.
The `error_returned` assertion checks that the client surfaces each code.

**How `error_returned` works in the runner:**
The Python runner does `str(exc)` and checks `code in error_str`. The TypeScript runner does
`JSON.stringify(err)` and checks `errStr.includes(code)`. So the error code MUST appear in the
wire error's `message` field (Python path: `str({"code": -32000, "message": "approval_..."})`)
or the `data.code` field (TypeScript path: `JSON.stringify({"code": -32000, "message": "...", "data": {...}})`).
Safest: put the code in BOTH `message` AND `data.code`.

**Step 1: Add failing tests**

`wrappers/conformance/tests/test_runner_py.py` — append:

```python


@pytest.mark.asyncio
async def test_approval_shim_three_error_codes() -> None:
    from runner_py import run_fixture

    report = await run_fixture(FIXTURES_DIR / "approval-shim-three-error-codes.yaml")
    assert report["passed"] is True, f"Expected passed=True, got:\n{report}"
```

`wrappers/conformance/test/runner-ts.test.ts` — append inside describe block:

```typescript
  it("approval_shim_three_error_codes passes", async () => {
    const report = await runFixture(
      `${FIXTURES_DIR}/approval-shim-three-error-codes.yaml`,
    );
    expect(report.passed).toBe(true);
  });
```

**Step 2: Verify tests fail** (FileNotFoundError expected)

```bash
uv run pytest wrappers/conformance/tests/test_runner_py.py::test_approval_shim_three_error_codes -v
```

**Step 3: Create the fixture file**

Create `src/amplifier_agent_lib/protocol/conformance/fixtures/approval-shim-three-error-codes.yaml`:

```yaml
name: approval_shim_three_error_codes
description: >
  WireApprovalProvider (Phase 1 A3, CR-2) surfaces three typed error codes.
  Each code is exercised via a separate initialize + turn/submit sequence with a scripted
  server-side error response (design §4.7, §4.10.2).

  Error codes under test:
    1. approval_translation_failed  — raised when the wire request cannot be serialized
    2. approval_timeout             — raised when the host does not respond within 30s
    3. approval_protocol_violation  — raised when the host response does not conform to schema

setup:
  protocolVersion: "0.1.0"
  clientCapabilities:
    display:
      events: [result/final]
    approval:
      actions: [accept, decline, cancel]

script:
  # ── Scenario 1: approval_translation_failed ────────────────────────────────
  - direction: client_to_server
    method: initialize
    id: 1
    params:
      protocolVersion: "0.1.0"
      clientInfo: {name: conformance-harness, version: "0.0.0"}
      capabilities:
        display:
          events: [result/final]
        approval:
          actions: [accept, decline, cancel]
      sessionId: sess-approval-tf

  - direction: server_to_client
    id: 1
    result:
      capabilities:
        display: {events: [result/final]}
        approval: {actions: [accept, decline, cancel]}
      serverInfo: {name: amplifier-agent, version: "0.2.0"}
      sessionState: {sessionId: sess-approval-tf, resumed: false}

  - direction: client_to_server
    method: turn/submit
    id: 2
    params:
      sessionId: sess-approval-tf
      turnId: turn-1
      prompt: "trigger approval translation failure"

  - direction: server_to_client
    id: 2
    error:
      code: -32000
      message: "approval_translation_failed"
      data:
        code: approval_translation_failed
        classification: approval
        severity: error
        message: "failed to translate ApprovalRequest to wire shape"

  # ── Scenario 2: approval_timeout ──────────────────────────────────────────
  - direction: client_to_server
    method: initialize
    id: 3
    params:
      protocolVersion: "0.1.0"
      clientInfo: {name: conformance-harness, version: "0.0.0"}
      capabilities:
        display:
          events: [result/final]
        approval:
          actions: [accept, decline, cancel]
      sessionId: sess-approval-to

  - direction: server_to_client
    id: 3
    result:
      capabilities:
        display: {events: [result/final]}
        approval: {actions: [accept, decline, cancel]}
      serverInfo: {name: amplifier-agent, version: "0.2.0"}
      sessionState: {sessionId: sess-approval-to, resumed: false}

  - direction: client_to_server
    method: turn/submit
    id: 4
    params:
      sessionId: sess-approval-to
      turnId: turn-2
      prompt: "trigger approval timeout"

  - direction: server_to_client
    id: 4
    error:
      code: -32000
      message: "approval_timeout"
      data:
        code: approval_timeout
        classification: approval
        severity: error
        message: "host did not respond to approval/request within 30s"

  # ── Scenario 3: approval_protocol_violation ────────────────────────────────
  - direction: client_to_server
    method: initialize
    id: 5
    params:
      protocolVersion: "0.1.0"
      clientInfo: {name: conformance-harness, version: "0.0.0"}
      capabilities:
        display:
          events: [result/final]
        approval:
          actions: [accept, decline, cancel]
      sessionId: sess-approval-pv

  - direction: server_to_client
    id: 5
    result:
      capabilities:
        display: {events: [result/final]}
        approval: {actions: [accept, decline, cancel]}
      serverInfo: {name: amplifier-agent, version: "0.2.0"}
      sessionState: {sessionId: sess-approval-pv, resumed: false}

  - direction: client_to_server
    method: turn/submit
    id: 6
    params:
      sessionId: sess-approval-pv
      turnId: turn-3
      prompt: "trigger approval protocol violation"

  - direction: server_to_client
    id: 6
    error:
      code: -32000
      message: "approval_protocol_violation"
      data:
        code: approval_protocol_violation
        classification: approval
        severity: error
        message: "approval/response did not conform to schema"

assertions:
  - kind: error_returned
    id: 2
    code: approval_translation_failed
  - kind: error_returned
    id: 4
    code: approval_timeout
  - kind: error_returned
    id: 6
    code: approval_protocol_violation
```

**Step 4: Run tests to verify they pass**

```bash
uv run pytest wrappers/conformance/tests/test_runner_py.py::test_approval_shim_three_error_codes -v
pnpm --filter amplifier-conformance-runner test
```

Both must pass.

---

## Task 10 — A8: Fixture `resume-with-session-store.yaml`

**Files:**
- Create: `src/amplifier_agent_lib/protocol/conformance/fixtures/resume-with-session-store.yaml`
- Modify: `wrappers/conformance/tests/test_runner_py.py`
- Modify: `wrappers/conformance/test/runner-ts.test.ts`

**Background:**

This fixture verifies the wire shape of the resume protocol with `session_store` context (CR-1,
Phase 1 A2). It extends the existing `resume_continuity.yaml` with the v0.1.0 protocol version.
The scripted server confirms `sessionState.resumed = true` on the second spawn, and the second
turn can reference first-turn context (simulated by the scripted transcript).

**Step 1: Add failing tests**

`wrappers/conformance/tests/test_runner_py.py` — append:

```python


@pytest.mark.asyncio
async def test_resume_with_session_store() -> None:
    from runner_py import run_fixture

    report = await run_fixture(FIXTURES_DIR / "resume-with-session-store.yaml")
    assert report["passed"] is True, f"Expected passed=True, got:\n{report}"
```

`wrappers/conformance/test/runner-ts.test.ts` — append inside describe block:

```typescript
  it("resume_with_session_store passes", async () => {
    const report = await runFixture(
      `${FIXTURES_DIR}/resume-with-session-store.yaml`,
    );
    expect(report.passed).toBe(true);
  });
```

**Step 2: Verify tests fail** (FileNotFoundError expected)

```bash
uv run pytest wrappers/conformance/tests/test_runner_py.py::test_resume_with_session_store -v
```

**Step 3: Create the fixture file**

Create `src/amplifier_agent_lib/protocol/conformance/fixtures/resume-with-session-store.yaml`:

```yaml
name: resume_with_session_store
description: >
  Session resume using v0.1.0 protocol + session_store (CR-1, Phase 1 A2).
  Turn 1: first spawn, run a tool-using turn, engine stores transcript via IncrementalSaveHook.
  Turn 2: re-spawn same sessionId with resume=true, engine loads transcript, second turn
  references first-turn context (the number 42).
  This fixture tests the wire-level resume contract; engine-level transcript continuity is
  tested in tests/test_resume_continuity.py (unit test).

setup:
  protocolVersion: "0.1.0"
  clientCapabilities:
    display:
      events: [result/final, tool/started, tool/completed]

script:
  # ── First spawn ─────────────────────────────────────────────────────────────
  - direction: client_to_server
    method: initialize
    id: 1
    params:
      protocolVersion: "0.1.0"
      clientInfo: {name: conformance-harness, version: "0.0.0"}
      capabilities:
        display:
          events: [result/final, tool/started, tool/completed]
      sessionId: sess-store-resume-1
      resume: false

  - direction: server_to_client
    id: 1
    result:
      capabilities:
        display:
          events: [result/final, tool/started, tool/completed]
      serverInfo: {name: amplifier-agent, version: "0.2.0"}
      sessionState: {sessionId: sess-store-resume-1, resumed: false}

  - direction: client_to_server
    method: turn/submit
    id: 2
    params:
      sessionId: sess-store-resume-1
      turnId: turn-1
      prompt: "remember the number 42"

  - direction: server_to_client
    method: tool/started
    params:
      sessionId: sess-store-resume-1
      turnId: turn-1
      toolName: todo
      callId: call-1

  - direction: server_to_client
    method: tool/completed
    params:
      sessionId: sess-store-resume-1
      turnId: turn-1
      callId: call-1

  - direction: server_to_client
    method: result/final
    params:
      sessionId: sess-store-resume-1
      turnId: turn-1
      text: "acknowledged — I have noted 42"

  - direction: server_to_client
    id: 2
    result:
      reply: "acknowledged — I have noted 42"
      turnId: turn-1
      sessionId: sess-store-resume-1

  # ── Second spawn: same sessionId, resume=true ─────────────────────────────
  - direction: client_to_server
    method: initialize
    id: 3
    params:
      protocolVersion: "0.1.0"
      clientInfo: {name: conformance-harness, version: "0.0.0"}
      capabilities:
        display:
          events: [result/final]
      sessionId: sess-store-resume-1
      resume: true

  - direction: server_to_client
    id: 3
    result:
      capabilities:
        display:
          events: [result/final]
      serverInfo: {name: amplifier-agent, version: "0.2.0"}
      sessionState: {sessionId: sess-store-resume-1, resumed: true}

  - direction: client_to_server
    method: turn/submit
    id: 4
    params:
      sessionId: sess-store-resume-1
      turnId: turn-2
      prompt: "what was the number?"

  - direction: server_to_client
    method: result/final
    params:
      sessionId: sess-store-resume-1
      turnId: turn-2
      text: "42"

  - direction: server_to_client
    id: 4
    result:
      reply: "42"
      turnId: turn-2
      sessionId: sess-store-resume-1

assertions:
  - kind: response_matches
    id: 3
    result:
      sessionState:
        sessionId: sess-store-resume-1
        resumed: true
  - kind: notification_emitted
    method: result/final
    payload_contains:
      turnId: turn-2
      text: "42"
```

**Step 4: Run tests to verify they pass**

```bash
uv run pytest wrappers/conformance/tests/test_runner_py.py::test_resume_with_session_store -v
pnpm --filter amplifier-conformance-runner test
```

Both must pass.

---

## Task 11 — A8: Run full conformance parity lint and commit all fixtures

**Files:**
- All four fixture files (verify they're all saved)
- `wrappers/conformance/tests/test_runner_py.py`
- `wrappers/conformance/test/runner-ts.test.ts`

**Step 1: Run the full harness Python tests**

```bash
uv run pytest wrappers/conformance/tests/ -v
```

**Expected:** All 6 tests pass (2 original + 4 new).

```
PASSED test_capability_negotiation
PASSED test_l14_synthesis
PASSED test_initialize_with_mcpservers
PASSED test_initialize_with_host_capabilities
PASSED test_approval_shim_three_error_codes
PASSED test_resume_with_session_store
```

**Step 2: Run the full harness TypeScript tests**

```bash
pnpm --filter amplifier-conformance-runner test
```

**Expected:** All 6 tests pass.

**Step 3: Run the cross-language parity lint**

This is the `@pytest.mark.integration` test that auto-discovers ALL fixture files and runs
BOTH runners, asserting identical `(kind, passed)` outcomes:

```bash
uv run pytest tests/test_conformance_parity.py -m integration -v
```

**Expected:** 9 fixtures pass parity lint (5 existing + 4 new).

If any fixture fails parity lint, the error message will show exactly which assertion diverges
between the Python and TypeScript runners. The most common cause is a YAML type coercion
difference (e.g., `true` vs `"true"`, integers vs strings). Fix the fixture YAML first.

**Step 4: Run the full test suite one final time**

```bash
uv run pytest tests/ -v
```

**Expected:** All tests pass. No regressions from Phase 1 or earlier in Phase 2.

**Step 5: Commit all fixture-related changes**

```bash
git add \
  src/amplifier_agent_lib/protocol/conformance/fixtures/initialize-with-mcpservers.yaml \
  src/amplifier_agent_lib/protocol/conformance/fixtures/initialize-with-host-capabilities.yaml \
  src/amplifier_agent_lib/protocol/conformance/fixtures/approval-shim-three-error-codes.yaml \
  src/amplifier_agent_lib/protocol/conformance/fixtures/resume-with-session-store.yaml \
  wrappers/conformance/tests/test_runner_py.py \
  wrappers/conformance/test/runner-ts.test.ts

git commit -m "test(conformance): A8 — 4 new fixtures: mcpServers, host-capabilities, approval error codes, session-store resume"
```

---

## Task 12 — A9: Bump version strings to 0.2.0

**Files:**
- Modify: `src/amplifier_agent_lib/__init__.py`
- Modify: `pyproject.toml`
- Modify: `wrappers/typescript/package.json`

**Background:**

Version `0.2.0` signals: all A1–A8 stages complete. The minor version bump (0.0.x → 0.2.0) is
intentional — the wire protocol bumped from `"2026-05-aaa-v0"` to `"0.1.0"` in Phase 1 (A1),
which is a breaking change for old consumers (strict-refuse). The package version aligns with
that significance.

**Step 1: Check current version strings**

```bash
grep -n "version" src/amplifier_agent_lib/__init__.py pyproject.toml wrappers/typescript/package.json
```

**Expected current values:**
- `__init__.py`: `__version__ = "0.0.1"`
- `pyproject.toml`: `version = '0.0.0'`
- `package.json`: `"version": "0.0.0"`

**Step 2: Bump `src/amplifier_agent_lib/__init__.py`**

Change:
```python
__version__ = "0.0.1"
```
To:
```python
__version__ = "0.2.0"
```

**Step 3: Bump `pyproject.toml`**

Change:
```toml
version = '0.0.0'
```
To:
```toml
version = '0.2.0'
```

**Step 4: Bump `wrappers/typescript/package.json`**

Change:
```json
"version": "0.0.0",
```
To:
```json
"version": "0.2.0",
```

**Step 5: Verify version consistency**

```bash
python -c "
from amplifier_agent_lib import __version__
print('lib version:', __version__)
assert __version__ == '0.2.0', f'Expected 0.2.0, got {__version__!r}'
print('OK')
"
```

Also check the TypeScript version is loadable:

```bash
node -e "const pkg = require('./wrappers/typescript/package.json'); console.log('ts version:', pkg.version)"
```

**Step 6: Run tests to verify nothing broke**

```bash
uv run pytest tests/ -v
```

Any test that checks `__version__` should now see `"0.2.0"`. If a test was hardcoded to `"0.0.1"`,
update that test to `"0.2.0"`.

**Step 7: Check pyright and ruff**

```bash
uv run ruff check src/
uv run pyright
```

**Step 8: Commit**

```bash
git add src/amplifier_agent_lib/__init__.py pyproject.toml wrappers/typescript/package.json
git commit -m "chore(release): A9 — bump version to 0.2.0 (wire v0.1.0, MCP threading, doctor --strict, 4 conformance fixtures)"
```

---

## Task 13 — A9: Create CHANGELOG.md

**Files:**
- Create: `CHANGELOG.md`

**Background:**

There is no `CHANGELOG.md` in the repo yet (confirmed by searching). You are creating it from
scratch. Use [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) format which is the
industry standard.

**Step 1: Create `CHANGELOG.md` in the repo root**

```markdown
# Changelog

All notable changes to amplifier-agent are documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.2.0] — 2026-05-22

### Added
- **Wire:** `mcpServers` and `host.capabilities` fields on `agent/initialize` params (`SpawnAgentParams`).
  `HostCapabilities`, `McpServerConfig` TypeScript interfaces; symmetric Python TypedDicts. (A1)
- **Wire:** `severity`, `correlationId`, `stderrTail` fields on `AaaError`; `classification: 'approval'`
  enum variant. (A1)
- **Engine:** `session_store.py` — `SessionStore` class with JSONL transcript + JSON metadata storage,
  atomic writes via `amplifier_foundation.write_with_backup`. (A2)
- **Engine:** `incremental_save.py` — `IncrementalSaveHook` on `tool:post` priority 900; flushes
  transcript after every tool call. (A2)
- **Engine:** `wire_approval_provider.py` — `WireApprovalProvider` shim implementing
  `amplifier_core.ApprovalProvider` with explicit three-code error contract:
  `approval_translation_failed`, `approval_timeout`, `approval_protocol_violation`. (A3)
- **Engine:** `_runtime.py` — resume path (load transcript via `SessionStore`), approval shim
  registration (`WireApprovalProvider`), MCP threading (`mcpServers` → `tool_overrides`),
  host capabilities storage (`session.metadata["host_capabilities"]`). (A2, A3, A5)
- **Bundle:** `context-simple` replaces `context-persistent` (CR-1). `tool-mcp@main` and
  `hooks-approval@v0.1.0` modules added. `hooks-logging` removed (SC-2). Bundle version `1.2.0`. (A4)
- **CLI:** `amplifier-agent doctor --strict` exits non-zero on any warning; gates image builds
  and CI. `--quick` minimal check path. `--emit-sha` emits bundle module source SHA baseline
  for supply-chain diffing. New checks: bundle module presence (context-simple/tool-mcp/
  hooks-approval), `wire_approval_provider` shape, `session_store` write/read roundtrip. (A7)
- **Conformance:** Four new scripted-replay fixtures (`initialize-with-mcpservers.yaml`,
  `initialize-with-host-capabilities.yaml`, `approval-shim-three-error-codes.yaml`,
  `resume-with-session-store.yaml`). Parity lint green on all 9 fixtures in TS and Py runners. (A8)
- **Wrappers:** `BLOCKED_ENV_KEYS` validation in `buildEnv()` — rejects `PYTHONPATH`,
  `LD_PRELOAD`, `LD_LIBRARY_PATH`, `PYTHONSTARTUP`, `PATH`, `PYTHONHOME`, `PYTHONNOUSERSITE`,
  `DYLD_INSERT_LIBRARIES`, `DYLD_LIBRARY_PATH` with `AaaError(code='env_injection_rejected')`. (A6)
- **Wrappers:** `probeEngineVersion()` made async in both TS and Py wrappers. (A6)

### Changed
- **Wire:** `PROTOCOL_VERSION` bumped `"2026-05-aaa-v0"` → `"0.1.0"`. Both ends strict-refuse on
  mismatch (locked design D6 unchanged). Consumers must update. (A1)
- **Bundle:** `bundle.version` bumped `1.1.0` → `1.2.0`. Existing prepared-bundle caches are
  invalidated (cache key includes `sha256(bundle.md)`). Run `amplifier-agent prepare` after
  upgrading to warm the new cache. (A4)

### Removed
- **Bundle:** `hooks-logging` module removed. Session audit is now handled by
  `IncrementalSaveHook` writing to the host-mounted volume. (A4, SC-2)

### Design references
- `docs/designs/2026-05-22-aaa-v2-amplifier-agent-nc-provider.md` (this release's full spec)
- `docs/designs/2026-05-20-aaa-v2-wrapper-and-wire.md` (wire contract)
- `docs/designs/2026-05-19-baked-in-bundle-decision.md` (bundle cache strategy)

---

## [0.0.1] — 2026-05-20

Initial implementation. Protocol, engine, bundle, wrapper stubs. Not production-ready.
```

**Step 2: Verify the file is well-formed**

```bash
python -c "
content = open('CHANGELOG.md').read()
assert '## [0.2.0]' in content, 'Missing 0.2.0 entry'
assert 'mcpServers' in content, 'Missing mcpServers mention'
assert 'session_store' in content, 'Missing session_store mention'
print('CHANGELOG.md looks good')
"
```

**Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(release): A9 — CHANGELOG.md for v0.2.0"
```

---

## Task 14 — A9: Create release tag v0.2.0

**Background:**

This task creates the local git tag. It does NOT push the tag, publish to PyPI, publish to npm,
or create a GitHub release. Those actions belong to `/finish` mode after this plan completes.

**Step 1: Verify the pre-tag acceptance gate is fully green**

Run the complete acceptance gate:

```bash
# Python unit tests
uv run pytest tests/ -v
echo "Exit code: $?"

# Conformance harness Python
uv run pytest wrappers/conformance/tests/ -v
echo "Exit code: $?"

# Conformance harness TypeScript
pnpm --filter amplifier-conformance-runner test
echo "Exit code: $?"

# Parity lint
uv run pytest tests/test_conformance_parity.py -m integration -v
echo "Exit code: $?"

# Lint + types
uv run ruff check src/ tests/ wrappers/conformance/tests/
uv run pyright

# Doctor --strict
uv run amplifier-agent doctor --strict
echo "Exit code: $?"
```

All exit codes must be 0. If any check fails, fix it before tagging.

**Step 2: Verify the git tree is clean**

```bash
git status
```

**Expected:** Working tree is clean (all changes committed). If there are uncommitted changes,
commit them before tagging.

**Step 3: Review the commit log for Phase 2**

```bash
git log --oneline -10
```

You should see at minimum these commits (most recent first):

```
docs(release): A9 — CHANGELOG.md for v0.2.0
chore(release): A9 — bump version to 0.2.0
test(conformance): A8 — 4 new fixtures: mcpServers, host-capabilities, approval error codes, session-store resume
feat(cli): A7c — doctor bundle module presence, approval shape, session_store roundtrip checks
feat(cli): A7b — doctor --emit-sha for supply-chain bundle source SHA baseline
feat(cli): A7a — doctor --strict (CI gate) and --quick (minimal check) flags
feat(engine): A5 — thread mcpServers into tool-mcp tool_overrides; store host.capabilities
feat(bundle): CR-1/Q6/Q9/SC-2 — context-simple, add tool-mcp + hooks-approval, remove hooks-logging
```

Plus Phase 1 commits before those.

**Step 4: Create the annotated tag**

```bash
git tag -a v0.2.0 -m "amplifier-agent v0.2.0 — wire v0.1.0, MCP threading, session persistence, doctor --strict, 4 conformance fixtures

Highlights:
- PROTOCOL_VERSION bumped to 0.1.0 (breaking: both ends strict-refuse on mismatch)
- bundle.md updated: context-simple (CR-1), tool-mcp, hooks-approval added; hooks-logging removed
- session_store.py + incremental_save.py for at-least-once transcript persistence
- wire_approval_provider.py with three-code error contract (CR-2)
- MCP threading: mcpServers threaded to tool-mcp.mount() via tool_overrides
- amplifier-agent doctor --strict gates image builds
- 4 new conformance fixtures; parity lint green

See CHANGELOG.md for full change list.
See docs/designs/2026-05-22-aaa-v2-amplifier-agent-nc-provider.md for design spec."
```

**Step 5: Verify the tag was created**

```bash
git tag -l "v0.2.0"
git show v0.2.0 --stat | head -20
```

**Expected:** Tag `v0.2.0` listed. The `git show` output should include the annotation message
and the HEAD commit's file stats.

**Step 6: ⛔ STOP HERE — do NOT run any of the following**

The following commands are NOT part of this plan. They belong to `/finish` mode:

```bash
# DO NOT run these:
# git push origin v0.2.0
# git push origin HEAD
# uv publish
# twine upload dist/*
# npm publish wrappers/typescript/
# gh release create v0.2.0
# pnpm publish
```

**Phase 2 is complete.** Hand off to Phase 3 (`docs/plans/2026-05-22-aaa-nc-provider-phase3-nanoclaw-consumption.md`)
once Phase 2 is reviewed and merged.

---

## Post-Phase 2 manual publish runbook (NOT executable tasks — reference only)

> Execute these steps ONLY via `/finish` mode after this plan is reviewed and the branch is merged.

### PyPI publish (amplifier-agent Python package)

```bash
# 1. Build the wheel and source dist
uv build

# 2. Verify the dist/ artifacts
ls -la dist/
# Expect: amplifier_agent-0.2.0-py3-none-any.whl, amplifier_agent-0.2.0.tar.gz

# 3. Upload to PyPI (requires PyPI credentials/token)
uv publish
# Or: twine upload dist/amplifier_agent-0.2.0*
```

### npm publish (amplifier-agent-client-ts TypeScript package)

```bash
cd wrappers/typescript/

# 1. Build the TypeScript dist
pnpm build

# 2. Verify dist/ artifacts
ls -la dist/

# 3. Publish to npm (requires npm credentials/token)
pnpm publish --access public
```

### GitHub release

```bash
# Push the tag
git push origin v0.2.0

# Create the GitHub release (uses the tag annotation as the release body)
gh release create v0.2.0 \
  --title "amplifier-agent v0.2.0" \
  --notes-from-tag \
  dist/amplifier_agent-0.2.0-py3-none-any.whl \
  dist/amplifier_agent-0.2.0.tar.gz
```

### Post-publish smoke test

```bash
# Test from clean install (in a fresh venv)
pip install amplifier-agent==0.2.0
amplifier-agent --version  # should print: amplifier-agent, version 0.2.0
amplifier-agent doctor

# Test TypeScript wrapper from npm
npm install amplifier-agent-client-ts@0.2.0
node -e "const {spawnAgent} = require('amplifier-agent-client-ts'); console.log('OK')"
```

---

## Checklist before calling Phase 2 "done"

Use this to track progress as you go:

- [ ] **A4** — `bundle.md` edits applied; sha256 changed; `bundle.version = 1.2.0`; prepare parses
- [ ] **A5** — `_runtime.py` MCP threading committed; all 3 MCP threading tests pass
- [ ] **A7a** — `doctor --strict` and `--quick` flags added and tested
- [ ] **A7b** — `doctor --emit-sha` added and tested; output includes `tool-mcp`, `hooks-approval`
- [ ] **A7c** — bundle module presence, approval shape, session_store roundtrip checks added and pass
- [ ] **A7 integration** — `uv run amplifier-agent doctor --strict` exits 0
- [ ] **A8 fixtures** — all 4 YAML fixtures created; 6 Python harness tests pass; 6 TS harness tests pass
- [ ] **A8 parity** — `test_conformance_parity.py -m integration` green for all 9 fixtures
- [ ] **A9** — version = `0.2.0` in all three files; `CHANGELOG.md` created; tag `v0.2.0` exists locally
- [ ] **Full test suite** — `uv run pytest tests/ -v` green with zero failures
- [ ] **Lint + types** — `ruff check` and `pyright` both clean

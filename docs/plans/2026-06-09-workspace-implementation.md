# Workspace Identity, Resolution, and Migration Implementation Plan

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Wire the validated `workspace` identity contract into amplifier-agent: resolve a workspace slug from argv/env/cwd, write it (and its ecosystem `project_slug` alias) into `coordinator.config`, bucket session state under `<state_root>/workspaces/<workspace>/sessions/<id>/`, propagate the workspace to child coordinators in `spawn.py`, migrate the existing flat `sessions/<id>/` tree to a `_legacy` workspace on first post-upgrade boot, and add a cross-workspace resume fallback. Filesystem-only backend; the storage capability is explicitly deferred per the companion design.

**Architecture:** A new resolver function in `persistence.py` produces a validated slug from `(argv, env, cwd)`. `_runtime.py` calls it before `SessionStore` construction and writes both `workspace` and `project_slug` to `coordinator.config`. `spawn.py` inherits the parent's workspace into child coordinators verbatim — never re-derives from cwd. `session_store.py` constructor takes a per-workspace root; cross-workspace `load()` walks all workspaces as a fallback. A `migrate_legacy_sessions_if_needed()` helper runs at startup, guarded by `flock`, moving any pre-existing flat `sessions/<id>/` into `workspaces/_legacy/sessions/<id>/`. No bundle.md changes. No host-config schema changes.

**Tech Stack:** Python 3.11+, `uv` for dependency management, `pytest` + `pytest-asyncio`, `ruff` for lint, `pyright` for type checking, `click` for CLI parsing, all tooling invoked via `uv run`.

**Design source (read before any task):**
- `docs/designs/2026-06-09-workspace-resolution-and-migration.md` (D1–D10, I1–I7) — the load-bearing design. Committed in `690aa8e`.
- `docs/designs/2026-06-09-workspace-identity-and-storage-flexibility.md` (companion, extensibility/deferral analysis). Committed in `994f67b`.

**Plan size note:** 19 tasks grouped into five sections (A–E). The sections function as phases: land A before B (the resolver must exist before the hot path consumes it), B before C/D, and E (real-binary integration) last. Each task is a self-contained 2–5 minute TDD unit — an implementer reading only one task can complete it.

**Unified-layout amendment (workspace I8):** Per the design owner's directive — *"there shouldn't be two separate places to write things; the flat list should be gone"* — **every** per-session artifact follows the workspace tree, not just the transcript. Tasks **B4** (audit write path) and **B5** (`--fresh` cleanup) re-point the two remaining flat-path writers in `single_turn.py` onto `workspaces/<workspace>/sessions/<id>/`, and **E6** verifies the audit path end-to-end. This closes Open Question #2 (see the parent design's §13 entry for #2 and §10 invariant **I8**). The migrator (D1) already carries `audits/` subdirectories along verbatim because it moves the whole session directory tree.

---

## Pre-flight (before starting any task)

Verify the baseline and read the touch-points. Do **not** skip this — the plan's code blocks assume the structures below.

**Step 0a: Confirm a green baseline.**

```bash
cd /Users/mpaidiparthy/repos/amplifier-agent
git status                                # working tree clean on main
uv sync                                   # deps installed
uv run pytest -x -q 2>&1 | tail -20       # full suite GREEN
```

Expected: `passed` line at the bottom, exit code 0. If anything is red, STOP and fix before proceeding.

**Step 0b: Required reading.** Read each file end-to-end before the task that touches it:

1. `src/amplifier_agent_lib/persistence.py` — confirm `state_root()` (line 43), `cache_root()`, `config_root()`, `session_state_dir(session_id)` (line 63) signatures. Note `APP_NAME = "amplifier-agent"` and the `os.environ.get("XDG_STATE_HOME") or None` pattern (line 48). New helpers mirror that pattern.
2. `src/amplifier_agent_lib/_runtime.py:128-353` — current `make_turn_handler` flow. `SessionStore(state_root())` is constructed **inside** `handler` at line 231; `session = await prepared.create_session(...)` at line 251; capability registration on `session.coordinator` at lines 260-333.
3. `src/amplifier_agent_lib/spawn.py:440-496` — current parent→child capability propagation (`approval.request`, `display.emit` loop at lines 453-456). The workspace propagation lands alongside it.
4. `src/amplifier_agent_lib/session_store.py` — `SessionStore(root)` constructor (line 30); `session_dir(id)` returns `self.root / "sessions" / session_id` (line 35); `load()` shape (lines 51-73).
5. `src/amplifier_agent_lib/incremental_save.py` — confirms `IncrementalSaveHook` takes `store` (not a path), so it needs zero change.
6. `src/amplifier_agent_cli/modes/single_turn.py:392-520` — `_TurnSpec` dataclass (line 392), `_execute_turn` (line 413) calling `make_turn_handler` (line 433), the click options block (lines 478-503), and the `run` signature (line 504) + `_TurnSpec(...)` construction (line 615).
7. `tests/cli/test_mode_a_v2_real_binary.py` — the canonical real-binary + mock-LLM fixture (`mock_llm`, `_binary_path()`, `_sse_message`). The new e2e tests in Section E reuse this exact pattern.
8. `tests/conftest.py` — the two autouse approval fixtures. Subprocess e2e tests inherit `$AMPLIFIER_AGENT_CONFIG` with `approval.mode: yes`; do not fight it.
9. `docs/designs/2026-06-09-workspace-resolution-and-migration.md` — all D-decisions and I-invariants flow from it. **In particular §10 invariant I8** (unified per-session layout: every per-session artifact — transcript, metadata, audits — lives under `workspaces/<workspace>/sessions/<id>/`; the flat `sessions/<id>/` tree disappears after migration). I8 is what Tasks B4/B5/E6 enforce.
10. `docs/designs/2026-06-09-workspace-identity-and-storage-flexibility.md` — what is deferred (no `session.storage` capability) and why.
11. `docs/designs/2026-05-24-aaa-v2-mode-a-pivot-amendment.md` §A2.1'/SC-H — the per-turn audit file format and contents. The writer is `_write_audit` in `src/amplifier_agent_cli/modes/single_turn.py:248-285`; it currently computes `audits_dir = session_state_dir(session_id) / "audits"` (line 273) and writes `turn-<turn_id>.json` (line 284). Tasks B4/E6 re-point this path. The audit JSON fields (real schema, verified from source): `argvDigest`, `envDigest`, `protocolVersion`, `exitCode`, `correlationId`, `startedAt`, `endedAt`. (Note: the amendment's prose mentions `mcp_servers_digest`/env-allowlist digests, but those were removed in E1/E2/D10 — do not assert fields the current writer does not emit.)
12. `src/amplifier_agent_cli/modes/single_turn.py` — the two remaining flat-path writers that B4/B5 move: `_write_audit` (audit dir, line 273) and the `--fresh` cleanup in `_execute_turn` (`state_dir = session_state_dir(spec.session_id)`, line 429). Also read `_emit_argv_envelope` (line 159, default `exit_code=2`, classification `"protocol"`) — B4 uses it to fail fast on an invalid `--workspace` before booting the engine.

**Step 0c: Verify one assumption the design depends on.** The design (D5/D7) writes identity to `coordinator.config`. Confirm `session.coordinator.config` is a mutable mapping on the real `AmplifierSession`:

```bash
uv run python -c "import inspect, amplifier_core; print([n for n in dir(amplifier_core)])"
grep -rn "coordinator.config" src/ | head
```

If `coordinator.config` is **not** a writable mapping in this version of `amplifier_core`, STOP and surface it as an **Open question** to the design owner — do not invent an alternative attribute. Tasks B2, C1, and D3 depend on this being writable. (The design assumes it is, per D5/D7.)

---

## Section A — Persistence layer (no behavior change to hot path)

These four tasks add the resolver, validator, and derivation helpers to `persistence.py`. They land first so the resolver exists before `_runtime.py` consumes it. None of them touch the hot path.

All four tasks share one new test file: `tests/test_persistence_workspaces.py`. Task A1 creates it; A2–A4 append.

### Task A1: Add `workspaces_root()` helper to `persistence.py`

**Files:**
- Modify: `src/amplifier_agent_lib/persistence.py`
- Create: `tests/test_persistence_workspaces.py`

**Step 1: Write the failing test.**

Create `tests/test_persistence_workspaces.py`:

```python
"""Tests for the workspace resolution helpers in persistence.py.

Design: docs/designs/2026-06-09-workspace-resolution-and-migration.md (D1-D4).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from amplifier_agent_lib import persistence


def test_workspaces_root_under_state_root(monkeypatch, tmp_path: Path) -> None:
    """workspaces_root() == state_root() / 'workspaces', honouring XDG_STATE_HOME (D8)."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    assert persistence.workspaces_root() == tmp_path / "amplifier-agent" / "workspaces"
    # And it is exactly state_root() / "workspaces".
    assert persistence.workspaces_root() == persistence.state_root() / "workspaces"
```

**Step 2: Run the test, watch it fail.**

```bash
uv run pytest tests/test_persistence_workspaces.py::test_workspaces_root_under_state_root -v
```

Expected: `FAILED` — `AttributeError: module 'amplifier_agent_lib.persistence' has no attribute 'workspaces_root'`.

**Step 3: Implement.**

Edit `src/amplifier_agent_lib/persistence.py`. Add this helper right after `state_root()` (after line 50):

```python
def workspaces_root() -> Path:
    """Return the root that buckets session state by workspace (D8).

    Layout: ``<state_root>/workspaces/<workspace>/sessions/<session_id>/``.
    Pure path computation; never creates directories.
    """
    return state_root() / "workspaces"
```

**Step 4: Run the test, watch it pass.**

```bash
uv run pytest tests/test_persistence_workspaces.py -v
```

Expected: 1 PASS.

**Step 5: Commit.**

```bash
git add src/amplifier_agent_lib/persistence.py tests/test_persistence_workspaces.py
git commit -m "$(cat <<'EOF'
feat(persistence): add workspaces_root() helper (D8)

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>
EOF
)"
```

---

### Task A2: Add `WorkspaceError` + `SLUG_RE` + `validate_slug()`

**Files:**
- Modify: `src/amplifier_agent_lib/persistence.py`
- Modify: `tests/test_persistence_workspaces.py`

**Step 1: Write the failing tests.**

Append to `tests/test_persistence_workspaces.py`:

```python
def test_validate_slug_accepts_valid() -> None:
    """A conforming slug is returned unchanged (D3)."""
    assert persistence.validate_slug("acme-api") == "acme-api"
    assert persistence.validate_slug("a") == "a"
    assert persistence.validate_slug("group-7f3a9d2c") == "group-7f3a9d2c"


def test_validate_slug_rejects_uppercase() -> None:
    """Uppercase is not lowercase-normalized; it is rejected (D3)."""
    with pytest.raises(persistence.WorkspaceError):
        persistence.validate_slug("ACME")


def test_validate_slug_rejects_path_traversal() -> None:
    """Path-traversal is blocked at parse, before it can reach the filesystem (D3)."""
    with pytest.raises(persistence.WorkspaceError):
        persistence.validate_slug("../etc")
    with pytest.raises(persistence.WorkspaceError):
        persistence.validate_slug("a/b")


def test_validate_slug_rejects_underscore_prefix() -> None:
    """Leading '_' is reserved for AAA-internal workspaces (D3, I7)."""
    with pytest.raises(persistence.WorkspaceError):
        persistence.validate_slug("_legacy")


def test_validate_slug_rejects_too_long() -> None:
    """64+ chars exceed the filesystem-safe bound (D3)."""
    with pytest.raises(persistence.WorkspaceError):
        persistence.validate_slug("a" * 65)


def test_validate_slug_rejects_empty() -> None:
    """Empty is rejected by validate_slug itself; tier fall-through is the caller's job (D2)."""
    with pytest.raises(persistence.WorkspaceError):
        persistence.validate_slug("")
```

**Step 2: Run the tests, watch them fail.**

```bash
uv run pytest tests/test_persistence_workspaces.py -k validate_slug -v
```

Expected: 6 FAILED — `AttributeError: ... has no attribute 'WorkspaceError'` (and `validate_slug`).

**Step 3: Implement.**

Edit `src/amplifier_agent_lib/persistence.py`. Add `import re` to the top imports (after `import os`, line 10). Then add at module scope (after `APP_NAME`, line 15):

```python
# Workspace slug grammar (D3). Lowercase alphanumerics + hyphens, 1-64 chars,
# must start with [a-z0-9]. Leading '_' is reserved for AAA-internal
# workspaces (e.g. "_legacy", I7) and is therefore unreachable via this regex.
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class WorkspaceError(ValueError):
    """Raised when a workspace slug fails the D3 grammar."""


def validate_slug(value: str) -> str:
    """Return ``value`` if it matches the D3 slug grammar, else raise.

    Path-traversal (``..``, ``/``), uppercase, the reserved ``_`` prefix,
    over-length, and empty values are all rejected here, before the value
    can ever be joined into a filesystem path.
    """
    if not SLUG_RE.match(value):
        raise WorkspaceError(
            f"invalid workspace slug: {value!r}; "
            f"must match [a-z0-9][a-z0-9-]{{0,63}}"
        )
    return value
```

**Step 4: Run the tests, watch them pass.**

```bash
uv run pytest tests/test_persistence_workspaces.py -v
```

Expected: all PASS (7 total so far).

**Step 5: Commit.**

```bash
git add src/amplifier_agent_lib/persistence.py tests/test_persistence_workspaces.py
git commit -m "$(cat <<'EOF'
feat(persistence): add WorkspaceError + validate_slug (D3)

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>
EOF
)"
```

---

### Task A3: Add `slugify()` + `derive_workspace_from_cwd()`

**Files:**
- Modify: `src/amplifier_agent_lib/persistence.py`
- Modify: `tests/test_persistence_workspaces.py`

**Step 1: Write the failing tests.**

Append to `tests/test_persistence_workspaces.py`:

```python
def test_derive_workspace_is_stable() -> None:
    """Same cwd -> same slug across calls (D4, I5)."""
    cwd = Path("/Users/me/repos/amplifier-agent")
    first = persistence.derive_workspace_from_cwd(cwd)
    second = persistence.derive_workspace_from_cwd(cwd)
    assert first == second
    # The derived slug must itself be valid (constructed-valid invariant, D4).
    assert persistence.validate_slug(first) == first


def test_derive_workspace_disambiguates_same_basename() -> None:
    """Two absolute paths sharing a basename get different slugs (D4 hash suffix)."""
    a = persistence.derive_workspace_from_cwd(Path("/home/a/myproj"))
    b = persistence.derive_workspace_from_cwd(Path("/home/b/myproj"))
    assert a != b
    assert a.startswith("myproj-")
    assert b.startswith("myproj-")


def test_derive_workspace_handles_root() -> None:
    """'/' has an empty basename; falls back to 'default-<hash>' (D4)."""
    slug = persistence.derive_workspace_from_cwd(Path("/"))
    assert slug.startswith("default-")
    assert persistence.validate_slug(slug) == slug


def test_derive_workspace_handles_invalid_basename() -> None:
    """A basename with spaces/punctuation slugifies cleanly (D4)."""
    slug = persistence.derive_workspace_from_cwd(Path("/tmp/My Project!"))
    assert slug.startswith("my-project-")
    assert persistence.validate_slug(slug) == slug
```

**Step 2: Run the tests, watch them fail.**

```bash
uv run pytest tests/test_persistence_workspaces.py -k derive_workspace -v
```

Expected: 4 FAILED — `AttributeError: ... has no attribute 'derive_workspace_from_cwd'`.

**Step 3: Implement.**

Edit `src/amplifier_agent_lib/persistence.py`. Add `import hashlib` to the top imports (after `import os`). Then add (after `validate_slug`):

```python
def slugify(text: str) -> str:
    """Lowercase, collapse non-alphanumeric runs to '-', strip ends.

    Returns ``"default"`` for input that slugifies to empty.
    """
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return text or "default"


def derive_workspace_from_cwd(cwd: Path) -> str:
    """Derive a stable, valid workspace slug from a working directory (D4).

    Same cwd always produces the same slug (I5). An 8-char SHA256 of the
    resolved absolute path disambiguates same-basename repos. The result is
    valid by construction (slugify + 48-char bound + hash suffix), so the
    reserved ``_`` prefix is unreachable and no validate_slug call is needed.
    """
    basename = cwd.name or "default"
    slug_base = slugify(basename)[:48]
    cwd_hash = hashlib.sha256(str(cwd.resolve()).encode()).hexdigest()[:8]
    return f"{slug_base}-{cwd_hash}"
```

**Step 4: Run the tests, watch them pass.**

```bash
uv run pytest tests/test_persistence_workspaces.py -v
```

Expected: all PASS (11 total so far).

**Step 5: Commit.**

```bash
git add src/amplifier_agent_lib/persistence.py tests/test_persistence_workspaces.py
git commit -m "$(cat <<'EOF'
feat(persistence): add slugify + derive_workspace_from_cwd (D4)

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>
EOF
)"
```

---

### Task A4: Add `resolve_workspace()` — the top-level resolver

**Files:**
- Modify: `src/amplifier_agent_lib/persistence.py`
- Modify: `tests/test_persistence_workspaces.py`

**Step 1: Write the failing tests.**

Append to `tests/test_persistence_workspaces.py`:

```python
def test_resolve_workspace_argv_wins() -> None:
    """argv flag beats env and cwd (D2, first-hit-wins)."""
    result = persistence.resolve_workspace(
        argv_workspace="from-flag",
        env={"AMPLIFIER_AGENT_WORKSPACE": "from-env"},
        cwd=Path("/Users/me/repos/amplifier-agent"),
    )
    assert result == "from-flag"


def test_resolve_workspace_env_when_no_argv() -> None:
    """env is used when argv is absent (D2)."""
    result = persistence.resolve_workspace(
        argv_workspace=None,
        env={"AMPLIFIER_AGENT_WORKSPACE": "from-env"},
        cwd=Path("/Users/me/repos/amplifier-agent"),
    )
    assert result == "from-env"


def test_resolve_workspace_cwd_fallback() -> None:
    """With neither argv nor env, fall back to the cwd-derived slug (D2/D4)."""
    cwd = Path("/Users/me/repos/amplifier-agent")
    result = persistence.resolve_workspace(argv_workspace=None, env={}, cwd=cwd)
    assert result == persistence.derive_workspace_from_cwd(cwd)


def test_resolve_workspace_empty_argv_falls_through() -> None:
    """Empty argv string falls through to env, then cwd (D2)."""
    cwd = Path("/Users/me/repos/amplifier-agent")
    # Empty argv + empty/whitespace env -> cwd-derived.
    result = persistence.resolve_workspace(
        argv_workspace="",
        env={"AMPLIFIER_AGENT_WORKSPACE": "   "},
        cwd=cwd,
    )
    assert result == persistence.derive_workspace_from_cwd(cwd)


def test_resolve_workspace_invalid_argv_raises() -> None:
    """An explicit-but-invalid argv slug raises rather than silently falling through (D2/D3)."""
    with pytest.raises(persistence.WorkspaceError):
        persistence.resolve_workspace(
            argv_workspace="Bad Slug",
            env={},
            cwd=Path("/Users/me/repos/amplifier-agent"),
        )
```

**Step 2: Run the tests, watch them fail.**

```bash
uv run pytest tests/test_persistence_workspaces.py -k resolve_workspace -v
```

Expected: 5 FAILED — `AttributeError: ... has no attribute 'resolve_workspace'`.

**Step 3: Implement.**

Edit `src/amplifier_agent_lib/persistence.py`. Add `from collections.abc import Mapping` to the top imports. Then add (after `derive_workspace_from_cwd`):

```python
def resolve_workspace(
    argv_workspace: str | None,
    env: Mapping[str, str],
    cwd: Path,
) -> str:
    """Resolve the workspace identifier (D2). First non-empty hit wins.

    Order: argv flag > ``AMPLIFIER_AGENT_WORKSPACE`` env var > cwd-derived.
    Never returns None or empty. Explicit argv/env values are validated;
    the cwd-derived fallback is valid by construction (D4).
    """
    if argv_workspace:
        return validate_slug(argv_workspace)
    env_value = env.get("AMPLIFIER_AGENT_WORKSPACE", "").strip()
    if env_value:
        return validate_slug(env_value)
    return derive_workspace_from_cwd(cwd)
```

**Step 4: Run the tests, watch them pass.**

```bash
uv run pytest tests/test_persistence_workspaces.py -v
```

Expected: all PASS (16 total). Also run the existing persistence suite to confirm no regression:

```bash
uv run pytest tests/test_persistence.py -v
```

Expected: all PASS.

**Step 5: Commit.**

```bash
git add src/amplifier_agent_lib/persistence.py tests/test_persistence_workspaces.py
git commit -m "$(cat <<'EOF'
feat(persistence): add resolve_workspace top-level resolver (D2)

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>
EOF
)"
```

---

## Section B — Engine wire-up

Land second so the resolver is wired into the hot path. B1 adds the argv surface; B2 wires resolution into `_runtime`; B3 confirms the `SessionStore` per-workspace root contract. B4 re-points the per-turn audit writer onto the workspace tree; B5 re-points the `--fresh` cleanup onto the workspace tree (both enforce the unified layout, I8).

### Task B1: Add `--workspace` click option to `modes/single_turn.py`

**Files:**
- Modify: `src/amplifier_agent_cli/modes/single_turn.py`
- Create: `tests/cli/test_run_workspace_flag.py`

**Step 1: Write the failing tests.**

Create `tests/cli/test_run_workspace_flag.py`:

```python
"""The `run --workspace <slug>` argv surface (D1).

Spec: the flag parses cleanly when present and threads onto _TurnSpec; an
invalid slug surfaces a clean error envelope rather than a traceback.
"""

from __future__ import annotations

from unittest.mock import patch

from click.testing import CliRunner

from amplifier_agent_cli.__main__ import cli


def test_workspace_flag_accepted() -> None:
    """`run --workspace foo` parses and reaches _execute_turn with workspace='foo'."""
    captured: dict[str, object] = {}

    async def _fake_exec(spec):
        captured["workspace"] = spec.workspace
        return {"reply": "ok", "turnId": "turn-1", "sessionId": ""}

    runner = CliRunner()
    with patch("amplifier_agent_cli.modes.single_turn._execute_turn", _fake_exec):
        result = runner.invoke(
            cli,
            ["run", "--workspace", "foo", "hello"],
            env={"ANTHROPIC_API_KEY": "sk-test"},
        )

    assert result.exit_code == 0, result.output
    assert captured.get("workspace") == "foo"


def test_workspace_flag_invalid_format_errors_cleanly() -> None:
    """An invalid slug exits non-zero and does NOT leak a Python traceback."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["run", "--workspace", "Bad Slug", "hello"],
        env={"ANTHROPIC_API_KEY": "sk-test"},
    )

    assert result.exit_code != 0
    # The envelope/error path is exercised, not an unhandled WorkspaceError.
    assert "Traceback" not in result.output
```

**Step 2: Run the tests, watch them fail.**

```bash
uv run pytest tests/cli/test_run_workspace_flag.py -v
```

Expected: FAILED — `_TurnSpec` has no `workspace` attribute / `run` has no `--workspace` option (`captured` empty or click "no such option").

**Step 3: Implement.**

Edit `src/amplifier_agent_cli/modes/single_turn.py`:

1. Add a `workspace` field to `_TurnSpec` (after `host_config`, line 405):

```python
    workspace: str | None = None
```

2. Add the click option to the `run` command. Insert after the `--config` option (line 483):

```python
@click.option("--workspace", default=None, help="Workspace identifier for session bucketing (D1).")
```

3. Add `workspace: str | None,` to the `run` function signature (alongside `config_path`, around line 511).

4. Thread it into the `_TurnSpec(...)` construction (around line 615):

```python
        workspace=workspace,
```

Resolution + validation happens in `_runtime` (Task B2), not here — the flag is a pass-through string at this layer. The "invalid format errors cleanly" test passes because the `WorkspaceError` raised downstream during `_execute_turn` is caught by the existing `AaaError`/exception envelope path in `run` (verify by reading the `try/except` around `asyncio.run(_execute_turn(spec))` at lines 631-710). If `WorkspaceError` does **not** subclass an exception that envelope path catches, surface this as an **Open question** — do not broaden the except clause speculatively.

**Step 4: Run the tests, watch them pass.**

```bash
uv run pytest tests/cli/test_run_workspace_flag.py -v
uv run pytest tests/cli/test_single_turn.py -v
```

Expected: new tests PASS; existing single_turn tests still PASS.

**Step 5: Commit.**

```bash
git add src/amplifier_agent_cli/modes/single_turn.py tests/cli/test_run_workspace_flag.py
git commit -m "$(cat <<'EOF'
feat(cli): add --workspace flag to run, thread to _TurnSpec (D1)

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>
EOF
)"
```

---

### Task B2: Wire `resolve_workspace()` into `_runtime.make_turn_handler`

**Files:**
- Modify: `src/amplifier_agent_lib/_runtime.py`
- Modify: `src/amplifier_agent_cli/modes/single_turn.py`
- Create: `tests/test_runtime_workspace.py`

**Step 1: Write the failing tests.**

Create `tests/test_runtime_workspace.py`:

```python
"""_runtime wires resolve_workspace into the hot path (D5, D6, D8).

The handler must:
  - write coordinator.config["workspace"] and ["project_slug"] (both the alias)
  - construct SessionStore with root = state_root()/workspaces/<workspace>
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_agent_lib import _runtime
from amplifier_agent_lib.persistence import state_root


class _FakeContextModule:
    async def get_messages(self) -> list[dict[str, Any]]:
        return []


def _make_fake_session() -> SimpleNamespace:
    """A fake AmplifierSession exposing the surface the handler touches."""
    coordinator = SimpleNamespace(
        config={},
        hooks=SimpleNamespace(set_default_fields=lambda **kw: None, register=lambda *a, **k: None),
        register_capability=lambda *a, **k: None,
        get=lambda key: _FakeContextModule() if key == "context" else None,
    )
    session = MagicMock()
    session.coordinator = coordinator
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.execute = AsyncMock(return_value="reply-text")
    return session


def _make_prepared(fake_session) -> MagicMock:
    prepared = MagicMock()
    prepared.mount_plan = {"agents": {}}
    prepared.create_session = AsyncMock(return_value=fake_session)
    return prepared


@pytest.mark.asyncio
async def test_runtime_writes_workspace_to_coordinator_config(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    fake_session = _make_fake_session()
    prepared = _make_prepared(fake_session)

    handler = _runtime.make_turn_handler(
        prepared, cwd=None, is_resumed=False, host_config=None, workspace="test-ws"
    )
    ctx = SimpleNamespace(
        session_id="sid-1",
        turn_id="turn-1",
        prompt="hi",
        display=SimpleNamespace(emit=lambda *a, **k: None),
        approval=SimpleNamespace(request=lambda *a, **k: None),
    )
    await handler(ctx)

    assert fake_session.coordinator.config["workspace"] == "test-ws"


@pytest.mark.asyncio
async def test_runtime_writes_project_slug_alias(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    fake_session = _make_fake_session()
    prepared = _make_prepared(fake_session)

    handler = _runtime.make_turn_handler(
        prepared, cwd=None, is_resumed=False, host_config=None, workspace="test-ws"
    )
    ctx = SimpleNamespace(
        session_id="sid-1", turn_id="turn-1", prompt="hi",
        display=SimpleNamespace(emit=lambda *a, **k: None),
        approval=SimpleNamespace(request=lambda *a, **k: None),
    )
    await handler(ctx)

    assert fake_session.coordinator.config["project_slug"] == "test-ws"


@pytest.mark.asyncio
async def test_runtime_uses_per_workspace_session_store(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    captured_roots: list[Path] = []

    real_store_cls = _runtime.SessionStore

    def _spy_store(root: Path):
        captured_roots.append(root)
        return real_store_cls(root)

    monkeypatch.setattr(_runtime, "SessionStore", _spy_store)

    fake_session = _make_fake_session()
    prepared = _make_prepared(fake_session)
    handler = _runtime.make_turn_handler(
        prepared, cwd=None, is_resumed=False, host_config=None, workspace="test-ws"
    )
    ctx = SimpleNamespace(
        session_id="sid-1", turn_id="turn-1", prompt="hi",
        display=SimpleNamespace(emit=lambda *a, **k: None),
        approval=SimpleNamespace(request=lambda *a, **k: None),
    )
    await handler(ctx)

    assert captured_roots, "SessionStore was never constructed"
    assert captured_roots[0] == state_root() / "workspaces" / "test-ws"
```

**Step 2: Run the tests, watch them fail.**

```bash
uv run pytest tests/test_runtime_workspace.py -v
```

Expected: FAILED — `make_turn_handler` does not accept `workspace`.

> **Note on the fakes:** the `_FakeContextModule` / `_make_fake_session` shape mirrors the real surface `handler` touches (lines 251-351 of `_runtime.py`). If the handler reads an attribute the fake omits, extend the fake — do not change the handler to accommodate the test.

**Step 3: Implement.**

Edit `src/amplifier_agent_lib/_runtime.py`:

1. Add `workspace: str | None = None` to the `make_turn_handler` signature (after `host_config`, line 133):

```python
def make_turn_handler(
    prepared: PreparedBundle,
    *,
    cwd: str | None,
    is_resumed: bool,
    host_config: dict[str, Any] | None = None,
    workspace: str | None = None,
) -> TurnHandler:
```

2. Resolve the workspace once at handler-creation time (cold path), right after `resolved_cwd` is computed (after line 170):

```python
    # Resolve the workspace identity once (cold path). argv > env > cwd (D2).
    # The resolved slug buckets all session state for this handler's turns and
    # is written to coordinator.config inside the handler (D5).
    from amplifier_agent_lib.persistence import resolve_workspace, workspaces_root

    resolved_workspace = resolve_workspace(
        argv_workspace=workspace,
        env=os.environ,
        cwd=resolved_cwd if resolved_cwd is not None else Path.cwd(),
    )
    workspace_root = workspaces_root() / resolved_workspace
```

3. Inside `handler`, change the `SessionStore` construction (line 231) from `store = SessionStore(state_root())` to:

```python
        store = SessionStore(workspace_root)
```

4. Inside `handler`, after `session = await prepared.create_session(...)` (line 251-255), write the dual key (D5) — place it just before the `set_default_fields` call at line 260:

```python
        # D5: write workspace identity to coordinator.config. project_slug is
        # the ecosystem-canonical alias every existing hook reads; workspace is
        # the AAA-canonical name. Written as aliases (I4) until the ecosystem
        # aligns on one.
        session.coordinator.config["workspace"] = resolved_workspace
        session.coordinator.config["project_slug"] = resolved_workspace
```

Leave the existing `state_root` import in place — it may still be referenced elsewhere; grep before removing.

5. Edit `src/amplifier_agent_cli/modes/single_turn.py` `_execute_turn` (the `make_turn_handler(...)` call at line 433) to forward the workspace:

```python
    handler = make_turn_handler(
        prepared,
        cwd=spec.cwd,
        is_resumed=spec.resume and not spec.fresh,
        host_config=spec.host_config,
        workspace=spec.workspace,
    )
```

**Step 4: Run the tests, watch them pass.**

```bash
uv run pytest tests/test_runtime_workspace.py -v
uv run pytest tests/test_runtime.py tests/test_runtime_resume_wiring.py -v
```

Expected: new tests PASS; existing runtime tests still PASS.

**Step 5: Commit.**

```bash
git add src/amplifier_agent_lib/_runtime.py src/amplifier_agent_cli/modes/single_turn.py tests/test_runtime_workspace.py
git commit -m "$(cat <<'EOF'
feat(runtime): resolve workspace, write workspace + project_slug to coordinator.config (D5, D6, D8)

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>
EOF
)"
```

---

### Task B3: Confirm `SessionStore` honours a per-workspace root

**Files:**
- Create: `tests/test_session_store_per_workspace.py`

`SessionStore(root)` already accepts an arbitrary root and `session_dir(id)` already appends `sessions/<id>` (verified at `session_store.py:35`). This task is a **regression anchor** — it locks the contract that the per-workspace root produces the D8 layout. No implementation change is expected.

**Step 1: Write the test.**

Create `tests/test_session_store_per_workspace.py`:

```python
"""SessionStore writes under a per-workspace root (D8).

Regression anchor: SessionStore(root) already appends sessions/<id>; this
confirms that passing a workspace-scoped root yields the D8 layout
<root>/workspaces/<ws>/sessions/<id>/transcript.jsonl.
"""

from __future__ import annotations

from pathlib import Path

from amplifier_agent_lib.session_store import SessionStore


def test_session_store_writes_under_workspace_root(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspaces" / "test-ws"
    store = SessionStore(workspace_root)

    store.save("sid-1", [{"role": "user", "content": "hi"}], {"k": "v"})

    expected = workspace_root / "sessions" / "sid-1" / "transcript.jsonl"
    assert expected.is_file()
    assert store.session_dir("sid-1") == workspace_root / "sessions" / "sid-1"
```

**Step 2: Run the test, watch it pass immediately.**

```bash
uv run pytest tests/test_session_store_per_workspace.py -v
```

Expected: PASS on the first run (no implementation change). If it FAILS, the `SessionStore` constructor or `session_dir` was altered upstream — investigate before continuing; do not patch the test to match a regression.

> TDD note: this is a deliberate regression anchor for already-correct behavior (locking the D8 layout against future drift), not a red→green cycle. The kill-the-mutant check is in Task D2, which extends `load()`.

**Step 3: Commit.**

```bash
git add tests/test_session_store_per_workspace.py
git commit -m "$(cat <<'EOF'
test(session-store): anchor per-workspace root layout (D8)

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>
EOF
)"
```

---

### Task B4: Move the per-turn audit write path to the workspace tree

**Files:**
- Modify: `src/amplifier_agent_cli/modes/single_turn.py`
- Create: `tests/test_runtime_audit_path.py`

Per the unified-layout directive (I8), the per-turn audit file must land under the workspace tree, not the flat `sessions/<id>/` tree. The writer is `_write_audit` (`single_turn.py:248-285`), which today computes `audits_dir = session_state_dir(session_id) / "audits"` (line 273) — the flat path.

> **Why resolve at the CLI layer, not from `coordinator.config["workspace"]`:** `_write_audit` and the `--fresh` cleanup run in the CLI layer (`run` / `_execute_turn`), *before/after* the handler. The coordinator — and therefore `coordinator.config["workspace"]` written by B2 — does not exist at these call sites. We instead resolve the workspace from the same `(argv, env, cwd)` inputs B2 uses. `resolve_workspace` is pure and deterministic (D2/D4, I5), so the CLI-layer slug is byte-identical to the handler's. This is the mechanically-correct source; reading `coordinator.config` here is not possible.

**Step 1: Write the failing tests.**

Create `tests/test_runtime_audit_path.py`:

```python
"""The per-turn audit file lands under the workspace tree, not the flat tree (I8).

Design: docs/designs/2026-06-09-workspace-resolution-and-migration.md (§10, I8);
audit format: docs/designs/2026-05-24-aaa-v2-mode-a-pivot-amendment.md (SC-H).
"""

from __future__ import annotations

import json
from pathlib import Path

from amplifier_agent_cli.modes import single_turn
from amplifier_agent_lib.persistence import state_root


def _call_write_audit(workspace: str, session_id: str, turn_id: str) -> None:
    single_turn._write_audit(
        session_id=session_id,
        turn_id=turn_id,
        correlation_id="corr-xyz",
        exit_code=0,
        started_at="2026-06-09T00:00:00+00:00",
        ended_at="2026-06-09T00:00:01+00:00",
        argv=["amplifier-agent", "run", "hi"],
        protocol_version="1.0",
        workspace=workspace,
    )


def test_audit_lands_at_workspace_scoped_path(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    _call_write_audit("test-ws", "sid-1", "001")

    expected = (
        state_root() / "workspaces" / "test-ws" / "sessions" / "sid-1" / "audits" / "turn-001.json"
    )
    assert expected.is_file(), f"expected audit at {expected}"


def test_audit_not_at_flat_path(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    _call_write_audit("test-ws", "sid-1", "001")

    flat = state_root() / "sessions" / "sid-1" / "audits" / "turn-001.json"
    assert not flat.exists(), f"audit must NOT be written to the flat path {flat}"


def test_audit_correlation_id_preserved(monkeypatch, tmp_path: Path) -> None:
    """The SC-H audit schema is unchanged; only the path moves."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    _call_write_audit("test-ws", "sid-1", "001")

    audit_file = (
        state_root() / "workspaces" / "test-ws" / "sessions" / "sid-1" / "audits" / "turn-001.json"
    )
    payload = json.loads(audit_file.read_text(encoding="utf-8"))
    assert payload["correlationId"] == "corr-xyz"
    # Verified SC-H field set (real writer schema, not the amendment's prose).
    for field in ("argvDigest", "envDigest", "protocolVersion", "exitCode", "startedAt", "endedAt"):
        assert field in payload, f"missing SC-H field {field!r}"
```

**Step 2: Run the tests, watch them fail.**

```bash
uv run pytest tests/test_runtime_audit_path.py -v
```

Expected: FAILED — `_write_audit()` got an unexpected keyword argument `workspace` (the param does not exist yet).

**Step 3: Implement.**

Edit `src/amplifier_agent_cli/modes/single_turn.py`:

1. Add `workspace: str` to the `_write_audit` keyword-only signature (after `protocol_version`, around line 257):

```python
def _write_audit(
    *,
    session_id: str,
    turn_id: str,
    correlation_id: str,
    exit_code: int,
    started_at: str,
    ended_at: str,
    argv: list[str],
    protocol_version: str,
    workspace: str,
) -> None:
```

2. Replace the flat-path computation inside `_write_audit` (lines 269 + 273). Change the import and the `audits_dir` line:

```python
    from amplifier_agent_lib.persistence import workspaces_root

    if not session_id:
        return  # No session id ⇒ no audit (matches anonymous CLI use).
    audits_dir = workspaces_root() / workspace / "sessions" / session_id / "audits"
    audits_dir.mkdir(parents=True, exist_ok=True)
```

(The rest of `_write_audit` — the digest fields and `turn-<turn_id>.json` write — is unchanged.)

3. In `run`, resolve the workspace once and thread it to every `_write_audit` call. Add the imports near the other persistence imports at the top of the file:

```python
from amplifier_agent_lib.persistence import resolve_workspace
from amplifier_agent_lib.persistence import WorkspaceError
```

Then, immediately after the `_TurnSpec(...)` construction (after line 626), resolve and fail fast on an invalid `--workspace` before booting the engine:

```python
    # Resolve the workspace once for CLI-layer state paths (audit trail, --fresh
    # cleanup). Same (argv, env, cwd) inputs as _runtime's resolution (D2/D4),
    # so the slug is byte-identical to the handler's. Fail fast on an invalid
    # --workspace before booting (workspace I8).
    try:
        resolved_workspace = resolve_workspace(
            spec.workspace, os.environ, Path(spec.cwd) if spec.cwd else Path.cwd()
        )
    except WorkspaceError as exc:
        _emit_argv_envelope("argv_workspace_invalid", str(exc), exit_code=2)
        return  # unreachable; _emit_argv_envelope calls sys.exit
```

4. Pass `workspace=resolved_workspace` to **all three** `_write_audit(...)` calls (the `AaaError` path ~line 661, the bare `except Exception` path ~line 684, and the success path ~line 709). Each gets one new keyword argument:

```python
        _write_audit(
            session_id=session_id or "",
            turn_id=...,
            correlation_id=correlation_id,
            exit_code=...,
            started_at=started_iso,
            ended_at=datetime.now(UTC).isoformat(),
            argv=sys.argv,
            protocol_version=PROTOCOL_VERSION,
            workspace=resolved_workspace,
        )
```

> **Note on `spec.workspace`:** B1 added the raw `--workspace` string to `_TurnSpec`. Here we resolve it (raw → validated slug) into the local `resolved_workspace`; `spec.workspace` itself is left untouched so B5 and B2 each resolve from the same raw input independently. The `_emit_argv_envelope` fail-fast keeps B1's `test_workspace_flag_invalid_format_errors_cleanly` green (exit ≠ 0, no traceback — a clean envelope).

**Step 4: Run the tests, watch them pass.**

```bash
uv run pytest tests/test_runtime_audit_path.py -v
uv run pytest tests/cli/test_run_workspace_flag.py tests/cli/test_single_turn.py -v
```

Expected: new audit-path tests PASS; B1's flag tests and existing single_turn tests still PASS.

**Step 5: Commit.**

```bash
git add src/amplifier_agent_cli/modes/single_turn.py tests/test_runtime_audit_path.py
git commit -m "$(cat <<'EOF'
feat(runtime): move audit write path to per-workspace tree (workspace I8)

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>
EOF
)"
```

---

### Task B5: Scope the `--fresh` cleanup to the per-workspace session dir

**Files:**
- Modify: `src/amplifier_agent_cli/modes/single_turn.py`
- Create: `tests/test_runtime_fresh_workspace.py`

Per I8, `--fresh` must wipe only the per-workspace session dir, never the flat tree (and never another workspace's session). The cleanup lives in `_execute_turn` (`single_turn.py:424-431`), which today does `state_dir = session_state_dir(spec.session_id)` — the flat path.

> **Same resolution rationale as B4:** the cleanup runs before `make_turn_handler`, so no coordinator exists. Resolve from `(spec.workspace, env, cwd)` — identical inputs to B2/B4, identical slug.

**Step 1: Write the failing tests.**

Create `tests/test_runtime_fresh_workspace.py`:

```python
"""`--fresh` cleans only the per-workspace session dir (I8).

We exercise _execute_turn's cleanup branch in isolation by stubbing the
post-cleanup engine work, so the test stays fast and free of real LLM calls.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_agent_cli.modes import single_turn
from amplifier_agent_lib.persistence import state_root


def _seed_session(workspace: str, session_id: str, monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    sess = state_root() / "workspaces" / workspace / "sessions" / session_id
    sess.mkdir(parents=True, exist_ok=True)
    (sess / "transcript.jsonl").write_text('{"role":"user"}', encoding="utf-8")
    return sess


def _make_spec(workspace: str, session_id: str):
    spec = MagicMock()
    spec.workspace = workspace
    spec.session_id = session_id
    spec.fresh = True
    spec.resume = False
    spec.cwd = None
    spec.provider = "anthropic"
    spec.host_config = None
    spec.allow_protocol_skew = False
    spec.prompt = "hi"
    return spec


def _stub_engine_path(monkeypatch) -> None:
    """Stub everything after the --fresh cleanup so _execute_turn returns fast."""
    monkeypatch.setattr(single_turn, "load_and_prepare_cached", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr(single_turn, "inject_provider", lambda *a, **k: None)
    monkeypatch.setattr(single_turn, "make_turn_handler", lambda *a, **k: None)
    fake_engine = MagicMock()
    fake_engine.boot = AsyncMock()
    fake_engine.submit_turn = AsyncMock(return_value={"reply": "ok", "turnId": "turn-1"})
    fake_engine.shutdown = AsyncMock()
    monkeypatch.setattr(single_turn, "Engine", lambda *a, **k: fake_engine)


@pytest.mark.asyncio
async def test_fresh_cleans_workspace_scoped_session_dir(monkeypatch, tmp_path) -> None:
    sess = _seed_session("ws-a", "sid-1", monkeypatch, tmp_path)
    assert (sess / "transcript.jsonl").exists()
    _stub_engine_path(monkeypatch)

    await single_turn._execute_turn(_make_spec("ws-a", "sid-1"))

    assert not sess.exists(), "the per-workspace session dir should have been removed"


@pytest.mark.asyncio
async def test_fresh_leaves_other_workspaces_untouched(monkeypatch, tmp_path) -> None:
    sess_a = _seed_session("ws-a", "sid-1", monkeypatch, tmp_path)
    sess_b = _seed_session("ws-b", "sid-1", monkeypatch, tmp_path)
    _stub_engine_path(monkeypatch)

    await single_turn._execute_turn(_make_spec("ws-a", "sid-1"))

    assert not sess_a.exists()
    assert sess_b.exists(), "--fresh must not touch a different workspace"


@pytest.mark.asyncio
async def test_fresh_with_no_existing_session_no_op(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    _stub_engine_path(monkeypatch)

    # No session seeded; cleanup must be a silent no-op (no error).
    result = await single_turn._execute_turn(_make_spec("ws-a", "missing"))
    assert result["reply"] == "ok"
```

**Step 2: Run the tests, watch them fail.**

```bash
uv run pytest tests/test_runtime_fresh_workspace.py -v
```

Expected: `test_fresh_cleans_workspace_scoped_session_dir` and `test_fresh_leaves_other_workspaces_untouched` FAIL — the current code wipes `session_state_dir(session_id)` (flat path), so the workspace-scoped dir survives.

> If the stub set in `_stub_engine_path` does not match the real import surface of `_execute_turn` (e.g. `load_and_prepare_cached` / `Engine` are imported differently), adjust the `monkeypatch.setattr` targets to the names actually bound in `single_turn` — do not change `_execute_turn` to fit the test.

**Step 3: Implement.**

Edit `_execute_turn` in `src/amplifier_agent_cli/modes/single_turn.py`. Replace the `--fresh` cleanup block (lines 424-431):

```python
    if spec.fresh and spec.session_id:
        import shutil

        from amplifier_agent_lib.persistence import resolve_workspace, workspaces_root

        workspace = resolve_workspace(
            spec.workspace, os.environ, Path(spec.cwd) if spec.cwd else Path.cwd()
        )
        state_dir = workspaces_root() / workspace / "sessions" / spec.session_id
        if state_dir.exists():
            shutil.rmtree(state_dir, ignore_errors=True)
```

This swaps `session_state_dir(spec.session_id)` (flat) for the workspace-scoped path. Resolution is deterministic and matches B2/B4 (I5). An invalid `--workspace` is already rejected fail-fast in `run` (B4) before `_execute_turn` is reached; if `_execute_turn` is called directly (as in these unit tests) with a valid slug, resolution succeeds.

**Step 4: Run the tests, watch them pass.**

```bash
uv run pytest tests/test_runtime_fresh_workspace.py -v
uv run pytest tests/cli/test_single_turn.py -v
```

Expected: new `--fresh` tests PASS; existing single_turn tests still PASS.

**Step 5: Commit.**

```bash
git add src/amplifier_agent_cli/modes/single_turn.py tests/test_runtime_fresh_workspace.py
git commit -m "$(cat <<'EOF'
feat(runtime): scope --fresh cleanup to per-workspace session dir (workspace I8)

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>
EOF
)"
```

---

## Section C — Child session propagation (the silent-bug guard)

### Task C1: Propagate workspace into child coordinators in `spawn.py`

**Files:**
- Modify: `src/amplifier_agent_lib/spawn.py`
- Create: `tests/test_spawn_workspace_propagation.py`

Per D7, a delegate's output must land in the **same** workspace as its parent. The child must inherit the parent's workspace verbatim and never re-derive from cwd (cwd may have changed mid-session).

**Step 1: Write the failing tests.**

Create `tests/test_spawn_workspace_propagation.py`:

```python
"""Child coordinators inherit the parent's workspace verbatim (D7).

The propagation lands alongside the existing approval.request / display.emit
capability inheritance in spawn_sub_session (spawn.py ~453-456). We test the
isolated propagation step rather than a full spawn so the test stays fast and
free of real module loading.
"""

from __future__ import annotations

from types import SimpleNamespace

from amplifier_agent_lib import spawn


def _coordinator(config: dict) -> SimpleNamespace:
    return SimpleNamespace(config=config)


def test_child_inherits_parent_workspace() -> None:
    parent = _coordinator({"workspace": "parent-ws", "project_slug": "parent-ws"})
    child = _coordinator({})

    spawn._propagate_workspace(parent, child)

    assert child.config["workspace"] == "parent-ws"
    assert child.config["project_slug"] == "parent-ws"


def test_child_does_not_rederive_from_cwd() -> None:
    """Even if the child's notion of cwd differs, the workspace is the parent's value."""
    parent = _coordinator({"workspace": "parent-ws", "project_slug": "parent-ws"})
    child = _coordinator({"workspace": "stale-child-derived"})

    spawn._propagate_workspace(parent, child)

    # Parent value wins; nothing is re-derived.
    assert child.config["workspace"] == "parent-ws"
    assert child.config["project_slug"] == "parent-ws"


def test_propagate_is_noop_when_parent_has_no_workspace() -> None:
    """A parent without a workspace key leaves the child untouched (defensive)."""
    parent = _coordinator({})
    child = _coordinator({"workspace": "unchanged"})

    spawn._propagate_workspace(parent, child)

    assert child.config["workspace"] == "unchanged"
```

**Step 2: Run the tests, watch them fail.**

```bash
uv run pytest tests/test_spawn_workspace_propagation.py -v
```

Expected: FAILED — `module 'amplifier_agent_lib.spawn' has no attribute '_propagate_workspace'`.

**Step 3: Implement.**

Edit `src/amplifier_agent_lib/spawn.py`. Add the helper at module scope (after the imports / `__all__` block, around line 44):

```python
def _propagate_workspace(parent_coordinator: Any, child_coordinator: Any) -> None:
    """Inherit the parent's workspace into the child coordinator verbatim (D7).

    The child never re-derives from cwd; it copies the parent's resolved
    workspace (and the project_slug alias) so a delegate's session state lands
    in the same workspace bucket as its parent. No-op if the parent has no
    workspace set (defensive).
    """
    workspace = parent_coordinator.config.get("workspace")
    if workspace is not None:
        child_coordinator.config["workspace"] = workspace
        child_coordinator.config["project_slug"] = workspace
```

Then call it inside `spawn_sub_session`, right after the existing capability-inheritance loop (after line 456, where `approval.request` / `display.emit` are copied):

```python
    # -- Inherit workspace identity (D7) -------------------------------
    # Must run after child_session.initialize() so the child coordinator
    # exists, alongside the capability inheritance above.
    _propagate_workspace(parent_session.coordinator, child_session.coordinator)
```

**Step 4: Run the tests, watch them pass.**

```bash
uv run pytest tests/test_spawn_workspace_propagation.py -v
uv run pytest tests/test_spawn.py tests/test_spawn_capability_inheritance.py -v
```

Expected: new tests PASS; existing spawn tests still PASS.

**Step 5: Commit.**

```bash
git add src/amplifier_agent_lib/spawn.py tests/test_spawn_workspace_propagation.py
git commit -m "$(cat <<'EOF'
feat(spawn): propagate workspace to child coordinators verbatim (D7)

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>
EOF
)"
```

---

## Section D — Migration (one-shot, idempotent, flock'd)

### Task D1: Implement `migrate_legacy_sessions_if_needed()` in a new module

**Files:**
- Create: `src/amplifier_agent_lib/migration.py`
- Create: `tests/test_migration.py`

Per D9: lazy, one-shot, idempotent, flock-guarded. Moves any pre-existing flat `state_root()/sessions/<id>/` into `state_root()/workspaces/_legacy/sessions/<id>/`. No data deletion (I6).

> **Unified-layout note (I8):** `shutil.move(session_dir, target)` moves the **entire** session directory tree verbatim — including any `audits/` subdirectory that a pre-upgrade session accumulated under the flat path. No D1 code change is needed for this; the migrator already carries audits along. Step 1 below adds an explicit regression test (`test_migration_brings_audit_subdirs_along`) so this property is locked, not assumed.

**Step 1: Write the failing tests.**

Create `tests/test_migration.py`:

```python
"""Migration of the flat sessions/ tree to workspaces/_legacy/ (D9, §7).

All paths are computed inside migrate_legacy_sessions_if_needed() so the
XDG_STATE_HOME monkeypatch takes effect.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from amplifier_agent_lib import migration
from amplifier_agent_lib.persistence import state_root


def _seed_legacy_session(name: str, monkeypatch, tmp_path) -> Path:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    sess = state_root() / "sessions" / name
    sess.mkdir(parents=True, exist_ok=True)
    (sess / "transcript.jsonl").write_text('{"role":"user"}', encoding="utf-8")
    return sess


def test_migration_moves_existing_sessions_to_legacy(monkeypatch, tmp_path) -> None:
    _seed_legacy_session("legacy-1", monkeypatch, tmp_path)

    result = migration.migrate_legacy_sessions_if_needed()

    assert result.migrated == 1
    assert result.skipped is False
    moved = state_root() / "workspaces" / "_legacy" / "sessions" / "legacy-1" / "transcript.jsonl"
    assert moved.is_file()


def test_migration_brings_audit_subdirs_along(monkeypatch, tmp_path) -> None:
    """shutil.move carries audits/ verbatim — every per-session artifact moves (I8)."""
    sess = _seed_legacy_session("legacy-1", monkeypatch, tmp_path)
    audits = sess / "audits"
    audits.mkdir(parents=True, exist_ok=True)
    (audits / "turn-001.json").write_text('{"correlationId":"corr-1"}', encoding="utf-8")

    migration.migrate_legacy_sessions_if_needed()

    moved_audit = (
        state_root()
        / "workspaces"
        / "_legacy"
        / "sessions"
        / "legacy-1"
        / "audits"
        / "turn-001.json"
    )
    assert moved_audit.is_file(), f"audit subdir not carried along to {moved_audit}"
    assert moved_audit.read_text(encoding="utf-8") == '{"correlationId":"corr-1"}'


def test_migration_is_idempotent(monkeypatch, tmp_path) -> None:
    _seed_legacy_session("legacy-1", monkeypatch, tmp_path)

    first = migration.migrate_legacy_sessions_if_needed()
    second = migration.migrate_legacy_sessions_if_needed()

    assert first.migrated == 1
    assert second.skipped is True
    assert second.migrated == 0


def test_migration_no_op_when_no_old_root(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    result = migration.migrate_legacy_sessions_if_needed()
    assert result.skipped is True
    assert result.migrated == 0


def test_migration_no_op_when_old_root_empty(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    (state_root() / "sessions").mkdir(parents=True, exist_ok=True)
    result = migration.migrate_legacy_sessions_if_needed()
    assert result.skipped is True
    assert result.migrated == 0


def test_migration_skips_target_collision_logs_warning(monkeypatch, tmp_path, caplog) -> None:
    _seed_legacy_session("legacy-1", monkeypatch, tmp_path)
    # Pre-create the target so the move collides.
    target = state_root() / "workspaces" / "_legacy" / "sessions" / "legacy-1"
    target.mkdir(parents=True, exist_ok=True)
    (target / "transcript.jsonl").write_text("existing", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        result = migration.migrate_legacy_sessions_if_needed()

    assert result.collided == 1
    assert result.migrated == 0
    # Source is left in place (no data deletion, I6).
    assert (state_root() / "sessions" / "legacy-1").is_dir()
    assert any("already at target" in r.message for r in caplog.records)


def test_migration_removes_empty_old_root(monkeypatch, tmp_path) -> None:
    _seed_legacy_session("legacy-1", monkeypatch, tmp_path)
    migration.migrate_legacy_sessions_if_needed()
    assert not (state_root() / "sessions").exists()


def test_migration_preserves_old_root_if_not_empty(monkeypatch, tmp_path) -> None:
    _seed_legacy_session("legacy-1", monkeypatch, tmp_path)
    # A collision leaves a child behind, so the old root must NOT be removed.
    target = state_root() / "workspaces" / "_legacy" / "sessions" / "legacy-1"
    target.mkdir(parents=True, exist_ok=True)
    (target / "transcript.jsonl").write_text("existing", encoding="utf-8")

    migration.migrate_legacy_sessions_if_needed()

    assert (state_root() / "sessions").exists()


def test_migration_holds_flock_during_operation(monkeypatch, tmp_path) -> None:
    """The lock file is created under state_root and released after the call."""
    _seed_legacy_session("legacy-1", monkeypatch, tmp_path)

    migration.migrate_legacy_sessions_if_needed()

    lock_path = state_root() / ".migration.lock"
    assert lock_path.exists()
    # After return, the lock is releasable by another acquirer (kernel released
    # it on context exit). Acquiring it again must not block.
    with migration.file_lock(lock_path):
        pass
```

**Step 2: Run the tests, watch them fail.**

```bash
uv run pytest tests/test_migration.py -v
```

Expected: FAILED — `No module named 'amplifier_agent_lib.migration'`.

**Step 3: Implement.**

Create `src/amplifier_agent_lib/migration.py`:

```python
"""One-shot migration of the legacy flat sessions/ tree to workspaces/_legacy/.

Design: docs/designs/2026-06-09-workspace-resolution-and-migration.md (D9, §7).

Lazy, idempotent, flock-guarded. Runs on the first AAA boot after upgrade.
Moves every pre-existing ``state_root()/sessions/<id>/`` into
``state_root()/workspaces/_legacy/sessions/<id>/``. Never deletes data (I6):
on a target collision the source is left in place and counted.

Unix-only (fcntl.flock). AAA targets Linux/macOS; Windows is out of scope.
"""

from __future__ import annotations

import contextlib
import fcntl
import logging
import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from amplifier_agent_lib.persistence import state_root

logger = logging.getLogger(__name__)

LEGACY_WORKSPACE = "_legacy"


@dataclass
class MigrationResult:
    """Outcome of a migration attempt."""

    migrated: int = 0
    skipped: bool = False
    collided: int = 0


@contextlib.contextmanager
def file_lock(lock_path: Path) -> Iterator[None]:
    """Acquire an exclusive flock on ``lock_path`` for the duration of the block.

    The lock file is created if absent. The kernel releases the lock when the
    file descriptor closes (on context exit or process death), so a killed
    process never strands the lock.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = open(lock_path, "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()


def migrate_legacy_sessions_if_needed() -> MigrationResult:
    """Move the flat sessions/ tree to workspaces/_legacy/ if present (D9).

    Returns a MigrationResult. ``skipped=True`` means there was nothing to do
    (no old root, or it was empty). Idempotent: a second call after a complete
    migration returns ``skipped=True``.
    """
    root = state_root()
    old_root = root / "sessions"
    if not old_root.exists() or not any(old_root.iterdir()):
        logger.debug("migration: no legacy sessions/ to migrate")
        return MigrationResult(migrated=0, skipped=True)

    new_root = root / "workspaces" / LEGACY_WORKSPACE / "sessions"
    lock_path = root / ".migration.lock"

    with file_lock(lock_path):
        # Re-check after acquiring the lock (concurrent-boot race, §7).
        if not old_root.exists() or not any(old_root.iterdir()):
            return MigrationResult(migrated=0, skipped=True)

        logger.info("migration: starting legacy sessions/ -> workspaces/_legacy/")
        new_root.mkdir(parents=True, exist_ok=True)
        moved, collided = 0, 0
        for session_dir in old_root.iterdir():
            if not session_dir.is_dir():
                continue
            target = new_root / session_dir.name
            if target.exists():
                logger.warning(
                    "migration: %s already at target; leaving in place", session_dir.name
                )
                collided += 1
                continue
            shutil.move(str(session_dir), str(target))
            moved += 1

        # Remove the old root only if nothing was left behind (no deletion, I6).
        with contextlib.suppress(OSError):
            old_root.rmdir()

        logger.info("migration: moved %d sessions to _legacy (%d collisions)", moved, collided)
        return MigrationResult(migrated=moved, skipped=False, collided=collided)
```

**Step 4: Run the tests, watch them pass.**

```bash
uv run pytest tests/test_migration.py -v
```

Expected: all PASS (8 tests).

**Step 5: Commit.**

```bash
git add src/amplifier_agent_lib/migration.py tests/test_migration.py
git commit -m "$(cat <<'EOF'
feat(migration): add migrate_legacy_sessions_if_needed (D9, flock-guarded, idempotent)

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>
EOF
)"
```

---

### Task D2: Add cross-workspace `load()` fallback to `SessionStore`

**Files:**
- Modify: `src/amplifier_agent_lib/session_store.py`
- Create: `tests/test_session_store_cross_workspace_load.py`

Per D10: `load()` checks the current workspace first, then walks every other workspace under `workspaces_root()` and returns the first match. Logs at INFO when found in a different workspace.

> **Layout note:** the design's §7 pseudocode writes `self.root / session_id`, but the real `SessionStore` layout is `self.root / "sessions" / session_id` (`session_store.py:35`). The implementation below uses the real `session_dir()` layout for both the primary check and the cross-workspace candidate. This matches existing verified behavior and the D8 layout — it is not a new design decision.

**Step 1: Write the failing tests.**

Create `tests/test_session_store_cross_workspace_load.py`:

```python
"""Cross-workspace resume fallback for SessionStore.load (D10)."""

from __future__ import annotations

import logging
from pathlib import Path

from amplifier_agent_lib.session_store import SessionStore


def _workspaces_root(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    return tmp_path / "amplifier-agent" / "workspaces"


def test_load_finds_in_current_workspace(tmp_path, monkeypatch) -> None:
    ws_root = _workspaces_root(tmp_path, monkeypatch)
    store = SessionStore(ws_root / "current")
    store.save("sid-1", [{"role": "user", "content": "hi"}], {"k": "v"})

    result = store.load("sid-1")
    assert result is not None
    transcript, _ = result
    assert transcript == [{"role": "user", "content": "hi"}]


def test_load_walks_workspaces_when_not_in_current(tmp_path, monkeypatch) -> None:
    ws_root = _workspaces_root(tmp_path, monkeypatch)
    # Session lives in "other", but we load from "current".
    other = SessionStore(ws_root / "other")
    other.save("sid-2", [{"role": "user", "content": "elsewhere"}], {})

    current = SessionStore(ws_root / "current")
    result = current.load("sid-2")

    assert result is not None
    transcript, _ = result
    assert transcript == [{"role": "user", "content": "elsewhere"}]


def test_load_logs_when_found_in_different_workspace(tmp_path, monkeypatch, caplog) -> None:
    ws_root = _workspaces_root(tmp_path, monkeypatch)
    SessionStore(ws_root / "_legacy").save("sid-3", [{"role": "user"}], {})
    current = SessionStore(ws_root / "current")

    with caplog.at_level(logging.INFO):
        current.load("sid-3")

    assert any(
        "found sid-3 in workspace _legacy" in r.message and "current=current" in r.message
        for r in caplog.records
    )


def test_load_returns_none_when_nowhere(tmp_path, monkeypatch) -> None:
    ws_root = _workspaces_root(tmp_path, monkeypatch)
    store = SessionStore(ws_root / "current")
    assert store.load("does-not-exist") is None
```

**Step 2: Run the tests, watch them fail.**

```bash
uv run pytest tests/test_session_store_cross_workspace_load.py -v
```

Expected: `test_load_finds_in_current_workspace` and `test_load_returns_none_when_nowhere` PASS (current behavior); the two cross-workspace tests FAIL (no fallback yet).

**Step 3: Implement.**

Edit `src/amplifier_agent_lib/session_store.py`. Add `import logging` to the top imports and a module logger, then extend `load()`. Replace the body of `load` (lines 51-73) so that when the current-workspace transcript is absent, it walks the sibling workspaces:

```python
import logging

logger = logging.getLogger(__name__)
```

```python
    def load(self, session_id: str) -> tuple[list[dict], dict] | None:
        """Load persisted state for ``session_id``.

        Checks the current workspace first; if absent, walks every other
        workspace under ``workspaces_root()`` and returns the first match
        (D10 cross-workspace resume fallback). Returns ``(transcript,
        metadata)`` or ``None`` if found nowhere.
        """
        found = self._read_session_dir(self.session_dir(session_id))
        if found is not None:
            return found

        # Cross-workspace fallback (D10). Import locally to avoid a module-load
        # cycle and to honour the live XDG_STATE_HOME at call time.
        from amplifier_agent_lib.persistence import workspaces_root

        ws_root = workspaces_root()
        if not ws_root.exists():
            return None
        current_ws = self.root.name
        for ws_dir in ws_root.iterdir():
            if not ws_dir.is_dir() or ws_dir.name == current_ws:
                continue
            candidate = ws_dir / "sessions" / session_id
            found = self._read_session_dir(candidate)
            if found is not None:
                logger.info(
                    "resume: found %s in workspace %s (current=%s)",
                    session_id,
                    ws_dir.name,
                    current_ws,
                )
                return found
        return None

    def _read_session_dir(self, d: Path) -> tuple[list[dict], dict] | None:
        """Read transcript.jsonl + metadata.json from ``d`` or return None."""
        transcript_file = d / "transcript.jsonl"
        metadata_file = d / "metadata.json"
        if not transcript_file.exists():
            return None
        transcript: list[dict] = []
        raw = transcript_file.read_text(encoding="utf-8")
        for line in raw.splitlines():
            if line:
                transcript.append(json.loads(line))
        metadata: dict = {}
        if metadata_file.exists():
            metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
        return transcript, metadata
```

This extracts the existing read logic into `_read_session_dir` (no behavior change for the primary path) and adds the fallback walk.

**Step 4: Run the tests, watch them pass.**

```bash
uv run pytest tests/test_session_store_cross_workspace_load.py -v
uv run pytest tests/test_session_store.py tests/test_session_store_per_workspace.py -v
```

Expected: new tests PASS; existing session_store tests still PASS.

**Step 5: Commit.**

```bash
git add src/amplifier_agent_lib/session_store.py tests/test_session_store_cross_workspace_load.py
git commit -m "$(cat <<'EOF'
feat(session-store): cross-workspace load fallback (D10)

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>
EOF
)"
```

---

### Task D3: Wire the migration trigger into `_runtime` startup

**Files:**
- Modify: `src/amplifier_agent_lib/_runtime.py`
- Create: `tests/test_runtime_migration_wired.py`

The migration runs once per process, before the first `SessionStore` construction. Guard with a process-level flag so it does not re-run every turn.

**Step 1: Write the failing tests.**

Create `tests/test_runtime_migration_wired.py`:

```python
"""_runtime triggers migration once per process (D9)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_agent_lib import _runtime


class _FakeContextModule:
    async def get_messages(self) -> list[dict[str, Any]]:
        return []


def _make_fake_session() -> SimpleNamespace:
    coordinator = SimpleNamespace(
        config={},
        hooks=SimpleNamespace(set_default_fields=lambda **kw: None, register=lambda *a, **k: None),
        register_capability=lambda *a, **k: None,
        get=lambda key: _FakeContextModule() if key == "context" else None,
    )
    session = MagicMock()
    session.coordinator = coordinator
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.execute = AsyncMock(return_value="reply")
    return session


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(
        session_id="sid-1", turn_id="turn-1", prompt="hi",
        display=SimpleNamespace(emit=lambda *a, **k: None),
        approval=SimpleNamespace(request=lambda *a, **k: None),
    )


@pytest.mark.asyncio
async def test_runtime_runs_migration_on_first_boot(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    # Reset the process-level guard so this test sees a "first boot".
    monkeypatch.setattr(_runtime, "_MIGRATION_RAN", False, raising=False)

    calls: list[int] = []
    monkeypatch.setattr(
        _runtime, "migrate_legacy_sessions_if_needed",
        lambda: calls.append(1) or SimpleNamespace(migrated=0, skipped=True, collided=0),
    )

    prepared = MagicMock()
    prepared.mount_plan = {"agents": {}}
    prepared.create_session = AsyncMock(return_value=_make_fake_session())

    handler = _runtime.make_turn_handler(
        prepared, cwd=None, is_resumed=False, host_config=None, workspace="ws"
    )
    await handler(_ctx())

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_runtime_skips_migration_on_subsequent_boots(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setattr(_runtime, "_MIGRATION_RAN", False, raising=False)

    calls: list[int] = []
    monkeypatch.setattr(
        _runtime, "migrate_legacy_sessions_if_needed",
        lambda: calls.append(1) or SimpleNamespace(migrated=0, skipped=True, collided=0),
    )

    prepared = MagicMock()
    prepared.mount_plan = {"agents": {}}
    prepared.create_session = AsyncMock(side_effect=lambda **kw: _make_fake_session())

    handler = _runtime.make_turn_handler(
        prepared, cwd=None, is_resumed=False, host_config=None, workspace="ws"
    )
    await handler(_ctx())
    await handler(_ctx())  # second turn, same process

    assert len(calls) == 1, "migration must run at most once per process"
```

**Step 2: Run the tests, watch them fail.**

```bash
uv run pytest tests/test_runtime_migration_wired.py -v
```

Expected: FAILED — `migrate_legacy_sessions_if_needed` is not imported into `_runtime`, and the `_MIGRATION_RAN` guard does not exist.

**Step 3: Implement.**

Edit `src/amplifier_agent_lib/_runtime.py`:

1. Add the import near the top (with the other `amplifier_agent_lib` imports, around line 22-29):

```python
from amplifier_agent_lib.migration import migrate_legacy_sessions_if_needed
```

2. Add a module-level guard flag (after `logger = logging.getLogger(__name__)`, line 34):

```python
# Process-level guard: the legacy-sessions migration runs at most once per
# process (D9), on the first turn handled.
_MIGRATION_RAN = False
```

3. Inside `handler`, run the migration once before `SessionStore` is constructed (just before line 231, `store = SessionStore(workspace_root)`):

```python
        global _MIGRATION_RAN
        if not _MIGRATION_RAN:
            _MIGRATION_RAN = True
            try:
                migrate_legacy_sessions_if_needed()
            except Exception:
                # A migration failure must not block the turn. Cross-workspace
                # resume (D10) tolerates partially-migrated state; the next
                # boot retries. Log and continue.
                logger.exception("legacy-sessions migration failed; continuing")
```

**Step 4: Run the tests, watch them pass.**

```bash
uv run pytest tests/test_runtime_migration_wired.py -v
uv run pytest tests/test_runtime_workspace.py -v
```

Expected: new tests PASS; B2's runtime-workspace tests still PASS. (Note: B2's tests don't reset `_MIGRATION_RAN`, so the migration may already have run there — harmless, since the mocked store path is unaffected.)

**Step 5: Commit.**

```bash
git add src/amplifier_agent_lib/_runtime.py tests/test_runtime_migration_wired.py
git commit -m "$(cat <<'EOF'
feat(runtime): trigger legacy-sessions migration once per process (D9)

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>
EOF
)"
```

---

## Section E — End-to-end real-binary integration tests

Each test launches the real `amplifier-agent` binary as a subprocess against the mock LLM (per the 2026-05-24 amendment §A4' convention). No mocks of the engine's argv parsing or envelope emission. Reuse the `_MockLLM` / `mock_llm` / `_binary_path` / `_sse_message` pattern from `tests/cli/test_mode_a_v2_real_binary.py`.

All Section E tests live in three new files. Task E1 creates `tests/integration/test_workspace_e2e.py` with the shared mock-LLM fixture; E2/E3/E5 append. Task E4 creates `tests/integration/test_migration_e2e.py`. Task E6 creates `tests/integration/test_audit_e2e.py`.

> **First, create the package dir if absent:**
> ```bash
> mkdir -p tests/integration && touch tests/integration/__init__.py
> ```

### Task E1: `--workspace` flag produces the expected directory layout

**Files:**
- Create: `tests/integration/__init__.py` (empty, if absent)
- Create: `tests/integration/test_workspace_e2e.py`

**Step 1: Write the failing test.**

Create `tests/integration/test_workspace_e2e.py`. Copy the `_sse_message`, `_MockLLM`, `mock_llm`, and `_binary_path` helpers verbatim from `tests/cli/test_mode_a_v2_real_binary.py` (lines 25-131), then add:

```python
import json as _json
from pathlib import Path


def _state_glob_transcript(state_home: Path, workspace: str, session_id: str) -> Path:
    return (
        state_home
        / "amplifier-agent"
        / "workspaces"
        / workspace
        / "sessions"
        / session_id
        / "transcript.jsonl"
    )


def test_workspace_flag_produces_expected_layout(mock_llm, tmp_path) -> None:
    import os
    import subprocess

    env = os.environ.copy()
    env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{mock_llm}"
    env["ANTHROPIC_API_KEY"] = "test-key"
    env["XDG_STATE_HOME"] = str(tmp_path)
    # Do not let the inherited conftest config override the workspace test;
    # approval mode "yes" from $AMPLIFIER_AGENT_CONFIG is fine to keep.

    proc = subprocess.run(
        [
            _binary_path(), "run",
            "--session-id", "ws-sid-1",
            "--workspace", "test-ws",
            "--fresh",
            "--output", "json",
            "--provider", "anthropic",
            "say hi",
        ],
        capture_output=True, text=True, env=env, timeout=30,
    )

    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    transcript = _state_glob_transcript(tmp_path, "test-ws", "ws-sid-1")
    assert transcript.is_file(), f"expected transcript at {transcript}"
```

**Step 2: Run the test, watch it fail (then pass after Sections A–D land).**

```bash
uv run pytest tests/integration/test_workspace_e2e.py::test_workspace_flag_produces_expected_layout -v
```

Expected before Sections A–D: FAIL (no `--workspace` flag / flat layout). After A–D are merged and the binary is reinstalled (`uv tool install -e .` or `uv sync`), this passes. If the binary on PATH is stale, the test reads old behavior — reinstall first:

```bash
uv tool install -e . --force 2>/dev/null || uv sync
```

> **Note on `project_slug` audit assertion (parent spec mention):** the parent task references verifying `coordinator.config["project_slug"]` via a per-turn audit file (SC-H). The audit-trail surface lives in `single_turn.py` (`audits_dir = session_state_dir(session_id) / "audits"`, line 273) and writes config snapshots only if that mechanism records `coordinator.config`. **Open question:** confirm whether the audit file captures `coordinator.config` post-D5. If it does, add an assertion that the audit JSON contains `project_slug == "test-ws"`. If it does not (the audit trail records argv/turn metadata, not coordinator.config), the directory-layout assertion above is the binding end-to-end proof and the `project_slug` value is already covered by the B2 unit test. Do not fabricate an audit assertion against a field the audit file does not contain.

**Step 3: Commit.**

```bash
git add tests/integration/__init__.py tests/integration/test_workspace_e2e.py
git commit -m "$(cat <<'EOF'
test(integration): --workspace flag produces workspaces/<ws> layout (D1, D8)

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>
EOF
)"
```

---

### Task E2: `AMPLIFIER_AGENT_WORKSPACE` env var produces the expected layout

**Files:**
- Modify: `tests/integration/test_workspace_e2e.py`

**Step 1: Write the failing test.** Append:

```python
def test_workspace_env_var_produces_expected_layout(mock_llm, tmp_path) -> None:
    import os
    import subprocess

    env = os.environ.copy()
    env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{mock_llm}"
    env["ANTHROPIC_API_KEY"] = "test-key"
    env["XDG_STATE_HOME"] = str(tmp_path)
    env["AMPLIFIER_AGENT_WORKSPACE"] = "env-ws"

    proc = subprocess.run(
        [
            _binary_path(), "run",
            "--session-id", "env-sid-1",
            "--fresh", "--output", "json", "--provider", "anthropic",
            "say hi",
        ],
        capture_output=True, text=True, env=env, timeout=30,
    )

    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    transcript = _state_glob_transcript(tmp_path, "env-ws", "env-sid-1")
    assert transcript.is_file(), f"expected transcript at {transcript}"
```

**Step 2: Run, watch it pass (after A–D landed).**

```bash
uv run pytest tests/integration/test_workspace_e2e.py::test_workspace_env_var_produces_expected_layout -v
```

Expected: PASS.

**Step 3: Commit.**

```bash
git add tests/integration/test_workspace_e2e.py
git commit -m "$(cat <<'EOF'
test(integration): AMPLIFIER_AGENT_WORKSPACE env var layout (D1, D2)

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>
EOF
)"
```

---

### Task E3: cwd-derived workspace is stable

**Files:**
- Modify: `tests/integration/test_workspace_e2e.py`

**Step 1: Write the failing test.** Append:

```python
def test_cwd_derived_workspace_is_stable(mock_llm, tmp_path) -> None:
    """Two no-flag/no-env invocations from the same cwd land in the same workspace dir (I5)."""
    import os
    import subprocess

    state_home = tmp_path / "state"
    work_cwd = tmp_path / "repo"
    work_cwd.mkdir()

    env = os.environ.copy()
    env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{mock_llm}"
    env["ANTHROPIC_API_KEY"] = "test-key"
    env["XDG_STATE_HOME"] = str(state_home)
    env.pop("AMPLIFIER_AGENT_WORKSPACE", None)

    def _run(session_id: str):
        return subprocess.run(
            [
                _binary_path(), "run",
                "--session-id", session_id,
                "--fresh", "--output", "json", "--provider", "anthropic",
                "say hi",
            ],
            capture_output=True, text=True, env=env, cwd=str(work_cwd), timeout=30,
        )

    assert _run("cwd-sid-1").returncode == 0
    assert _run("cwd-sid-2").returncode == 0

    ws_root = state_home / "amplifier-agent" / "workspaces"
    workspaces = [d.name for d in ws_root.iterdir() if d.is_dir()]
    # Both sessions derived the SAME workspace from the same cwd.
    assert len(workspaces) == 1, f"expected one stable cwd-derived workspace, got {workspaces}"
    ws = workspaces[0]
    assert (ws_root / ws / "sessions" / "cwd-sid-1" / "transcript.jsonl").is_file()
    assert (ws_root / ws / "sessions" / "cwd-sid-2" / "transcript.jsonl").is_file()
```

**Step 2: Run, watch it pass.**

```bash
uv run pytest tests/integration/test_workspace_e2e.py::test_cwd_derived_workspace_is_stable -v
```

Expected: PASS.

**Step 3: Commit.**

```bash
git add tests/integration/test_workspace_e2e.py
git commit -m "$(cat <<'EOF'
test(integration): cwd-derived workspace is stable across invocations (D4, I5)

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>
EOF
)"
```

---

### Task E4: Migration moves legacy sessions on first post-upgrade boot

**Files:**
- Create: `tests/integration/test_migration_e2e.py`

**Step 1: Write the failing test.**

Create `tests/integration/test_migration_e2e.py`. Copy the `_sse_message`, `_MockLLM`, `mock_llm`, `_binary_path` helpers verbatim from `tests/cli/test_mode_a_v2_real_binary.py`, then add:

```python
import os
import subprocess
from pathlib import Path


def test_legacy_sessions_migrated_on_first_boot(mock_llm, tmp_path) -> None:
    """A pre-existing flat sessions/<id>/ is moved to workspaces/_legacy/ on first run (D9)."""
    state_root = tmp_path / "amplifier-agent"
    legacy = state_root / "sessions" / "legacy-1"
    legacy.mkdir(parents=True)
    (legacy / "transcript.jsonl").write_text('{"role":"user","content":"old"}', encoding="utf-8")

    env = os.environ.copy()
    env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{mock_llm}"
    env["ANTHROPIC_API_KEY"] = "test-key"
    env["XDG_STATE_HOME"] = str(tmp_path)

    proc = subprocess.run(
        [
            _binary_path(), "run",
            "--session-id", "new-1",
            "--workspace", "current",
            "--fresh", "--output", "json", "--provider", "anthropic",
            "say hi",
        ],
        capture_output=True, text=True, env=env, timeout=30,
    )

    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    moved = state_root / "workspaces" / "_legacy" / "sessions" / "legacy-1" / "transcript.jsonl"
    assert moved.is_file(), f"legacy session not migrated to {moved}"
    assert moved.read_text(encoding="utf-8").strip() == '{"role":"user","content":"old"}'
    # Old flat root removed once empty.
    assert not (state_root / "sessions").exists()
```

**Step 2: Run, watch it pass (after A–D landed + binary reinstalled).**

```bash
uv run pytest tests/integration/test_migration_e2e.py -v
```

Expected: PASS.

**Step 3: Commit.**

```bash
git add tests/integration/test_migration_e2e.py
git commit -m "$(cat <<'EOF'
test(integration): legacy sessions migrated to _legacy on first boot (D9)

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>
EOF
)"
```

---

### Task E5: Cross-workspace resume finds migrated sessions

**Files:**
- Modify: `tests/integration/test_workspace_e2e.py`

**Step 1: Write the failing test.** Append to `tests/integration/test_workspace_e2e.py`:

```python
def test_resume_finds_session_in_legacy_workspace(mock_llm, tmp_path) -> None:
    """After migration, --resume <id> --workspace different-ws finds the session in _legacy (D10)."""
    import os
    import subprocess

    state_root = tmp_path / "amplifier-agent"
    # Seed a session directly under the _legacy workspace (post-migration state).
    legacy_sess = state_root / "workspaces" / "_legacy" / "sessions" / "legacy-1"
    legacy_sess.mkdir(parents=True)
    (legacy_sess / "transcript.jsonl").write_text(
        '{"role":"user","content":"hi"}\n{"role":"assistant","content":"hello"}',
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{mock_llm}"
    env["ANTHROPIC_API_KEY"] = "test-key"
    env["XDG_STATE_HOME"] = str(tmp_path)

    proc = subprocess.run(
        [
            _binary_path(), "run",
            "--session-id", "legacy-1",
            "--resume",
            "--workspace", "different-ws",
            "--output", "json", "--provider", "anthropic",
            "ping",
        ],
        capture_output=True, text=True, env=env, timeout=30,
    )

    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    # The INFO log line proves the cross-workspace lookup fired (D10).
    assert "found legacy-1 in workspace _legacy" in proc.stderr
    assert "current=different-ws" in proc.stderr
```

**Step 2: Run, watch it pass.**

```bash
uv run pytest tests/integration/test_workspace_e2e.py::test_resume_finds_session_in_legacy_workspace -v
```

Expected: PASS. If the INFO log line does not reach stderr (engine log level defaults above INFO in the real binary), this assertion may need an explicit `--debug`/`--verbose` flag or a log-level env var. **Open question if it fails:** confirm the binary's default stderr log level surfaces INFO. If INFO is suppressed by default, either (a) add the appropriate verbosity flag to the invocation, or (b) assert on the resume *outcome* (the session resumes successfully and the reply is produced) instead of the log line — but do not assert a log line that the binary does not emit at its default level.

**Step 3: Commit.**

```bash
git add tests/integration/test_workspace_e2e.py
git commit -m "$(cat <<'EOF'
test(integration): cross-workspace resume finds migrated session (D10)

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>
EOF
)"
```

---

### Task E6: End-to-end verification of the audit path under the workspace tree

**Files:**
- Create: `tests/integration/test_audit_e2e.py`

Real-binary proof that a per-turn audit file lands under `workspaces/<workspace>/sessions/<id>/audits/` after an actual turn, with its `correlationId` matching the envelope's `metadata.correlationId` (I8 + SC-H, end-to-end).

**Step 1: Write the failing test.**

Create `tests/integration/test_audit_e2e.py`. Copy the `_sse_message`, `_MockLLM`, `mock_llm`, and `_binary_path` helpers verbatim from `tests/cli/test_mode_a_v2_real_binary.py` (lines 25-131), then add:

```python
import json
import os
import subprocess
from pathlib import Path


def test_audit_lands_in_workspace_after_real_turn(mock_llm, tmp_path) -> None:
    """A real turn writes its audit under workspaces/<ws>/sessions/<id>/audits/ (I8)."""
    env = os.environ.copy()
    env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{mock_llm}"
    env["ANTHROPIC_API_KEY"] = "test-key"
    env["XDG_STATE_HOME"] = str(tmp_path)

    proc = subprocess.run(
        [
            _binary_path(), "run",
            "--session-id", "audit-sid-1",
            "--workspace", "e2e-ws",
            "--fresh", "--output", "json", "--provider", "anthropic",
            "hello",
        ],
        capture_output=True, text=True, env=env, timeout=30,
    )

    assert proc.returncode == 0, (proc.stdout, proc.stderr)

    # The success envelope is the last JSON line on stdout.
    envelope = json.loads(proc.stdout.strip().splitlines()[-1])
    turn_id = envelope["turnId"]
    correlation_id = envelope["metadata"]["correlationId"]

    audits_dir = (
        tmp_path / "amplifier-agent" / "workspaces" / "e2e-ws"
        / "sessions" / "audit-sid-1" / "audits"
    )
    audit_file = audits_dir / f"turn-{turn_id}.json"
    assert audit_file.is_file(), f"expected audit at {audit_file}; dir held {list(audits_dir.glob('*')) if audits_dir.exists() else 'MISSING'}"

    # No audit may exist on the flat path (I8 — the flat tree is gone).
    flat_audits = tmp_path / "amplifier-agent" / "sessions" / "audit-sid-1" / "audits"
    assert not flat_audits.exists(), f"audit must NOT be on the flat path {flat_audits}"

    payload = json.loads(audit_file.read_text(encoding="utf-8"))
    assert payload["correlationId"] == correlation_id
```

**Step 2: Run, watch it pass (after A–D + B4/B5 land and the binary is reinstalled).**

```bash
uv tool install -e . --force 2>/dev/null || uv sync
uv run pytest tests/integration/test_audit_e2e.py -v
```

Expected: PASS. If the binary on PATH is stale (pre-B4), the audit lands on the flat path and the test fails — reinstall first.

> **Open question if the success envelope's `turnId` differs from the audit filename:** the audit file is named `turn-<turn_id>.json` where `turn_id` is the engine's turn id (`result.get("turnId")`, `single_turn.py:711`). The mock-LLM turn returns `turnId: "turn-1"` in the canonical fixture, so the file is `turn-turn-1.json`. The test derives the filename from the envelope's `turnId` rather than hard-coding it, so it tolerates either convention. If the envelope's `turnId` and the audit-file `turn_id` are sourced differently, assert on the single file present in `audits_dir` instead — do not hard-code a turn id the binary does not emit.

**Step 3: Commit.**

```bash
git add tests/integration/test_audit_e2e.py
git commit -m "$(cat <<'EOF'
test(integration): audit file lands under workspace tree after real turn (I8, SC-H)

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>
EOF
)"
```

---

## Out of scope (state explicitly)

Per design D6 and I3, and the companion's §5 deferral list:

- **No host-config schema amendment.** Workspace is engine-level identity (D6); it does not enter the strict 5-key host-config schema (D7 of `2026-06-01-host-config-layer-revisit.md`).
- **No `bundle.md` changes.** The sealed manifest is untouched; ecosystem hooks read `coordinator.config["project_slug"]` automatically (D5).
- **No `session.storage` capability.** The substitution seam is identified in the companion (D4) but not implemented. Filesystem-only.
- **No multi-dimensional scope keys** (`tenant`, `user`). Additive in the future (companion D1), not today.
- **No `amplifier-agent workspaces list` admin command.** Deferred (design §13).
- **No `--legacy-layout` backward-compat flag.** Decided no (design §13).
- **No Windows path handling.** AAA is Linux/macOS only; `migration.py` uses `fcntl.flock`.

**Open Question #2 — RESOLVED (unified layout, I8).** Earlier iterations of this plan deferred audits and `--fresh` to the flat `state_root()/sessions/<id>` path, flagging the re-point as a separate design decision. The design owner has since directed: *"there shouldn't be two separate places to write things; the flat list should be gone."* That decision is now **implemented in B4** (audit write path → workspace tree) and **B5** (`--fresh` cleanup → workspace tree), verified end-to-end in **E6**, and the migrator (D1) carries `audits/` subdirectories along verbatim. See the parent design's §13 entry for #2 and §10 invariant **I8**. Note: `persistence.session_state_dir(session_id)` (line 63) is left in place — its two callers in `single_turn.py` are re-pointed at the workspace-scoped path by B4/B5, so `session_state_dir` becomes unused by the audit/`--fresh` paths; remove it only if no other caller remains (grep before deleting).

---

## Wrap-up checklist

After all 16 tasks land, run the full gate and tick each box with real evidence:

```bash
cd /Users/mpaidiparthy/repos/amplifier-agent
uv run pytest -q 2>&1 | tail -20
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pyright src/amplifier_agent_lib src/amplifier_agent_cli
```

- [ ] `resolve_workspace()` exists in `persistence.py` and is unit-tested (A1–A4)
- [ ] `--workspace` flag parses cleanly (B1)
- [ ] `_runtime` writes both `workspace` and `project_slug` to `coordinator.config` (B2)
- [ ] Session state lives under `workspaces/<workspace>/sessions/<id>/` (B2, B3, E1)
- [ ] Audit files land under `workspaces/<workspace>/sessions/<id>/audits/` (B4, E6)
- [ ] `--fresh` cleans only the per-workspace session dir (B5)
- [ ] Migration moves audit subdirs verbatim alongside transcripts (D1)
- [ ] No file written to the flat `sessions/<id>/` path post-migration (B4, B5, E6)
- [ ] Child coordinators inherit parent workspace verbatim (C1)
- [ ] Migration moves flat sessions to `_legacy` on first boot (D1, D3, E4)
- [ ] Migration is idempotent and flock-safe (D1)
- [ ] `SessionStore.load()` falls back across workspaces (D2, E5)
- [ ] E2E integration tests pass against real binary + mock LLM (E1–E5)
- [ ] No host-config schema change (D7 of `2026-06-01-host-config-layer-revisit.md` preserved)
- [ ] No `bundle.md` change (sealed manifest preserved)
- [ ] `uv run pytest` clean
- [ ] `uv run ruff check` clean
- [ ] `uv run pyright src/amplifier_agent_lib src/amplifier_agent_cli` clean

Implementation complete. Hand off to `/finish` for review + PR (no `git push` / `gh pr create` in this plan).

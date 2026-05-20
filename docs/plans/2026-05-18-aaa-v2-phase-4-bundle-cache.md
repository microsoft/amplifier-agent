# AaA v2 — Phase 4 of 4: Built-in Bundle Vendoring + Prepare-and-Cache

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Replace the Phase 1 mock bundle with a real vendored `bundle.md` that loads via `amplifier_foundation.load_bundle()`, prepare it once and cache to XDG, and wire the result through engine + admin verbs + a post-install hook so subsequent invocations are fast.

**Architecture:** A single `bundle.md` ships inside the wheel under `amplifier_agent_lib/bundle/`. On first invocation, `bundle.cache.load_and_prepare()` loads the YAML, calls `Bundle.prepare(install_deps=True)`, then writes the artifact to `$XDG_CACHE_HOME/amplifier-agent/prepared/<aaa_version>/`. Subsequent invocations read straight from the cache. The cache is version-keyed (so AaA upgrades invalidate it), corruption-tolerant (rebuild on bad file), and idempotent (a post-install hook primes it; a second run is a no-op). Engine.boot() consumes the cache; tests inject a mock via a `bundle_override` parameter on the engine. Admin verbs `cache clear` and `doctor` report against this cache.

**Tech Stack:** Python 3.11+, `amplifier-foundation` (Bundle, load_bundle, PreparedBundle), `amplifier-module-context-persistent`, hatchling (build backend with package-data inclusion), `uv tool install`, pytest + pytest-asyncio. XDG paths via the Phase 1 `amplifier_agent_lib.persistence` module.

---

## Prerequisites

- **Phase 1 complete** — `amplifier_agent_lib` engine, protocol types, `protocol_points/`, `persistence.py` (XDG paths), spawn stub. Engine accepts a mock bundle today; Phase 4 replaces that with the real one.
- **Phase 2 complete** — `amplifier_agent_cli` Mode A + admin verbs (`doctor`, `config show`, `cache clear`). Phase 2's `cache clear` is a stub; Phase 4 wires it. Phase 2's `doctor` does not yet check cache state; Phase 4 adds that check.
- **Phase 3 complete** — `amplifier_agent_cli` Mode B (`run --stdio`).
- **Working directory:** `/Users/mpaidiparthy/repos/AaA/opus-recon/amplifier-agent/`
- **Sibling repos for reference (read-only):**
  - `../amplifier-foundation/` — `load_bundle()`, `Bundle.prepare()`, `PreparedBundle`
  - `../amplifier-app-openclaw/src/amplifier_app_openclaw/runner.py:118-167` — canonical load+prepare flow
  - `../aaa-source/docs/designs/aaa-v2-design-checkpoint.md` — the design doc (§5 built-in bundle, §3 layer 4, Appendix D drops)

## Conventions used in this plan

- **All paths are relative to the working directory** unless otherwise noted.
- **Package layout is `src/`-based** (`src/amplifier_agent_lib/...`), matching Phases 1–3.
- **Every task follows TDD:** write the failing test, run it to see it fail, write the minimum code to make it pass, run it to see it pass, commit.
- **Conventional commits.** `feat:`, `fix:`, `test:`, `chore:`. Scope optional.
- **Run tests from the repo root** with `uv run pytest <path>`. If Phase 1 set a different runner, adapt.
- **No mocking `amplifier-foundation` inside `tests/test_bundle_loader.py` or `tests/test_bundle_cache.py` integration paths.** We want real load + prepare exercising the real wire. Pure-unit cache-mechanism tests may use `tmp_path` and serialized fixtures.

---

## Task 1: Vendor a minimal `bundle.md` and ship it in the wheel

**Files:**
- Create: `src/amplifier_agent_lib/bundle/__init__.py`
- Create: `src/amplifier_agent_lib/bundle/bundle.md`
- Modify: `pyproject.toml`
- Create: `tests/test_bundle_packaging.py`

**Step 1: Write the failing test**

Write `tests/test_bundle_packaging.py`:

```python
"""Verify the vendored bundle.md ships with the installed package."""
from importlib import resources

import amplifier_agent_lib.bundle as bundle_pkg


def test_bundle_md_is_packaged():
    """The bundle.md file must be importable via importlib.resources."""
    files = resources.files(bundle_pkg)
    bundle_md = files / "bundle.md"
    assert bundle_md.is_file(), "bundle.md not found in installed package data"


def test_bundle_md_has_yaml_frontmatter():
    """The bundle.md must start with a YAML frontmatter block."""
    files = resources.files(bundle_pkg)
    content = (files / "bundle.md").read_text(encoding="utf-8")
    assert content.startswith("---\n"), "bundle.md missing YAML frontmatter opener"
    assert "\n---\n" in content, "bundle.md missing YAML frontmatter closer"


def test_bundle_md_declares_name_and_includes_foundation():
    """The bundle.md must declare its name and include build-up-foundation."""
    files = resources.files(bundle_pkg)
    content = (files / "bundle.md").read_text(encoding="utf-8")
    assert "amplifier-agent-builtin" in content
    assert "build-up-foundation" in content or "amplifier-foundation" in content
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_bundle_packaging.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'amplifier_agent_lib.bundle'`.

**Step 3: Create the bundle package and vendored YAML**

Create `src/amplifier_agent_lib/bundle/__init__.py`:

```python
"""Built-in vendored bundle for amplifier-agent.

The `bundle.md` file in this directory is the sealed (per D4) built-in
bundle. It is loaded via `amplifier_foundation.load_bundle()` and prepared
exactly once per AaA version, with the result cached to
`$XDG_CACHE_HOME/amplifier-agent/prepared/<version>/`.

Do not edit `bundle.md` outside a deliberate design change — it is part
of the architectural contract (Brian D4: bundle stays internal,
opinionated, sealed).
"""

from pathlib import Path

BUNDLE_DIR: Path = Path(__file__).resolve().parent
BUNDLE_MD: Path = BUNDLE_DIR / "bundle.md"
```

Create `src/amplifier_agent_lib/bundle/bundle.md`:

```markdown
---
bundle:
  name: amplifier-agent-builtin
  version: 1.0.0
  description: |
    Built-in bundle for `amplifier-agent` (AaA v2).

    Composes the `build-up-foundation` bundle and declares
    `context-persistent` as the default context module so that
    --session-id + --resume can replay transcripts across invocations
    (per OpenClaw's validated logical-replay pattern).

    Sealed per Brian D4 — not user-edited.

includes:
  - bundle: git+https://github.com/microsoft/amplifier-foundation@main

session:
  orchestrator:
    module: loop-streaming
    source: git+https://github.com/microsoft/amplifier-module-loop-streaming@main
  context:
    module: context-persistent
    source: git+https://github.com/microsoft/amplifier-module-context-persistent@main
    config:
      max_tokens: 200000
      # transcript_path is injected at session creation per --session-id
---

# Amplifier Agent — Built-in Bundle

You are running inside the `amplifier-agent` CLI. Follow the conventions
of the parent application that invoked you. Approval and display flows
are mediated by the host adapter; do not assume a TTY is available.
```

Modify `pyproject.toml` — add `bundle.md` as package data. Find the existing `[tool.hatch.build.targets.wheel]` section (added in Phase 1). Add `force-include` for the bundle:

```toml
[tool.hatch.build.targets.wheel]
packages = ["src/amplifier_agent_lib", "src/amplifier_agent_cli"]

[tool.hatch.build.targets.wheel.force-include]
"src/amplifier_agent_lib/bundle/bundle.md" = "amplifier_agent_lib/bundle/bundle.md"
```

If a `[tool.hatch.build.targets.wheel]` section does not exist yet, add the block above in full. If the project uses `setuptools` instead, the equivalent is `[tool.setuptools.package-data]` with `"amplifier_agent_lib.bundle" = ["bundle.md"]`. Verify which build backend Phase 1 chose by reading the top of `pyproject.toml`.

**Step 4: Run test to verify it passes**

Run: `uv sync && uv run pytest tests/test_bundle_packaging.py -v`
Expected: 3 tests PASS.

**Step 5: Verify the wheel actually contains the bundle**

Run: `uv build && uv run python -m zipfile -l dist/amplifier_agent-*.whl | grep bundle.md`
Expected output (line will appear): `amplifier_agent_lib/bundle/bundle.md`

If the line is missing, the package-data config is wrong — fix before continuing.

**Step 6: Commit**

```bash
git add src/amplifier_agent_lib/bundle/ tests/test_bundle_packaging.py pyproject.toml
git commit -m "feat(bundle): vendor built-in bundle.md and ship in wheel"
```

---

## Task 2: Empirical spike — is `PreparedBundle` picklable?

The parent design instruction is explicit: we don't know yet whether the bundle's *prepared state* survives pickling. Phase 4 needs to know this before choosing a cache serialization strategy. Run a single decision-fixing test and record the answer.

**Files:**
- Create: `tests/spike_test_prepared_pickle.py`
- Create: `docs/decisions/2026-05-18-cache-serialization.md`

**Step 1: Write the spike test**

Create `tests/spike_test_prepared_pickle.py`:

```python
"""Spike: can PreparedBundle be round-tripped through pickle?

This test exists to settle a Phase 4 design question: do we cache the
PreparedBundle as a pickle, or do we cache only the composed bundle
source and re-run prepare() (without install_deps) on each load?

Run this test, observe the outcome, then record the decision in
`docs/decisions/2026-05-18-cache-serialization.md` and DELETE this file.
"""
import pickle

import pytest

from amplifier_agent_lib.bundle import BUNDLE_MD


@pytest.mark.asyncio
async def test_prepared_bundle_pickle_roundtrip():
    from amplifier_foundation import load_bundle

    bundle = await load_bundle(f"file://{BUNDLE_MD}")
    prepared = await bundle.prepare(install_deps=True)

    try:
        data = pickle.dumps(prepared)
    except (pickle.PicklingError, TypeError, AttributeError) as exc:
        pytest.skip(f"PreparedBundle not picklable: {type(exc).__name__}: {exc}")

    revived = pickle.loads(data)
    assert revived.mount_plan == prepared.mount_plan
```

**Step 2: Run the spike**

Run: `uv run pytest tests/spike_test_prepared_pickle.py -v -s`

Three possible outcomes:
- **PASS** → cache strategy is `pickle.dumps(prepared)`. Cheapest reload. Choose `STRATEGY = "pickle"`.
- **SKIP** with PicklingError/TypeError → cache strategy is "store the composed bundle SOURCE; re-run `prepare(install_deps=False)` on each load." Choose `STRATEGY = "source"`.
- **FAIL with assertion mismatch** → mount_plan is not preserved by pickle. Treat as if SKIP — choose `STRATEGY = "source"`.

**Step 3: Record the decision**

Create `docs/decisions/2026-05-18-cache-serialization.md`:

```markdown
# Phase 4 Cache Serialization Strategy

**Date:** 2026-05-18
**Status:** Decided

## Question
Should `bundle/cache.py` store the `PreparedBundle` as a pickle, or store
the composed bundle source and re-run `prepare(install_deps=False)` on each
warm load?

## Evidence
Spike test `tests/spike_test_prepared_pickle.py` outcome: **<PASTE OUTCOME>**

## Decision
**Chosen strategy:** `<pickle | source>`

## Rationale
- If pickle PASSED: a single deserialize call gives us the prepared
  artifact with no further foundation work. ~10ms warm load.
- If pickle SKIPPED or FAILED: we cache the composed bundle YAML on disk,
  and on warm load we call `Bundle.prepare(install_deps=False)`. The
  `install_deps=False` flag skips the slow network/install path; we get
  module activation only. ~100-500ms warm load (still acceptable per
  Phase 2.0e gate; >>10x faster than cold first-invocation).

## Implementation
The rest of Phase 4 uses `STRATEGY = "<pickle | source>"`. Tasks 4-7 are
written for both branches; follow the one that matches.
```

**Step 4: Delete the spike test**

```bash
rm tests/spike_test_prepared_pickle.py
```

The spike is not part of the test suite — its purpose was a one-time empirical decision.

**Step 5: Commit**

```bash
git add docs/decisions/2026-05-18-cache-serialization.md
git commit -m "chore(bundle): record cache serialization strategy decision"
```

> **For the rest of this plan**, the symbol `<STRATEGY>` refers to the chosen strategy. Where tasks below have a `[pickle]` and a `[source]` branch, follow the one matching your decision; ignore the other.

---

## Task 3: Implement `bundle/loader.py` against the real foundation

**Files:**
- Create: `src/amplifier_agent_lib/bundle/loader.py`
- Create: `tests/test_bundle_loader.py`

**Step 1: Write the failing test**

Create `tests/test_bundle_loader.py`:

```python
"""Loader: turn the vendored bundle.md into a PreparedBundle.

These tests exercise the real `amplifier_foundation.load_bundle()` +
`Bundle.prepare()` path. They are integration tests — expect them to
take seconds (cold) but never minutes.
"""
import pytest

from amplifier_agent_lib.bundle.loader import load_and_prepare_bundle


@pytest.mark.asyncio
async def test_load_and_prepare_returns_prepared_bundle():
    prepared = await load_and_prepare_bundle()
    assert prepared is not None
    assert hasattr(prepared, "mount_plan")
    assert isinstance(prepared.mount_plan, dict)


@pytest.mark.asyncio
async def test_prepared_bundle_declares_context_persistent():
    prepared = await load_and_prepare_bundle()
    session = prepared.mount_plan.get("session", {})
    context = session.get("context", {})
    assert context.get("module") == "context-persistent"


@pytest.mark.asyncio
async def test_load_and_prepare_accepts_override_path(tmp_path):
    """Dev/test override: --bundle <path> bypasses the vendored bundle."""
    override = tmp_path / "alt-bundle.md"
    override.write_text(
        "---\nbundle:\n  name: alt-bundle\n  version: 0.0.1\nincludes: []\n---\n"
    )
    prepared = await load_and_prepare_bundle(override_path=override)
    assert prepared.mount_plan.get("bundle", {}).get("name") == "alt-bundle"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_bundle_loader.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'amplifier_agent_lib.bundle.loader'`.

**Step 3: Implement the loader**

Create `src/amplifier_agent_lib/bundle/loader.py`:

```python
"""Load and prepare the vendored built-in bundle.

This module is the single entry point for turning the on-disk `bundle.md`
into a `PreparedBundle` ready for `AmplifierSession`. It does NOT cache;
caching lives in `bundle/cache.py`. This module is the "cold path."

The `override_path` parameter exists for dev/testing only — production
callers always use the vendored bundle (Brian D4: sealed).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from amplifier_agent_lib.bundle import BUNDLE_MD

if TYPE_CHECKING:
    from amplifier_foundation.bundle._prepared import PreparedBundle


async def load_and_prepare_bundle(
    override_path: Path | None = None,
    install_deps: bool = True,
) -> "PreparedBundle":
    """Load the vendored bundle.md, compose, and prepare.

    Args:
        override_path: Dev/test only. When set, loads this bundle.md
            instead of the vendored one. Production callers must NOT
            pass this.
        install_deps: Whether to install Python dependencies during
            prepare. Cold paths pass True; warm paths (when re-preparing
            from a cached source) pass False.

    Returns:
        PreparedBundle with `mount_plan` ready for `AmplifierSession`.
    """
    from amplifier_foundation import load_bundle

    target = override_path if override_path is not None else BUNDLE_MD
    if not target.exists():
        raise FileNotFoundError(f"bundle.md not found at {target}")

    bundle = await load_bundle(f"file://{target}")
    prepared = await bundle.prepare(install_deps=install_deps)
    return prepared
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_bundle_loader.py -v`
Expected: 3 tests PASS. First invocation may be slow (cold deps install); subsequent runs faster.

**Step 5: Commit**

```bash
git add src/amplifier_agent_lib/bundle/loader.py tests/test_bundle_loader.py
git commit -m "feat(bundle): load and prepare vendored bundle.md via amplifier-foundation"
```

---

## Task 4: Implement `bundle/cache.py` — cold path (first invocation: prepare + write)

**Files:**
- Create: `src/amplifier_agent_lib/bundle/cache.py`
- Create: `tests/test_bundle_cache.py`

**Step 1: Write the failing test (cold path only)**

Create `tests/test_bundle_cache.py`:

```python
"""Cache: first-invocation prepare-and-cache, subsequent invocations read."""
from pathlib import Path

import pytest

from amplifier_agent_lib.bundle.cache import (
    cache_dir_for_version,
    load_and_prepare_cached,
)


@pytest.mark.asyncio
async def test_cold_invocation_creates_cache(tmp_path, monkeypatch):
    """First call: no cache exists → prepare + write."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    cache_root = cache_dir_for_version("1.0.0")
    assert not cache_root.exists(), "precondition: cache must not exist"

    prepared = await load_and_prepare_cached(aaa_version="1.0.0")

    assert prepared is not None
    assert cache_root.exists(), "cache directory should exist after cold call"
    # exactly one cached artifact in the version dir
    artifacts = [p for p in cache_root.iterdir() if p.is_file()]
    assert len(artifacts) >= 1, "expected at least one cached artifact"


@pytest.mark.asyncio
async def test_cache_dir_is_xdg_keyed(tmp_path, monkeypatch):
    """cache_dir_for_version respects $XDG_CACHE_HOME and version keying."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    v1 = cache_dir_for_version("1.0.0")
    v2 = cache_dir_for_version("2.0.0")
    assert v1.parent == v2.parent, "version dirs must share a parent"
    assert v1.name == "1.0.0"
    assert v2.name == "2.0.0"
    assert str(tmp_path) in str(v1)
    assert "amplifier-agent" in str(v1)
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_bundle_cache.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'amplifier_agent_lib.bundle.cache'`.

**Step 3: Implement the cold path**

Create `src/amplifier_agent_lib/bundle/cache.py`. Pick the branch matching your Task 2 decision.

**`[pickle]` branch** — if Task 2 chose pickle:

```python
"""Prepare-and-cache the vendored bundle to XDG cache.

Cache layout:
    $XDG_CACHE_HOME/amplifier-agent/prepared/<aaa_version>/
        prepared.pickle    — PreparedBundle pickled (strategy: pickle)
        manifest.json      — { aaa_version, foundation_version, sha256 }

Cache key: AaA package version. Bumping AaA invalidates the cache
automatically. Corruption is treated as a cache miss and rebuilt.
"""

from __future__ import annotations

import json
import os
import pickle
from pathlib import Path
from typing import TYPE_CHECKING

from amplifier_agent_lib.bundle.loader import load_and_prepare_bundle

if TYPE_CHECKING:
    from amplifier_foundation.bundle._prepared import PreparedBundle

_CACHE_SUBDIR = "amplifier-agent/prepared"
_ARTIFACT_NAME = "prepared.pickle"
_MANIFEST_NAME = "manifest.json"


def _xdg_cache_home() -> Path:
    raw = os.environ.get("XDG_CACHE_HOME")
    if raw:
        return Path(raw)
    return Path.home() / ".cache"


def cache_dir_for_version(aaa_version: str) -> Path:
    return _xdg_cache_home() / _CACHE_SUBDIR / aaa_version


async def load_and_prepare_cached(
    aaa_version: str,
) -> "PreparedBundle":
    """Return the prepared bundle, hitting the cache when possible."""
    cache_dir = cache_dir_for_version(aaa_version)
    cache_dir.mkdir(parents=True, exist_ok=True)
    artifact = cache_dir / _ARTIFACT_NAME
    manifest = cache_dir / _MANIFEST_NAME

    # Cold path: no artifact present. (Warm path lands in Task 5.)
    prepared = await load_and_prepare_bundle()
    artifact.write_bytes(pickle.dumps(prepared))
    manifest.write_text(json.dumps({"aaa_version": aaa_version}))
    return prepared
```

**`[source]` branch** — if Task 2 chose source-recompose:

```python
"""Prepare-and-cache the composed bundle source to XDG cache.

Cache layout:
    $XDG_CACHE_HOME/amplifier-agent/prepared/<aaa_version>/
        composed.md        — composed bundle source (strategy: source)
        manifest.json      — { aaa_version }

Warm path re-runs Bundle.prepare(install_deps=False) — fast (no install),
not free (~100-500ms for module activation).
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from amplifier_agent_lib.bundle import BUNDLE_MD
from amplifier_agent_lib.bundle.loader import load_and_prepare_bundle

if TYPE_CHECKING:
    from amplifier_foundation.bundle._prepared import PreparedBundle

_CACHE_SUBDIR = "amplifier-agent/prepared"
_ARTIFACT_NAME = "composed.md"
_MANIFEST_NAME = "manifest.json"


def _xdg_cache_home() -> Path:
    raw = os.environ.get("XDG_CACHE_HOME")
    if raw:
        return Path(raw)
    return Path.home() / ".cache"


def cache_dir_for_version(aaa_version: str) -> Path:
    return _xdg_cache_home() / _CACHE_SUBDIR / aaa_version


async def load_and_prepare_cached(
    aaa_version: str,
) -> "PreparedBundle":
    """Return the prepared bundle, hitting the cache when possible."""
    cache_dir = cache_dir_for_version(aaa_version)
    cache_dir.mkdir(parents=True, exist_ok=True)
    artifact = cache_dir / _ARTIFACT_NAME
    manifest = cache_dir / _MANIFEST_NAME

    # Cold path: no artifact present. (Warm path lands in Task 5.)
    prepared = await load_and_prepare_bundle(install_deps=True)
    shutil.copy(BUNDLE_MD, artifact)
    manifest.write_text(json.dumps({"aaa_version": aaa_version}))
    return prepared
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_bundle_cache.py -v`
Expected: 2 tests PASS.

**Step 5: Commit**

```bash
git add src/amplifier_agent_lib/bundle/cache.py tests/test_bundle_cache.py
git commit -m "feat(bundle): cold-path prepare-and-cache to XDG"
```

---

## Task 5: Cache warm path (cache hit → load without prepare)

**Files:**
- Modify: `src/amplifier_agent_lib/bundle/cache.py`
- Modify: `tests/test_bundle_cache.py`

**Step 1: Add the failing warm-path test**

Append to `tests/test_bundle_cache.py`:

```python
@pytest.mark.asyncio
async def test_warm_invocation_hits_cache(tmp_path, monkeypatch):
    """Second call with same version → cache hit, no re-prepare network."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    # Cold call writes the cache
    first = await load_and_prepare_cached(aaa_version="1.0.0")
    assert first is not None

    # Sentinel: corrupt the loader so a fresh prepare would fail.
    # If the warm path actually short-circuits to the cache, this is fine.
    from amplifier_agent_lib.bundle import cache as cache_mod
    real_loader = cache_mod.load_and_prepare_bundle

    async def boom(*args, **kwargs):
        raise RuntimeError("loader should not be called on warm path")

    monkeypatch.setattr(cache_mod, "load_and_prepare_bundle", boom)

    # Warm call must succeed without invoking the loader
    second = await load_and_prepare_cached(aaa_version="1.0.0")
    assert second is not None
    # Restore (defensive — monkeypatch handles this on teardown)
    monkeypatch.setattr(cache_mod, "load_and_prepare_bundle", real_loader)
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_bundle_cache.py::test_warm_invocation_hits_cache -v`
Expected: FAIL with `RuntimeError: loader should not be called on warm path` (the current cold-only implementation always calls the loader).

**Step 3: Add the warm-path branch**

In `src/amplifier_agent_lib/bundle/cache.py`, replace the body of `load_and_prepare_cached` with the version below for your strategy.

**`[pickle]` branch:**

```python
async def load_and_prepare_cached(
    aaa_version: str,
) -> "PreparedBundle":
    cache_dir = cache_dir_for_version(aaa_version)
    cache_dir.mkdir(parents=True, exist_ok=True)
    artifact = cache_dir / _ARTIFACT_NAME
    manifest = cache_dir / _MANIFEST_NAME

    # Warm path
    if artifact.exists() and manifest.exists():
        return pickle.loads(artifact.read_bytes())

    # Cold path
    prepared = await load_and_prepare_bundle()
    artifact.write_bytes(pickle.dumps(prepared))
    manifest.write_text(json.dumps({"aaa_version": aaa_version}))
    return prepared
```

**`[source]` branch:**

```python
async def load_and_prepare_cached(
    aaa_version: str,
) -> "PreparedBundle":
    cache_dir = cache_dir_for_version(aaa_version)
    cache_dir.mkdir(parents=True, exist_ok=True)
    artifact = cache_dir / _ARTIFACT_NAME
    manifest = cache_dir / _MANIFEST_NAME

    # Warm path: composed source on disk → re-prepare with install_deps=False
    if artifact.exists() and manifest.exists():
        return await load_and_prepare_bundle(
            override_path=artifact, install_deps=False
        )

    # Cold path
    prepared = await load_and_prepare_bundle(install_deps=True)
    shutil.copy(BUNDLE_MD, artifact)
    manifest.write_text(json.dumps({"aaa_version": aaa_version}))
    return prepared
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_bundle_cache.py -v`
Expected: 3 tests PASS.

**Step 5: Commit**

```bash
git add src/amplifier_agent_lib/bundle/cache.py tests/test_bundle_cache.py
git commit -m "feat(bundle): warm-path cache read short-circuits prepare"
```

---

## Task 6: Cache version invalidation (new AaA version → re-prepare)

**Files:**
- Modify: `tests/test_bundle_cache.py`

**Step 1: Add the failing test**

Append to `tests/test_bundle_cache.py`:

```python
@pytest.mark.asyncio
async def test_new_version_invalidates_cache(tmp_path, monkeypatch):
    """Bumping AaA version writes to a new dir; old dir untouched."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    await load_and_prepare_cached(aaa_version="1.0.0")
    v1_dir = cache_dir_for_version("1.0.0")
    assert v1_dir.exists()

    await load_and_prepare_cached(aaa_version="2.0.0")
    v2_dir = cache_dir_for_version("2.0.0")
    assert v2_dir.exists()
    assert v1_dir != v2_dir
    assert v1_dir.exists(), "old version cache must NOT be deleted (downgrade safe)"
```

**Step 2: Run test to verify it passes**

Run: `uv run pytest tests/test_bundle_cache.py::test_new_version_invalidates_cache -v`

This test should already PASS — the cache directory is version-keyed in Task 4 by construction. If it FAILS, there's a bug in your `cache_dir_for_version` function; fix it.

Expected: PASS.

**Step 3: Commit (test-only)**

```bash
git add tests/test_bundle_cache.py
git commit -m "test(bundle): pin version-keyed cache invalidation"
```

---

## Task 7: Corrupted-cache recovery (bad pickle/source → re-prepare with warning)

**Files:**
- Modify: `src/amplifier_agent_lib/bundle/cache.py`
- Modify: `tests/test_bundle_cache.py`

**Step 1: Add the failing test**

Append to `tests/test_bundle_cache.py`:

```python
@pytest.mark.asyncio
async def test_corrupted_cache_triggers_rebuild(tmp_path, monkeypatch, caplog):
    """A corrupted cache file is treated as a miss; rebuild succeeds."""
    import logging

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    # Prime the cache
    await load_and_prepare_cached(aaa_version="1.0.0")
    cache_root = cache_dir_for_version("1.0.0")
    artifacts = [p for p in cache_root.iterdir() if p.is_file() and p.name != "manifest.json"]
    assert artifacts, "expected an artifact to corrupt"

    # Corrupt it
    artifacts[0].write_bytes(b"not-a-valid-cache-artifact-\x00\xff")

    # Re-load: must NOT raise; should rebuild silently or with a warning
    with caplog.at_level(logging.WARNING, logger="amplifier_agent_lib.bundle.cache"):
        prepared = await load_and_prepare_cached(aaa_version="1.0.0")

    assert prepared is not None
    assert any("cache" in r.message.lower() for r in caplog.records), \
        "expected a warning log about cache rebuild"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_bundle_cache.py::test_corrupted_cache_triggers_rebuild -v`
Expected: FAIL — corrupted-cache handling not yet implemented. The error will be a deserialization exception (`UnpicklingError` or YAML parse error).

**Step 3: Add corruption-tolerant deserialization**

Modify `src/amplifier_agent_lib/bundle/cache.py`. Add at the top:

```python
import logging

logger = logging.getLogger(__name__)
```

Wrap the warm-path read in try/except.

**`[pickle]` branch — warm path becomes:**

```python
    if artifact.exists() and manifest.exists():
        try:
            return pickle.loads(artifact.read_bytes())
        except Exception as exc:  # broad: corrupt cache → rebuild
            logger.warning(
                "Cache artifact at %s is corrupted (%s); rebuilding.",
                artifact, type(exc).__name__,
            )
            artifact.unlink(missing_ok=True)
            manifest.unlink(missing_ok=True)
```

**`[source]` branch — warm path becomes:**

```python
    if artifact.exists() and manifest.exists():
        try:
            return await load_and_prepare_bundle(
                override_path=artifact, install_deps=False
            )
        except Exception as exc:  # broad: corrupt cache → rebuild
            logger.warning(
                "Cache artifact at %s is corrupted (%s); rebuilding.",
                artifact, type(exc).__name__,
            )
            artifact.unlink(missing_ok=True)
            manifest.unlink(missing_ok=True)
```

After the try/except, control falls through to the cold path, which re-prepares and re-writes.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_bundle_cache.py -v`
Expected: all 5 tests PASS.

**Step 5: Commit**

```bash
git add src/amplifier_agent_lib/bundle/cache.py tests/test_bundle_cache.py
git commit -m "feat(bundle): rebuild on corrupted cache with warning"
```

---

## Task 8: Wire `Engine.boot()` to use the real cached bundle

The Phase 1 engine accepts a mock bundle. Phase 4 makes the cache the default and keeps mock injection behind an explicit `bundle_override` parameter for tests.

**Files:**
- Modify: `src/amplifier_agent_lib/engine.py`
- Create: `tests/test_engine_with_real_bundle.py`

**Step 1: Write the failing integration test**

Create `tests/test_engine_with_real_bundle.py`:

```python
"""Engine boots against the real vendored bundle (no mocks)."""
import pytest

from amplifier_agent_lib.engine import Engine


@pytest.mark.asyncio
async def test_engine_boots_with_real_bundle(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    # No bundle_override → must hit the cache path
    engine = await Engine.boot(session_id="test-real-bundle")
    try:
        # Smoke: the engine has a session with a mount plan
        assert engine.session is not None
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_engine_accepts_bundle_override_for_tests(tmp_path):
    """bundle_override bypasses the cache (test-only path)."""
    # A stub PreparedBundle-like object suffices since we only verify wiring
    class StubPrepared:
        mount_plan = {"session": {}, "tools": []}
        resolver = None

    engine = await Engine.boot(
        session_id="test-stub",
        bundle_override=StubPrepared(),  # type: ignore[arg-type]
    )
    try:
        # The stub was accepted — engine did NOT touch the cache
        assert engine.session is not None
    finally:
        await engine.shutdown()
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_engine_with_real_bundle.py -v`
Expected: FAIL. Phase 1's `Engine.boot()` does not yet call the cache; the signature probably also doesn't have `bundle_override`.

**Step 3: Modify `Engine.boot()` — read Phase 1 first**

Read `src/amplifier_agent_lib/engine.py` to confirm the current `boot()` signature. The change should:

1. Add `bundle_override: PreparedBundle | None = None` (or equivalent) parameter.
2. If `bundle_override` is None, call `await load_and_prepare_cached(aaa_version=__version__)`.
3. Otherwise, use the override.

A representative patch (adapt to the actual Phase 1 shape):

```python
# Add at the top of engine.py (or wherever imports live)
from amplifier_agent_lib import __version__
from amplifier_agent_lib.bundle.cache import load_and_prepare_cached

# In Engine.boot signature:
async def boot(
    cls,
    *,
    session_id: str,
    bundle_override: "PreparedBundle | None" = None,
    # ... existing Phase 1 / 2 / 3 params (approval_system, display_system, etc.)
) -> "Engine":
    prepared = bundle_override or await load_and_prepare_cached(
        aaa_version=__version__
    )
    # ... existing logic that turns `prepared` into an AmplifierSession
```

If `__version__` is not yet exposed by Phase 1, add it to `src/amplifier_agent_lib/__init__.py`:

```python
__version__ = "0.1.0"
```

(Keep it in sync with `pyproject.toml`.)

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_engine_with_real_bundle.py -v`
Expected: 2 tests PASS.

**Step 5: Confirm Phase 1 tests still pass**

Run: `uv run pytest tests/ -v -x`
Expected: every existing test still passes. If a Phase 1 test relied on `Engine.boot()` having NO `bundle_override` keyword, update those tests to pass a stub instead of mocking deeper internals.

**Step 6: Commit**

```bash
git add src/amplifier_agent_lib/engine.py src/amplifier_agent_lib/__init__.py tests/test_engine_with_real_bundle.py
git commit -m "feat(engine): boot from cached real bundle; bundle_override for tests"
```

---

## Task 9: Wire `admin/cache_clear.py` to actually clear the new cache

Phase 2's `cache clear` is a stub. Make it remove the real XDG cache dir.

**Files:**
- Modify: `src/amplifier_agent_cli/admin/cache_clear.py`
- Create: `tests/test_admin_cache_clear.py`

**Step 1: Write the failing test**

Create `tests/test_admin_cache_clear.py`:

```python
"""`amplifier-agent cache clear` removes the prepared bundle cache."""
from amplifier_agent_lib.bundle.cache import cache_dir_for_version
from amplifier_agent_cli.admin.cache_clear import clear_cache


def test_clear_cache_removes_xdg_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    v = cache_dir_for_version("1.0.0")
    v.mkdir(parents=True)
    (v / "prepared.pickle").write_bytes(b"fake")
    (v / "manifest.json").write_text("{}")

    result = clear_cache()
    assert result.existed is True
    assert not v.exists(), "version dir should be removed"


def test_clear_cache_is_idempotent(tmp_path, monkeypatch):
    """Calling clear twice in a row must not raise."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    clear_cache()
    clear_cache()  # must not raise
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_admin_cache_clear.py -v`
Expected: FAIL. The Phase 2 stub probably returns `None` or doesn't accept the same interface; signature mismatch.

**Step 3: Implement real clearing**

Replace `src/amplifier_agent_cli/admin/cache_clear.py` body with:

```python
"""`amplifier-agent cache clear` admin verb."""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from amplifier_agent_lib.bundle.cache import cache_dir_for_version


@dataclass
class ClearResult:
    removed_path: Path
    existed: bool


def clear_cache() -> ClearResult:
    """Remove the entire amplifier-agent prepared cache root.

    Returns a `ClearResult` describing what was removed. Idempotent —
    repeated calls do not error.
    """
    # cache_dir_for_version("any").parent is the root that holds all
    # version subdirs.
    root = cache_dir_for_version("_").parent
    existed = root.exists()
    if existed:
        shutil.rmtree(root)
    return ClearResult(removed_path=root, existed=existed)


def main() -> int:
    result = clear_cache()
    if result.existed:
        print(f"Removed cache at {result.removed_path}", file=sys.stderr)
    else:
        print(f"No cache present at {result.removed_path}", file=sys.stderr)
    return 0
```

If the Phase 2 entry-point dispatcher (`__main__.py`) calls a different function name (e.g. `cache_clear.run()` or `cache_clear.cli()`), keep that name too — make it a thin wrapper around `main()`:

```python
def run() -> int:  # legacy name from Phase 2
    return main()
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_admin_cache_clear.py -v`
Expected: 2 tests PASS.

**Step 5: Commit**

```bash
git add src/amplifier_agent_cli/admin/cache_clear.py tests/test_admin_cache_clear.py
git commit -m "feat(cli): wire cache clear to remove XDG prepared cache"
```

---

## Task 10: Wire `admin/doctor.py` to report cache status

**Files:**
- Modify: `src/amplifier_agent_cli/admin/doctor.py`
- Create: `tests/test_admin_doctor_cache.py`

**Step 1: Write the failing test**

Create `tests/test_admin_doctor_cache.py`:

```python
"""`amplifier-agent doctor` reports cache state."""
from amplifier_agent_cli.admin.doctor import check_cache_state
from amplifier_agent_lib.bundle.cache import cache_dir_for_version


def test_doctor_reports_not_prepared(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    state = check_cache_state(aaa_version="1.0.0")
    assert state.status == "needs prepare"
    assert state.cache_dir == cache_dir_for_version("1.0.0")


def test_doctor_reports_prepared(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    v = cache_dir_for_version("1.0.0")
    v.mkdir(parents=True)
    (v / "manifest.json").write_text('{"aaa_version": "1.0.0"}')
    # touch the artifact regardless of strategy
    (v / "prepared.pickle").write_bytes(b"x")
    (v / "composed.md").write_text("---\n---\n")
    state = check_cache_state(aaa_version="1.0.0")
    assert state.status == "prepared"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_admin_doctor_cache.py -v`
Expected: FAIL — `check_cache_state` doesn't exist yet.

**Step 3: Add the cache check to doctor.py**

Add to `src/amplifier_agent_cli/admin/doctor.py`:

```python
from dataclasses import dataclass
from pathlib import Path

from amplifier_agent_lib.bundle.cache import cache_dir_for_version


@dataclass
class CacheState:
    status: str          # "prepared" | "needs prepare"
    cache_dir: Path


def check_cache_state(aaa_version: str) -> CacheState:
    """Inspect XDG cache and report whether a prepared bundle exists."""
    cache_dir = cache_dir_for_version(aaa_version)
    manifest = cache_dir / "manifest.json"
    if cache_dir.exists() and manifest.exists():
        # any non-manifest file counts as the artifact
        artifacts = [
            p for p in cache_dir.iterdir()
            if p.is_file() and p.name != "manifest.json"
        ]
        if artifacts:
            return CacheState(status="prepared", cache_dir=cache_dir)
    return CacheState(status="needs prepare", cache_dir=cache_dir)
```

Then wire it into doctor's existing report output (add a `Cache: <status> (<path>)` line to whatever Phase 2 `doctor` already prints). Pseudo-patch for the `main()` (or `run()`) function in doctor.py:

```python
from amplifier_agent_lib import __version__

# inside the existing doctor main():
cache = check_cache_state(__version__)
print(f"Cache: {cache.status} ({cache.cache_dir})", file=sys.stderr)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_admin_doctor_cache.py -v`
Expected: 2 tests PASS.

**Step 5: Smoke-test the doctor command end-to-end**

Run: `uv run amplifier-agent doctor`
Expected stderr (or stdout, per Phase 2 convention) contains a line like `Cache: needs prepare (<...>/amplifier-agent/prepared/0.1.0)` on a fresh checkout.

**Step 6: Commit**

```bash
git add src/amplifier_agent_cli/admin/doctor.py tests/test_admin_doctor_cache.py
git commit -m "feat(cli): doctor reports prepared-bundle cache status"
```

---

## Task 11: Implement `post_install.py` + `pyproject.toml` hook entry

**Files:**
- Create: `src/amplifier_agent_lib/post_install.py`
- Modify: `pyproject.toml`
- Create: `tests/test_post_install.py`

**Step 1: Write the failing test**

Create `tests/test_post_install.py`:

```python
"""Post-install hook: primes the cache; idempotent; never raises on error."""
import pytest

from amplifier_agent_lib.bundle.cache import cache_dir_for_version
from amplifier_agent_lib.post_install import main as post_install_main


@pytest.mark.asyncio
async def test_post_install_primes_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    from amplifier_agent_lib import __version__
    cache = cache_dir_for_version(__version__)
    assert not cache.exists()

    exit_code = await post_install_main()
    assert exit_code == 0
    assert cache.exists(), "post-install must have primed the cache"


@pytest.mark.asyncio
async def test_post_install_is_idempotent(tmp_path, monkeypatch):
    """Second run must not re-prepare, must not raise, must exit 0."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert await post_install_main() == 0
    assert await post_install_main() == 0


@pytest.mark.asyncio
async def test_post_install_swallows_errors(tmp_path, monkeypatch):
    """A failed prepare must NOT fail the installer; exit 0 + stderr warning."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    from amplifier_agent_lib import post_install as pi

    async def boom(*args, **kwargs):
        raise RuntimeError("simulated prepare failure")

    monkeypatch.setattr(pi, "load_and_prepare_cached", boom)
    exit_code = await post_install_main()
    assert exit_code == 0, "post-install must exit 0 even on failure"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_post_install.py -v`
Expected: FAIL — module doesn't exist.

**Step 3: Implement the post-install hook**

Create `src/amplifier_agent_lib/post_install.py`:

```python
"""Post-install hook for `uv tool install amplifier-agent`.

Primes the XDG prepared-bundle cache so first runtime invocations don't
pay the cold-prepare cost. Failures here NEVER fail the install — the
runtime first-invocation path is the safety net.

Run as a script:
    python -m amplifier_agent_lib.post_install
or via the [project.scripts] entry `amplifier-agent-post-install`.
"""

from __future__ import annotations

import asyncio
import sys

from amplifier_agent_lib import __version__
from amplifier_agent_lib.bundle.cache import (
    cache_dir_for_version,
    load_and_prepare_cached,
)


async def main() -> int:
    cache_dir = cache_dir_for_version(__version__)
    manifest = cache_dir / "manifest.json"
    if cache_dir.exists() and manifest.exists():
        print(
            f"amplifier-agent: cache already prepared at {cache_dir}",
            file=sys.stderr,
        )
        return 0

    try:
        await load_and_prepare_cached(aaa_version=__version__)
        print(
            f"amplifier-agent: prepared bundle cached at {cache_dir}",
            file=sys.stderr,
        )
    except Exception as exc:
        print(
            f"amplifier-agent: post-install cache prime failed ({exc}); "
            "first invocation will prepare instead.",
            file=sys.stderr,
        )
    return 0


def cli_entry() -> None:
    raise SystemExit(asyncio.run(main()))


if __name__ == "__main__":
    cli_entry()
```

Modify `pyproject.toml` — add the entry-point under `[project.scripts]`:

```toml
[project.scripts]
amplifier-agent = "amplifier_agent_cli.__main__:main"
amplifier-agent-post-install = "amplifier_agent_lib.post_install:cli_entry"
```

> **Note on hook semantics:** `uv tool install` does not yet have a generic post-install hook spec equivalent to setuptools' bdist hooks. Shipping `amplifier-agent-post-install` as an entry-point script means installers (curl scripts, containerized installs) invoke it explicitly: `uv tool install amplifier-agent && amplifier-agent-post-install`. This is documented in §2 install-paths (deferred); Phase 4 ships the hook itself.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_post_install.py -v`
Expected: 3 tests PASS.

**Step 5: Commit**

```bash
git add src/amplifier_agent_lib/post_install.py tests/test_post_install.py pyproject.toml
git commit -m "feat(install): post-install hook primes prepared-bundle cache"
```

---

## Task 12: End-to-end integration — clean venv install → doctor → run

The capstone test for Phase 4. Verifies the full pipeline: build wheel → install into an isolated venv → run post-install → run `amplifier-agent` and observe the cache transition from "needs prepare" to "prepared."

**Files:**
- Create: `tests/test_e2e_install_and_run.py`

**Step 1: Write the failing test**

Create `tests/test_e2e_install_and_run.py`:

```python
"""End-to-end: install AaA into an isolated venv and run a one-turn invocation.

This test is slow (tens of seconds to a few minutes on first run). Mark
it `integration` so it can be skipped in fast CI tiers.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.integration
def test_install_then_doctor_reports_needs_prepare(tmp_path):
    if shutil.which("uv") is None:
        pytest.skip("uv not on PATH")

    repo = Path(__file__).resolve().parents[1]
    venv = tmp_path / "venv"
    cache_home = tmp_path / "cache"
    cache_home.mkdir()

    env = {
        **os.environ,
        "XDG_CACHE_HOME": str(cache_home),
        "VIRTUAL_ENV": str(venv),
    }

    # Build + install from local source
    subprocess.check_call(["uv", "venv", str(venv)], env=env)
    subprocess.check_call(
        ["uv", "pip", "install", "--python", str(venv / "bin" / "python"), "-e", str(repo)],
        env=env,
    )
    binary = venv / "bin" / "amplifier-agent"
    assert binary.exists()

    # Before priming: doctor reports "needs prepare"
    result = subprocess.run(
        [str(binary), "doctor"], env=env, capture_output=True, text=True
    )
    combined = result.stdout + result.stderr
    assert "needs prepare" in combined.lower()


@pytest.mark.integration
def test_post_install_then_doctor_reports_prepared(tmp_path):
    if shutil.which("uv") is None:
        pytest.skip("uv not on PATH")

    repo = Path(__file__).resolve().parents[1]
    venv = tmp_path / "venv"
    cache_home = tmp_path / "cache"
    cache_home.mkdir()
    env = {
        **os.environ,
        "XDG_CACHE_HOME": str(cache_home),
        "VIRTUAL_ENV": str(venv),
    }

    subprocess.check_call(["uv", "venv", str(venv)], env=env)
    subprocess.check_call(
        ["uv", "pip", "install", "--python", str(venv / "bin" / "python"), "-e", str(repo)],
        env=env,
    )

    post_install = venv / "bin" / "amplifier-agent-post-install"
    subprocess.check_call([str(post_install)], env=env)

    doctor = subprocess.run(
        [str(venv / "bin" / "amplifier-agent"), "doctor"],
        env=env, capture_output=True, text=True,
    )
    combined = doctor.stdout + doctor.stderr
    assert "prepared" in combined.lower()
    assert "needs prepare" not in combined.lower()
```

Also ensure `pyproject.toml` has the `integration` marker registered (Phase 2 may already have it):

```toml
[tool.pytest.ini_options]
markers = [
    "integration: slow end-to-end tests requiring full install",
]
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_e2e_install_and_run.py -v -m integration`
Expected: depending on Phases 1-3 entry-point shape, either it passes immediately (everything wired) or fails on `doctor` output not containing the expected strings. Inspect and fix.

**Step 3: Run test to verify it passes**

If failing, the likely fix is in doctor's output formatting (Task 10). Adjust `doctor` to print exact substrings `"prepared"` or `"needs prepare"` and rerun.

Run: `uv run pytest tests/test_e2e_install_and_run.py -v -m integration`
Expected: 2 tests PASS. (Slow: 30s-2min on first run, depending on dependency install time.)

**Step 4: Run the FULL test suite one final time**

Run: `uv run pytest tests/ -v`
Expected: all tests pass. If anything from Phases 1-3 broke during Phase 4, fix before continuing.

**Step 5: Commit**

```bash
git add tests/test_e2e_install_and_run.py pyproject.toml
git commit -m "test(e2e): install + post-install + doctor verifies cache transition"
```

---

## What's next (after Phase 4)

Phase 4 completes **Layer 4** of the v2 design. The next gate is **Phase 5: cold-start measurement** (currently deferred per the user's directive). The plan in §9 of the design checkpoint becomes:

| Phase | Deliverable | Why it comes next |
|---|---|---|
| **5 (deferred)** | Cold-start measurement scaffold + N=100 sample on representative hardware: first-invocation latency, cached-invocation latency, p50/p95/p99 | This is the decision gate from §2.0e of the design. If steady-state cold-start is <200ms, Mode B (the Phase 3 `--stdio` loop) becomes unnecessary and Phase 6 can drop it. |
| **6** | Wire protocol spec hardening + cross-language conformance suite | The wire is the day-one artifact for L3 wrappers. |
| **7** | `amplifier-agent-client-ts` and `-py` wrappers | Implementation of the locked §4 design. |
| **8** | NanoClaw + Paperclip adapter rebuilds on L3 | Phase 2.4–2.5. |
| **9** | Install paths (§2 in the design, currently DEFERRED) + Containers (§7, DEFERRED) | Both depend on packaging-shape evidence and cold-start results. |

Phase 4 leaves the repo in a state where:
- `amplifier-agent run "hello world"` works end-to-end against a real bundle.
- `amplifier-agent --stdio` (Mode B) works end-to-end against a real bundle.
- `amplifier-agent doctor` accurately reports cache state.
- `amplifier-agent cache clear` actually clears the cache.
- `amplifier-agent-post-install` primes the cache at install time and is idempotent.

That's the L4 implementation-complete bar. Hand off to Phase 5 to measure.

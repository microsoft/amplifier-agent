# Baked-in Bundle (Strategy 1) — Phase 2: Manifest Rewrite + Packaging

> **Execution:** Use the `subagent-driven-development` workflow to implement this plan.

**Goal:** Rewrite `src/amplifier_agent_lib/bundle/bundle.md` to drop the broken `includes: build-up-foundation` block and replace it with explicit `tools:` / `hooks:` / `agents:` blocks pointing at module repos at `@main` and the vendored agent files from Phase 1. Update wheel packaging so the new agent markdown ships in the wheel. Add a regression test for the agents block.

**Architecture:** The manifest stops referencing `includes:`. Every dependency is declared inline: orchestrator (`loop-streaming`), context (`context-simple`), provider (`anthropic-provider`), the tools the vendored agents need (`tool-bash`, `tool-filesystem`, `tool-search`, `tool-todo`, `tool-delegate`), and the four agents resolved by file paths relative to the wheel-local `agents/` directory exposed in Phase 1. The wheel build config gains a glob include for `bundle/agents/*.md`.

**Tech Stack:** Python 3.12+, `hatchling` for build, `amplifier-foundation` for manifest parsing.

**Source of truth:** `docs/designs/2026-05-19-baked-in-bundle-decision.md` §3, §4, §9 items 2+3+6.

**Prerequisites:**
- Phase 1 plan is complete and merged into the working tree (`docs/plans/2026-05-19-baked-in-bundle-impl-phase-1.md`).
- `from amplifier_agent_lib.bundle import AGENTS_DIR` resolves.
- `cache_dir_for_version(...)` already hashes `bundle.md` — any edit to `bundle.md` during this phase will self-invalidate the warm cache.

**Out of scope:**
- Cache implementation (Phase 1).
- Documentation renames (Phase 3).
- Engine/wire-protocol/Mode A-B/provider injection — design doc §11.

**Conventions:** Same as Phase 1 — TDD per task, atomic commits, Conventional Commits, ruff + pyright clean throughout.

---

## Task 1: Discover foundation's manifest grammar for `agents:`, `tools:`, `hooks:`

**Why:** The current `bundle.md` declares only `session.orchestrator` and `session.context`. Phase 2 introduces three new top-level blocks (`tools`, `hooks`, `agents`). The exact YAML grammar foundation accepts is set by foundation's manifest parser, not by us. Before rewriting `bundle.md`, confirm the schema by reading foundation's source.

**Files:** none — read-only investigation.

**Step 1: Locate foundation's manifest parser**

Run:

```bash
uv pip show amplifier-foundation | grep Location
```

The path printed (call it `$FND`) holds the installed foundation source. Inspect:

```bash
ls "$FND/amplifier_foundation/bundle/"
grep -rn "^def \|class \|^    \"agents\"\|^    \"tools\"\|^    \"hooks\"" "$FND/amplifier_foundation/bundle/" | head -40
```

**Step 2: Record what you found**

Read the manifest parser file (likely `amplifier_foundation/bundle/_manifest.py` or `_loader.py`). In the implementer's session notes (no file commit needed), record:

- The exact top-level keys foundation accepts (`bundle`, `session`, `includes`, `tools`, `hooks`, `agents`, etc.).
- The schema for each `agents:` entry — is it `{ name, source }`, `{ module, source }`, `{ name, path }`, or something else? Specifically: how does foundation resolve a wheel-local file path?
- Whether `tools:` entries follow the same `{ module, source, config }` shape used inside an agent's frontmatter today.
- Whether `hooks:` is required or may be omitted.

**Step 3: Locate a working precedent**

Find one bundle in the foundation tree (or in app-cli) that uses the explicit blocks rather than `includes:`. The build-up agents themselves are working examples for the `tools:` shape (look at `experiments/build-up/agents/explorer.md` from Phase 1 Task 6). Bookmark the precedent file path — Task 3 will copy its shape.

**Step 4: Confirm with a tiny smoke test**

In a Python REPL (`uv run python`), run:

```python
import asyncio
from amplifier_foundation import load_bundle

async def main():
    # Try parsing a minimal explicit manifest. Path to a tmp file you create on disk:
    bundle = await load_bundle("file:///tmp/sketch.md")
    print(bundle.name, bundle.agents if hasattr(bundle, "agents") else "no agents attr")

asyncio.run(main())
```

The point of this exercise is to confirm what attribute the parsed bundle exposes for the agents block (`bundle.agents`? `bundle.session.agents`?). Knowing the exact attribute name unblocks the assertions in Task 11.

**No commit.** This is investigation. Record findings in scratch notes.

**Expected outcome:** the implementer can answer, with file:line references:
1. "The exact YAML schema for `agents:` is X."
2. "The exact attribute path on the parsed `Bundle` object is `bundle.<path>`."
3. "Foundation resolves an agent's file path by `<rule>`."

If any of these are unanswerable from foundation's source, STOP and report back — the manifest rewrite cannot proceed on guesses.

---

## Task 2: RED — failing test that asserts the new manifest is parseable and exposes agents

**Files:**
- Modify: `tests/test_bundle_loader.py` (append at end)

**Step 1: Add the failing test**

Append the following to `tests/test_bundle_loader.py`. **Note:** the assertion `prepared.bundle.agents` is a placeholder — if Task 1 discovered a different attribute path (e.g. `prepared.bundle.session.agents`, or `prepared.mount_plan["agents"]`), substitute the real path everywhere `prepared.bundle.agents` appears below.

```python


@pytest.mark.asyncio
async def test_vendored_bundle_has_no_includes_block() -> None:
    """Vendored bundle.md must not contain `includes:` — the resolver does not handle named-bundle URIs.

    Regression guard for `No handler for URI: build-up-foundation`. Per the Strategy 1
    decision in docs/designs/2026-05-19-baked-in-bundle-decision.md, the manifest is now
    self-describing with explicit modules + agents and no foundation include.
    """
    from amplifier_agent_lib.bundle import BUNDLE_MD

    content = BUNDLE_MD.read_text()
    assert "\nincludes:" not in content, "bundle.md must not contain a top-level `includes:` block"


@pytest.mark.asyncio
async def test_vendored_bundle_declares_all_four_agents() -> None:
    """Vendored bundle.md declares explorer/planner/coder/tester via the agents: block."""
    from amplifier_agent_lib.bundle.loader import load_and_prepare_bundle

    prepared = await load_and_prepare_bundle(install_deps=False)

    # Foundation's parsed Bundle exposes the agents block — exact attribute set in Task 1.
    agents_by_name = {a.name: a for a in prepared.bundle.agents}
    assert set(agents_by_name) >= {"explorer", "planner", "coder", "tester"}, (
        f"Expected all four vendored agents; got {sorted(agents_by_name)}"
    )


@pytest.mark.asyncio
async def test_vendored_agents_resolve_to_wheel_local_files() -> None:
    """Each agent in the agents: block resolves to a file under the bundle/agents/ directory."""
    from amplifier_agent_lib.bundle import AGENTS_DIR
    from amplifier_agent_lib.bundle.loader import load_and_prepare_bundle

    prepared = await load_and_prepare_bundle(install_deps=False)

    for agent in prepared.bundle.agents:
        # Foundation should have resolved each agent ref to an absolute Path under AGENTS_DIR.
        # The exact attribute on each agent (`agent.source_path`, `agent.path`, `agent.file`)
        # is set by Task 1's discovery; substitute below if different.
        resolved: Path = Path(agent.source_path)
        assert resolved.is_file(), f"Agent {agent.name} did not resolve to a file: {resolved}"
        assert AGENTS_DIR in resolved.parents or resolved.parent == AGENTS_DIR, (
            f"Agent {agent.name} resolved outside the vendored AGENTS_DIR: {resolved}"
        )
```

**Step 2: Verify the tests fail**

Run: `uv run pytest tests/test_bundle_loader.py::test_vendored_bundle_has_no_includes_block tests/test_bundle_loader.py::test_vendored_bundle_declares_all_four_agents tests/test_bundle_loader.py::test_vendored_agents_resolve_to_wheel_local_files -v`

Expected: **all 3 FAIL.**
- `test_vendored_bundle_has_no_includes_block` fails because today's `bundle.md` still contains `includes:`.
- The other two fail because today's `bundle.md` has no `agents:` block (likely with `AttributeError: 'Bundle' object has no attribute 'agents'` or `KeyError`).

**Step 3: Commit the RED tests**

```bash
git add tests/test_bundle_loader.py
git commit -m "test(bundle): RED — manifest must drop includes: and declare four agents inline"
```

---

## Task 3: GREEN — rewrite `bundle.md` (drop `includes:`, add `tools:`/`hooks:`/`agents:`)

**Files:**
- Modify: `src/amplifier_agent_lib/bundle/bundle.md` (full rewrite)

**Step 1: Replace the entire file content**

Write the following content to `src/amplifier_agent_lib/bundle/bundle.md`. **IMPORTANT — schema verification:** the schema below reflects the shapes documented in foundation as of Phase 1. If Task 1's discovery uncovered a different schema for `agents:` entries (path key, name+file pair, etc.), adapt the `agents:` block here to match — but DO NOT silently change the other blocks; if they need to shift too, stop and reconcile with the design doc before continuing.

```markdown
---
bundle:
  name: amplifier-agent-builtin
  version: 1.0.0
  description: >
    Vendored opinionated manifest for the amplifier-agent CLI (Strategy 1 of
    docs/designs/2026-05-19-baked-in-bundle-decision.md). The manifest text and
    the four sub-session agent definitions are vendored inside this wheel. The
    modules referenced by `source: git+https://...@main` are not vendored — they
    are git-cloned and installed on first invocation. The prepared result is
    cached to $XDG_CACHE_HOME/amplifier-agent/prepared/<aaa_version>/<sha256(bundle.md)>/.
    Editing this file changes the cache key (sha256) and self-invalidates the warm pickle.

session:
  orchestrator:
    module: loop-streaming
    source: git+https://github.com/microsoft/amplifier-module-loop-streaming@main

  context:
    module: context-simple
    source: git+https://github.com/microsoft/amplifier-module-context-simple@main
    config:
      max_tokens: 200000
      compact_threshold: 0.8
      auto_compact: true

  provider:
    module: anthropic-provider
    source: git+https://github.com/microsoft/amplifier-module-anthropic-provider@main

tools:
  - module: tool-todo
    source: git+https://github.com/microsoft/amplifier-module-tool-todo@main
  - module: tool-delegate
    source: git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=modules/tool-delegate

hooks: []

agents:
  - name: explorer
    source: file://./agents/explorer.md
  - name: planner
    source: file://./agents/planner.md
  - name: coder
    source: file://./agents/coder.md
  - name: tester
    source: file://./agents/tester.md
---

# amplifier-agent Built-in Bundle (Vendored Opinionated Manifest)

This bundle defines the runtime environment for the **amplifier-agent CLI**. Per
the Strategy 1 decision (`docs/designs/2026-05-19-baked-in-bundle-decision.md`),
only the manifest text and the four sub-session agent definitions (`agents/*.md`
adjacent to this file) are vendored inside the wheel. Module sources are
declared explicitly with `@main` and resolve at runtime via the standard
foundation lazy activator.

## Tool surface at the parent (orchestrator) level

The parent agent loads exactly two tools: `todo` (planning) and `delegate`
(sub-session dispatch). All concrete work — reading files, running commands,
searching, editing — goes through one of the four sub-session agents below,
each of which carries its own tool surface in its frontmatter.

## The four sub-session agents

| Need | Delegate to |
|---|---|
| Understand code, find things, survey docs/configs | `explorer` |
| Design, architecture, code review, write a spec   | `planner`  |
| Implement code from a complete spec               | `coder`    |
| Run tests, measure coverage, generate test cases  | `tester`   |

Agent definitions are vendored in `agents/{explorer,planner,coder,tester}.md`
adjacent to this file. Editing them changes the manifest content hash and
self-invalidates the warm cache.

## Runtime context

The agent runs inside the `amplifier-agent` CLI process. Approval flows and
display updates are mediated by the host adapter — the component that bridges
agent-side events (tool calls, approval requests, stream chunks) to the host
application (e.g. the Paperclip VS Code extension or any compliant JSON-RPC
client).

Session-transcript persistence (writing to
`$XDG_STATE_HOME/amplifier-agent/sessions/<session-id>/`) is **not** owned by
the context module declared above (`context-simple`); it remains a future
CLI-layer hook concern. Out of scope for this manifest.

## Bundle stability

This manifest text is **sealed per release**. Module `source:` URLs use `@main`,
so upstream module updates flow automatically — drift is intentional product
behaviour, not a defect. Editing this file changes the cache key (sha256) and
invalidates all cached prepared bundles. Any change must be intentional and
reviewed as a design decision.
```

**Step 2: Sanity-check the YAML**

Run:

```bash
uv run python -c "
import re
content = open('src/amplifier_agent_lib/bundle/bundle.md').read()
assert '\nincludes:' not in content, 'includes: still present'
assert '\nagents:' in content, 'agents: block missing'
assert content.count('git+https://github.com/microsoft/amplifier-module-') >= 4, 'expected at least 4 module refs'
print('OK')
"
```

Expected: prints `OK` on one line.

**Step 3: Verify the three RED tests now pass**

Run: `uv run pytest tests/test_bundle_loader.py -v`

Expected: all tests in this file PASS, including the three added in Task 2 and the three pre-existing (`test_load_and_prepare_returns_prepared_bundle`, `test_prepared_bundle_declares_context_simple`, `test_load_and_prepare_accepts_override_path`).

**If any of the new tests still fail**, the manifest schema does not match what Task 1 discovered. Do NOT proceed by changing the test assertions — the test is the spec. Re-read foundation's manifest parser and adjust the `agents:` block (or related blocks) in `bundle.md` until the assertions hold against the real parsed bundle.

**Step 4: Commit**

```bash
git add src/amplifier_agent_lib/bundle/bundle.md
git commit -m "feat(bundle): rewrite manifest — drop includes:, declare modules + agents inline

Strategy 1 of docs/designs/2026-05-19-baked-in-bundle-decision.md. The
manifest now declares orchestrator + context + provider + tools + the four
vendored agents explicitly. No more includes: build-up-foundation, no
dependency on amplifier-app-cli's bundle registry, no 'No handler for URI'
warning."
```

---

## Task 4: RED — failing test that the wheel ships the four agent files

**Files:**
- Create: `tests/test_bundle_packaging.py`

**Step 1: Add the failing test**

Create `tests/test_bundle_packaging.py` with the following content:

```python
"""Packaging regression test — the built wheel must contain the four vendored agent files.

Strategy 1 of docs/designs/2026-05-19-baked-in-bundle-decision.md vendors the
manifest text + four agent markdown files into the wheel. If pyproject.toml's
force-include block forgets one of them, the manifest at first-run will fail to
resolve the agents: block. This test guards against that regression by inspecting
the actually-built wheel rather than the source tree.
"""

from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.integration
def test_built_wheel_contains_all_four_vendored_agents(tmp_path: Path) -> None:
    """Build the wheel and assert the four agent markdown files are inside it."""
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path)],
        cwd=REPO_ROOT,
        check=True,
    )

    wheels = list(tmp_path.glob("amplifier_agent-*.whl"))
    assert len(wheels) == 1, f"expected exactly one wheel, found {wheels}"

    with zipfile.ZipFile(wheels[0]) as zf:
        names = set(zf.namelist())

    expected = {
        "amplifier_agent_lib/bundle/bundle.md",
        "amplifier_agent_lib/bundle/agents/explorer.md",
        "amplifier_agent_lib/bundle/agents/planner.md",
        "amplifier_agent_lib/bundle/agents/coder.md",
        "amplifier_agent_lib/bundle/agents/tester.md",
    }
    missing = expected - names
    assert not missing, f"wheel missing files: {sorted(missing)}"
```

**Step 2: Verify the test fails**

Run: `uv run pytest tests/test_bundle_packaging.py -v -m integration`

Expected: **FAIL** with an assertion message listing the four agent files in `missing`. (The wheel currently only force-includes `bundle.md`; the agent files are inside `src/` but `force-include` is required to land them at the canonical path under `amplifier_agent_lib/bundle/agents/`.)

**Step 3: Commit the RED test**

```bash
git add tests/test_bundle_packaging.py
git commit -m "test(packaging): RED — wheel must contain the four vendored agent files"
```

---

## Task 5: GREEN — extend `pyproject.toml` `force-include` for the agents directory

**Files:**
- Modify: `pyproject.toml`

**Step 1: Extend the `force-include` block**

Open `pyproject.toml` and replace the existing block (currently lines 22–23):

```toml
[tool.hatch.build.targets.wheel.force-include]
"src/amplifier_agent_lib/bundle/bundle.md" = "amplifier_agent_lib/bundle/bundle.md"
```

with:

```toml
[tool.hatch.build.targets.wheel.force-include]
"src/amplifier_agent_lib/bundle/bundle.md" = "amplifier_agent_lib/bundle/bundle.md"
"src/amplifier_agent_lib/bundle/agents/explorer.md" = "amplifier_agent_lib/bundle/agents/explorer.md"
"src/amplifier_agent_lib/bundle/agents/planner.md" = "amplifier_agent_lib/bundle/agents/planner.md"
"src/amplifier_agent_lib/bundle/agents/coder.md" = "amplifier_agent_lib/bundle/agents/coder.md"
"src/amplifier_agent_lib/bundle/agents/tester.md" = "amplifier_agent_lib/bundle/agents/tester.md"
```

(Explicit per-file entries rather than a glob — hatchling supports both, but per-file entries make the wheel contents auditable from the manifest alone. If Phase 3 adds a fifth agent or a vendored `context/` file, append a new line here.)

**Step 2: Verify the packaging test passes**

Run: `uv run pytest tests/test_bundle_packaging.py -v -m integration`

Expected: **PASS**. (The build may take 5–15 seconds.)

**Step 3: Verify no other tests regressed**

Run: `uv run pytest -q`

Expected: all PASS. (The integration-marked test will run too; that's fine.)

---

## Task 6: VERIFY — ruff + pyright clean after Phase 2 so far

**Step 1: Run linters**

```bash
uv run ruff check
uv run pyright
```

Expected: both clean. (No new Python source was added, only test files + `bundle.md` + `pyproject.toml`. Pyright should have nothing to complain about.)

---

## Task 7: COMMIT — packaging update

**Step 1: Stage and commit**

```bash
git add pyproject.toml
git commit -m "feat(packaging): force-include vendored agent files in the wheel

Required by Strategy 1 (Phase 2). Without this, the manifest's agents: block
would reference files absent from the installed wheel, and first-run would fail
with FileNotFoundError on the agent path resolution. Per-file entries (rather
than a glob) keep wheel contents auditable from pyproject.toml alone."
```

---

## Task 8: RED — failing test that an editable install resolves agent files

**Why:** `force-include` covers the built wheel. The dev workflow uses editable installs (`uv pip install -e .`) where `force-include` doesn't apply — the source tree is mounted directly. Confirm that `AGENTS_DIR` resolves the same files in both modes.

**Files:**
- Modify: `tests/test_bundle_loader.py` (append at end)

**Step 1: Add the failing test**

Append the following to `tests/test_bundle_loader.py`:

```python


def test_agents_dir_resolves_in_editable_install() -> None:
    """AGENTS_DIR (used at runtime by the manifest's file:// agent refs) must contain real files.

    Regression guard: if a future refactor accidentally moves AGENTS_DIR or the
    agent files diverge from it, this test will catch it before the manifest fails
    at first-run with FileNotFoundError.
    """
    from amplifier_agent_lib.bundle import AGENTS_DIR

    expected = {"explorer.md", "planner.md", "coder.md", "tester.md"}
    actual_files = {p.name: p for p in AGENTS_DIR.iterdir() if p.suffix == ".md"}

    assert expected <= set(actual_files), f"missing: {expected - set(actual_files)}"
    for name, path in actual_files.items():
        if name in expected:
            assert path.stat().st_size > 100, f"{name} is suspiciously small ({path.stat().st_size} bytes)"
            assert path.read_text().startswith("---\n"), f"{name} missing YAML frontmatter"
```

**Step 2: Run it**

Run: `uv run pytest tests/test_bundle_loader.py::test_agents_dir_resolves_in_editable_install -v`

Expected: **PASS** (because Phase 1 Task 12 already exposed `AGENTS_DIR` and Phase 1 Tasks 6–9 wrote the four files). This is a "always-green" guard — it locks in the invariant rather than driving new code.

**Step 3: Commit**

```bash
git add tests/test_bundle_loader.py
git commit -m "test(bundle): lock invariant — AGENTS_DIR resolves all four vendored agents"
```

---

## Task 9: RED — failing test that `test_prepared_bundle_declares_context_simple` still holds under the new manifest

**Files:** none — verification of an existing test under the new manifest.

**Step 1: Run the existing test**

Run: `uv run pytest tests/test_bundle_loader.py::test_prepared_bundle_declares_context_simple -v`

Expected: **PASS**. The new `bundle.md` (Task 3) still declares `context-simple` as the session context module, so this test is unchanged-by-design.

**If it FAILS**: the rewrite in Task 3 broke a pre-existing invariant. The implementer must fix `bundle.md` (NOT the test) — the context module is `context-simple` per `docs/designs/2026-05-19-baked-in-bundle-decision.md` (Thread 1 fix, commit `654dfac`).

(This is a verification task only, no commit. If it surfaces a regression, fix `bundle.md` and amend Task 3's commit.)

---

## Task 10: VERIFY — full local test suite green under the rewritten manifest

**Step 1: Run the full suite**

Run: `uv run pytest -q`

Expected: **all PASS**. (Phase 1's cache tests, Phase 2's loader + packaging tests, plus all pre-existing CLI/runtime tests.)

**Step 2: Confirm no flaky/skipped tests**

Run: `uv run pytest -q --tb=short -ra`

Look at the short summary. Expect: zero `FAILED`, zero `ERRORED`. `SKIPPED` only acceptable if it was skipped before Phase 2 too.

If anything regressed, fix at the source — do not adjust test assertions to mask the regression.

---

## Task 11: VERIFY — manual smoke test that the manifest actually loads end-to-end

**Step 1: Clear the warm cache**

```bash
uv run amplifier-agent cache clear
```

Expected: command succeeds. (If `cache clear` is not a real subcommand in this build, fall back to `rm -rf "$HOME/.cache/amplifier-agent"` — both equally invalidate.)

**Step 2: Cold-load the manifest**

Run, with a 60-second timeout to accommodate the documented 5–30 s first-run cliff:

```bash
timeout 60 uv run python -c "
import asyncio
from amplifier_agent_lib.bundle.cache import load_and_prepare_cached
prepared = asyncio.run(load_and_prepare_cached(aaa_version='2.0.0-phase2'))
print('OK')
print('agents:', [a.name for a in prepared.bundle.agents])
print('mount_plan top-level keys:', sorted(prepared.mount_plan.keys()))
"
```

Expected:
- Exit 0.
- stdout contains `OK`.
- `agents:` line lists at least `explorer`, `planner`, `coder`, `tester`.
- No `No handler for URI: build-up-foundation` warning anywhere in stderr.

**Step 3: Warm-load to prove the cache key works**

Run the same command a second time. Expected: completes in under 2 seconds (warm pickle hit).

**Step 4: Confirm manifest edit invalidates**

Append a single trailing comment line to `src/amplifier_agent_lib/bundle/bundle.md` (e.g. `# touch`). Re-run Step 2. Expected: cold path runs again (multi-second prepare), proving the `sha256(bundle.md)` key from Phase 1 took effect. Revert the trailing comment (or leave it — Phase 3 will polish docs in a different file).

**No commit** for this task — it's an empirical verification. Record results in the implementer's status report.

---

## Phase 2 Exit Criteria

Before starting Phase 3, all of the following must be true:

1. `src/amplifier_agent_lib/bundle/bundle.md` no longer contains the substring `\nincludes:`.
2. `src/amplifier_agent_lib/bundle/bundle.md` declares `tools:`, `hooks:`, and `agents:` blocks plus the existing `session.orchestrator` and `session.context` blocks. A `session.provider` block is also declared.
3. `pyproject.toml`'s `[tool.hatch.build.targets.wheel.force-include]` lists all four agent files.
4. `uv run pytest -q` → all PASS, including `tests/test_bundle_packaging.py` (integration marker may add a few seconds).
5. `uv run ruff check` → clean.
6. `uv run pyright` → clean.
7. Manual smoke (Task 11) confirmed cold-load returns `OK` with all four agents listed, warm-load runs in <2 s, and a trivial manifest edit triggers re-prepare.
8. Cheatsheet language in `docs/test-docs/CHEATSHEET.md` is unchanged. (Phase 3 owns docs.)

When all criteria pass, proceed to `docs/plans/2026-05-19-baked-in-bundle-impl-phase-3.md`.

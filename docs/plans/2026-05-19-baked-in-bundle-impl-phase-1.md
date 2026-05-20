# Baked-in Bundle (Strategy 1) — Phase 1: Cache Key + Vendor Agents

> **Execution:** Use the `subagent-driven-development` workflow to implement this plan.

**Goal:** Make `bundle.md` content participate in the XDG cache key, and copy the four build-up sub-session agent definitions into the wheel as standalone markdown files.

**Architecture:** Two independent surfaces. (1) `cache.py` gains a `sha256(bundle.md)` mix-in so manifest edits self-invalidate. This MUST land before Phase 2's manifest rewrite so developers iterating on `bundle.md` get correct invalidation from day one. (2) Four agent definitions from `microsoft/amplifier-foundation@main` are vendored into `src/amplifier_agent_lib/bundle/agents/` and exposed via `AGENTS_DIR`. Neither surface depends on the other; they're packaged together because they're the foundational ground that Phase 2 (manifest rewrite) builds on.

**Tech Stack:** Python 3.12+, pytest + pytest-asyncio, hatchling wheel build. Ruff + Pyright must stay clean throughout.

**Source of truth:** `docs/designs/2026-05-19-baked-in-bundle-decision.md` §9 items 1 + 4. The design doc's "Next step" requires task ordering: **cache key change lands before or alongside manifest rewrite** — this plan satisfies that by landing the cache key in Phase 1, before Phase 2 touches `bundle.md`.

**Out of scope (do NOT touch in this phase):**
- `bundle.md` content rewrite (Phase 2)
- `pyproject.toml` `force-include` extension (Phase 2)
- Docs/cheatsheet renames (Phase 3)
- Engine/wire-protocol/Mode A-B/provider injection — out of scope for the entire plan series (design doc §11)

**Conventions:**
- TDD per task: **Write test → verify FAIL → write impl → verify PASS → commit**. Each verb is one task.
- `from __future__ import annotations` at the top of every new Python module.
- Type hints everywhere; `uv run ruff check` and `uv run pyright` must stay clean after each commit.
- One atomic commit per task. Conventional Commits prefix (`feat:`, `fix:`, `test:`, `refactor:`, `chore:`, `docs:`).
- Pytest discovery: `tests/` directory; `asyncio_mode = 'strict'` so all async tests need `@pytest.mark.asyncio`.

---

## Task 1: RED — failing test for `sha256(bundle.md)` cache key derivation

**Files:**
- Modify: `tests/test_bundle_cache.py` (append new tests at end of file)

**Step 1: Add the failing test**

Append the following at the end of `tests/test_bundle_cache.py` (after `test_corrupted_cache_triggers_rebuild`):

```python


@pytest.mark.asyncio
async def test_cache_dir_includes_bundle_content_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cache_dir_for_version() returns different dirs for different bundle.md content.

    Cache key must be (aaa_version, sha256(bundle.md)) so manifest edits self-invalidate.
    Per D2 of docs/designs/2026-05-19-baked-in-bundle-decision.md.
    """
    from amplifier_agent_lib.bundle.cache import cache_dir_for_version

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    bundle_a = tmp_path / "a.md"
    bundle_a.write_text("---\nbundle:\n  name: a\n  version: 1.0.0\n---\n")
    bundle_b = tmp_path / "b.md"
    bundle_b.write_text("---\nbundle:\n  name: b\n  version: 1.0.0\n---\n")

    dir_a = cache_dir_for_version("1.0.0", bundle_path=bundle_a)
    dir_b = cache_dir_for_version("1.0.0", bundle_path=bundle_b)

    assert dir_a != dir_b, "Different bundle content must produce different cache dirs"
    assert dir_a.parent == dir_b.parent, "Different bundle content must share the same version parent"


@pytest.mark.asyncio
async def test_cache_dir_stable_for_same_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Identical bundle.md content produces identical cache dirs (deterministic)."""
    from amplifier_agent_lib.bundle.cache import cache_dir_for_version

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    bundle = tmp_path / "b.md"
    bundle.write_text("---\nbundle:\n  name: stable\n  version: 1.0.0\n---\n")

    first = cache_dir_for_version("1.0.0", bundle_path=bundle)
    second = cache_dir_for_version("1.0.0", bundle_path=bundle)

    assert first == second


@pytest.mark.asyncio
async def test_manifest_edit_invalidates_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Editing bundle.md without bumping aaa_version causes a cache miss.

    Regression guard for the F8 failure mode called out in the predecessor design doc:
    today's version-only key serves a stale pickle silently when bundle.md is edited.
    """
    from amplifier_agent_lib.bundle import BUNDLE_MD
    from amplifier_agent_lib.bundle.cache import cache_dir_for_version

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    dir_before = cache_dir_for_version("1.0.0", bundle_path=BUNDLE_MD)

    # Simulate an edit by pointing at a different file with different content.
    edited = tmp_path / "edited.md"
    edited.write_text(BUNDLE_MD.read_text() + "\n# trivial edit\n")
    dir_after = cache_dir_for_version("1.0.0", bundle_path=edited)

    assert dir_before != dir_after, "Manifest edit must change the cache key"
```

**Step 2: Verify the test fails**

Run: `uv run pytest tests/test_bundle_cache.py::test_cache_dir_includes_bundle_content_hash -v`

Expected: **FAIL** with `TypeError: cache_dir_for_version() got an unexpected keyword argument 'bundle_path'`.

**Step 3: Commit the failing test**

```bash
git add tests/test_bundle_cache.py
git commit -m "test(bundle): RED — cache key must incorporate sha256(bundle.md)"
```

---

## Task 2: GREEN — extend `cache_dir_for_version` to hash bundle content

**Files:**
- Modify: `src/amplifier_agent_lib/bundle/cache.py`

**Step 1: Replace `cache_dir_for_version` and update `load_and_prepare_cached`**

Open `src/amplifier_agent_lib/bundle/cache.py`. Replace the existing `cache_dir_for_version` function (lines 51–61) and the existing `load_and_prepare_cached` function (lines 64–109) with the following, and add the `hashlib` + `BUNDLE_MD` imports near the top of the file.

At the imports section (after `import pickle`), add:

```python
import hashlib
```

After `from amplifier_agent_lib.bundle.loader import load_and_prepare_bundle`, add:

```python
from amplifier_agent_lib.bundle import BUNDLE_MD
```

Replace `cache_dir_for_version` with:

```python
def cache_dir_for_version(aaa_version: str, bundle_path: Path | None = None) -> Path:
    """Return the cache directory for a specific AaA version + bundle content hash.

    Cache key is ``(aaa_version, sha256(bundle.md content))``. Bumping AaA OR editing
    the vendored bundle.md changes the key and invalidates cached pickles automatically.
    This fixes the silent stale-cache failure mode (F8 in the predecessor design doc)
    that occurred when developers edited bundle.md without bumping ``__version__``.

    Per D2 of docs/designs/2026-05-19-baked-in-bundle-decision.md.

    Args:
        aaa_version: The AaA package version string (e.g. ``"1.0.0"``).
        bundle_path: Path to the bundle.md to hash. Defaults to the vendored
            ``BUNDLE_MD``. Provided as a parameter so tests can point at fixtures.

    Returns:
        A :class:`~pathlib.Path` to the cache directory keyed by
        ``<aaa_version>/<sha256-prefix>``. The directory may not yet exist.
    """
    target = bundle_path if bundle_path is not None else BUNDLE_MD
    content_hash = hashlib.sha256(target.read_bytes()).hexdigest()[:16]
    return _xdg_cache_home() / _CACHE_SUBDIR / aaa_version / content_hash
```

Replace `load_and_prepare_cached` with:

```python
async def load_and_prepare_cached(aaa_version: str) -> PreparedBundle:
    """Load and prepare the vendored bundle, caching the result to XDG cache.

    Warm path: if both ``prepared.pickle`` and ``manifest.json`` exist for this
    ``(aaa_version, sha256(bundle.md))`` pair, deserialise and return the cached
    :class:`~amplifier_foundation.bundle._prepared.PreparedBundle` without invoking
    :func:`~amplifier_agent_lib.bundle.loader.load_and_prepare_bundle`.

    Cold path: calls
    :func:`~amplifier_agent_lib.bundle.loader.load_and_prepare_bundle`, writes the
    resulting PreparedBundle to the version+hash-keyed cache directory as a pickled
    blob alongside a ``manifest.json`` describing the cache entry.

    Args:
        aaa_version: The AaA package version string. Combined with sha256 of
            the vendored bundle.md to form the full cache key.

    Returns:
        A :class:`~amplifier_foundation.bundle._prepared.PreparedBundle`
        ready for session creation.
    """
    cache_dir = cache_dir_for_version(aaa_version)
    cache_dir.mkdir(parents=True, exist_ok=True)

    artifact = cache_dir / _ARTIFACT_NAME
    manifest = cache_dir / _MANIFEST_NAME

    if artifact.exists() and manifest.exists():
        try:
            return pickle.loads(artifact.read_bytes())
        except Exception as exc:  # broad: corrupt cache → rebuild
            logger.warning(
                "Cache artifact at %s is corrupted (%s); rebuilding.",
                artifact,
                type(exc).__name__,
            )
            artifact.unlink(missing_ok=True)
            manifest.unlink(missing_ok=True)

    prepared = await load_and_prepare_bundle()

    artifact.write_bytes(pickle.dumps(prepared))
    bundle_hash = hashlib.sha256(BUNDLE_MD.read_bytes()).hexdigest()[:16]
    manifest.write_text(
        json.dumps({"aaa_version": aaa_version, "bundle_sha256_prefix": bundle_hash})
    )

    return prepared
```

Also update the module docstring at the top of `cache.py` — change the cache layout block (lines 5–8) to:

```python
"""Bundle cache — cold + warm path: prepare, write to XDG cache, and return from cache on hit.

Strategy: pickle (decided in task-2-empirical-spike-pickle).

Cache layout:
    $XDG_CACHE_HOME/amplifier-agent/prepared/<aaa_version>/<sha256(bundle.md)[:16]>/
        prepared.pickle  — pickle.dumps(PreparedBundle)
        manifest.json    — { "aaa_version": "<version>", "bundle_sha256_prefix": "<hex>" }

Cache key: (aaa_version, sha256(bundle.md content)). Bumping AaA OR editing
bundle.md invalidates the cache automatically. Per D2 of
docs/designs/2026-05-19-baked-in-bundle-decision.md. Corruption is treated as a
cache miss and rebuilt.
"""
```

**Step 2: Verify the new tests pass**

Run: `uv run pytest tests/test_bundle_cache.py::test_cache_dir_includes_bundle_content_hash tests/test_bundle_cache.py::test_cache_dir_stable_for_same_content tests/test_bundle_cache.py::test_manifest_edit_invalidates_cache -v`

Expected: **3 PASS**.

---

## Task 3: VERIFY — existing cache tests still pass under the new key

**Files:** none modified — verification only.

**Step 1: Run the full cache test file**

Run: `uv run pytest tests/test_bundle_cache.py -v`

Expected: **all tests PASS**. The existing tests (`test_cold_invocation_creates_cache`, `test_cache_dir_is_xdg_keyed`, `test_warm_invocation_hits_cache`, `test_new_version_invalidates_cache`, `test_corrupted_cache_triggers_rebuild`) call `cache_dir_for_version("1.0.0")` without a `bundle_path` — the new default falls back to `BUNDLE_MD`, so they should pass unchanged.

If `test_cache_dir_is_xdg_keyed` fails because it asserts `v1.name == "1.0.0"` but the new key is `1.0.0/<hash>/`, update the assertion to walk one level deeper. Specifically, change:

```python
    assert v1.name == "1.0.0"
    assert v2.name == "2.0.0"
```

to:

```python
    assert v1.parent.name == "1.0.0"
    assert v2.parent.name == "2.0.0"
    assert v1.name == v2.name, "same bundle content → same hash component"
```

(The two versions point at the same vendored `BUNDLE_MD`, so the hash component matches; the version component differs.)

Re-run: `uv run pytest tests/test_bundle_cache.py -v` → all PASS.

---

## Task 4: VERIFY — ruff + pyright clean

**Step 1: Run ruff**

Run: `uv run ruff check src/amplifier_agent_lib/bundle/cache.py tests/test_bundle_cache.py`

Expected: **clean** (no output, exit 0).

**Step 2: Run pyright**

Run: `uv run pyright src/amplifier_agent_lib/bundle/cache.py tests/test_bundle_cache.py`

Expected: **0 errors, 0 warnings**.

If either complains, fix the smallest possible thing — usually an unused `Path` import or a missing `from __future__ import annotations`.

---

## Task 5: COMMIT — cache key derivation

**Step 1: Stage and commit**

```bash
git add src/amplifier_agent_lib/bundle/cache.py tests/test_bundle_cache.py
git commit -m "feat(bundle/cache): incorporate sha256(bundle.md) into cache key

Manifest edits now self-invalidate the warm pickle. Resolves D2 from
docs/designs/2026-05-19-baked-in-bundle-decision.md and lands before Phase 2's
manifest rewrite so developers iterating on bundle.md get correct invalidation
behavior immediately."
```

Expected: commit succeeds, `git log -1 --oneline` shows the new commit.

---

## Task 6: Vendor `explorer.md` agent

**Files:**
- Create: `src/amplifier_agent_lib/bundle/agents/explorer.md`

**Step 1: Fetch and write the file**

The content below is the verbatim source of `experiments/build-up/agents/explorer.md` from `microsoft/amplifier-foundation@main` as of 2026-05-19. Write this exact content to `src/amplifier_agent_lib/bundle/agents/explorer.md`:

```markdown
---
meta:
  name: explorer
  description: |
    Multi-file exploration and codebase survey. Read-only reconnaissance.

    USE WHEN: the parent needs structured understanding of code, docs, or configuration
    spanning more than a single known file. Triggering questions: "how does X work?",
    "where is Y defined?", "what depends on Z?", "find everything related to A", "trace
    the flow of B", "survey the auth/config/<feature>".

    DO NOT USE WHEN: a single known file needs reading (the parent should delegate that
    to a more focused agent or, if the file is small, summarize the request directly to
    `coder`); design or architecture decisions need to be made (route to `planner`); a
    spec already exists and you just need code (route to `coder`).

    REQUIRES in the delegation instruction:
      - The objective or question to answer
      - Scope hints (directories, file types, keywords)
      - Constraints if any (time period, ownership, etc.)

    Returns: structured report with summary, key file:line references, coverage gaps, and
    suggested next actions or delegations.

    Examples:
    <example>
    user: "What does the event handling flow look like?"
    assistant: 'I will delegate to explorer to map the event modules and summarize the flow.'
    </example>
    <example>
    user: "Find everything related to auth across docs and configs."
    assistant: 'I will delegate to explorer to survey docs and configs for auth references.'
    </example>

model_role: [research, general]

tools:
  - module: tool-bash
    source: git+https://github.com/microsoft/amplifier-module-tool-bash@main
  - module: tool-filesystem
    source: git+https://github.com/microsoft/amplifier-module-tool-filesystem@main
  - module: tool-search
    source: git+https://github.com/microsoft/amplifier-module-tool-search@main
  - module: tool-todo
    source: git+https://github.com/microsoft/amplifier-module-tool-todo@main
  - module: tool-delegate
    source: git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=modules/tool-delegate
    config:
      settings:
        exclude_tools: [tool-delegate]
---

# Explorer

You map workspace slices that matter and surface artifacts that answer the caller's question.

## Execution model

You run as a one-shot sub-session. You see (1) these instructions, (2) the delegation instruction, and (3) data fetched via your tools. Intermediate thoughts are hidden — only your final response reaches the caller. Make it stand on its own.

## Required inputs (from the delegation instruction)

- Primary question or objective.
- Scope hints — directories, file types, keywords.
- Constraints if relevant.

If any are missing, return a short clarification request and stop.

## Operating principles

1. **Plan before digging.** Translate the objective into 3–6 todos. Update them as you go.
2. **Breadth before depth.** Start with directory listings and globs; read representative files only after you know which areas matter.
3. **Stay read-only.** Do not modify files.
4. **Cite paths.** Every key claim references `path:line`.
5. **Quantify.** Counts of files, sizes, prevalence of patterns. Avoid vague "many" / "various".
6. **Flag gaps.** Note what you couldn't determine and what would resolve it.
7. **Scale out when needed.** For independent sub-questions, dispatch parallel `delegate(agent="self", ...)` sub-sessions and synthesize their reports.

## Output contract

Your final message must include:

1. **Summary** — 2–3 sentences directly answering the objective.
2. **Key findings** — bulleted, each with a `path:line` reference and a one-line insight.
3. **Coverage & gaps** — what was explored, what wasn't, what's still unknown.
4. **Suggested next actions** — concrete follow-ups, including which agent to delegate to next (`planner` for design work, `coder` for implementation, `tester` for test work).

If exploration could not proceed, return a short failure summary plus the exact info required to retry.
```

**Step 2: Verify the file is present**

Run: `wc -l src/amplifier_agent_lib/bundle/agents/explorer.md`

Expected: line count matches roughly 60 lines (frontmatter + body).

**Step 3: Commit**

```bash
git add src/amplifier_agent_lib/bundle/agents/explorer.md
git commit -m "feat(bundle/agents): vendor explorer agent from amplifier-foundation@main

Source: experiments/build-up/agents/explorer.md from microsoft/amplifier-foundation@main.
Per D1 of docs/designs/2026-05-19-baked-in-bundle-decision.md — agent definitions
are vendored as wheel-local markdown so the bundle can resolve them without
includes:/registry."
```

---

## Task 7: Vendor `planner.md` agent

**Files:**
- Create: `src/amplifier_agent_lib/bundle/agents/planner.md`

**Step 1: Write the file**

Write the following verbatim content to `src/amplifier_agent_lib/bundle/agents/planner.md`:

```markdown
---
meta:
  name: planner
  description: |
    Design, architecture, and code review. Produces complete implementation specifications
    that the `coder` agent can execute on without further research.

    Three modes (driven by context, not commands):
      - ANALYZE: decompose a problem; surface 2–3 options with tradeoffs; recommend one.
      - DESIGN: produce an implementation spec — file paths, interfaces, success criteria.
      - REVIEW: critique existing code or design for simplicity and correctness.

    USE WHEN: design or architecture decisions need to be made; an implementation spec
    needs to be written; existing code or a design needs review for simplicity, correctness,
    or scope; questions like "how should we build X?", "design Y", "add feature Z",
    "critique this", "review this module".

    DO NOT USE WHEN: a complete spec already exists (route to `coder` directly);
    exploration is needed first to understand the territory (route to `explorer`); the
    change is trivial enough to spec inline in a delegate call to `coder`.

    Returns: structured spec or review with concrete next-action recommendations.

    Examples:
    <example>
    user: "Add a caching layer to improve API performance."
    assistant: 'I will delegate to planner to analyze the requirements and produce a spec, then delegate to coder.'
    </example>
    <example>
    user: "Review this module for complexity."
    assistant: 'I will delegate to planner in REVIEW mode for an objective assessment.'
    </example>

model_role: [reasoning, general]

tools:
  - module: tool-filesystem
    source: git+https://github.com/microsoft/amplifier-module-tool-filesystem@main
  - module: tool-todo
    source: git+https://github.com/microsoft/amplifier-module-tool-todo@main
  - module: tool-delegate
    source: git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=modules/tool-delegate
    config:
      settings:
        exclude_tools: [tool-delegate]
---

# Planner

You design, architect, and review. You do not implement.

## Execution model

One-shot sub-session. Output the design or review in your final message — that is the deliverable.

## Core philosophy

Ruthless simplicity. Every abstraction must justify its existence. Prefer the simplest design whose failure modes are acceptable. Build on existing patterns; don't invent.

## Modes

### ANALYZE (default for new work)

Start with: "Let me analyze this problem and design the solution."

Output:
- **Problem decomposition** — what really needs to happen, in 3–5 bullets.
- **Options** — 2–3 approaches, each with one-line tradeoffs.
- **Recommendation** — clear choice with one-paragraph justification.

### DESIGN (after analysis or when asked for a spec)

Output a specification complete enough for `coder` to implement WITHOUT reading more files than you cite, making design decisions, or researching patterns.

```
# Implementation Specification

## Overview
[Brief description of what gets built]

## Files to create or modify
- `path/to/file.py` — purpose, what changes

## Interfaces
- `function_name(arg: Type) -> ReturnType` — purpose, error cases

## Dependencies
- [external libs/modules required]

## Implementation notes
- [non-obvious decisions, patterns to follow]

## Test strategy
- [key test scenarios; edge cases to cover]

## Success criteria
- [measurable definition of done]
```

### REVIEW (when asked to critique)

Output:
- **Verdict** — Good / Concerns / Needs refactoring.
- **Issues** — specific problems with `path:line` references.
- **Recommendations** — concrete actions, ordered by priority.
- **Simplification opportunities** — what to remove or combine.

## Boundaries

- You may read files (`tool-filesystem`) to understand context. Do not write or edit any file.
- If you need broad codebase context, return early with: "Need exploration first" + a question for `explorer`. The parent will dispatch.
- If a task is too vague to spec, list the missing inputs and stop. Do not invent requirements.

## Handoff rule

When your spec is complete, end with:

> "Spec complete. Recommend: `delegate(agent='coder', instruction=<this spec>)`."

A spec is complete when `coder` can implement without (a) reading files beyond those cited, (b) making design decisions, or (c) researching patterns. If you can't satisfy that, you're not done.
```

**Step 2: Commit**

```bash
git add src/amplifier_agent_lib/bundle/agents/planner.md
git commit -m "feat(bundle/agents): vendor planner agent from amplifier-foundation@main"
```

---

## Task 8: Vendor `coder.md` agent

**Files:**
- Create: `src/amplifier_agent_lib/bundle/agents/coder.md`

**Step 1: Write the file**

Write the following verbatim content to `src/amplifier_agent_lib/bundle/agents/coder.md`:

```markdown
---
meta:
  name: coder
  description: |
    Implementation-only agent. Turns a complete specification into working code.
    REFUSES under-specified work — if the delegation instruction lacks file paths,
    interfaces, success criteria, or a pattern reference, it stops and reports the gap.

    USE WHEN: a `planner` spec exists, or the task is clearly bounded (file paths
    decided, interfaces designed, success measurable).

    DO NOT USE WHEN: requirements are vague, design decisions are still open, or
    exploration is needed first — route to `planner` (or `explorer`) instead.

    Returns: summary of what was implemented, the files changed, test results, and
    any gaps that blocked completion.

    Example:
    <example>
    user: "Implement the email validator from spec."
    assistant: 'I will delegate to coder with the full spec; coder will implement and run tests.'
    </example>

model_role: [coding, general]

tools:
  - module: tool-bash
    source: git+https://github.com/microsoft/amplifier-module-tool-bash@main
  - module: tool-filesystem
    source: git+https://github.com/microsoft/amplifier-module-tool-filesystem@main
  - module: tool-search
    source: git+https://github.com/microsoft/amplifier-module-tool-search@main
  - module: tool-todo
    source: git+https://github.com/microsoft/amplifier-module-tool-todo@main
  - module: tool-delegate
    source: git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=modules/tool-delegate
    config:
      settings:
        exclude_tools: [tool-delegate]
---

# Coder

You implement code from specifications. You do not design, explore, or research.

## Required inputs (verify FIRST)

The delegation instruction must contain:

- [ ] **File paths** — exact locations to create or modify.
- [ ] **Interfaces** — function signatures with types.
- [ ] **Pattern** — reference example OR explicit design freedom.
- [ ] **Success criteria** — measurable definition of done.

If any are missing or vague, **STOP** and return:
> "Specification incomplete: [the specific missing detail]. Cannot proceed without [X]."

Do **not** research. Do **not** read more than 3 files trying to "understand context." If the spec is vague, the spec is wrong — kick it back.

## Implementation loop

1. **Plan** — break the spec into todos. One todo per file change or test pass.
2. **Implement** — minimum code that meets the spec. Nothing speculative. No anticipated futures.
3. **Verify** — run tests / linters / the actual program. Iterate until success criteria pass.
4. **Clean up** — remove your own debugging artifacts (print statements, dead code, scratch files). Leave the rest of the codebase alone.

## Discipline

- **Touch only what the spec touches.** Other refactoring is its own task.
- **No over-engineering.** No abstraction "just in case."
- **Tests are code too.** When you change an interface, update the tests in the same change, not later.
- **3-file rule.** After modifying three files, pause and run the relevant tests/linters before continuing.
- **Mid-implementation gaps.** If you discover a missing decision while coding, STOP at that line, document where you got to, and report the gap. Do not continue researching.

## Forbidden

- "Let me read more files to understand the system…"
- "I'll search for similar patterns in the codebase…"
- "Let me figure out what this should do…"
- Reading the same file repeatedly hoping for clarity.

## Output contract

Final message must include:

1. **Status** — `Complete` / `Blocked` / `Partial`.
2. **Files changed** — list with one-line summaries.
3. **Verification** — what you ran, what passed, what failed.
4. **Gaps** — anything left undone and why (only for Blocked / Partial).
5. **Next action** — usually: "Recommend `delegate(agent='tester', ...)` to validate," or "Ready to merge."
```

**Step 2: Commit**

```bash
git add src/amplifier_agent_lib/bundle/agents/coder.md
git commit -m "feat(bundle/agents): vendor coder agent from amplifier-foundation@main"
```

---

## Task 9: Vendor `tester.md` agent

**Files:**
- Create: `src/amplifier_agent_lib/bundle/agents/tester.md`

**Step 1: Write the file**

Write the following verbatim content to `src/amplifier_agent_lib/bundle/agents/tester.md`:

```markdown
---
meta:
  name: tester
  description: |
    Test execution and coverage analysis. Runs the project's test suite, verifies behavior
    against success criteria, identifies coverage gaps, and generates new test cases when
    asked. May write test files; does NOT modify production code.

    USE WHEN: validating an implementation, assessing test coverage, generating test cases
    for a new feature, or reproducing a bug under test.

    DO NOT USE WHEN: production code needs changes — that's `coder` after `planner` (if
    the change isn't already specified).

    Returns: pass/fail status, coverage assessment, suggested test additions (with code),
    and any defects found.

    Example:
    <example>
    user: "Verify the new validator and check coverage."
    assistant: 'I will delegate to tester to run the suite, measure coverage, and report gaps.'
    </example>

model_role: general

tools:
  - module: tool-bash
    source: git+https://github.com/microsoft/amplifier-module-tool-bash@main
  - module: tool-filesystem
    source: git+https://github.com/microsoft/amplifier-module-tool-filesystem@main
  - module: tool-search
    source: git+https://github.com/microsoft/amplifier-module-tool-search@main
  - module: tool-todo
    source: git+https://github.com/microsoft/amplifier-module-tool-todo@main
  - module: tool-delegate
    source: git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=modules/tool-delegate
    config:
      settings:
        exclude_tools: [tool-delegate]
---

# Tester

You verify behavior, measure coverage, and add tests where they're missing.

## Boundaries

- **Read** any file. **Write** test files (`tests/` directories or matching test layouts) and **append** to them. Do **not** modify production source.
- If the test suite reveals a production bug that needs fixing, return early with a clear bug report and recommend `coder` (preceded by `planner` if the fix is non-trivial).

## Testing principles

- **Test behavior, not implementation.** Tests should survive refactors.
- **AAA pattern** — Arrange, Act, Assert. One concept per test.
- **Meaningful names** — `test_login_fails_with_wrong_password`, not `test_login`.
- **Test what matters** — critical paths, complex logic, edge cases, error handling. **Don't** test framework or library behavior.
- **Pyramid** — favour unit tests; integration sparingly; e2e only for critical journeys.

## Workflow

1. **Plan** — todos: identify the test command, the modules in scope, and any coverage targets.
2. **Run the suite as-is** — capture output. Note failures, errors, slow tests.
3. **Assess coverage** — for the modules in scope, identify untested or thinly-tested paths.
4. **Write missing tests** — only for gaps that matter (critical paths, complex logic, error handling).
5. **Re-run** — verify all tests pass after your additions.
6. **Report** — see Output contract below.

## Common test commands

- Python: `pytest -x` (fail fast) or `pytest --cov=<module> --cov-report=term-missing`
- Node: `npm test` or `npx vitest`
- Rust: `cargo test`
- Generic: try `pytest`, fall back to `python -m unittest`, then to running test files directly.

## Output contract

Final message must include:

1. **Status** — `All passing` / `Failures` / `Blocked`.
2. **Test results** — counts (passed/failed/skipped), wall time, any flaky behavior.
3. **Coverage gaps** — high-priority untested paths with `path:line` references.
4. **Tests added** — list of files written or appended, with a one-line purpose for each.
5. **Defects found** — any production bugs surfaced, with reproduction steps. Recommend `coder` (or `planner` first) for fixes.
```

**Step 2: Commit**

```bash
git add src/amplifier_agent_lib/bundle/agents/tester.md
git commit -m "feat(bundle/agents): vendor tester agent from amplifier-foundation@main"
```

---

## Task 10: Verify all four agent files are byte-identical to upstream

**Step 1: Diff each against upstream**

Run, one per agent:

```bash
for agent in explorer planner coder tester; do
  diff <(curl -sS "https://raw.githubusercontent.com/microsoft/amplifier-foundation/main/experiments/build-up/agents/${agent}.md") "src/amplifier_agent_lib/bundle/agents/${agent}.md" \
    && echo "OK: ${agent}" \
    || echo "DRIFT: ${agent}"
done
```

Expected: four lines, each printing `OK: <name>`. If any prints `DRIFT`, re-fetch and overwrite the local file with the upstream contents, then re-run.

(This task does NOT commit — it is verification only. Upstream may have moved between Task 6 and Task 10; if so, the implementer should re-base the four vendor commits with the fresh contents, or amend each commit. If drift exists and is acceptable, document it in the implementer's status report.)

---

## Task 11: RED — failing test for `AGENTS_DIR` export

**Files:**
- Modify: `tests/test_bundle_loader.py` (append at end)

**Step 1: Add the failing test**

Append the following at the end of `tests/test_bundle_loader.py`:

```python


def test_agents_dir_exposes_vendored_agents() -> None:
    """AGENTS_DIR points at the bundle/agents/ directory containing the four vendored agents."""
    from amplifier_agent_lib.bundle import AGENTS_DIR

    assert AGENTS_DIR.is_dir(), f"AGENTS_DIR does not exist: {AGENTS_DIR}"
    expected_names = {"explorer.md", "planner.md", "coder.md", "tester.md"}
    actual_names = {p.name for p in AGENTS_DIR.iterdir() if p.suffix == ".md"}
    missing = expected_names - actual_names
    assert not missing, f"AGENTS_DIR missing vendored agents: {missing}"
```

**Step 2: Verify it fails**

Run: `uv run pytest tests/test_bundle_loader.py::test_agents_dir_exposes_vendored_agents -v`

Expected: **FAIL** with `ImportError: cannot import name 'AGENTS_DIR' from 'amplifier_agent_lib.bundle'`.

**Step 3: Commit the RED test**

```bash
git add tests/test_bundle_loader.py
git commit -m "test(bundle): RED — expose AGENTS_DIR for vendored agent definitions"
```

---

## Task 12: GREEN — add `AGENTS_DIR` to `bundle/__init__.py`

**Files:**
- Modify: `src/amplifier_agent_lib/bundle/__init__.py`

**Step 1: Add the export**

Open `src/amplifier_agent_lib/bundle/__init__.py`. After the line `BUNDLE_MD: Path = BUNDLE_DIR / "bundle.md"`, append:

```python

#: Directory containing vendored sub-session agent definitions (explorer/planner/coder/tester).
#: Per D1 of docs/designs/2026-05-19-baked-in-bundle-decision.md.
AGENTS_DIR: Path = BUNDLE_DIR / "agents"
```

**Step 2: Verify the test passes**

Run: `uv run pytest tests/test_bundle_loader.py::test_agents_dir_exposes_vendored_agents -v`

Expected: **PASS**.

**Step 3: Verify the whole suite is still green**

Run: `uv run pytest -q`

Expected: all tests PASS (the existing 3 `test_bundle_loader.py` tests + the new agents-dir test + all cache tests + all CLI/runtime tests).

**Step 4: Lint + types clean**

Run:
```bash
uv run ruff check
uv run pyright
```

Expected: both clean (no errors).

**Step 5: Commit**

```bash
git add src/amplifier_agent_lib/bundle/__init__.py
git commit -m "feat(bundle): expose AGENTS_DIR pointing at vendored agent definitions"
```

---

## Phase 1 Exit Criteria

Before starting Phase 2, all of the following must be true:

1. `git log --oneline feat/baked-in-bundle-revisit ^main` shows commits matching the order in this plan (cache key first, then 4 agent vendor commits, then AGENTS_DIR).
2. `uv run pytest -q` → all PASS.
3. `uv run ruff check` → clean.
4. `uv run pyright` → clean.
5. The four files `src/amplifier_agent_lib/bundle/agents/{explorer,planner,coder,tester}.md` exist and parse as valid markdown (Task 10 verified byte-identity with upstream).
6. `from amplifier_agent_lib.bundle import AGENTS_DIR, BUNDLE_MD` works in a fresh Python process.
7. `bundle.md` is **unchanged from its pre-Phase-1 content** — Phase 2 owns its rewrite.

When all criteria pass, proceed to `docs/plans/2026-05-19-baked-in-bundle-impl-phase-2.md`.

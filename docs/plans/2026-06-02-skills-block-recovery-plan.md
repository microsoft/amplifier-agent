# `skills:` block — recovery plan (close Phase 4 gaps)

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Close the five gaps left after the partial SDD pass on `docs/plans/2026-06-02-skills-block-host-config-implementation.md` — specifically: add the missing `tool-skills` bundle entry, surface the `skills:` block in `config show`, document the breaking change in `CHANGELOG.md`, and run the two cold-prepare / end-to-end smoke verifications that the parent plan's Phase 4 was meant to gate on.

**Architecture:** This plan adds no new mechanisms. R1 is a YAML insertion into the vendored `bundle.md`; R2 mirrors the existing per-block reporting pattern in `config_show.py`; R3 is a `CHANGELOG.md` edit. R4/R5 are verification-only — they assert that the work already merged in commits `3a2f285..30c8242` actually composes end-to-end with the new bundle entry and a real `--config` payload.

**Tech Stack:** Python 3.12+, Click 8.x, pytest (with pytest-asyncio), ruff, pyright, PyYAML (already in deps via `bundle.md` parsing). No new dependencies.

**Branch:** `feat/host-config-skills-block` (off `origin/main` d587306). 13 commits ahead.

**Parent design:** `docs/designs/2026-06-01-host-config-layer-revisit.md` — D11/D12/D13.

**Parent plan:** `docs/plans/2026-06-02-skills-block-host-config-implementation.md` — Phases 1–2 complete (commits `3a2f285..f8930f5`); Phase 3 anchors complete (commits `bf8d2a6..d1a0acf`); Phase 4 task 4.5 complete (`30c8242`); tasks 4.1, 4.3, 4.4 deferred to this plan, plus 4.2 (config-show extension) which the parent plan tracked separately as Task 3.5.

---

## Preconditions (verify before starting R1)

This plan presupposes that the parent plan's Phase 1, Phase 2, and the Phase 3 deletion anchors have all landed on the current branch.

**Verify with:**

```bash
cd amplifier-agent

# Branch and tree
git rev-parse --abbrev-ref HEAD          # expect: feat/host-config-skills-block
git status --porcelain                   # expect: clean

# Loader validates skills.* (Phase 1)
grep -q '"skills"' src/amplifier_agent_lib/config/loader.py && echo "loader OK" || echo "MISSING"
grep -q '_validate_skills_block' src/amplifier_agent_lib/config/loader.py && echo "validator OK" || echo "MISSING"

# Merger has list-concat semantics for skills.skills (Phase 2)
grep -q 'skills' src/amplifier_agent_lib/config/merger.py && echo "merger OK" || echo "MISSING"

# Phase 3 deletions completed (regression anchors landed)
test -f tests/cli/test_single_turn.py && echo "test_single_turn.py OK" || echo "MISSING"
test -f tests/cli/test_package_imports.py && echo "test_package_imports.py OK" || echo "MISSING"
! test -f src/amplifier_agent_cli/skill_sources.py && echo "skill_sources.py deleted OK" || echo "skill_sources.py STILL PRESENT"

# Phase 4 task 4.5 (design doc LOCKED)
grep -q 'Status: LOCKED' docs/designs/2026-06-01-host-config-layer-revisit.md && echo "design LOCKED OK" || echo "MISSING"

# Test suite green at the starting point
pytest tests/config/ tests/cli/test_single_turn.py tests/cli/test_package_imports.py -q
```

If any check fails, **stop**. Re-base or re-investigate before proceeding. If everything prints `OK` and the test run is green, proceed to R1.

---

## R1 — Add `tool-skills` to the bundle (revised parent Task 4.1)

The parent plan's Task 4.1 assumed `tool-skills` was already declared in `bundle.md` with a stale source URL that just needed updating. **Reality on this branch:** `bundle.md` declares only three tools (`tool-todo`, `tool-delegate`, `tool-mcp` — lines 54–87). There is no `tool-skills` entry. This R1 inserts the missing entry.

The insertion intentionally changes the sha256 of `bundle.md` and therefore invalidates the cold-prepare cache key. This is expected (D12: bundle.md is the single source of truth for the bundle's static config, and any host-visible default flowing through `skills.skills` must originate there).

**Files this section touches:**
- Modify: `src/amplifier_agent_lib/bundle/bundle.md`
- Create: `tests/bundle/test_tool_skills_declared.py`

Before starting, read `bundle.md` lines 50–90 end-to-end to confirm the existing tool-entry indentation, key ordering, and comment style. Mirror that style for the new entry — do not reformat surrounding lines.

```bash
cd amplifier-agent
sed -n '50,90p' src/amplifier_agent_lib/bundle/bundle.md
```

### Task R1.1: Regression-anchor test for `tool-skills` declaration

**Files:**
- Create: `tests/bundle/test_tool_skills_declared.py`

**Step 1: Write the failing test**

Create `tests/bundle/test_tool_skills_declared.py` with the following content:

```python
"""D11 + parent-plan Phase 4.1 regression anchor.

Asserts the vendored bundle.md declares `tool-skills` with the correct source
URL and ships sensible defaults under `config.skills` and `config.visibility`.
These defaults are what the host_config `skills:` block per D11/D12 merges on
top of.
"""

from __future__ import annotations

import importlib.resources

import yaml


def _load_manifest() -> dict:
    pkg = importlib.resources.files("amplifier_agent_lib.bundle")
    bundle_md = pkg / "bundle.md"
    content = bundle_md.read_text(encoding="utf-8")
    # YAML frontmatter is everything between the first pair of '---\n' fences.
    return yaml.safe_load(content.split("---\n")[1])


def test_bundle_declares_tool_skills() -> None:
    """`tool-skills` is present in the bundle's `tools:` block."""
    manifest = _load_manifest()
    tools = manifest["tools"]
    modules = [t["module"] for t in tools]
    assert "tool-skills" in modules, (
        f"tool-skills must be declared in bundle.md tools: {modules}"
    )


def test_tool_skills_source_points_at_bundle_skills_repo() -> None:
    """Source URL targets amplifier-bundle-skills@main subdirectory."""
    manifest = _load_manifest()
    entry = next(t for t in manifest["tools"] if t["module"] == "tool-skills")
    expected = (
        "git+https://github.com/microsoft/amplifier-bundle-skills"
        "@main#subdirectory=modules/tool-skills"
    )
    assert entry["source"] == expected, entry["source"]


def test_tool_skills_ships_default_skills_sources() -> None:
    """D12: bundle defaults populate `config.skills` so host additions append."""
    manifest = _load_manifest()
    entry = next(t for t in manifest["tools"] if t["module"] == "tool-skills")
    skills = entry["config"]["skills"]
    assert isinstance(skills, list)
    # Three documented sources: the curated bundle, project-local, user-local.
    assert (
        "git+https://github.com/microsoft/amplifier-bundle-skills"
        "@main#subdirectory=skills"
    ) in skills
    assert ".amplifier/skills" in skills
    assert "~/.amplifier/skills" in skills


def test_tool_skills_ships_default_visibility() -> None:
    """D11: visibility defaults shape how skills surface to the LLM."""
    manifest = _load_manifest()
    entry = next(t for t in manifest["tools"] if t["module"] == "tool-skills")
    visibility = entry["config"]["visibility"]
    assert visibility == {
        "enabled": True,
        "inject_role": "user",
        "max_skills_visible": 50,
        "ephemeral": True,
        "priority": 20,
    }
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/bundle/test_tool_skills_declared.py -v`
Expected: 4 FAILs — `tool-skills` not in `modules`, `StopIteration` on the `next(...)` calls.

**Step 3: Commit the failing test**

```bash
git add tests/bundle/test_tool_skills_declared.py
git commit -m "test(bundle): regression anchor for tool-skills declaration

Asserts bundle.md declares tool-skills with the expected source URL and
default skills/visibility config blocks. Currently failing — bundle.md
will be amended in the next commit to declare the module.

Refs: docs/designs/2026-06-01-host-config-layer-revisit.md D11/D12

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

### Task R1.2: Insert the `tool-skills` entry into bundle.md

**Files:**
- Modify: `src/amplifier_agent_lib/bundle/bundle.md`

**Step 1: Read the surrounding lines**

```bash
sed -n '85,90p' src/amplifier_agent_lib/bundle/bundle.md
```

You should see the `tool-mcp` entry ending at line 87 with `max_content_size: 65536` and a blank line at 88 before the hooks comment block.

**Step 2: Apply the insertion**

Locate the exact block:

```yaml
  - module: tool-mcp
    source: git+https://github.com/microsoft/amplifier-module-tool-mcp@main
    config:
      verbose_servers: false
      max_content_size: 65536
```

Append immediately after `max_content_size: 65536` (and before the blank line that precedes the `# Hooks declared inline` comment) the following new entry. Match indentation exactly — two spaces for the list marker, four spaces for keys under the entry, six spaces for nested mapping keys:

```yaml
  - module: tool-skills
    source: git+https://github.com/microsoft/amplifier-bundle-skills@main#subdirectory=modules/tool-skills
    config:
      # D11/D12: skills sources resolved at mount time. Host config `skills.skills`
      # is list-concatenated AFTER these defaults (bundle-first, host-appended).
      skills:
        - git+https://github.com/microsoft/amplifier-bundle-skills@main#subdirectory=skills
        - .amplifier/skills
        - ~/.amplifier/skills
      # D11: visibility shapes how the skills loader injects skill metadata into
      # the LLM context. Host config `skills.visibility` is dict-overlaid on top.
      visibility:
        enabled: true
        inject_role: user
        max_skills_visible: 50
        ephemeral: true
        priority: 20
```

Do not touch any other line in `bundle.md`. The `tools:` block now has four entries.

**Step 3: Run the regression-anchor tests to verify they pass**

Run: `pytest tests/bundle/test_tool_skills_declared.py -v`
Expected: 4 PASS.

Also run the broader bundle suite to confirm no regression on neighbouring tests:
Run: `pytest tests/test_bundle_packaging.py tests/test_bundle_loader.py -v`
Expected: all PASS (cold-prepare may slow tests; allow up to 60 s).

**Step 4: Lint and type-check**

```bash
ruff format src/amplifier_agent_lib/bundle/bundle.md  # no-op — not Python
ruff check src/ tests/
pyright src/ tests/
```

Expected: ruff/pyright clean. (Markdown/YAML are not Python — `ruff format` is harmless but the file should remain untouched by it.)

**Step 5: Commit**

```bash
git add src/amplifier_agent_lib/bundle/bundle.md
git commit -m "feat(bundle): declare tool-skills with default skills/visibility

Adds tool-skills as the fourth parent-level tool in bundle.md, sourced from
amplifier-bundle-skills@main#subdirectory=modules/tool-skills. Ships three
default skill sources (curated bundle, .amplifier/skills, ~/.amplifier/skills)
and visibility defaults so host_config skills.skills list-concatenation
appends to a non-empty baseline (D12).

Cache invalidates as expected — bundle.md sha256 changes by design.

Refs: docs/designs/2026-06-01-host-config-layer-revisit.md D11/D12

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

---

## R2 — Surface the `skills:` block in `config show` (deferred parent Task 3.5)

`config show` already reports `provider`, `host_config`, and XDG paths. Per D8, it must also surface the post-merge `skills` block — bundle-declared sources first, host_config additions appended — so the operator can confirm both that host additions landed and that bundle defaults were not silently dropped.

**Files this section touches:**
- Modify: `src/amplifier_agent_cli/admin/config_show.py`
- Modify: `tests/cli/test_config_show.py`

Before starting, read both files end-to-end:

```bash
wc -l src/amplifier_agent_cli/admin/config_show.py tests/cli/test_config_show.py
sed -n '1,107p' src/amplifier_agent_cli/admin/config_show.py
```

Note in particular `_resolve_host_config` (lines 34–60) and the `payload` assembly in `config_show` (lines 95–105). The new block follows the same pattern: read the merged bundle+host config and emit a JSON-serialisable dict.

### Task R2.1: Failing test — `config show` reports merged `skills` block

**Files:**
- Modify: `tests/cli/test_config_show.py`

**Step 1: Write the failing test**

Append to `tests/cli/test_config_show.py` (after the existing tests):

```python
def test_config_show_reports_merged_skills_block(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D8: config show surfaces the post-merge skills block.

    The reported list is the post-concatenation result per D12 — bundle-declared
    sources first, host_config additions appended — so the operator can confirm
    both that host additions landed and that bundle defaults were not silently
    dropped.
    """
    cfg = tmp_path / "host.json"
    cfg.write_text(
        json.dumps({
            "skills": {
                "skills": ["/tmp/operator-skill-dir"],
                "visibility": {"max_skills_visible": 10},
            }
        }),
        encoding="utf-8",
    )
    monkeypatch.delenv("AMPLIFIER_AGENT_CONFIG", raising=False)
    env = {
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "ANTHROPIC_API_KEY": "sk-test",
    }
    result = runner.invoke(cli, ["config", "show", "--config", str(cfg)], env=env)
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)

    # The skills block is present in the payload.
    assert "skills" in parsed, parsed

    skills_block = parsed["skills"]

    # D12: bundle defaults come first, host additions appended.
    skills_list = skills_block["skills"]
    assert skills_list[-1] == "/tmp/operator-skill-dir"
    # The three bundle defaults are present and precede the host addition.
    assert ".amplifier/skills" in skills_list
    assert "~/.amplifier/skills" in skills_list

    # D11: visibility is dict-overlaid — host override beats bundle default,
    # other bundle defaults pass through unchanged.
    visibility = skills_block["visibility"]
    assert visibility["max_skills_visible"] == 10  # host override
    assert visibility["enabled"] is True             # bundle default preserved
    assert visibility["inject_role"] == "user"       # bundle default preserved


def test_config_show_reports_bundle_skills_when_host_block_absent(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When no host_config is supplied, the reported skills block reflects
    bundle defaults verbatim (D8)."""
    monkeypatch.delenv("AMPLIFIER_AGENT_CONFIG", raising=False)
    env = {
        "HOME": str(tmp_path),
        "ANTHROPIC_API_KEY": "sk-test",
    }
    result = runner.invoke(cli, ["config", "show"], env=env, catch_exceptions=False)
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)

    assert "skills" in parsed
    skills_block = parsed["skills"]

    # Bundle-only state — three default sources, no host append.
    assert ".amplifier/skills" in skills_block["skills"]
    assert "~/.amplifier/skills" in skills_block["skills"]
    assert skills_block["visibility"]["enabled"] is True
    assert skills_block["visibility"]["max_skills_visible"] == 50
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/cli/test_config_show.py::test_config_show_reports_merged_skills_block tests/cli/test_config_show.py::test_config_show_reports_bundle_skills_when_host_block_absent -v`
Expected: 2 FAIL — `KeyError: 'skills'` because the `payload` dict has no `skills` field.

**Step 3: Commit the failing test**

```bash
git add tests/cli/test_config_show.py
git commit -m "test(cli): regression anchor for config show skills block (D8)

Asserts config show surfaces the post-merge skills block — bundle defaults
first, host additions appended (D12), with dict-overlay semantics on
skills.visibility (D11). Currently failing — config_show.py will be
extended in the next commit.

Refs: docs/designs/2026-06-01-host-config-layer-revisit.md D8/D11/D12

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

### Task R2.2: Implement `_resolve_skills` and wire it into the payload

**Files:**
- Modify: `src/amplifier_agent_cli/admin/config_show.py`

**Step 1: Write the implementation**

Add a new helper `_resolve_skills` and reference it in `payload`.

Add the helper after `_resolve_provider` (around line 79):

```python
def _resolve_skills(config_arg: str | None) -> dict[str, Any]:
    """Report the post-merge skills block (D8 + D11/D12).

    Reads the bundle's `tool-skills` static config from bundle.md, then
    overlays the host_config `skills:` block using the same merge semantics
    the runtime uses:
      - `skills.skills` is list-concatenated (bundle-first, host-appended).
      - `skills.visibility` is dict-overlaid (host wins on key collisions).

    Never raises. Parse failures on the host config surface as
    ``parse_error`` while the bundle defaults are still reported so the
    operator sees what would compose absent the broken override.
    """
    # Local imports to keep startup cost off the no-config path.
    from amplifier_agent_lib.config import ConfigError, load_config

    # Bundle defaults — read from the manifest the same way _resolve_provider does.
    bundle_skills: list[str] = []
    bundle_visibility: dict[str, Any] = {}
    try:
        manifest = yaml.safe_load(BUNDLE_MD.read_text(encoding="utf-8").split("---\n")[1])
        if isinstance(manifest, dict):
            for entry in manifest.get("tools") or []:
                if isinstance(entry, dict) and entry.get("module") == "tool-skills":
                    cfg = entry.get("config") or {}
                    if isinstance(cfg, dict):
                        raw_skills = cfg.get("skills")
                        if isinstance(raw_skills, list):
                            bundle_skills = [s for s in raw_skills if isinstance(s, str)]
                        raw_vis = cfg.get("visibility")
                        if isinstance(raw_vis, dict):
                            bundle_visibility = dict(raw_vis)
                    break
    except Exception:
        # Diagnostic-only path — never fail config show.
        pass

    merged_skills = list(bundle_skills)
    merged_visibility = dict(bundle_visibility)
    parse_error: dict[str, str] | None = None

    try:
        parsed = load_config(config_arg=config_arg)
    except ConfigError as exc:
        parsed = None
        parse_error = {"code": exc.code, "message": exc.message}

    if isinstance(parsed, dict):
        host_skills_block = parsed.get("skills")
        if isinstance(host_skills_block, dict):
            host_list = host_skills_block.get("skills")
            if isinstance(host_list, list):
                merged_skills.extend(s for s in host_list if isinstance(s, str))
            host_vis = host_skills_block.get("visibility")
            if isinstance(host_vis, dict):
                merged_visibility.update(host_vis)

    result: dict[str, Any] = {"skills": merged_skills, "visibility": merged_visibility}
    if parse_error is not None:
        result["parse_error"] = parse_error
    return result
```

In `config_show` (around line 95), add `"skills"` to the `payload` dict between `host_config` and the XDG entries:

```python
    payload: dict[str, Any] = {
        "provider": _resolve_provider(),
        "host_config": _resolve_host_config(config_path),
        "skills": _resolve_skills(config_path),
        "xdg_config_home": _annotate_env_or_default("XDG_CONFIG_HOME", home / ".config"),
        "xdg_cache_home": _annotate_env_or_default("XDG_CACHE_HOME", home / ".cache"),
        "xdg_state_home": _annotate_env_or_default("XDG_STATE_HOME", home / ".local" / "state"),
    }
```

**Step 2: Run the new tests to verify they pass**

Run: `pytest tests/cli/test_config_show.py::test_config_show_reports_merged_skills_block tests/cli/test_config_show.py::test_config_show_reports_bundle_skills_when_host_block_absent -v`
Expected: 2 PASS.

**Step 3: Run the full config_show test file to confirm no regression**

Run: `pytest tests/cli/test_config_show.py -v`
Expected: all PASS.

**Step 4: Lint and type-check**

```bash
ruff format src/amplifier_agent_cli/admin/config_show.py
ruff check src/amplifier_agent_cli/admin/config_show.py tests/cli/test_config_show.py
pyright src/amplifier_agent_cli/admin/config_show.py
```

Expected: clean.

**Step 5: Commit**

```bash
git add src/amplifier_agent_cli/admin/config_show.py
git commit -m "feat(cli): surface merged skills block in config show (D8)

Adds _resolve_skills() that reads bundle defaults from bundle.md, then
overlays the host_config skills.* block with the same merge semantics the
runtime uses (list-concat for skills.skills, dict-overlay for
skills.visibility). Reports the post-merge result under payload['skills'].

Never raises — parse errors on the host config surface as parse_error
while bundle defaults are still reported.

Refs: docs/designs/2026-06-01-host-config-layer-revisit.md D8/D11/D12

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

---

## R3 — CHANGELOG entry

The Phase 3 work landed a **breaking change** (`--skills-dir` argv flag removed) and an **added feature** (`skills:` block in host_config). Neither is yet documented in `CHANGELOG.md`. The repo uses Keep a Changelog format; the most recent entry is `[0.3.0 engine / 0.4.0 wrapper] — 2026-05-27`.

**Files this section touches:**
- Modify: `CHANGELOG.md`

Before starting, confirm the existing structure:

```bash
sed -n '1,12p' CHANGELOG.md
```

The file currently has no `[Unreleased]` section. The convention from prior entries is `## [<version>] — <date>` headings, with sub-sections `### Added`, `### Changed`, `### Removed`, `### Migration`, `### Design references`. This R3 introduces an `[Unreleased]` section above `[0.3.0 engine / 0.4.0 wrapper]`.

### Task R3.1: Add `[Unreleased]` entry to CHANGELOG.md

**Files:**
- Modify: `CHANGELOG.md`

**Step 1: Apply the edit**

Insert immediately after the introductory paragraph (between line 6 and the `## [0.3.0 engine / 0.4.0 wrapper] — 2026-05-27` heading on line 8):

```markdown
## [Unreleased]

### Added

- **Engine** `skills:` block as the fifth top-level key in host_config (D11). Pass-through to the `tool-skills` module's `config`. Supports `skills.skills: list[str]` (list-concatenated with bundle-declared sources — D12, bundle-first, host-appended) and `skills.visibility: dict` (dict-overlaid on the bundle's visibility defaults — D11).
- **Bundle** `tool-skills` module declared in `bundle.md` (sourced from `git+https://github.com/microsoft/amplifier-bundle-skills@main#subdirectory=modules/tool-skills`) with three default skill sources (curated bundle, `.amplifier/skills`, `~/.amplifier/skills`) and default visibility config. Cache key invalidates on upgrade (`bundle.md` sha256 changes) — run `amplifier-agent prepare` after upgrade.
- **CLI** `config show` reports the post-merge `skills` block — bundle defaults plus host additions (D8), so operators can confirm both that host additions landed and that bundle defaults were not silently dropped.

### Changed

- **CLI (BREAKING)** `--skills-dir` argv flag removed from `amplifier-agent run`. Migration paths (per D13):
  1. **Preferred — env var**: set `$AMPLIFIER_SKILLS_DIR` (preserved as the adapter-bridge surface). The `tool-skills` module continues to honour it.
  2. **Or — host_config**: add a `skills:` block to your host_config JSON (per D11) and pass it via `--config <path>` or `$AMPLIFIER_AGENT_CONFIG`. Example:
     ```json
     {
       "skills": {
         "skills": ["/path/to/extra/skills"],
         "visibility": {"max_skills_visible": 20}
       }
     }
     ```

### Removed

- **Engine** `src/amplifier_agent_cli/skill_sources.py` (the `inject_skill_dirs()` helper). Unreachable after `--skills-dir` removal.

### Design references

- `docs/designs/2026-06-01-host-config-layer-revisit.md` (D11/D12/D13)

```

(Leave a blank line between the new `[Unreleased]` block and the existing `## [0.3.0 engine / 0.4.0 wrapper] — 2026-05-27` heading.)

**Step 2: Verify the file still parses as the expected structure**

```bash
grep -n '^## ' CHANGELOG.md
```

Expected output (4 `## ` headings):

```
8:## [Unreleased]
<line>:## [0.3.0 engine / 0.4.0 wrapper] — 2026-05-27
<line>:## [0.2.0] — 2026-05-22
<line>:## [0.0.1] — 2026-05-20
```

**Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): document skills block + --skills-dir removal

Adds [Unreleased] entry covering the breaking change (--skills-dir argv
flag removed) and the new skills: top-level key in host_config. Includes
migration guidance per D13 (\$AMPLIFIER_SKILLS_DIR preserved as the
primary bridge for now; host_config skills.* block for richer overrides).

Refs: docs/designs/2026-06-01-host-config-layer-revisit.md D11/D12/D13

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

---

## R4 — Cold-prepare smoke verification

This is a **verification-only** task — no code changes. The goal is to confirm that the bundle.md change in R1 successfully invalidates the cold-prepare cache, that the `amplifier-bundle-skills@main#subdirectory=modules/tool-skills` source resolves under `uv pip install --no-sources`, and that the prepared `mount_plan["tools"]` contains the new `tool-skills` entry.

If any check fails, **halt and report**. Do **not** attempt to fix in this plan — a failure here means either (a) `amplifier-bundle-skills` upstream is not yet ready for our consumption shape, or (b) some preparation-path assumption in `bundle/loader.py` is wrong. Both warrant their own investigation outside the recovery scope.

### Task R4.1: Cold-prepare in an isolated cache

**Step 1: Compute the expected pre/post bundle sha**

```bash
cd amplifier-agent
sha256sum src/amplifier_agent_lib/bundle/bundle.md
# Save this — the cold-prepare cache key includes this sha.
```

**Step 2: Run `amplifier-agent prepare` against an isolated cache directory**

```bash
PREPARE_CACHE="$(mktemp -d -t aaa-prepare-XXXXXX)"
XDG_CACHE_HOME="$PREPARE_CACHE" \
  uv run amplifier-agent prepare 2>&1 | tee /tmp/aaa-prepare-r4.log
echo "exit=$?"
```

Expected:
- Exit code 0.
- Log contains a line referencing `tool-skills` being installed.
- Log contains a line referencing the new `amplifier-bundle-skills@main` source.
- No new errors (compare against a known-good prepare log from before R1 if available).

**Step 3: Verify the prepared pickle mentions `tool-skills` in `mount_plan["tools"]`**

```bash
find "$PREPARE_CACHE" -name 'prepared*.pkl' -print
# Use the path returned by find:
uv run python -c "
import pickle, sys
from pathlib import Path
for p in Path('$PREPARE_CACHE').rglob('prepared*.pkl'):
    pb = pickle.loads(p.read_bytes())
    tools = pb.mount_plan.get('tools', [])
    modules = [t.get('module') for t in tools if isinstance(t, dict)]
    print('PKL:', p)
    print('tools:', modules)
    assert 'tool-skills' in modules, 'tool-skills missing from mount_plan'
    print('OK')
    sys.exit(0)
print('NO PKL FOUND')
sys.exit(1)
"
```

Expected: prints `OK` and exits 0.

**Step 4: Clean up the isolated cache**

```bash
rm -rf "$PREPARE_CACHE"
```

**Step 5: Record the verification**

If everything passed, add a short note to `SCRATCH.md` at the workspace root (the parent paperclip dir, not the amplifier-agent subdir) — this is workspace-ephemeral working memory, not a committed artefact. **Do not commit.** A passing R4 only gates progression to R5; nothing in the repo changes.

If any step failed, **stop the recovery plan here** and surface the failure to the operator with the captured `/tmp/aaa-prepare-r4.log`.

---

## R5 — End-to-end skills-discovery smoke verification

Verification-only. Confirms the parent plan's premise: the `skills:` block, when supplied via `--config`, actually surfaces additional skill directories into the running engine alongside the bundle-default skills.

If either configuration fails its assertion, **halt and report**. As with R4, fixes belong in a follow-up investigation, not this recovery plan.

### Task R5.1: Default discovery — curated skills resolve

**Step 1: Spawn `amplifier-agent run` with no host config**

```bash
cd amplifier-agent
uv run amplifier-agent run "list the names of all skills you have available" \
  2>&1 | tee /tmp/aaa-skills-default.log
echo "exit=$?"
```

Expected:
- Exit code 0.
- Output references curated skill names from `amplifier-bundle-skills@main#subdirectory=skills` — at minimum, `code-review` or `skills-assist` (both are documented entries in the curated bundle, listed in the available-skills surface at session boot).

Verify:

```bash
grep -E 'code-review|skills-assist' /tmp/aaa-skills-default.log && echo "curated OK" || echo "curated MISSING"
```

Expected: prints `curated OK`. If `curated MISSING`, halt and report `/tmp/aaa-skills-default.log`.

### Task R5.2: Host-config additions — operator skill resolves alongside curated

**Step 1: Stage a test skill on disk**

```bash
SKILL_ROOT="$(mktemp -d -t aaa-test-skill-XXXXXX)"
mkdir -p "$SKILL_ROOT/recovery-smoke-skill"
cat > "$SKILL_ROOT/recovery-smoke-skill/SKILL.md" <<'EOF'
---
name: recovery-smoke-skill
description: Synthetic skill used by the host-config-skills-block recovery plan R5 smoke test. Confirms that host_config skills.skills sources are discovered alongside bundle defaults.
---

# recovery-smoke-skill

This skill exists only for the recovery-plan smoke verification. It has no
production purpose. If you see this in real output, R5 ran and forgot to
clean up — please remove $TMPDIR/aaa-test-skill-*.
EOF
```

**Step 2: Write a temporary host config that adds the skill dir**

```bash
HOST_CFG="$(mktemp -t aaa-host-cfg-XXXXXX.json)"
cat > "$HOST_CFG" <<EOF
{
  "skills": {
    "skills": ["$SKILL_ROOT"]
  }
}
EOF
cat "$HOST_CFG"
```

**Step 3: Spawn `amplifier-agent run` with `--config`**

```bash
uv run amplifier-agent run --config "$HOST_CFG" \
  "list the names of all skills you have available" \
  2>&1 | tee /tmp/aaa-skills-merged.log
echo "exit=$?"
```

Expected:
- Exit code 0.
- Output references BOTH a curated skill (`code-review` OR `skills-assist`) AND the operator-supplied `recovery-smoke-skill`.

Verify:

```bash
grep -E 'code-review|skills-assist' /tmp/aaa-skills-merged.log && echo "curated still OK" || echo "curated DROPPED"
grep -E 'recovery-smoke-skill' /tmp/aaa-skills-merged.log && echo "host append OK" || echo "host append MISSING"
```

Expected: both `curated still OK` and `host append OK`. If either is missing, halt and report `/tmp/aaa-skills-merged.log` plus the contents of `$HOST_CFG` and `$SKILL_ROOT`.

**Step 4: Clean up**

```bash
rm -rf "$SKILL_ROOT" "$HOST_CFG"
rm -f /tmp/aaa-skills-default.log /tmp/aaa-skills-merged.log /tmp/aaa-prepare-r4.log
```

**Step 5: Record the verification**

R5 produces no commit. If both R5.1 and R5.2 passed, the recovery plan is complete — the bundle is structurally correct (R1), `config show` reports the merged state (R2), the breaking change is documented (R3), the cold-prepare path works (R4), and end-to-end skill discovery composes bundle + host correctly (R5).

---

## Closeout checklist

After all five sections, verify the branch state matches expectations:

```bash
cd amplifier-agent

# Branch state
git log --oneline origin/main..HEAD
# Expect: 13 pre-existing commits + 4 new commits from this plan:
#   R1.1 test(bundle): regression anchor for tool-skills declaration
#   R1.2 feat(bundle): declare tool-skills with default skills/visibility
#   R2.1 test(cli): regression anchor for config show skills block (D8)
#   R2.2 feat(cli): surface merged skills block in config show (D8)
#   R3   docs(changelog): document skills block + --skills-dir removal
# Total: 18 commits ahead of origin/main.

# Tree is clean
git status --porcelain          # expect: empty

# Full test suite green
pytest -q
# Expect: all green. Time budget: ~3-5 min depending on cold prepares.

# Lint and type-check are green
ruff format --check src/ tests/
ruff check src/ tests/
pyright src/ tests/
# Expect: all clean.
```

If all checks pass, the branch is ready for PR. The PR description should reference both `docs/designs/2026-06-01-host-config-layer-revisit.md` (the parent design, LOCKED) and both implementation plans (`docs/plans/2026-06-02-skills-block-host-config-implementation.md` for Phases 1–3 and 4.5; this plan for the recovery work).

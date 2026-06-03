# `skills:` block — host_config implementation plan

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Implement D11, D12, D13 (plus D4/D5/D7/D8/D10 amendments) from `docs/designs/2026-06-01-host-config-layer-revisit.md`: add a `skills:` block as the fifth top-level host_config key, drop `--skills-dir`, preserve `$AMPLIFIER_SKILLS_DIR`.

**Architecture:** The host_config layer parses a JSON file and validates against a fixed top-level allowlist, then merges each block over the bundle's static config at mount time. This plan extends the existing allowlist with `skills`, adds list-concat merge semantics for `skills.skills` (the first list-shaped value in the layer), removes the per-turn `--skills-dir` argv surface, and updates `bundle.md`'s `tool-skills` entry to source from the bundle skills repo.

**Tech Stack:** Python 3.12+, Click 8.x, pytest (with pytest-asyncio), ruff, pyright. No new dependencies.

---

## Preconditions (verify before starting Phase 1)

This plan presupposes the D1–D10 host_config foundation work has already landed in the working branch — specifically:

1. `src/amplifier_agent_lib/config/loader.py` exists and validates four top-level keys (`mcp`, `approval`, `provider`, `allowProtocolSkew`) against a strict allowlist, raising the four error codes named in D7 (`config_unreadable`, `config_malformed_json`, `config_unknown_key`, `config_invalid_type`).
2. `src/amplifier_agent_lib/config/merger.py` exists and implements `{**bundle_static, **host_overrides}` dict-overlay merging into `prepared.mount_plan` for the four existing blocks, raising `config_no_matching_module` per D7.
3. `tests/config/` exists with `test_loader.py` and `test_merger.py` covering the four existing blocks.
4. `--config <path>` is wired through `single_turn.py` and `$AMPLIFIER_AGENT_CONFIG` resolution works.

**Verify with:**

```bash
test -f src/amplifier_agent_lib/config/loader.py && echo "loader OK" || echo "MISSING"
test -f src/amplifier_agent_lib/config/merger.py && echo "merger OK" || echo "MISSING"
test -d tests/config && echo "tests/config/ OK" || echo "MISSING"
grep -q 'config_unknown_key' src/amplifier_agent_lib/config/loader.py && echo "validation OK" || echo "MISSING"
```

If any line prints `MISSING`, stop. This plan cannot be executed against a branch that lacks the D1–D10 foundation. Re-base onto the host-config branch first (or check with the design owner which branch to target).

If everything prints `OK`, the loader/merger module structure is in place — proceed.

---

## Phase 1 — Config schema + validation (6 tasks)

Add `skills` as the fifth recognized top-level key. Validate the inner shape (`skills.skills` list-of-strings, `skills.visibility` dict, no other inner keys) per D7 and D11.

**Files this phase touches:**
- Modify: `src/amplifier_agent_lib/config/loader.py`
- Modify: `tests/config/test_loader.py`

Before starting, read both files end-to-end so you understand the existing validation patterns:

```bash
wc -l src/amplifier_agent_lib/config/loader.py tests/config/test_loader.py
```

The existing allowlist is the load-bearing pattern — mirror it for each new check.

### Task 1.1: Add `skills` to the top-level allowlist

**Files:**
- Modify: `src/amplifier_agent_lib/config/loader.py`
- Modify: `tests/config/test_loader.py`

**Step 1: Write the failing test**

Append to `tests/config/test_loader.py`:

```python
def test_loader_accepts_skills_top_level_key(tmp_path: Path) -> None:
    """D11: `skills` is the fifth recognized top-level key; presence is allowed."""
    cfg = tmp_path / "aaa.json"
    cfg.write_text(json.dumps({"skills": {}}))

    parsed = load_config(str(cfg))

    assert parsed.skills == {}
```

(Adjust the import / `load_config` symbol name to match what the existing tests use; the symbol is the project's load entry point — find it with `grep -n 'def load' src/amplifier_agent_lib/config/loader.py`.)

**Step 2: Run test to verify it fails**

Run: `pytest tests/config/test_loader.py::test_loader_accepts_skills_top_level_key -v`
Expected: FAIL — either `config_unknown_key` raised (current allowlist rejects `skills`) or `AttributeError: 'ParsedConfig' object has no attribute 'skills'` (parser has no field for it).

**Step 3: Write minimal implementation**

In `src/amplifier_agent_lib/config/loader.py`:

1. Locate the top-level allowlist constant (look for the four existing keys `"mcp"`, `"approval"`, `"provider"`, `"allowProtocolSkew"`). Add `"skills"`.
2. Locate the parsed-config dataclass / TypedDict (look for the field `mcp:` or `approval:`). Add a `skills` field with the same default-empty-dict shape as `mcp`.
3. Locate the parsing branch that maps the raw JSON dict's `mcp` key onto the dataclass. Add the analogous branch for `skills`.

The diff is small (~6 lines) — mirror the existing `mcp` handling line-for-line.

**Step 4: Run test to verify it passes**

Run: `pytest tests/config/test_loader.py::test_loader_accepts_skills_top_level_key -v`
Expected: PASS.

Also run the full loader suite to confirm no regression:
Run: `pytest tests/config/test_loader.py -v`
Expected: all existing tests still PASS.

**Step 5: Commit**

```bash
git add src/amplifier_agent_lib/config/loader.py tests/config/test_loader.py
git commit -m "feat(config): add skills to top-level key allowlist

Implements D11. Skills is now the fifth recognized top-level
host_config key alongside mcp, approval, provider, allowProtocolSkew.

Refs: docs/designs/2026-06-01-host-config-layer-revisit.md D11

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

### Task 1.2: Validate `skills.skills` as list-of-strings

**Files:**
- Modify: `src/amplifier_agent_lib/config/loader.py`
- Modify: `tests/config/test_loader.py`

**Step 1: Write the failing test**

Append to `tests/config/test_loader.py`:

```python
def test_loader_rejects_skills_skills_non_list(tmp_path: Path) -> None:
    """D11/D7: `skills.skills` must be a list. Non-list -> config_invalid_type."""
    cfg = tmp_path / "aaa.json"
    cfg.write_text(json.dumps({"skills": {"skills": "not-a-list"}}))

    with pytest.raises(AaaError) as exc_info:
        load_config(str(cfg))

    assert exc_info.value.code == "config_invalid_type"
    assert exc_info.value.classification == "protocol"


def test_loader_rejects_skills_skills_non_string_member(tmp_path: Path) -> None:
    """D11/D7: list members must be strings; integer member -> config_invalid_type."""
    cfg = tmp_path / "aaa.json"
    cfg.write_text(json.dumps({"skills": {"skills": ["a-valid-uri", 123]}}))

    with pytest.raises(AaaError) as exc_info:
        load_config(str(cfg))

    assert exc_info.value.code == "config_invalid_type"


def test_loader_accepts_skills_skills_valid_list(tmp_path: Path) -> None:
    """Happy path: list of strings parses cleanly."""
    sources = [
        "git+https://github.com/microsoft/amplifier-bundle-skills@main#subdirectory=skills",
        ".amplifier/skills",
        "~/.amplifier/skills",
    ]
    cfg = tmp_path / "aaa.json"
    cfg.write_text(json.dumps({"skills": {"skills": sources}}))

    parsed = load_config(str(cfg))

    assert parsed.skills["skills"] == sources
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/config/test_loader.py -v -k "skills_skills"`
Expected: the two `rejects_*` tests FAIL (no validation raised yet); the `accepts_*` test should PASS (Task 1.1 already plumbs the field through).

If `accepts_*` also fails, Task 1.1's parsing branch did not preserve the inner dict — fix that before continuing.

**Step 3: Write minimal implementation**

In `src/amplifier_agent_lib/config/loader.py`, locate the per-block validation function (probably named `_validate_*` or called per-block from the main parse). Add a `_validate_skills_block(skills_dict)` helper that:

1. If `"skills"` key is present in `skills_dict`:
   - Raise `AaaError(code="config_invalid_type", classification="protocol", message="skills.skills must be a list")` if `not isinstance(skills_dict["skills"], list)`.
   - For each member, raise `AaaError(code="config_invalid_type", classification="protocol", message=f"skills.skills[{i}] must be a string, got {type(...).__name__}")` if `not isinstance(member, str)`.
2. Call this helper from the same place that calls `_validate_mcp_block` / `_validate_approval_block` (find with `grep -n '_validate_' src/amplifier_agent_lib/config/loader.py`).

Mirror the existing `approval.patterns` list-member validation if it exists — the shape is identical.

**Step 4: Run tests to verify pass**

Run: `pytest tests/config/test_loader.py -v -k "skills_skills"`
Expected: all three tests PASS.

Run: `pytest tests/config/test_loader.py -v`
Expected: full suite passes — nothing else broken.

**Step 5: Commit**

```bash
git add src/amplifier_agent_lib/config/loader.py tests/config/test_loader.py
git commit -m "feat(config): validate skills.skills as list of strings

D11 + D7. Non-list value or non-string member raises
config_invalid_type with classification=protocol.

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

### Task 1.3: Validate `skills.visibility` as dict

**Files:**
- Modify: `src/amplifier_agent_lib/config/loader.py`
- Modify: `tests/config/test_loader.py`

**Step 1: Write the failing test**

Append to `tests/config/test_loader.py`:

```python
def test_loader_rejects_skills_visibility_non_dict(tmp_path: Path) -> None:
    """D11/D7: `skills.visibility` must be a dict. List -> config_invalid_type."""
    cfg = tmp_path / "aaa.json"
    cfg.write_text(json.dumps({"skills": {"visibility": ["enabled"]}}))

    with pytest.raises(AaaError) as exc_info:
        load_config(str(cfg))

    assert exc_info.value.code == "config_invalid_type"


def test_loader_accepts_skills_visibility_valid_dict(tmp_path: Path) -> None:
    """Happy path: visibility dict parses with inner keys preserved verbatim."""
    visibility = {
        "enabled": True,
        "inject_role": "user",
        "max_skills_visible": 50,
        "ephemeral": True,
        "priority": 20,
    }
    cfg = tmp_path / "aaa.json"
    cfg.write_text(json.dumps({"skills": {"visibility": visibility}}))

    parsed = load_config(str(cfg))

    assert parsed.skills["visibility"] == visibility


def test_loader_passes_through_unknown_visibility_inner_keys(tmp_path: Path) -> None:
    """D11/D7 pass-through: unknown keys INSIDE skills.visibility are NOT validated."""
    cfg = tmp_path / "aaa.json"
    cfg.write_text(json.dumps({"skills": {"visibility": {"future_module_key": "x"}}}))

    parsed = load_config(str(cfg))

    assert parsed.skills["visibility"]["future_module_key"] == "x"
```

**Step 2: Run tests to verify fail**

Run: `pytest tests/config/test_loader.py -v -k "skills_visibility"`
Expected: `rejects_*` FAILs (no validation); `accepts_*` and `passes_through_*` should PASS via Task 1.1's plumbing.

**Step 3: Write minimal implementation**

In `_validate_skills_block` (from Task 1.2), add: if `"visibility"` is present in `skills_dict`, raise `AaaError(code="config_invalid_type", classification="protocol", message="skills.visibility must be a dict")` if `not isinstance(skills_dict["visibility"], dict)`.

Do NOT iterate inner keys — per D11, inner keys under `skills.visibility` are pass-through and the module owns their validation.

**Step 4: Run tests to verify pass**

Run: `pytest tests/config/test_loader.py -v -k "skills_visibility"`
Expected: all three PASS.

**Step 5: Commit**

```bash
git add src/amplifier_agent_lib/config/loader.py tests/config/test_loader.py
git commit -m "feat(config): validate skills.visibility as dict

D11 + D7. Non-dict raises config_invalid_type. Inner keys are
pass-through to the tool-skills module per D7's pass-through rule.

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

### Task 1.4: Reject unknown sub-keys under `skills.*`

**Files:**
- Modify: `src/amplifier_agent_lib/config/loader.py`
- Modify: `tests/config/test_loader.py`

**Step 1: Write the failing test**

Append to `tests/config/test_loader.py`:

```python
def test_loader_rejects_unknown_skills_subkey(tmp_path: Path) -> None:
    """D11: only `skills` and `visibility` are recognized under skills.*."""
    cfg = tmp_path / "aaa.json"
    cfg.write_text(json.dumps({"skills": {"sources": ["x"]}}))

    with pytest.raises(AaaError) as exc_info:
        load_config(str(cfg))

    assert exc_info.value.code == "config_invalid_type"
    assert "sources" in exc_info.value.message
```

**Step 2: Run test to verify fail**

Run: `pytest tests/config/test_loader.py::test_loader_rejects_unknown_skills_subkey -v`
Expected: FAIL — currently no validation of `skills` sub-keys.

**Step 3: Write minimal implementation**

In `_validate_skills_block`, add at the top:

```python
_ALLOWED_SKILLS_SUBKEYS = frozenset({"skills", "visibility"})

# inside _validate_skills_block, before per-key validation:
unknown = set(skills_dict.keys()) - _ALLOWED_SKILLS_SUBKEYS
if unknown:
    raise AaaError(
        code="config_invalid_type",
        classification="protocol",
        message=(
            f"Unknown sub-keys under skills.*: {sorted(unknown)}. "
            f"Allowed: {sorted(_ALLOWED_SKILLS_SUBKEYS)}."
        ),
    )
```

(Per D11, this is `config_invalid_type` — not `config_unknown_key`, which is reserved for top-level keys per D7.)

**Step 4: Run test to verify pass**

Run: `pytest tests/config/test_loader.py::test_loader_rejects_unknown_skills_subkey -v`
Expected: PASS.

Run full suite:
Run: `pytest tests/config/test_loader.py -v`
Expected: all PASS.

**Step 5: Commit**

```bash
git add src/amplifier_agent_lib/config/loader.py tests/config/test_loader.py
git commit -m "feat(config): reject unknown sub-keys under skills.*

D11 closes the skills.* inner shape against {skills, visibility}.
Unknown sub-keys raise config_invalid_type. Pass-through rule
(D7) applies only one level deeper, inside skills.visibility.

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

### Task 1.5: End-to-end loader integration test

**Files:**
- Modify: `tests/config/test_loader.py`

**Step 1: Write the failing test**

(This is a happy-path test that should pass already given Tasks 1.1–1.4 — it confirms the full block round-trips. Useful as a regression anchor.)

Append to `tests/config/test_loader.py`:

```python
def test_loader_end_to_end_skills_block(tmp_path: Path) -> None:
    """Full canonical skills: block parses with both inner keys intact."""
    skills_block = {
        "skills": [
            "git+https://github.com/microsoft/amplifier-bundle-skills@main#subdirectory=skills",
            ".amplifier/skills",
            "~/.amplifier/skills",
        ],
        "visibility": {
            "enabled": True,
            "inject_role": "user",
            "max_skills_visible": 50,
            "ephemeral": True,
            "priority": 20,
        },
    }
    cfg = tmp_path / "aaa.json"
    cfg.write_text(json.dumps({"skills": skills_block}))

    parsed = load_config(str(cfg))

    assert parsed.skills == skills_block
```

**Step 2: Run test to verify pass**

Run: `pytest tests/config/test_loader.py::test_loader_end_to_end_skills_block -v`
Expected: PASS (no new implementation needed; Tasks 1.1–1.4 cover it).

If it FAILS, one of the prior tasks left a gap — go back and fix the responsible task before continuing.

**Step 3: Commit**

```bash
git add tests/config/test_loader.py
git commit -m "test(config): end-to-end skills: block round-trip

Regression anchor for D11 inner-shape validation.

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

### Task 1.6: Phase 1 sanity check

**Run the full project test suite + lint to confirm Phase 1 is clean before moving on:**

```bash
cd /Users/mpaidiparthy/repos/amplifier-paperclip/amplifier-agent
uv run pytest tests/config/ -v
uv run ruff check src/amplifier_agent_lib/config/ tests/config/
uv run ruff format --check src/amplifier_agent_lib/config/ tests/config/
uv run pyright src/amplifier_agent_lib/config/
```

Expected: all green. If anything reports issues, fix them in a follow-up commit before Phase 2.

---

## Phase 2 — Merger logic (7 tasks)

Implement D12's list-concat merge for `skills.skills`, D5's dict-overlay for `skills.visibility`, and D7's `config_no_matching_module` when the host writes a `skills:` block but the bundle declares no `tool-skills` mount.

**Files this phase touches:**
- Modify: `src/amplifier_agent_lib/config/merger.py`
- Modify: `tests/config/test_merger.py`

Before starting, read `merger.py` end-to-end and especially the existing `mcp` / `approval` / `provider` merge branches — the new `skills` branch mirrors them but with list-concat instead of dict-overlay for `skills.skills`.

### Task 2.1: Merger finds `tool-skills` mount entry

**Files:**
- Modify: `src/amplifier_agent_lib/config/merger.py`
- Modify: `tests/config/test_merger.py`

**Step 1: Write the failing test**

Append to `tests/config/test_merger.py`:

```python
def test_merge_skills_block_locates_tool_skills_entry() -> None:
    """D5/D11: `skills` block targets the tool-skills mount entry in mount_plan['tools']."""
    prepared = SimpleNamespace(
        mount_plan={
            "tools": [
                {"module": "tool-todo", "config": {}},
                {"module": "tool-skills", "config": {"skills": [], "visibility": {}}},
            ]
        }
    )
    parsed = SimpleNamespace(skills={"visibility": {"enabled": True}})

    merge_config(prepared, parsed)

    tool_skills_entry = next(e for e in prepared.mount_plan["tools"] if e["module"] == "tool-skills")
    assert tool_skills_entry["config"]["visibility"]["enabled"] is True
```

(Adjust `merge_config` symbol if the project uses a different name — find it with `grep -n 'def merge' src/amplifier_agent_lib/config/merger.py`.)

**Step 2: Run test to verify fail**

Run: `pytest tests/config/test_merger.py::test_merge_skills_block_locates_tool_skills_entry -v`
Expected: FAIL — merger has no branch for `skills`.

**Step 3: Write minimal implementation**

In `src/amplifier_agent_lib/config/merger.py`, locate the per-block dispatch (probably an `if parsed.mcp: ... if parsed.approval: ...` chain). Add a `skills` branch that:

1. Finds the entry in `prepared.mount_plan["tools"]` where `entry["module"] == "tool-skills"`. If not found and `parsed.skills` is non-empty, raise `AaaError(code="config_no_matching_module", classification="protocol", message="host_config declares skills block but bundle has no tool-skills mount")` — but defer the raise to Task 2.5. For now: if missing, return without crashing.
2. Get/create `entry["config"]` dict.
3. Dispatch to two sub-mergers: list-concat for `skills` (Task 2.2), dict-overlay for `visibility` (Task 2.3). For now, implement an inline naive `entry["config"]["visibility"] = {**entry["config"].get("visibility", {}), **parsed.skills.get("visibility", {})}` so the test passes — Task 2.3 will refactor.

Skeleton (adjust to match existing merger style):

```python
def _merge_skills(prepared: Any, skills_block: dict) -> None:
    if not skills_block:
        return
    tools = prepared.mount_plan.get("tools", [])
    entry = next((e for e in tools if e.get("module") == "tool-skills"), None)
    if entry is None:
        # Task 2.5 turns this into a raise.
        return
    cfg = entry.setdefault("config", {})
    if "visibility" in skills_block:
        cfg["visibility"] = {**cfg.get("visibility", {}), **skills_block["visibility"]}
    if "skills" in skills_block:
        # Task 2.2 replaces this with list-concat.
        cfg["skills"] = list(cfg.get("skills", [])) + list(skills_block["skills"])
```

Call `_merge_skills(prepared, parsed.skills)` from the main `merge_config` body alongside the other block dispatchers.

**Step 4: Run test to verify pass**

Run: `pytest tests/config/test_merger.py::test_merge_skills_block_locates_tool_skills_entry -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add src/amplifier_agent_lib/config/merger.py tests/config/test_merger.py
git commit -m "feat(config): merge skills block into tool-skills mount entry

D5/D11. merger now dispatches the skills block onto the
tool-skills mount config. List-concat semantics for skills.skills
and config_no_matching_module are wired in follow-up commits.

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

### Task 2.2: List-concat merge for `skills.skills`

**Files:**
- Modify: `src/amplifier_agent_lib/config/merger.py`
- Modify: `tests/config/test_merger.py`

**Step 1: Write the failing test**

Append to `tests/config/test_merger.py`:

```python
def test_merge_skills_list_concatenates_bundle_then_host() -> None:
    """D12: bundle's skills list comes first; host's list is appended."""
    prepared = SimpleNamespace(
        mount_plan={
            "tools": [
                {
                    "module": "tool-skills",
                    "config": {"skills": ["bundle-source-1"]},
                }
            ]
        }
    )
    parsed = SimpleNamespace(skills={"skills": ["host-source-1", "host-source-2"]})

    merge_config(prepared, parsed)

    entry = prepared.mount_plan["tools"][0]
    assert entry["config"]["skills"] == [
        "bundle-source-1",
        "host-source-1",
        "host-source-2",
    ]


def test_merge_skills_empty_host_list_preserves_bundle() -> None:
    """D12: empty host skills.skills leaves bundle list unchanged."""
    prepared = SimpleNamespace(
        mount_plan={"tools": [{"module": "tool-skills", "config": {"skills": ["b1", "b2"]}}]}
    )
    parsed = SimpleNamespace(skills={"skills": []})

    merge_config(prepared, parsed)

    assert prepared.mount_plan["tools"][0]["config"]["skills"] == ["b1", "b2"]


def test_merge_skills_no_bundle_list_uses_host_only() -> None:
    """D12: bundle without a skills key still gets host additions."""
    prepared = SimpleNamespace(mount_plan={"tools": [{"module": "tool-skills", "config": {}}]})
    parsed = SimpleNamespace(skills={"skills": ["h1"]})

    merge_config(prepared, parsed)

    assert prepared.mount_plan["tools"][0]["config"]["skills"] == ["h1"]
```

**Step 2: Run tests to verify**

Run: `pytest tests/config/test_merger.py -v -k "skills_list"`
Expected: the first two probably PASS already (Task 2.1's naive impl), but verify explicitly. If any FAILs, the inline concat in Task 2.1 was wrong — fix.

**Step 3: Promote the inline concat to a named helper**

Refactor in `merger.py`: extract the inline `cfg["skills"] = list(...) + list(...)` from Task 2.1 into a named helper for clarity and future reuse:

```python
def _concat_list_pass_through(bundle_value: list | None, host_value: list) -> list:
    """D12: list-concat merge for list-shaped pass-through values.

    Bundle's list comes first; host's list is appended. Bundle is the floor,
    host extends, host cannot silently erase. Generalizes per D12 to any
    future list-shaped pass-through sub-key.
    """
    return list(bundle_value or []) + list(host_value)
```

And replace the inline expression in `_merge_skills` with `cfg["skills"] = _concat_list_pass_through(cfg.get("skills"), skills_block["skills"])`.

**Step 4: Run tests to verify pass**

Run: `pytest tests/config/test_merger.py -v -k "skills_list"`
Expected: all three PASS.

**Step 5: Commit**

```bash
git add src/amplifier_agent_lib/config/merger.py tests/config/test_merger.py
git commit -m "feat(config): list-concat merge for skills.skills

D12. Bundle's curated sources come first; host_config additions
are appended. Bundle is the floor, host extends, host cannot
silently erase. The _concat_list_pass_through helper generalizes
to any future list-shaped pass-through value per D12's closing
paragraph.

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

### Task 2.3: Dict-overlay merge for `skills.visibility`

**Files:**
- Modify: `src/amplifier_agent_lib/config/merger.py`
- Modify: `tests/config/test_merger.py`

**Step 1: Write the failing test**

Append to `tests/config/test_merger.py`:

```python
def test_merge_skills_visibility_overlays_per_key() -> None:
    """D5: visibility merges {**bundle, **host} — host wins per-key, bundle keys preserved."""
    prepared = SimpleNamespace(
        mount_plan={
            "tools": [
                {
                    "module": "tool-skills",
                    "config": {
                        "visibility": {
                            "enabled": True,
                            "priority": 20,
                            "inject_role": "user",
                        }
                    },
                }
            ]
        }
    )
    parsed = SimpleNamespace(skills={"visibility": {"priority": 10}})

    merge_config(prepared, parsed)

    visibility = prepared.mount_plan["tools"][0]["config"]["visibility"]
    assert visibility == {
        "enabled": True,         # bundle preserved
        "inject_role": "user",   # bundle preserved
        "priority": 10,          # host overrode
    }
```

**Step 2: Run test to verify pass**

Run: `pytest tests/config/test_merger.py::test_merge_skills_visibility_overlays_per_key -v`
Expected: PASS already given Task 2.1's inline overlay. If it FAILs, fix the visibility branch in `_merge_skills`.

**Step 3: Commit (regression anchor)**

```bash
git add tests/config/test_merger.py
git commit -m "test(config): skills.visibility dict-overlay regression anchor

D5. Confirms host overrides win per-key while bundle-declared
keys not in the host block are preserved.

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

### Task 2.4: Missing `tool-skills` entry → `config_no_matching_module`

**Files:**
- Modify: `src/amplifier_agent_lib/config/merger.py`
- Modify: `tests/config/test_merger.py`

**Step 1: Write the failing test**

Append to `tests/config/test_merger.py`:

```python
def test_merge_skills_block_without_tool_skills_mount_raises() -> None:
    """D7: host writes skills: block but bundle has no tool-skills -> config_no_matching_module."""
    prepared = SimpleNamespace(
        mount_plan={"tools": [{"module": "tool-todo", "config": {}}]}
    )
    parsed = SimpleNamespace(skills={"skills": ["host-source"]})

    with pytest.raises(AaaError) as exc_info:
        merge_config(prepared, parsed)

    assert exc_info.value.code == "config_no_matching_module"
    assert exc_info.value.classification == "protocol"


def test_merge_empty_skills_block_without_tool_skills_is_noop() -> None:
    """Boundary: empty skills block + no tool-skills mount does NOT raise.

    The error code fires only when the host actually declares content. An
    empty/missing block carries no expectation and must not surprise the
    operator at runtime.
    """
    prepared = SimpleNamespace(
        mount_plan={"tools": [{"module": "tool-todo", "config": {}}]}
    )
    parsed = SimpleNamespace(skills={})

    merge_config(prepared, parsed)  # must not raise

    assert prepared.mount_plan["tools"] == [{"module": "tool-todo", "config": {}}]
```

**Step 2: Run tests to verify fail**

Run: `pytest tests/config/test_merger.py -v -k "without_tool_skills"`
Expected: the `raises` test FAILs (Task 2.1's stub returns silently); the `noop` test PASSes.

**Step 3: Write minimal implementation**

In `_merge_skills`, replace the silent-return stub from Task 2.1 with:

```python
if entry is None:
    if not skills_block:
        return  # empty block + no mount = no-op (D7 boundary)
    raise AaaError(
        code="config_no_matching_module",
        classification="protocol",
        message=(
            "host_config declares a skills: block but the bundle has no "
            "tool-skills mount entry. Either add tool-skills to the bundle "
            "or remove the skills: block from host_config."
        ),
    )
```

**Step 4: Run tests to verify pass**

Run: `pytest tests/config/test_merger.py -v -k "without_tool_skills"`
Expected: both PASS.

**Step 5: Commit**

```bash
git add src/amplifier_agent_lib/config/merger.py tests/config/test_merger.py
git commit -m "feat(config): raise config_no_matching_module for orphan skills block

D7. When host_config carries a non-empty skills: block but the
bundle declares no tool-skills mount, merging raises
config_no_matching_module rather than silently dropping the
config. Empty block + missing mount remains a no-op.

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

### Task 2.5: Full canonical-block round-trip integration test

**Files:**
- Modify: `tests/config/test_merger.py`

**Step 1: Write the failing test**

Append to `tests/config/test_merger.py`:

```python
def test_merge_full_skills_block_against_bundle_defaults() -> None:
    """End-to-end: bundle.md's tool-skills entry + canonical host skills block -> merged result.

    Mirrors the canonical example from D11. The bundle has 1 source URL
    and a visibility dict; the host adds 2 more sources and overrides
    priority. Expected: 3 sources in order, all visibility keys preserved.
    """
    prepared = SimpleNamespace(
        mount_plan={
            "tools": [
                {
                    "module": "tool-skills",
                    "config": {
                        "skills": [
                            "git+https://github.com/microsoft/amplifier-bundle-skills@main#subdirectory=skills"
                        ],
                        "visibility": {
                            "enabled": True,
                            "inject_role": "user",
                            "max_skills_visible": 50,
                            "ephemeral": True,
                            "priority": 20,
                        },
                    },
                }
            ]
        }
    )
    parsed = SimpleNamespace(
        skills={
            "skills": [".amplifier/skills", "~/.amplifier/skills"],
            "visibility": {"priority": 10},
        }
    )

    merge_config(prepared, parsed)

    entry = prepared.mount_plan["tools"][0]
    assert entry["config"]["skills"] == [
        "git+https://github.com/microsoft/amplifier-bundle-skills@main#subdirectory=skills",
        ".amplifier/skills",
        "~/.amplifier/skills",
    ]
    assert entry["config"]["visibility"] == {
        "enabled": True,
        "inject_role": "user",
        "max_skills_visible": 50,
        "ephemeral": True,
        "priority": 10,  # host override
    }
```

**Step 2: Run test to verify pass**

Run: `pytest tests/config/test_merger.py::test_merge_full_skills_block_against_bundle_defaults -v`
Expected: PASS — all behavior is implemented by Tasks 2.1–2.4.

If it FAILS, the merger left a gap. Fix before continuing.

**Step 3: Commit**

```bash
git add tests/config/test_merger.py
git commit -m "test(config): end-to-end skills block merge regression anchor

D11/D12/D5/D7. Canonical example from D11: bundle's curated
source + host's two extra sources + visibility override.
Confirms list-concat + dict-overlay + per-key precedence
all behave per spec.

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

### Task 2.6: Phase 2 sanity check

Run the full config suite + lint:

```bash
cd /Users/mpaidiparthy/repos/amplifier-paperclip/amplifier-agent
uv run pytest tests/config/ -v
uv run ruff check src/amplifier_agent_lib/config/ tests/config/
uv run ruff format --check src/amplifier_agent_lib/config/ tests/config/
uv run pyright src/amplifier_agent_lib/config/
```

Expected: all green.

---

## Phase 3 — CLI cleanup (5 tasks)

Drop `--skills-dir` argv and its supporting helper. Extend `config show` to report the new `skills` block per D8.

**Files this phase touches:**
- Modify: `src/amplifier_agent_cli/modes/single_turn.py`
- Delete: `src/amplifier_agent_cli/skill_sources.py`
- Delete: `tests/cli/test_skill_sources.py`
- Modify: `tests/cli/test_single_turn.py`
- Modify: `src/amplifier_agent_cli/admin/config_show.py`
- Modify: `tests/cli/test_config_show.py`
- Modify: `tests/bundle/test_bundle_manifest.py`

### Task 3.1: Invert help-text assertion for `--skills-dir`

**Files:**
- Modify: `tests/cli/test_single_turn.py`

**Step 1: Read the existing test**

```bash
sed -n '449,475p' /Users/mpaidiparthy/repos/amplifier-paperclip/amplifier-agent/tests/cli/test_single_turn.py
```

Identify `test_run_help_text_documents_skills_dir` and the two `test_run_skills_dir_flag_*_populates_spec` tests around lines 401–446.

**Step 2: Edit the test file**

Replace `test_run_help_text_documents_skills_dir` with:

```python
def test_run_help_text_no_longer_documents_skills_dir(runner):
    """D10: --skills-dir was removed; the flag must not appear in `run --help`.

    Skill-source configuration moves to the `skills:` block in host_config
    (D11) and the $AMPLIFIER_SKILLS_DIR env var (D13).
    """
    result = runner.invoke(cli, ["run", "--help"])
    assert result.exit_code == 0
    assert "--skills-dir" not in result.output
```

Also delete `test_run_skills_dir_flag_single_value_populates_spec`, `test_run_skills_dir_flag_repeated_aggregates_in_order`, and `test_run_skills_dir_flag_absent_default_empty` (the three tests at lines 405–446). Their subject is being removed.

**Step 3: Run test to verify fail**

Run: `pytest tests/cli/test_single_turn.py::test_run_help_text_no_longer_documents_skills_dir -v`
Expected: FAIL — the flag is still in the help text.

**Step 4: Commit (test-only commit)**

```bash
git add tests/cli/test_single_turn.py
git commit -m "test(cli): invert --skills-dir help-text assertion (will fail)

Test now asserts --skills-dir is ABSENT from run --help. The
three populate-spec tests for the flag are removed. The implementation
commit follows.

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

### Task 3.2: Drop `--skills-dir` Click option + `_TurnSpec` field

**Files:**
- Modify: `src/amplifier_agent_cli/modes/single_turn.py`

**Step 1: Identify exact line ranges**

```bash
grep -n "skills-dir\|skill_dirs\|skill_sources\|inject_skill" src/amplifier_agent_cli/modes/single_turn.py
```

Note the line numbers; you'll need to delete:
- The `@click.option("--skills-dir", ...)` decorator block (around line 591).
- The `skill_dirs` parameter in the `run` function signature.
- The `skill_dirs=list(skills_dirs)` arg in the `_TurnSpec(...)` construction (around line 728).
- The `skill_dirs: list[str] = field(default_factory=list)` field on `_TurnSpec` (line 350).
- The `from amplifier_agent_cli.skill_sources import inject_skill_dirs` and `inject_skill_dirs(prepared, spec.skill_dirs)` block (lines 461–465).
- The `# G1 — CLI-provided skill directories...` comment on line 348.

**Step 2: Edit `single_turn.py`**

Make the deletions listed above. Use `edit_file` for each — keep edits small and focused so the diff is reviewable. After each `edit_file` call, re-run `grep -n "skills-dir\|skill_dirs\|inject_skill" src/amplifier_agent_cli/modes/single_turn.py` to confirm progress.

When the grep returns zero matches, you're done.

**Step 3: Run the inverted test to verify pass**

Run: `pytest tests/cli/test_single_turn.py::test_run_help_text_no_longer_documents_skills_dir -v`
Expected: PASS.

Also run the broader single_turn tests to catch regressions from the field removal:
Run: `pytest tests/cli/test_single_turn.py -v`
Expected: all PASS (the three removed tests are already gone from the file per Task 3.1).

**Step 4: Commit**

```bash
git add src/amplifier_agent_cli/modes/single_turn.py
git commit -m "feat(cli)!: drop --skills-dir argv flag

BREAKING: --skills-dir is removed from \`amplifier-agent run\`.
Skill-source configuration moves to:
  - the \`skills:\` block in host_config (D11) for declarative
    install-time configuration, or
  - \$AMPLIFIER_SKILLS_DIR (D13) for per-spawn adapter bridging.

D10 amendment closes the per-turn argv surface for skills the
same way it closed --env-allowlist, --env-extra, and
--allow-protocol-skew: skill paths are stable across the life
of a host install and do not belong in per-turn argv.

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

### Task 3.3: Delete `skill_sources.py` and its tests

**Files:**
- Delete: `src/amplifier_agent_cli/skill_sources.py`
- Delete: `tests/cli/test_skill_sources.py`

**Step 1: Confirm there are no remaining callers**

```bash
grep -rn "skill_sources\|inject_skill_dirs" src/ tests/ --include="*.py"
```

Expected output: empty. If any references remain, hunt them down (likely a stale import) and remove first.

**Step 2: Delete the files**

```bash
git rm src/amplifier_agent_cli/skill_sources.py tests/cli/test_skill_sources.py
```

**Step 3: Run the full CLI test suite to verify pass**

Run: `pytest tests/cli/ -v`
Expected: all PASS, with no collection errors from the deleted test file.

**Step 4: Commit**

```bash
git commit -m "chore(cli)!: delete skill_sources.py — no callers after --skills-dir removal

BREAKING: inject_skill_dirs() is removed. With --skills-dir gone
(Task 3.2), the helper has no caller. Its responsibilities (extending
the tool-skills mount entry's skill source list) move to the
host_config merger per D12.

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

### Task 3.4: Update `tests/bundle/test_bundle_manifest.py` references

**Files:**
- Modify: `tests/bundle/test_bundle_manifest.py`

**Step 1: Read the relevant block**

```bash
sed -n '1,50p' /Users/mpaidiparthy/repos/amplifier-paperclip/amplifier-agent/tests/bundle/test_bundle_manifest.py
```

The docstring and assertion message reference `--skills-dir`. The actual asserted invariant — that `bundle.md` declares the `tool-skills` module — is STILL CORRECT post-D10/D11 (it's the precondition for the `skills:` block to merge into anything).

**Step 2: Edit the docstring + assertion message**

In `tests/bundle/test_bundle_manifest.py`, replace each `--skills-dir` reference with a reference to the `skills:` host_config block:

Find (lines around 4, 35, 43):

```
the `--skills-dir` flag (Task 4 in the G1 skills
```

Replace with:

```
the `skills:` block in host_config (D11
```

Find:

```
Without this declaration, the `--skills-dir` CLI flag plumbs into nothing
```

Replace with:

```
Without this declaration, the `skills:` block in host_config has no
matching mount and triggers `config_no_matching_module` per D7
```

Find:

```
f"Without it the --skills-dir CLI flag plumbs into nothing."
```

Replace with:

```
f"Without it the host_config `skills:` block has no matching module."
```

**Step 3: Run the test to confirm it still passes**

Run: `pytest tests/bundle/test_bundle_manifest.py -v`
Expected: PASS (invariant unchanged; only the comments updated).

**Step 4: Commit**

```bash
git add tests/bundle/test_bundle_manifest.py
git commit -m "docs(test): retarget bundle-manifest test docstrings to skills: block

Invariant unchanged (tool-skills must be declared in bundle.md).
Docstring + assertion message now point to D11's skills: block
instead of the removed --skills-dir flag.

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

### Task 3.5: Extend `config show` to report the `skills` block

**Files:**
- Modify: `src/amplifier_agent_cli/admin/config_show.py`
- Modify: `tests/cli/test_config_show.py`

**Step 1: Write the failing test**

Append to `tests/cli/test_config_show.py`:

```python
def test_config_show_reports_skills_block_after_merge(
    runner: CliRunner, tmp_path: Path
) -> None:
    """D8: config show surfaces the post-merge skills block under the same conventions as the four existing blocks.

    The reported skills.skills list MUST be the post-concatenation result
    (bundle-declared sources first, host_config additions appended) per D12,
    so the operator can confirm both that host additions landed and that
    bundle defaults were not silently dropped.
    """
    cfg = tmp_path / "host.json"
    cfg.write_text(
        json.dumps(
            {
                "skills": {
                    "skills": ["/etc/operator/extra-skills"],
                    "visibility": {"priority": 10},
                }
            }
        )
    )
    env = {
        "AMPLIFIER_AGENT_CONFIG": str(cfg),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "ANTHROPIC_API_KEY": "sk-test",
    }
    result = runner.invoke(cli, ["config", "show"], env=env)
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)

    assert "skills" in parsed
    # The bundle-declared source from bundle.md (Phase 4) is first; the
    # host's additional source is appended.
    assert parsed["skills"]["skills"][-1] == "/etc/operator/extra-skills"
    # Host's priority override won.
    assert parsed["skills"]["visibility"]["priority"] == 10
```

**Step 2: Run test to verify fail**

Run: `pytest tests/cli/test_config_show.py::test_config_show_reports_skills_block_after_merge -v`
Expected: FAIL — `config show` does not report the skills block yet.

**Step 3: Write minimal implementation**

In `src/amplifier_agent_cli/admin/config_show.py`, extend the `config_show` command to:

1. Load the host_config (use the same loader that `single_turn.py` uses; import from `amplifier_agent_lib.config.loader`).
2. Load the prepared bundle, run the merger from `amplifier_agent_lib.config.merger`, then read the `tool-skills` mount entry's post-merge `config` block.
3. Add a `"skills"` key to the JSON payload mirroring the structure used for the other four blocks. Include both `skills` (list) and `visibility` (dict).

Mirror the existing branches for `mcp`, `approval`, `provider`, `allowProtocolSkew` if they exist in this file already; if `config_show.py` has not yet been extended for the other four blocks, that's a sign Phase 3 of the D1–D10 PR is incomplete — pause and check with the design owner before continuing.

Pattern (adjust to existing structure):

```python
# After existing payload keys:
from amplifier_agent_lib.config.loader import load_config_or_none
from amplifier_agent_lib.config.merger import merge_config
from amplifier_agent_lib.bundle.cache import load_and_prepare_cached

parsed_cfg = load_config_or_none()  # honours --config + $AMPLIFIER_AGENT_CONFIG
prepared = await load_and_prepare_cached(aaa_version=__version__)
if parsed_cfg is not None:
    merge_config(prepared, parsed_cfg)
tool_skills_entry = next(
    (e for e in prepared.mount_plan.get("tools", []) if e.get("module") == "tool-skills"),
    None,
)
payload["skills"] = (
    tool_skills_entry.get("config", {}) if tool_skills_entry else None
)
```

(If `config_show` is currently synchronous, wrap the async portion via `asyncio.run`. Match the project's pattern in other admin commands like `admin/prepare.py`.)

**Step 4: Run test to verify pass**

Run: `pytest tests/cli/test_config_show.py::test_config_show_reports_skills_block_after_merge -v`
Expected: PASS.

Run the full `config show` suite:
Run: `pytest tests/cli/test_config_show.py -v`
Expected: all PASS.

**Step 5: Commit**

```bash
git add src/amplifier_agent_cli/admin/config_show.py tests/cli/test_config_show.py
git commit -m "feat(cli): config show reports skills block per D8

The skills block is reported under the same conventions as the
four existing top-level blocks. skills.skills is shown as the
post-merge list (bundle sources + host additions, per D12) so
operators can confirm both that host additions landed and that
bundle defaults were not silently dropped.

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

### Task 3.6: Phase 3 sanity check

```bash
cd /Users/mpaidiparthy/repos/amplifier-paperclip/amplifier-agent
uv run pytest tests/cli/ tests/config/ tests/bundle/ -v
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pyright src/
```

Expected: all green.

Also confirm `--skills-dir` is GONE from the codebase:

```bash
grep -rn "skills-dir\|skill_dirs\|inject_skill\|skill_sources" src/ tests/ --include="*.py"
```

Expected output: empty.

---

## Phase 4 — Bundle + docs (5 tasks)

Update `bundle.md` to source `tool-skills` from the bundle-skills repo subdirectory, declare default sources + visibility via the new `config:` block, and document the breaking change.

**Files this phase touches:**
- Modify: `src/amplifier_agent_lib/bundle/bundle.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/designs/2026-06-01-host-config-layer-revisit.md`

### Task 4.1: Update `bundle.md` `tool-skills` entry

**Files:**
- Modify: `src/amplifier_agent_lib/bundle/bundle.md`

**Step 1: Read the current entry**

```bash
sed -n '80,90p' /Users/mpaidiparthy/repos/amplifier-paperclip/amplifier-agent/src/amplifier_agent_lib/bundle/bundle.md
```

Confirm lines 82–83 look like:

```yaml
  - module: tool-skills
    source: git+https://github.com/microsoft/amplifier-module-tool-skills@main
```

**Step 2: Replace with the new source + config block**

Use `edit_file` to replace those two lines with:

```yaml
  - module: tool-skills
    source: git+https://github.com/microsoft/amplifier-bundle-skills@main#subdirectory=modules/tool-skills
    config:
      skills:
        - git+https://github.com/microsoft/amplifier-bundle-skills@main#subdirectory=skills
        - .amplifier/skills
        - ~/.amplifier/skills
      visibility:
        enabled: true
        inject_role: user
        max_skills_visible: 50
        ephemeral: true
        priority: 20
```

(Match exact 2-space YAML indentation as the surrounding entries.)

**Step 3: Verify bundle.md is still valid YAML**

```bash
cd /Users/mpaidiparthy/repos/amplifier-paperclip/amplifier-agent
uv run python -c "import yaml; yaml.safe_load(open('src/amplifier_agent_lib/bundle/bundle.md').read())"
```

Expected: no exception (silent success).

Also run the bundle-manifest test:
Run: `pytest tests/bundle/test_bundle_manifest.py -v`
Expected: PASS — the test only checks that `tool-skills` is declared; the source URL and config don't affect it.

**Step 4: Commit**

```bash
git add src/amplifier_agent_lib/bundle/bundle.md
git commit -m "feat(bundle): point tool-skills at amplifier-bundle-skills subdir

The standalone amplifier-module-tool-skills repo is deprecated;
the module now ships from amplifier-bundle-skills@main under
modules/tool-skills. The bundle also gains a curated default
\`skills:\` list (one bundle source URL + two filesystem dirs)
and the visibility defaults the upstream module's hook expects.

The sha256-keyed prepared-bundle cache automatically
invalidates on this bundle.md change, so users get the new
module on next run without manual cache reset.

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

### Task 4.2: Cold-prepare smoke test

**Files:** (none modified — verification only)

**Step 1: Clear the prepared-bundle cache**

```bash
rm -rf "${XDG_CACHE_HOME:-$HOME/.cache}/amplifier-agent/prepared/"
```

(This forces a full re-prepare from the updated bundle.md.)

**Step 2: Run prepare**

```bash
cd /Users/mpaidiparthy/repos/amplifier-paperclip/amplifier-agent
uv run amplifier-agent admin prepare 2>&1 | tee /tmp/prepare-output.log
```

Expected:
- Exit code 0.
- Stderr/stdout mentions `tool-skills` being installed from `amplifier-bundle-skills` subdir.
- A new cache directory appears under `~/.cache/amplifier-agent/prepared/<aaa_version>/<sha256>/`.

**Step 3: Verify the cached mount config matches**

```bash
ls "${XDG_CACHE_HOME:-$HOME/.cache}/amplifier-agent/prepared/" -R | head -20
```

Confirm a new prepared directory exists with a fresh sha256 hash.

**Step 4: If anything failed**

- If `tool-skills` install fails with a "subdirectory not found" error: the upstream `amplifier-bundle-skills` repo may not yet have a `modules/tool-skills/` directory. Pause and check with the design owner before continuing.
- If the cache key didn't change: bundle.md edit didn't actually land. Re-check Task 4.1.

**Step 5: No commit (verification step only)**

This task produces no source change; it's a runtime gate. Record the result in your local notes / SCRATCH.md.

### Task 4.3: End-to-end skills discovery smoke test

**Files:** (none modified — verification only)

**Step 1: Default-config invocation**

```bash
cd /tmp
mkdir -p e2e-skills-smoke && cd e2e-skills-smoke
uv run --project /Users/mpaidiparthy/repos/amplifier-paperclip/amplifier-agent \
  amplifier-agent run --no --provider anthropic "list available skills" \
  2>&1 | tee /tmp/default-skills.log
```

Expected:
- Exit code 0.
- The response mentions at least one curated skill name (e.g., one from the bundle-skills repo).

**Step 2: Custom-config invocation with an extra workspace skill dir**

```bash
mkdir -p /tmp/extra-skills
cat > /tmp/extra-skills/HELLO.md <<'EOF'
---
name: hello-smoke
description: A smoke-test skill confirming custom source loaded.
---
# Hello smoke
EOF

cat > /tmp/host.json <<'EOF'
{
  "skills": {
    "skills": ["/tmp/extra-skills"]
  }
}
EOF

uv run --project /Users/mpaidiparthy/repos/amplifier-paperclip/amplifier-agent \
  amplifier-agent run --no --provider anthropic --config /tmp/host.json \
  "list available skills" 2>&1 | tee /tmp/custom-skills.log
```

Expected:
- Exit code 0.
- The response mentions both the curated bundle skills AND `hello-smoke`.

**Step 3: If either invocation fails**

- Read the JSON envelope on stdout for an `error.code`.
- If `config_no_matching_module`: bundle.md doesn't actually declare `tool-skills`. Re-check Task 4.1.
- If the curated skills don't appear: list-concat merge dropped them. Re-check Task 2.2.
- If `hello-smoke` doesn't appear: the host source wasn't merged in. Re-check Task 2.1 + the `config show` output for `/tmp/host.json`.

**Step 4: No commit (verification step only)**

### Task 4.4: CHANGELOG entry

**Files:**
- Modify: `CHANGELOG.md`

**Step 1: Read the current `[Unreleased]` section**

```bash
sed -n '1,20p' /Users/mpaidiparthy/repos/amplifier-paperclip/amplifier-agent/CHANGELOG.md
```

The existing structure has `## [Unreleased]` → `### Breaking changes` → `### Bug fixes`.

**Step 2: Append a new entry under `### Breaking changes`**

Use `edit_file` to insert (after the existing G3 entry, before `### Bug fixes`):

```markdown
- **Engine CLI / Host Config (D10/D11)** Two coordinated breaking changes for skill-source configuration:
  - `amplifier-agent run --skills-dir <path>` is removed. The flag was the G1 per-turn surface for adding skill source directories; D10 closes it as part of the broader argv shrink (alongside `--env-allowlist`, `--env-extra`, `--allow-protocol-skew`). Skill paths are stable across the life of a host install and do not belong in per-turn argv.
  - Host config gains a fifth top-level key, `skills:`, parameterizing the bundle's `tool-skills` mount. Shape is pass-through to the module's own config schema: `skills.skills` (list of source URIs) and `skills.visibility` (visibility-hook dict). Inner sub-keys under `skills.*` are closed against `{skills, visibility}`; further nesting inside `skills.visibility` is pass-through. The `skills.skills` value merges by **concatenation** (bundle-declared sources first, host_config additions appended) — the first list-shaped value in the host_config layer, generalizing per D12.
  - `$AMPLIFIER_SKILLS_DIR` is **preserved** as the per-spawn adapter bridge pattern (D13). The two surfaces (declarative file vs per-spawn env var) serve different audiences and do not duplicate; filesystem discovery precedence inside `tool-skills` is unchanged.
  - Migration: adapters that wired `--skills-dir` per turn migrate to either `$AMPLIFIER_SKILLS_DIR` (the canonical G1 bridge) or a persistent `--config <file>` declaring a `skills.skills` block. Hosts that want curated bundle defaults plus host extensions get them via list-concat with no extra effort.
  - Refs: `docs/designs/2026-06-01-host-config-layer-revisit.md` §D11, §D12, §D13, §D10 amendment.
- **Engine Bundle (D11 precondition)** `tool-skills` now ships from `amplifier-bundle-skills@main#subdirectory=modules/tool-skills` rather than the standalone (deprecated) `amplifier-module-tool-skills@main`. The bundle also declares a curated default `config.skills` list (one bundle source URL + the two conventional filesystem dirs) and the visibility defaults the module's hook expects. The sha256-keyed prepared-bundle cache invalidates automatically; users get the new module on next run with no manual cache reset.
```

**Step 3: Verify the CHANGELOG still renders**

```bash
head -40 /Users/mpaidiparthy/repos/amplifier-paperclip/amplifier-agent/CHANGELOG.md
```

Eyeball the structure: `### Breaking changes` should still be a single section listing G3, D10/D11, and bundle precondition entries in that order.

**Step 4: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): record D10/D11/D12/D13 breaking changes

Documents:
  - --skills-dir removal (D10 amendment)
  - skills: top-level host_config key (D11)
  - skills.skills list-concat merge semantics (D12)
  - \$AMPLIFIER_SKILLS_DIR preserved as adapter bridge (D13)
  - tool-skills source repointed to amplifier-bundle-skills subdir

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

### Task 4.5: Update design doc status line

**Files:**
- Modify: `docs/designs/2026-06-01-host-config-layer-revisit.md`

**Step 1: Identify current commit range**

```bash
cd /Users/mpaidiparthy/repos/amplifier-paperclip/amplifier-agent
git log --oneline | head -25
```

Note the first commit of Phase 1 (Task 1.1) and the most recent commit (Task 4.4). You'll cite the range in the status line.

**Step 2: Edit the status line**

Find line 3 of the design doc:

```
**Status:** DRAFT — pending review.
```

Replace with:

```
**Status:** LOCKED — D11/D12/D13 and D4/D5/D7/D8/D10 amendments implemented (<oldest-sha>..<newest-sha>).
```

Where `<oldest-sha>` is the Task 1.1 short SHA and `<newest-sha>` is the Task 4.4 short SHA.

(This matches the inline `**Status:**` style the host-config design already uses. The G1/G2/G3 designs use `| Status | Locked |` table format; the host-config doc uses inline `**Status:**` style, so preserve its existing convention.)

**Step 3: Verify the rendered status line**

```bash
sed -n '3p' /Users/mpaidiparthy/repos/amplifier-paperclip/amplifier-agent/docs/designs/2026-06-01-host-config-layer-revisit.md
```

Confirm it reads `**Status:** LOCKED — ...`.

**Step 4: Commit**

```bash
git add docs/designs/2026-06-01-host-config-layer-revisit.md
git commit -m "docs(design): mark host-config-layer-revisit LOCKED

D11/D12/D13 and the D4/D5/D7/D8/D10 amendments are implemented.
Status line now records the commit range for the implementation.

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

### Task 4.6: Final integration check

Run the full project test suite + lint + type check:

```bash
cd /Users/mpaidiparthy/repos/amplifier-paperclip/amplifier-agent
uv run pytest -v
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pyright src/
```

Expected: all green.

Then confirm the absence sweep:

```bash
grep -rn "skills-dir\|skill_dirs\|inject_skill\|skill_sources\|amplifier-module-tool-skills" src/ tests/ docs/ --include="*.py" --include="*.md"
```

Expected output: only matches inside `CHANGELOG.md` (describing the removal) and `docs/designs/2026-06-01-host-config-layer-revisit.md` (describing the migration). No matches in code or test files.

Implementation is complete. Hand off to `/finish` for review + PR.

---

## Plan summary

- **Phase 1** (6 tasks): config loader gains `skills` allowlist + inner-shape validation.
- **Phase 2** (6 tasks): merger gains `skills` dispatch + list-concat + dict-overlay + missing-mount error.
- **Phase 3** (6 tasks): `--skills-dir` and `skill_sources.py` removed; `config show` extended.
- **Phase 4** (6 tasks): `bundle.md` repointed + curated config block; smoke tests; CHANGELOG; design doc status.

**Total: 24 tasks** (4 of which are pure verification/sanity gates without a code commit). All in scope; nothing speculative beyond the four phases.

**Out of scope reminders:**
- No `git push`, `gh pr create`, or merge operations — those are `/finish` mode work.
- No paperclip adapter implementation. Adapter migration happens in a separate plan.
- No new Python dependencies. Work with what's in `pyproject.toml`.

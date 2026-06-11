# Model Overrides and Discovery Implementation Plan

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Add `--model`/`--effort` overrides to `amplifier-agent run` and a new `amplifier-agent models list --provider X` discovery subcommand.

**Architecture:** Two independent change sets on one branch. Change Set 1 threads a model/effort override from the TypeScript wrapper through the Python `run` command into the provider mount config. Change Set 2 adds a new admin command that loads a provider module, instantiates the provider, calls its async `list_models()`, and renders the result as JSON or a table. The two sets share nothing but the repo.

**Tech Stack:** Python 3.12 (Click CLI, pytest, pyright, ruff), TypeScript (vitest). No new dependencies.

**Design reference:** `docs/designs/model-overrides-and-discovery.md` (commit `a7426a6`). All decisions there are locked — do not re-litigate scope.

---

## Audience & House Rules (read first)

You are skilled at coding but you know nothing about this codebase. Follow these literally:

1. **All paths in this plan are absolute** and point into the `amplifier-agent` repo at `/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent`. The shell's current working directory may be a *different* repo (`amplifier-app-nanoclaw`). **Every command in this plan starts by `cd`-ing into the amplifier-agent repo.** Never run git commands without `cd`-ing first.
2. **TDD is non-negotiable.** Each task is: write failing test → run it and SEE it fail for the right reason → write minimal code → run it and SEE it pass → commit. Do not write implementation before the test. Do not skip the "watch it fail" step.
3. **One TDD cycle = one commit.** Use the exact conventional-commit messages given.
4. **Python tests run with `uv run pytest`.** TypeScript tests run with `npm test` (vitest) from `wrappers/typescript`.
5. **Line length is 120 (ruff), `E501` is ignored.** Target Python 3.12. After any Python edit, the implementation step tells you to run `python_check` — do it.
6. **Do NOT touch nanoclaw (`amplifier-app-nanoclaw`) or any provider module.** They are explicitly out of scope (see final section).

### Commands you will reuse

```bash
# Python: run one test (always cd first)
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && uv run pytest <path>::<test> -v

# Python: run a whole file
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && uv run pytest <path> -v

# TypeScript: run the argv-builder test file
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/wrappers/typescript && npx vitest run test/argv-builder.test.ts
```

---

## Task 0: Verify clean baseline and create the feature branch

**Files:** none (git only)

**Step 1: Confirm repo state**

Run:
```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && git status --short && git log --oneline -1
```
Expected: clean working tree; HEAD is `a7426a6 docs(designs): add model overrides and discovery design`.

If HEAD is not `a7426a6`, run `git log --oneline -5`, find the `docs(designs): add model overrides and discovery design` commit, and `git checkout` it before branching (the design doc must be in your branch's history).

**Step 2: Create the feature branch from the design commit**

The design doc was committed on branch `chore/dev-bundle`. Cut the feature branch from current HEAD (which contains the design):
```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && git checkout -b feat/model-overrides-and-discovery
```
Expected: `Switched to a new branch 'feat/model-overrides-and-discovery'`.

**Step 3: Confirm the green baseline**

Run the two suites we will be extending so you know they pass *before* you change anything:
```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && uv run pytest tests/cli/test_provider_sources.py tests/cli/test_single_turn.py -q
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/wrappers/typescript && npx vitest run test/argv-builder.test.ts
```
Expected: all pass. If anything fails here, STOP and report — the baseline is broken and that is not your change.

No commit for this task.

---

# Change Set 1 — Override path (`run --model` / `--effort`)

Bottom-up: wrapper → provider_sources → single_turn. Each layer is integrated before its consumer depends on it.

---

## Task 1: argv-builder emits `--model` when `modelOverride` is set

**Files:**
- Modify: `/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/wrappers/typescript/src/argv-builder.ts` (interface at `:13-40`, function at `:48-88`)
- Test: `/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/wrappers/typescript/test/argv-builder.test.ts`

**Step 1: Write the failing test**

Add this test inside the `describe("assembleArgv", ...)` block in `test/argv-builder.test.ts` (after case `(iv)`, before the closing `});`):

```ts
  it("(v) --model emitted when modelOverride set", () => {
    const input: AssembleArgvInput = {
      sessionId: "sid",
      prompt: "hello",
      protocolVersion: "0.1.0",
      modelOverride: "claude-sonnet-4-5",
    };
    const argv = assembleArgv(input);
    const idx = argv.indexOf("--model");
    expect(idx).toBeGreaterThanOrEqual(0);
    expect(argv[idx + 1]).toBe("claude-sonnet-4-5");
  });

  it("(v-baseline) --model absent when modelOverride unset", () => {
    const input: AssembleArgvInput = {
      sessionId: "sid",
      prompt: "hello",
      protocolVersion: "0.1.0",
    };
    const argv = assembleArgv(input);
    expect(argv).not.toContain("--model");
  });
```

**Step 2: Run test to verify it fails**

Run:
```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/wrappers/typescript && npx vitest run test/argv-builder.test.ts
```
Expected: the `(v)` test FAILS. It will be a TypeScript type error (`modelOverride` is not a known property of `AssembleArgvInput`) or a failed assertion. Either way it must fail.

**Step 3: Write minimal implementation**

In `argv-builder.ts`, add the field to the `AssembleArgvInput` interface immediately after the `providerOverride` field (`:25`):

```ts
  /** Provider override; emits `--provider <providerOverride>`. */
  providerOverride?: string;
  /** Model override; emits `--model <modelOverride>`. */
  modelOverride?: string;
```

Then in `assembleArgv()`, add the emission immediately after the existing `--provider` block (`:58-60`):

```ts
  if (input.providerOverride !== undefined) {
    argv.push("--provider", input.providerOverride);
  }
  if (input.modelOverride !== undefined) {
    argv.push("--model", input.modelOverride);
  }
```

**Step 4: Run test to verify it passes**

Run:
```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/wrappers/typescript && npx vitest run test/argv-builder.test.ts
```
Expected: all tests PASS, including `(v)` and `(v-baseline)`.

**Step 5: Commit**

```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && git add wrappers/typescript/src/argv-builder.ts wrappers/typescript/test/argv-builder.test.ts && git commit -m "feat(wrapper): emit --model when modelOverride set

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

---

## Task 2: argv-builder emits `--effort` when `effortOverride` is set

**Files:**
- Modify: `/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/wrappers/typescript/src/argv-builder.ts`
- Test: `/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/wrappers/typescript/test/argv-builder.test.ts`

**Step 1: Write the failing test**

Add inside the `describe` block, after the Task 1 cases:

```ts
  it("(vi) --effort emitted when effortOverride set", () => {
    const input: AssembleArgvInput = {
      sessionId: "sid",
      prompt: "hello",
      protocolVersion: "0.1.0",
      effortOverride: "high",
    };
    const argv = assembleArgv(input);
    const idx = argv.indexOf("--effort");
    expect(idx).toBeGreaterThanOrEqual(0);
    expect(argv[idx + 1]).toBe("high");
  });

  it("(vi-baseline) --effort absent when effortOverride unset", () => {
    const input: AssembleArgvInput = {
      sessionId: "sid",
      prompt: "hello",
      protocolVersion: "0.1.0",
    };
    const argv = assembleArgv(input);
    expect(argv).not.toContain("--effort");
  });
```

**Step 2: Run test to verify it fails**

Run:
```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/wrappers/typescript && npx vitest run test/argv-builder.test.ts
```
Expected: the `(vi)` test FAILS (unknown property `effortOverride` or failed assertion).

**Step 3: Write minimal implementation**

Add the field to `AssembleArgvInput` immediately after `modelOverride`:

```ts
  /** Model override; emits `--model <modelOverride>`. */
  modelOverride?: string;
  /** Effort/reasoning level override; emits `--effort <effortOverride>`. */
  effortOverride?: string;
```

Add the emission in `assembleArgv()` immediately after the `--model` block from Task 1:

```ts
  if (input.modelOverride !== undefined) {
    argv.push("--model", input.modelOverride);
  }
  if (input.effortOverride !== undefined) {
    argv.push("--effort", input.effortOverride);
  }
```

**Step 4: Run test to verify it passes**

Run:
```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/wrappers/typescript && npx vitest run test/argv-builder.test.ts
```
Expected: all tests PASS.

> Note (resolved design question): there is no hand-maintained `argv-builder.d.ts`. Declarations are generated by `tsc` (`npm run build`). No manual `.d.ts` edit is needed. You may optionally run `npm run typecheck` to confirm the source still type-checks.

**Step 5: Commit**

```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && git add wrappers/typescript/src/argv-builder.ts wrappers/typescript/test/argv-builder.test.ts && git commit -m "feat(wrapper): emit --effort when effortOverride set

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

---

## Task 3: `build_provider_entry` honors `model_override` and `effort_override`

**Files:**
- Modify: `/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/src/amplifier_agent_cli/provider_sources.py` (function at `:111`, return dict at `:158-166`, `default_model` line at `:163`)
- Test: `/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/tests/cli/test_provider_sources.py`

**Step 1: Write the failing test**

Append these tests to the end of `tests/cli/test_provider_sources.py`:

```python
# ---------------------------------------------------------------------------
# build_provider_entry — model / effort overrides
# ---------------------------------------------------------------------------


def test_build_provider_entry_model_override_replaces_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """A model_override replaces the catalog default_model in config."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    from amplifier_agent_cli.provider_sources import build_provider_entry

    entry = build_provider_entry("anthropic", model_override="claude-sonnet-4-5")
    assert entry["config"]["default_model"] == "claude-sonnet-4-5"


def test_build_provider_entry_no_model_override_keeps_catalog_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without an override, the catalog default_model is unchanged."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    from amplifier_agent_cli.provider_sources import PROVIDER_CATALOG, build_provider_entry

    entry = build_provider_entry("anthropic")
    assert entry["config"]["default_model"] == PROVIDER_CATALOG["anthropic"]["default_model"]


def test_build_provider_entry_effort_override_lands_in_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """An effort_override is added to config under the 'effort' key."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    from amplifier_agent_cli.provider_sources import build_provider_entry

    entry = build_provider_entry("anthropic", effort_override="high")
    assert entry["config"]["effort"] == "high"


def test_build_provider_entry_no_effort_override_omits_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without an effort override, 'effort' is not present in config."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    from amplifier_agent_cli.provider_sources import build_provider_entry

    entry = build_provider_entry("anthropic")
    assert "effort" not in entry["config"]
```

**Step 2: Run test to verify it fails**

Run:
```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && uv run pytest tests/cli/test_provider_sources.py -k "override" -v
```
Expected: FAIL with `TypeError: build_provider_entry() got an unexpected keyword argument 'model_override'`.

**Step 3: Write minimal implementation**

In `provider_sources.py`, change the `build_provider_entry` signature (`:111`) and its return dict (`:158-166`). Replace:

```python
def build_provider_entry(provider_name: str) -> dict[str, Any]:
```
with:
```python
def build_provider_entry(
    provider_name: str,
    model_override: str | None = None,
    effort_override: str | None = None,
) -> dict[str, Any]:
```

Then replace the `return { ... }` block (`:158-166`) with:

```python
    config: dict[str, Any] = {
        "api_key": api_key,
        "default_model": model_override or entry["default_model"],
        "priority": 1,
    }
    if effort_override is not None:
        config["effort"] = effort_override

    return {
        "module": entry["module"],
        "source": entry["source"],
        "config": config,
    }
```

Also add a short note to the docstring's `Args:` section (optional but preferred):
```python
        provider_name: One of ``PROVIDER_CATALOG`` keys (e.g. ``"anthropic"``).
        model_override: If set, replaces the catalog ``default_model``.
        effort_override: If set, added to ``config`` under ``"effort"``.
```

**Step 4: Run test to verify it passes**

Run:
```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && uv run pytest tests/cli/test_provider_sources.py -v
```
Expected: ALL tests in the file PASS (old ones still green, four new ones green).

Then run the quality gate:
```python
python_check(paths=["/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/src/amplifier_agent_cli/provider_sources.py"])
```
Expected: success (no errors).

**Step 5: Commit**

```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && git add src/amplifier_agent_cli/provider_sources.py tests/cli/test_provider_sources.py && git commit -m "feat(cli): build_provider_entry honors model and effort overrides

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

---

## Task 4: `inject_provider` forwards `model_override` and `effort_override`

**Files:**
- Modify: `/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/src/amplifier_agent_cli/provider_sources.py` (function at `:169`, body at `:186-188`)
- Test: `/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/tests/cli/test_provider_sources.py`

**Step 1: Write the failing test**

Append to `tests/cli/test_provider_sources.py`:

```python
def test_inject_provider_forwards_model_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """inject_provider threads model_override into the written entry."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    from amplifier_agent_cli.provider_sources import inject_provider

    prepared = _stub_prepared()
    inject_provider(prepared, "anthropic", model_override="claude-sonnet-4-5", effort_override="high")

    config = prepared.mount_plan["providers"][0]["config"]
    assert config["default_model"] == "claude-sonnet-4-5"
    assert config["effort"] == "high"
```

**Step 2: Run test to verify it fails**

Run:
```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && uv run pytest tests/cli/test_provider_sources.py::test_inject_provider_forwards_model_override -v
```
Expected: FAIL with `TypeError: inject_provider() got an unexpected keyword argument 'model_override'`.

**Step 3: Write minimal implementation**

In `provider_sources.py`, change the `inject_provider` signature (`:169`) and the entry-building call (`:188`). Replace:

```python
def inject_provider(prepared: Any, provider_name: str) -> None:
```
with:
```python
def inject_provider(
    prepared: Any,
    provider_name: str,
    model_override: str | None = None,
    effort_override: str | None = None,
) -> None:
```

Replace the final line (`:188`):
```python
    prepared.mount_plan["providers"] = [build_provider_entry(provider_name)]
```
with:
```python
    prepared.mount_plan["providers"] = [
        build_provider_entry(
            provider_name,
            model_override=model_override,
            effort_override=effort_override,
        )
    ]
```

**Step 4: Run test to verify it passes**

Run:
```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && uv run pytest tests/cli/test_provider_sources.py -v
```
Expected: ALL tests PASS.

```python
python_check(paths=["/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/src/amplifier_agent_cli/provider_sources.py"])
```
Expected: success.

**Step 5: Commit**

```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && git add src/amplifier_agent_cli/provider_sources.py tests/cli/test_provider_sources.py && git commit -m "feat(cli): inject_provider forwards model and effort overrides

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

---

## Task 5: `run` accepts `--model`/`--effort` and threads them to `inject_provider`

**Files:**
- Modify: `/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/src/amplifier_agent_cli/modes/single_turn.py`
  - `_TurnSpec` dataclass at `:308-328`
  - `_execute_turn` `inject_provider` call at `:345`
  - `run` Click options near `:404`, signature near `:458-479`, `_TurnSpec(...)` construction at `:561-572`
- Test: `/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/tests/cli/test_single_turn.py`

This task wires the flags through four edit points. We test it at the unit level first (the flags parse and reach `_TurnSpec`); Task 6 is the full engine-boot integration test.

**Step 1: Write the failing test**

Append to `tests/cli/test_single_turn.py`. This test patches `_execute_turn` to capture the `_TurnSpec` it receives, so we verify the flags flow into the spec without booting an engine:

```python
def test_run_threads_model_and_effort_into_turn_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    """`run --model X --effort Y` populates _TurnSpec.model_override / effort_override."""
    import amplifier_agent_cli.modes.single_turn as st
    from click.testing import CliRunner

    from amplifier_agent_cli.__main__ import cli

    captured: dict[str, object] = {}

    async def fake_execute_turn(spec: object) -> dict[str, object]:
        captured["spec"] = spec
        return {"reply": "ok", "turnId": "turn-1"}

    monkeypatch.setattr(st, "_execute_turn", fake_execute_turn)
    # Avoid touching the real audit/persistence layer.
    monkeypatch.setattr(st, "_write_audit", lambda **kwargs: None)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "run",
            "--provider", "anthropic",
            "--model", "claude-sonnet-4-5",
            "--effort", "high",
            "--output", "text",
            "hello",
        ],
        env={"ANTHROPIC_API_KEY": "sk-ant-test"},
    )

    assert result.exit_code == 0, (result.stdout, result.stderr)
    spec = captured["spec"]
    assert spec.model_override == "claude-sonnet-4-5"
    assert spec.effort_override == "high"
```

> If `tests/cli/test_single_turn.py` already imports `CliRunner`/`cli` at module top, you may drop the inner imports and reuse the existing ones. Match the file's existing import style.

**Step 2: Run test to verify it fails**

Run:
```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && uv run pytest tests/cli/test_single_turn.py::test_run_threads_model_and_effort_into_turn_spec -v
```
Expected: FAIL. Most likely `no such option: --model` (Click rejects the unknown flag, exit 2), or `AttributeError: '_TurnSpec' object has no attribute 'model_override'`.

**Step 3: Write minimal implementation (four edits, all in `single_turn.py`)**

**Edit 3a — add fields to `_TurnSpec`** (dataclass at `:308-328`). Add after the `mcp_config_path: str | None = None` field (`:328`), keeping the existing fields and appending the two new defaulted ones at the end so dataclass field-ordering stays valid:

```python
    provider: str  # detected provider short-name (e.g. 'anthropic')
    allow_protocol_skew: bool = False
    mcp_config_path: str | None = None
    model_override: str | None = None
    effort_override: str | None = None
```

**Edit 3b — forward to `inject_provider`** in `_execute_turn` (`:345`). Replace:
```python
    inject_provider(prepared, spec.provider)
```
with:
```python
    inject_provider(
        prepared,
        spec.provider,
        model_override=spec.model_override,
        effort_override=spec.effort_override,
    )
```

**Edit 3c — add Click options** to the `run` command. Add immediately after the existing `--provider` option (`:404`):
```python
@click.option("--provider", "provider_override", default=None, help="Override provider detection (e.g. anthropic).")
@click.option("--model", "model_override", default=None, help="Override the default model for the selected provider.")
@click.option("--effort", "effort_override", default=None, help="Override the reasoning/effort level for the selected provider.")
```

Add the two parameters to the `run` function signature, immediately after `provider_override: str | None,` (`:463`):
```python
    provider_override: str | None,
    model_override: str | None,
    effort_override: str | None,
```

**Edit 3d — pass to `_TurnSpec(...)`** at the construction site (`:561-572`). Add the two keyword args before the closing paren:
```python
    spec = _TurnSpec(
        prompt=prompt,
        session_id=session_id,
        resume=resume,
        fresh=fresh,
        cwd=cwd,
        approval=approval,
        display=display,
        provider=provider_name,
        allow_protocol_skew=allow_protocol_skew or bool(os.environ.get("AMPLIFIER_AGENT_ALLOW_PROTOCOL_SKEW")),
        mcp_config_path=mcp_config_path,
        model_override=model_override,
        effort_override=effort_override,
    )
```

**Step 4: Run test to verify it passes**

Run:
```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && uv run pytest tests/cli/test_single_turn.py -v
```
Expected: ALL tests PASS (the new one plus the pre-existing ones).

```python
python_check(paths=["/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/src/amplifier_agent_cli/modes/single_turn.py"])
```
Expected: success.

**Step 5: Commit**

```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && git add src/amplifier_agent_cli/modes/single_turn.py tests/cli/test_single_turn.py && git commit -m "feat(cli): add --model and --effort overrides to run command

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

---

## Task 6: Engine-boot integration test — override lands in the mount plan

**Files:**
- Test only: `/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/tests/cli/test_single_turn.py`

This is the Change Set 1 safety net. It exercises the *real* `_execute_turn` → real `inject_provider` path, faking only the engine boot (`load_and_prepare_cached`, `Engine`, `make_turn_handler`), and asserts the override reaches `prepared.mount_plan["providers"]`.

**Step 1: Write the failing test**

Append to `tests/cli/test_single_turn.py`:

```python
def test_run_override_lands_in_mount_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end (engine faked): --model/--effort reach mount_plan['providers'][0]['config']."""
    from types import SimpleNamespace

    import amplifier_agent_cli.modes.single_turn as st
    from click.testing import CliRunner

    from amplifier_agent_cli.__main__ import cli

    captured: dict[str, object] = {}

    async def fake_prepare(*, aaa_version: str) -> object:
        prepared = SimpleNamespace(mount_plan={})
        captured["prepared"] = prepared
        return prepared

    class FakeEngine:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def boot(self, params: dict, *, bundle_override: object = None) -> None:
            pass

        async def submit_turn(self, params: dict) -> dict:
            return {"reply": "ok", "turnId": "turn-1"}

        async def shutdown(self) -> None:
            pass

    monkeypatch.setattr(st, "load_and_prepare_cached", fake_prepare)
    monkeypatch.setattr(st, "Engine", FakeEngine)
    monkeypatch.setattr(st, "make_turn_handler", lambda prepared, **kwargs: object())
    monkeypatch.setattr(st, "_write_audit", lambda **kwargs: None)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "run",
            "--provider", "anthropic",
            "--model", "claude-sonnet-4-5",
            "--effort", "high",
            "--output", "text",
            "hello",
        ],
        env={"ANTHROPIC_API_KEY": "sk-ant-test"},
    )

    assert result.exit_code == 0, (result.stdout, result.stderr)
    prepared = captured["prepared"]
    config = prepared.mount_plan["providers"][0]["config"]
    assert config["default_model"] == "claude-sonnet-4-5"
    assert config["effort"] == "high"
```

**Step 2: Run test to verify it fails (and tests the right thing)**

Since Task 5 already implemented the threading, this test may PASS immediately — which violates the "watch it fail" rule. To prove the test actually detects the behavior, temporarily revert Edit 3b from Task 5 (the `inject_provider` forwarding), run the test, and confirm it FAILS:

```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && uv run pytest tests/cli/test_single_turn.py::test_run_override_lands_in_mount_plan -v
```
Expected (with Edit 3b reverted): FAIL — `config["default_model"]` is the catalog default `claude-opus-4-5`, not `claude-sonnet-4-5`; or `effort` raises `KeyError`.

Then **restore Edit 3b** (re-apply the forwarding) before Step 4.

**Step 3: Write minimal implementation**

None — Task 5 already implemented the production code. This task only adds the integration test. (Step 2 proved the test detects regressions.)

**Step 4: Run test to verify it passes**

Run:
```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && uv run pytest tests/cli/test_single_turn.py -v
```
Expected: ALL tests PASS, including `test_run_override_lands_in_mount_plan`.

**Step 5: Commit**

```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && git add tests/cli/test_single_turn.py && git commit -m "test(cli): engine-boot integration test for run --model/--effort override

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

**Change Set 1 is complete.** Run the full Change Set 1 surface once more to confirm green:
```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && uv run pytest tests/cli/test_provider_sources.py tests/cli/test_single_turn.py -q
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/wrappers/typescript && npx vitest run test/argv-builder.test.ts
```

---

# Change Set 2 — Discovery path (`models list`)

New module `admin/models.py`. We build it incrementally: skeleton + registration first (so the CLI wires up), then port the provider-loading helpers, then the async caller, then rendering, then error/empty-list handling.

**Reference implementation to port:** `amplifier_app_cli/provider_loader.py`. A copy is readable at:
`/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-bundle-evaluation/.venv/lib/python3.13/site-packages/amplifier_app_cli/provider_loader.py`
Read it before Tasks 8–11. Port `_get_provider_module_name`, `_load_provider_module`, `load_provider_class`, `_try_instantiate_provider`, and a `list_models` caller analogous to `get_provider_models`. Copy docstrings and structure faithfully. **Do NOT port `get_provider_info` or `_resolve_env_placeholder`** — out of scope.

---

## Task 7: `models list` skeleton + registration (CLI wiring)

**Files:**
- Create: `/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/src/amplifier_agent_cli/admin/models.py`
- Modify: `/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/src/amplifier_agent_cli/__main__.py` (imports `:24-30`, registrations `:42-48`)
- Test: `/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/tests/cli/test_admin_models.py` (CREATE)

> Naming note (resolved design question): admin tests are flat — `tests/cli/test_admin_models.py`, matching `tests/cli/test_admin_prepare.py` and `tests/cli/test_admin_verify.py`. Do NOT create a `tests/admin/` subdirectory.

> Registration note: the design lists registration as a late step, but the wiring test below *requires* the group to be registered. So registration happens here in Task 7. There is no separate registration task later.

**Step 1: Write the failing test**

Create `/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/tests/cli/test_admin_models.py`:

```python
"""Tests for the `amplifier-agent models list` admin command.

Covers wiring, provider-loading port, JSON/table rendering, and the
error/empty-list contract. All tests mock the provider via load_provider_class
— no real-API calls, matching the design's unit-only test strategy.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from amplifier_agent_cli.__main__ import cli


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def test_models_list_is_registered(runner: CliRunner) -> None:
    """`models list --help` is reachable (group registered on the root CLI)."""
    result = runner.invoke(cli, ["models", "list", "--help"])
    assert result.exit_code == 0, result.output
    assert "--provider" in result.output
```

**Step 2: Run test to verify it fails**

Run:
```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && uv run pytest tests/cli/test_admin_models.py::test_models_list_is_registered -v
```
Expected: FAIL — exit code 2 with `No such command 'models'.` (group not registered yet).

**Step 3: Write minimal implementation**

Create `src/amplifier_agent_cli/admin/models.py`:

```python
"""Admin command: models list — enumerate the models a provider offers.

Loads the provider module, instantiates the provider class, and calls its
``list_models()`` to discover available models. Emits the result as an
envelope-wrapped JSON document or a human-readable table.

No fallback: ``list_models()`` is a list API. If it raises (e.g. missing API
key, network error) the error propagates and the command exits 2. The consumer
(nanoclaw, a human) decides what to do — there is no catalog fallback, no retry,
no ``--source`` flag.

Provider-loading logic is ported from ``amplifier_app_cli.provider_loader``
(``load_provider_class``, ``_try_instantiate_provider`` and helpers), matching
the locked "no fallback" decision: exceptions propagate.
"""

from __future__ import annotations

import click


@click.group(name="models")
def models_group() -> None:
    """Inspect models offered by a provider."""


@models_group.command(name="list")
@click.option("--provider", "provider_name", required=True, help="Provider short-name (e.g. anthropic).")
@click.option(
    "--output",
    "output_mode",
    type=click.Choice(["auto", "json", "table"], case_sensitive=False),
    default="auto",
    show_default=True,
    help="Output format. 'auto' = table on a TTY, json when piped.",
)
@click.option(
    "--timeout",
    "timeout_seconds",
    type=float,
    default=15.0,
    show_default=True,
    help="Timeout in seconds for the live list_models() call.",
)
def models_list(provider_name: str, output_mode: str, timeout_seconds: float) -> None:
    """List the models a provider offers (calls the provider's live list API)."""
    raise click.ClickException("not implemented")
```

In `__main__.py`, add the import alongside the other admin imports (after `:29`):
```python
from amplifier_agent_cli.admin.models import models_group as _models_group
```
And register it alongside the others (after `:48`):
```python
cli.add_command(_models_group, name="models")
```

**Step 4: Run test to verify it passes**

Run:
```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && uv run pytest tests/cli/test_admin_models.py::test_models_list_is_registered -v
```
Expected: PASS.

```python
python_check(paths=["/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/src/amplifier_agent_cli/admin/models.py", "/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/src/amplifier_agent_cli/__main__.py"])
```
Expected: success.

**Step 5: Commit**

```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && git add src/amplifier_agent_cli/admin/models.py src/amplifier_agent_cli/__main__.py tests/cli/test_admin_models.py && git commit -m "feat(cli): scaffold and register models list subcommand

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

---

## Task 8: Port `_get_provider_module_name` and `_load_provider_module`

**Files:**
- Modify: `/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/src/amplifier_agent_cli/admin/models.py`
- Test: `/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/tests/cli/test_admin_models.py`

**Step 1: Write the failing test**

Append to `tests/cli/test_admin_models.py`:

```python
def test_get_provider_module_name_normalizes_prefix() -> None:
    from amplifier_agent_cli.admin.models import _get_provider_module_name

    assert _get_provider_module_name("anthropic") == "amplifier_module_provider_anthropic"
    assert _get_provider_module_name("provider-anthropic") == "amplifier_module_provider_anthropic"
    assert _get_provider_module_name("azure-openai") == "amplifier_module_provider_azure_openai"
```

**Step 2: Run test to verify it fails**

Run:
```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && uv run pytest tests/cli/test_admin_models.py::test_get_provider_module_name_normalizes_prefix -v
```
Expected: FAIL — `ImportError: cannot import name '_get_provider_module_name'`.

**Step 3: Write minimal implementation**

Port the two helpers from `provider_loader.py:21-74`. Update the top of `models.py` (extend imports) and insert the functions before `models_group`. Replace the import block at the top with:

```python
from __future__ import annotations

import importlib
import importlib.metadata
import logging
from typing import Any

import click

logger = logging.getLogger(__name__)


def _get_provider_module_name(provider_id: str) -> str:
    """Convert a provider ID to its Python module name.

    Ported from amplifier_app_cli.provider_loader._get_provider_module_name.

    Args:
        provider_id: Provider ID (e.g., "provider-anthropic" or "anthropic").

    Returns:
        Python module name (e.g., "amplifier_module_provider_anthropic").
    """
    if provider_id.startswith("provider-"):
        provider_id = provider_id[9:]
    return f"amplifier_module_provider_{provider_id.replace('-', '_')}"


def _load_provider_module(provider_id: str) -> Any:
    """Load a provider module (entry points first, then direct import).

    Ported from amplifier_app_cli.provider_loader._load_provider_module.

    Raises:
        ImportError: If the module cannot be loaded.
    """
    module_id = provider_id if provider_id.startswith("provider-") else f"provider-{provider_id}"

    try:
        eps = importlib.metadata.entry_points(group="amplifier.modules")
        for ep in eps:
            if ep.name == module_id:
                mount_fn = ep.load()
                return importlib.import_module(mount_fn.__module__.rsplit(".", 1)[0])
    except Exception as e:  # noqa: BLE001 - entry-point lookup is best-effort
        logger.debug(f"Entry point lookup failed for {module_id}: {e}")

    module_name = _get_provider_module_name(provider_id)
    try:
        return importlib.import_module(module_name)
    except ImportError as e:
        raise ImportError(f"Could not load provider module '{provider_id}': {e}") from e
```

**Step 4: Run test to verify it passes**

Run:
```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && uv run pytest tests/cli/test_admin_models.py -v
```
Expected: PASS.

```python
python_check(paths=["/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/src/amplifier_agent_cli/admin/models.py"])
```
Expected: success.

**Step 5: Commit**

```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && git add src/amplifier_agent_cli/admin/models.py tests/cli/test_admin_models.py && git commit -m "feat(cli): port provider module-name and module-load helpers

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

---

## Task 9: Port `load_provider_class`

**Files:**
- Modify: `/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/src/amplifier_agent_cli/admin/models.py`
- Test: `/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/tests/cli/test_admin_models.py`

**Step 1: Write the failing test**

Append to `tests/cli/test_admin_models.py`:

```python
def test_load_provider_class_returns_none_for_unloadable(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the module can't be loaded, load_provider_class returns None (no raise)."""
    import amplifier_agent_cli.admin.models as models_mod

    def _boom(provider_id: str) -> object:
        raise ImportError("nope")

    monkeypatch.setattr(models_mod, "_load_provider_module", _boom)
    assert models_mod.load_provider_class("anthropic") is None


def test_load_provider_class_finds_by_convention(monkeypatch: pytest.MonkeyPatch) -> None:
    """A module exposing {Name}Provider is resolved by convention."""
    import types

    import amplifier_agent_cli.admin.models as models_mod

    fake_module = types.ModuleType("fake_provider_mod")

    class AnthropicProvider:  # test double
        pass

    fake_module.AnthropicProvider = AnthropicProvider  # type: ignore[attr-defined]
    monkeypatch.setattr(models_mod, "_load_provider_module", lambda provider_id: fake_module)

    assert models_mod.load_provider_class("anthropic") is AnthropicProvider
```

**Step 2: Run test to verify it fails**

Run:
```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && uv run pytest tests/cli/test_admin_models.py -k "load_provider_class" -v
```
Expected: FAIL — `ImportError: cannot import name 'load_provider_class'` / `AttributeError`.

**Step 3: Write minimal implementation**

Port `load_provider_class` from `provider_loader.py:77-126` (adjust only formatting for line-length 120). Add to `models.py` after `_load_provider_module`:

```python
def load_provider_class(provider_id: str) -> type | None:
    """Load a provider class for configuration purposes.

    Lightweight load that doesn't require a full coordinator. Returns the
    provider class (e.g. AnthropicProvider) that can be instantiated to query
    list_models(). Ported from amplifier_app_cli.provider_loader.

    Returns:
        Provider class if found, None otherwise.
    """
    try:
        module = _load_provider_module(provider_id)

        provider_name = provider_id.replace("provider-", "") if provider_id.startswith("provider-") else provider_id
        class_name = f"{provider_name.title().replace('-', '')}Provider"

        if hasattr(module, class_name):
            return getattr(module, class_name)

        if hasattr(module, "__all__"):
            for name in module.__all__:
                if name.endswith("Provider"):
                    cls = getattr(module, name, None)
                    if cls and isinstance(cls, type):
                        return cls

        for name in dir(module):
            if name.endswith("Provider") and not name.startswith("_"):
                cls = getattr(module, name, None)
                if cls and isinstance(cls, type):
                    return cls

        logger.warning(f"No provider class found in module for '{provider_id}'")
        return None

    except ImportError as e:
        logger.debug(f"Could not load provider class for '{provider_id}': {e}")
        return None
```

**Step 4: Run test to verify it passes**

Run:
```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && uv run pytest tests/cli/test_admin_models.py -v
```
Expected: PASS.

```python
python_check(paths=["/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/src/amplifier_agent_cli/admin/models.py"])
```
Expected: success.

**Step 5: Commit**

```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && git add src/amplifier_agent_cli/admin/models.py tests/cli/test_admin_models.py && git commit -m "feat(cli): port load_provider_class convention resolver

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

---

## Task 10: Port `_try_instantiate_provider`

**Files:**
- Modify: `/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/src/amplifier_agent_cli/admin/models.py`
- Test: `/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/tests/cli/test_admin_models.py`

**Step 1: Write the failing test**

Append to `tests/cli/test_admin_models.py`:

```python
def test_try_instantiate_provider_standard_signature() -> None:
    """A provider with (api_key, config) instantiates via approach 1."""
    from amplifier_agent_cli.admin.models import _try_instantiate_provider

    class StdProvider:
        def __init__(self, api_key: str, config: dict) -> None:
            self.api_key = api_key

    inst = _try_instantiate_provider(StdProvider)
    assert isinstance(inst, StdProvider)


def test_try_instantiate_provider_returns_none_when_all_fail() -> None:
    """A class whose constructor always raises yields None."""
    from amplifier_agent_cli.admin.models import _try_instantiate_provider

    class Unbuildable:
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise ValueError("cannot build")

    assert _try_instantiate_provider(Unbuildable) is None
```

**Step 2: Run test to verify it fails**

Run:
```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && uv run pytest tests/cli/test_admin_models.py -k "try_instantiate" -v
```
Expected: FAIL — `ImportError: cannot import name '_try_instantiate_provider'`.

**Step 3: Write minimal implementation**

Port `_try_instantiate_provider` from `provider_loader.py:209-284`. Drop the `collected_config`/`_resolve_env_placeholder` machinery (out of scope) — instantiate with placeholder connection values, exactly enough for `list_models()` to run. Add to `models.py`:

```python
def _try_instantiate_provider(provider_class: type) -> Any | None:
    """Try to instantiate a provider class across known constructor signatures.

    Different providers have different constructor requirements:
    - Standard: (api_key, config) — Anthropic, OpenAI
    - Azure: (*, base_url, api_key, config) — Azure OpenAI
    - Ollama: (host, config)
    - VLLM: (base_url, *, config) — no api_key

    Ported from amplifier_app_cli.provider_loader._try_instantiate_provider.
    Connection values come from the environment via the provider's own logic;
    here we pass conventional placeholders sufficient to construct the object.

    Returns:
        Provider instance, or None if every known signature fails.
    """
    base_url = "http://placeholder"
    host = "http://localhost:11434"
    api_key = ""

    instantiation_errors = (TypeError, ValueError, RuntimeError)

    # Approach 1: Standard (api_key, config) — Anthropic, OpenAI
    try:
        return provider_class(api_key=api_key, config={})
    except instantiation_errors:
        pass

    # Approach 2: Azure-style (keyword-only base_url with api_key)
    try:
        return provider_class(base_url=base_url, api_key=api_key, config={})
    except instantiation_errors:
        pass

    # Approach 3: VLLM-style (base_url without api_key)
    try:
        return provider_class(base_url=base_url, config={})
    except instantiation_errors:
        pass

    # Approach 4: Ollama-style (host, config)
    try:
        return provider_class(host=host, config={})
    except instantiation_errors:
        pass

    # Approach 5: Just config
    try:
        return provider_class(config={})
    except instantiation_errors:
        pass

    # Approach 6: No args
    try:
        return provider_class()
    except instantiation_errors:
        pass

    return None
```

**Step 4: Run test to verify it passes**

Run:
```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && uv run pytest tests/cli/test_admin_models.py -v
```
Expected: PASS.

```python
python_check(paths=["/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/src/amplifier_agent_cli/admin/models.py"])
```
Expected: success.

**Step 5: Commit**

```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && git add src/amplifier_agent_cli/admin/models.py tests/cli/test_admin_models.py && git commit -m "feat(cli): port provider instantiation across constructor signatures

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

---

## Task 11: `list_provider_models` async caller (with timeout, exceptions propagate)

**Files:**
- Modify: `/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/src/amplifier_agent_cli/admin/models.py`
- Test: `/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/tests/cli/test_admin_models.py`

**Step 1: Write the failing test**

Append to `tests/cli/test_admin_models.py`:

```python
def test_list_provider_models_calls_async_and_cleans_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """list_provider_models runs the async list_models() and awaits close()."""
    import amplifier_agent_cli.admin.models as models_mod
    from amplifier_core import ModelInfo

    closed = {"value": False}

    class FakeProvider:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def list_models(self) -> list[ModelInfo]:
            return [ModelInfo(id="m1", display_name="Model One", context_window=1000, max_output_tokens=100)]

        async def close(self) -> None:
            closed["value"] = True

    monkeypatch.setattr(models_mod, "load_provider_class", lambda provider_id: FakeProvider)

    models = models_mod.list_provider_models("anthropic", timeout_seconds=5.0)
    assert [m.id for m in models] == ["m1"]
    assert closed["value"] is True


def test_list_provider_models_propagates_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    """If list_models() raises, the exception propagates (no swallowing)."""
    import amplifier_agent_cli.admin.models as models_mod

    class FakeProvider:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def list_models(self) -> list:
            raise RuntimeError("missing ANTHROPIC_API_KEY")

    monkeypatch.setattr(models_mod, "load_provider_class", lambda provider_id: FakeProvider)

    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        models_mod.list_provider_models("anthropic", timeout_seconds=5.0)
```

**Step 2: Run test to verify it fails**

Run:
```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && uv run pytest tests/cli/test_admin_models.py -k "list_provider_models" -v
```
Expected: FAIL — `ImportError: cannot import name 'list_provider_models'`.

**Step 3: Write minimal implementation**

Add `import asyncio` to the top of `models.py` (with the other stdlib imports). Then add this function (analogous to `provider_loader.get_provider_models:129-188`, plus a timeout):

```python
def list_provider_models(provider_id: str, timeout_seconds: float = 15.0) -> list[Any]:
    """Load a provider and return its list_models() result.

    Analogous to amplifier_app_cli.provider_loader.get_provider_models, but with
    a timeout on the live call and NO fallback: authentication, API, and
    connection errors propagate so the caller can exit non-zero. Returns [] only
    when the provider cannot be loaded/instantiated or lacks list_models() —
    those are not the "live call failed" case.

    Raises:
        Exception: Whatever list_models() raises (auth, network, timeout).
    """
    provider_class = load_provider_class(provider_id)
    if not provider_class:
        return []

    provider = _try_instantiate_provider(provider_class)
    if provider is None:
        logger.debug(f"Could not instantiate provider '{provider_id}' for model listing")
        return []

    if not hasattr(provider, "list_models"):
        logger.debug(f"Provider '{provider_id}' does not have list_models()")
        return []

    list_models_fn = provider.list_models
    if asyncio.iscoroutinefunction(list_models_fn):

        async def _list_and_cleanup() -> list[Any]:
            try:
                return await asyncio.wait_for(list_models_fn(), timeout=timeout_seconds)
            finally:
                if hasattr(provider, "close") and callable(provider.close):
                    try:
                        await provider.close()
                    except Exception:  # noqa: BLE001 - best-effort cleanup
                        pass

        return asyncio.run(_list_and_cleanup())

    return list_models_fn()
```

**Step 4: Run test to verify it passes**

Run:
```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && uv run pytest tests/cli/test_admin_models.py -v
```
Expected: PASS.

```python
python_check(paths=["/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/src/amplifier_agent_cli/admin/models.py"])
```
Expected: success.

**Step 5: Commit**

```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && git add src/amplifier_agent_cli/admin/models.py tests/cli/test_admin_models.py && git commit -m "feat(cli): add list_provider_models async caller with timeout

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

---

## Task 12: Wire `models list` happy path → JSON envelope output

**Files:**
- Modify: `/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/src/amplifier_agent_cli/admin/models.py` (replace the `not implemented` body)
- Test: `/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/tests/cli/test_admin_models.py`

**Step 1: Write the failing test**

Append to `tests/cli/test_admin_models.py`:

```python
def test_models_list_json_envelope_shape(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    """--output json emits the envelope with schema_version, provider, fetched_at, models."""
    import amplifier_agent_cli.admin.models as models_mod
    from amplifier_core import ModelInfo

    def fake_list(provider_id: str, timeout_seconds: float = 15.0) -> list[ModelInfo]:
        return [
            ModelInfo(
                id="claude-sonnet-4-5",
                display_name="Claude Sonnet 4.5",
                context_window=200000,
                max_output_tokens=8192,
                capabilities=["tools", "vision", "thinking"],
            )
        ]

    monkeypatch.setattr(models_mod, "list_provider_models", fake_list)

    result = runner.invoke(cli, ["models", "list", "--provider", "anthropic", "--output", "json"])
    assert result.exit_code == 0, (result.stdout, result.stderr)

    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 1
    assert payload["provider"] == "anthropic"
    assert "fetched_at" in payload
    assert payload["models"][0]["id"] == "claude-sonnet-4-5"
    assert payload["models"][0]["capabilities"] == ["tools", "vision", "thinking"]
```

**Step 2: Run test to verify it fails**

Run:
```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && uv run pytest tests/cli/test_admin_models.py::test_models_list_json_envelope_shape -v
```
Expected: FAIL — exit code 1 with `Error: not implemented` (the stub still raises).

**Step 3: Write minimal implementation**

Add these imports to `models.py` (with the existing imports): `import json`, `import sys`, `from datetime import datetime, timezone`, plus `from amplifier_agent_cli.provider_sources import PROVIDER_CATALOG` and `from amplifier_agent_cli.tty_detect import is_stdout_tty`. Add a module constant `SCHEMA_VERSION = 1` (near the top, after `logger = ...`).

Add a JSON-render helper before `models_group`:
```python
def _render_json(provider_name: str, models: list[Any]) -> None:
    """Print the envelope-wrapped JSON document to stdout."""
    payload = {
        "schema_version": SCHEMA_VERSION,
        "provider": provider_name,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "models": [m.model_dump() if hasattr(m, "model_dump") else dict(m) for m in models],
    }
    click.echo(json.dumps(payload, indent=2))
```

Add a **temporary** table-render stub (Task 13 replaces it with the real renderer, TDD-driven):
```python
def _render_table(models: list[Any]) -> None:
    """Placeholder; real implementation lands in the next task."""
    for m in models:
        click.echo(m.id if hasattr(m, "id") else str(m))
```

Replace the `models_list` body (`raise click.ClickException("not implemented")`) with:
```python
    if provider_name not in PROVIDER_CATALOG:
        known = sorted(PROVIDER_CATALOG)
        raise click.ClickException(f"Unknown provider {provider_name!r}. Known providers: {known}.")

    resolved_output = output_mode
    if resolved_output == "auto":
        resolved_output = "table" if is_stdout_tty() else "json"

    models = list_provider_models(provider_name, timeout_seconds=timeout_seconds)

    if resolved_output == "json":
        _render_json(provider_name, models)
    else:
        _render_table(models)
```

**Step 4: Run test to verify it passes**

Run:
```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && uv run pytest tests/cli/test_admin_models.py -v
```
Expected: PASS (including the wiring/help test still green).

```python
python_check(paths=["/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/src/amplifier_agent_cli/admin/models.py"])
```
Expected: success.

**Step 5: Commit**

```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && git add src/amplifier_agent_cli/admin/models.py tests/cli/test_admin_models.py && git commit -m "feat(cli): render models list as JSON envelope

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

---

## Task 13: Table output (4 columns) + `--output table`

**Files:**
- Modify: `/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/src/amplifier_agent_cli/admin/models.py` (replace the `_render_table` stub)
- Test: `/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/tests/cli/test_admin_models.py`

**Step 1: Write the failing test**

Append to `tests/cli/test_admin_models.py`:

```python
def test_models_list_table_columns(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    """--output table prints ID / DISPLAY NAME / CONTEXT / CAPABILITIES with values."""
    import amplifier_agent_cli.admin.models as models_mod
    from amplifier_core import ModelInfo

    def fake_list(provider_id: str, timeout_seconds: float = 15.0) -> list[ModelInfo]:
        return [
            ModelInfo(
                id="claude-sonnet-4-5",
                display_name="Claude Sonnet 4.5",
                context_window=200000,
                max_output_tokens=8192,
                capabilities=["tools", "vision", "thinking"],
            )
        ]

    monkeypatch.setattr(models_mod, "list_provider_models", fake_list)

    result = runner.invoke(cli, ["models", "list", "--provider", "anthropic", "--output", "table"])
    assert result.exit_code == 0, (result.stdout, result.stderr)

    out = result.stdout
    assert "ID" in out and "DISPLAY NAME" in out and "CONTEXT" in out and "CAPABILITIES" in out
    assert "claude-sonnet-4-5" in out
    assert "Claude Sonnet 4.5" in out
    assert "200000" in out
    assert "tools, vision, thinking" in out
```

**Step 2: Run test to verify it fails**

Run:
```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && uv run pytest tests/cli/test_admin_models.py::test_models_list_table_columns -v
```
Expected: FAIL — the stub from Task 12 prints only IDs, so `DISPLAY NAME` / `CONTEXT` headers are missing.

**Step 3: Write minimal implementation**

Replace the temporary `_render_table` stub with the real renderer:

```python
def _render_table(models: list[Any]) -> None:
    """Print a 4-column table: ID, DISPLAY NAME, CONTEXT, CAPABILITIES."""
    headers = ("ID", "DISPLAY NAME", "CONTEXT", "CAPABILITIES")
    rows: list[tuple[str, str, str, str]] = []
    for m in models:
        data = m.model_dump() if hasattr(m, "model_dump") else dict(m)
        rows.append(
            (
                str(data.get("id", "")),
                str(data.get("display_name", "")),
                str(data.get("context_window", "")),
                ", ".join(data.get("capabilities", []) or []),
            )
        )

    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def _fmt(cells: tuple[str, str, str, str]) -> str:
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells)).rstrip()

    click.echo(_fmt(headers))
    for row in rows:
        click.echo(_fmt(row))
```

**Step 4: Run test to verify it passes**

Run:
```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && uv run pytest tests/cli/test_admin_models.py -v
```
Expected: PASS.

```python
python_check(paths=["/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/src/amplifier_agent_cli/admin/models.py"])
```
Expected: success.

**Step 5: Commit**

```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && git add src/amplifier_agent_cli/admin/models.py tests/cli/test_admin_models.py && git commit -m "feat(cli): render models list as a 4-column table

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

---

## Task 14: Provider error → exit 2 + stderr message

**Files:**
- Modify: `/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/src/amplifier_agent_cli/admin/models.py` (wrap the `list_provider_models` call)
- Test: `/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/tests/cli/test_admin_models.py`

**Step 1: Write the failing test**

Append to `tests/cli/test_admin_models.py`:

```python
def test_models_list_provider_error_exits_2(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    """When list_models() raises, the command exits 2 and prints the error to stderr."""
    import amplifier_agent_cli.admin.models as models_mod

    def fake_list(provider_id: str, timeout_seconds: float = 15.0) -> list:
        raise RuntimeError("missing ANTHROPIC_API_KEY")

    monkeypatch.setattr(models_mod, "list_provider_models", fake_list)

    result = runner.invoke(cli, ["models", "list", "--provider", "anthropic", "--output", "json"])
    assert result.exit_code == 2, (result.stdout, result.stderr)
    assert "ANTHROPIC_API_KEY" in result.stderr
    # Nothing structured should have been written to stdout.
    assert result.stdout.strip() == ""
```

**Step 2: Run test to verify it fails**

Run:
```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && uv run pytest tests/cli/test_admin_models.py::test_models_list_provider_error_exits_2 -v
```
Expected: FAIL — the unhandled `RuntimeError` makes Click exit 1 (not 2), and the error is not on stderr in the expected shape.

**Step 3: Write minimal implementation**

In `models_list`, wrap the `list_provider_models` call in a try/except that prints to stderr and exits 2. Replace:
```python
    models = list_provider_models(provider_name, timeout_seconds=timeout_seconds)
```
with:
```python
    try:
        models = list_provider_models(provider_name, timeout_seconds=timeout_seconds)
    except Exception as exc:  # noqa: BLE001 - surface any provider failure, exit 2
        click.echo(
            f"# {provider_name}: list_models() failed: {type(exc).__name__}: {exc}",
            err=True,
        )
        sys.exit(2)
```

**Step 4: Run test to verify it passes**

Run:
```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && uv run pytest tests/cli/test_admin_models.py -v
```
Expected: PASS.

```python
python_check(paths=["/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/src/amplifier_agent_cli/admin/models.py"])
```
Expected: success.

**Step 5: Commit**

```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && git add src/amplifier_agent_cli/admin/models.py tests/cli/test_admin_models.py && git commit -m "feat(cli): models list exits 2 with stderr message on provider error

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

---

## Task 15: Empty list → exit 0 + stderr advisory

**Files:**
- Modify: `/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/src/amplifier_agent_cli/admin/models.py`
- Test: `/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/tests/cli/test_admin_models.py`

**Step 1: Write the failing test**

Append to `tests/cli/test_admin_models.py`:

```python
def test_models_list_empty_exits_0_with_advisory(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty model list (azure-openai, ollama-down) exits 0 with a stderr advisory."""
    import amplifier_agent_cli.admin.models as models_mod

    monkeypatch.setattr(models_mod, "list_provider_models", lambda provider_id, timeout_seconds=15.0: [])

    result = runner.invoke(cli, ["models", "list", "--provider", "azure-openai", "--output", "json"])
    assert result.exit_code == 0, (result.stdout, result.stderr)
    # Advisory on stderr.
    assert "azure-openai" in result.stderr
    assert "no live model list" in result.stderr
    # JSON envelope still emitted on stdout with an empty models array.
    payload = json.loads(result.stdout)
    assert payload["models"] == []
```

**Step 2: Run test to verify it fails**

Run:
```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && uv run pytest tests/cli/test_admin_models.py::test_models_list_empty_exits_0_with_advisory -v
```
Expected: FAIL — no advisory is printed to stderr yet (`"no live model list" in result.stderr` fails). Exit code and JSON are already correct, so it's the advisory assertion that fails.

**Step 3: Write minimal implementation**

In `models_list`, after the `try/except` that fetches `models` and before rendering, add the advisory:
```python
    if not models:
        click.echo(
            f"# {provider_name}: no live model list available; "
            "enter a model/deployment name manually or use catalog defaults.",
            err=True,
        )
```

So the tail of `models_list` reads:
```python
    try:
        models = list_provider_models(provider_name, timeout_seconds=timeout_seconds)
    except Exception as exc:  # noqa: BLE001
        click.echo(f"# {provider_name}: list_models() failed: {type(exc).__name__}: {exc}", err=True)
        sys.exit(2)

    if not models:
        click.echo(
            f"# {provider_name}: no live model list available; "
            "enter a model/deployment name manually or use catalog defaults.",
            err=True,
        )

    if resolved_output == "json":
        _render_json(provider_name, models)
    else:
        _render_table(models)
```

**Step 4: Run test to verify it passes**

Run:
```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && uv run pytest tests/cli/test_admin_models.py -v
```
Expected: PASS.

```python
python_check(paths=["/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/src/amplifier_agent_cli/admin/models.py"])
```
Expected: success.

**Step 5: Commit**

```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && git add src/amplifier_agent_cli/admin/models.py tests/cli/test_admin_models.py && git commit -m "feat(cli): models list emits stderr advisory on empty list, exit 0

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

---

## Task 16: Unknown provider → exit 1, and full-suite verification

**Files:**
- Test: `/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/tests/cli/test_admin_models.py`
- (No production change expected — the `ClickException` guard added in Task 12 already exits 1.)

**Step 1: Write the failing test**

Append to `tests/cli/test_admin_models.py`:

```python
def test_models_list_unknown_provider_exits_1(runner: CliRunner) -> None:
    """An unknown --provider is a usage error: exit 1 with the known-providers list."""
    result = runner.invoke(cli, ["models", "list", "--provider", "not-a-provider"])
    assert result.exit_code == 1, (result.stdout, result.stderr)
    # click.ClickException writes 'Error: ...' to stderr.
    assert "not-a-provider" in result.stderr
```

**Step 2: Run test to verify it fails (or confirm it already guards)**

Run:
```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && uv run pytest tests/cli/test_admin_models.py::test_models_list_unknown_provider_exits_1 -v
```
Expected: This should PASS immediately because Task 12 added the `ClickException` guard (`click.ClickException` exits with code 1). To prove the test detects regressions, temporarily comment out the `if provider_name not in PROVIDER_CATALOG:` guard in `models_list`, re-run, and confirm it FAILS (the command would proceed to `list_provider_models` and either exit 2 or crash). Then restore the guard.

> Why exit 1 and not 2: `click.ClickException` exits 1; `click.UsageError` exits 2. The design's exit-code table maps usage errors (unknown provider) to **1** and provider/live-call errors to **2**. Using `ClickException` (NOT a `click.Choice`, which would raise `UsageError` → exit 2) is what gives the correct exit-1 semantics. This is why `--provider` is validated manually against `PROVIDER_CATALOG` rather than via `type=click.Choice(...)`.

**Step 3: Write minimal implementation**

None — the guard exists from Task 12. (Step 2 verified it works and detects regressions.)

**Step 4: Run the full verification suite**

Run every suite this plan touched, end to end:

```bash
# Change Set 2 module
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && uv run pytest tests/cli/test_admin_models.py -v

# Change Set 1 modules
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && uv run pytest tests/cli/test_provider_sources.py tests/cli/test_single_turn.py -v

# Full CLI test directory (catch any cross-impact)
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && uv run pytest tests/cli/ -q

# TypeScript wrapper
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/wrappers/typescript && npx vitest run test/argv-builder.test.ts
```
Expected: all green.

Run the quality gate over every file you created or modified:
```python
python_check(paths=[
    "/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/src/amplifier_agent_cli/admin/models.py",
    "/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/src/amplifier_agent_cli/provider_sources.py",
    "/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/src/amplifier_agent_cli/modes/single_turn.py",
    "/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/src/amplifier_agent_cli/__main__.py",
    "/Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent/tests/cli/test_admin_models.py",
])
```
Expected: success (no errors).

**Step 5: Commit**

```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && git add tests/cli/test_admin_models.py && git commit -m "test(cli): models list unknown provider exits 1

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>"
```

---

## Final: Scope confirmation & next downstream work

**Confirm nothing out-of-scope was touched.** Run:
```bash
cd /Users/mpaidiparthy/repos/amplifier-nanoclaw/amplifier-agent && git diff --stat a7426a6..HEAD
```
Expected: the diff touches ONLY these files:
- `wrappers/typescript/src/argv-builder.ts`
- `wrappers/typescript/test/argv-builder.test.ts`
- `src/amplifier_agent_cli/provider_sources.py`
- `src/amplifier_agent_cli/modes/single_turn.py`
- `src/amplifier_agent_cli/admin/models.py` (new)
- `src/amplifier_agent_cli/__main__.py`
- `tests/cli/test_provider_sources.py`
- `tests/cli/test_single_turn.py`
- `tests/cli/test_admin_models.py` (new)

If any file outside this list appears in the diff, STOP and report it.

**Explicitly NOT touched (by design):**
- `amplifier-app-nanoclaw` — no changes. This plan only exposes the capability; nanoclaw consumes it later.
- Any provider module (`amplifier-module-provider-*`) — they already implement `list_models()`.
- No real-API integration tests, container tests, or CI smoke tests.
- No `models default` subcommand, no `--source` flag, no boot-time `--model` validation.

**Next downstream work (blocked on this plan merging — do NOT start it here):**
- nanoclaw model selection: surface `--model`/`--effort` per agent group in `amplifier-app-nanoclaw`'s `container_configs`, and call `amplifier-agent models list --provider X --output json` to populate a model picker. That work lives in the nanoclaw repo and is out of scope for this branch.

**Branch is ready for review.** The implementer should hand the branch `feat/model-overrides-and-discovery` back to the user for the merge/PR decision (do not open a PR autonomously).

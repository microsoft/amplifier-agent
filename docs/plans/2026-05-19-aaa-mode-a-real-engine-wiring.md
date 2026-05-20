# AaA Mode A — Real Engine Wiring (Cheatsheet §3 Fix)

> **Execution:** Use the `executing-plans` skill in `execute-plan` mode.

## Why this plan exists

The cheatsheet walk-through (in-session 2026-05-19) failed at §3 — the flagship "simplest test":

```
$ uv run amplifier-agent run "Reply with exactly one word: 'pong'"
TypeError: Engine.boot() got an unexpected keyword argument 'provider'
  at src/amplifier_agent_cli/modes/single_turn.py:251
```

Two intertwined bugs:

1. **Wrong wire-up.** `single_turn.py:run()` calls `Engine.boot(provider=..., approval=..., display=..., session_id=..., resume=..., fresh=..., cwd=..., bundle_override=..., config_path=...)` — a fictional Phase-2+ classmethod-factory. The real signature is `async def Engine.boot(self, params, bundle_override=None) -> InitializeResult`, called on an instance constructed with `Engine(turn_handler=..., protocol_points=...)`. `engine.submit_turn(prompt)` similarly passes a raw string but the real method takes a `TurnSubmitParams` dict.
2. **No real turn handler.** Even after fixing the wire-up, the inline `_StdioEngine.initialize` in `single_turn.py` (used by Mode B) builds the Engine with `async def _stub_handler(ctx): return ""  # Phase 4 wires in the real turn handler.` Mode A's fix will re-use the same handler factory, so both modes need the real handler that calls `PreparedBundle.create_session(...).execute(prompt)`.

**Why pytest didn't catch this.** The 16 tests in `tests/cli/test_single_turn.py` patch `Engine` with a `MagicMock`. The runtime signature mismatch is invisible to the test suite. The author's own confessional comment at `single_turn.py:244-247` admits this.

## Architecture

```
┌───────────────────────────────────────────────────────────────────┐
│ amplifier_agent_cli/modes/single_turn.py                          │
│                                                                   │
│  run() ── click flags → _TurnSpec ──┐                             │
│                                     ▼                             │
│                          _execute_turn(spec)  [asyncio.run]       │
│                          ┌──────────────────────────┐             │
│                          │ load_and_prepare_cached  │             │
│                          │ make_turn_handler(...)   │ ◄── NEW     │
│                          │ Engine(handler, points)  │             │
│                          │ await engine.boot(p, b)  │             │
│                          │ await engine.submit_turn │             │
│                          │ await engine.shutdown    │             │
│                          └──────────────────────────┘             │
└───────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌───────────────────────────────────────────────────────────────────┐
│ amplifier_agent_lib/_runtime.py             NEW MODULE            │
│   make_turn_handler(prepared, *, cwd, is_resumed) → TurnHandler   │
│     async def handler(ctx):                                       │
│       async with prepared.create_session(                         │
│           session_id=ctx.session_id or None,                      │
│           session_cwd=resolved_cwd, is_resumed=is_resumed,        │
│       ) as session:                                               │
│           return await session.execute(ctx.prompt)                │
└───────────────────────────────────────────────────────────────────┘
```

Same handler factory is reused by the inline `_StdioEngine` in `single_turn.py` so Mode B stops returning empty replies too.

## Out of scope

- Bridging our `protocol_points.ApprovalSystem` / `DisplaySystem` (method names `request` / `emit`) to amplifier-core's interfaces (`request_approval` / `show_message`). For this fix we pass `None` to `create_session(...)` for both — the bundle's default behavior applies. Bridging is a separate piece of work.
- The `--config <path>` flag stays a no-op (no config file format exists).
- The `--bundle <path>` flag stays hidden; if set, falls back to the cached default. (Wiring the override path lives in a later task.)

## Conventions

- Test-first: RED → verify-fail → GREEN → verify-pass → commit, per task.
- Conventional Commits (`fix:`, `feat:`, `test:`, `refactor:`, `chore:`).
- `from __future__ import annotations` at the top of every module.
- Type hints throughout; `ruff` and `pyright` clean.
- Each task = one atomic commit.

## Task dependencies

- T1 (helper) — independent.
- T2 (failing integration test) — depends on T1.
- T3 (Mode A `run()` rewrite) — depends on T1 + T2.
- T4 (Mode B inline `_StdioEngine` real handler) — depends on T1.
- T5 (rewrite the 16 mocked tests) — depends on T3.
- T6 (final verification) — depends on T1–T5.

---

## Task 1: `_runtime.make_turn_handler`

**Files:**
- Create: `src/amplifier_agent_lib/_runtime.py`
- Create: `tests/test_runtime.py`

### Step 1 — Failing test

`tests/test_runtime.py`:

```python
"""Tests for _runtime.make_turn_handler — the bridge from TurnContext to AmplifierSession."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_agent_lib._runtime import make_turn_handler
from amplifier_agent_lib.engine import TurnContext


def _ctx(prompt: str = "hi", session_id: str = "s") -> TurnContext:
    return TurnContext(
        session_id=session_id,
        turn_id="t-1",
        prompt=prompt,
        approval=MagicMock(),
        display=MagicMock(),
    )


def _fake_prepared(reply: str = "hello back") -> tuple[Any, AsyncMock]:
    """Return (prepared_bundle_mock, session_execute_mock)."""
    session = MagicMock()
    session.execute = AsyncMock(return_value=reply)
    ctx_mgr = MagicMock()
    ctx_mgr.__aenter__ = AsyncMock(return_value=session)
    ctx_mgr.__aexit__ = AsyncMock(return_value=None)
    prepared = MagicMock()
    prepared.create_session = MagicMock(return_value=ctx_mgr)
    return prepared, session.execute


@pytest.mark.asyncio
async def test_handler_calls_create_session_and_execute() -> None:
    prepared, execute = _fake_prepared("hello back")
    handler = make_turn_handler(prepared, cwd=None, is_resumed=False)
    reply = await handler(_ctx(prompt="ping"))
    assert reply == "hello back"
    execute.assert_awaited_once_with("ping")
    kwargs = prepared.create_session.call_args.kwargs
    assert kwargs["session_id"] == "s"
    assert kwargs["is_resumed"] is False


@pytest.mark.asyncio
async def test_handler_passes_session_cwd_resolved(tmp_path: Any) -> None:
    prepared, _ = _fake_prepared()
    handler = make_turn_handler(prepared, cwd=str(tmp_path), is_resumed=False)
    await handler(_ctx())
    assert prepared.create_session.call_args.kwargs["session_cwd"] == tmp_path.resolve()


@pytest.mark.asyncio
async def test_handler_empty_session_id_becomes_none() -> None:
    prepared, _ = _fake_prepared()
    handler = make_turn_handler(prepared, cwd=None, is_resumed=False)
    await handler(_ctx(session_id=""))
    assert prepared.create_session.call_args.kwargs["session_id"] is None


@pytest.mark.asyncio
async def test_handler_passes_is_resumed() -> None:
    prepared, _ = _fake_prepared()
    handler = make_turn_handler(prepared, cwd=None, is_resumed=True)
    await handler(_ctx())
    assert prepared.create_session.call_args.kwargs["is_resumed"] is True
```

**Verify FAIL:** `uv run pytest tests/test_runtime.py -v` → `ModuleNotFoundError`.

### Step 2 — Minimal implementation

`src/amplifier_agent_lib/_runtime.py`:

```python
"""Shared runtime helpers — TurnHandler factory bridging Engine ↔ AmplifierSession.

`make_turn_handler` is the single place that knows how to turn a TurnContext
(transport-agnostic, defined by the engine library) into a real Amplifier
session call. Both Mode A (single_turn.py) and Mode B (the inline _StdioEngine
adapter) consume the same factory so the model-invocation pathway is identical
across modes.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from amplifier_agent_lib.engine import TurnContext, TurnHandler

if TYPE_CHECKING:
    from amplifier_foundation.bundle._prepared import PreparedBundle


def make_turn_handler(
    prepared: PreparedBundle,
    *,
    cwd: str | None,
    is_resumed: bool,
) -> TurnHandler:
    """Return a TurnHandler closed over the loaded PreparedBundle.

    The returned coroutine creates a fresh AmplifierSession per turn
    (one-shot stateful via logical replay; OpenClaw pattern) and returns
    the model reply.
    """
    resolved_cwd: Path | None = Path(cwd).resolve() if cwd else None

    async def handler(ctx: TurnContext) -> str:
        session_id = ctx.session_id if ctx.session_id else None
        async with prepared.create_session(
            session_id=session_id,
            session_cwd=resolved_cwd,
            is_resumed=is_resumed,
        ) as session:
            return await session.execute(ctx.prompt)

    return handler
```

**Verify PASS:** `uv run pytest tests/test_runtime.py -v` → 4 PASS.

**Commit:** `feat(runtime): add make_turn_handler bridging Engine to AmplifierSession`

---

## Task 2: Failing integration test for Mode A

Locks in the regression so this bug never reappears.

**Files:**
- Create: `tests/cli/test_mode_a_integration.py`

### Step 1 — Failing test

```python
"""Mode A integration test — drives `run` against the REAL Engine (not a MagicMock),
with a stubbed PreparedBundle so we don't hit any network or real provider.

Regression guard for the bug discovered during the 2026-05-19 cheatsheet
walk-through: single_turn.py called the fictional Engine.boot(provider=...)
classmethod and crashed with TypeError on first real invocation. The
pre-existing test suite missed it because every other Mode A test mocked Engine.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from amplifier_agent_cli.__main__ import cli


class _StubPrepared:
    """Stand-in for amplifier_foundation PreparedBundle — duck-typed."""

    mount_plan: ClassVar[dict] = {"session": {}, "tools": []}
    resolver: ClassVar[Any] = None
    bundle_package_paths: ClassVar[list] = []

    def create_session(self, **kwargs: Any) -> Any:
        session = MagicMock()
        session.execute = AsyncMock(return_value=f"stub-reply for prompt")
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=session)
        ctx.__aexit__ = AsyncMock(return_value=None)
        return ctx


def test_mode_a_run_does_not_crash_with_typeerror(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    async def _stub_cache(*, aaa_version: str) -> _StubPrepared:
        return _StubPrepared()

    with patch("amplifier_agent_cli.modes.single_turn.load_and_prepare_cached", _stub_cache):
        runner = CliRunner()
        result = runner.invoke(cli, ["run", "ping"])

    assert "TypeError" not in (result.stderr or ""), f"TypeError leaked through:\n{result.stderr}"
    assert result.exit_code == 0, f"unexpected exit {result.exit_code}; stderr={result.stderr}"


def test_mode_a_run_emits_json_with_reply_to_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    async def _stub_cache(*, aaa_version: str) -> _StubPrepared:
        return _StubPrepared()

    with patch("amplifier_agent_cli.modes.single_turn.load_and_prepare_cached", _stub_cache):
        runner = CliRunner()
        result = runner.invoke(cli, ["run", "say hi"])

    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert "reply" in parsed
    assert parsed["reply"] == "stub-reply for prompt"
    assert parsed["turnId"] == "turn-1"
```

**Verify FAIL:** `uv run pytest tests/cli/test_mode_a_integration.py -v` → both tests FAIL with `TypeError`.

**Commit:** `test(cli): regression guard — Mode A run integrates with real Engine`

---

## Task 3: Rewrite `single_turn.py:run()` body

**Files:**
- Modify: `src/amplifier_agent_cli/modes/single_turn.py`

### Behavior

Click flags stay identical. Replace the internal pipeline:

```
run(...) →
  if --stdio: existing Mode B path (Task 4 fixes the stub handler inside it)
  validate -y/-n exclusion
  prompt-required guard
  detect_provider(override=provider_override) — raises ProviderNotConfigured
  approval = CliApprovalSystem(mode=_resolve_approval_mode(...))
  display  = CliDisplaySystem(verbosity=_resolve_verbosity(...), stream=sys.stderr)
  spec = _TurnSpec(prompt, session_id, resume, fresh, cwd, approval, display)
  try:
      result = asyncio.run(_execute_turn(spec))
  except AaaError as exc:  _emit_error(exc.code, exc.message); sys.exit(1)
  except Exception as exc: _emit_error("internal", f"{type(exc).__name__}: {exc}"); sys.exit(1)
  click.echo(json.dumps(result, indent=2))
```

`_execute_turn`:

```python
@dataclass
class _TurnSpec:
    prompt: str
    session_id: str | None
    resume: bool
    fresh: bool
    cwd: str | None
    approval: CliApprovalSystem
    display: CliDisplaySystem


async def _execute_turn(spec: _TurnSpec) -> dict[str, Any]:
    prepared = await load_and_prepare_cached(aaa_version=__version__)

    if spec.fresh and spec.session_id:
        from amplifier_agent_lib.persistence import session_state_dir
        import shutil
        state_dir = session_state_dir(spec.session_id)
        if state_dir.exists():
            shutil.rmtree(state_dir, ignore_errors=True)

    handler = make_turn_handler(
        prepared,
        cwd=spec.cwd,
        is_resumed=spec.resume and not spec.fresh,
    )
    engine = Engine(
        turn_handler=handler,
        protocol_points={"approval": spec.approval, "display": spec.display},
    )

    init_params: dict[str, Any] = {
        "protocolVersion": PROTOCOL_VERSION,
        "clientInfo": {"name": "amplifier-agent-cli", "version": __version__},
        "capabilities": dict(server_default_capabilities()),
        "sessionId": spec.session_id or "",
        "resume": spec.resume,
    }
    await engine.boot(init_params, bundle_override=prepared)

    submit_params: dict[str, Any] = {
        "sessionId": spec.session_id or "",
        "turnId": "turn-1",
        "prompt": spec.prompt,
    }
    try:
        result = await engine.submit_turn(submit_params)
    finally:
        await engine.shutdown()
    return dict(result)
```

Remove the stale Phase-2+ confessional comment.

**Verify PASS:**
- `uv run pytest tests/cli/test_mode_a_integration.py -v` → both PASS
- `uv run ruff check` → clean
- `uv run pyright` → clean
- (`tests/cli/test_single_turn.py` may show failures — fixed in Task 5)

**Commit:** `fix(cli): wire Mode A to the real async Engine API and real turn handler`

---

## Task 4: Replace Mode B's `_stub_handler`

**Files:**
- Modify: `src/amplifier_agent_cli/modes/single_turn.py` (the inline `_StdioEngine.initialize`)

Replace the inline `_stub_handler` with the real `make_turn_handler` factory using a pre-loaded bundle. Bundle is loaded once per stdio session and reused for every turn.

```python
async def initialize(self, *, client_capabilities, client_info):
    from amplifier_agent_lib._runtime import make_turn_handler
    from amplifier_agent_lib.bundle.cache import load_and_prepare_cached

    prepared = await load_and_prepare_cached(aaa_version=__version__)
    handler = make_turn_handler(prepared, cwd=None, is_resumed=False)
    engine = Engine(
        turn_handler=handler,
        protocol_points={"approval": self._approval, "display": self._display},
    )
    result = await engine.boot(
        {
            "capabilities": client_capabilities,
            "sessionId": "",
            "resume": False,
        },
        bundle_override=prepared,
    )
    self._inner = engine
    return {
        "protocolVersion": _PROTOCOL_VERSION,
        "serverInfo": dict(result["serverInfo"]),
        "capabilities": dict(result["capabilities"]),
    }
```

**Verify PASS:**
- `uv run pytest tests/cli/test_stdio_loop_subprocess.py tests/cli/test_stdio_loop_dispatch.py -v` → all green

**Commit:** `fix(cli): wire Mode B inline _StdioEngine to the real turn handler`

---

## Task 5: Repair the 16 existing tests in `test_single_turn.py`

**Files:**
- Modify: `tests/cli/test_single_turn.py`

Existing tests patch `single_turn.Engine` with `MagicMock`. After Task 3, the seam is `_execute_turn(spec)`. Patch there instead.

Helper near the top:

```python
from typing import Callable

def _patch_execute_turn(
    *,
    reply: str = "stub",
    raises: Exception | None = None,
) -> tuple[Callable, list]:
    """Return (patch_target, captured_specs_list).

    Captures every _TurnSpec passed in so tests can assert on flag → spec mapping.
    """
    captured: list = []

    async def _fake(spec):
        captured.append(spec)
        if raises is not None:
            raise raises
        return {"reply": reply, "turnId": "turn-1"}

    return patch("amplifier_agent_cli.modes.single_turn._execute_turn", _fake), captured
```

Rewrite each of the 16 tests. Examples (full list inline in implementation):

```python
def test_run_with_prompt_prints_json_to_stdout(runner, monkeypatch):
    _set_anthropic(monkeypatch)
    patch_obj, captured = _patch_execute_turn(reply="hello!")
    with patch_obj:
        result = runner.invoke(cli, ["run", "hello!"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["reply"] == "hello!"

def test_run_y_flag_sets_approval_mode_yes(runner, monkeypatch):
    _set_anthropic(monkeypatch)
    patch_obj, captured = _patch_execute_turn()
    with patch_obj:
        result = runner.invoke(cli, ["run", "test", "-y"])
    assert result.exit_code == 0
    assert captured[0].approval.mode == "yes"

def test_run_engine_raising_aaa_error_returns_json_envelope(runner, monkeypatch):
    _set_anthropic(monkeypatch)
    patch_obj, _ = _patch_execute_turn(raises=AaaError(code="bundle_load_failed", message="bad bundle"))
    with patch_obj:
        result = runner.invoke(cli, ["run", "test"])
    assert result.exit_code == 1
    parsed = json.loads(result.stdout)
    assert parsed["error"]["code"] == "bundle_load_failed"
    assert parsed["error"]["message"] == "bad bundle"
```

Tests 5, 13, 14, 15 (mutual exclusion, --stdio, prompt_required, no provider) are unaffected — keep as-is.

**Verify PASS:**
- `uv run pytest tests/cli/test_single_turn.py -v` → 16 PASS
- `uv run pytest tests/ -q --tb=short` → all PASS

**Commit:** `refactor(test): repair test_single_turn.py to patch _execute_turn seam`

---

## Task 6: Full verification

**Commands to run, in this session, capturing each output:**

1. `uv run pytest -q --tb=short` → must show all tests passing
2. `uv run ruff check`
3. `uv run pyright`
4. `uv run amplifier-agent --version`
5. `uv run amplifier-agent doctor`
6. `uv run amplifier-agent run "Reply with exactly: pong"` — MUST NOT crash with TypeError. Outcome is either:
   - JSON `{"reply": "...", "turnId": "turn-1"}` on stdout, exit 0
   - JSON `{"error": {"code": "...", "message": "..."}}` on stdout, exit 1 (graceful)
7. Mode B handshake probe: send `agent/initialize` + `turn/submit` + `agent/shutdown`; `turn/submit` reply MUST be non-empty (or a structured error, not just `""`).

If step 6 returns a structured `internal`/`provider_init_failed` error (likely with the short test key in env), that still proves wiring is correct — the next layer is reachable. The `TypeError` must not appear.

**Commit:** `chore: verification — Mode A real-engine wiring`

---

## Out-of-band finishing

After Task 6 passes, follow `superpowers:finishing-a-development-branch` to decide on merge / PR / cleanup.

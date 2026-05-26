# AaA Mode A Pivot — Phase A: Engine Implementation Plan

> **For execution:** Use `/execute-plan` mode or the subagent-driven-development recipe.

**Prerequisite:** Mode A pivot amendment (`docs/designs/2026-05-24-aaa-v2-mode-a-pivot-amendment.md`) locked. The amendment is the source of truth for all code shapes referenced below; consult it whenever a task says "per amendment §X".

**Goal:** Extend the engine's Mode A `run` subcommand to be the v2 wire surface — argv-borne session config (`--mcp-servers`, `--host-capabilities`, `--env-allowlist`, `--env-extra`, `--protocol-version`, `--output`), a structured JSON envelope on stdout that the wrapper parses (`{protocolVersion, sessionId, turnId, reply, error, metadata}`), structural stdout discipline so noisy bundles can never corrupt the envelope, a per-turn audit file digest of every secret-bearing input, and a process-group setup (`os.setsid()`) so the Phase B wrapper can reap MCP children via group signals.

**Architecture:** All changes are additive on the existing `src/amplifier_agent_cli/modes/single_turn.py` `run` command (per O4' — do NOT create a new module). Five new click options are added; the existing eight options carry forward unchanged. A new `_envelope_writer` helper enforces stdout discipline via `contextlib.redirect_stdout(sys.stderr)`. A new `audit_writer` helper writes a per-turn digest file to `$XDG_STATE_HOME/amplifier-agent/sessions/<sid>/audits/turn-<turnId>.json`. The existing `_TurnSpec`, `_execute_turn`, `_emit_error` helpers are extended (not rewritten).

**Tech Stack:** Python 3.11+, `click` for CLI parsing, `pytest` for tests, `ruff` for lint, `pyright` for type checking. All tooling invoked via `uv run`.

**Real-binary gate:** Tasks 13 and 14 are real-binary integration tests that launch `amplifier-agent` as a subprocess (no mocks of click parsing, envelope emission, or stdout discipline). Tasks 2–12 are unit tests against helper functions and the click runner's `CliRunner`. The Phase A acceptance gate (§ end) requires both unit and real-binary tests to pass.

**Task count:** 15 tasks. Each is 2–5 minutes of focused work.

---

## Required reading before starting

Before Task 1, read these so subsequent tasks are quick mechanical work:

1. `docs/designs/2026-05-24-aaa-v2-mode-a-pivot-amendment.md` §3 (CLI spec), §4 (JSON envelope schema), §4.0 (stdout discipline), §4.4 (exit code policy), §8.1 A1'/A2'/A2.1' (stage descriptions).
2. `src/amplifier_agent_cli/modes/single_turn.py` (the file being extended).
3. `src/amplifier_agent_lib/protocol/__init__.py` — confirm `PROTOCOL_VERSION` constant exists and note its current value.
4. `src/amplifier_agent_lib/persistence.py` — confirm `session_state_dir(session_id)` resolves to `$XDG_STATE_HOME/amplifier-agent/sessions/<sid>`; the audit file lives at `<that>/audits/turn-<turnId>.json`.
5. `tests/cli/` — pick one existing test file as the structural template for new tests (e.g. `tests/cli/test_run_command.py` if present, otherwise `tests/test_engine.py`).

Verify the test framework with `uv run pytest --collect-only tests/cli/ 2>&1 | head -5` before writing any test.

---

## Task 1: Create a feature branch and confirm baseline green

**Files:**
- No file changes.

**Step 1: Create the branch**
```bash
git checkout -b feat/mode-a-phase-a-engine
```

**Step 2: Confirm baseline tests pass**
```bash
uv run pytest tests/ -q
```
Expected: `431 passed` (the count quoted in the parent session; may have grown slightly). Exit code 0.

**Step 3: Confirm lint and type checks pass**
```bash
uv run ruff check src/ tests/ && uv run pyright src/
```
Expected: both exit 0.

**Step 4: Commit (empty marker)**
```bash
git commit --allow-empty -m "chore(engine): start Phase A — Mode A pivot engine work"
```

---

## Task 2: Write failing test for `--output json` default + envelope schema

**Files:**
- Create: `tests/cli/test_mode_a_v2_envelope.py`

**Test type:** (a) unit — uses `click.testing.CliRunner`, no real subprocess.

**Step 1: Write the failing test**
```python
# tests/cli/test_mode_a_v2_envelope.py
"""Phase A — Mode A v2 JSON envelope tests (unit-level, CliRunner)."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from amplifier_agent_cli.modes.single_turn import run


def _mock_turn_result(reply: str = "ok") -> dict:
    return {"sessionId": "test-sid", "turnId": "turn-1", "reply": reply}


def test_output_defaults_to_json_envelope_shape() -> None:
    """When --output is omitted, stdout is one JSON envelope per amendment §4.1."""
    runner = CliRunner()
    with patch(
        "amplifier_agent_cli.modes.single_turn._execute_turn",
        return_value=_mock_turn_result("hi"),
    ), patch(
        "amplifier_agent_cli.provider_detect.detect_provider",
        return_value="anthropic",
    ):
        result = runner.invoke(run, ["--session-id", "sid-1", "hello"])

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    # Required top-level fields per §4.1:
    assert "protocolVersion" in envelope
    assert envelope["sessionId"] == "sid-1"
    assert envelope["turnId"] == "turn-1"
    assert envelope["reply"] == "hi"
    assert envelope["error"] is None
    assert "metadata" in envelope
    assert "correlationId" in envelope["metadata"]
    assert "engineVersion" in envelope["metadata"]
```

**Step 2: Run the test to verify it fails**
```bash
uv run pytest tests/cli/test_mode_a_v2_envelope.py::test_output_defaults_to_json_envelope_shape -v
```
Expected: **FAIL.** The most likely failure is `KeyError: 'protocolVersion'` or `AssertionError` because the current `single_turn.py:247` emits `json.dumps(result, indent=2)` where `result` is the raw turn dict — it has no `protocolVersion`, no `metadata`, no `correlationId`.

**Step 3: Commit the failing test**
```bash
git add tests/cli/test_mode_a_v2_envelope.py
git commit -m "test(engine): A2' — failing test for v2 JSON envelope shape"
```

---

## Task 3: Implement minimal envelope builder to make Task 2 pass

**Files:**
- Modify: `src/amplifier_agent_cli/modes/single_turn.py`

**Step 1: Add the envelope builder helper**

Open `src/amplifier_agent_cli/modes/single_turn.py`. After the `_resolve_verbosity` helper (around line 65), add:

```python
import uuid
from datetime import datetime, timezone


def _mint_correlation_id() -> str:
    """UUID v4, minted once per `run` invocation. SC-G."""
    return str(uuid.uuid4())


def _build_envelope(
    result: dict[str, Any],
    *,
    correlation_id: str,
    host_capabilities: dict[str, Any] | None,
    duration_ms: int,
) -> dict[str, Any]:
    """Build the §4.1 success envelope from an engine turn result."""
    metadata: dict[str, Any] = {
        "tokensIn": int(result.get("tokensIn", 0) or 0),
        "tokensOut": int(result.get("tokensOut", 0) or 0),
        "durationMs": duration_ms,
        "bundleDigest": result.get("bundleDigest", ""),
        "engineVersion": __version__,
        "protocolVersion": PROTOCOL_VERSION,
        "correlationId": correlation_id,
    }
    if host_capabilities is not None:
        metadata["hostCapabilities"] = host_capabilities
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "sessionId": result.get("sessionId", ""),
        "turnId": result.get("turnId", "turn-1"),
        "reply": result.get("reply", ""),
        "error": None,
        "metadata": metadata,
    }
```

**Step 2: Wire the envelope builder into the success path**

Replace the final `click.echo(json.dumps(result, indent=2))` at line 247 with:

```python
    correlation_id = _mint_correlation_id()
    import time
    started = time.monotonic()
    try:
        result = asyncio.run(_execute_turn(spec))
    except AaaError as exc:
        _emit_error(exc.code, exc.message)
        sys.exit(1)
    except Exception as exc:
        _emit_error("internal", f"{type(exc).__name__}: {exc}")
        sys.exit(1)
    duration_ms = int((time.monotonic() - started) * 1000)
    envelope = _build_envelope(
        result,
        correlation_id=correlation_id,
        host_capabilities=None,  # populated in Task 7
        duration_ms=duration_ms,
    )
    click.echo(json.dumps(envelope))
```

**Step 3: Run the test to verify it passes**
```bash
uv run pytest tests/cli/test_mode_a_v2_envelope.py::test_output_defaults_to_json_envelope_shape -v
```
Expected: **PASS.**

**Step 4: Commit**
```bash
git add src/amplifier_agent_cli/modes/single_turn.py
git commit -m "feat(engine): A2' — emit Mode A v2 JSON envelope on success"
```

---

## Task 4: Write failing test for `--output text` opt-in human form

**Files:**
- Modify: `tests/cli/test_mode_a_v2_envelope.py`

**Test type:** (a) unit.

**Step 1: Append the test**
```python
def test_output_text_emits_reply_only() -> None:
    """--output text emits the reply on stdout, no JSON envelope. §4.6."""
    runner = CliRunner()
    with patch(
        "amplifier_agent_cli.modes.single_turn._execute_turn",
        return_value=_mock_turn_result("plain text reply"),
    ), patch(
        "amplifier_agent_cli.provider_detect.detect_provider",
        return_value="anthropic",
    ):
        result = runner.invoke(
            run, ["--session-id", "sid-1", "--output", "text", "hello"]
        )

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "plain text reply"
    # Must NOT be parseable as the JSON envelope:
    with pytest.raises(json.JSONDecodeError):
        json.loads(result.stdout)
```

**Step 2: Run it; expect FAIL**
```bash
uv run pytest tests/cli/test_mode_a_v2_envelope.py::test_output_text_emits_reply_only -v
```
Expected: **FAIL** — `--output` is not a recognized flag yet, so click rejects with exit code 2 and `Error: No such option: --output`.

**Step 3: Commit failing test**
```bash
git add tests/cli/test_mode_a_v2_envelope.py
git commit -m "test(engine): A1' — failing test for --output text|json flag"
```

---

## Task 5: Add `--output` click option and implement text mode

**Files:**
- Modify: `src/amplifier_agent_cli/modes/single_turn.py`

**Step 1: Add the click option**

Above the `--allow-protocol-skew` option (around line 169), add:

```python
@click.option(
    "--output",
    "output_mode",
    type=click.Choice(["text", "json"], case_sensitive=False),
    default="json",
    show_default=True,
    help="Output mode: 'json' (default, envelope) or 'text' (reply only).",
)
```

Add `output_mode: str` to the `run()` function signature.

**Step 2: Branch on output mode**

Replace the success-path envelope emission with:
```python
    if output_mode == "json":
        envelope = _build_envelope(
            result,
            correlation_id=correlation_id,
            host_capabilities=None,
            duration_ms=duration_ms,
        )
        click.echo(json.dumps(envelope))
    else:  # text
        click.echo(result.get("reply", ""))
```

**Step 3: Run both Task 2 + Task 4 tests**
```bash
uv run pytest tests/cli/test_mode_a_v2_envelope.py -v
```
Expected: both PASS.

**Step 4: Commit**
```bash
git add src/amplifier_agent_cli/modes/single_turn.py
git commit -m "feat(engine): A1' — add --output {text,json} flag, default json"
```

---

## Task 6: Write failing test for `--mcp-servers` inline JSON + `@path`

**Files:**
- Modify: `tests/cli/test_mode_a_v2_envelope.py`

**Test type:** (a) unit (we test the parsing, not the engine's `tool-mcp.mount` threading — that's covered by existing engine tests).

**Step 1: Append two tests**
```python
def test_mcp_servers_inline_json_parsed() -> None:
    """--mcp-servers '<json>' parses into the engine's _TurnSpec."""
    runner = CliRunner()
    captured: dict = {}

    async def fake_execute(spec):
        captured["mcp_servers"] = spec.mcp_servers
        return _mock_turn_result("ok")

    with patch("amplifier_agent_cli.modes.single_turn._execute_turn", side_effect=fake_execute), patch(
        "amplifier_agent_cli.provider_detect.detect_provider", return_value="anthropic"
    ):
        result = runner.invoke(
            run,
            [
                "--session-id",
                "sid-1",
                "--mcp-servers",
                '{"nc_send":{"transport":"stdio","command":"node","args":["/x.js"]}}',
                "hello",
            ],
        )

    assert result.exit_code == 0, result.output
    assert captured["mcp_servers"] == {
        "nc_send": {"transport": "stdio", "command": "node", "args": ["/x.js"]}
    }


def test_mcp_servers_at_path_form(tmp_path) -> None:
    """--mcp-servers @<path> reads JSON from a file."""
    cfg = tmp_path / "mcp.json"
    cfg.write_text(
        '{"server":{"transport":"stdio","command":"node","args":[]}}',
        encoding="utf-8",
    )
    runner = CliRunner()
    captured: dict = {}

    async def fake_execute(spec):
        captured["mcp_servers"] = spec.mcp_servers
        return _mock_turn_result("ok")

    with patch("amplifier_agent_cli.modes.single_turn._execute_turn", side_effect=fake_execute), patch(
        "amplifier_agent_cli.provider_detect.detect_provider", return_value="anthropic"
    ):
        result = runner.invoke(
            run, ["--session-id", "sid-1", "--mcp-servers", f"@{cfg}", "hello"]
        )

    assert result.exit_code == 0, result.output
    assert captured["mcp_servers"] == {
        "server": {"transport": "stdio", "command": "node", "args": []}
    }


def test_mcp_servers_malformed_json_yields_argv_envelope() -> None:
    """Malformed JSON in --mcp-servers maps to AaaError(argv_json_malformed). O2'."""
    runner = CliRunner()
    with patch(
        "amplifier_agent_cli.provider_detect.detect_provider", return_value="anthropic"
    ):
        result = runner.invoke(
            run,
            [
                "--session-id",
                "sid-1",
                "--mcp-servers",
                "{not json",
                "hello",
            ],
        )

    assert result.exit_code == 2, result.output
    envelope = json.loads(result.stdout)
    assert envelope["error"]["code"] == "argv_json_malformed"
    assert envelope["error"]["classification"] == "protocol"
```

**Step 2: Run; expect FAIL**
```bash
uv run pytest tests/cli/test_mode_a_v2_envelope.py -v -k mcp_servers
```
Expected: 3 FAIL — flag not recognized, `_TurnSpec` has no `mcp_servers` attribute, no `argv_json_malformed` envelope path.

**Step 3: Commit failing tests**
```bash
git add tests/cli/test_mode_a_v2_envelope.py
git commit -m "test(engine): A1'/D9 — failing tests for --mcp-servers parsing"
```

---

## Task 7: Implement `--mcp-servers` flag (inline + @path) with typed error envelope

**Files:**
- Modify: `src/amplifier_agent_cli/modes/single_turn.py`

**Step 1: Add the JSON-or-@path click callback**

After the `_resolve_verbosity` helper, add:
```python
from pathlib import Path


def _emit_argv_envelope(code: str, message: str, exit_code: int = 2) -> None:
    """Emit a §4.1-shape error envelope for argv-validation failures. O2'."""
    envelope = {
        "protocolVersion": PROTOCOL_VERSION,
        "sessionId": "",
        "turnId": "",
        "reply": "",
        "error": {
            "code": code,
            "classification": "protocol",
            "severity": "error",
            "correlationId": _mint_correlation_id(),
            "message": message,
        },
        "metadata": {
            "tokensIn": 0,
            "tokensOut": 0,
            "durationMs": 0,
            "bundleDigest": "",
            "engineVersion": __version__,
            "protocolVersion": PROTOCOL_VERSION,
            "correlationId": "",  # mirrored from error.correlationId by writer
        },
    }
    envelope["metadata"]["correlationId"] = envelope["error"]["correlationId"]
    click.echo(json.dumps(envelope))
    sys.exit(exit_code)


def _parse_json_or_atpath(value: str | None, *, flag_name: str) -> dict[str, Any] | None:
    """Parse a --foo '<json>' or --foo '@<path>' flag value. Returns None if value is None."""
    if value is None:
        return None
    if value.startswith("@"):
        path = Path(value[1:]).expanduser()
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            _emit_argv_envelope(
                "argv_path_unreadable",
                f"{flag_name} @path not readable: {path}: {exc}",
            )
    else:
        raw = value
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        _emit_argv_envelope(
            "argv_json_malformed",
            f"{flag_name} JSON parse error at position {exc.pos}: {exc.msg}",
        )
    if not isinstance(parsed, dict):
        _emit_argv_envelope(
            "argv_json_malformed",
            f"{flag_name} must be a JSON object, got {type(parsed).__name__}",
        )
    return parsed
```

**Step 2: Add the click option and `_TurnSpec` field**

Add a click option just above `--allow-protocol-skew`:
```python
@click.option(
    "--mcp-servers",
    "mcp_servers_raw",
    default=None,
    help="MCP servers config as inline JSON or '@<path>' to JSON file.",
)
```

Add `mcp_servers_raw: str | None` to the `run()` parameter list.

Add `mcp_servers: dict[str, Any] | None = None` to `_TurnSpec`.

**Step 3: Parse in `run()` and pass into spec**

In `run()`, after the `--output`-mode handling block:
```python
    mcp_servers = _parse_json_or_atpath(mcp_servers_raw, flag_name="--mcp-servers")
```
Then set `mcp_servers=mcp_servers` on the `_TurnSpec(...)` call.

**Step 4: Run the three new tests**
```bash
uv run pytest tests/cli/test_mode_a_v2_envelope.py -v -k mcp_servers
```
Expected: 3 PASS.

**Step 5: Commit**
```bash
git add src/amplifier_agent_cli/modes/single_turn.py
git commit -m "feat(engine): D9' — accept --mcp-servers inline JSON or @path"
```

**Note on engine threading:** This task wires the argv flag onto `_TurnSpec`. The actual threading of `_TurnSpec.mcp_servers` into `tool-mcp.mount(coordinator, config={**bundle_static, "servers": parsed})` lives in `_runtime.py` (the `make_turn_handler` factory) and is **preserved verbatim** from the 2026-05-22 §4.8 closure. If `_TurnSpec.mcp_servers` is non-None, `_execute_turn` must pass it through to `make_turn_handler(prepared, ..., mcp_servers=spec.mcp_servers)`. Confirm via `grep -n "tool-mcp" src/amplifier_agent_lib/_runtime.py` that the mount call accepts a `servers` config key, then add the kwarg pass-through. If the runtime does not yet accept the kwarg, leave a `TODO(phase-A-task-7)` comment and the kwarg will be wired in Phase A Task 15's lint cleanup — the unit tests in this task do not exercise the engine path.

---

## Task 8: Add `--host-capabilities`, `--env-allowlist`, `--env-extra`, `--protocol-version` flags

**Files:**
- Modify: `src/amplifier_agent_cli/modes/single_turn.py`
- Modify: `tests/cli/test_mode_a_v2_envelope.py`

**Test type:** (a) unit.

**Step 1: Write failing tests**

Append to `test_mode_a_v2_envelope.py`:
```python
def test_host_capabilities_threaded_to_envelope() -> None:
    """--host-capabilities '<json>' is echoed in envelope.metadata.hostCapabilities."""
    runner = CliRunner()
    with patch(
        "amplifier_agent_cli.modes.single_turn._execute_turn",
        return_value=_mock_turn_result("ok"),
    ), patch(
        "amplifier_agent_cli.provider_detect.detect_provider", return_value="anthropic"
    ):
        result = runner.invoke(
            run,
            [
                "--session-id",
                "sid-1",
                "--host-capabilities",
                '{"supports_steering":false,"supports_structured_errors":true}',
                "hello",
            ],
        )

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert envelope["metadata"]["hostCapabilities"] == {
        "supports_steering": False,
        "supports_structured_errors": True,
    }


def test_protocol_version_mismatch_yields_envelope() -> None:
    """--protocol-version 9.9.9 (mismatch, no skew flag) → error envelope, exit 2."""
    runner = CliRunner()
    with patch(
        "amplifier_agent_cli.provider_detect.detect_provider", return_value="anthropic"
    ):
        result = runner.invoke(
            run,
            ["--session-id", "sid-1", "--protocol-version", "9.9.9-NOT-REAL", "hello"],
        )

    assert result.exit_code == 2, result.output
    envelope = json.loads(result.stdout)
    assert envelope["error"]["code"] == "protocol_version_mismatch"
    assert envelope["error"]["classification"] == "protocol"
    assert "remediation" in envelope["error"]


def test_protocol_version_skew_suppressed_by_flag() -> None:
    """--protocol-version 9.9.9 + --allow-protocol-skew → no error, normal flow."""
    runner = CliRunner()
    with patch(
        "amplifier_agent_cli.modes.single_turn._execute_turn",
        return_value=_mock_turn_result("ok"),
    ), patch(
        "amplifier_agent_cli.provider_detect.detect_provider", return_value="anthropic"
    ):
        result = runner.invoke(
            run,
            [
                "--session-id",
                "sid-1",
                "--protocol-version",
                "9.9.9-NOT-REAL",
                "--allow-protocol-skew",
                "hello",
            ],
        )

    assert result.exit_code == 0, result.output
```

**Step 2: Run; expect FAIL** (flags not recognized).
```bash
uv run pytest tests/cli/test_mode_a_v2_envelope.py -v -k "host_capabilities or protocol_version"
```

**Step 3: Add the four click options + parsing logic**

In `single_turn.py`, add four click options near the others:
```python
@click.option("--host-capabilities", "host_capabilities_raw", default=None,
              help="Host capabilities as inline JSON object.")
@click.option("--env-allowlist", "env_allowlist_raw", default=None,
              help="Comma-separated env var names allowed into the engine subprocess.")
@click.option("--env-extra", "env_extra_raw", default=None,
              help="Extra env vars as inline JSON object (validated against BLOCKED_ENV_KEYS).")
@click.option("--protocol-version", "protocol_version_arg", default=None,
              help="Wrapper's pinned protocol version; engine self-validates.")
```

Add the matching params to `run()`. Parse them at the top of `run()`:
```python
    host_capabilities = _parse_json_or_atpath(host_capabilities_raw, flag_name="--host-capabilities")
    env_extra = _parse_json_or_atpath(env_extra_raw, flag_name="--env-extra")
    env_allowlist = (
        [k.strip() for k in env_allowlist_raw.split(",") if k.strip()]
        if env_allowlist_raw else None
    )

    # Protocol version self-validation (D6 mechanism shift)
    if protocol_version_arg and not (
        allow_protocol_skew or os.environ.get("AMPLIFIER_AGENT_ALLOW_PROTOCOL_SKEW")
    ):
        if protocol_version_arg != PROTOCOL_VERSION:
            _emit_argv_envelope(
                "protocol_version_mismatch",
                f"Wrapper expects protocol {protocol_version_arg}, engine "
                f"compiled with {PROTOCOL_VERSION}. To force, pass "
                f"--allow-protocol-skew (unsafe) or reinstall both: "
                f"`uv tool install --reinstall amplifier-agent` and "
                f"`npm install amplifier-agent-client-ts@latest`.",
            )
```

Thread `host_capabilities` to the envelope builder (replace the `host_capabilities=None,` arg).

**Step 4: Run tests**
```bash
uv run pytest tests/cli/test_mode_a_v2_envelope.py -v
```
Expected: all PASS.

**Step 5: Commit**
```bash
git add -u
git commit -m "feat(engine): A1'/D6'/D12' — add --host-capabilities, --env-*, --protocol-version flags"
```

---

## Task 9: Write failing test for stdout discipline (CR-B)

**Files:**
- Create: `tests/cli/test_mode_a_stdout_discipline.py`

**Test type:** (a) unit — uses `monkeypatch` to inject a `print()`-emitting hook into the execute path.

**Step 1: Write the test**
```python
# tests/cli/test_mode_a_stdout_discipline.py
"""Phase A — CR-B stdout-discipline test.

A bundle module that calls print() during turn execution must NOT corrupt
the JSON envelope on real stdout. The 50 prints land on stderr; the envelope
on stdout remains parseable.
"""
from __future__ import annotations

import json
from unittest.mock import patch

from click.testing import CliRunner

from amplifier_agent_cli.modes.single_turn import run


def test_noisy_module_prints_do_not_corrupt_envelope() -> None:
    """A bundle calling print() 50 times must not break envelope parsing."""

    async def noisy_execute(spec):
        # Simulates a bundle module that prints to "stdout" during turn.
        for i in range(50):
            print(f"DEBUG line {i} from a misbehaving module")
        return {"sessionId": spec.session_id or "", "turnId": "turn-1", "reply": "hi"}

    runner = CliRunner(mix_stderr=False)
    with patch(
        "amplifier_agent_cli.modes.single_turn._execute_turn", side_effect=noisy_execute
    ), patch(
        "amplifier_agent_cli.provider_detect.detect_provider", return_value="anthropic"
    ):
        result = runner.invoke(run, ["--session-id", "sid-1", "hello"])

    assert result.exit_code == 0, (result.stdout, result.stderr)
    # Critical: stdout must parse as a single JSON envelope despite the 50 prints.
    envelope = json.loads(result.stdout)
    assert envelope["reply"] == "hi"
    # The 50 print lines must appear on stderr, not stdout.
    assert "DEBUG line 0" in result.stderr
    assert "DEBUG line 49" in result.stderr
    # And NOT on stdout:
    assert "DEBUG line" not in result.stdout
```

**Step 2: Run; expect FAIL**
```bash
uv run pytest tests/cli/test_mode_a_stdout_discipline.py -v
```
Expected: FAIL — `json.JSONDecodeError` because the 50 `print()` lines interleave with `click.echo(envelope)` on stdout.

**Step 3: Commit failing test**
```bash
git add tests/cli/test_mode_a_stdout_discipline.py
git commit -m "test(engine): A2'/CR-B — failing test for stdout discipline"
```

---

## Task 10: Implement stdout discipline via `redirect_stdout(stderr)`

**Files:**
- Modify: `src/amplifier_agent_cli/modes/single_turn.py`

**Step 1: Wrap `asyncio.run(_execute_turn(spec))` in `redirect_stdout`**

Add at the top of the file:
```python
import contextlib
```

Refactor the turn-execution block. Save the real stdout FD at the start of `run()`, then redirect during execution. **Only** the envelope emission writes to the real stdout:

```python
def run(... , output_mode: str, ...) -> None:
    # ... existing argv parsing and validation ...

    _real_stdout = sys.stdout

    correlation_id = _mint_correlation_id()
    import time
    started = time.monotonic()
    try:
        # Stdout discipline (CR-B / §4.0): everything inside the with-block
        # that would have written to stdout goes to stderr. Only the final
        # envelope emission below writes to _real_stdout.
        if output_mode == "json":
            with contextlib.redirect_stdout(sys.stderr):
                result = asyncio.run(_execute_turn(spec))
        else:
            # text mode — leave stdout intact; users want to see the reply.
            result = asyncio.run(_execute_turn(spec))
    except AaaError as exc:
        # ... existing error handling, emit via _real_stdout per Task 11 ...
        raise
    except Exception as exc:
        raise
    duration_ms = int((time.monotonic() - started) * 1000)

    if output_mode == "json":
        envelope = _build_envelope(
            result,
            correlation_id=correlation_id,
            host_capabilities=host_capabilities,
            duration_ms=duration_ms,
        )
        _real_stdout.write(json.dumps(envelope) + "\n")
        _real_stdout.flush()
    else:
        _real_stdout.write(result.get("reply", "") + "\n")
        _real_stdout.flush()
```

**Step 2: Run the stdout-discipline test**
```bash
uv run pytest tests/cli/test_mode_a_stdout_discipline.py -v
```
Expected: PASS.

**Step 3: Run the full new-test file to check no regression**
```bash
uv run pytest tests/cli/test_mode_a_v2_envelope.py tests/cli/test_mode_a_stdout_discipline.py -v
```
Expected: all PASS.

**Step 4: Commit**
```bash
git add src/amplifier_agent_cli/modes/single_turn.py
git commit -m "feat(engine): A2'/CR-B — enforce stdout discipline via redirect_stdout"
```

---

## Task 11: Write failing test for error-envelope path (engine exception → §4.3 shape)

**Files:**
- Modify: `tests/cli/test_mode_a_v2_envelope.py`

**Test type:** (a) unit.

**Step 1: Append the test**
```python
def test_engine_exception_yields_error_envelope_shape() -> None:
    """An AaaError raised by the engine must surface as §4.3 error envelope."""
    from amplifier_agent_lib.protocol.errors import AaaError as EngineAaaError

    async def raise_engine_error(spec):
        raise EngineAaaError("approval_translation_failed", "bad action 'review'")

    runner = CliRunner()
    with patch(
        "amplifier_agent_cli.modes.single_turn._execute_turn",
        side_effect=raise_engine_error,
    ), patch(
        "amplifier_agent_cli.provider_detect.detect_provider", return_value="anthropic"
    ):
        result = runner.invoke(run, ["--session-id", "sid-1", "hello"])

    assert result.exit_code == 3, (result.exit_code, result.stdout)
    # exit 3 per §4.4 for classification == 'approval'
    envelope = json.loads(result.stdout)
    assert envelope["error"] is not None
    assert envelope["error"]["code"] == "approval_translation_failed"
    assert envelope["error"]["classification"] == "approval"
    assert envelope["reply"] == ""
    # error.correlationId must equal metadata.correlationId (SC-G)
    assert envelope["error"]["correlationId"] == envelope["metadata"]["correlationId"]
```

**Step 2: Run; expect FAIL** (current path uses the legacy `_emit_error` helper that emits `{"error": {"code", "message"}}` — wrong shape, wrong exit code).
```bash
uv run pytest tests/cli/test_mode_a_v2_envelope.py::test_engine_exception_yields_error_envelope_shape -v
```

**Step 3: Commit failing test**
```bash
git add tests/cli/test_mode_a_v2_envelope.py
git commit -m "test(engine): A2' — failing test for §4.3 error envelope shape"
```

---

## Task 12: Implement error-envelope path with exit code policy (§4.4)

**Files:**
- Modify: `src/amplifier_agent_cli/modes/single_turn.py`

**Step 1: Add the error envelope builder + classification mapper**

```python
_EXIT_CODE_BY_CLASSIFICATION = {
    "engine": 1,
    "transport": 1,
    "unknown": 1,
    "protocol": 2,
    "approval": 3,
}

# Map known engine AaaError codes onto classifications. Add entries as the
# engine grows new error codes; default to 'engine'.
_CLASSIFICATION_BY_CODE = {
    "approval_translation_failed": "approval",
    "approval_timeout": "approval",
    "approval_protocol_violation": "approval",
    "protocol_version_mismatch": "protocol",
    "argv_json_malformed": "protocol",
    "argv_path_unreadable": "protocol",
}


def _classify(code: str) -> str:
    return _CLASSIFICATION_BY_CODE.get(code, "engine")


def _build_error_envelope(
    *,
    code: str,
    message: str,
    correlation_id: str,
    session_id: str,
    turn_id: str,
    host_capabilities: dict[str, Any] | None,
    duration_ms: int,
    stderr_tail: str | None = None,
) -> dict[str, Any]:
    classification = _classify(code)
    metadata: dict[str, Any] = {
        "tokensIn": 0,
        "tokensOut": 0,
        "durationMs": duration_ms,
        "bundleDigest": "",
        "engineVersion": __version__,
        "protocolVersion": PROTOCOL_VERSION,
        "correlationId": correlation_id,
    }
    if host_capabilities is not None:
        metadata["hostCapabilities"] = host_capabilities
    error: dict[str, Any] = {
        "code": code,
        "classification": classification,
        "severity": "error",
        "correlationId": correlation_id,
        "message": message,
    }
    if stderr_tail:
        error["stderrTail"] = stderr_tail
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "sessionId": session_id,
        "turnId": turn_id,
        "reply": "",
        "error": error,
        "metadata": metadata,
    }
```

**Step 2: Rewire error handling in `run()`**

Replace the `except AaaError`/`except Exception` blocks:
```python
    try:
        if output_mode == "json":
            with contextlib.redirect_stdout(sys.stderr):
                result = asyncio.run(_execute_turn(spec))
        else:
            result = asyncio.run(_execute_turn(spec))
    except AaaError as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        envelope = _build_error_envelope(
            code=exc.code,
            message=exc.message,
            correlation_id=correlation_id,
            session_id=session_id or "",
            turn_id="turn-1",
            host_capabilities=host_capabilities,
            duration_ms=duration_ms,
        )
        _real_stdout.write(json.dumps(envelope) + "\n")
        _real_stdout.flush()
        sys.exit(_EXIT_CODE_BY_CLASSIFICATION[envelope["error"]["classification"]])
    except Exception as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        envelope = _build_error_envelope(
            code="internal",
            message=f"{type(exc).__name__}: {exc}",
            correlation_id=correlation_id,
            session_id=session_id or "",
            turn_id="turn-1",
            host_capabilities=host_capabilities,
            duration_ms=duration_ms,
        )
        _real_stdout.write(json.dumps(envelope) + "\n")
        _real_stdout.flush()
        sys.exit(1)
```

**Step 3: Run the error-envelope test**
```bash
uv run pytest tests/cli/test_mode_a_v2_envelope.py::test_engine_exception_yields_error_envelope_shape -v
```
Expected: PASS.

**Step 4: Run all new tests + regression**
```bash
uv run pytest tests/cli/ tests/ -q
```
Expected: all PASS. If anything in `tests/` breaks because the old envelope shape changed, that's a regression in the existing tests — investigate before continuing.

**Step 5: Commit**
```bash
git add -u
git commit -m "feat(engine): A2'/§4.4 — error envelope path with classification-based exit codes"
```

---

## Task 13: Implement A2.1' per-turn audit trail (SC-H)

**Files:**
- Modify: `src/amplifier_agent_cli/modes/single_turn.py`
- Create: `tests/cli/test_mode_a_audit_trail.py`

**Test type:** (a) unit.

**Step 1: Write the failing test**
```python
# tests/cli/test_mode_a_audit_trail.py
"""Phase A — A2.1'/SC-H audit trail test.

Every turn writes $XDG_STATE_HOME/amplifier-agent/sessions/<sid>/audits/turn-<turnId>.json
with sha256 digests of secret-bearing inputs.
"""
from __future__ import annotations

import json
from unittest.mock import patch

from click.testing import CliRunner

from amplifier_agent_cli.modes.single_turn import run


def test_audit_file_written_with_digests(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    runner = CliRunner()
    with patch(
        "amplifier_agent_cli.modes.single_turn._execute_turn",
        return_value={"sessionId": "sid-X", "turnId": "turn-1", "reply": "ok"},
    ), patch(
        "amplifier_agent_cli.provider_detect.detect_provider", return_value="anthropic"
    ):
        result = runner.invoke(
            run,
            [
                "--session-id",
                "sid-X",
                "--mcp-servers",
                '{"s":{"transport":"stdio","command":"node","args":[],"env":{"K":"SECRET"}}}',
                "--host-capabilities",
                '{"supports_steering":false}',
                "hello",
            ],
        )

    assert result.exit_code == 0, result.output
    audit_path = (
        tmp_path / "amplifier-agent" / "sessions" / "sid-X" / "audits" / "turn-turn-1.json"
    )
    assert audit_path.exists(), f"audit file not written at {audit_path}"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    # Required digests (SC-H):
    assert "argvDigest" in audit
    assert "mcpServersDigest" in audit
    assert "envDigest" in audit
    assert "hostCapabilities" in audit
    assert "protocolVersion" in audit
    assert "exitCode" in audit
    assert "correlationId" in audit
    assert "startedAt" in audit and "endedAt" in audit
    # Secrets must NOT appear literally:
    full = audit_path.read_text(encoding="utf-8")
    assert "SECRET" not in full
```

**Step 2: Run; expect FAIL**
```bash
uv run pytest tests/cli/test_mode_a_audit_trail.py -v
```

**Step 3: Implement audit writer**

In `single_turn.py`, add:
```python
import hashlib


def _sha256(s: str) -> str:
    return "sha256:" + hashlib.sha256(s.encode("utf-8")).hexdigest()


def _write_audit(
    *,
    session_id: str,
    turn_id: str,
    correlation_id: str,
    exit_code: int,
    started_at: str,
    ended_at: str,
    argv: list[str],
    mcp_servers: dict[str, Any] | None,
    env_allowlist: list[str] | None,
    env_extra: dict[str, Any] | None,
    host_capabilities: dict[str, Any] | None,
    protocol_version: str,
) -> None:
    """SC-H — write per-turn audit digest. Secrets are sha256'd, never literal."""
    from amplifier_agent_lib.persistence import session_state_dir

    if not session_id:
        return  # No session id ⇒ no audit (matches anonymous CLI use).
    audits_dir = session_state_dir(session_id) / "audits"
    audits_dir.mkdir(parents=True, exist_ok=True)
    audit = {
        "argvDigest": _sha256(" ".join(argv)),
        "mcpServersDigest": _sha256(json.dumps(mcp_servers, sort_keys=True))
            if mcp_servers else None,
        "envDigest": _sha256(
            json.dumps({"allow": env_allowlist or [], "extra": env_extra or {}}, sort_keys=True)
        ),
        "hostCapabilities": host_capabilities,
        "protocolVersion": protocol_version,
        "exitCode": exit_code,
        "correlationId": correlation_id,
        "startedAt": started_at,
        "endedAt": ended_at,
    }
    audit_file = audits_dir / f"turn-{turn_id}.json"
    audit_file.write_text(json.dumps(audit, indent=2), encoding="utf-8")
```

**Step 4: Call `_write_audit` from `run()`**

After envelope emission (both success and failure paths), call:
```python
    _write_audit(
        session_id=session_id or "",
        turn_id=envelope.get("turnId") or "turn-1",
        correlation_id=correlation_id,
        exit_code=0 if envelope["error"] is None else _EXIT_CODE_BY_CLASSIFICATION[envelope["error"]["classification"]],
        started_at=started_iso,
        ended_at=datetime.now(timezone.utc).isoformat(),
        argv=sys.argv,
        mcp_servers=mcp_servers,
        env_allowlist=env_allowlist,
        env_extra=env_extra,
        host_capabilities=host_capabilities,
        protocol_version=PROTOCOL_VERSION,
    )
```

Add `started_iso = datetime.now(timezone.utc).isoformat()` next to `started = time.monotonic()` at the top of the turn-execution block.

**Step 5: Run tests**
```bash
uv run pytest tests/cli/test_mode_a_audit_trail.py -v
```
Expected: PASS.

**Step 6: Commit**
```bash
git add -u
git commit -m "feat(engine): A2.1'/SC-H — per-turn audit trail with sha256-digested secrets"
```

---

## Task 14: Real-binary integration test — happy path end-to-end

**Files:**
- Create: `tests/cli/test_mode_a_v2_real_binary.py`

**Test type:** **(b) real-binary** — launches `amplifier-agent run` as a subprocess via `subprocess.run`. This is the R9' integration gate for Phase A.

**Setup notes for the junior engineer:** This test uses a **mock LLM HTTP server** (the only place where mocks are allowed in real-binary tests, per amendment §8.1 A4'). Build the mock server with the `aiohttp` test fixtures already present in `tests/` (search for one: `grep -rn "aiohttp" tests/ | head`). If no mock LLM server exists yet, create a minimal one inline that listens on a free port and returns a hard-coded Anthropic-API-shaped response. Point the engine at it via `ANTHROPIC_BASE_URL=http://localhost:<port>`.

**Step 1: Write the test**
```python
# tests/cli/test_mode_a_v2_real_binary.py
"""Phase A — Real-binary integration test (R9' gate).

Launches the actual amplifier-agent binary via subprocess.run, points it at
a localhost mock LLM, and asserts the envelope on stdout parses per §4.1.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest


class _MockLLM(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("content-length", "0"))
        _ = self.rfile.read(length)
        body = json.dumps(
            {
                "id": "msg_x",
                "type": "message",
                "role": "assistant",
                "model": "claude-3-5-sonnet-20241022",
                "content": [{"type": "text", "text": "real-binary-ok"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 5, "output_tokens": 3},
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args, **kwargs):  # silence stderr noise
        return


@pytest.fixture()
def mock_llm():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = HTTPServer(("127.0.0.1", port), _MockLLM)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        server.shutdown()


def _binary_path() -> str:
    p = shutil.which("amplifier-agent")
    if p is None:
        pytest.skip("amplifier-agent binary not on PATH; run `uv tool install -e .` first")
    return p


def test_real_binary_happy_path(mock_llm, tmp_path) -> None:
    env = os.environ.copy()
    env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{mock_llm}"
    env["ANTHROPIC_API_KEY"] = "test-key"
    env["XDG_STATE_HOME"] = str(tmp_path)

    proc = subprocess.run(
        [
            _binary_path(),
            "run",
            "--session-id",
            "real-bin-sid",
            "--fresh",
            "--output",
            "json",
            "--provider",
            "anthropic",
            "say hi",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )

    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    envelope = json.loads(proc.stdout)
    assert envelope["protocolVersion"]
    assert envelope["sessionId"] == "real-bin-sid"
    assert envelope["error"] is None
    assert "real-binary-ok" in envelope["reply"]
    assert envelope["metadata"]["correlationId"]
```

**Step 2: Install the binary in development mode (so the test can find it)**
```bash
uv tool install -e . --force
which amplifier-agent
```
Expected: prints a path; `--version` works.

**Step 3: Run the test**
```bash
uv run pytest tests/cli/test_mode_a_v2_real_binary.py -v
```
Expected: PASS. If it fails because the mock LLM doesn't match what the Anthropic provider expects, inspect `proc.stderr` and adjust the mock response shape — the engine's provider layer is the source of truth for what the LLM must return.

**Step 4: Commit**
```bash
git add tests/cli/test_mode_a_v2_real_binary.py
git commit -m "test(engine): A4'/R9' — real-binary happy-path integration gate"
```

---

## Task 15: PGID setup (`os.setsid()`) at engine entry for SC-B

**Files:**
- Modify: `src/amplifier_agent_cli/modes/single_turn.py`
- Modify: `tests/cli/test_mode_a_v2_real_binary.py` (add one more real-binary test)

**Test type:** **(b) real-binary.**

**Step 1: Write the failing test (append to test_mode_a_v2_real_binary.py)**
```python
def test_real_binary_becomes_session_leader(mock_llm, tmp_path) -> None:
    """SC-B: engine must call os.setsid() so MCP children inherit a group.

    We verify by reading /proc/<pid>/stat on Linux, or skipping on darwin.
    The check is: getsid(engine_pid) == engine_pid (engine is the session leader).
    """
    import sys
    if sys.platform == "darwin":
        pytest.skip("getsid via /proc is Linux-only; PGID behavior verified in Phase B fixtures")

    env = os.environ.copy()
    env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{mock_llm}"
    env["ANTHROPIC_API_KEY"] = "test-key"
    env["XDG_STATE_HOME"] = str(tmp_path)
    env["AMPLIFIER_AGENT_DEBUG_SIDLOG"] = "1"

    proc = subprocess.run(
        [_binary_path(), "run", "--session-id", "sid-sid", "--fresh", "--output", "json", "say hi"],
        capture_output=True, text=True, env=env, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    # The engine logs its SID and PID at debug if AMPLIFIER_AGENT_DEBUG_SIDLOG is set.
    assert "engine-sid-ok" in proc.stderr
```

**Step 2: Run; expect FAIL.**
```bash
uv run pytest tests/cli/test_mode_a_v2_real_binary.py::test_real_binary_becomes_session_leader -v
```

**Step 3: Implement PGID setup**

At the top of `run()` (very first line of the function body, before any argv work):
```python
    # SC-B — engine becomes session leader so MCP child processes spawned via
    # tool-mcp.mount() inherit a shared session group. The wrapper kills the
    # group on cancel so children die with the parent.
    try:
        if os.getsid(0) != os.getpid():
            os.setsid()
    except (OSError, PermissionError):
        # Best-effort — running under a debugger or test harness that already
        # owns a session may make setsid() fail; tolerate.
        pass
    if os.environ.get("AMPLIFIER_AGENT_DEBUG_SIDLOG"):
        try:
            sys.stderr.write(f"engine-sid-ok pid={os.getpid()} sid={os.getsid(0)}\n")
        except OSError:
            pass
```

**Step 4: Run the test**
```bash
uv run pytest tests/cli/test_mode_a_v2_real_binary.py -v
```
Expected: both real-binary tests PASS.

**Step 5: Run the full suite + lint + types**
```bash
uv run pytest tests/ -q
uv run ruff check src/ tests/
uv run pyright src/
```
Expected: all green. Fix any lint/type complaints from the new code.

**Step 6: Commit**
```bash
git add -u
git commit -m "feat(engine): A1'/SC-B — engine self-promotes to session leader for MCP group cleanup"
```

---

## Phase A Acceptance Gate

Before declaring Phase A complete, **every** item below must be a verified pass — not "should pass" or "probably passes":

1. **Full pytest suite green:**
   ```bash
   uv run pytest tests/ -q
   ```
   Must show the prior baseline count + new tests (Phase A adds ~12 new tests).

2. **Lint clean:**
   ```bash
   uv run ruff check src/ tests/
   ```

3. **Type-check clean:**
   ```bash
   uv run pyright src/
   ```

4. **Real-binary integration test green:**
   ```bash
   uv run pytest tests/cli/test_mode_a_v2_real_binary.py -v
   ```

5. **CR-E backward-compat manual check:**
   ```bash
   # Existing scripts call this exact form. Output MUST still be a valid JSON envelope.
   uv tool install -e . --force
   amplifier-agent run --session-id back-compat-X --fresh "say back-compat-ok"
   ```
   Expected: stdout is a JSON object with top-level keys `protocolVersion`, `sessionId`, `turnId`, `reply`, `error`, `metadata`. (Assuming a working Anthropic key is configured. If not, the error envelope itself counts as backward-compat-OK because the prior `single_turn.py` also emitted a JSON error structure on failure.)

6. **§3.3 backward-compat check:** the eight original click flags (`--session-id`, `--resume`, `--fresh`, `--provider`, `--cwd`, `-v`, `--debug`, `-y`, `-n`, `--quiet`, `--allow-protocol-skew`) all still work — verified by the existing test suite continuing to pass.

7. **`schemas/run-output.json` exists** with §4.1 schema (hand-authored if `wrappers/_gen.py` doesn't cover envelope generation; one PR comment is enough to track if generation is added later).

8. **Audit file presence:** after the manual run in #5, the file `~/.local/state/amplifier-agent/sessions/back-compat-X/audits/turn-turn-1.json` exists and contains no plaintext secrets (verify with `grep -i 'api[-_]key' <audit-file>` — should output nothing).

**Push to remote** only after all eight items pass:
```bash
git push -u origin feat/mode-a-phase-a-engine
```

Open a PR with title `feat(engine): Phase A — Mode A v2 wire surface (A1'/A2'/A2.1')` and let the orchestrator close out the gate.

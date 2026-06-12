"""Tests for Mode A single-turn run command (Task 8).

Tests covering:
  1.  test_run_with_prompt_prints_json_to_stdout
  2.  test_run_passes_prompt_to_engine
  3.  test_run_y_flag_sets_approval_mode_yes
  4.  test_run_n_flag_sets_approval_mode_no
  5.  test_run_y_and_n_mutually_exclusive
  6.  test_run_default_approval_is_prompt_when_tty
  7.  test_run_fails_loudly_when_headless_without_policy (G3, replaces silent-deny pin)
  8.  test_run_quiet_flag_sets_display_quiet
  9.  test_run_verbose_flag_sets_display_verbose
  10. test_run_debug_flag_sets_display_debug
  11. test_run_session_id_and_resume_passed_to_engine
  12. test_run_fresh_flag_passed_to_engine
  13. test_run_missing_prompt_and_non_tty_fails_with_prompt_required
  14. test_run_no_provider_configured_errors
  15. test_run_engine_raising_aaa_error_returns_json_envelope

G3 additions:
  - test_run_fails_loudly_when_headless_without_policy
  - test_run_honors_host_config_approval_mode_yes_when_not_tty
  - test_run_honors_host_config_approval_mode_no_when_not_tty
  - test_run_argv_y_overrides_host_config_approval_mode_no
  - test_run_argv_n_overrides_host_config_approval_mode_yes
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from amplifier_agent_cli.__main__ import cli
from amplifier_agent_lib.protocol.errors import AaaError

# ---------------------------------------------------------------------------
# Provider env var constants
# ---------------------------------------------------------------------------

_PROVIDER_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_KEY",  # legacy alias, still accepted
    "OLLAMA_HOST",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _patch_execute_turn(
    *,
    reply: str = "stub",
    raises: Exception | None = None,
) -> tuple[Any, list]:
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


def _set_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set ANTHROPIC_API_KEY so provider detection returns 'anthropic'."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def runner() -> CliRunner:
    """Standard CliRunner for CLI tests."""
    return CliRunner()


# ---------------------------------------------------------------------------
# Test 1: run with prompt prints JSON to stdout
# ---------------------------------------------------------------------------


def test_run_with_prompt_prints_json_to_stdout(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """'run' with a prompt exits 0 and JSON reply is on stdout."""
    _set_anthropic(monkeypatch)
    patch_obj, _ = _patch_execute_turn(reply="hello!")
    with patch_obj:
        result = runner.invoke(cli, ["run", "-y", "hello!"])
    assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}. Output:\n{result.output}"
    parsed = json.loads(result.stdout)
    assert parsed["reply"] == "hello!"


# ---------------------------------------------------------------------------
# Test 2: run passes prompt to engine
# ---------------------------------------------------------------------------


def test_run_passes_prompt_to_engine(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_execute_turn is called with a spec whose prompt matches the CLI argument."""
    _set_anthropic(monkeypatch)
    patch_obj, captured = _patch_execute_turn()
    with patch_obj:
        result = runner.invoke(cli, ["run", "-y", "do the thing"])
    assert result.exit_code == 0
    assert len(captured) == 1
    assert captured[0].prompt == "do the thing"


# ---------------------------------------------------------------------------
# Test 3: -y flag sets approval mode to 'yes'
# ---------------------------------------------------------------------------


def test_run_y_flag_sets_approval_mode_yes(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Passing -y sets approval.mode == 'yes' in the _TurnSpec."""
    _set_anthropic(monkeypatch)
    patch_obj, captured = _patch_execute_turn()
    with patch_obj:
        result = runner.invoke(cli, ["run", "test", "-y"])
    assert result.exit_code == 0
    assert captured[0].approval.mode == "yes"


# ---------------------------------------------------------------------------
# Test 4: -n flag sets approval mode to 'no'
# ---------------------------------------------------------------------------


def test_run_n_flag_sets_approval_mode_no(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Passing -n sets approval.mode == 'no' in the _TurnSpec."""
    _set_anthropic(monkeypatch)
    patch_obj, captured = _patch_execute_turn()
    with patch_obj:
        result = runner.invoke(cli, ["run", "test", "-n"])
    assert result.exit_code == 0
    assert captured[0].approval.mode == "no"


# ---------------------------------------------------------------------------
# Test 5: -y and -n mutually exclusive
# ---------------------------------------------------------------------------


def test_run_y_and_n_mutually_exclusive(runner: CliRunner) -> None:
    """-y and -n together produce a usage error (exit 2)."""
    result = runner.invoke(cli, ["run", "test", "-y", "-n"])
    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# Test 6: default approval is 'prompt' when stdin is a TTY
# ---------------------------------------------------------------------------


def test_run_default_approval_is_prompt_when_tty(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With is_stdin_tty=True and no -y/-n, approval.mode is 'prompt'.

    Suppresses the conftest's session-wide ``AMPLIFIER_AGENT_CONFIG`` default
    so the TTY-based fallback path is exercised (otherwise host_config's
    ``approval.mode: yes`` would short-circuit before the TTY check).
    """
    _set_anthropic(monkeypatch)
    monkeypatch.delenv("AMPLIFIER_AGENT_CONFIG", raising=False)
    patch_obj, captured = _patch_execute_turn()
    with patch_obj:
        with patch("amplifier_agent_cli.modes.single_turn.is_stdin_tty", return_value=True):
            result = runner.invoke(cli, ["run", "test"])
    assert result.exit_code == 0
    assert captured[0].approval.mode == "prompt"


# ---------------------------------------------------------------------------
# Test 7 (G3): fail-fast when headless and no explicit approval policy.
#
# Replaces the prior test_run_default_approval_is_no_when_not_tty, which
# pinned the indefensible silent-deny default as "correct". The new behavior
# is to refuse the run with a §4.1 error envelope (exit 2) so monitoring
# sees a loud failure rather than a success-shaped no-op.
# ---------------------------------------------------------------------------


def test_run_fails_loudly_when_headless_without_policy(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G3: non-TTY + no -y/-n + no host_config.approval.mode → exit 2 with structured error.

    The previous behavior — silently defaulting to approval.mode='no' and
    succeeding with every tool call denied — was the worst failure mode for
    any headless host: monitoring saw green, no work happened, and there
    was no programmatic signal. The fix is to refuse the run loudly.

    Suppresses the conftest's session-wide ``AMPLIFIER_AGENT_CONFIG`` default
    so the fail-fast path is exercised (otherwise the conftest's host_config
    would short-circuit and the test would silently pass for the wrong reason).
    """
    _set_anthropic(monkeypatch)
    monkeypatch.delenv("AMPLIFIER_AGENT_CONFIG", raising=False)
    patch_obj, captured = _patch_execute_turn()
    with patch_obj:
        with patch("amplifier_agent_cli.modes.single_turn.is_stdin_tty", return_value=False):
            result = runner.invoke(cli, ["run", "test"])
    assert result.exit_code == 2, (
        f"Expected exit 2 (fail-fast on headless ambiguity), got {result.exit_code}.\nOutput:\n{result.output}"
    )
    # _execute_turn must NOT be reached — the run aborts during argv validation.
    assert len(captured) == 0, (
        f"Expected no _execute_turn call on fail-fast, got {len(captured)}.\nOutput:\n{result.output}"
    )
    # Envelope must be a parseable §4.1 JSON error envelope on stdout.
    parsed = json.loads(result.stdout)
    assert parsed["error"]["code"] == "approval_unconfigured"
    assert parsed["error"]["classification"] == "protocol"
    assert "remediation" in parsed["error"], (
        "G3 envelope must include a `remediation` hint pointing at -y / -n / host_config."
    )
    # Remediation must mention all three escape hatches so the operator knows their options.
    remediation = parsed["error"]["remediation"].lower()
    assert "-y" in remediation or "--yes" in remediation
    assert "approval" in remediation
    assert "mode" in remediation


def test_run_honors_host_config_approval_mode_yes_when_not_tty(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """G3: host_config.approval.mode='yes' lets a headless run proceed without -y."""
    _set_anthropic(monkeypatch)
    cfg = tmp_path / "host_config.json"
    cfg.write_text('{"approval": {"mode": "yes"}}', encoding="utf-8")
    patch_obj, captured = _patch_execute_turn()
    with patch_obj:
        with patch("amplifier_agent_cli.modes.single_turn.is_stdin_tty", return_value=False):
            result = runner.invoke(cli, ["run", "test", "--config", str(cfg)])
    assert result.exit_code == 0, (
        f"Expected exit 0 (host_config sets approval.mode), got {result.exit_code}.\nOutput:\n{result.output}"
    )
    assert captured[0].approval.mode == "yes"


def test_run_honors_host_config_approval_mode_no_when_not_tty(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """G3: host_config.approval.mode='no' is an explicit deny-all opt-in.

    Hosts that genuinely want deny-all in headless mode (e.g. test harnesses
    asserting "tools didn't run") can still get it — but only by saying so
    explicitly. The silent-default trap is closed.
    """
    _set_anthropic(monkeypatch)
    cfg = tmp_path / "host_config.json"
    cfg.write_text('{"approval": {"mode": "no"}}', encoding="utf-8")
    patch_obj, captured = _patch_execute_turn()
    with patch_obj:
        with patch("amplifier_agent_cli.modes.single_turn.is_stdin_tty", return_value=False):
            result = runner.invoke(cli, ["run", "test", "--config", str(cfg)])
    assert result.exit_code == 0
    assert captured[0].approval.mode == "no"


def test_run_argv_y_overrides_host_config_approval_mode_no(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """G3 precedence: argv -y wins over host_config.approval.mode='no'.

    Matches the precedence the engine uses everywhere else (argv flag >
    host_config > bundle/TTY default).
    """
    _set_anthropic(monkeypatch)
    cfg = tmp_path / "host_config.json"
    cfg.write_text('{"approval": {"mode": "no"}}', encoding="utf-8")
    patch_obj, captured = _patch_execute_turn()
    with patch_obj:
        with patch("amplifier_agent_cli.modes.single_turn.is_stdin_tty", return_value=False):
            result = runner.invoke(cli, ["run", "test", "-y", "--config", str(cfg)])
    assert result.exit_code == 0
    assert captured[0].approval.mode == "yes"


def test_run_argv_n_overrides_host_config_approval_mode_yes(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """G3 precedence: argv -n wins over host_config.approval.mode='yes'."""
    _set_anthropic(monkeypatch)
    cfg = tmp_path / "host_config.json"
    cfg.write_text('{"approval": {"mode": "yes"}}', encoding="utf-8")
    patch_obj, captured = _patch_execute_turn()
    with patch_obj:
        with patch("amplifier_agent_cli.modes.single_turn.is_stdin_tty", return_value=False):
            result = runner.invoke(cli, ["run", "test", "-n", "--config", str(cfg)])
    assert result.exit_code == 0
    assert captured[0].approval.mode == "no"


# ---------------------------------------------------------------------------
# Test 8: --quiet sets display verbosity to 'quiet'
# ---------------------------------------------------------------------------


def test_run_quiet_flag_sets_display_quiet(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--quiet sets display.verbosity == 'quiet' in the _TurnSpec."""
    _set_anthropic(monkeypatch)
    patch_obj, captured = _patch_execute_turn()
    with patch_obj:
        result = runner.invoke(cli, ["run", "-y", "test", "--quiet"])
    assert result.exit_code == 0
    assert captured[0].display.verbosity == "quiet"


# ---------------------------------------------------------------------------
# Test 9: --verbose sets display verbosity to 'verbose'
# ---------------------------------------------------------------------------


def test_run_verbose_flag_sets_display_verbose(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--verbose sets display.verbosity == 'verbose' in the _TurnSpec."""
    _set_anthropic(monkeypatch)
    patch_obj, captured = _patch_execute_turn()
    with patch_obj:
        result = runner.invoke(cli, ["run", "-y", "test", "--verbose"])
    assert result.exit_code == 0
    assert captured[0].display.verbosity == "verbose"


# ---------------------------------------------------------------------------
# Test 10: --debug sets display verbosity to 'debug'
# ---------------------------------------------------------------------------


def test_run_debug_flag_sets_display_debug(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--debug sets display.verbosity == 'debug' in the _TurnSpec."""
    _set_anthropic(monkeypatch)
    patch_obj, captured = _patch_execute_turn()
    with patch_obj:
        result = runner.invoke(cli, ["run", "-y", "test", "--debug"])
    assert result.exit_code == 0
    assert captured[0].display.verbosity == "debug"


# ---------------------------------------------------------------------------
# Test 11: --session-id and --resume passed to Engine.boot
# ---------------------------------------------------------------------------


def test_run_session_id_and_resume_passed_to_engine(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--session-id and --resume appear in the _TurnSpec."""
    _set_anthropic(monkeypatch)
    patch_obj, captured = _patch_execute_turn()
    with patch_obj:
        result = runner.invoke(cli, ["run", "-y", "test", "--session-id", "abc", "--resume"])
    assert result.exit_code == 0
    assert captured[0].session_id == "abc"
    assert captured[0].resume is True


# ---------------------------------------------------------------------------
# Test 12: --fresh flag passed to Engine.boot
# ---------------------------------------------------------------------------


def test_run_fresh_flag_passed_to_engine(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--fresh appears as True in the _TurnSpec."""
    _set_anthropic(monkeypatch)
    patch_obj, captured = _patch_execute_turn()
    with patch_obj:
        result = runner.invoke(cli, ["run", "-y", "test", "--session-id", "abc", "--fresh"])
    assert result.exit_code == 0
    assert captured[0].fresh is True


# ---------------------------------------------------------------------------
# Test 13: missing prompt + non-TTY stdin emits 'prompt_required'
# ---------------------------------------------------------------------------


def test_run_missing_prompt_and_non_tty_fails_with_prompt_required(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No prompt + non-TTY stdin: exit 2 with 'prompt_required' on stderr."""
    with patch("amplifier_agent_cli.modes.single_turn.is_stdin_tty", return_value=False):
        result = runner.invoke(cli, ["run"])
    assert result.exit_code == 2
    assert "prompt_required" in result.stderr or "prompt" in result.stderr.lower()


# ---------------------------------------------------------------------------
# Test 14 (removed): no-provider-configured envelope no longer surfaces here.
#   Removed as part of E5 (D6): the CLI no longer routes provider selection
#   through env-var auto-detection; the bundle default_provider fallback (or a
#   downstream provider-module error) replaces that contract.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Test 15: AaaError from engine → JSON envelope on stdout, exit 1
# ---------------------------------------------------------------------------


def test_run_engine_raising_aaa_error_returns_json_envelope(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AaaError raised by _execute_turn produces JSON error envelope on stdout, exit 1."""
    _set_anthropic(monkeypatch)
    patch_obj, _ = _patch_execute_turn(raises=AaaError(code="bundle_load_failed", message="bad bundle"))
    with patch_obj:
        result = runner.invoke(cli, ["run", "-y", "test"])
    assert result.exit_code == 1
    parsed = json.loads(result.stdout)
    assert parsed["error"]["code"] == "bundle_load_failed"
    assert parsed["error"]["message"] == "bad bundle"


# ---------------------------------------------------------------------------
# Test D2: --config flag is forwarded to load_config and host_config lands on _TurnSpec
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Test E1: --env-allowlist flag is removed (D10)
# ---------------------------------------------------------------------------


def test_env_allowlist_flag_is_removed(runner: CliRunner) -> None:
    """`--env-allowlist` is no longer a recognised CLI option (D10).

    The flag was subsumed by the host config layer (E1). Click must reject
    the unknown option with a non-zero exit code and a 'no such option'
    style diagnostic.
    """
    result = runner.invoke(cli, ["run", "--env-allowlist", "PATH", "hello"])
    assert result.exit_code != 0, (
        f"Expected non-zero exit because --env-allowlist is removed, got {result.exit_code}. Output:\n{result.output}"
    )
    haystack = (result.output or "").lower() + " " + str(result.exception or "").lower()
    assert "no such option" in haystack, (
        f"Expected click 'no such option' diagnostic, got:\n{result.output}\nException: {result.exception}"
    )


# ---------------------------------------------------------------------------
# Test E2: --env-extra flag is removed (D10)
# ---------------------------------------------------------------------------


def test_env_extra_flag_is_removed(runner: CliRunner) -> None:
    """`--env-extra` is no longer a recognised CLI option (D10).

    The flag was subsumed by the host config layer (E2). Click must reject
    the unknown option with a non-zero exit code and a 'no such option'
    style diagnostic.
    """
    result = runner.invoke(cli, ["run", "--env-extra", "{}", "hello"])
    assert result.exit_code != 0, (
        f"Expected non-zero exit because --env-extra is removed, got {result.exit_code}. Output:\n{result.output}"
    )
    haystack = (result.output or "").lower() + " " + str(result.exception or "").lower()
    assert "no such option" in haystack, (
        f"Expected click 'no such option' diagnostic, got:\n{result.output}\nException: {result.exception}"
    )


# ---------------------------------------------------------------------------
# Test E3: --allow-protocol-skew flag is removed (D10)
# ---------------------------------------------------------------------------


def test_allow_protocol_skew_flag_is_removed(runner: CliRunner) -> None:
    """`--allow-protocol-skew` is no longer a recognised CLI option (D10).

    The flag was subsumed by the host config layer (E3 / D10). Click must
    reject the unknown option with a non-zero exit code and a 'no such
    option' style diagnostic. Wrappers now express the (unsafe) override
    via ``allowProtocolSkew: true`` in the --config file.
    """
    result = runner.invoke(cli, ["run", "--allow-protocol-skew", "hello"])
    assert result.exit_code != 0, (
        f"Expected non-zero exit because --allow-protocol-skew is removed, got {result.exit_code}. Output:\n{result.output}"
    )
    haystack = (result.output or "").lower() + " " + str(result.exception or "").lower()
    assert "no such option" in haystack, (
        f"Expected click 'no such option' diagnostic, got:\n{result.output}\nException: {result.exception}"
    )


def test_run_loads_config_and_forwards_to_spec(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """--config <path> calls load_config(config_arg=<path>) and forwards the result to _TurnSpec."""
    _set_anthropic(monkeypatch)
    # Write a config file (load_config is patched below so contents are irrelevant for the call,
    # but the file must look real for click.Path() validation in case it gains exists=True later).
    cfg = tmp_path / "host.json"
    cfg.write_text("{}", encoding="utf-8")

    captured: dict[str, Any] = {}
    sentinel_host_config = {"defaults": {"approval": {"mode": "yes"}}}

    def _fake_load_config(config_arg=None):
        captured["arg"] = config_arg
        return sentinel_host_config

    patch_exec, exec_captured = _patch_execute_turn()
    with patch("amplifier_agent_cli.modes.single_turn.load_config", _fake_load_config), patch_exec:
        result = runner.invoke(cli, ["run", "-y", "--config", str(cfg), "hello"])

    assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}. Output:\n{result.output}"
    assert captured["arg"] == str(cfg)
    assert len(exec_captured) == 1
    assert exec_captured[0].host_config is sentinel_host_config


# ---------------------------------------------------------------------------
# Test E4: --skills-dir flag is no longer documented in `run --help` (D10)
# ---------------------------------------------------------------------------


def test_run_help_text_no_longer_documents_skills_dir(runner: CliRunner) -> None:
    """`run --help` must not advertise a `--skills-dir` option (D10 amendment).

    The per-turn argv surface for skill directories was closed the same way
    --env-allowlist, --env-extra, and --allow-protocol-skew were closed.
    Migration paths:
      - host_config ``skills:`` block (D11)
      - ``$AMPLIFIER_SKILLS_DIR`` environment variable (D13)

    This test guards against accidental reintroduction of the flag by
    inspecting the rendered help text rather than probing for an error;
    a documented-but-broken flag would slip past a 'no such option' check.
    """
    result = runner.invoke(cli, ["run", "--help"])
    assert result.exit_code == 0, f"Expected `run --help` to exit 0, got {result.exit_code}. Output:\n{result.output}"
    assert "--skills-dir" not in result.output, (
        "`--skills-dir` must not appear in `run --help`; the flag was removed "
        "and replaced by the host_config `skills:` block (D11) and "
        "$AMPLIFIER_SKILLS_DIR (D13). Help text:\n" + result.output
    )


# ---------------------------------------------------------------------------
# CLI flag removal guards: --provider, --model, --effort
#
# These three argv flags were removed in favor of host_config.provider.{module,config}.
# Every provider-related knob now goes through the config file; the engine has
# exactly ONE path for provider configuration. The guards below pin that
# contract two ways:
#
#   1. The flag does not appear in `run --help` output (catches documented-but-
#      broken regressions).
#   2. Passing the flag fails with click's "No such option" error (catches the
#      flag being silently re-added even if its help text is suppressed).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("flag", ["--provider", "--model", "--effort"])
def test_run_help_does_not_expose_removed_provider_flag(runner: CliRunner, flag: str) -> None:
    """--provider, --model, --effort must not appear in `run --help`.

    Removed in favor of host_config.provider.{module,config}. host_config.json
    is the single source of truth for provider configuration.
    """
    result = runner.invoke(cli, ["run", "--help"])
    assert result.exit_code == 0, f"Expected `run --help` to exit 0, got {result.exit_code}. Output:\n{result.output}"
    assert flag not in result.output, (
        f"{flag} must not appear in `run --help`; it was removed in favor of "
        f"host_config.provider.{{module,config}}. Help text:\n{result.output}"
    )


@pytest.mark.parametrize(
    ("flag", "value"),
    [
        ("--provider", "anthropic"),
        ("--model", "claude-sonnet-4-5"),
        ("--effort", "high"),
    ],
)
def test_run_rejects_removed_provider_flag_as_unknown_option(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    flag: str,
    value: str,
) -> None:
    """Passing --provider/--model/--effort fails with click's `No such option`.

    Catches the flag being silently re-added even if its help text is suppressed
    (e.g. `hidden=True`).
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    result = runner.invoke(cli, ["run", flag, value, "hello"])
    assert result.exit_code != 0, (
        f"Expected non-zero exit when passing removed flag {flag}, got {result.exit_code}. Output:\n{result.output}"
    )
    assert "No such option" in result.output, f"Expected click 'No such option' error for {flag}, got:\n{result.output}"


# ---------------------------------------------------------------------------
# host_config.provider.config -- single source of truth for provider configuration
#
# These tests pin the contract that all provider configuration knobs flow from
# the host config file through the engine into mount_plan["providers"][0]["config"],
# with no CLI-flag override path.
# ---------------------------------------------------------------------------


def _write_host_config(tmp_path: Path, payload: dict[str, Any]) -> str:
    """Write a host config JSON file and return its path as a string."""
    cfg = tmp_path / "host_config.json"
    cfg.write_text(json.dumps(payload), encoding="utf-8")
    return str(cfg)


def _patch_engine_boot(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch load_and_prepare_cached / Engine / make_turn_handler / _write_audit.

    Returns a captured dict that will have ``captured["prepared"]`` set once
    ``_execute_turn`` runs, so the test can inspect mount_plan["providers"].
    """
    from types import SimpleNamespace

    import amplifier_agent_cli.modes.single_turn as st

    captured: dict[str, Any] = {}

    async def fake_prepare(*, aaa_version: str):
        prepared = SimpleNamespace(mount_plan={})
        captured["prepared"] = prepared
        return prepared

    class FakeEngine:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def boot(self, params: Any, *, bundle_override: Any = None) -> None:
            pass

        async def submit_turn(self, params: Any) -> dict[str, Any]:
            return {"reply": "ok", "turnId": "turn-1"}

        async def shutdown(self) -> None:
            pass

    monkeypatch.setattr(st, "load_and_prepare_cached", fake_prepare)
    monkeypatch.setattr(st, "Engine", FakeEngine)
    monkeypatch.setattr(st, "make_turn_handler", lambda prepared, **kwargs: object())
    monkeypatch.setattr(st, "_write_audit", lambda **_: None)
    return captured


def test_host_config_provider_module_selects_provider(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``provider.module`` in host_config selects the provider (no --provider flag)."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
    config_path = _write_host_config(tmp_path, {"provider": {"module": "openai"}})
    captured = _patch_engine_boot(monkeypatch)

    result = runner.invoke(cli, ["run", "--config", config_path, "-y", "--output", "text", "hello"])

    assert result.exit_code == 0, (
        f"Expected exit 0, got {result.exit_code}. Output:\n{result.output}\nException: {result.exception}"
    )
    providers = captured["prepared"].mount_plan["providers"]
    assert providers[0]["module"] == "provider-openai", f"Expected provider-openai, got {providers[0]['module']!r}"


def test_host_config_provider_config_default_model_lands_in_mount_plan(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``provider.config.default_model`` lands in mount_plan[providers][0][config]."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    config_path = _write_host_config(
        tmp_path,
        {"provider": {"module": "anthropic", "config": {"default_model": "claude-sonnet-4-5"}}},
    )
    captured = _patch_engine_boot(monkeypatch)

    result = runner.invoke(cli, ["run", "--config", config_path, "-y", "--output", "text", "hello"])

    assert result.exit_code == 0, (
        f"Expected exit 0, got {result.exit_code}. Output:\n{result.output}\nException: {result.exception}"
    )
    cfg = captured["prepared"].mount_plan["providers"][0]["config"]
    assert cfg["default_model"] == "claude-sonnet-4-5", (
        f"Expected default_model='claude-sonnet-4-5' from host_config, got {cfg.get('default_model')!r}"
    )


def test_host_config_provider_config_effort_lands_in_mount_plan(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``provider.config.effort`` lands in mount_plan[providers][0][config]."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    config_path = _write_host_config(
        tmp_path,
        {"provider": {"module": "anthropic", "config": {"effort": "high"}}},
    )
    captured = _patch_engine_boot(monkeypatch)

    result = runner.invoke(cli, ["run", "--config", config_path, "-y", "--output", "text", "hello"])

    assert result.exit_code == 0, (
        f"Expected exit 0, got {result.exit_code}. Output:\n{result.output}\nException: {result.exception}"
    )
    cfg = captured["prepared"].mount_plan["providers"][0]["config"]
    assert cfg["effort"] == "high", f"Expected effort='high' from host_config, got {cfg.get('effort')!r}"


def test_host_config_provider_config_arbitrary_keys_pass_through(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Arbitrary keys in ``provider.config`` (e.g. temperature, max_tokens) pass through.

    Forward-compat: the engine doesn't enumerate or validate keys; whatever the
    host config supplies under ``provider.config`` lands in mount_plan so the
    provider can consume it. This lets new provider-side knobs (temperature,
    max_tokens, thinking_budget_tokens, ...) be threaded through without an
    engine release.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    config_path = _write_host_config(
        tmp_path,
        {
            "provider": {
                "module": "anthropic",
                "config": {
                    "default_model": "claude-sonnet-4-5",
                    "temperature": 0.3,
                    "max_tokens": 4096,
                    "thinking_budget_tokens": 8192,
                },
            }
        },
    )
    captured = _patch_engine_boot(monkeypatch)

    result = runner.invoke(cli, ["run", "--config", config_path, "-y", "--output", "text", "hello"])

    assert result.exit_code == 0, (
        f"Expected exit 0, got {result.exit_code}. Output:\n{result.output}\nException: {result.exception}"
    )
    cfg = captured["prepared"].mount_plan["providers"][0]["config"]
    assert cfg["default_model"] == "claude-sonnet-4-5"
    assert cfg["temperature"] == 0.3
    assert cfg["max_tokens"] == 4096
    assert cfg["thinking_budget_tokens"] == 8192


def test_no_host_config_omits_model_and_effort(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No host_config -> mount_plan config has only api_key + priority.

    With no config file and no CLI overrides, the provider's own
    ``get_info().defaults`` are the source of truth -- the catalog never injects
    a default_model or effort.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    captured = _patch_engine_boot(monkeypatch)

    result = runner.invoke(cli, ["run", "-y", "--output", "text", "hello"])

    assert result.exit_code == 0, (
        f"Expected exit 0, got {result.exit_code}. Output:\n{result.output}\nException: {result.exception}"
    )
    cfg = captured["prepared"].mount_plan["providers"][0]["config"]
    assert "default_model" not in cfg, (
        f"Expected default_model absent without host_config, got {cfg.get('default_model')!r}"
    )
    assert "effort" not in cfg, f"Expected effort absent without host_config, got {cfg.get('effort')!r}"

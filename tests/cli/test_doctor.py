"""Tests for the doctor admin verb (Task 7).

Verifies that `amplifier-agent doctor`:
  - Exits 0 when all five required checks pass.
  - Reports provider name in output.
  - Exits 1 when provider is not configured.
  - Reports XDG paths (config, cache, state) in output.
  - Reports Python version in output.
  - Reports bundle cache missing as [INFO] (exit 0), not [FAIL].
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from amplifier_agent_cli.__main__ import cli

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
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _clear_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove all four provider env vars."""
    for var in _PROVIDER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture()
def writable_xdg(tmp_path: Path) -> dict[str, str]:
    """Return an env dict with XDG_CONFIG/CACHE/STATE_HOME pointing to tmp subdirs."""
    cfg = tmp_path / "config"
    cache = tmp_path / "cache"
    state = tmp_path / "state"
    return {
        "XDG_CONFIG_HOME": str(cfg),
        "XDG_CACHE_HOME": str(cache),
        "XDG_STATE_HOME": str(state),
        "HOME": str(tmp_path),
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_doctor_all_green_exits_0(
    runner: CliRunner,
    writable_xdg: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With ANTHROPIC_API_KEY set and writable XDG dirs, exit 0 and 'OK' in output."""
    _clear_providers(monkeypatch)
    env = {**writable_xdg, "ANTHROPIC_API_KEY": "sk-test"}
    result = runner.invoke(cli, ["doctor"], env=env)
    assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}. Output:\n{result.output}"
    assert "OK" in result.output, f"Expected 'OK' in output:\n{result.output}"


def test_doctor_reports_bundle_default_provider_status(
    runner: CliRunner,
    writable_xdg: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """doctor reports the bundle.md ``default_provider`` field (E5/D6).

    Env vars no longer drive doctor's provider check. The vendored bundle.md
    ships ``default_provider: anthropic``, so doctor must mention 'provider'
    and 'anthropic' in its output regardless of env var state.
    """
    _clear_providers(monkeypatch)
    # Set an unrelated env var to confirm it does NOT influence the report.
    env = {**writable_xdg, "OPENAI_API_KEY": "sk-openai-test"}
    result = runner.invoke(cli, ["doctor"], env=env)
    output_lower = result.output.lower()
    assert "provider" in output_lower, f"Expected 'provider' in output:\n{result.output}"
    assert "anthropic" in output_lower, (
        f"Expected bundle default 'anthropic' in output (env vars no longer used):\n{result.output}"
    )


def test_doctor_passes_when_no_env_vars_but_bundle_has_default(
    runner: CliRunner,
    writable_xdg: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no provider env vars set, doctor still passes the provider check
    because bundle.md declares ``default_provider`` (E5/D6).
    """
    _clear_providers(monkeypatch)
    result = runner.invoke(cli, ["doctor"], env=writable_xdg)
    assert result.exit_code == 0, (
        f"Expected exit 0 (bundle declares default_provider), got {result.exit_code}. Output:\n{result.output}"
    )
    output_lower = result.output.lower()
    assert "ok" in output_lower, f"Expected '[ OK ]' lines in output:\n{result.output}"
    assert "default_provider" in output_lower or "provider" in output_lower, (
        f"Expected 'provider' or 'default_provider' line in output:\n{result.output}"
    )


def test_doctor_reports_xdg_paths(
    runner: CliRunner,
    writable_xdg: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Output must include lines mentioning config, cache, and state paths."""
    _clear_providers(monkeypatch)
    env = {**writable_xdg, "ANTHROPIC_API_KEY": "sk-test"}
    result = runner.invoke(cli, ["doctor"], env=env)
    output_lower = result.output.lower()
    assert "config" in output_lower, f"Expected 'config' in output:\n{result.output}"
    assert "cache" in output_lower, f"Expected 'cache' in output:\n{result.output}"
    assert "state" in output_lower, f"Expected 'state' in output:\n{result.output}"


def test_doctor_reports_python_version(
    runner: CliRunner,
    writable_xdg: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Output must include a line mentioning Python version."""
    _clear_providers(monkeypatch)
    env = {**writable_xdg, "ANTHROPIC_API_KEY": "sk-test"}
    result = runner.invoke(cli, ["doctor"], env=env)
    assert "python" in result.output.lower(), f"Expected 'python' in output:\n{result.output}"


def test_doctor_reports_bundle_cache_missing_as_info_not_failure(
    runner: CliRunner,
    writable_xdg: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bundle cache absent → [INFO] line, NOT [FAIL]; exit 0 (Phase 2 behavior)."""
    _clear_providers(monkeypatch)
    # writable_xdg points to empty tmp subdirs — no bundle cache seeded
    env = {**writable_xdg, "ANTHROPIC_API_KEY": "sk-test"}
    result = runner.invoke(cli, ["doctor"], env=env)
    assert result.exit_code == 0, (
        f"Expected exit 0 when bundle cache absent, got {result.exit_code}. Output:\n{result.output}"
    )
    output_lower = result.output.lower()
    assert "bundle" in output_lower or "cache" in output_lower, (
        f"Expected 'bundle' or 'cache' in output:\n{result.output}"
    )


def test_doctor_version_upgrade_no_contradictory_output(
    runner: CliRunner,
    writable_xdg: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After a version upgrade, doctor must NOT report '[ OK ] bundle cache:' for the old version's data.

    Scenario: user upgrades from 0.0.0 → current version.  The old 0.0.0 cache dir exists.
    The legacy _check_bundle_cache() sees a non-empty prepared/ directory and falsely says OK.
    The version-aware check_cache_state() correctly says 'needs prepare'.
    After the fix, only one check exists (version-aware) and output is on stdout via click.echo.
    """
    _clear_providers(monkeypatch)
    env = {**writable_xdg, "ANTHROPIC_API_KEY": "sk-test"}

    # Seed an old-version cache directory (simulates a prior install)
    cache_home = Path(writable_xdg["XDG_CACHE_HOME"])
    old_version_dir = cache_home / "amplifier-agent" / "prepared" / "0.0.0"
    old_version_dir.mkdir(parents=True)
    (old_version_dir / "manifest.json").write_text('{"aaa_version": "0.0.0"}')
    (old_version_dir / "prepared.pickle").write_bytes(b"fake-old-data")

    result = runner.invoke(cli, ["doctor"], env=env)

    # The version-aware check knows the CURRENT version cache isn't ready.
    # Doctor must NOT claim the bundle cache is OK — it's the wrong version.
    assert "[ OK ] bundle cache:" not in result.output, (
        "After version upgrade, doctor must not report '[ OK ] bundle cache:' "
        "for stale data from a different version.\n"
        f"Output:\n{result.output}"
    )
    # Exit 0 — stale/missing bundle cache is informational, not fatal
    assert result.exit_code == 0, (
        f"Expected exit 0 even with stale bundle cache, got {result.exit_code}.\nOutput:\n{result.output}"
    )


def test_doctor_does_not_call_load_and_prepare_cached() -> None:
    """doctor.py must NOT contain any reference to load_and_prepare_cached.

    Per the admin verb split (Task 7), doctor is a pure diagnostic command.
    It reports cache state via check_cache_state() but never primes the cache.
    """
    import inspect

    from amplifier_agent_cli.admin import doctor as doctor_module

    source = inspect.getsource(doctor_module)
    assert "load_and_prepare_cached" not in source, (
        "doctor.py must not call load_and_prepare_cached; cache priming belongs to the 'prepare' command."
    )


def test_doctor_uses_persistence_for_xdg_paths() -> None:
    """doctor.py must not define a local _xdg() helper.

    Per D9, XDG path lookup is consolidated through
    amplifier_agent_lib.persistence.{config_root,cache_root,state_root}.
    The previous private _xdg() helper in admin/doctor.py is a duplicate
    of the persistence-layer logic and must be removed.
    """
    from amplifier_agent_cli.admin import doctor as doctor_mod

    assert hasattr(doctor_mod, "_xdg") is False, (
        "doctor.py must not define _xdg(); use persistence.config_root() / cache_root() / state_root() instead (D9)."
    )


def test_doctor_bundle_cache_uses_structured_format(
    runner: CliRunner,
    writable_xdg: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cache status must use the [OK]/[INFO] structured prefix, not the raw 'Cache: ...' format.

    Before fix: print(f"Cache: {status} ({dir})", file=sys.stderr) produces an unstructured line.
    After fix:  click.echo(f"[INFO] bundle cache: {status} ({dir})") produces a structured line.
    """
    _clear_providers(monkeypatch)
    env = {**writable_xdg, "ANTHROPIC_API_KEY": "sk-test"}
    result = runner.invoke(cli, ["doctor"], env=env)

    # The raw "Cache: ..." prefix from print() must be gone; structured lines use "[...] bundle cache:"
    assert "Cache: " not in result.output, (
        f"Cache output must use structured [OK]/[INFO] format, not raw 'Cache: ...'.\nOutput:\n{result.output}"
    )
    # The structured bundle cache line must appear so operators can see the status
    assert "bundle cache:" in result.output.lower(), (
        f"Expected a structured 'bundle cache:' line in output.\nOutput:\n{result.output}"
    )


# ---------------------------------------------------------------------------
# G4: mcp module importable check
# ---------------------------------------------------------------------------


def test_doctor_reports_mcp_importable_when_present(
    runner: CliRunner,
    writable_xdg: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G4: when `mcp` is importable and bundle declares tool-mcp, doctor reports OK."""
    _clear_providers(monkeypatch)
    env = {**writable_xdg, "ANTHROPIC_API_KEY": "sk-test"}
    result = runner.invoke(cli, ["doctor"], env=env)
    # Default bundle declares tool-mcp, so the check must fire.
    assert "mcp module" in result.output.lower(), f"Expected an 'mcp module' line in doctor output:\n{result.output}"
    # When this test runs, `mcp` is in pyproject deps so it must import cleanly.
    assert "[ OK ] mcp module: importable" in result.output, (
        f"Expected '[ OK ] mcp module: importable' in doctor output:\n{result.output}"
    )
    assert result.exit_code == 0


def test_doctor_fails_loudly_when_mcp_not_importable(
    runner: CliRunner,
    writable_xdg: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G4: when `mcp` cannot be imported but bundle declares tool-mcp, doctor exits 1.

    Simulates the legacy install pain: tool-mcp is in the bundle, but mcp wasn't
    installed (because the user didn't pass --with mcp on an old amplifier-agent
    pin). Doctor must surface this with a clear remediation line rather than let
    the failure cascade into a downstream `bundle.origins` AttributeError.
    """
    _clear_providers(monkeypatch)
    env = {**writable_xdg, "ANTHROPIC_API_KEY": "sk-test"}

    import builtins

    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "mcp" or name.startswith("mcp."):
            raise ImportError("No module named 'mcp' (simulated by test)")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    result = runner.invoke(cli, ["doctor"], env=env)

    assert result.exit_code == 1, (
        f"Expected exit 1 when mcp not importable, got {result.exit_code}.\nOutput:\n{result.output}"
    )
    assert "[FAIL] mcp module: import failed" in result.output, (
        f"Expected '[FAIL] mcp module: import failed' line in output:\n{result.output}"
    )
    # Remediation must point at the canonical install command.
    assert "uv tool install" in result.output, (
        f"Expected remediation line with 'uv tool install' in output:\n{result.output}"
    )


def test_doctor_skips_mcp_check_when_bundle_omits_tool_mcp(
    runner: CliRunner,
    writable_xdg: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G4: doctor's mcp-importable check skips (reports [INFO]) when tool-mcp is not in the bundle.

    Air-gapped or minimal-bundle deployments may omit `tool-mcp`; in that case
    `mcp` is not a runtime requirement and doctor must not penalise it.
    """
    _clear_providers(monkeypatch)
    env = {**writable_xdg, "ANTHROPIC_API_KEY": "sk-test"}
    monkeypatch.setattr(
        "amplifier_agent_cli.admin.doctor._bundle_declares_tool_mcp",
        lambda: False,
    )
    result = runner.invoke(cli, ["doctor"], env=env)
    assert result.exit_code == 0
    assert "[INFO] mcp module: skipped" in result.output, (
        f"Expected '[INFO] mcp module: skipped' in output when tool-mcp not in bundle:\n{result.output}"
    )

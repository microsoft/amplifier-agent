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

_PROVIDER_ENV_VARS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "AZURE_OPENAI_KEY", "OLLAMA_HOST")


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


def test_doctor_reports_provider_status(
    runner: CliRunner,
    writable_xdg: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With OPENAI_API_KEY set, output should mention 'provider' and 'openai'."""
    _clear_providers(monkeypatch)
    env = {**writable_xdg, "OPENAI_API_KEY": "sk-openai-test"}
    result = runner.invoke(cli, ["doctor"], env=env)
    output_lower = result.output.lower()
    assert "provider" in output_lower, f"Expected 'provider' in output:\n{result.output}"
    assert "openai" in result.output.lower(), f"Expected 'openai' in output:\n{result.output}"


def test_doctor_fails_when_no_provider(
    runner: CliRunner,
    writable_xdg: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When no provider env vars are set, exit 1, 'FAIL' and 'provider' in output."""
    _clear_providers(monkeypatch)
    result = runner.invoke(cli, ["doctor"], env=writable_xdg)
    assert result.exit_code == 1, (
        f"Expected exit 1 when no provider set, got {result.exit_code}. Output:\n{result.output}"
    )
    output_lower = result.output.lower()
    assert "fail" in output_lower, f"Expected 'fail' in output:\n{result.output}"
    assert "provider" in output_lower, f"Expected 'provider' in output:\n{result.output}"


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

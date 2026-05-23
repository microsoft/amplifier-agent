"""Tests for admin/doctor.py Phase 2 flags — --strict (CI gate) and --quick (minimal)."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from amplifier_agent_cli.admin.doctor import doctor


def _isolate_xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point all XDG dirs at *tmp_path* so the test never touches a real cache."""
    cache = tmp_path / "cache"
    config = tmp_path / "config"
    state = tmp_path / "state"
    cache.mkdir()
    config.mkdir()
    state.mkdir()
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setenv("XDG_STATE_HOME", str(state))


def test_doctor_strict_exits_nonzero_when_cache_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--strict must exit 1 when the prepared-bundle cache is absent."""
    _isolate_xdg(tmp_path, monkeypatch)
    # Make sure no provider check failure masks the cache failure path.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")

    runner = CliRunner()
    result = runner.invoke(doctor, ["--strict"])

    assert result.exit_code == 1, result.output
    assert "[FAIL] bundle cache" in result.output


def test_doctor_without_strict_exits_zero_when_only_cache_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without --strict, missing cache is [INFO] only and overall exit is 0."""
    _isolate_xdg(tmp_path, monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")

    runner = CliRunner()
    result = runner.invoke(doctor, [])

    assert result.exit_code == 0, result.output
    assert "[INFO] bundle cache" in result.output


def test_doctor_strict_flag_is_present() -> None:
    """`doctor --help` must list the --strict option."""
    runner = CliRunner()
    result = runner.invoke(doctor, ["--help"])

    assert result.exit_code == 0
    assert "--strict" in result.output, "doctor --help must list --strict"


def test_doctor_quick_flag_is_present() -> None:
    """`doctor --help` must list the --quick option."""
    runner = CliRunner()
    result = runner.invoke(doctor, ["--help"])

    assert result.exit_code == 0
    assert "--quick" in result.output, "doctor --help must list --quick"


def test_doctor_quick_exits_without_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`doctor --quick` exits with 0 or 1 (health verdict), never 2 (Click usage error)."""
    _isolate_xdg(tmp_path, monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")

    runner = CliRunner()
    result = runner.invoke(doctor, ["--quick"])

    assert result.exit_code in (0, 1), (
        f"doctor --quick must return a health verdict, not a usage error; "
        f"got exit_code={result.exit_code}, output={result.output!r}"
    )


def test_doctor_emit_sha_flag_is_present() -> None:
    """`doctor --help` must list the --emit-sha option."""
    runner = CliRunner()
    result = runner.invoke(doctor, ["--help"])

    assert result.exit_code == 0
    assert "--emit-sha" in result.output, "doctor --help must list --emit-sha"


def test_doctor_emit_sha_outputs_module_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`doctor --emit-sha` must print 'module=' lines for bundle modules."""
    _isolate_xdg(tmp_path, monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")

    runner = CliRunner()
    result = runner.invoke(doctor, ["--emit-sha"])

    assert result.exit_code in (0, 1), (
        f"doctor --emit-sha must return a health verdict, not a usage error; "
        f"got exit_code={result.exit_code}, output={result.output!r}"
    )
    assert "module=" in result.output, f"doctor --emit-sha must emit lines containing 'module='; got: {result.output!r}"


def test_doctor_emit_sha_includes_tool_mcp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`doctor --emit-sha` output must include tool-mcp (verifies A4 edits landed)."""
    _isolate_xdg(tmp_path, monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")

    runner = CliRunner()
    result = runner.invoke(doctor, ["--emit-sha"])

    assert "tool-mcp" in result.output, (
        f"doctor --emit-sha must list tool-mcp (A4 verification); got: {result.output!r}"
    )


def test_doctor_emit_sha_includes_hooks_approval(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`doctor --emit-sha` output must include hooks-approval (verifies A4 edits landed)."""
    _isolate_xdg(tmp_path, monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")

    runner = CliRunner()
    result = runner.invoke(doctor, ["--emit-sha"])

    assert "hooks-approval" in result.output, (
        f"doctor --emit-sha must list hooks-approval (A4 verification); got: {result.output!r}"
    )

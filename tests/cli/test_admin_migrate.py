"""Tests for the `amplifier-agent migrate` CLI subcommand.

Covers:
- Both migrations are called when the command runs
- Idempotency: second run reports skipped=True for both
- JSON output shape matches spec
- Text output sanity check
- Exit code semantics (0 on success/skipped, 1 on exception)
- Subcommand is registered in the CLI group
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from amplifier_agent_cli.__main__ import cli

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sessions_result(migrated: int = 0, skipped: bool = True, collided: int = 0) -> SimpleNamespace:
    return SimpleNamespace(migrated=migrated, skipped=skipped, collided=collided)


def _xdg_result(migrated: int = 0, skipped: bool = True, collided: int = 0, from_xdg: bool = True) -> SimpleNamespace:
    return SimpleNamespace(migrated=migrated, skipped=skipped, collided=collided, from_xdg=from_xdg)


@pytest.fixture()
def patch_migrations(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Patch both migration functions with controllable fakes (default: all skipped)."""
    state: dict = {
        "sessions_result": _sessions_result(),
        "xdg_result": _xdg_result(),
        "sessions_calls": 0,
        "xdg_calls": 0,
    }

    def _fake_sessions() -> SimpleNamespace:
        state["sessions_calls"] += 1
        return state["sessions_result"]

    def _fake_xdg() -> SimpleNamespace:
        state["xdg_calls"] += 1
        return state["xdg_result"]

    monkeypatch.setattr(
        "amplifier_agent_cli.admin.migrate.migrate_legacy_sessions_if_needed",
        _fake_sessions,
    )
    monkeypatch.setattr(
        "amplifier_agent_cli.admin.migrate.maybe_migrate_legacy_xdg_storage",
        _fake_xdg,
    )
    return state


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_migrate_runs_both_migrations(patch_migrations: dict) -> None:
    """amplifier-agent migrate calls both migration functions exactly once."""
    result = CliRunner().invoke(cli, ["migrate"])
    assert result.exit_code == 0, result.output
    assert patch_migrations["sessions_calls"] == 1
    assert patch_migrations["xdg_calls"] == 1


def test_migrate_idempotency_second_run_all_skipped(patch_migrations: dict) -> None:
    """Second run: both migrations report skipped=True (idempotent behaviour)."""
    # Default fixture state: both skipped=True already.
    result = CliRunner().invoke(cli, ["migrate", "--output", "json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    assert payload["sessions_migration"]["skipped"] is True
    assert payload["xdg_migration"]["skipped"] is True
    assert payload["sessions_migration"]["migrated"] == 0
    assert payload["xdg_migration"]["migrated"] == 0


def test_migrate_json_output_shape(patch_migrations: dict) -> None:
    """--output json emits both migration results with all required keys and values."""
    patch_migrations["sessions_result"] = _sessions_result(migrated=2, skipped=False, collided=0)
    patch_migrations["xdg_result"] = _xdg_result(migrated=3, skipped=False, collided=1, from_xdg=True)

    result = CliRunner().invoke(cli, ["migrate", "--output", "json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())

    # Top-level shape
    assert "sessions_migration" in payload
    assert "xdg_migration" in payload

    sm = payload["sessions_migration"]
    assert sm["migrated"] == 2
    assert sm["skipped"] is False
    assert sm["collided"] == 0

    xdg = payload["xdg_migration"]
    assert xdg["migrated"] == 3
    assert xdg["skipped"] is False
    assert xdg["collided"] == 1
    assert xdg["from_xdg"] is True


def test_migrate_text_output_migrated(patch_migrations: dict) -> None:
    """Text mode: success lines appear when migrations ran."""
    patch_migrations["sessions_result"] = _sessions_result(migrated=2, skipped=False, collided=0)
    patch_migrations["xdg_result"] = _xdg_result(migrated=1, skipped=False, collided=0, from_xdg=True)

    result = CliRunner().invoke(cli, ["migrate"])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "sessions" in out
    # XDG migration success line references the path or "XDG"
    assert "XDG" in out or "amplifier-agent" in out


def test_migrate_text_output_skipped(patch_migrations: dict) -> None:
    """Text mode: 'already done' or similar markers appear when both are skipped."""
    result = CliRunner().invoke(cli, ["migrate"])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "already done" in out or "nothing to move" in out


def test_migrate_text_output_collisions(patch_migrations: dict) -> None:
    """Text mode: collision warnings appear when collided > 0."""
    patch_migrations["sessions_result"] = _sessions_result(migrated=1, skipped=False, collided=2)
    patch_migrations["xdg_result"] = _xdg_result(migrated=0, skipped=True)

    result = CliRunner().invoke(cli, ["migrate"])
    assert result.exit_code == 0, result.output
    assert "!" in result.output or "collision" in result.output


def test_migrate_exit_0_when_all_skipped(patch_migrations: dict) -> None:
    """Exit 0 even when both migrations report nothing to do."""
    result = CliRunner().invoke(cli, ["migrate"])
    assert result.exit_code == 0


def test_migrate_exit_0_when_fully_migrated(patch_migrations: dict) -> None:
    """Exit 0 when both migrations ran successfully."""
    patch_migrations["sessions_result"] = _sessions_result(migrated=5, skipped=False)
    patch_migrations["xdg_result"] = _xdg_result(migrated=3, skipped=False)
    result = CliRunner().invoke(cli, ["migrate"])
    assert result.exit_code == 0


def test_migrate_exit_1_on_sessions_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exit 1 when sessions migration raises an exception."""

    def _boom() -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(
        "amplifier_agent_cli.admin.migrate.migrate_legacy_sessions_if_needed",
        _boom,
    )
    monkeypatch.setattr(
        "amplifier_agent_cli.admin.migrate.maybe_migrate_legacy_xdg_storage",
        lambda: _xdg_result(),
    )
    result = CliRunner().invoke(cli, ["migrate"])
    assert result.exit_code == 1


def test_migrate_exit_1_on_xdg_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exit 1 when XDG migration raises an exception."""

    def _boom() -> None:
        raise RuntimeError("permission denied")

    monkeypatch.setattr(
        "amplifier_agent_cli.admin.migrate.migrate_legacy_sessions_if_needed",
        lambda: _sessions_result(),
    )
    monkeypatch.setattr(
        "amplifier_agent_cli.admin.migrate.maybe_migrate_legacy_xdg_storage",
        _boom,
    )
    result = CliRunner().invoke(cli, ["migrate"])
    assert result.exit_code == 1


def test_migrate_json_error_on_sessions_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """--output json: error key present when sessions migration raises."""

    def _boom() -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(
        "amplifier_agent_cli.admin.migrate.migrate_legacy_sessions_if_needed",
        _boom,
    )
    monkeypatch.setattr(
        "amplifier_agent_cli.admin.migrate.maybe_migrate_legacy_xdg_storage",
        lambda: _xdg_result(),
    )
    result = CliRunner().invoke(cli, ["migrate", "--output", "json"])
    assert result.exit_code == 1
    payload = json.loads(result.output.strip())
    assert "error" in payload
    assert "sessions-migration-failed" in payload["error"]


def test_migrate_is_registered_in_cli() -> None:
    """amplifier-agent migrate --help exits 0 (subcommand is wired into CLI group)."""
    result = CliRunner().invoke(cli, ["migrate", "--help"])
    assert result.exit_code == 0, result.output
    assert "migrate" in result.output.lower()
    assert "idempotent" in result.output.lower() or "legacy" in result.output.lower()

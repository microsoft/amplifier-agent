"""Tests for the cache clear admin verb (Task 5).

Verifies that `amplifier-agent cache clear`:
  - Removes $XDG_CACHE_HOME/amplifier-agent/prepared/ when it exists.
  - Reports the removed path (contains 'prepared') in stdout.
  - Is idempotent: exits 0 with an informational message when the cache is absent.
  - Does NOT touch sibling directories under XDG_CACHE_HOME.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from amplifier_agent_cli.__main__ import cli


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture()
def fake_cache(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """Seed $XDG_CACHE_HOME/amplifier-agent/prepared/0.0.0/{bundle.json, sub/more.txt}.

    Returns (cache_home_path, env_dict) so tests can both inspect the filesystem
    and pass the env override to CliRunner.invoke().
    """
    cache_home = tmp_path / "cache"
    prepared_version = cache_home / "amplifier-agent" / "prepared" / "0.0.0"
    prepared_version.mkdir(parents=True, exist_ok=True)
    (prepared_version / "bundle.json").write_text("{}")
    sub = prepared_version / "sub"
    sub.mkdir()
    (sub / "more.txt").write_text("data")
    env: dict[str, str] = {"XDG_CACHE_HOME": str(cache_home)}
    return cache_home, env


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_cache_clear_removes_prepared_dir(
    runner: CliRunner,
    fake_cache: tuple[Path, dict[str, str]],
) -> None:
    """After `cache clear`, the prepared/ directory must not exist; exit code 0."""
    cache_home, env = fake_cache
    prepared = cache_home / "amplifier-agent" / "prepared"
    assert prepared.exists(), "fixture must have created the prepared dir"

    result = runner.invoke(cli, ["cache", "clear"], env=env)

    assert result.exit_code == 0, result.output
    assert not prepared.exists()


def test_cache_clear_reports_removed_path(
    runner: CliRunner,
    fake_cache: tuple[Path, dict[str, str]],
) -> None:
    """Output must contain 'prepared' and exit 0 after a successful clear."""
    _cache_home, env = fake_cache
    result = runner.invoke(cli, ["cache", "clear"], env=env)

    assert result.exit_code == 0, result.output
    assert "prepared" in result.output


def test_cache_clear_idempotent_when_empty(
    runner: CliRunner,
    tmp_path: Path,
) -> None:
    """When no prepared cache exists, exit 0 with a message containing 'nothing' or 'no cache'."""
    cache_home = tmp_path / "cache"
    # Deliberately do NOT create any subdirectories — cache is empty/absent.
    env: dict[str, str] = {"XDG_CACHE_HOME": str(cache_home)}

    result = runner.invoke(cli, ["cache", "clear"], env=env)

    assert result.exit_code == 0, result.output
    output_lower = result.output.lower()
    assert "nothing" in output_lower or "no cache" in output_lower, (
        f"Expected 'nothing' or 'no cache' in output, got: {result.output!r}"
    )


def test_cache_clear_does_not_remove_unrelated_dirs(
    runner: CliRunner,
    fake_cache: tuple[Path, dict[str, str]],
) -> None:
    """A sibling 'some-other-tool' directory under XDG_CACHE_HOME must be preserved."""
    cache_home, env = fake_cache
    sibling = cache_home / "some-other-tool"
    sibling.mkdir(parents=True, exist_ok=True)
    (sibling / "data.txt").write_text("keep me")

    result = runner.invoke(cli, ["cache", "clear"], env=env)

    assert result.exit_code == 0, result.output
    assert sibling.exists(), "sibling dir must not be removed by cache clear"
    assert (sibling / "data.txt").exists(), "files in sibling dir must not be removed"

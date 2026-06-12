"""Tests for the cache clear admin verb (Task 5).

Verifies that `amplifier-agent cache clear`:
  - Removes <AMPLIFIER_AGENT_HOME>/cache/prepared/ when it exists.
  - Reports the removed path (contains 'prepared') in stdout.
  - Is idempotent: exits 0 with an informational message when the cache is absent.
  - Does NOT touch sibling directories under AMPLIFIER_AGENT_HOME.
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
    """Seed <AMPLIFIER_AGENT_HOME>/cache/prepared/0.0.0/{bundle.json, sub/more.txt}.

    Returns (aah_path, env_dict) so tests can both inspect the filesystem
    and pass the env override to CliRunner.invoke().  aah_path is the value
    of AMPLIFIER_AGENT_HOME; the prepared dir lives at aah_path/cache/prepared/.
    """
    # With AMPLIFIER_AGENT_HOME=tmp_path, cache_root() == tmp_path / "cache"
    # and prepared_bundle_dir() == tmp_path / "cache" / "prepared" / <version>.
    prepared_version = tmp_path / "cache" / "prepared" / "0.0.0"
    prepared_version.mkdir(parents=True, exist_ok=True)
    (prepared_version / "bundle.json").write_text("{}")
    sub = prepared_version / "sub"
    sub.mkdir()
    (sub / "more.txt").write_text("data")
    env: dict[str, str] = {"AMPLIFIER_AGENT_HOME": str(tmp_path)}
    return tmp_path, env


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_cache_clear_removes_prepared_dir(
    runner: CliRunner,
    fake_cache: tuple[Path, dict[str, str]],
) -> None:
    """After `cache clear`, the prepared/ directory must not exist; exit code 0."""
    aah, env = fake_cache
    prepared = aah / "cache" / "prepared"
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
    # Deliberately do NOT create any subdirectories — cache is empty/absent.
    env: dict[str, str] = {"AMPLIFIER_AGENT_HOME": str(tmp_path)}

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
    """A sibling 'other-dir' directory under AMPLIFIER_AGENT_HOME must be preserved."""
    aah, env = fake_cache
    sibling = aah / "some-other-tool"
    sibling.mkdir(parents=True, exist_ok=True)
    (sibling / "data.txt").write_text("keep me")

    result = runner.invoke(cli, ["cache", "clear"], env=env)

    assert result.exit_code == 0, result.output
    assert sibling.exists(), "sibling dir must not be removed by cache clear"
    assert (sibling / "data.txt").exists(), "files in sibling dir must not be removed"

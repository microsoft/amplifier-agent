"""End-to-end integration tests for the full install → post-install → doctor pipeline.

Capstone test for Phase 4. Verifies:
  1. Fresh venv install → `amplifier-agent doctor` reports 'needs prepare'.
  2. After `amplifier-agent-post-install` runs → `amplifier-agent doctor` reports 'prepared'.

These tests are slow (30s-2min on first run due to dependency install time) and are
marked @pytest.mark.integration so they can be skipped in fast CI tiers:

    uv run pytest tests/test_e2e_install_and_run.py -v -m integration
    uv run pytest tests/ -v -m "not integration"  # skip in fast tier
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _install_into_venv(repo: Path, venv: Path, cache_home: Path) -> dict[str, str]:
    """Create an isolated venv, install the package, and return the env dict.

    Args:
        repo: Absolute path to the repository root (contains pyproject.toml).
        venv: Destination path for the virtual environment.
        cache_home: Path to use as XDG_CACHE_HOME (isolates cache from real home).

    Returns:
        An env dict suitable for passing to subprocess calls.
    """
    cache_home.mkdir(parents=True, exist_ok=True)
    env: dict[str, str] = {
        **os.environ,
        "XDG_CACHE_HOME": str(cache_home),
        "VIRTUAL_ENV": str(venv),
    }

    # Create the virtual environment.
    subprocess.check_call(["uv", "venv", str(venv)], env=env)

    # Install the package in editable mode so the console scripts are wired.
    subprocess.check_call(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(venv / "bin" / "python"),
            "-e",
            str(repo),
        ],
        env=env,
    )

    return env


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_install_then_doctor_reports_needs_prepare(tmp_path: Path) -> None:
    """Fresh install with no cache → `amplifier-agent doctor` reports 'needs prepare'.

    Pipeline:
      1. Create isolated venv.
      2. Install package in editable mode.
      3. Run `amplifier-agent doctor` with XDG_CACHE_HOME pointing at an empty dir.
      4. Assert the output contains 'needs prepare' (cache not yet primed).
    """
    if shutil.which("uv") is None:
        pytest.skip("uv not found on PATH — skipping integration test")

    repo = Path(__file__).resolve().parents[1]
    venv = tmp_path / "venv"
    cache_home = tmp_path / "cache"

    env = _install_into_venv(repo, venv, cache_home)

    binary = venv / "bin" / "amplifier-agent"
    assert binary.exists(), f"Expected console-script at {binary}"

    result = subprocess.run(
        [str(binary), "doctor"],
        env=env,
        capture_output=True,
        text=True,
    )

    combined = (result.stdout + result.stderr).lower()
    assert "needs prepare" in combined, (
        f"Expected 'needs prepare' in combined doctor output.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


@pytest.mark.integration
def test_post_install_then_doctor_reports_prepared(tmp_path: Path) -> None:
    """After `amplifier-agent-post-install` runs → doctor reports 'prepared' (not 'needs prepare').

    Pipeline:
      1. Create isolated venv and install package.
      2. Run `amplifier-agent-post-install` to prime the bundle cache.
      3. Run `amplifier-agent doctor`.
      4. Assert output contains 'prepared' and does NOT contain 'needs prepare'.
    """
    if shutil.which("uv") is None:
        pytest.skip("uv not found on PATH — skipping integration test")

    repo = Path(__file__).resolve().parents[1]
    venv = tmp_path / "venv"
    cache_home = tmp_path / "cache"

    env = _install_into_venv(repo, venv, cache_home)

    binary = venv / "bin" / "amplifier-agent"
    assert binary.exists(), f"Expected console-script at {binary}"

    # Prime the bundle cache via the post-install hook.
    post_install = venv / "bin" / "amplifier-agent-post-install"
    subprocess.check_call([str(post_install)], env=env)

    # Now doctor should report the cache as 'prepared'.
    result = subprocess.run(
        [str(binary), "doctor"],
        env=env,
        capture_output=True,
        text=True,
    )

    combined = (result.stdout + result.stderr).lower()
    assert "prepared" in combined, (
        f"Expected 'prepared' in combined doctor output.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "needs prepare" not in combined, (
        f"Expected 'needs prepare' NOT in combined doctor output after post-install.\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )

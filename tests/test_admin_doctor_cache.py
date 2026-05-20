"""Tests for admin/doctor.py — check_cache_state reports prepared-bundle cache status."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_doctor_reports_not_prepared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """check_cache_state returns 'needs prepare' when no cache directory exists."""
    from amplifier_agent_cli.admin.doctor import check_cache_state
    from amplifier_agent_lib.bundle.cache import cache_dir_for_version

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    state = check_cache_state(aaa_version="1.0.0")

    assert state.status == "needs prepare"
    assert state.cache_dir == cache_dir_for_version("1.0.0")


def test_doctor_reports_prepared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """check_cache_state returns 'prepared' when manifest.json and a non-manifest artifact exist."""
    from amplifier_agent_cli.admin.doctor import check_cache_state
    from amplifier_agent_lib.bundle.cache import cache_dir_for_version

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    v = cache_dir_for_version("1.0.0")
    v.mkdir(parents=True)
    (v / "manifest.json").write_text('{"aaa_version": "1.0.0"}')
    # Write artifacts to handle both pickle and compose strategies
    (v / "prepared.pickle").write_bytes(b"x")
    (v / "composed.md").write_text("---\n---\n")

    state = check_cache_state(aaa_version="1.0.0")

    assert state.status == "prepared"

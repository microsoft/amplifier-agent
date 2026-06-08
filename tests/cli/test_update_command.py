"""Tests for the `amplifier-agent update` CLI subcommand.

Covers:
- --check shows current vs latest without running install
- No action when versions match (default)
- Runs `uv tool install --reinstall --force git+...@<tag>` when newer
- Refuses on editable installs (exit 2)
- Prints equivalent command on "other" installs (exit 0)
- --force reinstalls even at same version
- --tag overrides the latest-release lookup
- GitHub API failure → clear error + exit 2 + manual install line
- --output json envelope shape
- Unknown flag → Click UsageError exit 2

All subprocess calls AND the GitHub API call are monkeypatched.
No test shells out to real `uv tool install` or hits the real network.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from click.testing import CliRunner

from amplifier_agent_cli.__main__ import cli

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_release(tag: str = "v0.4.2", date: str = "2026-05-30T12:00:00Z") -> dict[str, Any]:
    """A minimal GitHub Releases /latest payload."""
    return {
        "tag_name": tag,
        "name": tag,
        "published_at": date,
        "html_url": f"https://github.com/microsoft/amplifier-agent/releases/tag/{tag}",
    }


class _FakeCompleted:
    """Mimics subprocess.CompletedProcess for `uv tool install ...` runs."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture
def patch_lookups(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Default patches: GitHub returns v0.4.2, install is uv-tool, current = 0.5.0.

    Each test can override individual values via the returned dict.
    """
    state: dict[str, Any] = {
        "release": _fake_release(tag="v0.4.2"),
        "release_error": None,
        "install_method": "uv-tool",
        "current_version": "0.5.0",
        "subprocess_calls": [],
        "subprocess_result": _FakeCompleted(returncode=0),
    }

    def _fake_fetch(_url: str = "") -> dict[str, Any]:
        if state["release_error"] is not None:
            raise state["release_error"]
        return state["release"]

    def _fake_install_method() -> str:
        return state["install_method"]

    def _fake_current_version() -> str:
        return state["current_version"]

    def _fake_run(cmd: list[str], **kwargs: Any) -> _FakeCompleted:
        state["subprocess_calls"].append(cmd)
        return state["subprocess_result"]

    # Patch the module under test (admin.update).
    monkeypatch.setattr("amplifier_agent_cli.admin.update._fetch_latest_release", _fake_fetch)
    monkeypatch.setattr("amplifier_agent_cli.admin.update._detect_install_method", _fake_install_method)
    monkeypatch.setattr("amplifier_agent_cli.admin.update._current_version", _fake_current_version)
    monkeypatch.setattr("amplifier_agent_cli.admin.update.subprocess.run", _fake_run)
    return state


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_update_check_shows_current_and_latest(patch_lookups: dict[str, Any]) -> None:
    """--check prints the status table with both versions and exits 0."""
    patch_lookups["current_version"] = "0.5.0"
    patch_lookups["release"] = _fake_release(tag="v0.4.2")

    result = CliRunner().invoke(cli, ["update", "--check"])
    assert result.exit_code == 0, result.output
    assert "0.5.0" in result.output
    assert "0.4.2" in result.output
    assert patch_lookups["subprocess_calls"] == []


def test_update_no_action_when_versions_match(patch_lookups: dict[str, Any]) -> None:
    """current == latest, no --force: no install, action is skipped_up_to_date."""
    patch_lookups["current_version"] = "0.4.2"
    patch_lookups["release"] = _fake_release(tag="v0.4.2")

    result = CliRunner().invoke(cli, ["update", "--output", "json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    assert payload["action"] == "skipped_up_to_date"
    assert patch_lookups["subprocess_calls"] == []


def test_update_runs_uv_install_when_newer_available(patch_lookups: dict[str, Any]) -> None:
    """current < latest on a uv-tool install: subprocess is called with the right argv."""
    patch_lookups["current_version"] = "0.4.0"
    patch_lookups["release"] = _fake_release(tag="v0.4.2")

    result = CliRunner().invoke(cli, ["update"])
    assert result.exit_code == 0, result.output
    assert patch_lookups["subprocess_calls"], "expected uv tool install to have been called"
    argv = patch_lookups["subprocess_calls"][0]
    assert argv[0] == "uv"
    assert argv[1:5] == ["tool", "install", "--reinstall", "--force"]
    assert any("git+https://github.com/microsoft/amplifier-agent@v0.4.2" in a for a in argv)


def test_update_refuses_on_editable_install(patch_lookups: dict[str, Any]) -> None:
    """Editable installs are refused: action is skipped_editable, exit 2."""
    patch_lookups["install_method"] = "editable"
    patch_lookups["current_version"] = "0.4.0"

    result = CliRunner().invoke(cli, ["update", "--output", "json"])
    assert result.exit_code == 2, result.output
    payload = json.loads(result.output.strip())
    assert payload["action"] == "skipped_editable"
    assert patch_lookups["subprocess_calls"] == []


def test_update_prints_command_on_other_install(patch_lookups: dict[str, Any]) -> None:
    """Non-uv install: print the equivalent command and exit 0 without running it."""
    patch_lookups["install_method"] = "other"
    patch_lookups["current_version"] = "0.4.0"
    patch_lookups["release"] = _fake_release(tag="v0.4.2")

    result = CliRunner().invoke(cli, ["update"])
    assert result.exit_code == 0, result.output
    assert "uv tool install" in result.output
    assert "git+https://github.com/microsoft/amplifier-agent@v0.4.2" in result.output
    assert patch_lookups["subprocess_calls"] == []


def test_update_force_reinstalls_when_versions_match(patch_lookups: dict[str, Any]) -> None:
    """--force at same version still triggers the install."""
    patch_lookups["current_version"] = "0.4.2"
    patch_lookups["release"] = _fake_release(tag="v0.4.2")

    result = CliRunner().invoke(cli, ["update", "--force"])
    assert result.exit_code == 0, result.output
    assert patch_lookups["subprocess_calls"], "expected uv tool install to have been called with --force"


def test_update_tag_overrides_latest_lookup(patch_lookups: dict[str, Any]) -> None:
    """--tag installs the given ref, not whatever the API reports as latest."""
    patch_lookups["current_version"] = "0.5.0"
    patch_lookups["release"] = _fake_release(tag="v0.4.2")  # API says 0.4.2

    result = CliRunner().invoke(cli, ["update", "--tag", "v0.4.0", "--force"])
    assert result.exit_code == 0, result.output
    assert patch_lookups["subprocess_calls"], "expected uv tool install to have been called"
    argv = patch_lookups["subprocess_calls"][0]
    assert any("git+https://github.com/microsoft/amplifier-agent@v0.4.0" in a for a in argv), argv


def test_update_handles_github_api_failure(patch_lookups: dict[str, Any]) -> None:
    """API failure: clear error to stderr, manual install command shown, exit 2."""
    patch_lookups["release_error"] = OSError("Network is unreachable")

    result = CliRunner().invoke(cli, ["update"])
    assert result.exit_code == 2, result.output
    combined = result.output + (result.stderr if result.stderr_bytes else "")
    assert "GitHub" in combined or "github" in combined
    assert "uv tool install" in combined
    assert patch_lookups["subprocess_calls"] == []


def test_update_json_output(patch_lookups: dict[str, Any]) -> None:
    """--output json emits the envelope shape from the spec."""
    patch_lookups["current_version"] = "0.5.0"
    patch_lookups["release"] = _fake_release(tag="v0.4.2", date="2026-05-30T12:00:00Z")

    result = CliRunner().invoke(cli, ["update", "--check", "--output", "json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    for key in (
        "current",
        "latest",
        "tag",
        "release_url",
        "install_method",
        "action",
        "error",
    ):
        assert key in payload, f"{key} missing from {payload}"
    assert payload["current"] == "0.5.0"
    assert payload["latest"] == "0.4.2"
    assert payload["tag"] == "v0.4.2"
    assert payload["install_method"] == "uv-tool"
    assert payload["error"] is None


def test_update_emits_loud_failure_on_unknown_flag() -> None:
    """Unknown flag: Click UsageError, exit code 2."""
    result = CliRunner().invoke(cli, ["update", "--frobnicate"])
    assert result.exit_code == 2, result.output
    assert "No such option" in result.output or "no such option" in result.output.lower()

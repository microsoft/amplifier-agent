"""Tests for the config show admin verb (Task 6).

Verifies that `amplifier-agent config show`:
  - Outputs valid JSON.
  - Reports provider with value=<bundle default> and source='bundle.default_provider'
    when bundle.md declares a default_provider (D6 / E5).
  - Reports XDG_CONFIG_HOME with source='env:XDG_CONFIG_HOME' when set.
  - Reports xdg_config_home with source='default' when XDG_CONFIG_HOME is absent.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from amplifier_agent_cli.__main__ import cli


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def test_config_show_outputs_valid_json(runner: CliRunner, tmp_path: Path) -> None:
    """config show must emit valid JSON to stdout with exit code 0."""
    cfg = tmp_path / "config"
    cache = tmp_path / "cache"
    state = tmp_path / "state"
    env = {
        "XDG_CONFIG_HOME": str(cfg),
        "XDG_CACHE_HOME": str(cache),
        "XDG_STATE_HOME": str(state),
        "ANTHROPIC_API_KEY": "sk-test",
    }
    result = runner.invoke(cli, ["config", "show"], env=env)
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert isinstance(parsed, dict)


def test_config_show_reports_provider_from_bundle_default(runner: CliRunner, tmp_path: Path) -> None:
    """provider.value/source reflect bundle.md's `default_provider:` field (D6 / E5).

    Env vars no longer influence the reported provider — the vendored bundle.md
    is the single source of truth. The vendored manifest ships
    `default_provider: anthropic`.
    """
    env = {
        # Set provider env vars to verify they do NOT influence the reported
        # source (E5: env-var-based detection was removed).
        "ANTHROPIC_API_KEY": "sk-test",
        "OPENAI_API_KEY": "",
        "AZURE_OPENAI_API_KEY": "",
        "AZURE_OPENAI_KEY": "",
        "OLLAMA_HOST": "",
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
    }
    result = runner.invoke(cli, ["config", "show"], env=env)
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["provider"]["value"] == "anthropic"
    assert parsed["provider"]["source"] == "bundle.default_provider"


def test_config_show_reports_xdg_config_home_from_env(runner: CliRunner, tmp_path: Path) -> None:
    """When XDG_CONFIG_HOME is set, xdg_config_home.value matches it and source='env:XDG_CONFIG_HOME'."""
    cfg = tmp_path / "my_config"
    env = {
        "XDG_CONFIG_HOME": str(cfg),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "ANTHROPIC_API_KEY": "sk-test",
    }
    result = runner.invoke(cli, ["config", "show"], env=env)
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["xdg_config_home"]["value"] == str(cfg)
    assert parsed["xdg_config_home"]["source"] == "env:XDG_CONFIG_HOME"


def test_config_show_reports_default_when_env_absent(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When XDG_CONFIG_HOME is unset, xdg_config_home.source=='default'."""
    # CliRunner.invoke(env=...) MERGES with os.environ rather than REPLACING it.
    # GitHub Actions runners set XDG_CONFIG_HOME by default, which would leak into
    # the test and make source='env:XDG_CONFIG_HOME' instead of the 'default' this
    # test is asserting. Explicitly delete the XDG_* env vars to ensure hermeticity.
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    env = {
        "HOME": str(tmp_path),
        "ANTHROPIC_API_KEY": "sk-test",
    }
    result = runner.invoke(cli, ["config", "show"], env=env, catch_exceptions=False)
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["xdg_config_home"]["source"] == "default"


def test_config_show_reports_flag_resolution_source(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When --config <path> is passed, host_config reports path=<path> and source='--config flag' (D8)."""
    cfg = tmp_path / "host.toml"
    cfg.write_text("# stub host config\n", encoding="utf-8")
    monkeypatch.delenv("AMPLIFIER_AGENT_CONFIG", raising=False)
    env = {
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "ANTHROPIC_API_KEY": "sk-test",
    }
    result = runner.invoke(cli, ["config", "show", "--config", str(cfg)], env=env)
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["host_config"]["path"] == str(cfg)
    assert parsed["host_config"]["source"] == "--config flag"


def test_config_show_reports_env_resolution_source(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When $AMPLIFIER_AGENT_CONFIG is set (and no --config flag), host_config reports
    path=<env-path> and source='$AMPLIFIER_AGENT_CONFIG env' (D8).
    """
    cfg = tmp_path / "host.toml"
    cfg.write_text("# stub host config\n", encoding="utf-8")
    env = {
        "HOME": str(tmp_path),
        "AMPLIFIER_AGENT_CONFIG": str(cfg),
    }
    result = runner.invoke(cli, ["config", "show"], env=env)
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["host_config"]["path"] == str(cfg)
    assert parsed["host_config"]["source"] == "$AMPLIFIER_AGENT_CONFIG env"


def test_config_show_reports_no_source_when_absent(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When neither --config flag nor $AMPLIFIER_AGENT_CONFIG is set, host_config
    reports path=None and source='none' (D8).
    """
    monkeypatch.delenv("AMPLIFIER_AGENT_CONFIG", raising=False)
    env = {
        "HOME": str(tmp_path),
    }
    result = runner.invoke(cli, ["config", "show"], env=env)
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["host_config"]["path"] is None
    assert parsed["host_config"]["source"] == "none"


def test_config_show_emits_parsed_values(runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When --config points at a valid config, host_config.parsed reflects the parsed values (D8).

    config show MUST surface the loader's output under host_config.parsed so that
    operators can verify what the engine will see — not just where the file
    lives.
    """
    cfg = tmp_path / "host.json"
    cfg.write_text(
        json.dumps({"mcp": {"verbose_servers": True}, "approval": {"auto_approve": False}}),
        encoding="utf-8",
    )
    monkeypatch.delenv("AMPLIFIER_AGENT_CONFIG", raising=False)
    env = {
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "ANTHROPIC_API_KEY": "sk-test",
    }
    result = runner.invoke(cli, ["config", "show", "--config", str(cfg)], env=env)
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["host_config"]["parsed"] == {
        "mcp": {"verbose_servers": True},
        "approval": {"auto_approve": False},
    }


def test_config_show_succeeds_when_config_malformed(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """config show stays exit-0 and surfaces parse_error when host config is malformed (D8).

    Operators must be able to locate the offending file *before* they can
    debug its contents. So even when load_config raises a ConfigError, the
    diagnostic command exits 0, reports path + source, sets parsed=None,
    and attaches parse_error={code,message} for the loader's error code.
    """
    cfg = tmp_path / "host.json"
    # Truncated JSON: valid prefix, no closing braces. Triggers
    # json.JSONDecodeError inside load_config, which maps to
    # ConfigError(code='config_malformed_json').
    cfg.write_text('{"mcp": {"verbose_servers": true,', encoding="utf-8")
    monkeypatch.delenv("AMPLIFIER_AGENT_CONFIG", raising=False)
    env = {
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "ANTHROPIC_API_KEY": "sk-test",
    }
    result = runner.invoke(cli, ["config", "show", "--config", str(cfg)], env=env)
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["host_config"]["path"] == str(cfg)
    assert parsed["host_config"]["source"] == "--config flag"
    assert parsed["host_config"]["parsed"] is None
    assert parsed["host_config"]["parse_error"]["code"] == "config_malformed_json"


def test_config_show_reports_bundle_default_even_with_no_env_vars(runner: CliRunner, tmp_path: Path) -> None:
    """Provider resolution is decoupled from env vars (E5/D6).

    When no provider env vars are set, ``config show`` still reports the
    bundle's ``default_provider`` because env-var-based detection no longer
    influences this command. Exit 0 throughout.
    """
    env = {
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        # Explicitly set all four provider keys to empty strings to unset them.
        "ANTHROPIC_API_KEY": "",
        "OPENAI_API_KEY": "",
        "AZURE_OPENAI_KEY": "",
        "OLLAMA_HOST": "",
    }
    result = runner.invoke(cli, ["config", "show"], env=env)
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    # bundle.md ships `default_provider: anthropic`.
    assert parsed["provider"]["value"] == "anthropic"
    assert parsed["provider"]["source"] == "bundle.default_provider"


def test_config_show_reports_merged_skills_block(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """config show surfaces the merged skills block (host overrides on top of bundle defaults) (D8).

    When the host config provides a ``skills`` block, ``config show`` MUST emit a
    top-level ``skills`` field reflecting the merged view: host-appended skills
    paths at the end, host visibility overrides applied on top of bundle
    defaults, and bundle defaults preserved for any field the host did not
    override.
    """
    cfg = tmp_path / "host.json"
    cfg.write_text(
        json.dumps(
            {
                "skills": {
                    "skills": ["/tmp/operator-skill-dir"],
                    "visibility": {"max_skills_visible": 10},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("AMPLIFIER_AGENT_CONFIG", raising=False)
    env = {
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "ANTHROPIC_API_KEY": "sk-test",
    }
    result = runner.invoke(cli, ["config", "show", "--config", str(cfg)], env=env)
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert "skills" in parsed
    skills_list = parsed["skills"]["skills"]
    visibility = parsed["skills"]["visibility"]
    # Host append at end: the host-provided path lands at the tail of the merged list.
    assert skills_list[-1] == "/tmp/operator-skill-dir"
    # Bundle-default skill roots remain present.
    assert ".amplifier/skills" in skills_list
    assert "~/.amplifier/skills" in skills_list
    # Host visibility override applied.
    assert visibility["max_skills_visible"] == 10
    # Bundle visibility defaults preserved for unset fields.
    assert visibility["enabled"] is True
    assert visibility["inject_role"] == "user"


def test_config_show_reports_bundle_skills_when_host_block_absent(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """config show surfaces bundle-default skills block verbatim when no host config (D8).

    With no host config and no ``skills`` overrides, ``config show`` MUST still
    emit a top-level ``skills`` field populated entirely from bundle defaults
    so operators see exactly what the engine will see.
    """
    monkeypatch.delenv("AMPLIFIER_AGENT_CONFIG", raising=False)
    env = {
        "HOME": str(tmp_path),
        "ANTHROPIC_API_KEY": "sk-test",
    }
    result = runner.invoke(cli, ["config", "show"], env=env, catch_exceptions=False)
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert "skills" in parsed
    skills_block = parsed["skills"]
    assert ".amplifier/skills" in skills_block["skills"]
    assert "~/.amplifier/skills" in skills_block["skills"]
    assert skills_block["visibility"]["enabled"] is True
    assert skills_block["visibility"]["max_skills_visible"] == 50

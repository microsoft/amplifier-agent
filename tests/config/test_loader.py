"""Tests for amplifier_agent_lib.config package skeleton (B1) and loader (B2+).

Verifies that ConfigError is a proper AaaError subclass that propagates
code/classification/message correctly so the CLI's existing
_build_error_envelope path emits a §4.1 envelope with
classification='protocol' (exit code 2 per _EXIT_CODE_BY_CLASSIFICATION).
"""

from __future__ import annotations

import pytest

from amplifier_agent_lib.config import ConfigError, load_config
from amplifier_agent_lib.protocol.errors import AaaError


def test_config_error_is_aaa_error_subclass() -> None:
    assert issubclass(ConfigError, AaaError)


def test_config_error_carries_code_classification_message() -> None:
    exc = ConfigError(
        code="config_unreadable",
        message="not found",
        classification="protocol",
    )
    assert exc.code == "config_unreadable"
    assert exc.classification == "protocol"
    assert exc.message == "not found"


def test_load_config_returns_none_when_no_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D1: returns None when neither --config arg nor env var is present."""
    monkeypatch.delenv("AMPLIFIER_AGENT_CONFIG", raising=False)
    assert load_config(config_arg=None) is None


def test_load_config_reads_flag_path_with_json_load(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D1/D3: --config flag tier reads file via json.load and returns parsed dict."""
    monkeypatch.delenv("AMPLIFIER_AGENT_CONFIG", raising=False)
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text('{"mcp": {"verbose_servers": true}}', encoding="utf-8")
    result = load_config(config_arg=str(cfg_path))
    assert result == {"mcp": {"verbose_servers": True}}


def test_load_config_reads_env_path_when_flag_absent(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D1: env-tier ($AMPLIFIER_AGENT_CONFIG) is read when --config flag is absent."""
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text('{"approval": {"auto_approve": false}}', encoding="utf-8")
    monkeypatch.setenv("AMPLIFIER_AGENT_CONFIG", str(cfg_path))
    assert load_config(config_arg=None) == {"approval": {"auto_approve": False}}


def test_load_config_flag_wins_over_env(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D1: --config flag tier wins over env tier when both are present."""
    flag_path = tmp_path / "flag.json"
    flag_path.write_text('{"mcp": {"verbose_servers": true}}', encoding="utf-8")
    env_path = tmp_path / "env.json"
    env_path.write_text('{"mcp": {"verbose_servers": false}}', encoding="utf-8")
    monkeypatch.setenv("AMPLIFIER_AGENT_CONFIG", str(env_path))
    assert load_config(config_arg=str(flag_path)) == {"mcp": {"verbose_servers": True}}


def test_load_config_raises_on_malformed_json(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D7: malformed JSON raises ConfigError(code='config_malformed_json')."""
    monkeypatch.delenv("AMPLIFIER_AGENT_CONFIG", raising=False)
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text('{"mcp": {"verbose_servers": true,', encoding="utf-8")
    with pytest.raises(ConfigError) as exc_info:
        load_config(config_arg=str(cfg_path))
    exc = exc_info.value
    assert exc.code == "config_malformed_json"
    assert exc.classification == "protocol"
    assert str(cfg_path) in exc.message


def test_load_config_raises_on_missing_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D2: --config pointing at a missing path raises ConfigError(code='config_unreadable')."""
    monkeypatch.delenv("AMPLIFIER_AGENT_CONFIG", raising=False)
    missing = "/missing/path/definitely/not/there.json"
    with pytest.raises(ConfigError) as exc_info:
        load_config(config_arg=missing)
    exc = exc_info.value
    assert exc.code == "config_unreadable"
    assert exc.classification == "protocol"
    assert missing in exc.message


def test_load_config_raises_on_missing_env_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D2: $AMPLIFIER_AGENT_CONFIG pointing at a missing path is NOT silently ignored.

    Setting the env var is an affirmative declaration that a config exists at
    that path; if the path does not exist we surface ConfigError rather than
    fall through to "no host config" defaults.
    """
    missing = "/missing/path/from/env/config.json"
    monkeypatch.setenv("AMPLIFIER_AGENT_CONFIG", missing)
    with pytest.raises(ConfigError) as exc_info:
        load_config(config_arg=None)
    exc = exc_info.value
    assert exc.code == "config_unreadable"
    assert exc.classification == "protocol"
    assert missing in exc.message


def test_load_config_rejects_unknown_top_level_key(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D7: unknown top-level key raises ConfigError(code='config_unknown_key').

    The schema is closed at the top level. An unknown key like 'notifications'
    must produce a hard error whose message names the offending key and lists
    all four valid keys, so the operator can correct the config immediately.
    """
    monkeypatch.delenv("AMPLIFIER_AGENT_CONFIG", raising=False)
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        '{"mcp": {}, "notifications": {"enabled": true}}',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(config_arg=str(cfg_path))
    exc = exc_info.value
    assert exc.code == "config_unknown_key"
    assert exc.classification == "protocol"
    assert "notifications" in exc.message
    # All four valid keys must be listed for operator guidance.
    assert "mcp" in exc.message
    assert "approval" in exc.message
    assert "provider" in exc.message
    assert "allowProtocolSkew" in exc.message


def test_load_config_accepts_all_four_known_keys(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D7: a config containing all four valid top-level keys parses cleanly."""
    monkeypatch.delenv("AMPLIFIER_AGENT_CONFIG", raising=False)
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        '{"mcp": {}, "approval": {}, "provider": {}, "allowProtocolSkew": false}',
        encoding="utf-8",
    )
    result = load_config(config_arg=str(cfg_path))
    assert result is not None
    assert set(result.keys()) == {"mcp", "approval", "provider", "allowProtocolSkew"}


def test_loader_accepts_skills_top_level_key(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D11: ``skills`` is the fifth recognized top-level host_config key.

    A JSON config with ``{"skills": {}}`` must parse cleanly and preserve the
    block in the returned mapping, mirroring how the loader already treats
    ``mcp``.  Before D11, this key was outside the closed top-level schema
    and triggered ``config_unknown_key``.
    """
    monkeypatch.delenv("AMPLIFIER_AGENT_CONFIG", raising=False)
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text('{"skills": {}}', encoding="utf-8")
    parsed = load_config(config_arg=str(cfg_path))
    assert parsed is not None
    assert parsed == {"skills": {}}
    assert parsed["skills"] == {}


def test_load_config_rejects_non_string_approval_pattern(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D7 type guard: non-string items in approval.patterns raise ConfigError.

    JSON parses literal types unambiguously, but a host could still pass a
    number/bool/null inside the patterns array. The loader enforces the
    string-only constraint so downstream hooks-approval matching receives
    only strings.
    """
    monkeypatch.delenv("AMPLIFIER_AGENT_CONFIG", raising=False)
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text('{"approval": {"patterns": [123]}}', encoding="utf-8")
    with pytest.raises(ConfigError) as exc_info:
        load_config(config_arg=str(cfg_path))
    exc = exc_info.value
    assert exc.code == "config_invalid_type"
    assert exc.classification == "protocol"
    assert "approval.patterns" in exc.message
    assert "string" in exc.message.lower()


def test_load_config_accepts_string_patterns(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D7 type guard: well-typed string-only approval.patterns parses cleanly."""
    monkeypatch.delenv("AMPLIFIER_AGENT_CONFIG", raising=False)
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        '{"approval": {"patterns": ["no", "rm -rf"]}}',
        encoding="utf-8",
    )
    result = load_config(config_arg=str(cfg_path))
    assert result == {"approval": {"patterns": ["no", "rm -rf"]}}


def test_load_config_rejects_unknown_provider_module(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A3/D7: an unknown provider.module value raises ConfigError at parse time.

    The merger silently falls through on invalid provider.module (defensive,
    preserving bundle default).  The loader catches it loudly so the operator
    sees the error immediately rather than as a silent no-op much later.
    The error message must enumerate all four valid module names so the
    operator can correct the typo without consulting documentation.
    """
    monkeypatch.delenv("AMPLIFIER_AGENT_CONFIG", raising=False)
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text('{"provider": {"module": "auto"}}', encoding="utf-8")
    with pytest.raises(ConfigError) as exc_info:
        load_config(config_arg=str(cfg_path))
    exc = exc_info.value
    assert exc.code == "config_invalid_provider_module"
    assert exc.classification == "protocol"
    # Offending value surfaces in the message so the operator can find it.
    assert "auto" in exc.message
    # All four valid module names must be listed for operator guidance.
    assert "anthropic" in exc.message
    assert "openai" in exc.message
    assert "azure-openai" in exc.message
    assert "ollama" in exc.message


def test_load_config_accepts_each_valid_provider_module(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A3/D7: each of the four supported provider.module values parses cleanly."""
    monkeypatch.delenv("AMPLIFIER_AGENT_CONFIG", raising=False)
    for module_name in ("anthropic", "openai", "azure-openai", "ollama"):
        cfg_path = tmp_path / f"config-{module_name}.json"
        cfg_path.write_text(
            f'{{"provider": {{"module": "{module_name}"}}}}',
            encoding="utf-8",
        )
        result = load_config(config_arg=str(cfg_path))
        assert result == {"provider": {"module": module_name}}


def test_loader_rejects_skills_skills_non_list(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D11 + D7 type guard: ``skills.skills`` must be a JSON array.

    A non-list value (e.g. a string) is a closed-schema violation and must
    raise ``ConfigError(code='config_invalid_type', classification='protocol')``
    at parse time, mirroring how the loader treats a non-list
    ``approval.patterns`` value.  Surfacing this at parse time prevents the
    downstream skills-loader from receiving a structurally invalid value and
    failing opaquely far from the offending config file.
    """
    monkeypatch.delenv("AMPLIFIER_AGENT_CONFIG", raising=False)
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text('{"skills": {"skills": "not-a-list"}}', encoding="utf-8")
    with pytest.raises(ConfigError) as exc_info:
        load_config(config_arg=str(cfg_path))
    exc = exc_info.value
    assert exc.code == "config_invalid_type"
    assert exc.classification == "protocol"
    assert "skills.skills" in exc.message
    assert "list" in exc.message.lower()


def test_loader_rejects_skills_skills_non_string_member(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D11 + D7 type guard: each member of ``skills.skills`` must be a string.

    A list containing a non-string member (e.g. an integer) must raise
    ``ConfigError(code='config_invalid_type', classification='protocol')``
    with a message that names the offending index and the wrong type, so the
    operator can locate the bad entry without diffing the file by hand.
    Mirrors the per-member validation already applied to ``approval.patterns``.
    """
    monkeypatch.delenv("AMPLIFIER_AGENT_CONFIG", raising=False)
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text('{"skills": {"skills": ["ok", 42]}}', encoding="utf-8")
    with pytest.raises(ConfigError) as exc_info:
        load_config(config_arg=str(cfg_path))
    exc = exc_info.value
    assert exc.code == "config_invalid_type"
    assert exc.classification == "protocol"
    assert "skills.skills" in exc.message
    assert "string" in exc.message.lower()


def test_loader_accepts_skills_skills_valid_list(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D11 + D7: a well-typed ``skills.skills`` list of source URIs parses cleanly.

    Three canonical source-URI shapes are represented:
      * a git URI (remote source),
      * a workspace-relative ``.amplifier/skills`` path,
      * a user-home ``~/.amplifier/skills`` path.

    The parsed value must be preserved verbatim so the downstream skills
    loader receives the same list the operator wrote.
    """
    monkeypatch.delenv("AMPLIFIER_AGENT_CONFIG", raising=False)
    cfg_path = tmp_path / "config.json"
    skills_list = [
        "git+https://github.com/example/skills.git",
        ".amplifier/skills",
        "~/.amplifier/skills",
    ]
    cfg_path.write_text(
        '{"skills": {"skills": ['
        '"git+https://github.com/example/skills.git", '
        '".amplifier/skills", '
        '"~/.amplifier/skills"'
        "]}}",
        encoding="utf-8",
    )
    parsed = load_config(config_arg=str(cfg_path))
    assert parsed is not None
    assert parsed["skills"]["skills"] == skills_list

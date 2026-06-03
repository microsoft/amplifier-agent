"""End-to-end: amplifier-agent run --config <path> <prompt> reflects merged config."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from amplifier_agent_cli.__main__ import cli
from amplifier_agent_lib import _runtime


def test_run_with_config_threads_overrides_through_to_spec(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G1: --config flag's mcp+provider blocks reach _TurnSpec.host_config and spec.provider.

    End-to-end smoke test that verifies the config file is loaded by the CLI,
    threaded through to the _TurnSpec, and that the provider resolution honors
    host.provider.module ahead of the bundle default.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        '{"mcp": {"verbose_servers": true}, "provider": {"module": "anthropic"}}',
        encoding="utf-8",
    )

    captured: dict[str, Any] = {}

    async def _fake_execute_turn(spec):
        captured["host_config"] = spec.host_config
        captured["provider"] = spec.provider
        return {"reply": "stub", "turnId": "turn-1"}

    runner = CliRunner()
    with patch("amplifier_agent_cli.modes.single_turn._execute_turn", _fake_execute_turn):
        result = runner.invoke(cli, ["run", "-y", "--config", str(cfg_path), "hello"])

    assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}. Output:\n{result.output}"
    assert captured["host_config"] == {
        "mcp": {"verbose_servers": True},
        "provider": {"module": "anthropic"},
    }
    assert captured["provider"] == "anthropic"


def test_run_without_config_matches_today_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G2: no --config + no env → host_config is None, provider is bundle default.

    Acceptance criteria from §9: `amplifier-agent run "..."` with no config file
    and no env override produces identical behavior to today — the spec's
    host_config stays None and provider resolves via the bundle default
    ('anthropic') rather than via host.provider.module.
    """
    monkeypatch.delenv("AMPLIFIER_AGENT_CONFIG", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    captured: dict[str, Any] = {}

    async def _fake_execute_turn(spec):
        captured["host_config"] = spec.host_config
        captured["provider"] = spec.provider
        return {"reply": "stub", "turnId": "turn-1"}

    runner = CliRunner()
    with patch("amplifier_agent_cli.modes.single_turn._execute_turn", _fake_execute_turn):
        result = runner.invoke(cli, ["run", "-y", "hello"])

    assert result.exit_code == 0, f"Expected exit 0, got {result.exit_code}. Output:\n{result.output}"
    assert captured["host_config"] is None
    assert captured["provider"] == "anthropic"


class _FakePreparedRealShape:
    """Stand-in for ``PreparedBundle`` with the REAL mount_plan shape.

    Mirrors ``Bundle.to_mount_plan()`` output: tools/hooks/providers are
    LISTS of ``{module, config, source}`` dicts. Used to verify the merger
    output lands in the same list entries the kernel reads from -- not in
    phantom top-level keys.
    """

    def __init__(self) -> None:
        self.mount_plan: dict[str, Any] = {
            "session": {},
            "tools": [
                {
                    "module": "tool-mcp",
                    "config": {"verbose_servers": False, "max_content_size": 50000},
                    "source": "git+https://example/tool-mcp",
                },
            ],
            "hooks": [
                {
                    "module": "hooks-approval",
                    "config": {"auto_approve": False, "default_action": "deny"},
                    "source": "git+https://example/hooks-approval",
                },
            ],
            "providers": [
                {
                    "module": "anthropic-provider",
                    "config": {"default_model": "claude-sonnet-4-5"},
                    "source": "git+https://example/anthropic-provider",
                },
            ],
            "agents": {},
            "spawn": {},
        }


def test_merged_config_lands_in_mount_plan_list_entries_not_phantom_keys() -> None:
    """End-to-end: merged host config writes back into mount_plan list entries.

    Regression for the silent-drop bug where ``merge_config`` was being
    invoked with section names (``"tools"``/``"hooks"``/``"providers"``) as
    if they were module IDs.  The merger's output then went into phantom
    top-level keys (``mount_plan["tool-mcp"]``) that the kernel never
    reads, so every host override was silently dropped.

    The fix builds a ``{module_id: config_dict}`` view from the list
    entries, runs the merge, and writes results back into the SAME list
    entries.  This test asserts the final mount_plan state directly.
    """
    fake_prepared = _FakePreparedRealShape()
    host_config: dict[str, Any] = {
        "mcp": {"verbose_servers": True, "servers": {"time": {}}},
        "approval": {"auto_approve": True},
        "provider": {
            "module": "anthropic",
            "config": {"default_model": "claude-opus-4-6"},
        },
    }

    _runtime.make_turn_handler(
        fake_prepared,  # type: ignore[arg-type]
        cwd=None,
        is_resumed=False,
        host_config=host_config,
    )

    # tool-mcp config (in the tools list entry)
    mcp_entry = next(e for e in fake_prepared.mount_plan["tools"] if e["module"] == "tool-mcp")
    assert mcp_entry["config"] == {
        "verbose_servers": True,  # host override
        "max_content_size": 50000,  # bundle key preserved
        "servers": {"time": {}},  # new host key
    }

    # hooks-approval config (in the hooks list entry)
    approval_entry = next(e for e in fake_prepared.mount_plan["hooks"] if e["module"] == "hooks-approval")
    assert approval_entry["config"] == {
        "auto_approve": True,  # host override
        "default_action": "deny",  # bundle key preserved
    }

    # anthropic-provider config (in the providers list entry)
    provider_entry = next(e for e in fake_prepared.mount_plan["providers"] if e["module"] == "anthropic-provider")
    assert provider_entry["config"] == {"default_model": "claude-opus-4-6"}

    # Negative assertion: no phantom top-level keys created by the merge.
    for phantom in ("tool-mcp", "hooks-approval", "anthropic-provider"):
        assert phantom not in fake_prepared.mount_plan, (
            f"Merge created phantom top-level key {phantom!r} -- "
            f"this is the original bug. Merged config must land in list entries."
        )

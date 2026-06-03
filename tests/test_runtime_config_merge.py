"""Tests for host_config plumbing through _runtime.make_turn_handler (D5).

Covers:
  1. ``make_turn_handler`` accepts ``host_config`` as a keyword argument
     without raising, and returns a callable handler.
  2. ``make_turn_handler`` invokes ``merge_config`` with bundle module
     configs extracted from the REAL mount_plan shape (``tools``/``hooks``/
     ``providers`` are lists of ``{module, config, source}`` dicts), and
     writes the merged values back into the SAME list entries -- not into
     phantom top-level keys keyed by section name.
  3. ``mcp.configPath`` in host config wires to ``AMPLIFIER_MCP_CONFIG``
     (D4 engine-level convenience key), with CLI flag taking precedence.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from amplifier_agent_lib import _runtime


class _FakePrepared:
    """Minimal stand-in for ``PreparedBundle`` using the REAL mount_plan shape.

    ``make_turn_handler`` only touches ``prepared.mount_plan`` at handler
    construction time (the rest happens inside the returned coroutine, which
    these tests never invoke).

    The shape here mirrors what ``Bundle.to_mount_plan()`` actually produces
    (see amplifier_foundation/bundle/_dataclass.py::to_mount_plan):
        - ``session``: dict
        - ``providers``/``tools``/``hooks``: list of {module, config, source}
        - ``agents``/``spawn``: dict
    """

    def __init__(self, mount_plan: dict[str, Any] | None = None) -> None:
        if mount_plan is not None:
            self.mount_plan: dict[str, Any] = mount_plan
            return

        # Default real-shaped mount_plan with one entry per overridable section
        # so the merge path has something concrete to read and update.
        self.mount_plan = {
            "session": {"orchestrator": {}, "context": {}},
            "tools": [
                {
                    "module": "tool-mcp",
                    "config": {"verbose_servers": False, "max_content_size": 50000},
                    "source": "git+https://example/tool-mcp",
                },
                {
                    "module": "tool-todo",
                    "config": {},
                    "source": "git+https://example/tool-todo",
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


def test_make_turn_handler_accepts_host_config_kwarg() -> None:
    """make_turn_handler must accept ``host_config`` without raising."""
    fake_prepared = _FakePrepared()

    handler = _runtime.make_turn_handler(
        fake_prepared,  # type: ignore[arg-type]
        cwd=None,
        is_resumed=False,
        host_config=None,
    )

    assert callable(handler)


def test_make_turn_handler_calls_merge_config_when_host_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """make_turn_handler must forward host_config into merge_config.

    Also verifies the merger is called with the {module_id: config_dict}
    shape it actually expects -- extracted from the list entries, not the
    top-level section dict.
    """
    captured: dict[str, Any] = {}

    def _fake_merge_config(
        *,
        bundle_modules: dict[str, dict[str, Any]],
        host_config: dict[str, Any] | None,
    ) -> tuple[dict[str, dict[str, Any]], bool]:
        captured["bundle_modules"] = bundle_modules
        captured["host_config"] = host_config
        # Return the bundle_modules unchanged so the writeback path runs.
        return bundle_modules, False

    monkeypatch.setattr(_runtime, "merge_config", _fake_merge_config)

    fake_prepared = _FakePrepared()
    host_config = {"mcp": {"verbose_servers": True}}

    _runtime.make_turn_handler(
        fake_prepared,  # type: ignore[arg-type]
        cwd=None,
        is_resumed=False,
        host_config=host_config,
    )

    assert captured["host_config"] == host_config
    # Confirm the merger sees {module_id: config_dict}, not section names.
    assert "tool-mcp" in captured["bundle_modules"]
    assert "hooks-approval" in captured["bundle_modules"]
    assert "anthropic-provider" in captured["bundle_modules"]
    # And does NOT see the bogus shape the old code passed.
    assert "tools" not in captured["bundle_modules"]
    assert "hooks" not in captured["bundle_modules"]
    assert "providers" not in captured["bundle_modules"]


def test_mcp_inline_overrides_land_in_tool_mcp_entry() -> None:
    """host.mcp keys merge into the tool-mcp list entry's config, not a top-level key."""
    fake_prepared = _FakePrepared()
    host_config = {
        "mcp": {
            "verbose_servers": True,
            "servers": {"time": {"command": "uvx", "args": ["mcp-server-time"]}},
        }
    }

    _runtime.make_turn_handler(
        fake_prepared,  # type: ignore[arg-type]
        cwd=None,
        is_resumed=False,
        host_config=host_config,
    )

    # The merge must land in the SAME list entry the kernel reads.
    tool_entries = fake_prepared.mount_plan["tools"]
    mcp_entry = next(e for e in tool_entries if e["module"] == "tool-mcp")
    assert mcp_entry["config"] == {
        "verbose_servers": True,  # host override
        "max_content_size": 50000,  # bundle key preserved
        "servers": {"time": {"command": "uvx", "args": ["mcp-server-time"]}},  # new host key
    }
    # And NOT in a phantom top-level key.
    assert "tool-mcp" not in fake_prepared.mount_plan


def test_approval_overrides_land_in_hooks_approval_entry() -> None:
    """host.approval keys merge into the hooks-approval list entry's config."""
    fake_prepared = _FakePrepared()
    host_config = {"approval": {"auto_approve": True}}

    _runtime.make_turn_handler(
        fake_prepared,  # type: ignore[arg-type]
        cwd=None,
        is_resumed=False,
        host_config=host_config,
    )

    hook_entries = fake_prepared.mount_plan["hooks"]
    approval_entry = next(e for e in hook_entries if e["module"] == "hooks-approval")
    assert approval_entry["config"]["auto_approve"] is True  # host override
    assert approval_entry["config"]["default_action"] == "deny"  # bundle key preserved
    # No phantom top-level key.
    assert "hooks-approval" not in fake_prepared.mount_plan


def test_provider_config_overrides_land_in_provider_entry() -> None:
    """host.provider.config keys merge into the named provider entry's config."""
    fake_prepared = _FakePrepared()
    host_config = {
        "provider": {
            "module": "anthropic",
            "config": {"default_model": "claude-opus-4-6"},
        }
    }

    _runtime.make_turn_handler(
        fake_prepared,  # type: ignore[arg-type]
        cwd=None,
        is_resumed=False,
        host_config=host_config,
    )

    provider_entries = fake_prepared.mount_plan["providers"]
    anthropic_entry = next(e for e in provider_entries if e["module"] == "anthropic-provider")
    assert anthropic_entry["config"]["default_model"] == "claude-opus-4-6"
    # No phantom top-level key.
    assert "anthropic-provider" not in fake_prepared.mount_plan


def test_mcp_config_path_in_host_sets_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D4: host_config.mcp.configPath → AMPLIFIER_MCP_CONFIG env var.

    With the ``--mcp-config-path`` CLI flag removed, host_config is the
    single engine-side translation site for the MCP config path. Hosts can
    alternatively set ``AMPLIFIER_MCP_CONFIG`` directly in the engine's
    subprocess environment, which ``tool-mcp`` reads natively.
    """
    monkeypatch.delenv("AMPLIFIER_MCP_CONFIG", raising=False)
    fake_prepared = _FakePrepared()
    host_config = {"mcp": {"configPath": "/tmp/test-mcp.json"}}

    _runtime.make_turn_handler(
        fake_prepared,  # type: ignore[arg-type]
        cwd=None,
        is_resumed=False,
        host_config=host_config,
    )

    assert os.environ["AMPLIFIER_MCP_CONFIG"] == "/tmp/test-mcp.json"

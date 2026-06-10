"""Tests for spawn.py — hydrate_agent_overlay, merge_configs, spawn_sub_session.

Covers:
1. hydrate_agent_overlay parses all four vendored agent markdown files
2. merge_configs deep-merges tools/hooks by module ID, sets instruction
3. spawn_sub_session returns dict with output and session_id (mocked session)
4. spawn_sub_session raises ValueError for unknown agent names
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

AGENTS_DIR: Path = Path(__file__).parent.parent / "src" / "amplifier_agent_lib" / "bundle" / "agents"


# ---------------------------------------------------------------------------
# 1. hydrate_agent_overlay — parses vendored agent .md files
# ---------------------------------------------------------------------------


def test_hydrate_explorer_has_non_empty_instruction() -> None:
    """hydrate_agent_overlay(explorer.md) must return non-empty 'instruction'."""
    from amplifier_agent_lib.spawn import hydrate_agent_overlay

    overlay = hydrate_agent_overlay(AGENTS_DIR / "explorer.md")
    assert "instruction" in overlay
    assert isinstance(overlay["instruction"], str)
    assert len(overlay["instruction"]) > 0


def test_hydrate_explorer_body_contains_agent_header() -> None:
    """The instruction body must include the '# Explorer' markdown header."""
    from amplifier_agent_lib.spawn import hydrate_agent_overlay

    overlay = hydrate_agent_overlay(AGENTS_DIR / "explorer.md")
    assert "# Explorer" in overlay["instruction"]


def test_hydrate_explorer_has_tools() -> None:
    """hydrate_agent_overlay(explorer.md) must return a non-empty 'tools' list."""
    from amplifier_agent_lib.spawn import hydrate_agent_overlay

    overlay = hydrate_agent_overlay(AGENTS_DIR / "explorer.md")
    assert "tools" in overlay
    assert isinstance(overlay["tools"], list)
    assert len(overlay["tools"]) > 0
    # Each tool entry must have a 'module' key
    for tool in overlay["tools"]:
        assert "module" in tool


def test_hydrate_explorer_model_role() -> None:
    """hydrate_agent_overlay(explorer.md) must return model_role == ['research', 'general']."""
    from amplifier_agent_lib.spawn import hydrate_agent_overlay

    overlay = hydrate_agent_overlay(AGENTS_DIR / "explorer.md")
    assert "model_role" in overlay
    assert overlay["model_role"] == ["research", "general"]


@pytest.mark.parametrize("agent_name", ["explorer", "planner", "coder", "tester"])
def test_all_agents_have_instruction_and_model_role(agent_name: str) -> None:
    """Every vendored agent file must produce an overlay with instruction and model_role."""
    from amplifier_agent_lib.spawn import hydrate_agent_overlay

    overlay = hydrate_agent_overlay(AGENTS_DIR / f"{agent_name}.md")
    assert overlay.get("instruction"), f"{agent_name}: instruction is empty"
    assert "model_role" in overlay, f"{agent_name}: model_role missing"


@pytest.mark.parametrize("agent_name", ["explorer", "planner", "coder", "tester"])
def test_all_agents_have_tools_list(agent_name: str) -> None:
    """Every vendored agent file must produce an overlay with a non-empty tools list."""
    from amplifier_agent_lib.spawn import hydrate_agent_overlay

    overlay = hydrate_agent_overlay(AGENTS_DIR / f"{agent_name}.md")
    assert "tools" in overlay, f"{agent_name}: tools missing"
    assert len(overlay["tools"]) > 0, f"{agent_name}: tools list is empty"


# ---------------------------------------------------------------------------
# 2. merge_configs — deep merge with module-list awareness
# ---------------------------------------------------------------------------


def test_merge_configs_tools_merged_by_module_id() -> None:
    """Tools from parent and overlay are merged by 'module' ID, not replaced."""
    from amplifier_agent_lib.spawn import merge_configs

    parent = {"tools": [{"module": "tool-a"}, {"module": "tool-b"}]}
    overlay = {"tools": [{"module": "tool-c"}]}
    result = merge_configs(parent, overlay)
    modules = {t["module"] for t in result["tools"]}
    assert "tool-a" in modules
    assert "tool-b" in modules
    assert "tool-c" in modules


def test_merge_configs_hooks_merged_by_module_id() -> None:
    """Hooks from parent and overlay are merged by 'module' ID."""
    from amplifier_agent_lib.spawn import merge_configs

    parent = {"hooks": [{"module": "hook-a"}]}
    overlay = {"hooks": [{"module": "hook-b"}]}
    result = merge_configs(parent, overlay)
    modules = {h["module"] for h in result["hooks"]}
    assert "hook-a" in modules
    assert "hook-b" in modules


def test_merge_configs_instruction_propagated() -> None:
    """'instruction' key in overlay ends up in the merged result."""
    from amplifier_agent_lib.spawn import merge_configs

    parent: dict = {"tools": []}
    overlay = {"instruction": "Be a great explorer", "tools": []}
    result = merge_configs(parent, overlay)
    assert result["instruction"] == "Be a great explorer"


def test_merge_configs_overlay_scalar_wins() -> None:
    """Scalar values in overlay override parent values."""
    from amplifier_agent_lib.spawn import merge_configs

    parent = {"model_role": "general", "tools": []}
    overlay = {"model_role": "coding", "tools": []}
    result = merge_configs(parent, overlay)
    assert result["model_role"] == "coding"


def test_merge_configs_parent_values_preserved_when_no_overlay() -> None:
    """Parent keys not touched by overlay are preserved in the result."""
    from amplifier_agent_lib.spawn import merge_configs

    parent = {"session": {"orchestrator": {"module": "loop-streaming"}}, "tools": []}
    overlay = {"tools": [{"module": "tool-extra"}]}
    result = merge_configs(parent, overlay)
    assert result["session"]["orchestrator"]["module"] == "loop-streaming"


# ---------------------------------------------------------------------------
# 3. spawn_sub_session — returns dict with output and session_id (mocked)
# ---------------------------------------------------------------------------


def _make_mock_parent_session(config: dict | None = None) -> MagicMock:
    """Build a minimal mock parent session."""
    parent = MagicMock()
    parent.session_id = "parent-session-id"
    parent.config = config or {
        "session": {
            "orchestrator": {"module": "loop-streaming"},
            "context": {"module": "context-simple"},
            "provider": {"module": "anthropic-provider"},
        },
        "tools": [],
        "hooks": [],
    }
    coordinator = MagicMock()
    coordinator.get.return_value = None
    coordinator.get_capability.return_value = None
    coordinator.approval_system = None
    coordinator.display_system = None
    parent.coordinator = coordinator
    return parent


def _make_mock_child_session(reply: str = "child reply") -> MagicMock:
    """Build a minimal mock child session."""
    child = MagicMock()
    child.session_id = "child-session-id"
    child.initialize = AsyncMock()
    child.execute = AsyncMock(return_value=reply)
    child.cleanup = AsyncMock()
    child_coord = MagicMock()
    child_coord.mount = AsyncMock()
    child_coord.get.return_value = None
    child_coord.register_capability = MagicMock()
    child.coordinator = child_coord
    return child


@pytest.mark.asyncio
async def test_spawn_sub_session_returns_dict_with_output_and_session_id() -> None:
    """spawn_sub_session must return a dict with 'output' and 'session_id' keys."""
    from amplifier_agent_lib.spawn import spawn_sub_session

    parent = _make_mock_parent_session()
    child = _make_mock_child_session("PONG")

    with (
        patch("amplifier_core.AmplifierSession", return_value=child),
        patch("amplifier_foundation.generate_sub_session_id", return_value="sub-123"),
    ):
        result = await spawn_sub_session(
            agent_name="explorer",
            instruction="Return the string PONG",
            parent_session=parent,
            agent_configs={"explorer": {"instruction": "You are explorer", "tools": []}},
        )

    assert "output" in result
    assert "session_id" in result
    assert result["output"] == "PONG"
    assert result["session_id"] == "sub-123"


@pytest.mark.asyncio
async def test_spawn_sub_session_returns_status_success() -> None:
    """spawn_sub_session must return status='success', turn_count, and metadata."""
    from amplifier_agent_lib.spawn import spawn_sub_session

    parent = _make_mock_parent_session()
    child = _make_mock_child_session("reply")

    with (
        patch("amplifier_core.AmplifierSession", return_value=child),
        patch("amplifier_foundation.generate_sub_session_id", return_value="sub-456"),
    ):
        result = await spawn_sub_session(
            agent_name="explorer",
            instruction="Do stuff",
            parent_session=parent,
            agent_configs={"explorer": {"instruction": "You are explorer", "tools": []}},
        )

    assert result.get("status") == "success"
    assert "turn_count" in result
    assert "metadata" in result


@pytest.mark.asyncio
async def test_spawn_sub_session_raises_for_unknown_agent() -> None:
    """spawn_sub_session must raise ValueError when agent_name is not in agent_configs."""
    from amplifier_agent_lib.spawn import spawn_sub_session

    parent = _make_mock_parent_session()

    with pytest.raises(ValueError, match="not found"):
        await spawn_sub_session(
            agent_name="nonexistent-agent",
            instruction="Do stuff",
            parent_session=parent,
            agent_configs={},
        )


@pytest.mark.asyncio
async def test_spawn_sub_session_calls_initialize_and_execute() -> None:
    """spawn_sub_session must call child.initialize() before child.execute(instruction)."""
    from amplifier_agent_lib.spawn import spawn_sub_session

    parent = _make_mock_parent_session()
    child = _make_mock_child_session("done")

    with (
        patch("amplifier_core.AmplifierSession", return_value=child),
        patch("amplifier_foundation.generate_sub_session_id", return_value="sub-789"),
    ):
        await spawn_sub_session(
            agent_name="planner",
            instruction="Design something",
            parent_session=parent,
            agent_configs={"planner": {"instruction": "You plan", "tools": []}},
        )

    child.initialize.assert_awaited_once()
    child.execute.assert_awaited_once_with("Design something")
    child.cleanup.assert_awaited_once()


@pytest.mark.asyncio
async def test_spawn_sub_session_self_delegation_inherits_parent_config() -> None:
    """spawn_sub_session with agent_name='self' must not raise even without config entry."""
    from amplifier_agent_lib.spawn import spawn_sub_session

    parent = _make_mock_parent_session()
    child = _make_mock_child_session("self-reply")

    with (
        patch("amplifier_core.AmplifierSession", return_value=child),
        patch("amplifier_foundation.generate_sub_session_id", return_value="sub-self"),
    ):
        result = await spawn_sub_session(
            agent_name="self",
            instruction="Do the same task",
            parent_session=parent,
            agent_configs={},  # "self" requires no entry
        )

    assert result["output"] == "self-reply"

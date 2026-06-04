"""Tests for parent->child capability inheritance in spawn_sub_session.

Regression for the delegate-sub-session auto-deny bug: parents register
``approval.request`` and ``display.emit`` via the coordinator capability
registry (see ``_runtime.py``), but ``spawn_sub_session`` historically only
forwarded the Rust-backed ``coordinator.approval_system`` /
``coordinator.display_system`` properties. Those slots are uncoupled --
the registry-based registration does not populate the property -- so
sub-sessions silently received ``provider=None`` and the hooks-approval
hook auto-denied every side-effecting tool call.

This test pins the registry-to-registry inheritance contract: after
``child_session.initialize()``, the parent's ``approval.request`` and
``display.emit`` capabilities (as read from the registry) must be
re-registered on the child's coordinator.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_parent_with_registered_capabilities(
    approval_fn,
    display_fn,
) -> MagicMock:
    """Mock a parent session that registered approval+display via the registry.

    Mirrors how ``_runtime.py`` wires them: via
    ``coordinator.register_capability(...)``. Critically, the Rust-backed
    ``coordinator.approval_system`` / ``coordinator.display_system``
    properties remain ``None`` -- which is the exact production shape that
    triggered the bug.
    """
    parent = MagicMock()
    parent.session_id = "parent-session-id"
    parent.config = {
        "session": {
            "orchestrator": {"module": "loop-streaming"},
            "context": {"module": "context-simple"},
            "provider": {"module": "anthropic-provider"},
        },
        "tools": [],
        "hooks": [],
    }
    coordinator = MagicMock()

    registry: dict[str, object] = {
        "approval.request": approval_fn,
        "display.emit": display_fn,
    }

    def _get_capability(key: str):
        return registry.get(key)

    coordinator.get_capability.side_effect = _get_capability
    coordinator.get.return_value = None
    # Properties are NOT populated -- mirrors _runtime.py's actual wiring.
    coordinator.approval_system = None
    coordinator.display_system = None
    parent.coordinator = coordinator
    return parent


def _make_child_with_real_registry() -> MagicMock:
    """Mock a child whose coordinator records register_capability calls.

    The child's ``register_capability`` writes to a real dict so the test
    can assert what ended up registered. ``get_capability`` reads from the
    same dict, simulating the real coordinator's registry semantics.
    """
    child = MagicMock()
    child.session_id = "child-session-id"
    child.initialize = AsyncMock()
    child.execute = AsyncMock(return_value="ok")
    child.cleanup = AsyncMock()

    child_coord = MagicMock()
    child_coord.mount = AsyncMock()
    child_coord.get.return_value = None

    registry: dict[str, object] = {}

    def _register(key: str, value: object) -> None:
        registry[key] = value

    def _get_capability(key: str):
        return registry.get(key)

    child_coord.register_capability = MagicMock(side_effect=_register)
    child_coord.get_capability.side_effect = _get_capability
    # Expose for assertions.
    child_coord._test_registry = registry  # type: ignore[attr-defined]
    child.coordinator = child_coord
    return child


@pytest.mark.asyncio
async def test_spawn_inherits_approval_request_capability_from_parent_registry() -> None:
    """Child must inherit the parent's ``approval.request`` capability."""
    from amplifier_agent_lib.spawn import spawn_sub_session

    async def fake_approval_request(*args, **kwargs):  # pragma: no cover - identity check only
        return {"granted": True}

    async def fake_display_emit(*args, **kwargs):  # pragma: no cover - identity check only
        return None

    parent = _make_parent_with_registered_capabilities(
        approval_fn=fake_approval_request,
        display_fn=fake_display_emit,
    )
    child = _make_child_with_real_registry()

    with (
        patch("amplifier_core.AmplifierSession", return_value=child),
        patch("amplifier_foundation.generate_sub_session_id", return_value="sub-1"),
    ):
        await spawn_sub_session(
            agent_name="explorer",
            instruction="do stuff",
            parent_session=parent,
            agent_configs={"explorer": {"instruction": "explorer", "tools": []}},
        )

    inherited = child.coordinator.get_capability("approval.request")
    assert inherited is fake_approval_request, (
        "child.coordinator.get_capability('approval.request') must return the "
        "exact function the parent registered -- otherwise hooks-approval "
        "auto-denies every side-effecting tool call in the sub-session."
    )


@pytest.mark.asyncio
async def test_spawn_inherits_display_emit_capability_from_parent_registry() -> None:
    """Child must inherit the parent's ``display.emit`` capability.

    Same structural bug shape as approval.request: registry-based parent
    registration was uncoupled from the property slot spawn used to read.
    """
    from amplifier_agent_lib.spawn import spawn_sub_session

    async def fake_approval_request(*args, **kwargs):  # pragma: no cover - identity check only
        return {"granted": True}

    async def fake_display_emit(*args, **kwargs):  # pragma: no cover - identity check only
        return None

    parent = _make_parent_with_registered_capabilities(
        approval_fn=fake_approval_request,
        display_fn=fake_display_emit,
    )
    child = _make_child_with_real_registry()

    with (
        patch("amplifier_core.AmplifierSession", return_value=child),
        patch("amplifier_foundation.generate_sub_session_id", return_value="sub-2"),
    ):
        await spawn_sub_session(
            agent_name="explorer",
            instruction="do stuff",
            parent_session=parent,
            agent_configs={"explorer": {"instruction": "explorer", "tools": []}},
        )

    inherited = child.coordinator.get_capability("display.emit")
    assert inherited is fake_display_emit, (
        "child.coordinator.get_capability('display.emit') must return the "
        "exact function the parent registered -- otherwise sub-session "
        "display events are silently dropped."
    )


@pytest.mark.asyncio
async def test_spawn_inheritance_runs_after_child_initialize() -> None:
    """Capability inheritance must happen after ``child.initialize()``.

    Hooks-approval mounts during ``initialize()``; if inheritance ran
    before, the hook would mount against an empty registry and the
    capability would be set too late to matter. This test pins the
    ordering by asserting both events happened (initialize was awaited
    and the capability ended up registered).
    """
    from amplifier_agent_lib.spawn import spawn_sub_session

    async def fake_approval_request(*args, **kwargs):  # pragma: no cover
        return {"granted": True}

    async def fake_display_emit(*args, **kwargs):  # pragma: no cover
        return None

    parent = _make_parent_with_registered_capabilities(
        approval_fn=fake_approval_request,
        display_fn=fake_display_emit,
    )
    child = _make_child_with_real_registry()

    with (
        patch("amplifier_core.AmplifierSession", return_value=child),
        patch("amplifier_foundation.generate_sub_session_id", return_value="sub-3"),
    ):
        await spawn_sub_session(
            agent_name="explorer",
            instruction="do stuff",
            parent_session=parent,
            agent_configs={"explorer": {"instruction": "explorer", "tools": []}},
        )

    child.initialize.assert_awaited_once()
    assert child.coordinator.get_capability("approval.request") is fake_approval_request

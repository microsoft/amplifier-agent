"""Child coordinators inherit the parent's workspace verbatim (D7).

The propagation lands alongside the existing approval.request / display.emit
capability inheritance in spawn_sub_session (spawn.py ~453-456). We test the
isolated propagation step rather than a full spawn so the test stays fast and
free of real module loading.
"""

from __future__ import annotations

from types import SimpleNamespace

from amplifier_agent_lib import spawn


def _coordinator(config: dict) -> SimpleNamespace:
    return SimpleNamespace(config=config)


def test_child_inherits_parent_workspace() -> None:
    parent = _coordinator({"workspace": "parent-ws", "project_slug": "parent-ws"})
    child = _coordinator({})

    spawn._propagate_workspace(parent, child)

    assert child.config["workspace"] == "parent-ws"
    assert child.config["project_slug"] == "parent-ws"


def test_child_does_not_rederive_from_cwd() -> None:
    """Even if the child's notion of cwd differs, the workspace is the parent's value."""
    parent = _coordinator({"workspace": "parent-ws", "project_slug": "parent-ws"})
    child = _coordinator({"workspace": "stale-child-derived"})

    spawn._propagate_workspace(parent, child)

    # Parent value wins; nothing is re-derived.
    assert child.config["workspace"] == "parent-ws"
    assert child.config["project_slug"] == "parent-ws"


def test_propagate_is_noop_when_parent_has_no_workspace() -> None:
    """A parent without a workspace key leaves the child untouched (defensive)."""
    parent = _coordinator({})
    child = _coordinator({"workspace": "unchanged"})

    spawn._propagate_workspace(parent, child)

    assert child.config["workspace"] == "unchanged"

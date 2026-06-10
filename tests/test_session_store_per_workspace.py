"""SessionStore writes under a per-workspace root (D8).

Regression anchor: SessionStore(root) already appends sessions/<id>; this
confirms that passing a workspace-scoped root yields the D8 layout
<root>/workspaces/<ws>/sessions/<id>/transcript.jsonl.
"""

from __future__ import annotations

from pathlib import Path

from amplifier_agent_lib.session_store import SessionStore


def test_session_store_writes_under_workspace_root(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspaces" / "test-ws"
    store = SessionStore(workspace_root)

    store.save("sid-1", [{"role": "user", "content": "hi"}], {"k": "v"})

    expected = workspace_root / "sessions" / "sid-1" / "transcript.jsonl"
    assert expected.is_file()
    assert store.session_dir("sid-1") == workspace_root / "sessions" / "sid-1"

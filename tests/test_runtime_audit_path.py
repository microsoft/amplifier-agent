"""The per-turn audit file lands under the workspace tree, not the flat tree (I8).

Design: docs/designs/2026-06-09-workspace-resolution-and-migration.md (§10, I8);
audit format: docs/designs/2026-05-24-aaa-v2-mode-a-pivot-amendment.md (SC-H).
"""

from __future__ import annotations

import json
from pathlib import Path

from amplifier_agent_cli.modes import single_turn
from amplifier_agent_lib.persistence import state_root


def _call_write_audit(workspace: str, session_id: str, turn_id: str) -> None:
    single_turn._write_audit(
        session_id=session_id,
        turn_id=turn_id,
        correlation_id="corr-xyz",
        exit_code=0,
        started_at="2026-06-09T00:00:00+00:00",
        ended_at="2026-06-09T00:00:01+00:00",
        argv=["amplifier-agent", "run", "hi"],
        protocol_version="1.0",
        workspace=workspace,
    )


def test_audit_lands_at_workspace_scoped_path(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AMPLIFIER_AGENT_HOME", str(tmp_path))
    _call_write_audit("test-ws", "sid-1", "001")

    expected = state_root() / "workspaces" / "test-ws" / "sessions" / "sid-1" / "audits" / "turn-001.json"
    assert expected.is_file(), f"expected audit at {expected}"


def test_audit_not_at_flat_path(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AMPLIFIER_AGENT_HOME", str(tmp_path))
    _call_write_audit("test-ws", "sid-1", "001")

    flat = state_root() / "sessions" / "sid-1" / "audits" / "turn-001.json"
    assert not flat.exists(), f"audit must NOT be written to the flat path {flat}"


def test_audit_correlation_id_preserved(monkeypatch, tmp_path: Path) -> None:
    """The SC-H audit schema is unchanged; only the path moves."""
    monkeypatch.setenv("AMPLIFIER_AGENT_HOME", str(tmp_path))
    _call_write_audit("test-ws", "sid-1", "001")

    audit_file = state_root() / "workspaces" / "test-ws" / "sessions" / "sid-1" / "audits" / "turn-001.json"
    payload = json.loads(audit_file.read_text(encoding="utf-8"))
    assert payload["correlationId"] == "corr-xyz"
    # Verified SC-H field set (real writer schema, not the amendment's prose).
    for field in ("argvDigest", "envDigest", "protocolVersion", "exitCode", "startedAt", "endedAt"):
        assert field in payload, f"missing SC-H field {field!r}"

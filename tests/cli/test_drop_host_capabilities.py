"""Removal verification tests for the dropped --host-capabilities surface.

These tests assert that the field is GONE. They will be removed (or kept
as guardrails — choose at PR time) once the cleanup lands.
"""

import importlib
import inspect
import json

from click.testing import CliRunner

from amplifier_agent_cli.modes.single_turn import _build_envelope, _build_error_envelope, _write_audit, run


def test_host_capabilities_flag_not_in_help() -> None:
    """`--host-capabilities` must be absent from `amplifier-agent run --help`."""
    runner = CliRunner()
    result = runner.invoke(run, ["--help"])
    assert result.exit_code == 0, result.output
    assert "--host-capabilities" not in result.output, (
        "--host-capabilities flag should be removed from `amplifier-agent run`"
    )


def test_success_envelope_metadata_excludes_host_capabilities() -> None:
    """_build_envelope must NOT include hostCapabilities in metadata."""
    result = {
        "sessionId": "sess-1",
        "turnId": "turn-1",
        "reply": "ok",
        "tokensIn": 1,
        "tokensOut": 2,
        "bundleDigest": "sha256:abc",
    }
    envelope = _build_envelope(
        result,
        correlation_id="corr-1",
        duration_ms=42,
        session_id="sess-1",
    )
    assert "hostCapabilities" not in envelope["metadata"], (
        "hostCapabilities must not appear in success envelope metadata"
    )


def test_error_envelope_metadata_excludes_host_capabilities() -> None:
    """_build_error_envelope must NOT accept host_capabilities nor emit it."""
    envelope = _build_error_envelope(
        code="internal",
        message="boom",
        correlation_id="corr-1",
        session_id="sess-1",
        turn_id="turn-1",
        duration_ms=42,
    )
    assert "hostCapabilities" not in envelope["metadata"], "hostCapabilities must not appear in error envelope metadata"


def test_audit_dict_excludes_host_capabilities(tmp_path, monkeypatch) -> None:
    """_write_audit must not accept host_capabilities and must not emit it."""
    # Patch session_state_dir to a tmp_path-based location.
    from amplifier_agent_lib import persistence

    def _fake_state_dir(session_id: str):
        return tmp_path / session_id

    monkeypatch.setattr(persistence, "session_state_dir", _fake_state_dir)

    session_id = "sess-1"
    turn_id = "turn-1"
    _write_audit(
        session_id=session_id,
        turn_id=turn_id,
        correlation_id="corr-1",
        exit_code=0,
        started_at="2026-06-01T00:00:00+00:00",
        ended_at="2026-06-01T00:00:01+00:00",
        argv=["amplifier-agent", "run", "hello"],
        mcp_config_path=None,
        env_allowlist=None,
        env_extra=None,
        protocol_version="2026-05-01",
    )

    audit_file = tmp_path / session_id / "audits" / f"turn-{turn_id}.json"
    audit = json.loads(audit_file.read_text(encoding="utf-8"))
    assert "hostCapabilities" not in audit, "hostCapabilities must not appear in per-turn audit record"


def test_runtime_session_metadata_excludes_host_capabilities() -> None:
    """amplifier_agent_lib._runtime source must NOT write host_capabilities to session.metadata."""
    runtime = importlib.import_module("amplifier_agent_lib._runtime")
    source = inspect.getsource(runtime)
    assert 'metadata["host_capabilities"]' not in source, (
        'session.metadata["host_capabilities"] assignment must be removed from _runtime.py'
    )
    assert "host_capabilities" not in source, "no reference to host_capabilities should remain in _runtime.py"

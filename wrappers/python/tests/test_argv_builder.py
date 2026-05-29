"""Tests for argv_builder.py: assemble_argv()

Mirror of wrappers/typescript/test/argv-builder.test.ts.

Protocol 0.2.0 cases:
(i)  happy path minimal session — exact argv array
(ii) resume mode replaces --fresh with --resume
(iii) --host-capabilities threaded as JSON string and parseable
(iv) --mcp-config-path threaded as bare path when caller pre-spilled
"""

from __future__ import annotations

import json

from amplifier_agent_client.argv_builder import assemble_argv


def test_happy_path_minimal_session_returns_canonical_argv() -> None:
    """(i) happy path minimal session returns canonical argv."""
    argv = assemble_argv(
        session_id="sid",
        prompt="hello",
        protocol_version="0.2.0",
    )
    assert argv == [
        "run",
        "--session-id",
        "sid",
        "--fresh",
        "--output",
        "json",
        "--protocol-version",
        "0.2.0",
        "-y",
        "hello",
    ]


def test_resume_mode_replaces_fresh_with_resume() -> None:
    """(ii) resume mode replaces --fresh with --resume."""
    argv = assemble_argv(
        session_id="sid",
        prompt="hello",
        protocol_version="0.2.0",
        resume=True,
    )
    assert "--resume" in argv
    assert "--fresh" not in argv


def test_host_capabilities_threaded_as_json_string_and_parseable() -> None:
    """(iii) --host-capabilities threaded as JSON string and parseable."""
    caps = {"fs": {"read": True}, "net": False}
    argv = assemble_argv(
        session_id="sid",
        prompt="hello",
        protocol_version="0.2.0",
        host_capabilities=caps,
    )
    idx = argv.index("--host-capabilities")
    assert idx >= 0
    json_arg = argv[idx + 1]
    assert isinstance(json_arg, str)
    assert json.loads(json_arg) == caps


def test_mcp_config_path_threaded_as_bare_path_when_caller_pre_spilled() -> None:
    """(iv) --mcp-config-path threaded as a bare filesystem path."""
    spilled = "/tmp/aaa-mcp-servers-abc.json"
    argv = assemble_argv(
        session_id="sid",
        prompt="hello",
        protocol_version="0.2.0",
        mcp_config_path=spilled,
    )
    idx = argv.index("--mcp-config-path")
    assert idx >= 0
    assert argv[idx + 1] == spilled

"""Tests for argv_builder.py: assemble_argv()

Mirror of wrappers/typescript/test/argv-builder.test.ts.

TDD cases (task-5 / A3'):
(i)  happy path minimal session — exact argv array
(ii) resume mode replaces --fresh with --resume
(iii) --host-capabilities threaded as JSON string and parseable
(iv) --mcp-servers threaded as inline JSON when no env spill
(v)  --mcp-servers @path threaded when caller pre-spilled
"""

from __future__ import annotations

import json

from amplifier_agent_client.argv_builder import assemble_argv


def test_happy_path_minimal_session_returns_canonical_argv() -> None:
    """(i) happy path minimal session returns canonical argv."""
    argv = assemble_argv(
        session_id="sid",
        prompt="hello",
        protocol_version="0.1.0",
    )
    assert argv == [
        "run",
        "--session-id",
        "sid",
        "--fresh",
        "--output",
        "json",
        "--protocol-version",
        "0.1.0",
        "-y",
        "hello",
    ]


def test_resume_mode_replaces_fresh_with_resume() -> None:
    """(ii) resume mode replaces --fresh with --resume."""
    argv = assemble_argv(
        session_id="sid",
        prompt="hello",
        protocol_version="0.1.0",
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
        protocol_version="0.1.0",
        host_capabilities=caps,
    )
    idx = argv.index("--host-capabilities")
    assert idx >= 0
    json_arg = argv[idx + 1]
    assert isinstance(json_arg, str)
    assert json.loads(json_arg) == caps


def test_mcp_servers_threaded_as_inline_json_when_no_env_spill() -> None:
    """(iv) --mcp-servers threaded as inline JSON when no env spill."""
    inline_json = '{"servers":[{"id":"a","command":"foo"}]}'
    argv = assemble_argv(
        session_id="sid",
        prompt="hello",
        protocol_version="0.1.0",
        mcp_servers_flag=inline_json,
    )
    idx = argv.index("--mcp-servers")
    assert idx >= 0
    assert argv[idx + 1] == inline_json


def test_mcp_servers_at_path_threaded_when_caller_pre_spilled() -> None:
    """(v) --mcp-servers @path threaded when caller pre-spilled."""
    spilled = "@/tmp/aaa-mcp-servers-abc.json"
    argv = assemble_argv(
        session_id="sid",
        prompt="hello",
        protocol_version="0.1.0",
        mcp_servers_flag=spilled,
    )
    idx = argv.index("--mcp-servers")
    assert idx >= 0
    assert argv[idx + 1] == spilled

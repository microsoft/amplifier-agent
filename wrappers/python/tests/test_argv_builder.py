"""Tests for argv_builder.py: assemble_argv()

Mirror of wrappers/typescript/test/argv-builder.test.ts.

Protocol 0.2.0 cases:
(i)   happy path minimal session — exact argv array
(ii)  resume mode replaces --fresh with --resume
(iii) --host-capabilities flag NOT emitted (drop-host-capabilities)
(iv)  --mcp-config-path flag NOT emitted (dropped; replaced by
      AMPLIFIER_MCP_CONFIG env var injected at submit time)
(v)   assemble_argv rejects the obsolete mcp_config_path kwarg
"""

from __future__ import annotations

import pytest

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


def test_host_capabilities_flag_not_emitted() -> None:
    """(iii) --host-capabilities flag is not emitted (drop-host-capabilities)."""
    argv = assemble_argv(
        session_id="sid",
        prompt="hello",
        protocol_version="0.2.0",
    )
    assert "--host-capabilities" not in argv


def test_mcp_config_path_flag_not_emitted() -> None:
    """(iv) --mcp-config-path flag is not emitted.

    The flag was dropped — MCP config now flows via the AMPLIFIER_MCP_CONFIG
    env var injected into the engine's subprocess environment at submit time
    (or via host_config["mcp"]["configPath"] in the host's config file).
    """
    argv = assemble_argv(
        session_id="sid",
        prompt="hello",
        protocol_version="0.2.0",
    )
    assert "--mcp-config-path" not in argv


def test_assemble_argv_rejects_obsolete_mcp_config_path_kwarg() -> None:
    """(v) assemble_argv must not accept mcp_config_path as a kwarg.

    Removal guardrail: callers that still pass the obsolete kwarg should
    fail loudly with TypeError, not silently no-op.
    """
    with pytest.raises(TypeError):
        # pyright: ignore[reportCallIssue] -- intentional: we are testing
        # that the obsolete kwarg is rejected at runtime.
        assemble_argv(
            session_id="sid",
            prompt="hello",
            protocol_version="0.2.0",
            mcp_config_path="/tmp/x.json",  # pyright: ignore[reportCallIssue]
        )

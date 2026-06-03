"""argv_builder.py — pure argv assembly for `amplifier-agent run`.

Mode A v2 (task-5 / A3'): given fully-resolved kwargs, produce the exact argv
list the wrapper will pass to the engine binary. This function performs no
I/O and reads no environment — all spilling, env resolution, and capability
composition happen upstream.

SC-C: the wrapper always passes `-y` to enforce auto-allow at the bundle
layer; approvals are handled by the orchestrating host, not the engine.
"""

from __future__ import annotations


def assemble_argv(
    *,
    session_id: str,
    prompt: str,
    protocol_version: str,
    resume: bool = False,
    cwd: str | None = None,
    provider_override: str | None = None,
) -> list[str]:
    """Build the argv list for `amplifier-agent run`.

    Pure function: no I/O, no env reads, no globals. Order is canonical and
    stable so wrapper integration tests can pin against it.

    Args:
        session_id: Session identifier (caller-supplied, never generated here).
        prompt: Final user prompt — emitted last as a positional argument.
        protocol_version: Protocol version the wrapper speaks (e.g. "0.2.0").
        resume: When True, emit `--resume` instead of `--fresh`.
        cwd: Working directory override; emits `--cwd <cwd>`.
        provider_override: Provider override; emits `--provider <provider_override>`.

    Returns:
        Canonical argv list, e.g. `["run", "--session-id", "sid", "--fresh",
        "--output", "json", "--protocol-version", "0.2.0", "-y", "<prompt>"]`.

    Removed argv flags (no longer emitted by this wrapper):
        - ``--mcp-config-path`` (engine PR #29): MCP config is now forwarded
          via the ``AMPLIFIER_MCP_CONFIG`` env var (set in the subprocess
          environment by ``SessionHandle._make_iterable`` after spilling,
          or by the host directly).
        - ``--env-allowlist``, ``--env-extra`` (engine PR #27): env
          composition is the host's responsibility. Hosts either set
          ``$AMPLIFIER_AGENT_CONFIG`` in the subprocess env or pass
          ``--config <path>`` per turn.
        - ``--allow-protocol-skew`` (engine PR #27): the unsafe override
          moved to ``host_config.allowProtocolSkew: true`` in the JSON
          config file.
    """
    argv: list[str] = []

    argv.append("run")
    argv.extend(["--session-id", session_id])
    argv.append("--resume" if resume else "--fresh")

    if cwd is not None:
        argv.extend(["--cwd", cwd])
    if provider_override is not None:
        argv.extend(["--provider", provider_override])

    argv.extend(["--output", "json"])
    argv.extend(["--protocol-version", protocol_version])

    # SC-C: wrapper enforces auto-allow at the bundle layer.
    argv.append("-y")

    # Prompt is the final positional argument.
    argv.append(prompt)

    return argv

"""argv_builder.py — pure argv assembly for `amplifier-agent run`.

Mode A v2 (task-5 / A3'): given fully-resolved kwargs, produce the exact argv
list the wrapper will pass to the engine binary. This function performs no
I/O and reads no environment — all spilling, env resolution, and capability
composition happen upstream.

SC-C: the wrapper always passes `-y` to enforce auto-allow at the bundle
layer; approvals are handled by the orchestrating host, not the engine.
"""

from __future__ import annotations

import json
from typing import Any


def assemble_argv(
    *,
    session_id: str,
    prompt: str,
    protocol_version: str,
    resume: bool = False,
    cwd: str | None = None,
    provider_override: str | None = None,
    mcp_servers_flag: str | None = None,
    host_capabilities: Any | None = None,
    env_allowlist: list[str] | None = None,
    env_extra: dict[str, str] | None = None,
    allow_protocol_skew: bool = False,
) -> list[str]:
    """Build the argv list for `amplifier-agent run`.

    Pure function: no I/O, no env reads, no globals. Order is canonical and
    stable so wrapper integration tests can pin against it.

    Args:
        session_id: Session identifier (caller-supplied, never generated here).
        prompt: Final user prompt — emitted last as a positional argument.
        protocol_version: Protocol version the wrapper speaks (e.g. "0.1.0").
        resume: When True, emit `--resume` instead of `--fresh`.
        cwd: Working directory override; emits `--cwd <cwd>`.
        provider_override: Provider override; emits `--provider <provider_override>`.
        mcp_servers_flag: Pre-resolved value for `--mcp-servers`. Caller
            decides whether this is inline JSON or `@/path/to/file.json`;
            argv-builder threads it through unchanged.
        host_capabilities: Host capabilities object — emitted as
            `--host-capabilities <JSON>`.
        env_allowlist: Allowlisted env variable names — emits
            `--env-allowlist <comma-joined>`.
        env_extra: Extra env entries — emitted as `--env-extra <JSON>`.
        allow_protocol_skew: When True, emit `--allow-protocol-skew`.

    Returns:
        Canonical argv list, e.g. `["run", "--session-id", "sid", "--fresh",
        "--output", "json", "--protocol-version", "0.1.0", "-y", "<prompt>"]`.
    """
    argv: list[str] = []

    argv.append("run")
    argv.extend(["--session-id", session_id])
    argv.append("--resume" if resume else "--fresh")

    if cwd is not None:
        argv.extend(["--cwd", cwd])
    if provider_override is not None:
        argv.extend(["--provider", provider_override])
    if mcp_servers_flag is not None:
        argv.extend(["--mcp-servers", mcp_servers_flag])
    if host_capabilities is not None:
        argv.extend(["--host-capabilities", json.dumps(host_capabilities)])
    if env_allowlist is not None and len(env_allowlist) > 0:
        argv.extend(["--env-allowlist", ",".join(env_allowlist)])
    if env_extra is not None:
        argv.extend(["--env-extra", json.dumps(env_extra)])

    argv.extend(["--output", "json"])
    argv.extend(["--protocol-version", protocol_version])

    if allow_protocol_skew:
        argv.append("--allow-protocol-skew")

    # SC-C: wrapper enforces auto-allow at the bundle layer.
    argv.append("-y")

    # Prompt is the final positional argument.
    argv.append(prompt)

    return argv

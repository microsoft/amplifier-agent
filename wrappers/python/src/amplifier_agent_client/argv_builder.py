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


def assemble_argv(
    *,
    session_id: str,
    prompt: str,
    protocol_version: str,
    resume: bool = False,
    cwd: str | None = None,
    provider_override: str | None = None,
    mcp_config_path: str | None = None,
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
        protocol_version: Protocol version the wrapper speaks (e.g. "0.2.0").
        resume: When True, emit `--resume` instead of `--fresh`.
        cwd: Working directory override; emits `--cwd <cwd>`.
        provider_override: Provider override; emits `--provider <provider_override>`.
        mcp_config_path: Path to the MCP config JSON file, pre-spilled by
            ``resolve_mcp_config_path``. Passed to the engine as
            `--mcp-config-path <path>`; the engine sets ``AMPLIFIER_MCP_CONFIG``
            so the tool-mcp module loads it during mount.
        env_allowlist: Allowlisted env variable names — emits
            `--env-allowlist <comma-joined>`.
        env_extra: Extra env entries — emitted as `--env-extra <JSON>`.
        allow_protocol_skew: When True, emit `--allow-protocol-skew`.

    Returns:
        Canonical argv list, e.g. `["run", "--session-id", "sid", "--fresh",
        "--output", "json", "--protocol-version", "0.2.0", "-y", "<prompt>"]`.
    """
    argv: list[str] = []

    argv.append("run")
    argv.extend(["--session-id", session_id])
    argv.append("--resume" if resume else "--fresh")

    if cwd is not None:
        argv.extend(["--cwd", cwd])
    if provider_override is not None:
        argv.extend(["--provider", provider_override])
    if mcp_config_path is not None:
        argv.extend(["--mcp-config-path", mcp_config_path])
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

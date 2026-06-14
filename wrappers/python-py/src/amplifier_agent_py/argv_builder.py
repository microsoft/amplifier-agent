"""Pure argv assembly for ``amplifier-agent run``.

Mirrors wrappers/typescript/src/argv-builder.ts 1:1.  Given a fully-resolved
input dict, produce the exact argv list the wrapper will pass to the engine
binary.  This function performs no I/O and reads no environment — all spilling,
env resolution, and capability composition happen upstream.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ApprovalMode = Literal["yes", "no", "prompt"]
DisplayMode = Literal["text", "ndjson"]


@dataclass(frozen=True, kw_only=True)
class AssembleArgvInput:
    """Pure inputs to :func:`assemble_argv`.

    Field semantics mirror ``AssembleArgvInput`` in
    wrappers/typescript/src/argv-builder.ts exactly.

    - ``session_id``        — caller-supplied; never generated here.
    - ``prompt``            — emitted as the final positional argument.
    - ``protocol_version``  — emitted via ``--protocol-version <version>``.
    - ``resume``            — when True, emit ``--resume``; else ``--fresh``.
    - ``cwd``               — emits ``--cwd <cwd>`` when set.
    - ``config_path``       — emits ``--config <path>`` (Issue #1).
    - ``approval_mode``     — ``"yes"`` -> ``-y``; ``"no"`` -> ``-n``;
                              ``"prompt"`` -> emit nothing; ``None`` -> historical
                              default of ``-y``.
    - ``display_mode``      — emits ``--display <mode>`` when set.
    - ``workspace``         — emits ``--workspace <slug>`` when set and non-empty.
    """

    session_id: str
    prompt: str
    protocol_version: str
    resume: bool = False
    cwd: str | None = None
    config_path: str | None = None
    approval_mode: ApprovalMode | None = None
    display_mode: DisplayMode | None = None
    workspace: str | None = None


def assemble_argv(input_: AssembleArgvInput) -> list[str]:
    """Build the argv list for ``amplifier-agent run``.

    Pure function — no I/O, no env reads, no globals.  Order is canonical and
    stable so wrapper integration tests can pin against it.

    Removed argv flags (no longer emitted by this wrapper, mirror TS):
      - ``--mcp-config-path``: MCP config flows via the ``AMPLIFIER_MCP_CONFIG``
        env var (engine PR #29).
      - ``--env-allowlist`` / ``--env-extra``: env composition is the host's
        responsibility; pass ``--config`` (engine PR #27).
      - ``--allow-protocol-skew``: moved to ``host_config.allowProtocolSkew`` in
        the JSON config file (engine PR #27).
      - ``--provider`` / ``--model`` / ``--effort``: provider config now flows
        through ``host_config.provider`` via ``--config`` (engine PR #49).
    """
    argv: list[str] = []

    argv.append("run")
    argv.extend(["--session-id", input_.session_id])
    argv.append("--resume" if input_.resume else "--fresh")

    if input_.cwd is not None:
        argv.extend(["--cwd", input_.cwd])

    # Issue #1: surface the engine's --config flag.
    if input_.config_path is not None:
        argv.extend(["--config", input_.config_path])

    argv.extend(["--output", "json"])
    argv.extend(["--protocol-version", input_.protocol_version])

    # Optional --display flag.  Only emit when explicitly set so older engines
    # (which don't accept --display) keep working with this wrapper.
    if input_.display_mode is not None:
        argv.extend(["--display", input_.display_mode])

    # Optional --workspace flag.  When set, the engine writes session state to
    # `~/.amplifier-agent/state/workspaces/<workspace>/sessions/<id>/`
    # instead of auto-deriving the slug from cwd.
    if input_.workspace is not None and len(input_.workspace) > 0:
        argv.extend(["--workspace", input_.workspace])

    # Issue #10: approval policy is caller-controlled.
    #   "yes"    -> -y (always allow)
    #   "no"     -> -n (always deny)
    #   "prompt" -> emit nothing; engine falls back to host_config.approval.mode
    #               or the bundle's TTY-based default.
    #   None     -> preserve historical default (-y) so existing callers
    #               who haven't opted into the approval API are unaffected.
    mode = input_.approval_mode
    if mode == "yes" or mode is None:
        argv.append("-y")
    elif mode == "no":
        argv.append("-n")
    # mode == "prompt": deliberately emit no flag.

    # Prompt is the final positional argument.
    argv.append(input_.prompt)

    return argv

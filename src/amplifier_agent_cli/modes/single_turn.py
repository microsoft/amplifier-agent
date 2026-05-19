"""Mode A — single-turn CLI run command.

Boots the Engine, submits one prompt, prints the JSON result to stdout,
and exits 0.  All diagnostics go to stderr only.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from typing import Any

import click

from amplifier_agent_cli.provider_detect import ProviderNotConfigured, detect_provider
from amplifier_agent_cli.tty_detect import is_stdin_tty
from amplifier_agent_lib import __version__
from amplifier_agent_lib._runtime import make_turn_handler
from amplifier_agent_lib.bundle.cache import load_and_prepare_cached
from amplifier_agent_lib.engine import Engine
from amplifier_agent_lib.protocol import PROTOCOL_VERSION, server_default_capabilities
from amplifier_agent_lib.protocol.errors import AaaError
from amplifier_agent_lib.protocol_points.defaults_cli import CliApprovalSystem, CliDisplaySystem

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _emit_error(code: str, message: str) -> None:
    """Write a JSON error envelope to stdout."""
    click.echo(json.dumps({"error": {"code": code, "message": message}}, indent=2))


def _resolve_approval_mode(yes: bool, no: bool) -> str:
    """Resolve the approval mode string from -y/-n flags.

    Returns ``'yes'``, ``'no'``, or ``'prompt'`` / ``'no'`` based on TTY.

    Raises
    ------
    click.UsageError
        If both *yes* and *no* are True simultaneously.
    """
    if yes and no:
        raise click.UsageError("-y and -n are mutually exclusive")
    if yes:
        return "yes"
    if no:
        return "no"
    return "prompt" if is_stdin_tty() else "no"


def _resolve_verbosity(quiet: bool, verbose: bool, debug: bool) -> str:
    """Resolve the display verbosity string from flag values."""
    if debug:
        return "debug"
    if verbose:
        return "verbose"
    if quiet:
        return "quiet"
    return "normal"


# ---------------------------------------------------------------------------
# _TurnSpec dataclass
# ---------------------------------------------------------------------------


@dataclass
class _TurnSpec:
    """Parameters for a single-turn execution."""

    prompt: str
    session_id: str | None
    resume: bool
    fresh: bool
    cwd: str | None
    approval: CliApprovalSystem
    display: CliDisplaySystem


# ---------------------------------------------------------------------------
# _execute_turn async function
# ---------------------------------------------------------------------------


async def _execute_turn(spec: _TurnSpec) -> dict[str, Any]:
    """Boot the Engine, submit one turn, and return the result dict."""
    prepared = await load_and_prepare_cached(aaa_version=__version__)

    if spec.fresh and spec.session_id:
        import shutil

        from amplifier_agent_lib.persistence import session_state_dir

        state_dir = session_state_dir(spec.session_id)
        if state_dir.exists():
            shutil.rmtree(state_dir, ignore_errors=True)

    handler = make_turn_handler(
        prepared,
        cwd=spec.cwd,
        is_resumed=spec.resume and not spec.fresh,
    )
    engine = Engine(
        turn_handler=handler,
        protocol_points={"approval": spec.approval, "display": spec.display},
    )

    init_params: dict[str, Any] = {
        "protocolVersion": PROTOCOL_VERSION,
        "clientInfo": {"name": "amplifier-agent-cli", "version": __version__},
        "capabilities": dict(server_default_capabilities()),
        "sessionId": spec.session_id or "",
        "resume": spec.resume,
    }
    await engine.boot(init_params, bundle_override=prepared)

    submit_params: dict[str, Any] = {
        "sessionId": spec.session_id or "",
        "turnId": "turn-1",
        "prompt": spec.prompt,
    }
    try:
        result = await engine.submit_turn(submit_params)
    finally:
        await engine.shutdown()
    return dict(result)


# ---------------------------------------------------------------------------
# 'run' command
# ---------------------------------------------------------------------------


@click.command()
@click.argument("prompt", required=False, default=None)
@click.option("--session-id", default=None, help="Session ID to resume or tag.")
@click.option("--resume", is_flag=True, default=False, help="Resume an existing session.")
@click.option("--fresh", is_flag=True, default=False, help="Force a fresh session (discard saved state).")
@click.option("--stdio", is_flag=True, default=False, help="Use stdio JSON-RPC transport (Mode B, Phase 3).")
@click.option(
    "--idle-timeout",
    "idle_timeout",
    default=None,
    type=int,
    help="Idle timeout in milliseconds before the agent exits.",
)
@click.option("--provider", "provider_override", default=None, help="Override provider detection (e.g. anthropic).")
@click.option("--bundle", default=None, hidden=True, help="Override the bundle name (hidden, for internal use).")
@click.option("--config", "config_path", default=None, type=click.Path(), help="Path to a config file.")
@click.option("--cwd", default=None, type=click.Path(), help="Working directory for the agent.")
@click.option("-v", "--verbose", is_flag=True, default=False, help="Enable verbose output.")
@click.option("--debug", is_flag=True, default=False, help="Enable debug output.")
@click.option("-y", "--yes", "yes_flag", is_flag=True, default=False, help="Auto-approve all requests.")
@click.option("-n", "--no", "no_flag", is_flag=True, default=False, help="Auto-decline all requests.")
@click.option("--quiet", is_flag=True, default=False, help="Suppress all diagnostic output.")
def run(
    prompt: str | None,
    session_id: str | None,
    resume: bool,
    fresh: bool,
    stdio: bool,
    idle_timeout: int | None,
    provider_override: str | None,
    bundle: str | None,
    config_path: str | None,
    cwd: str | None,
    verbose: bool,
    debug: bool,
    yes_flag: bool,
    no_flag: bool,
    quiet: bool,
) -> None:
    """Run the agent in single-turn mode (Mode A).

    Boots the engine, submits PROMPT, prints the JSON result to stdout,
    and exits 0.  All diagnostic output goes to stderr only.
    """
    # (1) --stdio: Mode B — run the JSON-RPC stdio loop.
    if stdio:
        import asyncio as _asyncio

        from amplifier_agent_cli.modes import stdio_loop as _stdio_loop
        from amplifier_agent_lib.protocol import PROTOCOL_VERSION as _PROTOCOL_VERSION

        _idle_timeout_s: float = (idle_timeout / 1000.0) if idle_timeout is not None else 300.0

        class _StdioEngine:
            """Phase 3 engine adapter satisfying stdio_loop._EngineProtocol.

            Protocol points are injected by stdio_loop.run() before the first
            message.  The real Engine instance is created in initialize().
            """

            def __init__(self) -> None:
                self._display: Any = None
                self._approval: Any = None
                self._inner: Any = None

            def attach_display(self, display: Any) -> None:
                self._display = display

            def attach_approval(self, approval: Any) -> None:
                self._approval = approval

            async def initialize(self, *, client_capabilities: Any, client_info: Any) -> dict[str, Any]:
                from amplifier_agent_lib._runtime import make_turn_handler
                from amplifier_agent_lib.bundle.cache import load_and_prepare_cached

                prepared = await load_and_prepare_cached(aaa_version=__version__)
                handler = make_turn_handler(prepared, cwd=None, is_resumed=False)
                engine = Engine(
                    turn_handler=handler,
                    protocol_points={"approval": self._approval, "display": self._display},
                )
                result = await engine.boot(
                    {
                        "capabilities": client_capabilities,
                        "sessionId": "",
                        "resume": False,
                    },
                    bundle_override=prepared,
                )
                self._inner = engine
                return {
                    "protocolVersion": _PROTOCOL_VERSION,
                    "serverInfo": dict(result["serverInfo"]),
                    "capabilities": dict(result["capabilities"]),
                }

            async def dispatch(self, method: str, params: Any) -> Any:
                if self._inner is None:
                    raise RuntimeError("Engine not initialized")
                return await self._inner.dispatch(method, params)

        class _SysStdinReader:
            """Reads NDJSON lines from sys.stdin.buffer in a thread executor."""

            async def readline(self) -> bytes:
                loop = _asyncio.get_running_loop()
                return await loop.run_in_executor(None, sys.stdin.buffer.readline)

        class _SysStdoutWriter:
            """Writes bytes directly to sys.stdout.buffer and flushes."""

            def write(self, data: bytes) -> None:
                sys.stdout.buffer.write(data)

            async def drain(self) -> None:
                sys.stdout.buffer.flush()

        async def _main_stdio() -> int:
            return await _stdio_loop.run(
                reader=_SysStdinReader(),
                writer=_SysStdoutWriter(),
                engine=_StdioEngine(),
                idle_timeout_s=_idle_timeout_s,
            )

        sys.exit(_asyncio.run(_main_stdio()))

    # (2) -y and -n are mutually exclusive.
    if yes_flag and no_flag:
        raise click.UsageError("-y and -n are mutually exclusive")

    # (3) Prompt discipline.
    if prompt is None:
        if is_stdin_tty():
            raise click.UsageError("Missing argument 'PROMPT'.")
        click.echo(
            "[error] prompt_required: pass prompt as argument: "
            '`amplifier-agent run "..."`. '
            "For stdio JSON-RPC, use --stdio.",
            err=True,
        )
        sys.exit(2)

    # (4) Provider detection.
    try:
        detect_provider(override=provider_override)
    except ProviderNotConfigured as exc:
        _emit_error(exc.code, exc.message)
        sys.exit(1)

    # (5) Protocol points.
    approval = CliApprovalSystem(mode=_resolve_approval_mode(yes_flag, no_flag))
    display = CliDisplaySystem(
        verbosity=_resolve_verbosity(quiet, verbose, debug),
        stream=sys.stderr,
    )

    # (6) Build spec.
    spec = _TurnSpec(prompt, session_id, resume, fresh, cwd, approval, display)

    # (7) Run with error handling.
    try:
        result = asyncio.run(_execute_turn(spec))
    except AaaError as exc:
        _emit_error(exc.code, exc.message)
        sys.exit(1)
    except Exception as exc:
        _emit_error("internal", f"{type(exc).__name__}: {exc}")
        sys.exit(1)
    click.echo(json.dumps(result, indent=2))

"""Mode A — single-turn CLI run command.

Boots the Engine, submits one prompt, prints the JSON result to stdout,
and exits 0.  All diagnostics go to stderr only.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
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


def _mint_correlation_id() -> str:
    """UUID v4, minted once per `run` invocation. SC-G."""
    return str(uuid.uuid4())


def _emit_argv_envelope(
    code: str,
    message: str,
    exit_code: int = 2,
    *,
    remediation: str | None = None,
) -> None:
    """Emit a §4.1-shape error envelope for argv-validation failures. O2'.

    *remediation*, when provided, is included as ``error.remediation`` — a
    structured hint that wrappers can surface verbatim to users.
    """
    error: dict[str, Any] = {
        "code": code,
        "classification": "protocol",
        "severity": "error",
        "correlationId": _mint_correlation_id(),
        "message": message,
    }
    if remediation is not None:
        error["remediation"] = remediation
    envelope: dict[str, Any] = {
        "protocolVersion": PROTOCOL_VERSION,
        "sessionId": "",
        "turnId": "",
        "reply": "",
        "error": error,
        "metadata": {
            "tokensIn": 0,
            "tokensOut": 0,
            "durationMs": 0,
            "bundleDigest": "",
            "engineVersion": __version__,
            "protocolVersion": PROTOCOL_VERSION,
            "correlationId": "",  # mirrored from error.correlationId below
        },
    }
    envelope["metadata"]["correlationId"] = envelope["error"]["correlationId"]
    click.echo(json.dumps(envelope))
    sys.exit(exit_code)


def _parse_json_or_atpath(value: str | None, *, flag_name: str) -> dict[str, Any] | None:
    """Parse a ``--foo '<json>'`` or ``--foo '@<path>'`` flag value.

    Returns ``None`` when *value* is ``None``.  On parse/IO/type errors emits
    a §4.1 error envelope via :func:`_emit_argv_envelope` and exits with
    code 2 (argv-validation failures are protocol-class).
    """
    if value is None:
        return None
    if value.startswith("@"):
        path = Path(value[1:]).expanduser()
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            _emit_argv_envelope(
                "argv_path_unreadable",
                f"{flag_name} @path not readable: {path}: {exc}",
            )
            return None  # unreachable; _emit_argv_envelope calls sys.exit
    else:
        raw = value
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        _emit_argv_envelope(
            "argv_json_malformed",
            f"{flag_name} JSON parse error at position {exc.pos}: {exc.msg}",
        )
        return None  # unreachable
    if not isinstance(parsed, dict):
        _emit_argv_envelope(
            "argv_json_malformed",
            f"{flag_name} must be a JSON object, got {type(parsed).__name__}",
        )
        return None  # unreachable
    return parsed


# ---------------------------------------------------------------------------
# Audit trail (SC-H / A2.1') — per-turn sha256-digested audit records
# ---------------------------------------------------------------------------


def _sha256(s: str) -> str:
    return "sha256:" + hashlib.sha256(s.encode("utf-8")).hexdigest()


def _write_audit(
    *,
    session_id: str,
    turn_id: str,
    correlation_id: str,
    exit_code: int,
    started_at: str,
    ended_at: str,
    argv: list[str],
    mcp_servers: dict[str, Any] | None,
    env_allowlist: list[str] | None,
    env_extra: dict[str, Any] | None,
    host_capabilities: dict[str, Any] | None,
    protocol_version: str,
) -> None:
    """SC-H — write per-turn audit digest. Secrets are sha256'd, never literal."""
    from amplifier_agent_lib.persistence import session_state_dir

    if not session_id:
        return  # No session id ⇒ no audit (matches anonymous CLI use).
    audits_dir = session_state_dir(session_id) / "audits"
    audits_dir.mkdir(parents=True, exist_ok=True)
    audit = {
        "argvDigest": _sha256(" ".join(argv)),
        "mcpServersDigest": (_sha256(json.dumps(mcp_servers, sort_keys=True)) if mcp_servers else None),
        "envDigest": _sha256(json.dumps({"allow": env_allowlist or [], "extra": env_extra or {}}, sort_keys=True)),
        "hostCapabilities": host_capabilities,
        "protocolVersion": protocol_version,
        "exitCode": exit_code,
        "correlationId": correlation_id,
        "startedAt": started_at,
        "endedAt": ended_at,
    }
    audit_file = audits_dir / f"turn-{turn_id}.json"
    audit_file.write_text(json.dumps(audit, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Error envelope (§4.3 / §4.4) — classification → exit code mapping
# ---------------------------------------------------------------------------

_EXIT_CODE_BY_CLASSIFICATION = {
    "engine": 1,
    "transport": 1,
    "unknown": 1,
    "protocol": 2,
    "approval": 3,
}

# Map known engine AaaError codes onto classifications. Add entries as the
# engine grows new error codes; default to 'engine'.
_CLASSIFICATION_BY_CODE = {
    "approval_translation_failed": "approval",
    "approval_timeout": "approval",
    "approval_protocol_violation": "approval",
    "protocol_version_mismatch": "protocol",
    "argv_json_malformed": "protocol",
    "argv_path_unreadable": "protocol",
}


def _classify(code: str) -> str:
    return _CLASSIFICATION_BY_CODE.get(code, "engine")


def _build_error_envelope(
    *,
    code: str,
    message: str,
    correlation_id: str,
    session_id: str,
    turn_id: str,
    host_capabilities: dict[str, Any] | None,
    duration_ms: int,
    stderr_tail: str | None = None,
) -> dict[str, Any]:
    classification = _classify(code)
    metadata: dict[str, Any] = {
        "tokensIn": 0,
        "tokensOut": 0,
        "durationMs": duration_ms,
        "bundleDigest": "",
        "engineVersion": __version__,
        "protocolVersion": PROTOCOL_VERSION,
        "correlationId": correlation_id,
    }
    if host_capabilities is not None:
        metadata["hostCapabilities"] = host_capabilities
    error: dict[str, Any] = {
        "code": code,
        "classification": classification,
        "severity": "error",
        "correlationId": correlation_id,
        "message": message,
    }
    if stderr_tail:
        error["stderrTail"] = stderr_tail
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "sessionId": session_id,
        "turnId": turn_id,
        "reply": "",
        "error": error,
        "metadata": metadata,
    }


def _build_envelope(
    result: dict[str, Any],
    *,
    correlation_id: str,
    host_capabilities: dict[str, Any] | None,
    duration_ms: int,
    session_id: str = "",
) -> dict[str, Any]:
    """Build the §4.1 success envelope from an engine turn result.

    ``session_id`` (when non-empty) overrides ``result['sessionId']`` so the
    envelope echoes the session ID supplied by the caller / CLI option.
    """
    metadata: dict[str, Any] = {
        "tokensIn": int(result.get("tokensIn", 0) or 0),
        "tokensOut": int(result.get("tokensOut", 0) or 0),
        "durationMs": duration_ms,
        "bundleDigest": result.get("bundleDigest", ""),
        "engineVersion": __version__,
        "protocolVersion": PROTOCOL_VERSION,
        "correlationId": correlation_id,
    }
    if host_capabilities is not None:
        metadata["hostCapabilities"] = host_capabilities
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "sessionId": session_id or result.get("sessionId", ""),
        "turnId": result.get("turnId", "turn-1"),
        "reply": result.get("reply", ""),
        "error": None,
        "metadata": metadata,
    }


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
    provider: str  # detected provider short-name (e.g. 'anthropic')
    allow_protocol_skew: bool = False
    mcp_servers: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# _execute_turn async function
# ---------------------------------------------------------------------------


async def _execute_turn(spec: _TurnSpec) -> dict[str, Any]:
    """Boot the Engine, submit one turn, and return the result dict."""
    prepared = await load_and_prepare_cached(aaa_version=__version__)

    # Inject the detected provider into mount_plan["providers"] post-prepare.
    # Mirrors openclaw's _inject_user_providers pattern; keeps secrets out of
    # the pickle cache (env vars resolve per-invocation, not at cache write).
    from amplifier_agent_cli.provider_sources import inject_provider

    inject_provider(prepared, spec.provider)

    if spec.fresh and spec.session_id:
        import shutil

        from amplifier_agent_lib.persistence import session_state_dir

        state_dir = session_state_dir(spec.session_id)
        if state_dir.exists():
            shutil.rmtree(state_dir, ignore_errors=True)

    # TODO(phase-A-task-7): thread spec.mcp_servers into make_turn_handler so
    # tool-mcp.mount() receives the dynamic ``servers`` config dict from the
    # CLI flag.  make_turn_handler does not yet accept an ``mcp_servers``
    # kwarg (only handle_initialize does, via wire ``params["mcpServers"]``);
    # wiring lives in Task 15's lint cleanup per the 2026-05-22 §4.8 closure.
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
    if spec.cwd:
        init_params["cwd"] = spec.cwd
    if spec.provider:
        init_params["providerOverride"] = spec.provider
    if spec.allow_protocol_skew:
        init_params["allowProtocolSkew"] = True
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
@click.option("--provider", "provider_override", default=None, help="Override provider detection (e.g. anthropic).")
@click.option("--bundle", default=None, hidden=True, help="Override the bundle name (hidden, for internal use).")
@click.option("--config", "config_path", default=None, type=click.Path(), help="Path to a config file.")
@click.option("--cwd", default=None, type=click.Path(), help="Working directory for the agent.")
@click.option("-v", "--verbose", is_flag=True, default=False, help="Enable verbose output.")
@click.option("--debug", is_flag=True, default=False, help="Enable debug output.")
@click.option("-y", "--yes", "yes_flag", is_flag=True, default=False, help="Auto-approve all requests.")
@click.option("-n", "--no", "no_flag", is_flag=True, default=False, help="Auto-decline all requests.")
@click.option("--quiet", is_flag=True, default=False, help="Suppress all diagnostic output.")
@click.option(
    "--output",
    "output_mode",
    type=click.Choice(["text", "json"], case_sensitive=False),
    default="json",
    show_default=True,
    help="Output mode: 'json' (default, envelope) or 'text' (reply only).",
)
@click.option(
    "--mcp-servers",
    "mcp_servers_raw",
    default=None,
    help="MCP servers config as inline JSON or '@<path>' to JSON file.",
)
@click.option(
    "--allow-protocol-skew",
    "allow_protocol_skew",
    is_flag=True,
    default=False,
    help="Allow protocol version mismatch between client and engine (unsafe; for testing only).",
)
@click.option(
    "--host-capabilities",
    "host_capabilities_raw",
    default=None,
    help="Host capabilities as inline JSON object.",
)
@click.option(
    "--env-allowlist",
    "env_allowlist_raw",
    default=None,
    help="Comma-separated env var names allowed into the engine subprocess.",
)
@click.option(
    "--env-extra",
    "env_extra_raw",
    default=None,
    help="Extra env vars as inline JSON object (validated against BLOCKED_ENV_KEYS).",
)
@click.option(
    "--protocol-version",
    "protocol_version_arg",
    default=None,
    help="Wrapper's pinned protocol version; engine self-validates.",
)
def run(
    prompt: str | None,
    session_id: str | None,
    resume: bool,
    fresh: bool,
    provider_override: str | None,
    bundle: str | None,
    config_path: str | None,
    cwd: str | None,
    verbose: bool,
    debug: bool,
    yes_flag: bool,
    no_flag: bool,
    quiet: bool,
    output_mode: str,
    mcp_servers_raw: str | None,
    allow_protocol_skew: bool,
    host_capabilities_raw: str | None,
    env_allowlist_raw: str | None,
    env_extra_raw: str | None,
    protocol_version_arg: str | None,
) -> None:
    """Run the agent in single-turn mode (Mode A).

    Boots the engine, submits PROMPT, prints the JSON result to stdout,
    and exits 0.  All diagnostic output goes to stderr only.
    """
    # SC-B — engine becomes session leader so MCP child processes spawned via
    # tool-mcp.mount() inherit a shared session group. The wrapper kills the
    # group on cancel so children die with the parent.
    try:
        if os.getsid(0) != os.getpid():
            os.setsid()
    except (OSError, PermissionError):
        # Best-effort — running under a debugger or test harness that already
        # owns a session may make setsid() fail; tolerate.
        pass
    if os.environ.get("AMPLIFIER_AGENT_DEBUG_SIDLOG"):
        try:
            sys.stderr.write(f"engine-sid-ok pid={os.getpid()} sid={os.getsid(0)}\n")
        except OSError:
            pass

    # (1) -y and -n are mutually exclusive.
    if yes_flag and no_flag:
        raise click.UsageError("-y and -n are mutually exclusive")

    # (3) Prompt discipline.
    if prompt is None:
        if is_stdin_tty():
            raise click.UsageError("Missing argument 'PROMPT'.")
        click.echo(
            '[error] prompt_required: pass prompt as argument: `amplifier-agent run "..."`.',
            err=True,
        )
        sys.exit(2)

    # (4) Provider detection.
    try:
        provider_name = detect_provider(override=provider_override)
    except ProviderNotConfigured as exc:
        _emit_error(exc.code, exc.message)
        sys.exit(1)

    # (5) Protocol points.
    approval = CliApprovalSystem(mode=_resolve_approval_mode(yes_flag, no_flag))
    display = CliDisplaySystem(
        verbosity=_resolve_verbosity(quiet, verbose, debug),
        stream=sys.stderr,
    )

    # (5b) Parse --mcp-servers (inline JSON or @path).  Emits a §4.1 error
    # envelope and exits 2 on parse / IO / type errors.
    mcp_servers = _parse_json_or_atpath(mcp_servers_raw, flag_name="--mcp-servers")

    # (5c) Parse host capabilities, env extras, and env allowlist (A1'/D12').
    # env_extra and env_allowlist are parsed here but threaded into the engine
    # subprocess wiring in a later task (D12' completion); the surface is
    # exposed now so wrappers can begin passing them without an interface bump.
    host_capabilities = _parse_json_or_atpath(host_capabilities_raw, flag_name="--host-capabilities")
    env_extra = _parse_json_or_atpath(env_extra_raw, flag_name="--env-extra")
    env_allowlist = [k.strip() for k in env_allowlist_raw.split(",") if k.strip()] if env_allowlist_raw else None

    # (5d) Protocol version self-validation (D6 mechanism shift).
    if protocol_version_arg and not (allow_protocol_skew or os.environ.get("AMPLIFIER_AGENT_ALLOW_PROTOCOL_SKEW")):
        if protocol_version_arg != PROTOCOL_VERSION:
            _emit_argv_envelope(
                "protocol_version_mismatch",
                f"Wrapper expects protocol {protocol_version_arg}, engine compiled with {PROTOCOL_VERSION}.",
                remediation=(
                    "To force, pass --allow-protocol-skew (unsafe) or reinstall both: "
                    "`uv tool install --reinstall amplifier-agent` and "
                    "`npm install amplifier-agent-client-ts@latest`."
                ),
            )

    # (6) Build spec.
    spec = _TurnSpec(
        prompt=prompt,
        session_id=session_id,
        resume=resume,
        fresh=fresh,
        cwd=cwd,
        approval=approval,
        display=display,
        provider=provider_name,
        allow_protocol_skew=allow_protocol_skew or bool(os.environ.get("AMPLIFIER_AGENT_ALLOW_PROTOCOL_SKEW")),
        mcp_servers=mcp_servers,
    )

    # (7) Run with error handling.
    # Capture the real stdout FD before any redirection so the final envelope
    # emission (and only that) writes to it.  CR-B / §4.0 stdout discipline:
    # any print() or stray write inside _execute_turn (e.g. a misbehaving
    # bundle module) gets diverted to stderr so it cannot corrupt the JSON
    # envelope on stdout.
    _real_stdout = sys.stdout

    correlation_id = _mint_correlation_id()
    import time

    started = time.monotonic()
    started_iso = datetime.now(UTC).isoformat()
    try:
        if output_mode == "json":
            with contextlib.redirect_stdout(sys.stderr):
                result = asyncio.run(_execute_turn(spec))
        else:
            # text mode — leave stdout intact; users want to see the reply.
            result = asyncio.run(_execute_turn(spec))
    except AaaError as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        envelope = _build_error_envelope(
            code=exc.code,
            message=exc.message,
            correlation_id=correlation_id,
            session_id=session_id or "",
            turn_id="turn-1",
            host_capabilities=host_capabilities,
            duration_ms=duration_ms,
        )
        _real_stdout.write(json.dumps(envelope) + "\n")
        _real_stdout.flush()
        exit_code = _EXIT_CODE_BY_CLASSIFICATION[envelope["error"]["classification"]]
        _write_audit(
            session_id=session_id or "",
            turn_id=envelope.get("turnId") or "turn-1",
            correlation_id=correlation_id,
            exit_code=exit_code,
            started_at=started_iso,
            ended_at=datetime.now(UTC).isoformat(),
            argv=sys.argv,
            mcp_servers=mcp_servers,
            env_allowlist=env_allowlist,
            env_extra=env_extra,
            host_capabilities=host_capabilities,
            protocol_version=PROTOCOL_VERSION,
        )
        sys.exit(exit_code)
    except Exception as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        envelope = _build_error_envelope(
            code="internal",
            message=f"{type(exc).__name__}: {exc}",
            correlation_id=correlation_id,
            session_id=session_id or "",
            turn_id="turn-1",
            host_capabilities=host_capabilities,
            duration_ms=duration_ms,
        )
        _real_stdout.write(json.dumps(envelope) + "\n")
        _real_stdout.flush()
        _write_audit(
            session_id=session_id or "",
            turn_id=envelope.get("turnId") or "turn-1",
            correlation_id=correlation_id,
            exit_code=1,
            started_at=started_iso,
            ended_at=datetime.now(UTC).isoformat(),
            argv=sys.argv,
            mcp_servers=mcp_servers,
            env_allowlist=env_allowlist,
            env_extra=env_extra,
            host_capabilities=host_capabilities,
            protocol_version=PROTOCOL_VERSION,
        )
        sys.exit(1)
    duration_ms = int((time.monotonic() - started) * 1000)
    if output_mode == "json":
        envelope = _build_envelope(
            result,
            correlation_id=correlation_id,
            host_capabilities=host_capabilities,
            duration_ms=duration_ms,
            session_id=session_id or "",
        )
        _real_stdout.write(json.dumps(envelope) + "\n")
        _real_stdout.flush()
    else:  # text
        _real_stdout.write(result.get("reply", "") + "\n")
        _real_stdout.flush()
    # SC-H — per-turn audit trail (success path).
    _write_audit(
        session_id=session_id or "",
        turn_id=result.get("turnId") or "turn-1",
        correlation_id=correlation_id,
        exit_code=0,
        started_at=started_iso,
        ended_at=datetime.now(UTC).isoformat(),
        argv=sys.argv,
        mcp_servers=mcp_servers,
        env_allowlist=env_allowlist,
        env_extra=env_extra,
        host_capabilities=host_capabilities,
        protocol_version=PROTOCOL_VERSION,
    )

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

from amplifier_agent_cli.tty_detect import is_stdin_tty
from amplifier_agent_lib import __version__
from amplifier_agent_lib._runtime import make_turn_handler
from amplifier_agent_lib.bundle import BUNDLE_MD
from amplifier_agent_lib.bundle.cache import load_and_prepare_cached
from amplifier_agent_lib.config import ConfigError, load_config
from amplifier_agent_lib.engine import Engine
from amplifier_agent_lib.persistence import WorkspaceError, resolve_workspace
from amplifier_agent_lib.protocol import PROTOCOL_VERSION, server_default_capabilities
from amplifier_agent_lib.protocol.errors import AaaError
from amplifier_agent_lib.protocol_points.defaults_cli import (
    CliApprovalSystem,
    CliDisplaySystem,
    JsonDisplaySystem,
)

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _emit_error(code: str, message: str) -> None:
    """Write a JSON error envelope to stdout."""
    click.echo(json.dumps({"error": {"code": code, "message": message}}, indent=2))


def _read_bundle_default_provider() -> str:
    """Read ``default_provider:`` from the vendored bundle.md manifest (D6).

    The bundle.md ships a top-level ``default_provider:`` field that names the
    fallback provider when neither ``--provider`` nor ``host.provider.module``
    is configured. Missing/non-string values are bundle integrity errors and
    raise ``AaaError(code='bundle_load_failed')``.
    """
    import yaml

    text = BUNDLE_MD.read_text(encoding="utf-8")
    parts = text.split("---\n")
    manifest = yaml.safe_load(parts[1]) or {}
    default = manifest.get("default_provider")
    if not isinstance(default, str):
        raise AaaError(
            code="bundle_load_failed",
            message=(
                "bundle.md missing required `default_provider:` top-level field. "
                "This is a bundle integrity error (D6); reinstall amplifier-agent."
            ),
            classification="protocol",
        )
    return default


def _resolve_approval_mode(yes: bool, no: bool, host_config: dict[str, Any] | None = None) -> str:
    """Resolve the approval mode string from argv flags, host config, and TTY (G3).

    Precedence (first present wins, matching the precedence the engine uses
    everywhere else: argv flag > host_config > bundle/TTY default):

    1. ``-y`` / ``-n`` argv flags → ``'yes'`` / ``'no'``
    2. ``host_config["approval"]["mode"]`` (validated by the loader to be one
       of ``{"yes", "no", "prompt"}``)
    3. TTY-prompt when stdin is a TTY → ``'prompt'``
    4. Non-TTY with no explicit policy → **fail-fast** via
       :func:`_emit_argv_envelope` (G3). The previous behavior — silently
       falling through to ``'no'`` and producing success-shaped no-op runs —
       was an indefensible failure mode for any headless host that ever
       forgot to pass ``-y``. Hosts that genuinely want deny-all in headless
       mode must say so explicitly via ``-n`` or
       ``host_config.approval.mode = "no"``.

    Returns
    -------
    str
        One of ``'yes'``, ``'no'``, or ``'prompt'``.

    Raises
    ------
    click.UsageError
        If both *yes* and *no* are True simultaneously.

    Notes
    -----
    On the fail-fast path this function does not return — it calls
    :func:`_emit_argv_envelope` which writes a §4.1 error envelope to stdout
    and ``sys.exit(2)``.
    """
    if yes and no:
        raise click.UsageError("-y and -n are mutually exclusive")
    if yes:
        return "yes"
    if no:
        return "no"
    # G3: consult host_config before falling through to TTY-based defaults.
    if isinstance(host_config, dict):
        approval_block = host_config.get("approval")
        if isinstance(approval_block, dict):
            mode = approval_block.get("mode")
            if isinstance(mode, str):
                # Loader has already validated this against VALID_APPROVAL_MODES.
                return mode
    if is_stdin_tty():
        return "prompt"
    # G3: non-TTY with no explicit policy — fail loudly rather than silently
    # auto-denying every tool call. The success-shaped no-op was the worst
    # failure mode in the prior design: monitoring saw green, no work happened,
    # and there was no programmatic signal to catch it. Any host running
    # amplifier-agent headlessly must opt into a policy explicitly.
    _emit_argv_envelope(
        "approval_unconfigured",
        (
            "Headless run requires an explicit approval policy. Stdin is not a "
            "TTY, neither -y/--yes nor -n/--no was passed, and host_config does "
            "not set `approval.mode`. Refusing to silently auto-deny all tool "
            "calls (the previous behavior produced success-shaped no-op runs)."
        ),
        exit_code=2,
        remediation=(
            "Pass `-y` to auto-approve, `-n` to auto-deny, or set "
            '`{"approval": {"mode": "yes"|"no"|"prompt"}}` in your --config / '
            "$AMPLIFIER_AGENT_CONFIG file."
        ),
    )
    return "no"  # unreachable; _emit_argv_envelope calls sys.exit


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
    protocol_version: str,
    workspace: str,
) -> None:
    """SC-H — write per-turn audit digest. Secrets are sha256'd, never literal.

    Note: env allow-listing was removed in E1/D10 (host config subsumes it),
    ``--env-extra`` was removed in E2/D10 (also host-config), and
    ``--mcp-config-path`` was removed (host config + AMPLIFIER_MCP_CONFIG
    env var subsume it). The envDigest field is preserved for schema
    stability and now hashes an empty ``{"extra": {}}`` placeholder; the
    former mcpConfigPathDigest field was dropped entirely because no
    argv-supplied path remains to digest.
    """
    from amplifier_agent_lib.persistence import workspaces_root

    if not session_id:
        return  # No session id ⇒ no audit (matches anonymous CLI use).
    audits_dir = workspaces_root() / workspace / "sessions" / session_id / "audits"
    audits_dir.mkdir(parents=True, exist_ok=True)
    audit = {
        "argvDigest": _sha256(" ".join(argv)),
        "envDigest": _sha256(json.dumps({"extra": {}}, sort_keys=True)),
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
    "approval_unconfigured": "protocol",  # G3: argv-validation class, not approval-runtime
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
    host_config: dict | None = None
    workspace: str | None = None


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

        from amplifier_agent_lib.persistence import workspaces_root

        # WorkspaceError is caught upstream in run() via _emit_argv_envelope;
        # by the time we reach _execute_turn, spec.workspace is already validated.
        resolved_workspace = resolve_workspace(spec.workspace, os.environ, Path(spec.cwd) if spec.cwd else Path.cwd())
        state_dir = workspaces_root() / resolved_workspace / "sessions" / spec.session_id
        if state_dir.exists():
            shutil.rmtree(state_dir, ignore_errors=True)

    handler = make_turn_handler(
        prepared,
        cwd=spec.cwd,
        is_resumed=spec.resume and not spec.fresh,
        host_config=spec.host_config,
        workspace=spec.workspace,
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
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    default=False,
    help="Enable verbose output. Only applies with --display text; ignored under --display ndjson.",
)
@click.option(
    "--debug",
    is_flag=True,
    default=False,
    help="Enable debug output. Only applies with --display text; ignored under --display ndjson.",
)
@click.option("-y", "--yes", "yes_flag", is_flag=True, default=False, help="Auto-approve all requests.")
@click.option("-n", "--no", "no_flag", is_flag=True, default=False, help="Auto-decline all requests.")
@click.option(
    "--quiet",
    is_flag=True,
    default=False,
    help="Suppress all diagnostic output. Only applies with --display text; ignored under --display ndjson.",
)
@click.option(
    "--output",
    "output_mode",
    type=click.Choice(["text", "json"], case_sensitive=False),
    default="text",
    show_default=True,
    help=(
        "Output mode: 'text' (default, reply only) or 'json' (envelope). Wrappers always pass --output json explicitly."
    ),
)
@click.option(
    "--display",
    "display_mode",
    type=click.Choice(["text", "ndjson"], case_sensitive=False),
    default="text",
    show_default=True,
    help=(
        "Stderr display mode. 'text' (default) emits human-readable "
        "summaries. 'ndjson' emits one JSON-RPC notification per line for "
        "wrapper consumption. Independent of --output, which governs stdout."
    ),
)
@click.option(
    "--protocol-version",
    "protocol_version_arg",
    default=None,
    help="Wrapper's pinned protocol version; engine self-validates.",
)
@click.option(
    "--workspace",
    default=None,
    help="Workspace name for isolating session state by project (defaults to current directory).",
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
    display_mode: str,
    protocol_version_arg: str | None,
    workspace: str | None,
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

    # (2) Verbosity flags conflict with --quiet.
    if quiet and (verbose or debug):
        raise click.UsageError("--quiet conflicts with -v/--verbose and --debug; choose one verbosity tier")

    # (2b) --resume and --fresh are mutually exclusive.
    if resume and fresh:
        raise click.UsageError("--resume and --fresh are mutually exclusive")

    # (3) Prompt discipline.
    if prompt is None:
        if is_stdin_tty():
            raise click.UsageError("Missing argument 'PROMPT'.")
        click.echo(
            '[error] prompt_required: pass prompt as argument: `amplifier-agent run "..."`.',
            err=True,
        )
        sys.exit(2)

    # (3b) Load host configuration (D1). Must run before provider resolution
    # so that host.provider.module can outrank the bundle default. ConfigError
    # subclasses AaaError with classification='protocol', so _emit_argv_envelope
    # maps it to exit code 2 via _EXIT_CODE_BY_CLASSIFICATION.
    try:
        host_config = load_config(config_arg=config_path)
    except ConfigError as exc:
        _emit_argv_envelope(exc.code, exc.message, exit_code=2)
        return  # unreachable; _emit_argv_envelope calls sys.exit

    # (4) Provider resolution (D6). Priority:
    #     --provider override > host.provider.module > bundle default_provider.
    # Env-var-based provider detection (removed in E5) is no longer called; bundle.md is the
    # source of truth for the default provider when nothing else is configured.
    if provider_override is not None:
        provider_name = provider_override
    elif isinstance(host_config, dict) and isinstance(host_config.get("provider"), dict):
        provider_name = host_config["provider"].get("module") or _read_bundle_default_provider()
    else:
        provider_name = _read_bundle_default_provider()

    # (5) Protocol points.
    # G3: pass host_config so `approval.mode` is honoured alongside -y/-n,
    # and fail-fast is wired up for non-TTY runs that lack an explicit policy.
    approval = CliApprovalSystem(mode=_resolve_approval_mode(yes_flag, no_flag, host_config))
    # Display selection.  Default `--display text` keeps the historical
    # CliDisplaySystem (`[type] summary` human-readable lines on stderr).
    # `--display ndjson` swaps in JsonDisplaySystem so hosts can consume the
    # structured wire-event stream via the amplifier-agent-ts wrapper's
    # parseNdjsonStream.  Backward compatible -- existing wrappers that don't
    # pass --display continue to receive the human-text format.
    display: CliDisplaySystem | JsonDisplaySystem
    if display_mode.lower() == "ndjson":
        display = JsonDisplaySystem(stream=sys.stderr)
    else:
        display = CliDisplaySystem(
            verbosity=_resolve_verbosity(quiet, verbose, debug),
            stream=sys.stderr,
        )

    # (5b) MCP config: the former --mcp-config-path argv flag was removed.
    # Hosts now supply the path via either (1) host_config["mcp"]["configPath"]
    # (translated to AMPLIFIER_MCP_CONFIG by _runtime.make_turn_handler) or
    # (2) AMPLIFIER_MCP_CONFIG set directly in the engine's process env
    # (tool-mcp reads it natively via its config-discovery priority chain).
    # Wrappers that previously spilled MCP server maps to a tmpfile and
    # passed --mcp-config-path now spill the same file and inject
    # AMPLIFIER_MCP_CONFIG=<path> into the subprocess env instead.

    # (5c) Env handling. The previous --env-allowlist (E1/D10) and --env-extra
    # (E2/D10) argv flags were removed: env allow-listing and extra env vars
    # are now host-config concerns, not argv-validation concerns. Wrappers
    # express both via the host config file consumed by load_config().

    # (5d) Protocol version self-validation (D6 mechanism shift; D10: skew flag
    # now sourced from host_config['allowProtocolSkew'], not from argv).
    if protocol_version_arg and not bool((host_config or {}).get("allowProtocolSkew", False)):
        if protocol_version_arg != PROTOCOL_VERSION:
            _emit_argv_envelope(
                "protocol_version_mismatch",
                f"Wrapper expects protocol {protocol_version_arg}, engine compiled with {PROTOCOL_VERSION}.",
                remediation=(
                    "To force, set `allowProtocolSkew: true` in your --config file (unsafe) "
                    "or reinstall both: `uv tool install --reinstall amplifier-agent` and "
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
        allow_protocol_skew=bool((host_config or {}).get("allowProtocolSkew", False)),
        host_config=host_config,
        workspace=workspace,
    )

    # (6b) Resolve workspace once for CLI-layer state paths (audit trail, --fresh
    # cleanup). Same (argv, env, cwd) inputs as _runtime's resolution (D2/D4),
    # so the slug is byte-identical to the handler's. Fail fast on an invalid
    # --workspace before booting (workspace I8).
    try:
        resolved_workspace = resolve_workspace(spec.workspace, os.environ, Path(spec.cwd) if spec.cwd else Path.cwd())
    except WorkspaceError as exc:
        _emit_argv_envelope("argv_workspace_invalid", str(exc), exit_code=2)
        return  # unreachable; _emit_argv_envelope calls sys.exit

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
            protocol_version=PROTOCOL_VERSION,
            workspace=resolved_workspace,
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
            protocol_version=PROTOCOL_VERSION,
            workspace=resolved_workspace,
        )
        sys.exit(1)
    duration_ms = int((time.monotonic() - started) * 1000)
    if output_mode == "json":
        envelope = _build_envelope(
            result,
            correlation_id=correlation_id,
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
        protocol_version=PROTOCOL_VERSION,
        workspace=resolved_workspace,
    )

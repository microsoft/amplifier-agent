"""Public entry-point function ``spawn_agent``.

Lives in its own module (not ``__init__.py``) so that ``sync.py`` can import
it cleanly without going through the package ``__init__``, which would create
an import cycle: ``__init__`` → ``sync`` → (via ``from . import spawn_agent``)
back to a still-initializing ``__init__``.

The package re-exports ``spawn_agent`` from ``__init__`` so external callers
see ``amplifier_agent_py.spawn_agent`` unchanged.

Mirrors ``spawnAgent`` in wrappers/typescript/src/index.ts.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from .argv_builder import ApprovalMode, DisplayMode
from .errors import AaaError
from .session import SessionHandle, SessionHandleParams
from .spawn import DEFAULT_ALLOWLIST, build_env, probe_engine_version, resolve_binary_path
from .types import DisplayEvent, McpServerConfig
from .version import VersionCheckFail, check_protocol_version

#: The protocol version this wrapper requires.  Forwarded to the engine via
#: ``--protocol-version`` on every ``submit()`` and checked at
#: ``spawn_agent()`` time against the engine's reported protocol version.
PROTOCOL_VERSION_REQUIRED_BY_WRAPPER = "0.3.0"


async def spawn_agent(
    *,
    lifecycle: str = "one-shot",
    session_id: str,
    resume: bool = False,
    cwd: str | None = None,
    env: dict[str, Any] | None = None,
    approval: dict[str, Any] | None = None,
    display: dict[str, Any] | None = None,
    display_mode: DisplayMode | None = None,
    workspace: str | None = None,
    mcp_servers: dict[str, McpServerConfig | dict[str, Any]] | None = None,
    timeout_ms: int | None = None,
    config_path: str | None = None,
    allow_protocol_skew: bool = False,
    _binary_resolver: Callable[[], str] | None = None,
    _engine_version_probe: Callable[[], Any] | None = None,
) -> SessionHandle:
    """Compose all internal components into the single public entry point.

    Mirrors ``spawnAgent`` in wrappers/typescript/src/index.ts.

    Mode A v2 flow:
      1. Lifecycle guard: must be ``"one-shot"``.
      2. Reject ``approval.on_request != None`` — v1 has no mid-turn channel.
      3. Resolve engine binary path (or inject via ``_binary_resolver``).
      4. Build subprocess environment via ``build_env``.
      5. Probe engine binary for protocol version; gate via
         ``check_protocol_version()``.  Skipped if ``allow_protocol_skew=True``.
      6. Return ``SessionHandle(params)`` — NO subprocess is spawned here.
         The engine is launched per ``submit()`` call.

    Args:
        lifecycle:           Must be ``"one-shot"``.  ``"burst"`` is reserved.
        session_id:          Caller-supplied session identifier (required).
        resume:              When True, emit ``--resume`` (else ``--fresh``).
        cwd:                 Working directory for the engine subprocess.
        env:                 ``{"allowlist": [...], "extra": {...}}``.  When
                             unset, defaults to ``DEFAULT_ALLOWLIST`` and no
                             extras.
        approval:            ``{"mode": "yes" | "no" | "prompt"}``.  Mid-turn
                             callback (``on_request``) is rejected.
        display:             ``{"on_event": callable, "subagent_events": ...}``.
                             ``on_event`` is sync, called per
                             ``NotificationEvent``.
        display_mode:        ``"text"`` or ``"ndjson"``.  ``"ndjson"`` is
                             required for structured event consumption.
        workspace:           Workspace slug for state isolation.
        mcp_servers:         MCP server map (spilled to a 0600 tmpfile).
        timeout_ms:          Per-submit wall-clock cap (None / 0 disables).
        config_path:         Path to engine host config file (``--config``).
        allow_protocol_skew: Skip the wrapper-side version probe.

    Returns:
        ``SessionHandle`` — call ``submit()`` to drive a single turn.

    Raises:
        AaaError: ``lifecycle_unsupported``, ``approval_not_supported_in_v1``,
                  ``invalid_approval_mode``, ``binary_not_found``,
                  ``env_injection_rejected``, ``engine_probe_failed``, or
                  ``protocol_version_mismatch``.
    """
    # SC-C: reject mid-turn approval callback before any other work.
    if approval is not None and approval.get("on_request") is not None:
        raise AaaError(
            "approval_not_supported_in_v1",
            "Mid-turn approval callbacks (approval['on_request']) are not supported in v1. "
            "The Mode A wire has no mid-turn request channel. Use the static-policy shape "
            "approval={'mode': 'yes' | 'no' | 'prompt'} instead — it maps to engine argv "
            "(`-y` / `-n`) and to host_config.approval.mode.",
            classification="protocol",
            severity="error",
        )

    # Validate the static-policy shape if present.
    approval_mode_arg: ApprovalMode | None = None
    if approval is not None and approval.get("mode") is not None:
        m = approval["mode"]
        if m not in ("yes", "no", "prompt"):
            raise AaaError(
                "invalid_approval_mode",
                f"approval['mode'] must be 'yes', 'no', or 'prompt' (got {m!r})",
                classification="protocol",
                severity="error",
            )
        approval_mode_arg = m

    # 1. Lifecycle guard.
    if lifecycle != "one-shot":
        raise AaaError(
            "lifecycle_unsupported",
            f"lifecycle {lifecycle!r} is not supported in v1; only 'one-shot' is supported. "
            "'burst' is reserved for a future minor version.",
            classification="protocol",
            severity="error",
        )

    # 2. Resolve binary path.
    if _binary_resolver is not None:
        binary_path = _binary_resolver()
    else:
        binary_path = resolve_binary_path()

    # 3. Build subprocess environment.
    allowlist: list[str] = list(env["allowlist"]) if env is not None and "allowlist" in env else list(DEFAULT_ALLOWLIST)
    extra: dict[str, str] = dict(env["extra"]) if env is not None and "extra" in env else {}
    subprocess_env = build_env(
        process_env=dict(os.environ),
        allowlist=allowlist,
        extra=extra,
    )

    # 4. Probe the engine binary for its protocol version BEFORE constructing a
    #    SessionHandle.  Single `amplifier-agent version --json` roundtrip.
    engine_version = ""
    engine_protocol_version = ""
    engine_bundle_digest = ""
    try:
        if _engine_version_probe is not None:
            payload = await _engine_version_probe()
            engine_version = getattr(payload, "version", "") or ""
            engine_protocol_version = getattr(payload, "protocol_version", "") or ""
            engine_bundle_digest = getattr(payload, "bundle_digest", "") or ""
        else:
            payload = await probe_engine_version(binary_path, subprocess_env)
            engine_version = payload.version
            engine_protocol_version = payload.protocol_version
            engine_bundle_digest = payload.bundle_digest or ""
    except AaaError:
        if not allow_protocol_skew:
            raise
        # Skew override: fall back to empty metadata.

    check = check_protocol_version(
        wrapper=PROTOCOL_VERSION_REQUIRED_BY_WRAPPER,
        engine=engine_protocol_version,
        allow_skew=allow_protocol_skew,
    )
    if isinstance(check, VersionCheckFail):
        raise AaaError(
            check.code,
            check.remediation,
            classification="protocol",
            severity="error",
        )

    display_on_event: Callable[[DisplayEvent], None] | None = None
    if display is not None and "on_event" in display:
        display_on_event = display["on_event"]

    # 5. Return a SessionHandle. NO subprocess spawned here — engine is
    #    launched per submit().
    return SessionHandle(
        SessionHandleParams(
            binary_path=binary_path,
            session_id=session_id,
            subprocess_env=subprocess_env,
            protocol_version=PROTOCOL_VERSION_REQUIRED_BY_WRAPPER,
            resume=resume,
            cwd=cwd,
            mcp_servers=mcp_servers,
            config_path=config_path,
            approval_mode=approval_mode_arg,
            display_mode=display_mode,
            workspace=workspace,
            timeout_ms=timeout_ms,
            display_on_event=display_on_event,
            engine_version=engine_version,
            bundle_digest=engine_bundle_digest,
        )
    )

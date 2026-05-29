"""amplifier_agent_client — Python client wrapper for the Amplifier agent protocol.

Public API (design §8.2, amended for Mode A v2 §5):
- spawn_agent(...)  → SessionHandle
- SessionHandle     — one-shot session wrapper with submit(), cancel(),
                      dispose(), get_engine_info()
- AaaError          — typed error with .code, .remediation, .classification,
                      .severity
- PROTOCOL_VERSION_REQUIRED_BY_WRAPPER

spawn_agent() is synchronous-in-spirit: it validates parameters, resolves the
engine binary path, builds the subprocess environment, and constructs a
SessionHandle. **No subprocess is spawned at spawn-time** — the engine is
launched per submit() (amendment §5.2).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any, Literal

from amplifier_agent_client.session import (
    AaaError,
    SessionHandle,
    SessionHandleParams,
)
from amplifier_agent_client.spawn import (
    DEFAULT_ALLOWLIST,
    build_env,
    resolve_binary_path,
)

#: The protocol version this Python wrapper requires.
#: Forwarded to the engine via ``--protocol-version`` on every ``submit()``.
PROTOCOL_VERSION_REQUIRED_BY_WRAPPER = "0.2.0"

__all__ = [
    "PROTOCOL_VERSION_REQUIRED_BY_WRAPPER",
    "AaaError",
    "SessionHandle",
    "SessionHandleParams",
    "spawn_agent",
]


# ---------------------------------------------------------------------------
# spawn_agent() — locked public entry point (Mode A v2)
# ---------------------------------------------------------------------------


async def spawn_agent(
    *,
    lifecycle: Literal["one-shot"],
    session_id: str,
    resume: bool = False,
    cwd: str | None = None,
    env: dict[str, Any] | None = None,
    provider_override: str | None = None,
    approval: dict[str, Any] | None = None,
    display: dict[str, Any] | None = None,
    allow_protocol_skew: bool = False,
    mcp_servers: dict[str, dict[str, Any]] | None = None,
    host: dict[str, Any] | None = None,
    timeout_ms: int | None = None,
    # Test-only injection points (undocumented in public API).
    _binary_resolver: Callable[[], str] | None = None,
) -> SessionHandle:
    """Compose all internal components into the single public entry point.

    Mode A v2 flow (amendment §5):
      1. SC-C: reject ``approval.on_request`` LOUDLY (no mid-turn channel in v1).
      2. D10: guard ``lifecycle == 'one-shot'``.
      3. Resolve engine binary path (or inject via ``_binary_resolver``).
      4. Build subprocess environment via ``build_env``.
      5. Return ``SessionHandle(params)`` — NO subprocess is spawned here.

    The engine is launched per ``submit()`` (amendment §5.2). ``agent/initialize``
    is gone; the protocol-version handshake moves to argv at submit-time.

    Args:
        lifecycle:           Must be 'one-shot' (D10). 'burst' is reserved.
        session_id:          Session identifier (caller-supplied).
        resume:              Whether to resume an existing session.
        cwd:                 Working directory for the subprocess.
        env:                 Dict with optional 'allowlist' and 'extra' keys.
        provider_override:   Provider override for the engine.
        approval:            Dict with 'on_request' callable. **NOT SUPPORTED
                             in v1.** Passing a non-None ``on_request`` raises
                             ``AaaError(approval_not_supported_in_v1)``.
        display:             Reserved; not used in Mode A v2 (engine emits a
                             single envelope, not a stream).
        allow_protocol_skew: If True, bypass strict-refuse version check.
        mcp_servers:         MCP servers dict; spilled to a 0600 tmpfile and
                             forwarded via ``--mcp-config-path <path>``.
        host:                Host capabilities envelope.
        timeout_ms:          Per-submit timeout in milliseconds (default: 10 min).
        _binary_resolver:    Test-only: replaces ``resolve_binary_path()``.

    Returns:
        ``SessionHandle`` ready for one ``submit()`` call.

    Raises:
        AaaError('approval_not_supported_in_v1'): SC-C — loud rejection.
        AaaError('lifecycle_unsupported'):        if lifecycle != 'one-shot'.
        AaaError('binary_not_found'):             if the engine binary cannot be resolved.
    """
    # SC-C: reject mid-turn approval callback BEFORE any other work. The Mode
    # A wire has no mid-turn request channel; warning-only acceptance ships
    # silent auto-allow to a host author who believed their callback was wired.
    if approval is not None and approval.get("on_request") is not None:
        raise AaaError(
            "approval_not_supported_in_v1",
            "Mid-turn approval callbacks (approval.on_request) are not supported in v1. "
            "The Mode A wire has no mid-turn request channel. The bundle's "
            "hooks-approval mount is the v1 policy point — auto-approve by default, "
            "configurable per-tool via the bundle's hooks-approval default-mode and "
            "gating settings. To customize approval policy in v1, configure the bundle; "
            "do not pass an on_request callback. Mid-turn callbacks will return in "
            "v1.x — track WG-4 in amendment §6.",
            classification="protocol",
            severity="error",
        )

    # 1. Lifecycle guard (D10).
    if lifecycle != "one-shot":
        raise AaaError(
            "lifecycle_unsupported",
            f"lifecycle '{lifecycle}' is not supported in v1; only 'one-shot' is supported. "
            "'burst' is reserved for a future minor version.",
        )

    # 2. Resolve binary path.
    if _binary_resolver is not None:
        binary_path = _binary_resolver()
    else:
        try:
            binary_path = resolve_binary_path(env=dict(os.environ))
        except RuntimeError as e:
            raise AaaError("binary_not_found", str(e)) from e

    # 3. Build subprocess environment.
    allowlist: list[str] = (env or {}).get("allowlist", DEFAULT_ALLOWLIST)
    extra: dict[str, str] = (env or {}).get("extra", {})
    subprocess_env = build_env(
        process_env=dict(os.environ),
        allowlist=allowlist,
        extra=extra,
    )

    # 4. Construct SessionHandle. NO subprocess spawned here — the engine is
    #    launched per submit() (amendment §5.2).
    host_capabilities: dict[str, Any] | None = None
    if host is not None and isinstance(host.get("capabilities"), dict):
        host_capabilities = host["capabilities"]

    from amplifier_agent_client.session import DEFAULT_TIMEOUT_MS

    params = SessionHandleParams(
        binary_path=binary_path,
        session_id=session_id,
        subprocess_env=subprocess_env,
        protocol_version=PROTOCOL_VERSION_REQUIRED_BY_WRAPPER,
        resume=resume,
        cwd=cwd,
        mcp_servers=mcp_servers,
        host_capabilities=host_capabilities,
        env_allowlist=allowlist,
        env_extra=extra,
        provider_override=provider_override,
        allow_protocol_skew=allow_protocol_skew,
        timeout_ms=timeout_ms if timeout_ms is not None else DEFAULT_TIMEOUT_MS,
    )

    return SessionHandle(params)

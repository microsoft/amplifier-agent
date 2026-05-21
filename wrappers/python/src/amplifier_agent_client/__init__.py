"""amplifier_agent_client — Python client wrapper for the Amplifier agent protocol.

Public API (design §8.2 — Python mirror):
- spawn_agent(...)  → SessionHandle
- SessionHandle     — one-shot session wrapper with submit(), cancel(), dispose(),
                      get_engine_info()
- AaaError          — typed error with .code and .remediation
- PROTOCOL_VERSION_REQUIRED_BY_WRAPPER

The full spawnAgent flow is wired here in spawn_agent():
  (1) Lifecycle guard (D10)
  (2) Binary resolution
  (3) Env build
  (4) Version probe
  (5) Protocol version check (D6)
  (6) Transport spawn
  (7) JsonRpcClient construction
  (8) agent/initialize
  (9) SessionHandle return with getEngineInfo()
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from typing import Any, Literal

from amplifier_agent_client.jsonrpc import JsonRpcClient
from amplifier_agent_client.session import AaaError, SessionHandle
from amplifier_agent_client.spawn import (
    DEFAULT_ALLOWLIST,
    build_env,
    probe_engine_version,
    resolve_binary_path,
)
from amplifier_agent_client.transport import Transport
from amplifier_agent_client.version import (
    check_protocol_version,
)

#: The protocol version that this Python wrapper requires.
#: Must match the version string shipped by `amplifier-agent` (amplifier_agent_lib).
PROTOCOL_VERSION_REQUIRED_BY_WRAPPER = "2026-05-aaa-v0"

__all__ = [
    "PROTOCOL_VERSION_REQUIRED_BY_WRAPPER",
    "AaaError",
    "SessionHandle",
    "spawn_agent",
]


# ---------------------------------------------------------------------------
# Internal real-transport adapter
# ---------------------------------------------------------------------------


class _StdioTransportAdapter:
    """Adapter that bridges Transport (frames() generator) to the JsonRpcClient
    interface (on_frame callbacks).

    Used for the real subprocess path only. Test injection uses FakeTransport
    directly, which already implements the expected interface.
    """

    def __init__(self, transport: Transport) -> None:
        self._transport = transport
        self._frame_cbs: list[Callable[[Any], None]] = []
        self._pump_task: asyncio.Task[None] | None = None
        # Keep strong references to background send tasks (RUF006).
        self._send_tasks: set[asyncio.Task[None]] = set()

    def on_frame(self, cb: Callable[[Any], None]) -> None:
        self._frame_cbs.append(cb)

    def send(self, obj: Any) -> None:
        # Fire-and-forget the async send. A failed write is not fatal at this layer.
        # Keep a strong reference so the task is not garbage-collected (RUF006).
        task = asyncio.create_task(self._transport.send(obj))
        self._send_tasks.add(task)
        task.add_done_callback(self._send_tasks.discard)

    async def start(self) -> None:
        await self._transport.start()
        self._pump_task = asyncio.create_task(self._pump())

    async def terminate(self) -> int:
        if self._pump_task is not None:
            self._pump_task.cancel()
            try:
                await self._pump_task
            except asyncio.CancelledError:
                pass
        return await self._transport.terminate()

    async def _pump(self) -> None:
        """Read frames from the transport and dispatch to registered callbacks."""
        async for frame in self._transport.frames():
            for cb in self._frame_cbs:
                cb(frame)


# ---------------------------------------------------------------------------
# spawn_agent() — locked public entry point
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
    # Test-only injection points (undocumented in public API).
    _transport_factory: Callable[[], Any] | None = None,
    _version_probe: Callable[..., dict[str, Any]] | None = None,
    _binary_resolver: Callable[[], str] | None = None,
) -> SessionHandle:
    """Compose all internal components into the single public entry point.

    Args:
        lifecycle:           Must be 'one-shot'; 'burst' raises AaaError(lifecycle_unsupported).
        session_id:          Session identifier.
        resume:              Whether to resume an existing session.
        cwd:                 Working directory for the subprocess.
        env:                 Dict with optional 'allowlist' and 'extra' keys.
        provider_override:   Optional provider override for the engine.
        approval:            Dict with 'on_request' callable and 'timeout_ms'.
        display:             Dict with optional 'on_event' callable and 'subagent_events'.
        allow_protocol_skew: If True, bypass D6 strict-refuse version check.
        _transport_factory:  Test-only: factory returning a transport-like object.
        _version_probe:      Test-only: replaces probe_engine_version().
        _binary_resolver:    Test-only: replaces resolve_binary_path().

    Returns:
        SessionHandle with get_engine_info() returning resolved engine metadata.

    Raises:
        AaaError('lifecycle_unsupported'): if lifecycle != 'one-shot'.
        AaaError('binary_not_found'):      if the engine binary cannot be resolved.
        AaaError('protocol_version_mismatch'): on version skew without allow_protocol_skew.
    """
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

    # 4. Probe engine version.
    if _version_probe is not None:
        version_payload = _version_probe(binary_path, subprocess_env)
    else:
        version_payload = probe_engine_version(binary_path, subprocess_env)

    # 5. Check protocol version (D6 strict-refuse).
    allow_skew = allow_protocol_skew or os.environ.get("AMPLIFIER_AGENT_ALLOW_PROTOCOL_SKEW") == "1"
    version_check = check_protocol_version(
        wrapper=PROTOCOL_VERSION_REQUIRED_BY_WRAPPER,
        engine=version_payload["protocolVersion"],
        allow_skew=allow_skew,
    )
    if not version_check.ok:
        raise AaaError(version_check.code or "protocol_version_mismatch", version_check.remediation)

    # 6. Spawn transport.
    if _transport_factory is not None:
        transport = _transport_factory()
    else:
        real_transport = Transport(
            command=binary_path,
            args=["run", "--stdio"],
            env=subprocess_env,
            cwd=cwd,
        )
        transport = _StdioTransportAdapter(real_transport)

    await transport.start()

    # 7. Construct JsonRpcClient with the transport.
    rpc = JsonRpcClient(transport)

    # 8. Send agent/initialize.
    capabilities: dict[str, Any] = {}
    if approval:
        capabilities["approval"] = {"actions": ["allow", "deny"]}
    if display:
        capabilities["display"] = {"events": ["*"]}

    init_result: dict[str, Any] = await rpc.call(
        "agent/initialize",
        {
            "protocolVersion": PROTOCOL_VERSION_REQUIRED_BY_WRAPPER,
            "clientInfo": {"name": "amplifier-agent-client-py", "version": "0.0.0"},
            "capabilities": capabilities,
            "sessionId": session_id,
            "resume": resume,
            "cwd": cwd,
            "providerOverride": provider_override,
        },
    )

    effective_session_id: str = init_result["sessionState"]["sessionId"]

    # 9. Return SessionHandle with engine info.
    engine_info = {
        "binary_path": binary_path,
        "protocol_version": version_payload.get("protocolVersion", ""),
        "engine_version": version_payload.get("version", ""),
        "bundle_digest": version_payload.get("bundleDigest", ""),
    }

    # Wire approval handler if provided.
    approval_on_request = (approval or {}).get("on_request")
    approval_timeout_ms = int((approval or {}).get("timeout_ms", 30_000))

    # Wire display adapter if provided.
    display_on_event = (display or {}).get("on_event")
    display_subagent_events = (display or {}).get("subagent_events", "all")

    async def _terminate() -> int:
        return await transport.terminate()

    return SessionHandle(
        rpc=rpc,
        session_id=effective_session_id,
        terminate=_terminate,
        approval_on_request=approval_on_request,
        approval_timeout_ms=approval_timeout_ms,
        display_on_event=display_on_event,
        display_subagent_events=display_subagent_events,
        engine_info=engine_info,
    )

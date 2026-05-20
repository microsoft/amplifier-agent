"""Engine — mode-agnostic core for amplifier_agent_lib.

The Engine NEVER reads stdin or writes stdout directly.  All output flows
through the DisplaySystem injected at construction via ProtocolPoints.

See docs/designs/aaa-v2-design-checkpoint.md §5 for the naming rationale
and the "Critical invariant" that this transport-free separation enables.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from amplifier_agent_lib import __version__
from amplifier_agent_lib.bundle.cache import load_and_prepare_cached
from amplifier_agent_lib.protocol import (
    PROTOCOL_VERSION,
    AgentShutdownResult,
    InitializeResult,
    TurnSubmitResult,
    negotiate_capabilities,
    server_default_capabilities,
)
from amplifier_agent_lib.protocol_points import ApprovalSystem, DisplaySystem, ProtocolPoints

if TYPE_CHECKING:
    from amplifier_foundation.bundle._prepared import PreparedBundle

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class EngineNotBootedError(RuntimeError):
    """Raised when an operation requires Engine.boot() to have been called first."""


class EngineShutdownError(RuntimeError):
    """Raised when an operation is attempted after Engine.shutdown()."""


# ---------------------------------------------------------------------------
# TurnContext and TurnHandler
# ---------------------------------------------------------------------------


@dataclass
class TurnContext:
    """Context passed to the TurnHandler for each submitted turn.

    Provides the turn-level identity fields and both protocol points so the
    handler can emit display events and issue approval requests without
    knowing about transport details.
    """

    session_id: str
    turn_id: str
    prompt: str
    approval: ApprovalSystem
    display: DisplaySystem


TurnHandler = Callable[[TurnContext], Awaitable[str]]
"""Type alias for a turn handler coroutine.

Receives a TurnContext and returns the reply string.  All model invocation
is deferred to Phase 4; Phase 1 tests use a mock injected at construction.
"""


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class Engine:
    """Mode-agnostic core engine.

    Accepts injected ProtocolPoints at construction.  Never reads stdin or
    writes stdout — all output flows through the injected DisplaySystem.

    Lifecycle
    ---------
    1. ``__init__``  — inject turn_handler + protocol_points
    2. ``boot``      — async, idempotent capability negotiation + bundle load
    3. ``dispatch``  — single entry-point the CLI calls per method
    4. ``shutdown``  — async, idempotent cleanup
    """

    SERVER_NAME = "amplifier-agent"

    def __init__(
        self,
        *,
        turn_handler: TurnHandler,
        protocol_points: ProtocolPoints,
    ) -> None:
        self._turn_handler = turn_handler
        self._protocol_points = protocol_points
        self._booted: bool = False
        self._shutdown: bool = False
        self._session_id: str | None = None
        self._init_result: InitializeResult | None = None
        #: The prepared bundle loaded (or injected) during boot().  None until boot() completes.
        self.session: PreparedBundle | None = None

    # ------------------------------------------------------------------
    # Public async API
    # ------------------------------------------------------------------

    async def boot(
        self,
        params: Any,
        bundle_override: PreparedBundle | None = None,
    ) -> InitializeResult:
        """Boot the engine, loading the prepared bundle and performing capability negotiation.

        Idempotent: a second call returns the cached InitializeResult.

        Parameters
        ----------
        params:
            An ``InitializeParams``-shaped dict.  Reads ``capabilities``,
            ``sessionId``, and ``resume``.
        bundle_override:
            If provided, use this object as the prepared bundle instead of
            loading from the XDG cache.  For tests only — production callers
            must leave this as ``None`` so the real cached bundle is used.

        Returns
        -------
        InitializeResult
            Negotiated capabilities, serverInfo, and sessionState.

        Raises
        ------
        EngineShutdownError
            If the engine has already been shut down.
        """
        self._guard_not_shutdown()
        if self._booted:
            assert self._init_result is not None
            return self._init_result

        # SC-3: Strict-refuse protocol version skew (D6).
        client_version = params.get("protocolVersion", "")
        allow_skew = bool(params.get("allowProtocolSkew", False)) or bool(
            __import__("os").environ.get("AMPLIFIER_AGENT_ALLOW_PROTOCOL_SKEW")
        )
        if client_version and client_version != PROTOCOL_VERSION and not allow_skew:
            from amplifier_agent_lib.protocol.errors import AaaError

            raise AaaError(
                code="protocol_version_mismatch",
                message=(
                    f"Protocol version mismatch: client requested {client_version!r}, "
                    f"engine speaks {PROTOCOL_VERSION!r}. Remediation: reinstall both "
                    f"wrapper and engine to compatible versions, or pass "
                    f"--allow-protocol-skew (engine CLI flag) / "
                    f"AMPLIFIER_AGENT_ALLOW_PROTOCOL_SKEW=1 (env var) to override."
                ),
            )

        # Load the prepared bundle from the XDG cache, or use the injected override.
        self.session = bundle_override or await load_and_prepare_cached(aaa_version=__version__)

        client_caps = params.get("capabilities", {})
        server_caps = server_default_capabilities()
        negotiated = negotiate_capabilities(client=client_caps, server=server_caps)

        session_id: str = params.get("sessionId") or ""
        resumed: bool = params.get("resume", False)

        self._session_id = session_id
        self._init_result = InitializeResult(
            capabilities=dict(negotiated),
            serverInfo={"name": self.SERVER_NAME, "version": __version__},
            sessionState={"sessionId": session_id, "resumed": resumed},
        )
        self._booted = True
        return self._init_result

    async def submit_turn(self, params: Any) -> TurnSubmitResult:
        """Submit a turn to the injected turn handler.

        Builds a TurnContext from the params and protocol points, awaits
        the handler, and returns the reply.  All display events MUST flow
        through ``ctx.display`` inside the handler — never via stdout.

        Parameters
        ----------
        params:
            A ``TurnSubmitParams``-shaped dict.  Must have ``sessionId``,
            ``turnId``, and ``prompt``.

        Returns
        -------
        TurnSubmitResult
            ``{'reply': <str>, 'turnId': <str>}``.

        Raises
        ------
        EngineNotBootedError
            If boot() has not yet been called.
        EngineShutdownError
            If shutdown() has already been called.
        """
        self._guard_booted()
        self._guard_not_shutdown()

        ctx = TurnContext(
            session_id=params["sessionId"],
            turn_id=params["turnId"],
            prompt=params["prompt"],
            approval=self._protocol_points["approval"],
            display=self._protocol_points["display"],
        )
        reply = await self._turn_handler(ctx)
        return TurnSubmitResult(reply=reply, turnId=params["turnId"], sessionId=params["sessionId"])

    async def shutdown(self, _params: Any = None) -> AgentShutdownResult:
        """Shut down the engine.

        Idempotent: calling twice is safe and returns ``{}`` both times.
        After shutdown, further calls to ``submit_turn`` raise
        ``EngineShutdownError``.

        Returns
        -------
        AgentShutdownResult
            Always ``{}``.
        """
        self._shutdown = True
        return AgentShutdownResult()

    async def dispatch(self, method: str, params: Any) -> Any:
        """Dispatch a protocol method call to the appropriate handler.

        Parameters
        ----------
        method:
            One of ``'agent/initialize'``, ``'turn/submit'``,
            ``'agent/shutdown'``.
        params:
            The method params dict.

        Returns
        -------
        Any
            The result of the dispatched method.

        Raises
        ------
        ValueError
            For unrecognised method names.
        """
        if method == "agent/initialize":
            return await self.boot(params)
        if method == "turn/submit":
            return await self.submit_turn(params)
        if method == "agent/shutdown":
            return await self.shutdown(params)
        raise ValueError(f"unknown method: {method!r}")

    # ------------------------------------------------------------------
    # Private guards
    # ------------------------------------------------------------------

    def _guard_booted(self) -> None:
        """Raise EngineNotBootedError if boot() has not been called."""
        if not self._booted:
            raise EngineNotBootedError("Engine.boot() must be called before this operation")

    def _guard_not_shutdown(self) -> None:
        """Raise EngineShutdownError if shutdown() has been called."""
        if self._shutdown:
            raise EngineShutdownError("Engine has been shut down")

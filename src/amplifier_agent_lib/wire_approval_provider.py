"""Wire-bridging shim that implements ``amplifier_core.ApprovalProvider``.

Per design §4.7 (A3 — CR-2), this shim forwards approval requests from the
engine to the host adapter over the wire. It exposes exactly three failure
modes via :class:`AaaError` with ``classification='approval'``:

* ``approval_translation_failed`` — request could not be serialized to wire shape.
* ``approval_timeout``            — host did not respond within ``timeout_seconds``.
* ``approval_protocol_violation`` — host response did not conform to expected shape.

Each error code maps via ``_runtime.py``'s wire-error surface to an ``AaaError``
with ``classification='approval'``. NC's event-translator catches that
classification and emits a typed ``error`` event the operator can grep for.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from amplifier_core.interfaces import ApprovalProvider, ApprovalRequest, ApprovalResponse

from amplifier_agent_lib.protocol.errors import AaaError

APPROVAL_TIMEOUT_SECONDS: float = 30.0


class WireApprovalProvider(ApprovalProvider):
    """Forward :class:`ApprovalRequest` objects over the wire to the host adapter.

    Construction is keyword-only:

    * ``approval_request_fn`` — async callable; receives the translated wire payload
      and returns the host's raw wire response (translated back via
      :meth:`_translate_response`).
    * ``timeout_seconds`` — host-response deadline; defaults to
      :data:`APPROVAL_TIMEOUT_SECONDS`.
    """

    def __init__(
        self,
        *,
        approval_request_fn: Callable[..., Awaitable[Any]],
        timeout_seconds: float = APPROVAL_TIMEOUT_SECONDS,
    ) -> None:
        self._approval_request_fn = approval_request_fn
        self._timeout_seconds = timeout_seconds

    async def request_approval(self, request: ApprovalRequest) -> ApprovalResponse:
        """Translate, send, await, translate — raising approval-classified AaaError on failure."""
        # 1. Translate request → wire payload.
        try:
            wire_payload = self._translate_request(request)
        except Exception as exc:
            raise AaaError(
                code="approval_translation_failed",
                message=f"failed to translate ApprovalRequest to wire shape: {exc}",
                classification="approval",
                severity="error",
            ) from exc

        # 2. Send over the wire, enforcing the response deadline.
        try:
            wire_response = await asyncio.wait_for(
                self._approval_request_fn(wire_payload),
                timeout=self._timeout_seconds,
            )
        except TimeoutError:
            raise AaaError(
                code="approval_timeout",
                message=(f"host did not respond to approval request within {self._timeout_seconds}s"),
                classification="approval",
                severity="error",
            ) from None

        # 3. Translate wire response → ApprovalResponse.
        try:
            return self._translate_response(wire_response)
        except Exception as exc:
            raise AaaError(
                code="approval_protocol_violation",
                message=f"approval response did not conform to expected shape: {exc}",
                classification="approval",
                severity="error",
            ) from exc

    # ------------------------------------------------------------------
    # Translation seams (overrideable by tests / future host-specific shims).
    # ------------------------------------------------------------------
    def _translate_request(self, request: ApprovalRequest) -> dict[str, Any]:
        """Default request translator — mirrors ``ApprovalRequest`` onto a JSON-safe dict."""
        return {
            "action": getattr(request, "action", None),
            "tool_name": getattr(request, "tool_name", None),
            "arguments": getattr(request, "arguments", {}),
        }

    def _translate_response(self, wire_response: Any) -> ApprovalResponse:
        """Default response translator — pass-through (no shape validation)."""
        return wire_response

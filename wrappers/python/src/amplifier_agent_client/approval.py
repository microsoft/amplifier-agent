"""Approval bridge — in-band, mid-turn JSON-RPC round-trip (§5.2).

make_approval_handler(*, on_request, timeout_ms) returns an async handler for
'approval/request' server-initiated requests. Wire it into the JsonRpcClient
via rpc.on_request('approval/request', make_approval_handler(...)).

Decision semantics:
  - 'allow'   — adapter accepted the tool call
  - 'deny'    — adapter rejected, adapter threw, or no adapter configured
  - 'timeout' — adapter did not resolve within timeout_ms

Pattern reference: Design §5.2 — six-step round-trip.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

ApprovalRequestHandler = Callable[[Any], Coroutine[Any, Any, dict[str, Any]]]


def make_approval_handler(
    *,
    on_request: ApprovalRequestHandler | None,
    timeout_ms: int,
) -> Callable[[Any], Coroutine[Any, Any, dict[str, Any]]]:
    """Create an async handler for 'approval/request' server-initiated requests.

    Args:
        on_request: Async callable that takes the request params and returns an
                    ApprovalResponse dict. None → default deny.
        timeout_ms: Maximum milliseconds to wait for on_request to resolve.

    Returns:
        An async function (params) -> dict suitable for rpc.on_request().
    """
    if on_request is None:

        async def _no_adapter(params: Any) -> dict[str, Any]:
            return {"decision": "deny", "reason": "no_adapter_configured"}

        return _no_adapter

    async def _handler(params: Any) -> dict[str, Any]:
        try:
            return await asyncio.wait_for(on_request(params), timeout=timeout_ms / 1000)
        except TimeoutError:
            return {"decision": "timeout"}
        except Exception:
            return {"decision": "deny", "reason": "adapter_error"}

    return _handler

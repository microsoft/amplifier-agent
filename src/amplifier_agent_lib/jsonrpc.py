"""NDJSON JSON-RPC 2.0 framing — pure serialization module.

One JSON object per line (\\n terminated). No transport policy, no dispatch
logic, and no knowledge of any specific method or notification name.

This is the MCP-fixed pattern: malformed or non-object lines are skipped
(with a warning) so accidental stdout pollution in a sub-tool does not crash
the protocol bridge. Skips are logged at WARNING so production callers can
surface them to operators.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class _Writer(Protocol):
    """Minimal async writer interface for JSON-RPC framing."""

    def write(self, data: bytes) -> None: ...

    async def drain(self) -> None: ...


@runtime_checkable
class _Reader(Protocol):
    """Minimal async reader interface for JSON-RPC framing."""

    async def readline(self) -> bytes: ...


async def write_message(writer: _Writer, message: dict) -> None:  # type: ignore[type-arg]
    """Serialize *message* as a single NDJSON line and write to *writer*.

    Serialization: compact JSON (no extra spaces), UTF-8 encoded, followed
    by a single newline byte.  ``writer.drain()`` is awaited to ensure the
    bytes are flushed to the underlying transport.

    JSON serialization naturally escapes embedded newlines in string fields
    as ``\\n``, so the resulting line contains no literal embedded newlines.
    """
    line = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
    writer.write(line.encode("utf-8") + b"\n")
    await writer.drain()


async def read_message(reader: _Reader) -> dict | None:  # type: ignore[type-arg]
    """Read and return the next JSON-RPC message from *reader*.

    Reads one newline-terminated line at a time.

    Returns:
        A ``dict`` representing the parsed JSON object, or ``None`` on EOF
        (when ``reader.readline()`` returns empty bytes).

    Behaviour on bad input (MCP-fixed defensive pattern):
    - Empty bytes → EOF → returns ``None``.
    - ``json.JSONDecodeError`` → line is not valid JSON → skip and continue.
    - Parsed value is not a ``dict`` (array, scalar, etc.) → skip and continue.

    This means accidental stdout pollution from sub-tools (progress messages,
    debug prints) will not crash the protocol bridge — they are silently
    skipped until a valid JSON object is found.
    """
    while True:
        raw = await reader.readline()
        if not raw:
            return None
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning(
                "jsonrpc.read_message: skipping non-JSON line (%s): %r",
                exc.__class__.__name__,
                raw[:200],
            )
            continue
        if not isinstance(obj, dict):
            logger.warning(
                "jsonrpc.read_message: skipping non-object JSON value (%s)",
                type(obj).__name__,
            )
            continue
        return obj


def classify(message: dict) -> str:  # type: ignore[type-arg]
    """Classify a JSON-RPC message by shape and return a category string.

    Returns:
        ``'request'``      — has ``method`` AND ``id``
        ``'notification'`` — has ``method`` AND no ``id``
        ``'response'``     — has ``id`` AND (``result`` OR ``error``)
        ``'invalid'``      — anything else
    """
    has_method = "method" in message
    has_id = "id" in message
    has_result = "result" in message
    has_error = "error" in message

    if has_method and has_id:
        return "request"
    if has_method and not has_id:
        return "notification"
    if has_id and (has_result or has_error):
        return "response"
    return "invalid"


def make_response(*, id: int | str, result: Any) -> dict:  # type: ignore[type-arg]
    """Return a JSON-RPC 2.0 result response frame.

    Args:
        id:     The request id being responded to.
        result: The result payload (any JSON-serialisable value).
    """
    return {"jsonrpc": "2.0", "id": id, "result": result}


def make_error(
    *,
    id: int | str | None,
    code: int,
    message: str,
    data: dict | None = None,  # type: ignore[type-arg]
) -> dict:  # type: ignore[type-arg]
    """Return a JSON-RPC 2.0 error response frame.

    Args:
        id:      The request id (``None`` when the id could not be determined).
        code:    JSON-RPC error code (e.g. ``-32600`` for Invalid Request).
        message: Human-readable error description.
        data:    Optional AaA structured error payload, e.g.
                 ``{'code': 'provider_not_configured'}``.  When ``None``,
                 the ``data`` key is **omitted** from the error object.
    """
    error_obj: dict = {"code": code, "message": message}  # type: ignore[type-arg]
    if data is not None:
        error_obj["data"] = data
    return {"jsonrpc": "2.0", "id": id, "error": error_obj}


def make_notification(*, method: str, params: dict) -> dict:  # type: ignore[type-arg]
    """Return a JSON-RPC 2.0 notification frame (no ``id`` field).

    Args:
        method: The notification method name.
        params: The notification parameters.
    """
    return {"jsonrpc": "2.0", "method": method, "params": params}

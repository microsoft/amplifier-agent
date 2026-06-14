"""NDJSON stream parser for the engine subprocess's stderr.

Mirrors ``parseNdjsonStream`` in wrappers/typescript/src/transport.ts.

The engine emits one JSON object per line on stderr when invoked with
``--display ndjson``.  Each line is either:

- A parseable JSON object (a wire-protocol notification) — delivered to
  ``on_json``.
- Plain text or partial frames — delivered verbatim to ``on_non_json``
  (without the trailing newline), or silently dropped if no handler given.

``parse_ndjson_stream()`` returns when the underlying stream emits EOF.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any


async def parse_ndjson_stream(
    stream: asyncio.StreamReader,
    *,
    on_json: Callable[[dict[str, Any]], None],
    on_non_json: Callable[[str], None] | None = None,
) -> None:
    """Consume an NDJSON stream line-by-line.

    Mirrors ``parseNdjsonStream`` in TS transport.ts.  Each successfully parsed
    JSON object is delivered to ``on_json``; non-JSON lines (or JSON-parseable
    primitives like bare numbers/strings) are delivered to ``on_non_json`` or
    silently dropped when ``on_non_json`` is None.

    Args:
        stream:      The ``asyncio.StreamReader`` for the subprocess's stderr.
        on_json:     Callback invoked with each parsed JSON object.
        on_non_json: Callback invoked with each non-JSON line (verbatim, no
                     trailing newline).  Defaults to silently dropping.

    Returns:
        Coroutine that resolves when the stream reaches EOF.
    """
    while True:
        line_bytes = await stream.readline()
        if not line_bytes:
            return
        # Strip only the trailing newline; leave other whitespace intact for
        # diagnostic accuracy.
        line = line_bytes.decode(errors="replace").rstrip("\r\n")
        trimmed = line.strip()
        if not trimmed:
            continue
        try:
            obj = json.loads(trimmed)
        except (json.JSONDecodeError, ValueError):
            if on_non_json is not None:
                on_non_json(line)
            continue
        if isinstance(obj, dict):
            on_json(obj)
        elif on_non_json is not None:
            # JSON-parseable but not an object (e.g. bare number/string).
            on_non_json(line)

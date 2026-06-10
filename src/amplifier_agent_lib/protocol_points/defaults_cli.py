"""CLI Mode A defaults — TTY approval and stderr display.

Per design §6 Mode A defaults:
- approval is prompt-when-tty, deny-otherwise with -y/-n overrides;
- display writes [type] prefixed lines to an injected stream.

CRITICAL: this module must NOT default to sys.stdout.
Callers pass sys.stderr explicitly per stdout-discipline invariant.
"""

from __future__ import annotations

import enum
import json
from collections.abc import Callable
from typing import TextIO

from amplifier_agent_lib.protocol_points.base import (
    ApprovalRequest,
    ApprovalResponse,
    DisplayEvent,
)


class DisplayVerbosity(enum.Enum):
    """Verbosity tiers for CliDisplaySystem."""

    QUIET = "quiet"
    DEFAULT = "default"
    VERBOSE = "verbose"
    DEBUG = "debug"


_SUPPRESSED_AT_DEFAULT: frozenset[str] = frozenset({"thinking/delta", "thinking/final", "progress"})

# Map string verbosity names (including 'normal' alias) to DisplayVerbosity enum.
_STR_TO_VERBOSITY: dict[str, DisplayVerbosity] = {
    "quiet": DisplayVerbosity.QUIET,
    "normal": DisplayVerbosity.DEFAULT,
    "default": DisplayVerbosity.DEFAULT,
    "verbose": DisplayVerbosity.VERBOSE,
    "debug": DisplayVerbosity.DEBUG,
}


class CliDisplaySystem:
    """Concrete DisplaySystem that writes [type] prefixed lines to an injected stream.

    The stream MUST be provided by the caller (typically sys.stderr).
    This class never touches sys.stdout directly.

    *verbosity* accepts either a :class:`DisplayVerbosity` enum value (existing
    interface) or a plain string such as ``'quiet'``, ``'verbose'``,
    ``'debug'``, ``'normal'``, or ``'default'`` (new-style CLI interface).
    The resolved string is exposed as the public ``verbosity`` attribute.
    """

    def __init__(
        self,
        *,
        stream: TextIO,
        verbosity: str | DisplayVerbosity = DisplayVerbosity.DEFAULT,
    ) -> None:
        if isinstance(verbosity, str):
            self.verbosity: str = verbosity
            self._verbosity = _STR_TO_VERBOSITY.get(verbosity, DisplayVerbosity.DEFAULT)
        else:
            self._verbosity = verbosity
            self.verbosity = verbosity.value
        self._stream = stream

    async def emit(self, event: DisplayEvent) -> None:
        """Emit a display event to the stream, gated by verbosity."""
        if self._verbosity is DisplayVerbosity.QUIET:
            return

        event_type: str = event.get("type", "")  # type: ignore[union-attr]

        if self._verbosity is DisplayVerbosity.DEFAULT and event_type in _SUPPRESSED_AT_DEFAULT:
            return

        summary = self._summarize(event)
        line = f"[{event_type}] {summary}"
        self._stream.write(line + "\n")

        if self._verbosity is DisplayVerbosity.DEBUG:
            self._stream.write(json.dumps(dict(event), sort_keys=True) + "\n")  # type: ignore[call-overload]

        self._stream.flush()

    @staticmethod
    def _summarize(event: DisplayEvent) -> str:  # type: ignore[override]
        """Return a human-readable summary string for the event."""
        event_type: str = event.get("type", "")  # type: ignore[union-attr]

        if event_type.startswith("result/") or event_type.startswith("thinking/"):
            return str(event.get("text", ""))  # type: ignore[union-attr]
        if event_type == "tool/started":
            return str(event.get("name", ""))  # type: ignore[union-attr]
        if event_type == "tool/completed":
            name = event.get("name", "")  # type: ignore[union-attr]
            duration = event.get("durationMs", "")  # type: ignore[union-attr]
            return f"{name} ({duration}ms)"
        if event_type == "progress":
            return str(event.get("message", ""))  # type: ignore[union-attr]
        if event_type == "usage":
            # The streaming hook (commit fa3b237 / #45) enriches usage events
            # with cost, model, provider, cache token counts, llm duration,
            # session cost total, and (for delegated sub-agents) agentName.
            # Surface each one only when present so terse usage events still
            # render as just "in=N out=N" for older engines / providers that
            # don't supply enrichment.
            input_tokens = event.get("inputTokens", "")  # type: ignore[union-attr]
            output_tokens = event.get("outputTokens", "")  # type: ignore[union-attr]
            parts: list[str] = [f"in={input_tokens}", f"out={output_tokens}"]
            cost = event.get("cost")  # type: ignore[union-attr]
            if cost is not None and cost != "":
                # cost is emitted as a Decimal-precision string on the wire
                # (notifications.py:123). Keep the string form so monetary
                # precision is preserved in the log; format with a leading $
                # for readability.
                parts.append(f"cost=${cost}")
            cache_read = event.get("cacheReadTokens")  # type: ignore[union-attr]
            if cache_read:
                parts.append(f"cache_read={cache_read}")
            cache_write = event.get("cacheWriteTokens")  # type: ignore[union-attr]
            if cache_write:
                parts.append(f"cache_write={cache_write}")
            llm_duration = event.get("llmDurationMs")  # type: ignore[union-attr]
            if llm_duration:
                parts.append(f"dur={llm_duration}ms")
            model = event.get("model")  # type: ignore[union-attr]
            if model:
                parts.append(f"model={model}")
            provider = event.get("provider")  # type: ignore[union-attr]
            if provider:
                parts.append(f"provider={provider}")
            session_cost_total = event.get("sessionCostTotal")  # type: ignore[union-attr]
            if session_cost_total is not None and session_cost_total != "":
                parts.append(f"session_total=${session_cost_total}")
            agent_name = event.get("agentName")  # type: ignore[union-attr]
            if agent_name:
                parts.append(f"agent={agent_name}")
            return " ".join(parts)
        if event_type == "error":
            code = event.get("code", "")  # type: ignore[union-attr]
            message = event.get("message", "")  # type: ignore[union-attr]
            return f"{code}: {message}"
        return ""


class JsonDisplaySystem:
    """DisplaySystem that emits one JSON-RPC notification per event to the stream.

    Designed for host-driven structured consumption (e.g. the amplifier-agent-ts
    wrapper's parseNdjsonStream, which reads child.stderr and dispatches each
    parsed object as a typed `notification` event to the host).

    Wire shape per line::

        {"method": "<event-type>", "params": <rest of event dict>}

    where ``<event-type>`` is the value of the source event's ``type`` field
    (e.g. ``"usage"``, ``"tool/started"``, ``"result/delta"``).  The remaining
    keys of the event dict become the ``params`` payload.  This matches the
    JSON-RPC notification shape the wrapper expects and lets the host's
    notification switch (e.g. ``case "usage":``) fire directly on the
    method name.

    Like CliDisplaySystem, the stream MUST be provided by the caller (typically
    sys.stderr).  This class never touches sys.stdout directly --- stdout is
    reserved for the §4.1 turn-completion envelope.

    Contract notes:
    - One JSON object per line (NDJSON).
    - No filtering, no verbosity dial -- the host filters on its side.  This is
      the structured contract; CliDisplaySystem is the human-facing one.
    - Fields are additive-only: hosts should ignore unknown ``params`` keys to
      stay forward-compatible.
    """

    def __init__(self, *, stream: TextIO) -> None:
        self._stream = stream

    async def emit(self, event: DisplayEvent) -> None:
        """Emit a DisplayEvent as a JSON-RPC-style notification line."""
        event_dict = dict(event)
        method = event_dict.pop("type", "unknown")
        payload = {"method": method, "params": event_dict}
        self._stream.write(json.dumps(payload) + "\n")
        self._stream.flush()


class ApprovalOverride(enum.Enum):
    """CLI -y/-n override for non-interactive approval."""

    YES = "yes"
    NO = "no"


class CliApprovalSystem:
    """Concrete ApprovalSystem for Mode A CLI usage.

    Priority:
    1. override YES -> accept
    2. override NO -> decline
    3. not is_tty -> decline
    4. prompt_fn is None -> decline
    5. prompt -> accept if 'y'/'yes', else decline

    The new-style CLI constructor accepts a *mode* string
    (``'yes'``, ``'no'``, or ``'prompt'``) as the primary interface.
    The legacy *override*, *is_tty*, and *prompt_fn* parameters remain
    supported for backward compatibility.
    """

    def __init__(
        self,
        *,
        mode: str | None = None,
        override: ApprovalOverride | None = None,
        is_tty: bool = False,
        prompt_fn: Callable[[str], str] | None = None,
    ) -> None:
        self.mode: str | None = mode
        self._override = override
        self._is_tty = is_tty
        self._prompt_fn = prompt_fn

    async def request(self, req: ApprovalRequest) -> ApprovalResponse:
        """Submit an approval request and await the response."""
        if self._override is ApprovalOverride.YES:
            return {"action": "accept"}
        if self._override is ApprovalOverride.NO:
            return {"action": "decline"}
        if not self._is_tty:
            return {"action": "decline"}
        if self._prompt_fn is None:
            return {"action": "decline"}

        kind: str = req["kind"]
        payload_summary = self._summarize_payload(req["payload"])
        prompt = f"Approve [{kind}] {payload_summary} [y/N]: "
        answer = self._prompt_fn(prompt).strip().lower()
        if answer in ("y", "yes"):
            return {"action": "accept"}
        return {"action": "decline"}

    @staticmethod
    def _summarize_payload(payload: dict) -> str:  # type: ignore[type-arg]
        """Return a short summary of the payload."""
        if "toolName" in payload:
            return str(payload["toolName"])
        return json.dumps(payload, sort_keys=True)[:80]

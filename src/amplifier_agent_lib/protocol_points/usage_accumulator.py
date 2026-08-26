"""Turn-scoped token/cost accumulation, as a transparent DisplaySystem decorator.

Every LLM call the engine makes emits a ``usage`` DisplayEvent through the display
protocol point (``bundle/hook_streaming.py``), including calls made by delegated
sub-agents (those carry an extra ``agentName`` field). That makes the display point
the one place where the whole turn's usage is observable, regardless of which
renderer the host attached or how verbose it is.

So this is a **decorator around the display protocol point, not a renderer**:

    Engine  ->  UsageAccumulator  ->  CliDisplaySystem | JsonDisplaySystem | ...

Placement is load-bearing. ``CliDisplaySystem.emit`` early-returns at QUIET
verbosity (``defaults_cli.py``), so an accumulator downstream of it would report
zero whenever the user passed ``--quiet``. Sitting upstream means usage accounting
is independent of both the display mode and the verbosity: the numbers are a
property of the turn, not of whoever happened to be watching it.

The decorator is an **observer, never a filter**. Every event is forwarded to the
wrapped system unchanged, in order, whether or not this class understood it.

Arithmetic notes (mirrors ``amplifier_agent_http/_event_translator.py``, which was
already forced to correct to exactly this):

* Input tokens arrive split across three buckets -- ``inputTokens`` (new, full
  rate), ``cacheWriteTokens`` (~1.25x) and ``cacheReadTokens`` (~0.1x). The model
  sees all three as input; the split is purely a billing distinction. Reporting
  only ``inputTokens`` made cached turns look 1000-2000x cheaper than they were.
  ``charged_input`` is therefore the sum of all three.
* Usage events are **summed**, never taken-last. The engine emits a trailing
  rollup event with ``inputTokens: 0`` / ``outputTokens: 0`` that carries only
  ``sessionCostTotal`` (``hook_streaming.on_orchestrator_complete``); a
  last-event-wins reader reports zero for the whole turn.
* ``sessionCostTotal`` is deliberately **not** added to ``cost_usd``. It is a
  session-wide total collected from the kernel's cost contributions, not a
  per-call cost, so adding it to a sum of per-call costs double-counts.
* ``cost`` crosses the wire as a decimal **string** to preserve monetary
  precision, and is parsed with ``Decimal``. Never float: summing per-call costs
  as binary floats accumulates drift a host cannot see.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from amplifier_agent_lib.protocol_points.base import DisplayEvent, DisplaySystem


def _to_int(value: Any) -> int:
    """Coerce a wire value to a non-negative-ish int, defaulting to 0.

    Deliberately total: a malformed count from a misbehaving provider module must
    not abort a turn that has already been paid for. Mirrors the ``_to_int`` in
    ``amplifier_agent_http/_event_translator.py``.
    """
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _to_decimal(value: Any) -> Decimal | None:
    """Parse a wire cost into a ``Decimal``, or ``None`` if it is not a number.

    ``str()`` first, always: ``Decimal(0.1)`` captures the binary float's error,
    ``Decimal("0.1")`` does not.
    """
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


class UsageAccumulator:
    """A DisplaySystem decorator that sums ``usage`` events as they pass through.

    Conforms to the ``DisplaySystem`` protocol, so it can stand in for one
    anywhere a display protocol point is accepted.

    Attributes
    ----------
    new_input:
        Sum of ``inputTokens`` -- input tokens billed at the full rate.
    cache_read_tokens:
        Sum of ``cacheReadTokens``.
    cache_write_tokens:
        Sum of ``cacheWriteTokens``.
    output_tokens:
        Sum of ``outputTokens``.
    cost_usd:
        Sum of the per-call ``cost`` fields as a ``Decimal``, or ``None`` when no
        event carried a cost at all. ``None`` is not zero: an honest "no provider
        reported a cost" beats a silently-wrong 0.00.
    """

    def __init__(self, inner: DisplaySystem) -> None:
        self._inner = inner
        self.new_input: int = 0
        self.cache_read_tokens: int = 0
        self.cache_write_tokens: int = 0
        self.output_tokens: int = 0
        self.cost_usd: Decimal | None = None

    # ------------------------------------------------------------------
    # DisplaySystem protocol
    # ------------------------------------------------------------------

    async def emit(self, event: DisplayEvent) -> None:
        """Observe the event, then forward it to the wrapped system unchanged.

        Observation never alters, drops, reorders or delays an event, and never
        raises: accounting is a side channel, and a bad counter must not be able
        to break the display path or fail a turn.
        """
        try:
            self._observe(event)
        except Exception:  # pragma: no cover - defensive; _observe is already total
            pass
        await self._inner.emit(event)

    # ------------------------------------------------------------------
    # Accumulation
    # ------------------------------------------------------------------

    def _observe(self, event: DisplayEvent) -> None:
        """Fold one event into the running totals. Non-usage events are ignored."""
        get = getattr(event, "get", None)
        if get is None or get("type") != "usage":
            return

        # SUM, never take-last: the trailing sessionCostTotal rollup carries
        # zeroes for both token counts and would otherwise erase the turn.
        self.new_input += _to_int(get("inputTokens"))
        self.cache_read_tokens += _to_int(get("cacheReadTokens"))
        self.cache_write_tokens += _to_int(get("cacheWriteTokens"))
        self.output_tokens += _to_int(get("outputTokens"))

        # Per-call cost only. `sessionCostTotal` on the rollup event is a
        # session-wide figure, not a per-call one -- adding it double-counts.
        cost = _to_decimal(get("cost"))
        if cost is not None:
            self.cost_usd = cost if self.cost_usd is None else self.cost_usd + cost

    # ------------------------------------------------------------------
    # Readout
    # ------------------------------------------------------------------

    @property
    def charged_input(self) -> int:
        """Total input tokens CHARGED: new + cache reads + cache writes.

        The model saw all three as input. A host that wants the new-only figure
        derives it as ``charged_input - cache_read_tokens - cache_write_tokens``.
        """
        return self.new_input + self.cache_read_tokens + self.cache_write_tokens

    def totals(self) -> dict[str, Any]:
        """Return the totals under their wire names.

        The single place the internal names are mapped onto the wire names shared
        by ``TurnSubmitResult`` and the CLI's stdout envelope metadata. ``costUsd``
        is a decimal STRING (or ``None``) so it survives JSON without losing
        monetary precision.
        """
        return {
            "tokensIn": self.charged_input,
            "tokensOut": self.output_tokens,
            "cacheReadTokens": self.cache_read_tokens,
            "cacheWriteTokens": self.cache_write_tokens,
            "costUsd": None if self.cost_usd is None else str(self.cost_usd),
        }

    def reset(self) -> None:
        """Zero every total. Called at the start of each turn to turn-scope them."""
        self.new_input = 0
        self.cache_read_tokens = 0
        self.cache_write_tokens = 0
        self.output_tokens = 0
        self.cost_usd = None

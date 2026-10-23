"""Usage: tokens and cost per phase, summed from the `Usage` entries on each turn's `TurnResult`."""

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from decimal import Decimal
import time

from amplifier_agent import Usage

from llm_wiki.console import say


@dataclass
class PhaseUsage:
    sessions: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost: Decimal = Decimal(0)
    cost_known: bool = True
    seconds: float = 0.0


@dataclass
class Tally:
    """Wall-clock time per phase, and token and cost totals per phase from each turn's final usage snapshot."""

    phases: dict[str, PhaseUsage] = field(default_factory=dict)
    started: float = field(default_factory=time.monotonic)

    @contextmanager
    def timed(self, phase: str) -> Generator[None]:
        totals = self.phases.setdefault(phase, PhaseUsage())
        begin = time.monotonic()
        try:
            yield
        finally:
            totals.seconds += time.monotonic() - begin

    def add(self, phase: str, usage: Usage | None) -> None:
        totals = self.phases.setdefault(phase, PhaseUsage())
        totals.sessions += 1
        for entry in usage.entries if usage is not None else []:
            totals.tokens_in += entry.tokens_in or 0
            totals.tokens_out += entry.tokens_out or 0
            if entry.cost is not None and "USD" in entry.cost:
                totals.cost += entry.cost["USD"]
            else:
                totals.cost_known = False

    def report(self) -> None:
        total = PhaseUsage()
        for phase, usage in self.phases.items():
            say("usage", self.line(phase, usage))
            total.sessions += usage.sessions
            total.tokens_in += usage.tokens_in
            total.tokens_out += usage.tokens_out
            total.cost += usage.cost
            total.cost_known &= usage.cost_known
        total.seconds = time.monotonic() - self.started
        if self.phases:
            say("usage", self.line("total", total))

    @staticmethod
    def line(phase: str, usage: PhaseUsage) -> str:
        cost = f"USD {usage.cost:.4f}" + ("" if usage.cost_known else " (partial)")
        return (
            f"{phase} sessions={usage.sessions} tokens_in={usage.tokens_in} tokens_out={usage.tokens_out} cost={cost} "
            f"time={usage.seconds:.0f}s"
        )

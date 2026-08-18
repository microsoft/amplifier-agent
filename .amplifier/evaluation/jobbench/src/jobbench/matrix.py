"""Build and validate the (agent, task) matrix for `run.py run`.

A sweep is `--agent` values x `--task` values (or every task in a split, via
`--all-tasks`). This module owns turning raw, repeatable/comma-separated CLI
input into a validated list of `Pair`s -- BEFORE anything is launched, so a
typo in the third agent name fails instantly instead of four hours into a
sweep. All of dataset selector validation is delegated to `dataset.resolve`,
which already fails loudly on an unknown task selector; this module adds the
equivalent validation for agent names (the registry has no such check today)
and the `all` expansion for both axes.
"""

from __future__ import annotations

from dataclasses import dataclass

from jobbench import agents, dataset
from jobbench.dataset import Task

# Observed cost_usd range across prior JobBench runs (trial.json's own
# telemetry). Not a pricing table -- purely a planning aid for --dry-run, so
# a user can tell "$8 sweep" from "$1,600 sweep" before launching anything.
OBSERVED_COST_MIN_USD = 2.04
OBSERVED_COST_MAX_USD = 10.07
OBSERVED_COST_MEAN_USD = 6.0


class MatrixError(RuntimeError):
    """Invalid --agent/--task selection. Raised before any trial launches."""


@dataclass(frozen=True)
class Pair:
    """One cell of the (agent, task) matrix."""

    agent: str
    task: Task

    @property
    def label(self) -> str:
        """`agent task-selector`, the prefix every progress line uses."""
        return f"{self.agent} {self.task.selector}"


def _split_csv(values: list[str]) -> list[str]:
    """Flatten repeated `--flag a --flag b` and comma-separated `--flag a,b`
    into one ordered, deduped list. Both habits are common enough (and cheap
    enough to support) that neither should be a surprise to the user.
    """
    seen: dict[str, None] = {}
    for raw in values:
        for part in raw.split(","):
            part = part.strip()
            if part:
                seen.setdefault(part, None)
    return list(seen)


def resolve_agent_names(raw: list[str] | None) -> list[str]:
    """Expand `--agent` values into a validated list of registered agent names.

    `all` (anywhere in the values) expands to every name currently in the
    adapter registry (`agents.names()`) -- never a hardcoded list, so a newly
    registered adapter is included automatically. Every other name is checked
    against the registry up front; an unknown name raises before the matrix
    is even built, let alone launched.
    """
    if not raw:
        raise MatrixError("--agent is required (repeatable, comma-separated, or 'all')")
    values = _split_csv(raw)
    if "all" in values:
        return agents.names()
    known = set(agents.names())
    unknown = [v for v in values if v not in known]
    if unknown:
        raise MatrixError(
            f"unknown agent(s) {unknown}; expected one of {agents.names()} (or 'all')"
        )
    return values


def resolve_tasks(*, split: str, raw: list[str] | None, all_tasks: bool) -> list[Task]:
    """Expand `--task`/`--all-tasks` into a validated list of tasks.

    `--all-tasks` wins over `--task` when both are given, matching how `all`
    wins for `--agent`. Selector validation itself is `dataset.resolve`'s
    job -- it already raises `DatasetError` naming the exact bad selector.
    """
    if all_tasks:
        return dataset.discover(split)
    if not raw:
        raise MatrixError("--task is required (repeatable, comma-separated) or use --all-tasks")
    return dataset.resolve(split, _split_csv(raw))


def build_matrix(agent_list: list[str], tasks: list[Task]) -> list[Pair]:
    """Agent-major cross product: every task for the first agent, then the
    next agent, and so on. Order only matters for how --dry-run and the
    summary read; concurrency and skip-existing behave the same regardless.
    """
    return [Pair(agent=agent, task=task) for agent in agent_list for task in tasks]


def estimate_cost(n_pairs: int) -> tuple[float, float, float]:
    """(low, mean, high) USD estimate for `n_pairs` trials.

    Derived from observed trial.json cost_usd values across prior runs --
    an ESTIMATE for sweep planning, not a quote. Actual cost varies with
    task, agent, and model.
    """
    return (
        n_pairs * OBSERVED_COST_MIN_USD,
        n_pairs * OBSERVED_COST_MEAN_USD,
        n_pairs * OBSERVED_COST_MAX_USD,
    )


__all__ = [
    "OBSERVED_COST_MAX_USD",
    "OBSERVED_COST_MEAN_USD",
    "OBSERVED_COST_MIN_USD",
    "MatrixError",
    "Pair",
    "build_matrix",
    "estimate_cost",
    "resolve_agent_names",
    "resolve_tasks",
]

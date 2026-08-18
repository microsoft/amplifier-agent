"""Unit tests for jobbench.trial._dtu_name -- DTU naming under a concurrent matrix.

The property that matters for a parallel sweep: launching several agents
against the SAME task must never produce colliding container names, since
Incus refuses to reuse a name that's still in use.
"""

from __future__ import annotations

from pathlib import Path

from jobbench.dataset import Task
from jobbench.trial import _dtu_name


def _task(occupation: str = "biostatisticians", num: int = 1) -> Task:
    return Task(split="easy", occupation=occupation, task_num=num, root=Path("/nonexistent"))


def test_dtu_name_carries_the_agent_name():
    task = _task()
    name = _dtu_name("amplifier-agent", task)
    assert name.startswith("jb-")
    assert "amplifier-agent" in name


def test_dtu_name_stays_within_incus_budget():
    task = _task()
    for agent in (
        "amplifier-agent",
        "amplifier-foundation",
        "opencode-amplifier",
        "opencode-vanilla",
    ):
        assert len(_dtu_name(agent, task)) <= 60


def test_dtu_names_differ_across_agents_on_the_same_task():
    """4 agents launched concurrently against one task must not collide."""
    task = _task()
    names = {
        _dtu_name(agent, task)
        for agent in (
            "amplifier-agent",
            "amplifier-foundation",
            "opencode-amplifier",
            "opencode-vanilla",
        )
    }
    assert len(names) == 4
    # Each name should still be identifiable as belonging to its own agent,
    # not just distinct by the random uuid suffix.
    for agent in (
        "amplifier-agent",
        "amplifier-foundation",
        "opencode-amplifier",
        "opencode-vanilla",
    ):
        matching = [n for n in names if agent in n]
        assert matching, f"no dtu name carries agent {agent!r}: {names}"


def test_dtu_names_are_unique_across_repeated_calls():
    """Repeated launches of the same (agent, task) pair (e.g. --skip-existing
    re-runs) must not collide either -- uniqueness comes from the uuid tail."""
    task = _task()
    names = {_dtu_name("amplifier-agent", task) for _ in range(20)}
    assert len(names) == 20

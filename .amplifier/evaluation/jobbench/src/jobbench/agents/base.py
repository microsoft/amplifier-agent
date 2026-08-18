"""Agent-under-test adapter contract for JobBench trials.

trial.py owns the DTU lifecycle (launch, seed, run, pull, destroy) and is
agent-agnostic. Everything specific to one agent's CLI -- how it takes a
config, how it wants the prompt handed to it, where its session state lives
-- is isolated behind this Adapter contract so a new agent (opencode,
claude-code, codex) is a new module in this package, not a change to
trial.py.

`session_dirs` and `metrics_source` are part of the contract now even though
nothing consumes them yet (that lands with telemetry/grading in a later
phase). Declaring the shape up front means adding those phases won't require
every adapter's public surface to change.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from jobbench.dtu import DTU


class AdapterError(RuntimeError):
    """Unknown agent name, or an adapter misbehaved during configure/command."""


class Adapter(ABC):
    """One agent-under-test, as trial.py needs to see it.

    A concrete adapter overrides the four properties as plain class
    attributes (this satisfies the abstract-property contract without
    needing a getter method) and implements `configure`/`command`.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Registry key, e.g. ``amplifier-agent``. Also the agent-under-test
        half of ``images.agent_alias(name)``."""

    @property
    @abstractmethod
    def image_alias(self) -> str:
        """Published Incus image alias this adapter launches from."""

    @property
    @abstractmethod
    def session_dirs(self) -> tuple[str, ...]:
        """Container paths holding this agent's session/trajectory state.
        Unused in this phase; reserved for the telemetry-pull phase."""

    @property
    @abstractmethod
    def metrics_source(self) -> str:
        """How a later phase should extract metrics: ``"events"`` for the
        Amplifier events.jsonl convention, ``"opencode_db"`` for OpenCode's
        SQLite session store, etc. Unused in this phase."""

    @abstractmethod
    async def configure(self, dtu: DTU, *, model: str) -> None:
        """Write whatever per-trial config this agent needs into a live DTU.

        Called once, after launch and before seeding. Must not write
        secrets to disk -- API keys arrive via the launch profile's
        `passthrough.services`, never through this method.
        """

    @abstractmethod
    def command(self) -> list[str]:
        """argv to invoke the agent, run from /workspace inside the DTU.

        Takes no arguments: anything the command needs beyond the CLI's own
        state (e.g. a per-trial session id) must be resolved by `configure`
        or the adapter's own `__init__` and captured on the instance.
        """


_REGISTRY: dict[str, type[Adapter]] = {}


def register(cls: type[Adapter]) -> type[Adapter]:
    """Class decorator: add an adapter to the registry under `cls().name`.

    Instantiates once at import time purely to read the `name` property --
    cheap, since adapter `__init__` does no I/O.
    """
    key = cls().name
    _REGISTRY[key] = cls
    return cls


def get(name: str, **kwargs: Any) -> Adapter:
    """Look up an adapter by name and return a fresh instance.

    Fresh per call: an adapter instance carries per-trial state (e.g. a
    generated session id), so callers must not share one instance across
    trials.

    `kwargs` are forwarded to the adapter's own `__init__` -- e.g.
    `agents.get("amplifier-foundation", bundle=...)`. Every current adapter's
    `__init__` takes no required arguments, so a caller that passes nothing
    gets the same `cls()` this function always produced; only a caller that
    deliberately overrides an agent-specific constructor kwarg needs this.
    """
    try:
        cls = _REGISTRY[name]
    except KeyError:
        raise AdapterError(f"unknown agent {name!r}; expected one of {names()}") from None
    return cls(**kwargs)


def names() -> list[str]:
    return sorted(_REGISTRY)


__all__ = ["Adapter", "AdapterError", "get", "names", "register"]

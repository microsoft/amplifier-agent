"""Agent-under-test adapters, registered by import.

Each adapter module self-registers via `@register` at import time (see
base.py). Importing this package eagerly imports every known adapter module
so `get`/`names` see the full set without callers needing to know which
modules exist.
"""

from __future__ import annotations

from jobbench.agents import (  # noqa: F401
    amplifier_agent,
    amplifier_foundation,
    opencode_amplifier,
    opencode_vanilla,
)
from jobbench.agents.base import Adapter, AdapterError, get, names

__all__ = ["Adapter", "AdapterError", "get", "names"]

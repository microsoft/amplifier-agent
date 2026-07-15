"""AI User package: a Foundation session that drives an agent inside a DTU.

Public surface:

- `AIUser`: composes foundation + anthropic-sonnet + the fixed system
  instruction, then drives an agent-under-test through a scenario via the
  bash-over-`amplifier-digital-twin exec` transport.
- `InteractionResult`: the outcome of one `AIUser.run(...)` / `run_for(...)`.
- `ConcludeTool` / `ConcludeResult`: the verdict-capturing tool the AI User
  calls when the scenario is done.
"""

from __future__ import annotations

from eval.ai_user.ai_user import (
    DEFAULT_FOUNDATION_SOURCE,
    DEFAULT_PERSONA,
    DEFAULT_PROVIDER_SOURCE,
    AIUser,
    InteractionResult,
)
from eval.ai_user.tools import ConcludeResult, ConcludeTool

__all__ = [
    "AIUser",
    "InteractionResult",
    "ConcludeResult",
    "ConcludeTool",
    "DEFAULT_FOUNDATION_SOURCE",
    "DEFAULT_PROVIDER_SOURCE",
    "DEFAULT_PERSONA",
]

"""Runtime bridge — make_turn_handler factory.

Creates a TurnHandler closed over a PreparedBundle that creates a fresh
AmplifierSession per turn (one-shot stateful via logical replay; OpenClaw pattern).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from amplifier_agent_lib.engine import TurnContext, TurnHandler

if TYPE_CHECKING:
    from amplifier_foundation.bundle._prepared import PreparedBundle


def make_turn_handler(
    prepared: PreparedBundle,
    *,
    cwd: str | None,
    is_resumed: bool,
) -> TurnHandler:
    """Return a TurnHandler closed over the loaded PreparedBundle.

    The returned coroutine creates a fresh AmplifierSession per turn
    (one-shot stateful via logical replay; OpenClaw pattern) and returns
    the model reply.

    Parameters
    ----------
    prepared:
        The loaded PreparedBundle to use for each turn.
    cwd:
        Optional working directory string.  Resolved to an absolute Path
        if provided; None otherwise.
    is_resumed:
        Whether the session should be treated as a resumed session.

    Returns
    -------
    TurnHandler
        Async callable that accepts a TurnContext and returns a reply string.
    """
    resolved_cwd: Path | None = Path(cwd).resolve() if cwd else None

    async def handler(ctx: TurnContext) -> str:
        session_id = ctx.session_id if ctx.session_id else None
        session = await prepared.create_session(
            session_id=session_id,
            session_cwd=resolved_cwd,
            is_resumed=is_resumed,
        )
        async with session:
            return await session.execute(ctx.prompt)

    return handler

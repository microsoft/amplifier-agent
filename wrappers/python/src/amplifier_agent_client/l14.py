"""L14 safety net — client-side result/final synthesis (design §4.6 contract #1).

If the engine emits a non-null reply in its turn/submit response BUT no
result/final notification was observed first, the wrapper synthesizes a
result/final-shaped DisplayEvent dict with synthesized=True.

Branch A (engine emits result/final) → saw_final=True → no synthesis.
Branch B (engine omits result/final) → saw_final=False, reply≠None → synthesize.
"""

from __future__ import annotations

from typing import Any


def synthesize_final_if_missing(
    *,
    saw_final: bool,
    reply: str | None,
    session_id: str,
    turn_id: str,
) -> dict[str, Any] | None:
    """Synthesize a result/final DisplayEvent dict if the engine omitted it.

    Returns None if:
    - saw_final is True (result/final was already observed), OR
    - reply is None (no reply to synthesize from)

    Otherwise returns a dict shaped like a result/final DisplayEvent with
    synthesized=True.
    """
    if saw_final or reply is None:
        return None
    return {
        "type": "result/final",
        "session_id": session_id,
        "turn_id": turn_id,
        "synthesized": True,
        "payload": {
            "sessionId": session_id,
            "turnId": turn_id,
            "text": reply,
        },
    }

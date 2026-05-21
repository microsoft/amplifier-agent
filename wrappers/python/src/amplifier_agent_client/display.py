"""Display adapter — onEvent push callback + sub-agent event filtering.

Pattern reference: design §4.5 sub-agent leak control.

apply_display_filter(*, subagent_events='all') returns a keep predicate
    Callable[[DisplayEvent], bool].

If subagent_events == 'all': keep everything.
If subagent_events == 'none': suppress events whose parent_turn_id is set.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from amplifier_agent_client.session import DisplayEvent

#: Allowed sub-agent filter mode values.
SubagentMode = Literal["all", "none"]


def apply_display_filter(
    *,
    subagent_events: SubagentMode = "all",
) -> Callable[[DisplayEvent], bool]:
    """Build a keep-predicate from the subagent_events mode.

    Args:
        subagent_events: 'all' (default) keeps every event;
                         'none' suppresses events with parent_turn_id set.

    Returns:
        A predicate ``(ev: DisplayEvent) -> bool`` that returns True iff
        the event should be delivered to the consumer.
    """
    if subagent_events == "all":
        return lambda _ev: True

    # subagent_events == 'none': suppress events carrying parent_turn_id.
    return lambda ev: ev.parent_turn_id is None

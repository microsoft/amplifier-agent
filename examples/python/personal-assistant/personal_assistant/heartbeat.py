"""The heartbeat: a timer that queues a check every few minutes, and a cheap check that decides, without a model call,
whether there is anything for the model to look at.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime

from personal_assistant.memory import Home, Reminder

HEARTBEAT = "/heartbeat"
# The reply that means there is nothing to tell the user. It is never shown.
QUIET = "HEARTBEAT_OK"


@dataclass(frozen=True)
class Due:
    reminders: list[Reminder]
    items: list[str]


async def tick(queue: asyncio.Queue[str], minutes: float) -> None:
    while True:
        await asyncio.sleep(minutes * 60)
        queue.put_nowait(HEARTBEAT)


def cheap_check(home: Home, now: datetime) -> Due | None:
    """The due reminders and open checklist items, or None when there are neither."""
    due = Due(home.due_reminders(now), home.open_items())
    return due if due.reminders or due.items else None


def heartbeat_prompt(due: Due, now: datetime) -> str:
    lines = [f"Heartbeat. It is {now:%A %Y-%m-%d %H:%M}."]
    if due.reminders:
        lines += ["", "Reminders that are due now:"]
        lines += [f"- {reminder.at:%Y-%m-%d %H:%M} {reminder.text}" for reminder in due.reminders]
    if due.items:
        lines += ["", "Open items in HEARTBEAT.md:"]
        lines += [f"- {item}" for item in due.items]
    lines += [
        "",
        (
            "Write a short message to the user for anything above that they should hear about now, and deliver every "
            f"due reminder. If nothing needs telling the user, reply with exactly {QUIET} and nothing else."
        ),
    ]
    return "\n".join(lines)

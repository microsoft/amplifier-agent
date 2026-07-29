"""Read `llm:*` events back out of a session's ``events.jsonl`` inside the DTU.

The hook-context-intelligence hook writes one JSON object per line, shaped
``{"event": "<name>", "data": {..., "turn_id": "..."}}``. These helpers locate the
session directory, read the file out of the container, and scope the events to the
most recent turn so a resumed or multi-turn session cannot leak an older turn's
payload into an assertion.
"""

from __future__ import annotations

import json
import shlex
from typing import Any

from framework import dtu

# Root that buckets session state by workspace (persistence.workspaces_root()).
WORKSPACES_ROOT = "/root/.amplifier-agent/state/workspaces"

# Path to the event log, relative to a session directory.
EVENTS_RELPATH = "context-intelligence/events.jsonl"


def resolve_session_dir(dtu_id: str, session_id: str, *, root: str = WORKSPACES_ROOT) -> str:
    """Return the absolute in-DTU session directory for ``session_id``.

    The workspace slug is chosen at runtime, so the directory is found rather than
    constructed. A missing or ambiguous match is a hard failure: without the record
    there is nothing to assert against, and a silent fallback would let the test
    pass for the wrong reason.
    """
    cmd = f"find {shlex.quote(root)} -maxdepth 3 -type d -name {shlex.quote(session_id)} 2>/dev/null"
    result = dtu.exec_json(dtu_id, ["bash", "-lc", cmd])
    matches = [line.strip() for line in result.get("stdout", "").splitlines() if line.strip()]

    if not matches:
        listing = dtu.exec_json(dtu_id, ["bash", "-lc", f"ls -1 {shlex.quote(root)} 2>&1"])
        raise AssertionError(
            f"no session directory named {session_id!r} under {root}.\n"
            f"The turn did not persist a session record, so there are no events to inspect.\n"
            f"workspaces present:\n{listing.get('stdout', '')}"
        )
    if len(matches) > 1:
        raise AssertionError(
            f"ambiguous session id {session_id!r}; matched {len(matches)} dirs:\n" + "\n".join(matches)
        )
    return matches[0]


def read_turn_events(dtu_id: str, session_dir: str) -> list[tuple[str, dict[str, Any]]]:
    """Return ``(event_name, data)`` pairs for the MOST RECENT turn in the session.

    Every event carries ``turn_id``; the last one seen identifies the latest turn.
    Falls back to all events when no event carries a turn id.
    """
    events_path = f"{session_dir}/{EVENTS_RELPATH}"
    result = dtu.exec_json(dtu_id, ["bash", "-lc", f"cat {shlex.quote(events_path)}"])
    if result.get("exit_code") != 0:
        raise AssertionError(
            f"could not read {events_path} (exit {result.get('exit_code')}).\nstderr:\n{result.get('stderr', '')}"
        )

    lines = [line for line in result.get("stdout", "").splitlines() if line.strip()]
    if not lines:
        raise AssertionError(f"no events at {events_path}; the turn produced no event record to inspect.")

    parsed: list[tuple[str, dict[str, Any]]] = []
    latest_turn_id: str | None = None
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        name = str(event.get("event") or "")
        data = event.get("data") or {}
        if not isinstance(data, dict):
            continue
        parsed.append((name, data))
        turn_id = data.get("turn_id")
        if isinstance(turn_id, str) and turn_id:
            latest_turn_id = turn_id

    if latest_turn_id is None:
        return parsed
    return [(n, d) for n, d in parsed if d.get("turn_id") == latest_turn_id]


def first_named(events: list[tuple[str, dict[str, Any]]], name: str) -> dict[str, Any]:
    """Return the data dict of the first event named ``name``, or fail with the names seen."""
    for event_name, data in events:
        if event_name == name:
            return data
    seen = sorted({n for n, _ in events})
    raise AssertionError(f"no {name!r} event in the latest turn. Events seen: {seen}")

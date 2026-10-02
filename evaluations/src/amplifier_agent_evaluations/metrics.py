"""Per-trial metrics from what the contract reported. An unknown value is `not_available`, never zero."""

from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
from typing import Any

NA = "not_available"
TOKEN_FIELDS = {
    "input_tokens": "tokens_in",
    "output_tokens": "tokens_out",
    "cache_read": "cache_read_tokens",
    "cache_write": "cache_write_tokens",
}
INSTALL_LINE = re.compile(r"install exit (\d+) after (\d+)s")


def load_events(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """The envelopes in arrival order, and a description of every line that is not a JSON object."""
    events: list[dict[str, Any]] = []
    bad: list[str] = []
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            bad.append(f"line {number}: {error}")
            continue
        if isinstance(value, dict):
            events.append(value)
        else:
            bad.append(f"line {number}: not an object")
    return events, bad


def turns_of(result: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not result:
        return []
    return [turn for segment in result.get("segments") or [] for turn in segment.get("turns") or []]


def _sum_field(turns: list[dict[str, Any]], field: str) -> int | str:
    """The sum over every turn's usage entries; `not_available` when any turn or entry leaves it unknown."""
    if not turns:
        return NA
    total = 0
    for turn in turns:
        entries = (turn.get("usage") or {}).get("entries")
        if not entries:
            return NA
        for entry in entries:
            value = entry.get(field)
            if not isinstance(value, int) or isinstance(value, bool):
                return NA
            total += value
    return total


def _sum_cost(turns: list[dict[str, Any]]) -> float | str:
    if not turns:
        return NA
    total = Decimal(0)
    for turn in turns:
        entries = (turn.get("usage") or {}).get("entries")
        if not entries:
            return NA
        for entry in entries:
            usd = (entry.get("cost") or {}).get("USD")
            if usd is None:
                return NA
            try:
                total += Decimal(str(usd))
            except InvalidOperation:
                return NA
    return float(total)


def _seconds(start: Any, end: Any) -> float | None:
    try:
        return (datetime.fromisoformat(str(end)) - datetime.fromisoformat(str(start))).total_seconds()
    except (TypeError, ValueError):
        return None


def _agent_wallclock(turns: list[dict[str, Any]]) -> float | str:
    if not turns:
        return NA
    total = 0.0
    for turn in turns:
        seconds = _seconds(turn.get("started_at"), turn.get("ended_at"))
        if seconds is None:
            return NA
        total += seconds
    return round(total, 3)


def install_seconds(log: Path) -> int | str:
    if not log.is_file():
        return NA
    match = INSTALL_LINE.search(log.read_text(errors="replace"))
    return int(match.group(2)) if match else NA


def compute(trial: Path, total_wallclock_s: float | None) -> dict[str, Any]:
    driver_dir = trial / "driver"
    result: dict[str, Any] | None = None
    if (driver_dir / "result.json").is_file():
        try:
            result = json.loads((driver_dir / "result.json").read_text())
        except json.JSONDecodeError:
            result = None
    turns = turns_of(result)

    metrics: dict[str, Any] = {name: _sum_field(turns, field) for name, field in TOKEN_FIELDS.items()}
    metrics["total_tokens"] = (
        metrics["input_tokens"] + metrics["output_tokens"]
        if isinstance(metrics["input_tokens"], int) and isinstance(metrics["output_tokens"], int)
        else NA
    )
    metrics["cost_usd"] = _sum_cost(turns)

    events_path = driver_dir / "events.jsonl"
    if events_path.is_file():
        events, _ = load_events(events_path)
        calls = [(event.get("payload") or {}).get("call") or {} for event in events if event.get("type") == "tool_call"]
        by_name: dict[str, int] = {}
        by_source: dict[str, int] = {}
        for call in calls:
            by_name[str(call.get("name"))] = by_name.get(str(call.get("name")), 0) + 1
            by_source[str(call.get("source"))] = by_source.get(str(call.get("source")), 0) + 1
        metrics["llm_responses"] = sum(1 for event in events if event.get("type") == "usage")
        metrics["tool_calls"] = len(calls)
        metrics["tool_calls_by_name"] = by_name
        metrics["tool_calls_by_source"] = by_source
        metrics["delegations"] = by_name.get("delegate", 0)
    else:
        for name in ("llm_responses", "tool_calls", "tool_calls_by_name", "tool_calls_by_source", "delegations"):
            metrics[name] = NA

    metrics["agent_wallclock_s"] = _agent_wallclock(turns)
    metrics["total_wallclock_s"] = round(total_wallclock_s, 3) if total_wallclock_s is not None else NA
    metrics["install_s"] = install_seconds(trial / "install.log")
    metrics["turns"] = len(turns) if result is not None else NA
    states: dict[str, int] = {}
    for turn in turns:
        states[str(turn.get("state"))] = states.get(str(turn.get("state")), 0) + 1
    metrics["turn_states"] = states if result is not None else NA
    return metrics

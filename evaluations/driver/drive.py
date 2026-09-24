"""Run one segment of a task's turn plan against the installed amplifier-agent.

See ../README.md (Task, Driver) for the task format and the files this writes.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
import importlib
import json
import os
from pathlib import Path
import platform
import sys
import traceback
from typing import Any

import amplifier_agent
from amplifier_agent import (
    BUILTIN_TOOLS,
    AgentOptions,
    ConversationMessage,
    SessionOptions,
    TextPart,
    TurnInput,
    create_agent,
)

DRAIN_SECONDS = 30


def now() -> str:
    return datetime.now(UTC).isoformat()


def plain(value):
    """Convert library objects into JSON-compatible values."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return plain(value.value)
    if isinstance(value, Path):
        return str(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [plain(v) for v in value]
    if isinstance(value, BaseException):
        return {"type": type(value).__name__, **plain(vars(value)), "str": str(value)}
    try:
        return plain(vars(value))
    except TypeError:
        return repr(value)


def error_record(err) -> dict | None:
    if err is None:
        return None
    return {
        "code": getattr(err, "code", None),
        "message": getattr(err, "message", None) or str(err),
        "remedy": getattr(err, "remedy", None),
        "category": getattr(err, "category", None),
        "retryable": getattr(err, "retryable", None),
        "details": plain(getattr(err, "details", None)),
    }


def split_segments(turns: list[dict]) -> list[list[dict]]:
    segments: list[list[dict]] = [[]]
    for turn in turns:
        if turn.get("restart"):
            segments.append([])
        else:
            segments[-1].append(turn)
    return segments


def write_json_atomic(path: Path, data) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


class Tee:
    def __init__(self, stream, log):
        self.stream, self.log = stream, log

    def write(self, data):
        self.stream.write(data)
        self.log.write(data)
        return len(data)

    def flush(self):
        self.stream.flush()
        self.log.flush()


def load_host(name: str | None, task: dict):
    if not name:
        return None
    module = importlib.import_module(f"hosts.{name}")
    module.TASK = task  # ty: ignore[unresolved-attribute]
    return module


def build_options(task: dict, host) -> AgentOptions:
    agent = task.get("agent") or {}
    os.environ.update({key: str(value) for key, value in (task.get("env") or {}).items()})
    host_tools = list(host.tools()) if host else []
    tools = task.get("tools", "all")
    tools = ([*BUILTIN_TOOLS, *host_tools] if host_tools else None) if tools == "all" else [*tools, *host_tools]

    approvals = task.get("approvals", "none")
    if approvals == "none":
        approvals = None
    elif approvals == "host":
        approvals = host.approvals()
    elif approvals not in ("allow", "deny"):
        raise ValueError(f"unknown approvals value: {approvals!r}")

    extra = {
        key: task["agent_options"][key]
        for key in ("tool_error_policy", "tool_result_max_bytes")
        if key in (task.get("agent_options") or {})
    }
    return AgentOptions(
        provider=agent.get("provider"),
        model=agent.get("model"),
        tools=tools,
        skills=task.get("skills") or None,
        approvals=approvals,
        **extra,
    )


def turn_input(spec: dict) -> TurnInput:
    user = spec.get("user", "")
    content = [TextPart(user)] if user else []
    history = spec.get("history")
    if history is None:
        return TurnInput(content=content)
    messages = [ConversationMessage(m["role"], [TextPart(m["content"])]) for m in history]
    return TurnInput(content=content, history=messages)


async def consume(turn, spec: dict, record: dict, events_file) -> None:
    async for event in turn.events():
        envelope = {
            "contract_version": event.contract_version,
            "session_id": event.session_id,
            "turn_id": event.turn_id,
            "sequence": event.sequence,
            "at": plain(event.at),
            "type": event.type,
            "payload": plain(event.payload),
        }
        events_file.write(json.dumps(envelope) + "\n")
        events_file.flush()
        record["event_count"] += 1
        if event.type == "terminal":
            result = event.payload
            record["state"] = result.state
            record["content"] = "".join(part.text for part in result.content or [])
            record["error"] = error_record(result.error)
            record["usage"] = plain(result.usage)
    if spec.get("consume_twice"):
        try:
            async for _ in turn.events():
                break
            record["second_events_error"] = None
        except Exception as exc:
            record["second_events_error"] = {"type": type(exc).__name__, "code": getattr(exc, "code", None)}


async def run_turn(session, spec: dict, record: dict, events_file) -> None:
    record.update(state=None, content=None, error=None, usage=None, started_at=now(), ended_at=None, event_count=0)
    try:
        turn = await session.start_turn(turn_input(spec))
        record["turn_id"] = turn.info.turn_id
        consumer = asyncio.create_task(consume(turn, spec, record, events_file))
        try:
            await asyncio.shield(consumer)
        except asyncio.CancelledError:
            await turn.cancel()
            try:
                await asyncio.wait_for(consumer, DRAIN_SECONDS)
            except Exception as exc:
                record["drain_error"] = f"{type(exc).__name__}: {exc}"
            raise
    finally:
        record["ended_at"] = now()


async def run_segment(task: dict, segment: int, host, seg_record: dict, events_file) -> None:
    segments = split_segments(task.get("turns") or [])
    if segment >= len(segments):
        raise ValueError(f"segment {segment} out of range: task has {len(segments)}")
    first_index = sum(len(s) for s in segments[:segment])
    session_spec = task.get("session") or {}

    async with await create_agent(build_options(task, host)) as agent:
        if segment == 0:
            session = await agent.create_session(
                SessionOptions(
                    session_id=session_spec.get("session_id"),
                    persistence=session_spec.get("persistence", "durable"),
                )
            )
        else:
            session = await agent.resume_session(session_spec["session_id"])
        async with session:
            for offset, spec in enumerate(segments[segment]):
                if "tools" in spec:
                    print(f"turn {first_index + offset}: per-turn tools ignored, not supported by the API")
                record = {"index": first_index + offset}
                seg_record["turns"].append(record)
                await run_turn(session, spec, record, events_file)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--segment", required=True, type=int)
    parser.add_argument("--cwd", default="/workspace", help="working directory the agent captures")
    args = parser.parse_args()

    try:
        task = json.loads(args.task.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"cannot read task {args.task}: {exc}", file=sys.stderr)
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    log = (args.out / "driver.log").open("a", encoding="utf-8")
    sys.stdout, sys.stderr = Tee(sys.__stdout__, log), Tee(sys.__stderr__, log)
    print(f"--- segment {args.segment} pid {os.getpid()} at {now()}")
    os.chdir(args.cwd)

    write_json_atomic(
        args.out / "env.json",
        {
            "pid": os.getpid(),
            "segment": args.segment,
            "python": platform.python_version(),
            "amplifier_agent": amplifier_agent.__version__,
            "contract_versions": list(amplifier_agent.contract_versions),
        },
    )

    result_path = args.out / "result.json"
    result: dict[str, Any] = (
        json.loads(result_path.read_text(encoding="utf-8"))
        if result_path.exists()
        else {"segments": [], "host": None, "driver_error": None}
    )
    seg_record = {"segment": args.segment, "pid": os.getpid(), "started_at": now(), "ended_at": None, "turns": []}
    result["segments"].append(seg_record)

    exit_code = 0
    host = None
    with (args.out / "events.jsonl").open("a", encoding="utf-8") as events_file:
        try:
            host = load_host(task.get("host"), task)
            asyncio.run(
                asyncio.wait_for(
                    run_segment(task, args.segment, host, seg_record, events_file),
                    task.get("timeout_seconds", 300),
                )
            )
        except TimeoutError:
            result["driver_error"] = {
                "type": "TimeoutError",
                "code": "segment_timeout",
                "message": f"segment {args.segment} exceeded {task.get('timeout_seconds', 300)}s",
                "remedy": "Raise timeout_seconds or investigate the stalled turn.",
                "traceback": traceback.format_exc(),
            }
        except Exception as exc:
            result["driver_error"] = {
                "type": type(exc).__name__,
                "code": getattr(exc, "code", None),
                "message": getattr(exc, "message", None) or str(exc),
                "remedy": getattr(exc, "remedy", None),
                "traceback": traceback.format_exc(),
            }
            if hasattr(exc, "code"):
                print(f"driver_error {exc.code}: {result['driver_error']['message']}", file=sys.stderr)
            else:
                exit_code = 2
                traceback.print_exc()

    seg_record["ended_at"] = now()
    if host is not None:
        result["host"] = plain(host.record())
    write_json_atomic(result_path, result)
    print(f"--- segment {args.segment} done, driver_error={result['driver_error'] and result['driver_error']['code']}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

"""Run one segment of a task's turn plan against the installed ACP face through the ACP Python SDK, as an editor does.

The driver spawns `uv run amplifier-agent-acp` in the task's working directory with the agent's stderr in acp.log,
sends `initialize` and `session/new`, then one `session/prompt` per turn: the user text as a text block and each of
`images` as an image block. Every `session/update` notification is appended to events.jsonl as received, with the
segment and the turn it arrived in. A turn's `answer` (allow, deny, or cancel) answers each `session/request_permission`
with the option of kind allow_once, the option of kind reject_once, or the cancelled outcome; a turn without one
answers cancelled. It writes result.json in drive.py's shape, plus an `acp` record per turn. A JSON-RPC error on any
request is the turn's error. See ../README.md for the task format.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
from datetime import UTC, datetime
import importlib.metadata as md
import json
import os
from pathlib import Path
import platform
import sys
import traceback
from typing import IO, Any, cast

from acp import Client, RequestError, spawn_agent_process
from acp.client.connection import ClientSideConnection
from acp.connection import StreamDirection, StreamEvent
from acp.schema import (
    AllowedOutcome,
    AudioContentBlock,
    ClientCapabilities,
    DeniedOutcome,
    EmbeddedResourceContentBlock,
    ImageContentBlock,
    PermissionOption,
    RequestPermissionResponse,
    ResourceContentBlock,
    TextContentBlock,
    ToolCallUpdate,
)
from pydantic import BaseModel

PROTOCOL_VERSION = 1
MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
# The permission option kind each turn `answer` selects; any other answer, or none, is the cancelled outcome.
ANSWER_KINDS = {"allow": "allow_once", "deny": "reject_once"}
STATES = {"end_turn": "success", "cancelled": "cancelled"}
PromptBlock = (
    TextContentBlock | ImageContentBlock | AudioContentBlock | ResourceContentBlock | EmbeddedResourceContentBlock
)


def now() -> str:
    return datetime.now(UTC).isoformat()


def write_json_atomic(path: Path, data) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def version(name: str) -> str | None:
    try:
        return md.version(name)
    except md.PackageNotFoundError:
        return None


def split_segments(turns: list[dict]) -> list[list[dict]]:
    segments: list[list[dict]] = [[]]
    for turn in turns:
        if turn.get("restart"):
            segments.append([])
        else:
            segments[-1].append(turn)
    return segments


def wire(model: BaseModel) -> dict[str, Any]:
    """The fields the message carried, under their wire names."""
    return model.model_dump(mode="json", by_alias=True, exclude_unset=True)


def error_record(error: RequestError, method: str) -> dict[str, Any]:
    return {"method": method, "code": error.code, "message": str(error), "data": error.data}


def media_type(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix not in MEDIA_TYPES:
        raise ValueError(f"image {path}: unsupported extension {suffix!r}; use one of {', '.join(MEDIA_TYPES)}")
    return MEDIA_TYPES[suffix]


def prompt_blocks(spec: dict, cwd: Path) -> list[PromptBlock]:
    """The turn's prompt: the user text as a text block, then one base64 image block per image."""
    user = spec.get("user", "")
    blocks: list[PromptBlock] = [TextContentBlock(text=user)] if user else []
    for path in spec.get("images") or []:
        kind = media_type(path)
        data = base64.b64encode((cwd / path).read_bytes()).decode("ascii")
        blocks.append(ImageContentBlock(data=data, mime_type=kind))
    return blocks


class Editor:
    """The client side of the connection: records session updates and answers permission requests."""

    def __init__(self, segment: int, events: IO[str]) -> None:
        self.segment = segment
        self.events = events
        self.turn: int | None = None
        self.answer: str | None = None
        self.chunks: list[str] = []
        self.permissions: list[dict[str, Any]] = []

    def observe(self, event: StreamEvent) -> None:
        """Record each incoming session/update from the wire, in arrival order, before the SDK parses it."""
        message = event.message
        if event.direction != StreamDirection.INCOMING or message.get("method") != "session/update":
            return
        line = {"segment": self.segment, "turn": self.turn, "at": now(), "message": message}
        self.events.write(json.dumps(line) + "\n")
        self.events.flush()
        update = (message.get("params") or {}).get("update") or {}
        content = update.get("content") or {}
        if update.get("sessionUpdate") == "agent_message_chunk" and content.get("type") == "text":
            self.chunks.append(content.get("text") or "")

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        """Recorded by `observe`."""

    async def request_permission(
        self, session_id: str, tool_call: ToolCallUpdate, options: list[PermissionOption], **kwargs: Any
    ) -> RequestPermissionResponse:
        kind = ANSWER_KINDS.get(self.answer or "")
        option = next((option for option in options if option.kind == kind), None)
        outcome = AllowedOutcome(option_id=option.option_id) if option else DeniedOutcome()
        self.permissions.append(
            {
                "tool_call_id": tool_call.tool_call_id,
                "options": [wire(offered) for offered in options],
                "answer": self.answer,
                "outcome": outcome.outcome,
                "option_id": option.option_id if option else None,
            }
        )
        return RequestPermissionResponse(outcome=outcome)


async def run_turn(
    conn: ClientSideConnection, editor: Editor, session_id: str, spec: dict, record: dict, cwd: Path
) -> None:
    editor.turn, editor.answer, editor.chunks = record["index"], spec.get("answer"), []
    editor.permissions = record["acp"]["permissions"]
    try:
        response = await conn.prompt(session_id=session_id, prompt=prompt_blocks(spec, cwd))
        record["acp"]["stop_reason"] = response.stop_reason
        record["state"] = STATES.get(response.stop_reason, "failure")
    except RequestError as error:
        record["state"] = "failure"
        record["error"] = record["acp"]["error"] = error_record(error, "session/prompt")
    finally:
        record["content"] = "".join(editor.chunks)
        record["ended_at"] = now()
        editor.turn = None


async def run_segment(task: dict, segment: int, args: argparse.Namespace, seg_record: dict, events: IO[str]) -> None:
    segments = split_segments(task.get("turns") or [])
    if segment >= len(segments):
        raise ValueError(f"segment {segment} out of range: task has {len(segments)}")
    first_index = sum(len(s) for s in segments[:segment])
    environment = os.environ | {key: str(value) for key, value in (task.get("env") or {}).items()}
    agent = task.get("agent") or {}
    for key, name in (("provider", "AMPLIFIER_AGENT_PROVIDER"), ("model", "AMPLIFIER_AGENT_MODEL")):
        if agent.get(key):
            environment[name] = agent[key]
    environment.pop("VIRTUAL_ENV", None)
    editor = Editor(segment, events)
    record_agent: dict[str, Any] = {}
    seg_record["agent"] = record_agent
    with (args.out / "acp.log").open("a", encoding="utf-8") as log:
        log.write(f"--- agent at {now()}\n")
        log.flush()
        process = None
        try:
            async with spawn_agent_process(
                cast(Client, editor),
                "uv",
                "run",
                "--project",
                str(args.app),
                "amplifier-agent-acp",
                env=environment,
                cwd=args.cwd,
                transport_kwargs={"stderr": log},
                observers=[editor.observe],
            ) as (conn, process):
                record_agent["pid"] = process.pid
                initialize: dict[str, Any] | None = None
                session_id: str | None = None
                setup_error: dict[str, Any] | None = None
                method = "initialize"
                try:
                    capabilities = ClientCapabilities()
                    initialize = wire(
                        await conn.initialize(protocol_version=PROTOCOL_VERSION, client_capabilities=capabilities)
                    )
                    method = "session/new"
                    session_id = (await conn.new_session(cwd=args.cwd, mcp_servers=[])).session_id
                except RequestError as error:
                    setup_error = error_record(error, method)
                for offset, spec in enumerate(segments[segment]):
                    acp = {
                        "initialize": initialize,
                        "session_id": session_id,
                        "stop_reason": None,
                        "error": None,
                        "permissions": [],
                    }
                    record: dict[str, Any] = {
                        "index": first_index + offset,
                        "images": list(spec.get("images") or []),
                        "state": None,
                        "content": None,
                        "error": None,
                        "usage": None,
                        "started_at": now(),
                        "ended_at": None,
                        "acp": acp,
                    }
                    seg_record["turns"].append(record)
                    if session_id is None:
                        record["state"] = "failure"
                        record["error"] = acp["error"] = setup_error
                        record["ended_at"] = now()
                        continue
                    await run_turn(conn, editor, session_id, spec, record, Path(args.cwd))
        finally:
            if process is not None:
                record_agent["exit"] = process.returncode
            log.write(f"--- agent exited {record_agent.get('exit')} at {now()}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--segment", required=True, type=int)
    parser.add_argument("--cwd", default="/workspace", help="working directory the face runs in and opens sessions in")
    parser.add_argument("--app", default=Path.cwd(), type=Path, help="the uv project the face is installed in")
    args = parser.parse_args()
    args.out = args.out.resolve()
    args.app = args.app.resolve()

    try:
        task = json.loads(args.task.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"cannot read task {args.task}: {exc}", file=sys.stderr)
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    log = (args.out / "driver.log").open("a", encoding="utf-8")

    def say(line: str) -> None:
        print(line, flush=True)
        log.write(line + "\n")
        log.flush()

    say(f"--- segment {args.segment} pid {os.getpid()} at {now()}")
    write_json_atomic(
        args.out / "env.json",
        {
            "pid": os.getpid(),
            "segment": args.segment,
            "python": platform.python_version(),
            "amplifier_agent_acp": version("amplifier-agent-acp"),
            "amplifier_agent": version("amplifier-agent"),
            "agent_client_protocol": version("agent-client-protocol"),
        },
    )

    result_path = args.out / "result.json"
    result: dict[str, Any] = (
        json.loads(result_path.read_text(encoding="utf-8"))
        if result_path.exists()
        else {"segments": [], "host": None, "driver_error": None}
    )
    seg_record: dict[str, Any] = {
        "segment": args.segment,
        "pid": os.getpid(),
        "started_at": now(),
        "ended_at": None,
        "turns": [],
    }
    result["segments"].append(seg_record)

    exit_code = 0
    with (args.out / "events.jsonl").open("a", encoding="utf-8") as events:
        try:
            asyncio.run(
                asyncio.wait_for(
                    run_segment(task, args.segment, args, seg_record, events), task.get("timeout_seconds", 300)
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
            exit_code = 2
            result["driver_error"] = {
                "type": type(exc).__name__,
                "code": None,
                "message": str(exc),
                "remedy": None,
                "traceback": traceback.format_exc(),
            }
            say(traceback.format_exc())

    seg_record["ended_at"] = now()
    write_json_atomic(result_path, result)
    say(f"--- segment {args.segment} done, driver_error={result['driver_error'] and result['driver_error']['code']}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

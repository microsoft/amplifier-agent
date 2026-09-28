"""Run one segment of a task's turn plan against the installed HTTP face through the OpenAI Python client.

The driver starts `uv run amplifier-agent-face` in the task's working directory, waits for `/v1/models`, and sends
each turn as one chat-completions request carrying the whole conversation so far. `stream: true` on a turn streams
it. It writes result.json in drive.py's shape, plus an `http` record per turn; the face emits no events. A turn's
usage is the face's one total as a single entry. See ../README.md for the task format.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import importlib.metadata as md
import json
import os
from pathlib import Path
import platform
import secrets
import socket
import subprocess
import sys
import time
import traceback
from typing import Any

import openai
from openai.types.chat import ChatCompletionMessageParam

STOP_SECONDS = 10


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


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def error_record(error: openai.APIError) -> dict[str, Any]:
    """The face's error body as the OpenAI client surfaces it, with the HTTP status when there was one."""
    body = error.body if isinstance(error.body, dict) else {}
    return {
        "code": body.get("code") or getattr(error, "code", None),
        "message": body.get("message") or error.message,
        "remedy": body.get("remedy"),
        "category": body.get("type"),
        "param": body.get("param"),
        "status": getattr(error, "status_code", None),
        "client_error": type(error).__name__,
    }


class Face:
    """The HTTP face as a child process, with its output appended to face.log."""

    def __init__(self, agent: dict, env: dict, cwd: str, app: Path, log: Path) -> None:
        self.token = secrets.token_hex(16)
        self.port = free_port()
        self.base_url = f"http://127.0.0.1:{self.port}/v1"
        environment = os.environ | {key: str(value) for key, value in env.items()}
        environment |= {"AMPLIFIER_AGENT_FACE_TOKEN": self.token, "AMPLIFIER_AGENT_FACE_PORT": str(self.port)}
        for key, name in (("provider", "AMPLIFIER_AGENT_PROVIDER"), ("model", "AMPLIFIER_AGENT_MODEL")):
            if agent.get(key):
                environment[name] = agent[key]
        environment.pop("VIRTUAL_ENV", None)
        self.log = log.open("a", encoding="utf-8")
        self.log.write(f"--- face on port {self.port} at {now()}\n")
        self.log.flush()
        self.process = subprocess.Popen(
            ["uv", "run", "--project", str(app), "amplifier-agent-face"],
            cwd=cwd,
            env=environment,
            stdout=self.log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )

    def wait_ready(self, client: openai.OpenAI, deadline: float) -> list[str]:
        """The model ids /v1/models lists once the face answers; raises when it exits or the deadline passes."""
        while True:
            if self.process.poll() is not None:
                raise RuntimeError(f"the face exited {self.process.returncode} before answering; see face.log")
            try:
                return [model.id for model in client.models.list()]
            except openai.APIConnectionError:
                if time.monotonic() >= deadline:
                    raise TimeoutError from None
                time.sleep(1)

    def stop(self) -> int | None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(STOP_SECONDS)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.log.write(f"--- face exited {self.process.returncode} at {now()}\n")
        self.log.close()
        return self.process.returncode


def turn_usage(usage: dict[str, Any] | None, model: str | None) -> dict[str, Any] | None:
    """The face's one usage total as a single drive.py usage entry.

    The face sums every selection the turn used, so the entry carries the face's model alias and no provider.
    Cache writes are folded into prompt tokens by the face, so the entry has no cache_write_tokens.
    """
    if not usage:
        return None
    entry: dict[str, Any] = {
        "provider": None,
        "model": model,
        "tokens_in": usage.get("prompt_tokens"),
        "tokens_out": usage.get("completion_tokens"),
    }
    details = usage.get("prompt_tokens_details") or {}
    if details.get("cached_tokens") is not None:
        entry["cache_read_tokens"] = details["cached_tokens"]
    if usage.get("cost_usd") is not None:
        entry["cost"] = {"USD": usage["cost_usd"]}
    return {"entries": [entry]}


def run_turn(
    client: openai.OpenAI, model: str, messages: list[ChatCompletionMessageParam], spec: dict, record: dict
) -> None:
    stream = bool(spec.get("stream", False))
    http: dict[str, Any] = {"stream": stream, "messages_sent": len(messages), "finish_reason": None}
    record.update(state=None, content=None, error=None, usage=None, started_at=now(), ended_at=None, http=http)
    try:
        if stream:
            deltas: list[str] = []
            roles: list[str] = []
            http |= {"chunks": 0, "deltas": deltas, "roles": roles}
            chunks = client.chat.completions.create(model=model, messages=messages, stream=True)
            for chunk in chunks:
                http["chunks"] += 1
                http.setdefault("id", chunk.id)
                http.setdefault("object", chunk.object)
                http.setdefault("model", chunk.model)
                for choice in chunk.choices:
                    if choice.delta.role:
                        roles.append(choice.delta.role)
                    if choice.delta.content is not None:
                        deltas.append(choice.delta.content)
                    if choice.finish_reason:
                        http["finish_reason"] = choice.finish_reason
                if chunk.usage is not None:
                    http["usage"] = chunk.usage.model_dump()
            content = "".join(deltas)
        else:
            reply = client.chat.completions.create(model=model, messages=messages, stream=False)
            choice = reply.choices[0]
            http |= {
                "id": reply.id,
                "object": reply.object,
                "model": reply.model,
                "choices": len(reply.choices),
                "role": choice.message.role,
                "finish_reason": choice.finish_reason,
                "usage": reply.usage.model_dump() if reply.usage else None,
            }
            content = choice.message.content
        record["content"] = content
        record["usage"] = turn_usage(http.get("usage"), http.get("model"))
        record["state"] = "success" if http["finish_reason"] == "stop" else "failure"
    except openai.APIError as error:
        record["state"] = "failure"
        record["error"] = error_record(error)
    finally:
        record["ended_at"] = now()


def run_segment(task: dict, segment: int, args: argparse.Namespace, seg_record: dict) -> None:
    segments = split_segments(task.get("turns") or [])
    if segment >= len(segments):
        raise ValueError(f"segment {segment} out of range: task has {len(segments)}")
    first_index = sum(len(s) for s in segments[:segment])
    timeout = task.get("timeout_seconds", 300)
    deadline = time.monotonic() + timeout
    face = Face(task.get("agent") or {}, task.get("env") or {}, args.cwd, args.app, args.out / "face.log")
    record_face: dict[str, Any] = {"port": face.port, "pid": face.process.pid}
    seg_record["face"] = record_face
    try:
        client = openai.OpenAI(base_url=face.base_url, api_key=face.token, max_retries=0, timeout=timeout)
        models = face.wait_ready(client, deadline)
        record_face["models"] = models
        model = models[0]
        messages: list[ChatCompletionMessageParam] = []
        for offset, spec in enumerate(segments[segment]):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            record: dict[str, Any] = {"index": first_index + offset}
            seg_record["turns"].append(record)
            messages.append({"role": "user", "content": spec.get("user", "")})
            run_turn(client.with_options(timeout=remaining), model, messages, spec, record)
            if isinstance(record["error"], dict) and record["error"]["client_error"] == "APITimeoutError":
                raise TimeoutError
            messages.append({"role": "assistant", "content": record["content"] or ""})
    finally:
        record_face["exit"] = face.stop()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--segment", required=True, type=int)
    parser.add_argument("--cwd", default="/workspace", help="working directory the face runs in")
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
            "amplifier_agent_http": version("amplifier-agent-http"),
            "amplifier_agent": version("amplifier-agent"),
            "openai": openai.__version__,
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
    try:
        run_segment(task, args.segment, args, seg_record)
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

"""Run one segment of a task's turn plan against the installed HTTP face through the OpenAI Python client.

The driver starts `uv run amplifier-agent-face` in the task's working directory, waits for `/v1/models`, and sends
each turn as one chat-completions request carrying the whole conversation so far. `stream: true` on a turn streams
it; a turn's `model` and `reasoning_effort` become the request's fields, and `session.session_id` is sent as the
`Amplifier-Session-Id` header on every request. `echo_reasoning: true` sends the turn's returned reasoning back as
`reasoning_content` on its assistant message in later requests. It writes result.json in drive.py's shape, plus an `http` record per
turn. A turn's usage is the face's one total as a single entry. A turn with `answer` (allow, deny, or cancel) is sent
instead to /v1/amplifier/turns with approvals "ask": the driver answers each approval request with that decision,
appends every event to events.jsonl, and takes the turn's state, content, error, and usage from its terminal event.
See ../README.md for the task format.
"""

from __future__ import annotations

import argparse
import base64
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
from typing import Any, cast

import httpx
import openai
from openai.types.chat import ChatCompletionContentPartParam, ChatCompletionMessageParam, ChatCompletionUserMessageParam

STOP_SECONDS = 10
MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


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
    Cache writes are part of prompt tokens and also reported apart, as the entry's cache_write_tokens.
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
    if details.get("cache_write_tokens") is not None:
        entry["cache_write_tokens"] = details["cache_write_tokens"]
    if usage.get("cost_usd") is not None:
        entry["cost"] = {"USD": usage["cost_usd"]}
    return {"entries": [entry]}


def media_type(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix not in MEDIA_TYPES:
        raise ValueError(f"image {path}: unsupported extension {suffix!r}; use one of {', '.join(MEDIA_TYPES)}")
    return MEDIA_TYPES[suffix]


def user_message(spec: dict, cwd: Path) -> ChatCompletionUserMessageParam:
    """The turn's user message: plain text, or a content-part array with one base64 data URL per image."""
    user = spec.get("user", "")
    images = spec.get("images") or []
    if not images:
        return {"role": "user", "content": user}
    parts: list[ChatCompletionContentPartParam] = [{"type": "text", "text": user}] if user else []
    for path in images:
        kind = media_type(path)
        data = base64.b64encode((cwd / path).read_bytes()).decode("ascii")
        parts.append({"type": "image_url", "image_url": {"url": f"data:{kind};base64,{data}"}})
    return {"role": "user", "content": parts}


def reasoning_of(value: Any) -> str | None:
    """The `reasoning_content` extension field the face adds to a message or delta, if any."""
    reasoning = (value.model_extra or {}).get("reasoning_content")
    return reasoning if isinstance(reasoning, str) else None


def returned_reasoning(http: dict[str, Any]) -> str:
    """The reasoning the face returned for a turn: the joined deltas when streamed, the message's field otherwise."""
    if http.get("stream"):
        return "".join(http.get("reasoning_deltas") or [])
    return http.get("reasoning_content") or ""


def run_events_turn(
    client: openai.OpenAI,
    model: str,
    messages: list[ChatCompletionMessageParam],
    spec: dict,
    record: dict,
    session_id: str | None,
    events: Path,
) -> None:
    """One turn over /v1/amplifier/turns, answering each approval request with the turn's `answer`."""
    model = spec.get("model") or model
    http: dict[str, Any] = {
        "endpoint": "turns",
        "messages_sent": len(messages),
        "model_sent": model,
        "reasoning_effort_sent": spec.get("reasoning_effort"),
        "session_id_sent": session_id,
        "answers": [],
        "events": 0,
    }
    body: dict[str, Any] = {"model": model, "messages": messages, "approvals": "ask"}
    if spec.get("reasoning_effort"):
        body["reasoning_effort"] = spec["reasoning_effort"]
    headers = {"Authorization": f"Bearer {client.api_key}"}
    if session_id:
        headers["Amplifier-Session-Id"] = session_id
    record.update(state=None, content=None, error=None, usage=None, started_at=now(), ended_at=None, http=http)
    base = str(client.base_url).rstrip("/")
    try:
        with (
            httpx.Client(base_url=base, headers=headers, timeout=client.timeout) as face,
            face.stream("POST", "/amplifier/turns", json=body) as response,
            events.open("a", encoding="utf-8") as sink,
        ):
            http["status"] = response.status_code
            if response.status_code != 200:
                text = response.read().decode("utf-8", "replace")
                try:
                    error = json.loads(text).get("error") or {}
                except (ValueError, AttributeError):
                    error = {"code": None, "message": text[:2000]}
                record["state"] = "failure"
                record["error"] = {**error, "status": response.status_code}
                return
            for line in response.iter_lines():
                if not line.startswith("data: "):
                    continue
                event = json.loads(line.removeprefix("data: "))
                http["events"] += 1
                sink.write(json.dumps(event) + "\n")
                sink.flush()
                record.setdefault("turn_id", event["turn_id"])
                if event["type"] == "approval_request":
                    request_id = event["payload"]["request"]["request_id"]
                    answer = face.post(f"/amplifier/approvals/{request_id}", json={"decision": spec["answer"]})
                    http["answers"].append({"request_id": request_id, "status": answer.status_code})
                elif event["type"] == "terminal":
                    payload = event["payload"]
                    record["state"] = payload["state"]
                    record["content"] = "".join(part.get("text", "") for part in payload.get("content") or [])
                    record["error"] = payload.get("error")
                    record["usage"] = payload.get("usage")
    except httpx.TimeoutException:
        record["state"] = "failure"
        record["error"] = {"code": None, "message": "the turn timed out", "client_error": "APITimeoutError"}
    finally:
        record["ended_at"] = now()


def run_turn(
    client: openai.OpenAI,
    model: str,
    messages: list[ChatCompletionMessageParam],
    spec: dict,
    record: dict,
    session_id: str | None = None,
) -> None:
    stream = bool(spec.get("stream", False))
    model = spec.get("model") or model
    http: dict[str, Any] = {
        "stream": stream,
        "messages_sent": len(messages),
        "model_sent": model,
        "reasoning_effort_sent": spec.get("reasoning_effort"),
        "session_id_sent": session_id,
        "reasoning_content_sent": sum(1 for message in messages if "reasoning_content" in message),
        "finish_reason": None,
    }
    options: dict[str, Any] = {}
    if spec.get("reasoning_effort"):
        options["reasoning_effort"] = spec["reasoning_effort"]
    if session_id:
        options["extra_headers"] = {"Amplifier-Session-Id": session_id}
    record.update(state=None, content=None, error=None, usage=None, started_at=now(), ended_at=None, http=http)
    try:
        if stream:
            deltas: list[str] = []
            roles: list[str] = []
            reasoning: list[str] = []
            http |= {"chunks": 0, "deltas": deltas, "roles": roles, "reasoning_deltas": reasoning}
            chunks = client.chat.completions.create(model=model, messages=messages, stream=True, **options)
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
                    if reasoning_of(choice.delta) is not None:
                        reasoning.append(reasoning_of(choice.delta) or "")
                    if choice.finish_reason:
                        http["finish_reason"] = choice.finish_reason
                if chunk.usage is not None:
                    http["usage"] = chunk.usage.model_dump()
            content = "".join(deltas)
        else:
            reply = client.chat.completions.create(model=model, messages=messages, stream=False, **options)
            choice = reply.choices[0]
            http |= {
                "id": reply.id,
                "object": reply.object,
                "model": reply.model,
                "choices": len(reply.choices),
                "role": choice.message.role,
                "reasoning_content": reasoning_of(choice.message),
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
        session_id = (task.get("session") or {}).get("session_id")
        messages: list[ChatCompletionMessageParam] = []
        for offset, spec in enumerate(segments[segment]):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            record: dict[str, Any] = {"index": first_index + offset, "images": list(spec.get("images") or [])}
            seg_record["turns"].append(record)
            messages.append(user_message(spec, Path(args.cwd)))
            if spec.get("answer"):
                run_events_turn(
                    client.with_options(timeout=remaining),
                    model,
                    messages,
                    spec,
                    record,
                    session_id,
                    args.out / "events.jsonl",
                )
            else:
                run_turn(client.with_options(timeout=remaining), model, messages, spec, record, session_id)
            if isinstance(record["error"], dict) and record["error"]["client_error"] == "APITimeoutError":
                raise TimeoutError
            reply: dict[str, Any] = {"role": "assistant", "content": record["content"] or ""}
            reasoning = returned_reasoning(record["http"])
            if spec.get("echo_reasoning") and reasoning:
                reply["reasoning_content"] = reasoning
            messages.append(cast(ChatCompletionMessageParam, reply))
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

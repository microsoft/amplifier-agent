"""Benchmark driver for amplifier-agent: one durable session, a task turn, and recovery turns.

Runs inside the trial container under the binding's own interpreter, so it imports only the
standard library and amplifier_agent. No instructions are passed, so the agent uses its own default
system prompt. Writes into --out:

    run-config.json   what was run, written before the first turn
    stream.jsonl      every event envelope, one JSON line each
    result.json       final state, per-turn states and usage, elapsed time
    sessions/         the binding's sessions directory

With --raw-llm-payloads, the engine attaches each full LLM request and response to the
llm:request and llm:response events in the session's events.jsonl.

stream.jsonl is not named events.jsonl because metrics collection globs that name and must
find only the binding's own context-intelligence events.

Exit codes: 0 success, 1 failure, rejected, or no terminal, 2 cancelled, 3 driver error.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
import signal
import sys
import time
from datetime import datetime
from decimal import Decimal
from importlib import metadata
from pathlib import Path
from typing import Any

from amplifier_agent import AgentOptions, SessionOptions, TextPart, TurnInput, create_agent

# After a tool outcome resolves `unknown`, the engine permits only read-only inspection for the
# rest of that turn, so the remaining work needs a new turn.
RECOVERY_PROMPT = (
    "A tool call in the previous turn ended with an uncertain outcome, so that turn could not "
    "make further changes. Inspect the current state, then continue the original task until it "
    "is complete."
)

# The engine does not repeat a provider request once its output has begun streaming; that failure
# ends the turn with a retryable error, and starting another turn is the caller's job.
RETRY_PROMPT = (
    "The previous turn stopped because a model request failed. Inspect the current state, then "
    "continue the original task until it is complete."
)
RETRY_DELAY_S = 15

RAW_PAYLOADS_ENV = "AMPLIFIER_AGENT_ENGINE_RAW_LLM_PAYLOADS"
ENVELOPE_FIELDS = ("contract_version", "session_id", "turn_id", "sequence", "at", "type", "payload")
EXIT_SUCCESS, EXIT_FAILURE, EXIT_CANCELLED, EXIT_DRIVER_ERROR = 0, 1, 2, 3


class Parser(argparse.ArgumentParser):
    def error(self, message: str):
        # argparse exits 2 by default, which this driver reserves for a cancelled turn.
        self.print_usage(sys.stderr)
        self.exit(EXIT_DRIVER_ERROR, f"{self.prog}: error: {message}\n")


def plain(value: Any) -> Any:
    """Convert binding objects into JSON-compatible values; decimals become strings."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [plain(v) for v in value]
    if isinstance(value, BaseException):
        return error_record(value)
    try:
        return plain(vars(value))
    except TypeError:
        return repr(value)


def error_record(err: BaseException | None) -> dict | None:
    if err is None:
        return None
    return {
        "type": type(err).__name__,
        "code": getattr(err, "code", None),
        "message": getattr(err, "message", None) or str(err),
        "remedy": getattr(err, "remedy", None),
        "category": getattr(err, "category", None),
        "retryable": getattr(err, "retryable", None),
        "details": plain(getattr(err, "details", None)),
    }


def write_json(path: Path, data: Any) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def versions() -> dict[str, Any]:
    info: dict[str, Any] = {}
    for name in ("amplifier-agent", "amplifier-agent-engine"):
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            info[name] = None
            continue
        direct_url = dist.read_text("direct_url.json")
        info[name] = {
            "version": dist.version,
            "direct_url": json.loads(direct_url) if direct_url else None,
        }
    return info


def say(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


async def run_turn(session: Any, prompt: str, stream: Any, control: dict) -> dict:
    turn = await session.start_turn(TurnInput(content=[TextPart(prompt)]))
    control["turn"] = turn
    if control["signal"] is not None:
        await turn.cancel()
    record: dict[str, Any] = {"state": None, "error": None, "usage": None, "unknown": 0}
    names: dict[str, str] = {}
    try:
        async for event in turn.events():
            envelope = {field: plain(getattr(event, field)) for field in ENVELOPE_FIELDS}
            stream.write(json.dumps(envelope) + "\n")
            stream.flush()
            payload = event.payload
            if event.type == "output_delta":
                say("".join(getattr(part, "text", "") for part in payload.content or []))
            elif event.type == "tool_call":
                names[payload.call.call_id] = payload.call.name
                say(f"\n[tool] {payload.call.name}\n")
            elif event.type == "tool_result":
                outcome = payload.resolution.outcome
                if outcome == "unknown":
                    record["unknown"] += 1
                if outcome != "completed":
                    call_id = payload.resolution.call_id
                    say(f"\n[tool {outcome}] {names.get(call_id, call_id)}\n")
            elif event.type == "terminal":
                record.update(
                    state=payload.state,
                    error=error_record(payload.error),
                    usage=plain(payload.usage),
                )
    finally:
        control["turn"] = None
    say("\n")
    return record


async def drive(args: argparse.Namespace, out: Path, result: dict) -> None:
    control: dict[str, Any] = {"turn": None, "signal": None, "cancels": set()}
    loop = asyncio.get_running_loop()

    def on_signal(signum: int) -> None:
        control["signal"] = signal.Signals(signum).name
        if control["turn"] is not None:
            # Turn.cancel() reaches running work and the stream still ends in `terminal`.
            task = loop.create_task(control["turn"].cancel())
            control["cancels"].add(task)
            task.add_done_callback(control["cancels"].discard)

    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, on_signal, signum)

    if args.raw_llm_payloads:
        os.environ[RAW_PAYLOADS_ENV] = "1"
    if "AMPLIFIER_AGENT_CONFIG" not in os.environ:
        host_config = out / "host-config.json"
        host_config.write_text("{}", encoding="utf-8")
        os.environ["AMPLIFIER_AGENT_CONFIG"] = str(host_config)

    working_directory = Path(args.working_directory)
    options: dict[str, Any] = {}
    write_json(
        out / "run-config.json",
        {
            "provider": args.provider,
            "model": args.model,
            "reasoning_effort": args.reasoning_effort,
            "working_directory": str(working_directory),
            "versions": versions(),
            "tools": "built-in defaults",
            "max_recovery_turns": args.max_recovery_turns,
            "raw_llm_payloads": os.environ.get(RAW_PAYLOADS_ENV) == "1",
        },
    )
    if args.reasoning_effort:
        options["reasoning_effort"] = args.reasoning_effort
    agent_options = AgentOptions(
        provider=args.provider,
        model=args.model,
        working_directory=str(working_directory),
        sessions_directory=str(out / "sessions"),
        approvals="allow",
        tool_error_policy="continue",
        **options,
    )
    prompt = Path(args.prompt_file).read_text(encoding="utf-8")
    async with await create_agent(agent_options) as agent:
        session = await agent.create_session(SessionOptions(persistence="durable"))
        async with session:
            with (out / "stream.jsonl").open("a", encoding="utf-8") as stream:
                while control["signal"] is None:
                    turn = await run_turn(session, prompt, stream, control)
                    result["turns"] += 1
                    result["per_turn"].append([turn["state"], turn["unknown"]])
                    result["usage"].append(turn["usage"])
                    result["state"], result["error"] = turn["state"], turn["error"]
                    retryable = turn["state"] == "failure" and (turn["error"] or {}).get(
                        "retryable"
                    )
                    uncertain = turn["state"] == "success" and turn["unknown"] > 0
                    if (
                        not (retryable or uncertain)
                        or result["recovery_turns"] >= args.max_recovery_turns
                    ):
                        break
                    result["recovery_turns"] += 1
                    if retryable:
                        await asyncio.sleep(RETRY_DELAY_S * result["recovery_turns"])
                    prompt = RETRY_PROMPT if retryable else RECOVERY_PROMPT
    result["signal"] = control["signal"]


def main(argv: list[str] | None = None) -> int:
    parser = Parser(prog="amplifier-agent-driver", description=__doc__.splitlines()[0])
    parser.add_argument("--version", action="store_true", help="print installed versions")
    parser.add_argument("--prompt-file")
    parser.add_argument("--working-directory")
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--reasoning-effort")
    parser.add_argument("--out")
    parser.add_argument("--max-recovery-turns", type=int, default=2)
    parser.add_argument(
        "--raw-llm-payloads",
        action="store_true",
        help="record full LLM requests and responses in events.jsonl",
    )
    args = parser.parse_args(argv)
    if args.version:
        print(json.dumps(versions(), indent=2))
        return 0
    required = ("prompt_file", "working_directory", "provider", "model", "out")
    missing = [f"--{name.replace('_', '-')}" for name in required if not getattr(args, name)]
    if missing:
        parser.error(f"missing required arguments: {', '.join(missing)}")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    result: dict[str, Any] = {"state": None, "error": None, "turns": 0, "recovery_turns": 0}
    result.update(per_turn=[], usage=[], signal=None, elapsed_s=None)
    try:
        asyncio.run(drive(args, out, result))
        if result["signal"] is not None:
            result["state"] = result["state"] or "cancelled"
        if result["signal"] is not None or result["state"] == "cancelled":
            code = EXIT_CANCELLED
        else:
            code = EXIT_SUCCESS if result["state"] == "success" else EXIT_FAILURE
    except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 - every failure gets a result.json
        result["state"], result["error"] = "driver_error", error_record(exc)
        print(f"driver error: {type(exc).__name__}: {exc}", file=sys.stderr)
        code = EXIT_DRIVER_ERROR
    result["elapsed_s"] = round(time.monotonic() - started, 3)
    write_json(out / "result.json", result)
    return code


if __name__ == "__main__":
    sys.exit(main())

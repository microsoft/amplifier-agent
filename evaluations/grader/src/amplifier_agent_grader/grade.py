"""Grade one trial against its task's grader.yaml with an amplifier-agent grader, inside the trial's container.

`amplifier-agent-grade --layout layout.json --rubric grader.yaml --out DIR --provider P --model M` writes
`DIR/grader_result.json` and, per evaluation, `DIR/<evaluation>/report.md` and `DIR/<evaluation>/events.jsonl`.
`layout.json` maps each piece of evidence to its path in the container; see LAYOUT_KEYS.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses
from datetime import datetime
from decimal import Decimal
from enum import Enum
import json
import os
from pathlib import Path
import sys
import tempfile

from amplifier_agent import AgentOptions, SessionOptions, TextPart, Tool, ToolFailed, TurnInput, create_agent

from amplifier_agent_grader import prompts
from amplifier_agent_grader import rubric as rubric_file

SUBMIT = "submit_rubric"
TOOLS = ("read_file", "glob", "grep", "bash")
TURN_SECONDS = 300
DRAIN_SECONDS = 30
REPORT_RETRIES = 2
SUBMIT_RETRIES = 2
LAYOUT_KEYS = ("workspace", "driver", "task", "sessions", "grader_data", "scratch")


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


def submit_schema(rubric: dict) -> dict:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            key: {
                "type": "object",
                "properties": {
                    "points": {"type": "integer", "minimum": 0, "maximum": int(spec["points"])},
                    "reason": {"type": "string"},
                },
                "required": ["points", "reason"],
                "additionalProperties": False,
            }
            for key, spec in rubric.items()
        },
        "required": list(rubric),
        "additionalProperties": False,
    }


def validate(arguments, rubric: dict) -> str | None:
    if not isinstance(arguments, dict):
        return "arguments must be an object keyed by criterion"
    problems = []
    missing = [key for key in rubric if key not in arguments]
    extra = [key for key in arguments if key not in rubric]
    if missing:
        problems.append(f"missing criteria: {', '.join(missing)}")
    if extra:
        problems.append(f"unknown criteria: {', '.join(extra)}")
    for key, spec in rubric.items():
        entry = arguments.get(key)
        if key not in arguments:
            continue
        if not isinstance(entry, dict):
            problems.append(f"{key} must be an object with points and reason")
            continue
        points, maximum = entry.get("points"), int(spec["points"])
        if not isinstance(points, int) or isinstance(points, bool):
            problems.append(f"{key}.points must be an integer, got {points!r}")
        elif not 0 <= points <= maximum:
            problems.append(f"{key}.points must be between 0 and {maximum}, got {points}")
        if not isinstance(entry.get("reason"), str) or not entry["reason"].strip():
            problems.append(f"{key}.reason must be a non-empty string")
        extra_fields = [field for field in entry if field not in ("points", "reason")]
        if extra_fields:
            problems.append(f"{key} has unknown fields: {', '.join(extra_fields)}")
    return "; ".join(problems) or None


class Submission:
    def __init__(self, rubric: dict) -> None:
        self.rubric = rubric
        self.value: dict | None = None
        self.last_error: str | None = None

    async def handler(self, arguments, context) -> str:
        if self.value is not None:
            return "A valid rubric was already recorded; this submission is ignored."
        problem = validate(arguments, self.rubric)
        if problem:
            self.last_error = problem
            raise ToolFailed(f"Invalid submission: {problem}")
        self.value = arguments
        return "Rubric recorded."

    def tool(self) -> Tool:
        return Tool(
            name=SUBMIT,
            description="Submit the rubric scores: one entry per criterion with integer points and a reason.",
            input_schema=submit_schema(self.rubric),
            handler=self.handler,
        )


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def final_replies(result) -> list[dict]:
    if not isinstance(result, dict):
        return []
    replies = []
    for segment in result.get("segments") or []:
        for turn in segment.get("turns") or []:
            replies.append(
                {
                    "segment": segment.get("segment"),
                    "index": turn.get("index"),
                    "state": turn.get("state"),
                    "content": turn.get("content"),
                    "error": turn.get("error"),
                }
            )
    if result.get("driver_error"):
        error = result["driver_error"]
        replies.append({"driver_error": {key: error.get(key) for key in ("code", "message", "remedy")}})
    return replies


def task_view(task: dict) -> dict:
    """The task fields the system prompt shows."""
    return {
        "name": task.get("name"),
        "description": task.get("description"),
        "turns": task.get("turns") or [],
    }


def evaluation_view(evaluation: dict) -> dict:
    """The evaluation as the system prompt shows it: rubric criteria in order, descriptions on one line."""
    return {
        "name": evaluation["name"],
        "steps": evaluation["steps"].strip(),
        "rubric": [
            {"key": key, "points": spec["points"], "description": " ".join(str(spec["description"]).split())}
            for key, spec in evaluation["rubric"].items()
        ],
    }


def instructions(layout: dict, task: dict, replies: list[dict], evaluation: dict) -> str:
    return prompts.render(
        "system",
        task=task_view(task),
        replies=replies,
        layout=layout,
        evaluation=evaluation_view(evaluation),
        submit_tool=SUBMIT,
    )


class Recorder:
    def __init__(self, path: Path) -> None:
        self.file = path.open("w", encoding="utf-8")

    def write(self, event) -> None:
        envelope = {
            "contract_version": event.contract_version,
            "session_id": event.session_id,
            "turn_id": event.turn_id,
            "sequence": event.sequence,
            "at": plain(event.at),
            "type": event.type,
            "payload": plain(event.payload),
        }
        self.file.write(json.dumps(envelope) + "\n")
        self.file.flush()

    def close(self) -> None:
        self.file.close()


async def run_turn(session, text: str, recorder: Recorder) -> dict:
    turn = await session.start_turn(TurnInput(content=[TextPart(text)]))
    record = {"state": None, "content": "", "error": None, "usage": None}

    async def consume() -> None:
        async for event in turn.events():
            recorder.write(event)
            if event.type == "terminal":
                result = event.payload
                record["state"] = result.state
                record["content"] = "".join(part.text for part in result.content or [])
                record["error"] = result.error
                record["usage"] = plain(result.usage)

    consumer = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(asyncio.shield(consumer), TURN_SECONDS)
    except TimeoutError:
        await turn.cancel()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(consumer, DRAIN_SECONDS)
        record["state"] = record["state"] or "timeout"
    return record


def error_text(record: dict) -> str | None:
    error = record["error"]
    if error is None:
        return None if record["state"] in ("success", None) else record["state"]
    return f"{getattr(error, 'code', None)}: {getattr(error, 'message', None) or error}"


async def grade_evaluation(
    evaluation: dict, layout: dict, task: dict, replies: list[dict], out: Path, provider: str, model: str, storage: str
) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    submission = Submission(evaluation["rubric"])
    recorder = Recorder(out / "events.jsonl")
    turns: list[dict] = []
    reports: list[str] = []

    def step(phase: str, record: dict) -> None:
        turns.append({"phase": phase, "state": record["state"], "error": error_text(record), "usage": record["usage"]})

    try:
        options = AgentOptions(
            provider=provider,
            model=model,
            instructions=instructions(layout, task, replies, evaluation),
            tools=[*TOOLS, submission.tool()],
            storage=storage,
            approvals="allow",
            tool_error_policy="continue",
        )
        async with (
            await create_agent(options) as agent,
            await agent.create_session(SessionOptions(persistence="ephemeral")) as session,
        ):
            prompt = prompts.render("report")
            for _ in range(1 + REPORT_RETRIES):
                record = await run_turn(session, prompt, recorder)
                step("report", record)
                if record["content"].strip():
                    reports.append(record["content"].strip())
                if record["state"] == "success":
                    break
                prompt = prompts.render("report_retry", reason=error_text(record))
            else:
                raise RuntimeError(f"no report after {1 + REPORT_RETRIES} turns: {turns[-1]['error']}")

            prompt = prompts.render("submit", submit_tool=SUBMIT)
            for _ in range(1 + SUBMIT_RETRIES):
                record = await run_turn(session, prompt, recorder)
                step("submit", record)
                if submission.value is not None:
                    break
                reason = submission.last_error or f"{SUBMIT} was not called ({error_text(record) or 'no call'})"
                prompt = prompts.render("submit_retry", reason=reason, submit_tool=SUBMIT)
            else:
                raise RuntimeError(f"no valid {SUBMIT} call after {1 + SUBMIT_RETRIES} turns: {reason}")
    finally:
        recorder.close()

    report = "\n\n".join(reports)
    (out / "report.md").write_text(f"# {evaluation['name']}\n\n{report}\n", encoding="utf-8")
    criteria = {
        key: {
            "points": submission.value[key]["points"],
            "max": int(spec["points"]),
            "reason": submission.value[key]["reason"],
        }
        for key, spec in evaluation["rubric"].items()
    }
    return {
        "name": evaluation["name"],
        "weight": evaluation.get("weight", 1),
        "points_awarded": sum(entry["points"] for entry in criteria.values()),
        "points_possible": sum(entry["max"] for entry in criteria.values()),
        "criteria": criteria,
        "report": report,
        "state": [{key: turn[key] for key in ("phase", "state", "error")} for turn in turns],
        "_usage": [turn["usage"] for turn in turns],
    }


def total_usage(usages: list) -> dict:
    totals: dict[tuple, dict] = {}
    fields = ("tokens_in", "tokens_out", "cache_read_tokens", "cache_write_tokens")
    for usage in usages:
        for entry in (usage or {}).get("entries") or []:
            key = (entry.get("provider"), entry.get("model"))
            total = totals.setdefault(key, {"provider": key[0], "model": key[1], **dict.fromkeys(fields, 0)})
            for field in fields:
                if isinstance(entry.get(field), int):
                    total[field] += entry[field]
    return {"entries": list(totals.values())}


def load_layout(path: Path) -> dict[str, str]:
    layout = load_json(path)
    if not isinstance(layout, dict):
        raise ValueError(f"{path}: layout is a JSON object")
    missing = [key for key in LAYOUT_KEYS if not isinstance(layout.get(key), str)]
    if missing:
        raise ValueError(f"{path}: layout needs {', '.join(missing)}")
    return {key: layout[key] for key in LAYOUT_KEYS}


async def grade(layout_path: Path, rubric_path: Path, out: Path, provider: str, model: str) -> dict:
    layout = load_layout(layout_path)
    rubric = rubric_file.load(rubric_path)
    task = load_json(Path(layout["task"])) or {}
    replies = final_replies(load_json(Path(layout["driver"]) / "result.json"))
    out.mkdir(parents=True, exist_ok=True)
    scratch = Path(layout["scratch"])
    scratch.mkdir(parents=True, exist_ok=True)
    os.chdir(scratch)
    evaluations = []
    with tempfile.TemporaryDirectory(prefix="storage-", dir=scratch) as storage:
        for evaluation in rubric["evaluations"]:
            evaluations.append(
                await grade_evaluation(
                    evaluation, layout, task, replies, out / evaluation["name"], provider, model, storage
                )
            )
    usages = [usage for evaluation in evaluations for usage in evaluation.pop("_usage")]
    weights = sum(evaluation["weight"] for evaluation in evaluations)
    score = (
        sum(
            evaluation["weight"] * evaluation["points_awarded"] / evaluation["points_possible"]
            for evaluation in evaluations
            if evaluation["points_possible"]
        )
        / weights
        if weights
        else 0.0
    )
    overall = round(score, 4)
    return {
        "overall_score": overall,
        "pass_score": rubric["pass_score"],
        "passed": overall >= rubric["pass_score"],
        "evaluations": evaluations,
        "grader": {"provider": provider, "model": model, "usage": total_usage(usages)},
        "rubric": rubric,
    }


def main(argv: list[str] | None = None) -> int:
    """Grade and write `<out>/grader_result.json`; exit 0 when written, 1 when grading failed."""
    parser = argparse.ArgumentParser(prog="amplifier-agent-grade", description=__doc__.splitlines()[0])
    parser.add_argument("--layout", required=True, type=Path, help="JSON mapping evidence to container paths")
    parser.add_argument("--rubric", required=True, type=Path, help="the task's grader.yaml")
    parser.add_argument("--out", required=True, type=Path, help="directory for grader_result.json and reports")
    parser.add_argument("--provider", required=True, help="the grader's provider")
    parser.add_argument("--model", required=True, help="the grader's model")
    args = parser.parse_args(argv)
    out = args.out.resolve()
    try:
        result = asyncio.run(grade(args.layout.resolve(), args.rubric.resolve(), out, args.provider, args.model))
    except Exception as error:
        code = getattr(error, "code", None)
        print(f"grading failed: {type(error).__name__}{f' {code}' if code else ''}: {error}", file=sys.stderr)
        return 1
    path = out / "grader_result.json"
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"overall_score {result['overall_score']} written to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

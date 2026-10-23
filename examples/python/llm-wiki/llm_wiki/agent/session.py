"""Sessions and turns: `AgentOptions`, `create_agent`, a session, `start_turn`, streamed `Event`s, and the `TurnResult`.

Also shows model selection with `reasoning_effort`, skills, `tool_result_max_bytes`, ephemeral and durable sessions,
resuming a durable session by id, cancelling a turn after a timeout, and `AgentError` with its code and remedy.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import json
from pathlib import Path
import sys
from typing import Literal

from amplifier_agent import (
    AgentError,
    AgentOptions,
    Event,
    OutputDelta,
    SessionOptions,
    TextPart,
    Tool,
    ToolCallEvent,
    TurnInput,
    TurnResult,
    create_agent,
)

from llm_wiki.agent.approvals import PathPolicy, approval_handler
from llm_wiki.agent.usage import Tally
from llm_wiki.console import say

# Every model request resends the whole conversation, so one large file read is paid for again on each later step.
TOOL_RESULT_MAX_BYTES = 32_768
# Seconds before one turn is cancelled.
DEFAULT_TIMEOUT = 900.0


@dataclass(frozen=True)
class AgentConfig:
    """What every session shares: the model, the instructions and skills, where sessions live, and the turn timeout."""

    provider: str
    model: str
    reasoning_effort: str
    instructions: str
    skills_directory: Path
    sessions_directory: Path
    timeout: float = DEFAULT_TIMEOUT


@dataclass
class SessionSpec:
    """Everything one model session gets: where it works, what it may touch, and what it is asked."""

    phase: str
    prompt: str
    working_directory: Path
    tools: list[Tool | str]
    read_roots: tuple[Path, ...]
    write_targets: frozenset[Path] = frozenset()
    skills: bool = False


@dataclass
class Outcome:
    ok: bool
    answer: str = ""
    error: str = ""


def describe(result: TurnResult | None, timed_out: bool) -> str:
    if timed_out:
        return "timed out"
    if result is None:
        return "the turn ended without a terminal event"
    if result.error is None:
        return result.state
    return f"{result.error.code}: {result.error.message} {result.error.remedy}"


async def run_turn(
    config: AgentConfig,
    spec: SessionSpec,
    tally: Tally,
    on_event: Callable[[Event], None] | None = None,
    persistence: Literal["durable", "ephemeral"] = "ephemeral",
    resume: str | None = None,
) -> Outcome:
    """One agent, one session, one turn, under a path-scoped approval policy."""
    policy = PathPolicy(
        working_directory=spec.working_directory.resolve(),
        read_roots=spec.read_roots,
        write_targets=spec.write_targets,
        caller_tools=frozenset(tool.name for tool in spec.tools if isinstance(tool, Tool)),
    )
    options = AgentOptions(
        provider=config.provider,
        model=config.model,
        reasoning_effort=config.reasoning_effort,
        instructions=config.instructions,
        tools=spec.tools,
        skills=[str(config.skills_directory)] if spec.skills else None,
        approvals=approval_handler(policy),
        tool_result_max_bytes=TOOL_RESULT_MAX_BYTES,
        working_directory=spec.working_directory,
        sessions_directory=config.sessions_directory,
    )
    result: TurnResult | None = None
    timed_out = False
    try:
        async with await create_agent(options) as agent:
            if resume is not None:
                session = await agent.resume_session(resume)
            else:
                session = await agent.create_session(SessionOptions(persistence=persistence))
            async with session:
                if session.info.persistence == "durable":
                    say("session", session.info.session_id)
                turn = await session.start_turn(TurnInput(content=[TextPart(spec.prompt)]))

                async def watchdog() -> None:
                    nonlocal timed_out
                    await asyncio.sleep(config.timeout)
                    timed_out = True
                    await turn.cancel()

                guard = asyncio.create_task(watchdog())
                try:
                    async for event in turn.events():
                        if on_event is not None:
                            on_event(event)
                        if event.type == "terminal" and isinstance(event.payload, TurnResult):
                            result = event.payload
                finally:
                    guard.cancel()
    except AgentError as error:
        return Outcome(False, error=f"{error.code}: {error.message} {error.remedy}")
    tally.add(spec.phase, result.usage if result is not None else None)
    if result is not None and result.state == "success" and not timed_out:
        return Outcome(True, answer="".join(part.text for part in result.content or []))
    return Outcome(False, error=describe(result, timed_out))


async def with_retry(label: str, attempt: Callable[[], Awaitable[Outcome]]) -> Outcome:
    outcome = await attempt()
    if not outcome.ok:
        say("retry", f"{label}: {outcome.error}")
        outcome = await attempt()
    return outcome


def print_event(event: Event) -> None:
    """Stream the answer to stdout as it arrives, and each tool call to stderr."""
    if isinstance(event.payload, OutputDelta):
        print("".join(part.text for part in event.payload.content), end="", flush=True)
    elif isinstance(event.payload, ToolCallEvent):
        call = event.payload.call
        preview = json.dumps(call.arguments, ensure_ascii=False)
        print(f"tool> {call.name} {preview[:120]}", file=sys.stderr, flush=True)

"""Sessions and turns: `AgentOptions`, `create_agent`, `start_turn`, streamed `Event`s, and the `TurnResult`.

Also shows instructions rebuilt for every turn, a durable session resumed by id across agents and processes, an
ephemeral session for background work, switching a resumed session's model with `set_model`, and `AgentError` with
its code and remedy.

Instructions belong to the agent, and a resumed session uses the resuming agent's instructions. So each turn creates
a fresh agent with current memory and time, and resumes the one main conversation.
"""

from datetime import datetime
import json
from pathlib import Path

from amplifier_agent import (
    Agent,
    AgentError,
    AgentOptions,
    Event,
    OutputDelta,
    Session,
    SessionOptions,
    TextPart,
    ToolCallEvent,
    TurnInput,
    TurnResult,
    create_agent,
)

from personal_assistant.agent.approvals import PathPolicy, approval_handler
from personal_assistant.agent.tools import caller_tools, tools
from personal_assistant.console import note
from personal_assistant.memory import Home

PROVIDER = "openai"
DEFAULT_MODEL = "gpt-6-luna"


def build_instructions(home: Home, now: datetime) -> str:
    sections = [
        (home.root / "SOUL.md").read_text(encoding="utf-8").strip(),
        (home.root / "RULES.md").read_text(encoding="utf-8").strip(),
        "## Memory\n\nThe contents of MEMORY.md:\n\n" + (home.read_memory().strip() or "(empty)"),
        f"## Now\n\nIt is {now:%A %Y-%m-%d %H:%M}, local time.",
        "## Home directory",
        "\n".join(
            [
                f"- {home.root}: your working directory",
                f"- {home.memory}: lasting facts, also shown above",
                f"- {home.logs}: one log per day, named YYYY-MM-DD.md",
                f"- {home.checklist}: the heartbeat checklist",
                f"- {home.reminders}: reminders not delivered yet",
            ]
        ),
    ]
    return "\n\n".join(sections)


def print_event(event: Event) -> None:
    """Stream the answer to stdout as it arrives, and each tool call to stderr."""
    if isinstance(event.payload, OutputDelta):
        print("".join(part.text for part in event.payload.content), end="", flush=True)
    elif isinstance(event.payload, ToolCallEvent):
        call = event.payload.call
        note(f"tool> {call.name} {json.dumps(call.arguments, ensure_ascii=False)[:120]}")


class Assistant:
    """Runs one turn at a time against the home directory, on the main durable session or a throwaway one."""

    def __init__(self, home: Home, model: str) -> None:
        self.home = home
        self.model = model
        self.state = home.root.parent / ".state"
        self.session_file = self.state / "session_id"

    async def chat(self, text: str) -> str | None:
        """Answer the user on the main session, streaming the reply. None when the turn failed."""
        return await self.run(text, durable=True)

    async def heartbeat(self, prompt: str) -> str | None:
        """Answer a heartbeat on an ephemeral session, without printing. None when the turn failed."""
        return await self.run(prompt, durable=False)

    def options(self) -> AgentOptions:
        policy = PathPolicy(self.home.root, frozenset({*(tool.name for tool in caller_tools(self.home)), "web_fetch"}))
        return AgentOptions(
            provider=PROVIDER,
            model=self.model,
            instructions=build_instructions(self.home, datetime.now()),
            tools=tools(self.home),
            approvals=approval_handler(policy),
            working_directory=self.home.root,
            sessions_directory=self.state / "sessions",
        )

    async def open_main(self, agent: Agent) -> Session:
        saved = self.session_file.read_text(encoding="utf-8").strip() if self.session_file.exists() else ""
        if saved:
            try:
                session = await agent.resume_session(saved)
            except AgentError as error:
                note(f"Could not resume session {saved} ({error.code}: {error.message}). Starting a new conversation.")
            else:
                if session.info.model != self.model:
                    await session.set_model(PROVIDER, self.model)
                return session
        session = await agent.create_session(SessionOptions(persistence="durable"))
        write_text(self.session_file, session.info.session_id)
        return session

    async def run(self, prompt: str, durable: bool) -> str | None:
        result: TurnResult | None = None
        try:
            async with await create_agent(self.options()) as agent:
                if durable:
                    session = await self.open_main(agent)
                else:
                    session = await agent.create_session(SessionOptions(persistence="ephemeral"))
                async with session:
                    turn = await session.start_turn(TurnInput(content=[TextPart(prompt)]))
                    async for event in turn.events():
                        if durable:
                            print_event(event)
                        if event.type == "terminal" and isinstance(event.payload, TurnResult):
                            result = event.payload
        except AgentError as error:
            note(f"error> {error.code}: {error.message} {error.remedy}")
            return None
        finally:
            if durable:
                print(flush=True)
        if result is None:
            note("error> the turn ended without a terminal event")
            return None
        if result.state != "success":
            detail = f"{result.error.code}: {result.error.message} {result.error.remedy}" if result.error else ""
            note(f"error> turn {result.state}. {detail}".rstrip())
            return None
        return "".join(part.text for part in result.content or [])


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")

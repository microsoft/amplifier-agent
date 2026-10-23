"""Caller tools: `Tool` declarations with JSON Schema inputs and async handlers that write the home directory.

`remember` and `forget` edit MEMORY.md, and `set_reminder` adds to reminders.json. A handler that raises `ToolFailed`
sends its message back to the model, which can correct the call. Built-in tools are named by string in `BUILT_INS`.
"""

from datetime import datetime

from amplifier_agent import Tool, ToolContext, ToolFailed

from personal_assistant.memory import Home

READ_TOOLS = ("read_file", "glob", "grep")
BUILT_INS = (*READ_TOOLS, "web_fetch")

SCHEMA = "https://json-schema.org/draft/2020-12/schema"


def object_schema(properties: dict[str, dict], required: list[str]) -> dict:
    return {
        "$schema": SCHEMA,
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def parse_time(value: str, now: datetime) -> datetime:
    try:
        at = datetime.fromisoformat(value.strip())
    except ValueError:
        raise ToolFailed(
            f"at must be an ISO 8601 local date and time, such as {now:%Y-%m-%dT%H:%M}. Got: {value}"
        ) from None
    if at.tzinfo is not None:
        at = at.astimezone().replace(tzinfo=None)
    if at <= now:
        raise ToolFailed(f"{at:%Y-%m-%dT%H:%M} is in the past; it is now {now:%Y-%m-%dT%H:%M}. Choose a later time.")
    return at


def caller_tools(home: Home) -> list[Tool]:
    async def remember(arguments: dict, context: ToolContext) -> str:
        fact = str(arguments["fact"])
        if not fact.strip():
            raise ToolFailed("fact is empty. Pass the fact to save as one short sentence.")
        return "Saved to MEMORY.md." if home.remember(fact) else "MEMORY.md already has this fact."

    async def forget(arguments: dict, context: ToolContext) -> str:
        match = str(arguments["match"])
        removed = home.forget(match)
        if not removed:
            raise ToolFailed(
                f"No line in MEMORY.md contains {match!r}. Read MEMORY.md and pass text that appears in the line to "
                "remove."
            )
        return "Removed from MEMORY.md:\n" + "\n".join(removed)

    async def set_reminder(arguments: dict, context: ToolContext) -> str:
        at = parse_time(str(arguments["at"]), datetime.now())
        reminder = home.add_reminder(at, " ".join(str(arguments["text"]).split()))
        return f"Reminder set for {reminder.at:%A %Y-%m-%d %H:%M}: {reminder.text}"

    return [
        Tool(
            name="remember",
            description="Save one lasting fact about the user to MEMORY.md as a bullet.",
            input_schema=object_schema({"fact": {"type": "string", "description": "one short sentence"}}, ["fact"]),
            handler=remember,
        ),
        Tool(
            name="forget",
            description="Remove every MEMORY.md bullet that contains the given text, ignoring case.",
            input_schema=object_schema({"match": {"type": "string"}}, ["match"]),
            handler=forget,
        ),
        Tool(
            name="set_reminder",
            description="Remind the user of something at a later local date and time.",
            input_schema=object_schema(
                {
                    "at": {"type": "string", "description": "ISO 8601 local date and time, such as 2026-10-23T17:30"},
                    "text": {"type": "string", "description": "what to remind the user of"},
                },
                ["at", "text"],
            ),
            handler=set_reminder,
        ),
    ]


def tools(home: Home) -> list[Tool | str]:
    return [*caller_tools(home), *BUILT_INS]

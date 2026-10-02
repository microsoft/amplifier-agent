"""A caller tool, lookup_token, that returns a fixed token."""

from amplifier_agent import Tool

TOKEN = "tok_7c2e9f41b8d3"
_calls: list[dict] = []


async def _lookup_token(arguments: dict, context) -> str:
    _calls.append({"call_id": context.call_id, "arguments": arguments})
    return TOKEN


def tools() -> list[Tool]:
    return [
        Tool(
            name="lookup_token",
            description="Look up the current access token. Takes no arguments.",
            input_schema={
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            handler=_lookup_token,
        )
    ]


def approvals():
    return None


def record() -> dict:
    return {"calls": _calls, "token": TOKEN}

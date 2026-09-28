"""Validate the pinned chat-completions fields and preserve message boundaries."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, cast

from amplifier_agent import ConversationMessage, TextPart, TurnInput, Usage


@dataclass
class InvalidRequestError(Exception):
    field: str
    remedy: str


def _object(value: Any, field: str, allowed: set[str], required: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InvalidRequestError(field, f"Supply {field} as an object.")
    extra = value.keys() - allowed
    if extra:
        key = sorted(extra)[0]
        path = key if field == "request" else f"{field}.{key}"
        raise InvalidRequestError(path, f"Remove {path} and configure the agent at server startup.")
    missing = required - value.keys()
    if missing:
        key = sorted(missing)[0]
        raise InvalidRequestError(f"{field}.{key}", f"Supply the required {key} field.")
    return value


def project_request(body: Any) -> tuple[str, bool, TurnInput]:
    request = _object(body, "request", {"model", "messages", "stream", "stream_options"}, {"model", "messages"})
    model = request["model"]
    if not isinstance(model, str) or not model:
        raise InvalidRequestError("model", "Select a nonempty model name from /v1/models.")
    stream = request.get("stream", False)
    if not isinstance(stream, bool):
        raise InvalidRequestError("stream", "Supply stream as a boolean.")
    if "stream_options" in request:
        options = _object(request["stream_options"], "stream_options", {"include_usage"}, set())
        if not stream:
            raise InvalidRequestError("stream_options", "Remove stream_options or set stream to true.")
        if "include_usage" in options and not isinstance(options["include_usage"], bool):
            raise InvalidRequestError("stream_options.include_usage", "Supply include_usage as a boolean.")
    messages = request["messages"]
    if not isinstance(messages, list) or not messages:
        raise InvalidRequestError("messages", "Supply a nonempty array of conversation messages.")
    history = []
    for index, value in enumerate(messages):
        path = f"messages[{index}]"
        message = _object(value, path, {"role", "content"}, {"role", "content"})
        role = message["role"]
        if role not in ("system", "developer", "user", "assistant"):
            raise InvalidRequestError(f"{path}.role", "Use system, developer, user, or assistant with text content.")
        content = message["content"]
        if isinstance(content, str):
            parts = [TextPart(text=content)]
        elif isinstance(content, list):
            parts = []
            for part_index, value in enumerate(content):
                part_path = f"{path}.content[{part_index}]"
                part = _object(value, part_path, {"type", "text"}, {"type", "text"})
                if part["type"] != "text" or not isinstance(part["text"], str):
                    raise InvalidRequestError(part_path, "Supply text parts with type 'text' and string text.")
                parts.append(TextPart(text=part["text"]))
        else:
            raise InvalidRequestError(f"{path}.content", "Supply a string or an array of text parts.")
        history.append(
            ConversationMessage(role=cast(Literal["system", "developer", "user", "assistant"], role), content=parts)
        )
    return model, stream, TurnInput(content=[], history=history)


def error_body(code: str, category: str, message: str, remedy: str, param: str | None = None) -> dict[str, Any]:
    return {"error": {"message": f"{message} {remedy}", "type": category, "code": code, "param": param}}


def project_usage(usage: Usage | None) -> dict[str, Any] | None:
    """Sum a turn's usage into one chat-completions usage object, or None when token counts are unknown.

    `tokens_in` already includes cache reads, so only cache writes are added to prompt tokens.
    """
    entries = usage.entries if usage is not None else []
    if not entries or any(entry.tokens_in is None or entry.tokens_out is None for entry in entries):
        return None
    prompt = sum((entry.tokens_in or 0) + (entry.cache_write_tokens or 0) for entry in entries)
    completion = sum(entry.tokens_out or 0 for entry in entries)
    projected: dict[str, Any] = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }
    cached = [entry.cache_read_tokens for entry in entries]
    if all(value is not None for value in cached):
        projected["prompt_tokens_details"] = {"cached_tokens": sum(value or 0 for value in cached)}
    costs = [(entry.cost or {}).get("USD") for entry in entries]
    if all(value is not None for value in costs):
        projected["cost_usd"] = format(sum((Decimal(value or 0) for value in costs), Decimal(0)), "f")
    return projected

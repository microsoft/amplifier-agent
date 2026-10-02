"""Validate the pinned chat-completions fields and preserve message boundaries."""

from dataclasses import dataclass
from decimal import Decimal
import re
from typing import Any, Literal, cast

from amplifier_agent import ContentPart, ConversationMessage, ImagePart, TextPart, TurnInput, Usage

# The image travels inline; media type and base64 validity are judged by the agent.
_DATA_URL = re.compile(r"data:(?P<media_type>[^;,]+);base64,(?P<data>.*)", re.DOTALL)
_DETAIL = ("auto", "low", "high")
_PART_REMEDY = "Supply text parts, or image_url parts with base64 data URLs in user messages."
_HISTORY_FIELD = re.compile(
    r"input\.history\[(?P<message>\d+)\](?:\.content(?:\[(?P<part>\d+)\](?P<rest>\..*)?)?|(?P<role>\.role))?"
)


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


def _image(value: Any, field: str) -> ImagePart:
    image_url = _object(value, field, {"url", "detail"}, {"url"})
    url = image_url["url"]
    match = _DATA_URL.fullmatch(url) if isinstance(url, str) else None
    if match is None:
        raise InvalidRequestError(
            f"{field}.url",
            "Supply the image inline as a data URL, data:<media type>;base64,<data>; remote URLs are never fetched.",
        )
    if "detail" in image_url and image_url["detail"] not in _DETAIL:
        raise InvalidRequestError(f"{field}.detail", f"Use one of {', '.join(_DETAIL)} for detail, or omit it.")
    return ImagePart(media_type=match["media_type"], data=match["data"])


def _part(value: Any, field: str, role: str) -> ContentPart:
    kind = value.get("type") if isinstance(value, dict) else None
    if kind == "image_url":
        if role != "user":
            raise InvalidRequestError(field, "Move the image into a user message; images are accepted only there.")
        part = _object(value, field, {"type", "image_url"}, {"type", "image_url"})
        return _image(part["image_url"], f"{field}.image_url")
    if kind != "text":
        raise InvalidRequestError(field, _PART_REMEDY)
    part = _object(value, field, {"type", "text"}, {"type", "text"})
    if not isinstance(part["text"], str):
        raise InvalidRequestError(field, "Supply text parts with type 'text' and string text.")
    return TextPart(text=part["text"])


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
        parts: list[ContentPart]
        if isinstance(content, str):
            parts = [TextPart(text=content)]
        elif isinstance(content, list):
            parts = [_part(item, f"{path}.content[{position}]", role) for position, item in enumerate(content)]
        else:
            raise InvalidRequestError(f"{path}.content", "Supply a string or an array of content parts.")
        history.append(
            ConversationMessage(role=cast(Literal["system", "developer", "user", "assistant"], role), content=parts)
        )
    return model, stream, TurnInput(content=[], history=history)


def request_param(field: Any, body: Any) -> str | None:
    """The request field an agent input field names, for a request project_request accepted."""
    match = _HISTORY_FIELD.fullmatch(field) if isinstance(field, str) else None
    if match is None:
        return None
    index = int(match["message"])
    path = f"messages[{index}]"
    if match["part"] is None:
        if match["role"]:
            return f"{path}.role"
        return f"{path}.content" if match[0].endswith(".content") else path
    if isinstance(body["messages"][index]["content"], str):
        return f"{path}.content"
    path = f"{path}.content[{match['part']}]"
    # An image part's media type and data both come from its data URL.
    return f"{path}.image_url.url" if match["rest"] in (".media_type", ".data") else path


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

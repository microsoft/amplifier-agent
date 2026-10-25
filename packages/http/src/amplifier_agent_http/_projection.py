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
_CONTENT_FIELD = re.compile(r"input\.content(?:\[(?P<part>\d+)\](?P<rest>\..*)?)?")
# agent-interface.v1, Identity: caller-supplied session ids.
_SESSION_ID = re.compile(r"[a-z0-9][a-z0-9-]{7,63}")
SESSION_HEADER = "Amplifier-Session-Id"
CONTEXT_FIELD = "com.microsoft.amplifier.context"


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
    """The model name, whether to stream, and the ephemeral turn input: every message as history."""
    request = _object(
        body, "request", {"model", "messages", "stream", "stream_options", "reasoning_effort"}, {"model", "messages"}
    )
    _model(request)
    stream = request.get("stream", False)
    if not isinstance(stream, bool):
        raise InvalidRequestError("stream", "Supply stream as a boolean.")
    if "stream_options" in request:
        options = _object(request["stream_options"], "stream_options", {"include_usage"}, set())
        if not stream:
            raise InvalidRequestError("stream_options", "Remove stream_options or set stream to true.")
        if "include_usage" in options and not isinstance(options["include_usage"], bool):
            raise InvalidRequestError("stream_options.include_usage", "Supply include_usage as a boolean.")
    model, turn_input = _conversation(request)
    return model, stream, turn_input


def project_turn_request(body: Any) -> tuple[str, bool, TurnInput]:
    """The model name, whether approvals are asked, and the ephemeral turn input of a turns request."""
    if isinstance(body, dict):
        for field in ("stream", "stream_options"):
            if field in body:
                raise InvalidRequestError(field, f"Remove {field}; a turns response always streams.")
    request = _object(body, "request", {"model", "messages", "reasoning_effort", "approvals"}, {"model", "messages"})
    if "approvals" in request and request["approvals"] != "ask":
        raise InvalidRequestError(
            "approvals", "Set approvals to 'ask', or omit it to let the server's approval policy decide."
        )
    model, turn_input = _conversation(request)
    return model, "approvals" in request, turn_input


def _model(request: dict[str, Any]) -> str:
    model = request["model"]
    if not isinstance(model, str) or not model:
        raise InvalidRequestError("model", "Select a nonempty model name from /v1/models.")
    return model


def _conversation(request: dict[str, Any]) -> tuple[str, TurnInput]:
    """The model name and the ephemeral turn input of a request whose top-level fields are checked."""
    model = _model(request)
    effort = request.get("reasoning_effort")
    if "reasoning_effort" in request and not isinstance(effort, str):
        raise InvalidRequestError(
            "reasoning_effort", "Supply reasoning_effort as a string the selected model lists in /v1/models."
        )
    messages = request["messages"]
    if not isinstance(messages, list) or not messages:
        raise InvalidRequestError("messages", "Supply a nonempty array of conversation messages.")
    history = []
    for index, value in enumerate(messages):
        path = f"messages[{index}]"
        # http-face.v1 section 6: an assistant message may carry back the reasoning this face returned, unused.
        echoed = {"reasoning_content"} if isinstance(value, dict) and value.get("role") == "assistant" else set()
        message = _object(value, path, {"role", "content", *echoed}, {"role", "content"})
        role = message["role"]
        if "reasoning_content" in message and not isinstance(message["reasoning_content"], str):
            raise InvalidRequestError(
                f"{path}.reasoning_content",
                "Supply reasoning_content as a string, as this face returned it, or omit it.",
            )
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
    return model, TurnInput(content=[], history=history, reasoning_effort=effort)


def selection(model: str, alias: str) -> tuple[str, str] | None:
    """The provider and model `model` names, None for the configured agent's own.

    Raises ValueError for a name that is neither the alias nor `<provider>/<model>`.
    """
    if model == alias:
        return None
    provider, _, name = model.partition("/")
    if not provider or not name:
        raise ValueError(model)
    return provider, name


def session_id(value: str | None) -> str | None:
    """The session id the session header carries, or None without the header."""
    if value is None:
        return None
    if not _SESSION_ID.fullmatch(value):
        raise InvalidRequestError(
            SESSION_HEADER,
            f"Supply {SESSION_HEADER} as 8 to 64 lowercase letters, digits, or hyphens, starting with a letter or digit.",
        )
    return value


def continuation(turn_input: TurnInput) -> TurnInput:
    """The input that continues a server-held session: the final user message as content.

    Raises InvalidRequestError when the final message is not a user message.
    """
    history = turn_input.history or []
    last = len(history) - 1
    if history[last].role != "user":
        raise InvalidRequestError(
            f"messages[{last}].role", f"End the conversation with a user message when sending {SESSION_HEADER}."
        )
    return TurnInput(content=history[last].content, reasoning_effort=turn_input.reasoning_effort)


def user_messages(turn_input: TurnInput) -> int:
    return sum(message.role == "user" for message in turn_input.history or [])


def request_param(field: Any, body: Any, content_message: int | None = None) -> str | None:
    """The request field an agent input field names, for a request project_request accepted.

    `content_message` is the index of the message sent as `TurnInput.content`, if any.
    """
    if field == "input.reasoning_effort":
        return "reasoning_effort"
    if not isinstance(field, str):
        return None
    match = _HISTORY_FIELD.fullmatch(field)
    if match is not None:
        index = int(match["message"])
        if match["part"] is None and match["role"]:
            return f"messages[{index}].role"
        whole = match["part"] is None and not match[0].endswith(".content")
    else:
        match = _CONTENT_FIELD.fullmatch(field)
        if match is None or content_message is None:
            return None
        index, whole = content_message, False
    path = f"messages[{index}]"
    if whole:
        return path
    if match["part"] is None or isinstance(body["messages"][index]["content"], str):
        return f"{path}.content"
    path = f"{path}.content[{match['part']}]"
    # An image part's media type and data both come from its data URL.
    return f"{path}.image_url.url" if match["rest"] in (".media_type", ".data") else path


def error_body(code: str, category: str, message: str, remedy: str, param: str | None = None) -> dict[str, Any]:
    return {"error": {"message": f"{message} {remedy}", "type": category, "code": code, "param": param}}


def context_size(data: Any) -> dict[str, int] | None:
    """The `{tokens, budget}` a `context_size` progress payload carries, or None for any other payload."""
    size = data.get("context_size") if isinstance(data, dict) else None
    if not isinstance(size, dict):
        return None
    return {
        name: value
        for name, least in (("tokens", 0), ("budget", 1))
        if type(value := size.get(name)) is int and value >= least
    }


def project_usage(usage: Usage | None, context: dict[str, int] | None = None) -> dict[str, Any] | None:
    """Sum a turn's usage into one chat-completions usage object, or None when token counts are unknown.

    `tokens_in` already includes cache reads, so only cache writes are added to prompt tokens.
    `context` is the turn's last context size, carried as an extension field when there is one.
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
    details = {}
    for name, field in (("cached_tokens", "cache_read_tokens"), ("cache_write_tokens", "cache_write_tokens")):
        counts = [getattr(entry, field) for entry in entries]
        if all(value is not None for value in counts):
            details[name] = sum(value or 0 for value in counts)
    if details:
        projected["prompt_tokens_details"] = details
    costs = [(entry.cost or {}).get("USD") for entry in entries]
    if all(value is not None for value in costs):
        projected["cost_usd"] = format(sum((Decimal(value or 0) for value in costs), Decimal(0)), "f")
    if context is not None:
        projected[CONTEXT_FIELD] = dict(context)
    return projected

"""Validate the pinned chat-completions fields and preserve message boundaries."""

from dataclasses import dataclass
from typing import Any, Literal, cast

from amplifier_agent import ConversationMessage, TextPart, TurnInput


@dataclass
class InvalidRequest(Exception):
    field: str
    remedy: str


def _object(value: Any, field: str, allowed: set[str], required: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InvalidRequest(field, f"Supply {field} as an object.")
    extra = value.keys() - allowed
    if extra:
        key = sorted(extra)[0]
        path = key if field == "request" else f"{field}.{key}"
        raise InvalidRequest(path, f"Remove {path} and configure the agent at server startup.")
    missing = required - value.keys()
    if missing:
        key = sorted(missing)[0]
        raise InvalidRequest(f"{field}.{key}", f"Supply the required {key} field.")
    return value


def project_request(body: Any) -> tuple[str, bool, TurnInput]:
    request = _object(
        body, "request", {"model", "messages", "stream", "stream_options"}, {"model", "messages"}
    )
    model = request["model"]
    if not isinstance(model, str) or not model:
        raise InvalidRequest("model", "Select a nonempty model name from /v1/models.")
    stream = request.get("stream", False)
    if not isinstance(stream, bool):
        raise InvalidRequest("stream", "Supply stream as a boolean.")
    if "stream_options" in request:
        options = _object(request["stream_options"], "stream_options", {"include_usage"}, set())
        if not stream:
            raise InvalidRequest("stream_options", "Remove stream_options or set stream to true.")
        if "include_usage" in options and options["include_usage"] is not False:
            raise InvalidRequest(
                "stream_options.include_usage",
                "Omit include_usage or set it to false; use a binding for usage.",
            )
    messages = request["messages"]
    if not isinstance(messages, list) or not messages:
        raise InvalidRequest("messages", "Supply a nonempty array of conversation messages.")
    history = []
    for index, value in enumerate(messages):
        path = f"messages[{index}]"
        message = _object(value, path, {"role", "content"}, {"role", "content"})
        role = message["role"]
        if role not in ("system", "developer", "user", "assistant"):
            raise InvalidRequest(
                f"{path}.role", "Use system, developer, user, or assistant with text content."
            )
        content = message["content"]
        if isinstance(content, str):
            parts = [TextPart(text=content)]
        elif isinstance(content, list):
            parts = []
            for part_index, value in enumerate(content):
                part_path = f"{path}.content[{part_index}]"
                part = _object(value, part_path, {"type", "text"}, {"type", "text"})
                if part["type"] != "text" or not isinstance(part["text"], str):
                    raise InvalidRequest(
                        part_path, "Supply text parts with type 'text' and string text."
                    )
                parts.append(TextPart(text=part["text"]))
        else:
            raise InvalidRequest(f"{path}.content", "Supply a string or an array of text parts.")
        history.append(
            ConversationMessage(
                role=cast(Literal["system", "developer", "user", "assistant"], role), content=parts
            )
        )
    return model, stream, TurnInput(content=[], history=history)


def error_body(
    code: str, category: str, message: str, remedy: str, param: str | None = None
) -> dict[str, Any]:
    return {
        "error": {"message": f"{message} {remedy}", "type": category, "code": code, "param": param}
    }

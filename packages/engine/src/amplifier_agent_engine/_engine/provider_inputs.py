"""Preserve supplied conversation roles, text parts, and image parts during native conversion."""

from __future__ import annotations

from collections.abc import Callable
import copy
import json
from typing import Any
import uuid

from amplifier_agent_engine._records import AgentError

ROLE = "agent_conversation_role"


def context_text(role: str, parts: list[dict[str, str]]) -> str:
    return json.dumps(
        {"conversation_context": {"role": role, "content": parts}},
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )


def text_parts(content: Any) -> list[dict[str, str]] | None:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    result = []
    for part in content:
        item = part if isinstance(part, dict) else part.model_dump()
        if item.get("type") != "text":
            return None
        result.append({"type": "text", "text": item["text"]})
    return result


def conversation_parts(content: Any) -> list[dict[str, Any]] | None:
    """Text and base64 image blocks in order, or None when content holds any other block."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    result: list[dict[str, Any]] = []
    for part in content:
        item = part if isinstance(part, dict) else part.model_dump()
        source = item.get("source")
        if item.get("type") == "text":
            result.append({"type": "text", "text": item["text"]})
        elif item.get("type") == "image" and isinstance(source, dict) and source.get("type") == "base64":
            result.append({"type": "image", "media_type": source["media_type"], "data": source["data"]})
        else:
            return None
    return result


def wire_tool_content(content: Any) -> Any:
    """Anthropic tool result content with text and base64 image blocks in their wire shape.

    The provider forwards dumped core blocks as they are, and the API refuses their
    extra fields; other blocks and string content pass unchanged.
    """
    if not isinstance(content, list):
        return content
    result = []
    for block in content:
        source = block.get("source") if isinstance(block, dict) else None
        if isinstance(block, dict) and block.get("type") == "text":
            result.append({"type": "text", "text": block.get("text")})
        elif isinstance(block, dict) and block.get("type") == "image" and isinstance(source, dict):
            result.append(
                {
                    "type": "image",
                    "source": {
                        "type": source.get("type"),
                        "media_type": source.get("media_type"),
                        "data": source.get("data"),
                    },
                }
            )
        else:
            result.append(block)
    return result


def data_url(part: dict[str, Any]) -> str:
    return f"data:{part['media_type']};base64,{part['data']}"


def preserve_context(request: Any, *, native_roles: bool) -> Any:
    adapted = request.model_copy(deep=True)
    for message in adapted.messages:
        if message.role not in {"system", "developer"}:
            continue
        # Configured instructions are strings; imported conversation has explicit parts.
        if message.role == "system" and isinstance(message.content, str):
            continue
        parts = text_parts(message.content)
        if parts is None:
            raise ValueError("Conversation instructions must contain only text parts.")
        role = message.role
        message.role = "user"
        if native_roles:
            message.metadata = {**(message.metadata or {}), ROLE: role}
        else:
            message.content = context_text(role, parts)
    return adapted


def response_roles(messages: list[dict[str, Any]], convert: Callable[..., Any]) -> Any:
    adapted = copy.deepcopy(messages)
    replacements = {}
    for message in adapted:
        imported_role = (message.get("metadata") or {}).get(ROLE)
        role = imported_role or message.get("role")
        if role in {"system", "developer"} and imported_role is None:
            continue
        parts = conversation_parts(message["content"])
        if role not in {"system", "developer", "user", "assistant"} or parts is None or message.get("tool_calls"):
            continue
        if role != "user" and any(part["type"] == "image" for part in parts):
            continue
        key = str(uuid.uuid4())
        replacements[key] = {
            "role": role,
            "content": [
                {"type": "input_image", "image_url": data_url(part)}
                if part["type"] == "image"
                else {"type": "output_text" if role == "assistant" else "input_text", "text": part["text"]}
                for part in parts
            ],
        }
        message["role"] = "user"
        message["content"] = key
    result = convert(adapted)
    restored = dict.fromkeys(replacements, 0)
    for index, item in enumerate(result):
        content = item.get("content")
        if item.get("role") != "user":
            continue
        key = (
            content
            if isinstance(content, str)
            else (content[0].get("text") if isinstance(content, list) and len(content) == 1 else None)
        )
        replacement = replacements.get(key) if isinstance(key, str) else None
        if isinstance(key, str) and replacement is not None:
            result[index] = replacement
            restored[key] += 1
    encoded = json.dumps(result, allow_nan=False)
    if any(count != 1 for count in restored.values()) or any(key in encoded for key in replacements):
        raise AgentError(
            "provider_failed",
            "provider",
            "The provider adapter could not preserve the supplied conversation.",
            "Use a provider with compatible conversation support or update the engine.",
        )
    return result

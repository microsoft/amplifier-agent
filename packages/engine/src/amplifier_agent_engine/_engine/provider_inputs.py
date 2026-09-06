"""Preserve supplied conversation roles and text parts during native conversion."""

from __future__ import annotations

import copy
import json
import uuid
from typing import Any, Callable

from .._records import AgentError

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
        parts = text_parts(message["content"])
        if (
            role not in {"system", "developer", "user", "assistant"}
            or parts is None
            or message.get("tool_calls")
        ):
            continue
        key = str(uuid.uuid4())
        replacements[key] = {
            "role": role,
            "content": [
                {
                    "type": "output_text" if role == "assistant" else "input_text",
                    "text": part["text"],
                }
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
            else (
                content[0].get("text") if isinstance(content, list) and len(content) == 1 else None
            )
        )
        replacement = replacements.get(key)
        if replacement is not None:
            result[index] = replacement
            restored[key] += 1
    encoded = json.dumps(result, allow_nan=False)
    if any(count != 1 for count in restored.values()) or any(
        key in encoded for key in replacements
    ):
        raise AgentError(
            "provider_failed",
            "provider",
            "The provider adapter could not preserve the supplied conversation.",
            "Use a provider with compatible conversation support or update the engine.",
        )
    return result

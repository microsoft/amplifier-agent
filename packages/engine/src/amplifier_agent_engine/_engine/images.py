"""Image parts: conversation blocks, tool results, model capability evidence, and image_unsupported."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable, Iterable
import re
from typing import Any

from amplifier_core.llm_errors import InvalidRequestError

from amplifier_agent_engine._engine.configuration import image_part, invalid, record
from amplifier_agent_engine._records import AgentError, ContentPart, ImagePart, TextPart, TurnInput, TurnRecord

# A model listing is network work; past this bound the provider reports nothing.
LISTING_SECONDS = 15.0

# Provider error phrasings meaning the model or endpoint takes no image input, from
# OpenAI and Azure, vLLM, Ollama, llama.cpp, LM Studio, and serde-validated
# OpenAI-compatible servers. Other refusals, such as an oversized image, are not here.
IMAGE_REFUSALS = tuple(
    re.compile(pattern, re.IGNORECASE | re.DOTALL)
    for pattern in (
        r"(?:does not|doesn't|do not|don't) support (?:images?|image inputs?|vision|multimodal)",
        r"(?:images?|image inputs?|vision|multimodal inputs?) (?:is|are) not supported",
        r"not (?:a )?multimodal",
        r"image_url is only supported",
        r"unknown variant `image_url`",
        r"(?=.*image).*(?:invalid|unsupported) content type",
        r"at most 0 image",
        r"missing data required for image input",
    )
)

TERMINAL_NOTE = (
    "The conversation now holds a one-line description in place of each image from this turn; "
    "images from earlier turns remain."
)


def content_block(part: ContentPart) -> dict[str, Any]:
    """The amplifier-core conversation block for one content part."""
    if isinstance(part, ImagePart):
        return {"type": "image", "source": {"type": "base64", "media_type": part.media_type, "data": part.data}}
    return {"type": "text", "text": part.text}


def holds_image(inputs: Iterable[TurnInput]) -> bool:
    """Whether any of these inputs carries an image part in its content or supplied history."""
    for value in inputs:
        messages = [value.content, *(message.content for message in value.history or [])]
        if any(isinstance(part, ImagePart) for content in messages for part in content):
            return True
    return False


def request_holds_image(request: Any) -> bool:
    """Whether a provider request carries an image block in any message."""
    for message in getattr(request, "messages", None) or []:
        content = message.get("content") if isinstance(message, dict) else getattr(message, "content", None)
        if not isinstance(content, list):
            continue
        for block in content:
            kind = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
            if kind == "image":
                return True
    return False


def rejects_request(error: BaseException) -> bool:
    """Whether a provider error is a non-retryable refusal of the request as sent."""
    if getattr(error, "retryable", False):
        return False
    return isinstance(error, InvalidRequestError) or getattr(error, "status_code", None) in {400, 415, 422}


def refuses_images(message: str) -> bool:
    """Whether a provider error message says the model or endpoint does not accept images."""
    return any(pattern.search(message) for pattern in IMAGE_REFUSALS)


def image_unsupported(
    provider: str,
    model: str,
    provider_message: str | None = None,
    problem: str | None = None,
    remedy: str | None = None,
) -> AgentError:
    details: dict[str, Any] = {"provider": provider, "model": model}
    if provider_message is not None:
        details["provider_message"] = provider_message
    return AgentError(
        "image_unsupported",
        "input",
        problem
        or (
            f"The selected model {model!r} of provider {provider!r} does not accept image input. "
            "The conversation holds at least one image part."
        ),
        remedy
        or (
            "Choose a model that accepts images, or start the turn without image parts. Images from earlier "
            "turns stay in the session; when they caused this, choose a model that accepts images."
        ),
        retryable=False,
        details=details,
    )


def provider_refusal(provider: str, model: str, provider_message: str) -> AgentError:
    return image_unsupported(
        provider,
        model,
        provider_message,
        remedy=f"Choose a model that accepts images, and send this turn's images again if still needed. {TERMINAL_NOTE}",
    )


def tool_result_unsupported(provider: str, model: str) -> AgentError:
    return image_unsupported(
        provider,
        model,
        problem=(
            f"The selected model {model!r} of provider {provider!r} does not accept images in tool results. "
            "A tool result in the conversation holds an image part."
        ),
        remedy=(
            "Choose a provider and model that accept images in tool results, or have the tool return text "
            f"only. {TERMINAL_NOTE}"
        ),
    )


def copilot_unsupported(model: str, terminal: bool = False) -> AgentError:
    remedy = (
        "Send images only in the input content of a fresh session's turn, or choose a provider that "
        "accepts images throughout the conversation."
    )
    return image_unsupported(
        "github-copilot",
        model,
        problem=(
            "Provider 'github-copilot' accepts images only in the newest user message of a turn. "
            "The conversation holds an image in supplied history, an earlier turn, or a tool result."
        ),
        remedy=f"{remedy} {TERMINAL_NOTE}" if terminal else remedy,
    )


def tool_result_images(provider: str, model: str) -> bool:
    """Whether the provider's own conversion carries a tool result's image parts to the model.

    Only these conversions place images in the native tool result. The others stringify,
    drop, flatten, or describe them, or have no native image form for a tool message.
    """
    if provider == "anthropic":
        from amplifier_module_provider_anthropic import AnthropicProvider

        return "vision" in AnthropicProvider._get_capabilities(model).capability_tags
    if provider in {"openai", "azure-openai"}:
        from amplifier_module_provider_openai._capabilities import get_capabilities

        # The conversion consults this same flag and otherwise replaces each image with a notice.
        return get_capabilities(model).supports_vision
    return False


def _field(message: Any, name: str) -> Any:
    return message.get(name) if isinstance(message, dict) else getattr(message, name, None)


def _message_holds_image(message: Any) -> bool:
    content = _field(message, "content")
    return isinstance(content, list) and any(_field(block, "type") == "image" for block in content)


def request_refusal(provider: str, model: str, request: Any) -> AgentError | None:
    """The image_unsupported failure for a request the provider would not carry intact, if any."""
    messages = list(getattr(request, "messages", None) or [])
    if not tool_result_images(provider, model) and any(
        _field(message, "role") == "tool" and _message_holds_image(message) for message in messages
    ):
        return tool_result_unsupported(provider, model)
    if provider == "github-copilot":
        # The Copilot SDK attaches images from the last user message only and replaces others with text.
        users = [index for index, message in enumerate(messages) if _field(message, "role") == "user"]
        newest = users[-1] if users else None
        if any(_message_holds_image(message) for index, message in enumerate(messages) if index != newest):
            return copilot_unsupported(model, terminal=True)
    return None


def conversation_inputs(history: list[TurnRecord]) -> list[TurnInput]:
    """The inputs of earlier turns whose images the conversation still holds.

    A turn that failed image_unsupported left only descriptions of its images.
    """
    return [
        record.input
        for record in history
        if record.result.error is None or record.result.error.code != "image_unsupported"
    ]


def copilot_drops_images(input: TurnInput, history: list[TurnRecord]) -> bool:
    """Whether an image sits anywhere but the turn's newest user message, known before the stream."""
    if holds_image(conversation_inputs(history)):
        return True
    messages = list(input.history or [])
    if not input.content and messages and messages[-1].role == "user":
        messages = messages[:-1]
    return any(isinstance(part, ImagePart) for message in messages for part in message.content)


def result_parts(value: list[Any]) -> list[ContentPart]:
    """Validate a tool's list result as text and image parts, failing tool_result_invalid."""
    for index, part in enumerate(value):
        path = f"result[{index}]"
        try:
            if isinstance(part, ImagePart):
                record(part, ImagePart, path)
                if part.type != "image":
                    raise invalid(f"{path}.type", "an ImagePart has type 'image'.", "Leave ImagePart.type unset.")
                image_part(part, path)
            elif isinstance(part, TextPart):
                record(part, TextPart, path)
                if part.type != "text" or not isinstance(part.text, str):
                    raise invalid(path, "expected a text part with string text.", "Use TextPart with a string text.")
            else:
                raise invalid(
                    path,
                    "unregistered content part.",
                    "Return a string, or a list of TextPart and ImagePart values, from the tool handler.",
                )
        except AgentError as error:
            raise AgentError(
                "tool_result_invalid",
                "executor",
                f"The tool returned a malformed content part. {error.message}",
                error.remedy,
                details=error.details,
            ) from None
    return list(value)


def image_line(media_type: str, data: str) -> str:
    """One line naming an image's media type and decoded size."""
    return f"[image: {media_type}, {len(base64.b64decode(data))} bytes]"


def describe(parts: list[ContentPart]) -> str:
    """The text of a list result, with each image as one line naming its media type and decoded size."""
    return "\n".join(
        part.text if isinstance(part, TextPart) else image_line(part.media_type, part.data) for part in parts
    )


def described_blocks(content: Any) -> Any:
    """Conversation content with each image block replaced by a text block of its description."""
    if not isinstance(content, list):
        return content
    result = []
    for block in content:
        source = _field(block, "source")
        if _field(block, "type") == "image" and isinstance(source, dict):
            result.append({"type": "text", "text": image_line(source["media_type"], source["data"])})
        else:
            result.append(block)
    return result


def result_blocks(parts: list[ContentPart]) -> list[dict[str, Any]]:
    """Conversation blocks for a list result; providers refuse empty text blocks."""
    return [content_block(part) for part in parts if not (isinstance(part, TextPart) and not part.text)]


async def reported_capabilities(provider: Any, model: str) -> frozenset[str] | None:
    """The capabilities the provider reports for ``model``, or None when it reports nothing.

    Providers with an installed capability table answer from it without network work.
    Others answer from their model listing; a failed listing, or one that omits the
    model, reports nothing.
    """
    table = _capability_table(provider)
    if table is not None:
        return table(model)
    list_models = getattr(provider, "list_models", None)
    if not callable(list_models):
        return None
    try:
        models = await asyncio.wait_for(list_models(), LISTING_SECONDS)
    except asyncio.CancelledError:
        raise
    except Exception:
        return None
    for info in models or []:
        if getattr(info, "id", None) == model:
            capabilities = getattr(info, "capabilities", None)
            return frozenset(capabilities) if isinstance(capabilities, list) else None
    return None


def _capability_table(provider: Any) -> Callable[[str], frozenset[str] | None] | None:
    from amplifier_module_provider_anthropic import AnthropicProvider
    from amplifier_module_provider_openai import OpenAIProvider

    if isinstance(provider, AnthropicProvider):
        # The table maps every Claude model id to a known family.
        return lambda model: frozenset(AnthropicProvider._get_capabilities(model).capability_tags)
    if isinstance(provider, OpenAIProvider):
        from amplifier_module_provider_openai._capabilities import get_capabilities

        def openai(model: str) -> frozenset[str] | None:
            capabilities = get_capabilities(model)
            # An unrecognized model id gets generic defaults, which are not evidence.
            return None if capabilities.family == "unknown" else frozenset(capabilities.capability_tags)

        return openai
    return None

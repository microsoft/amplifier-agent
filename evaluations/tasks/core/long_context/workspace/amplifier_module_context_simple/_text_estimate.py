"""Text-only size hints for structured messages, never image token counts.

Keep this small helper local: runtime modules must not depend on a host or on
another context/loop implementation. Image cost requires the selected provider.
Only actual content-block positions are inspected; quoted data and tool arguments
remain ordinary text. The original messages and image payloads are never changed.
"""

from typing import Any, NamedTuple


class TextEstimate(NamedTuple):
    tokens: int
    has_unmeasured_images: bool


def estimate_messages(messages: list[dict[str, Any]]) -> TextEstimate:
    tokens = 0
    has_images = False
    for message in messages:
        content = message.get("content")
        if isinstance(content, list):
            content, found = _content_without_image_payloads(content)
            message = {**message, "content": content}
            has_images |= found
        tokens += len(str(message)) // 4
    return TextEstimate(tokens, has_images)


def _content_without_image_payloads(content: list[Any]) -> tuple[list[Any], bool]:
    result = []
    has_images = False
    for block in content:
        if not isinstance(block, dict):
            # Core content blocks may remain typed in an in-memory view.
            if callable(getattr(block, "model_dump", None)):
                block = block.model_dump(exclude_none=True)
            else:
                result.append(block)
                continue
        kind = block.get("type")
        if kind in {"image", "image_url", "input_image"}:
            # Count the textual envelope, not encoded pixels or URL transport.
            # This is explicitly a partial estimate, not a zero-cost image.
            block = {
                k: v
                for k, v in block.items()
                if k not in {"source", "image_url", "url", "data", "file_id"}
            }
            has_images = True
        elif kind == "tool_result" and isinstance(block.get("content"), list):
            nested, found = _content_without_image_payloads(block["content"])
            block = {**block, "content": nested}
            has_images |= found
        result.append(block)
    return result, has_images

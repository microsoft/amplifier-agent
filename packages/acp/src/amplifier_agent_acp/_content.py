"""Prompt content blocks as turn input parts (acp-face.v1 section 10)."""

from acp.schema import (
    AudioContentBlock,
    BlobResourceContents,
    EmbeddedResourceContentBlock,
    ImageContentBlock,
    ResourceContentBlock,
    TextContentBlock,
)
from amplifier_agent import ContentPart, ImagePart, TextPart

from amplifier_agent_acp._errors import refusal

IMAGE_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp")

Block = TextContentBlock | ImageContentBlock | AudioContentBlock | ResourceContentBlock | EmbeddedResourceContentBlock

_ACCEPTED = "Send text, images (png, jpeg, gif, webp), embedded text or image resources, or resource links."


def _image(media_type: str | None, data: str, block: str) -> ImagePart:
    if media_type not in IMAGE_TYPES:
        raise refusal(
            f"The prompt's {block} block has media type '{media_type}', which this agent cannot read.", _ACCEPTED
        )
    return ImagePart(media_type, data)


def turn_content(blocks: list[Block]) -> list[ContentPart]:
    parts: list[ContentPart] = []
    for block in blocks:
        if isinstance(block, TextContentBlock):
            parts.append(TextPart(block.text))
        elif isinstance(block, ImageContentBlock):
            parts.append(_image(block.mime_type, block.data, "image"))
        elif isinstance(block, EmbeddedResourceContentBlock):
            resource = block.resource
            if isinstance(resource, BlobResourceContents):
                parts.append(_image(resource.mime_type, resource.blob, "resource"))
            else:
                parts.append(TextPart(f'<context ref="{resource.uri}">{resource.text}</context>'))
        elif isinstance(block, ResourceContentBlock):
            parts.append(TextPart(f"[{block.name}]({block.uri})"))
        else:
            raise refusal(f"The prompt has a {block.type} block, which this agent cannot read.", _ACCEPTED)
    return parts

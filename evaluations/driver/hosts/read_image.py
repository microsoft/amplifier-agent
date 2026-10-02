"""A caller tool, read_image, that returns an image file from the workspace as image content."""

import base64
from pathlib import Path

from amplifier_agent import ImagePart, TextPart, Tool

MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
_calls: list[dict] = []


async def _read_image(arguments: dict, context) -> list[TextPart | ImagePart]:
    _calls.append({"call_id": context.call_id, "arguments": arguments})
    path = Path.cwd() / arguments["path"]
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return [TextPart(f"Image {arguments['path']}:"), ImagePart(media_type=MEDIA_TYPES[path.suffix.lower()], data=data)]


def tools() -> list[Tool]:
    return [
        Tool(
            name="read_image",
            description="Read an image file from the workspace and show it to you. Takes the file's relative path.",
            input_schema={
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Path relative to the workspace."}},
                "required": ["path"],
                "additionalProperties": False,
            },
            handler=_read_image,
        )
    ]


def approvals():
    return None


def record() -> dict:
    return {"calls": _calls}

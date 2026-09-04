"""Serve controlled Anthropic responses over the provider HTTP protocol."""

import json

from starlette.applications import Starlette
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route


def anthropic_service(requests, failure=None, release=None):
    async def messages(request):
        body = await request.json()
        requests.append(body)
        assert request.headers.get("x-api-key") == "fixture-api-key"
        if failure:
            return JSONResponse(
                {
                    "type": "error",
                    "error": {
                        "type": "overloaded_error",
                        "message": "The fixture rejected this request.",
                    },
                },
                status_code=failure,
            )

        async def events():
            frames = [
                {
                    "type": "message_start",
                    "message": {
                        "id": "msg_fixture",
                        "type": "message",
                        "role": "assistant",
                        "content": [],
                        "model": "claude-sonnet-5",
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {
                            "input_tokens": 17,
                            "output_tokens": 0,
                            "cache_read_input_tokens": 3,
                            "cache_creation_input_tokens": 5,
                        },
                    },
                },
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                },
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "Wire "},
                },
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "reply"},
                },
                {"type": "content_block_stop", "index": 0},
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                    "usage": {"output_tokens": 2},
                },
                {"type": "message_stop"},
            ]
            for frame in frames:
                yield f"event: {frame['type']}\ndata: {json.dumps(frame)}\n\n"
                if release is not None and frame.get("delta", {}).get("text") == "Wire ":
                    await release.wait()

        return StreamingResponse(events(), media_type="text/event-stream")

    return Starlette(routes=[Route("/v1/messages", messages, methods=["POST"])])

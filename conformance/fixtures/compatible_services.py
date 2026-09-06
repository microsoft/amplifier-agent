"""Local protocol services for endpoint and subscription provider adapters."""

import json

from starlette.applications import Starlette
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from .provider_services import _has_result, _openai


def compatible_service(protocol, requests, *, tool=None, failure=None):
    async def complete(request):
        body = await request.json()
        requests.append(body)
        if failure:
            return JSONResponse(
                {"error": {"message": "The fixture rejected this request.", "type": "api_error"}},
                status_code=failure,
            )
        model = body.get("model", "gpt-5")
        selected_tool = (
            tool
            if tool and not _has_result(body) and '"role": "tool"' not in json.dumps(body)
            else None
        )

        async def events():
            if protocol in {"responses", "chatgpt"}:
                for frame in _openai(model, selected_tool, False, True, {"value": "fixture"}):
                    if protocol == "chatgpt" and frame["type"] == "response.completed":
                        frame["type"] = "response.done"
                    yield "event: " + frame["type"] + "\ndata: " + json.dumps(frame) + "\n\n"
            elif protocol == "chat":
                base = {
                    "id": "chat_fixture",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": model,
                }
                if selected_tool:
                    delta = {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_fixture",
                                "type": "function",
                                "function": {
                                    "name": selected_tool,
                                    "arguments": '{"value":"fixture"}',
                                },
                            }
                        ],
                    }
                    yield (
                        "data: "
                        + json.dumps(
                            {
                                **base,
                                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
                            }
                        )
                        + "\n\n"
                    )
                else:
                    for text in ("Wire ", "reply"):
                        yield (
                            "data: "
                            + json.dumps(
                                {
                                    **base,
                                    "choices": [
                                        {
                                            "index": 0,
                                            "delta": {"content": text},
                                            "finish_reason": None,
                                        }
                                    ],
                                }
                            )
                            + "\n\n"
                        )
                yield (
                    "data: "
                    + json.dumps(
                        {
                            **base,
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {},
                                    "finish_reason": "tool_calls" if selected_tool else "stop",
                                }
                            ],
                            "usage": {
                                "prompt_tokens": 20,
                                "completion_tokens": 2,
                                "total_tokens": 22,
                            },
                        }
                    )
                    + "\n\n"
                )
                yield "data: [DONE]\n\n"
            else:
                if selected_tool:
                    message = {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {"function": {"name": selected_tool, "arguments": {"value": "fixture"}}}
                        ],
                    }
                    yield json.dumps({"model": model, "message": message, "done": False}) + "\n"
                else:
                    for text in ("Wire ", "reply"):
                        yield (
                            json.dumps(
                                {
                                    "model": model,
                                    "message": {"role": "assistant", "content": text},
                                    "done": False,
                                }
                            )
                            + "\n"
                        )
                yield (
                    json.dumps(
                        {
                            "model": model,
                            "message": {"role": "assistant", "content": ""},
                            "done": True,
                            "done_reason": "stop",
                            "prompt_eval_count": 20,
                            "eval_count": 2,
                        }
                    )
                    + "\n"
                )

        return StreamingResponse(
            events(),
            media_type="application/x-ndjson" if protocol == "ollama" else "text/event-stream",
        )

    async def show(request):
        return JSONResponse(
            {
                "model_info": {"general.architecture": "llama", "llama.context_length": 32768},
                "capabilities": ["completion", "tools"],
            }
        )

    async def models(request):
        return JSONResponse(
            {
                "models": [{"name": "fixture-model", "model": "fixture-model"}],
                "data": [{"id": "gpt-5", "object": "model"}],
            }
        )

    routes = [
        Route(path, complete, methods=["POST"])
        for path in (
            "/responses",
            "/v1/responses",
            "/openai/v1/responses",
            "/v1/chat/completions",
            "/api/chat",
        )
    ]
    routes += [
        Route("/api/show", show, methods=["POST"]),
        Route("/api/tags", models),
        Route("/v1/models", models),
    ]
    return Starlette(routes=routes)

"""Controlled provider protocols with observable input and streaming barriers."""

from __future__ import annotations

import json

from starlette.applications import Starlette
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

MODELS = {"anthropic": "claude-sonnet-5", "openai": "gpt-5", "gemini": "gemini-2.5-flash"}
KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "gemini": "GOOGLE_API_KEY"}
URL_ENV = {
    "anthropic": "ANTHROPIC_BASE_URL",
    "openai": "OPENAI_BASE_URL",
    "gemini": "GOOGLE_GEMINI_BASE_URL",
}


def _has_result(body):
    encoded = json.dumps(body)
    return any(
        word in encoded
        for word in ('"tool_result"', '"function_call_output"', '"functionResponse"')
    )


def provider_service(
    provider,
    requests,
    *,
    release=None,
    failure=None,
    tool=None,
    reasoning=False,
    usage=True,
    request_event=None,
    tool_arguments=None,
    late_signature=False,
    partial_failure=False,
    reported_model=None,
    request_validator=None,
    frame_transform=None,
):
    async def complete(request):
        body = await request.json()
        requests.append(body)
        if request_event is not None:
            request_event.set()
        if request_validator is not None and (problem := request_validator(body)):
            return JSONResponse(
                {"error": {"message": problem, "type": "invalid_request_error"}}, status_code=400
            )
        if provider == "anthropic":
            assert request.headers.get("x-api-key") == "fixture-api-key"
        elif provider == "openai":
            assert request.headers.get("authorization") == "Bearer fixture-api-key"
        else:
            assert request.headers.get("x-goog-api-key") == "fixture-api-key"
            for message in body.get("contents", []):
                for part in message.get("parts", []):
                    if "functionCall" in part and not part.get("thoughtSignature"):
                        return JSONResponse(
                            {
                                "error": {
                                    "code": 400,
                                    "status": "INVALID_ARGUMENT",
                                    "message": "Function call is missing a thought_signature.",
                                }
                            },
                            status_code=400,
                        )
        if failure:
            return JSONResponse(
                {
                    "error": {
                        "type": "api_error",
                        "message": "The fixture rejected this request.",
                        "code": failure,
                        "status": "UNAVAILABLE",
                    }
                },
                status_code=failure,
            )
        selected = body.get("model", request.path_params.get("model", MODELS[provider]))
        selected = selected.removesuffix(":streamGenerateContent").removesuffix(":generateContent")
        selected_tool = tool if tool and not _has_result(body) else None
        frames = {"anthropic": _anthropic, "openai": _openai, "gemini": _gemini}[provider](
            reported_model or selected,
            selected_tool,
            reasoning,
            usage,
            {"value": "fixture"} if tool_arguments is None else tool_arguments,
            late_signature=late_signature,
        )

        async def events():
            for frame in frames:
                if frame_transform is not None:
                    frame = frame_transform(frame, len(requests))
                prefix = f"event: {frame['type']}\n" if "type" in frame else ""
                yield prefix + "data: " + json.dumps(frame) + "\n\n"
                if partial_failure and "Wire " in json.dumps(frame):
                    return
                if (
                    release is not None
                    and "Wire " in json.dumps(frame)
                    and "reply" not in json.dumps(frame)
                ):
                    await release.wait()

        return StreamingResponse(events(), media_type="text/event-stream")

    paths = {
        "anthropic": ["/v1/messages"],
        "openai": ["/responses", "/v1/responses"],
        "gemini": ["/v1beta/models/{model:path}", "/v1/models/{model:path}"],
    }[provider]
    return Starlette(routes=[Route(path, complete, methods=["POST"]) for path in paths])


def _anthropic(model, tool, reasoning, usage, arguments=None, late_signature=False):
    measured = {
        "input_tokens": 17,
        "output_tokens": 0,
        "cache_read_input_tokens": 3,
        "cache_creation_input_tokens": 5,
    }
    start = {
        "id": "msg_fixture",
        "type": "message",
        "role": "assistant",
        "content": [],
        "model": model,
        "stop_reason": None,
        "stop_sequence": None,
        "usage": measured if usage else {"input_tokens": 0, "output_tokens": 0},
    }
    yield {"type": "message_start", "message": start}
    index = 0
    if reasoning:
        yield {
            "type": "content_block_start",
            "index": index,
            "content_block": {"type": "thinking", "thinking": "", "signature": ""},
        }
        yield {
            "type": "content_block_delta",
            "index": index,
            "delta": {"type": "thinking_delta", "thinking": "Considering."},
        }
        yield {
            "type": "content_block_delta",
            "index": index,
            "delta": {"type": "signature_delta", "signature": "fixture-signature"},
        }
        yield {"type": "content_block_stop", "index": index}
        index += 1
    if tool:
        yield {
            "type": "content_block_start",
            "index": index,
            "content_block": {"type": "tool_use", "id": "call_fixture", "name": tool, "input": {}},
        }
        yield {
            "type": "content_block_delta",
            "index": index,
            "delta": {
                "type": "input_json_delta",
                "partial_json": json.dumps(arguments, allow_nan=False),
            },
        }
    else:
        yield {
            "type": "content_block_start",
            "index": index,
            "content_block": {"type": "text", "text": ""},
        }
        for text in ("Wire ", "reply"):
            yield {
                "type": "content_block_delta",
                "index": index,
                "delta": {"type": "text_delta", "text": text},
            }
    yield {"type": "content_block_stop", "index": index}
    yield {
        "type": "message_delta",
        "delta": {"stop_reason": "tool_use" if tool else "end_turn", "stop_sequence": None},
        "usage": {"output_tokens": 2},
    }
    yield {"type": "message_stop"}


def _openai(model, tool, reasoning, usage, arguments=None, late_signature=False):
    response = {
        "id": "resp_fixture",
        "object": "response",
        "created_at": 1,
        "model": model,
        "status": "in_progress",
        "output": [],
        "error": None,
        "incomplete_details": None,
        "parallel_tool_calls": True,
        "temperature": 1,
        "tool_choice": "auto",
        "tools": [],
        "top_p": 1,
        "store": False,
    }
    seq = 0

    def event(kind, **values):
        nonlocal seq
        seq += 1
        return {"type": kind, "sequence_number": seq, **values}

    yield event("response.created", response=response.copy())
    output = []
    if reasoning:
        item = {
            "id": "rs_fixture",
            "type": "reasoning",
            "summary": [{"type": "summary_text", "text": "Considering."}],
            "encrypted_content": "opaque-fixture-reasoning",
        }
        output.append(item)
        yield event("response.output_item.added", output_index=0, item={**item, "summary": []})
        yield event(
            "response.reasoning_summary_part.added",
            item_id=item["id"],
            output_index=0,
            summary_index=0,
            part={"type": "summary_text", "text": ""},
        )
        yield event(
            "response.reasoning_summary_text.delta",
            item_id=item["id"],
            output_index=0,
            summary_index=0,
            delta="Considering.",
        )
        yield event(
            "response.reasoning_summary_text.done",
            item_id=item["id"],
            output_index=0,
            summary_index=0,
            text="Considering.",
        )
        yield event("response.output_item.done", output_index=0, item=item)
    index = len(output)
    if tool:
        item = {
            "id": "fc_fixture",
            "type": "function_call",
            "call_id": "call_fixture",
            "name": tool,
            "arguments": json.dumps(arguments, allow_nan=False),
            "status": "completed",
        }
        yield event(
            "response.output_item.added",
            output_index=index,
            item={**item, "arguments": "", "status": "in_progress"},
        )
        yield event(
            "response.function_call_arguments.delta",
            item_id=item["id"],
            output_index=index,
            delta=item["arguments"],
        )
        yield event(
            "response.function_call_arguments.done",
            item_id=item["id"],
            output_index=index,
            arguments=item["arguments"],
        )
    else:
        item = {
            "id": "msg_fixture",
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": "Wire reply", "annotations": []}],
        }
        yield event(
            "response.output_item.added",
            output_index=index,
            item={**item, "content": [], "status": "in_progress"},
        )
        yield event(
            "response.content_part.added",
            item_id=item["id"],
            output_index=index,
            content_index=0,
            part={"type": "output_text", "text": "", "annotations": []},
        )
        for text in ("Wire ", "reply"):
            yield event(
                "response.output_text.delta",
                item_id=item["id"],
                output_index=index,
                content_index=0,
                delta=text,
            )
        yield event(
            "response.output_text.done",
            item_id=item["id"],
            output_index=index,
            content_index=0,
            text="Wire reply",
        )
        yield event(
            "response.content_part.done",
            item_id=item["id"],
            output_index=index,
            content_index=0,
            part=item["content"][0],
        )
    yield event("response.output_item.done", output_index=index, item=item)
    output.append(item)
    final = {
        **response,
        "status": "completed",
        "output": output,
        "usage": {
            "input_tokens": 25,
            "output_tokens": 2,
            "total_tokens": 27,
            "input_tokens_details": {"cached_tokens": 3, "cache_write_tokens": 5},
            "output_tokens_details": {"reasoning_tokens": 0},
        }
        if usage
        else None,
    }
    yield event("response.completed", response=final)


def _gemini(model, tool, reasoning, usage, arguments=None, late_signature=False):
    if reasoning:
        yield {
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [
                            {
                                "text": "Considering.",
                                "thought": True,
                                "thoughtSignature": "c2lnbmF0dXJl",
                            }
                        ],
                    }
                }
            ],
            "modelVersion": model,
        }
    if tool:
        parts = [
            {
                "functionCall": {"name": tool, "args": arguments},
                "thoughtSignature": "c2lnbmF0dXJl",
            }
        ]
        if late_signature:
            parts[0].pop("thoughtSignature", None)
        yield {
            "candidates": [{"content": {"role": "model", "parts": parts}}],
            "modelVersion": model,
        }
        if late_signature:
            yield {
                "candidates": [
                    {"content": {"role": "model", "parts": [{"thoughtSignature": "c2lnbmF0dXJl"}]}}
                ],
                "modelVersion": model,
            }
    else:
        for text in ("Wire ", "reply"):
            yield {
                "candidates": [{"content": {"role": "model", "parts": [{"text": text}]}}],
                "modelVersion": model,
            }
    final = {
        "candidates": [{"content": {"role": "model", "parts": []}, "finishReason": "STOP"}],
        "modelVersion": model,
    }
    if usage:
        final["usageMetadata"] = {
            "promptTokenCount": 20,
            "candidatesTokenCount": 2,
            "totalTokenCount": 22,
            "cachedContentTokenCount": 3,
            "thoughtsTokenCount": 0,
        }
    yield final

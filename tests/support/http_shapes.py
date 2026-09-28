"""Read HTTP face bodies through the OpenAI Python client's own types and check stream projection."""

import json
from pathlib import Path

from openai import BaseModel
from openai.types import ErrorObject, Model
from openai.types.chat import ChatCompletion, ChatCompletionChunk

CASES = json.loads((Path(__file__).resolve().parent / "http" / "cases.json").read_text())


def _refuse_extensions(value, path):
    if isinstance(value, BaseModel):
        if value.model_extra:
            raise ValueError(f"{path} carries fields the OpenAI client does not define: {sorted(value.model_extra)}.")
        for name in type(value).model_fields:
            _refuse_extensions(getattr(value, name), f"{path}.{name}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _refuse_extensions(item, f"{path}[{index}]")


def _parse(model, body):
    parsed = model.model_validate(body)
    _refuse_extensions(parsed, model.__name__)
    return parsed


def completion(body):
    return _parse(ChatCompletion, body)


def chunk(body):
    return _parse(ChatCompletionChunk, body)


def models(body):
    if set(body) != {"object", "data"} or body["object"] != "list":
        raise ValueError("A model list is an object 'list' carrying only 'data'.")
    return [_parse(Model, item) for item in body["data"]]


def error(body):
    if set(body) != {"error"}:
        raise ValueError("An error body carries only 'error'.")
    return _parse(ErrorObject, body["error"])


def stream_content(frames):
    if not frames:
        raise ValueError("The stream has no terminal response.")
    identity = None
    content = []
    stopped = False
    failed = False
    for index, frame in enumerate(frames):
        if frame == "[DONE]":
            if index != len(frames) - 1 or not stopped or failed:
                raise ValueError("[DONE] must follow a successful finish chunk.")
            return "".join(content)
        if stopped or failed:
            raise ValueError("A response followed the terminal response.")
        if isinstance(frame, dict) and "error" in frame:
            error(frame)
            if index != len(frames) - 1:
                raise ValueError("An error must be the final response.")
            failed = True
            continue
        parsed = chunk(frame)
        current = (parsed.id, parsed.created, parsed.model)
        if identity is not None and current != identity:
            raise ValueError("Chunk identity changed during the response.")
        identity = current
        if len(parsed.choices) != 1:
            raise ValueError("A chunk carries exactly one choice.")
        choice = parsed.choices[0]
        if choice.finish_reason == "stop":
            if choice.delta.content is not None:
                raise ValueError("The finish chunk carries no content.")
            stopped = True
        elif choice.finish_reason is None and choice.delta.content is not None:
            content.append(choice.delta.content)
        else:
            raise ValueError("A chunk is either reply content or the stop finish.")
    if failed:
        return None
    raise ValueError("The stream ended without a terminal response and [DONE].")


def check_projection(frames, reply):
    text = stream_content(frames)
    expected = ["".join(part["text"] for part in parts) for parts in reply["deltas"]]
    actual = [
        frame["choices"][0]["delta"]["content"]
        for frame in frames
        if isinstance(frame, dict) and "choices" in frame and "content" in frame["choices"][0]["delta"]
    ]
    if actual != expected:
        raise ValueError("Reply projection merged, split, or reordered output events.")
    if text != "".join(part["text"] for part in reply["terminal"]["content"]):
        raise ValueError("Streamed content differs from the terminal reply.")

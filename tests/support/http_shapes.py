"""Read HTTP face bodies through the OpenAI Python client's own types and check stream projection.

The fields outside those types are the extensions docs/http/reference.md lists: `cost_usd`,
`com.microsoft.amplifier.context`, and `prompt_tokens_details.cache_write_tokens` on usage, `reasoning_content` on a
message or delta, and the model limits on a model.
"""

from decimal import Decimal
import json
from pathlib import Path

from openai import BaseModel
from openai.types import CompletionUsage, ErrorObject, Model
from openai.types.chat import ChatCompletion, ChatCompletionChunk, ChatCompletionMessage
from openai.types.chat.chat_completion_chunk import ChoiceDelta
from openai.types.completion_usage import PromptTokensDetails

CASES = json.loads((Path(__file__).resolve().parent / "http" / "cases.json").read_text())


def _count(value, minimum):
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _context(value):
    return (
        isinstance(value, dict)
        and value.keys() <= {"tokens", "budget"}
        and ("tokens" not in value or _count(value["tokens"], 0))
        and ("budget" not in value or _count(value["budget"], 1))
    )


# Extension fields per type, each with the check its value must pass.
_EXTENSIONS = {
    CompletionUsage: {"com.microsoft.amplifier.context": _context},
    PromptTokensDetails: {"cache_write_tokens": lambda value: _count(value, 0)},
    ChatCompletionMessage: {"reasoning_content": lambda value: isinstance(value, str)},
    ChoiceDelta: {"reasoning_content": lambda value: isinstance(value, str)},
    Model: {
        "display_name": lambda value: isinstance(value, str),
        "context_window": lambda value: _count(value, 1),
        "max_output_tokens": lambda value: _count(value, 1),
        "reasoning_efforts": lambda value: isinstance(value, list) and all(isinstance(item, str) for item in value),
    },
}


def _refuse_extensions(value, path):
    if isinstance(value, BaseModel):
        extra = dict(value.model_extra or {})
        if isinstance(value, CompletionUsage) and "cost_usd" in extra:
            cost = extra.pop("cost_usd")
            if not isinstance(cost, str) or not Decimal(cost).is_finite() or Decimal(cost) < 0:
                raise ValueError(f"{path}.cost_usd is not a nonnegative decimal string.")
        for name, valid in _EXTENSIONS.get(type(value), {}).items():
            if name in extra and not valid(extra.pop(name)):
                raise ValueError(f"{path}.{name} does not have its documented type.")
        if extra:
            raise ValueError(f"{path} carries fields the OpenAI client does not define: {sorted(extra)}.")
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


def stream_reasoning(frames):
    """The reasoning_content pieces of a stream, in order."""
    return [
        frame["choices"][0]["delta"]["reasoning_content"]
        for frame in frames
        if isinstance(frame, dict) and "choices" in frame and "reasoning_content" in frame["choices"][0]["delta"]
    ]


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
        elif choice.finish_reason is None and (choice.delta.model_extra or {}).get("reasoning_content") is not None:
            pass
        else:
            raise ValueError("A chunk is either reply content, reasoning, or the stop finish.")
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

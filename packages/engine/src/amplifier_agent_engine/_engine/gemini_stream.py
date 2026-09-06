"""Associate Gemini signature seals with the function call they authenticate."""

from __future__ import annotations

from typing import Any, Callable


def preserve_function_signatures(
    client: Any, on_model: Callable[[str], None] | None = None
) -> None:
    models = client.aio.models
    original = models.generate_content_stream

    async def generate_content_stream(**kwargs: Any) -> Any:
        upstream = await original(**kwargs)

        async def chunks() -> Any:
            pending = None
            pending_part = None
            finished = False
            try:
                async for chunk in upstream:
                    model = getattr(chunk, "model_version", None)
                    if isinstance(model, str) and model and on_model is not None:
                        on_model(model)
                    candidates = chunk.candidates or []
                    if any(getattr(candidate, "finish_reason", None) for candidate in candidates):
                        finished = True
                    feedback = getattr(chunk, "prompt_feedback", None)
                    if getattr(feedback, "block_reason", None):
                        finished = True
                    content = candidates[0].content if candidates else None
                    parts = content.parts if content else []
                    for part in parts or []:
                        if part.function_call is not None and part.thought_signature is None:
                            if pending is not None:
                                yield pending
                            pending, pending_part = chunk, part
                            break
                        if part.thought_signature is not None and pending_part is not None:
                            pending_part.thought_signature = part.thought_signature
                            yield pending
                            pending, pending_part = None, None
                    else:
                        yield chunk
                if pending is not None:
                    yield pending
                if not finished:
                    raise RuntimeError("Gemini response ended without a terminal finish reason.")
            finally:
                await upstream.aclose()

        return chunks()

    models.generate_content_stream = generate_content_stream

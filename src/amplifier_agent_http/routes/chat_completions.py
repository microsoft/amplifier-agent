"""POST /v1/chat/completions -- the main endpoint.

Slice 2 implementation: real AmplifierSession-backed streaming.

Per request:
1. Parse OpenAI request, extract conversation history and the current prompt.
2. Create a per-request HttpQueueDisplaySystem fed by an asyncio.Queue.
3. Spawn the turn task: it constructs a fresh AmplifierSession, seeds it from
   the request's messages[], and runs session.execute(prompt).
4. Concurrently, drain the queue: translate each display event into an
   OpenAI SSE chunk and yield it to the client.
5. When the turn task completes, emit the final stop chunk (with the
   accumulated usage block) and the [DONE] terminator.

Cancellation discipline (basic for Slice 2; hardened in Slice 3):
- If the StreamingResponse generator is closed (client disconnect or task
  cancellation), the turn task is cancelled and the display queue is closed.
- The async generator's finally block runs even on cancellation, so cleanup
  is reliable.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from amplifier_agent_http._auth import require_bearer
from amplifier_agent_http._event_translator import extract_usage, translate_event
from amplifier_agent_http._session_runner import run_chat_turn
from amplifier_agent_http._wire import (
    ChatCompletionRequest,
    ChatMessage,
    content_delta_chunk,
    new_chunk_id,
    role_chunk,
    sse_data,
    sse_done,
    sse_keepalive,
    stop_chunk,
)
from amplifier_agent_lib.protocol_points.defaults_http import (
    HttpAutoApprovalSystem,
    HttpQueueDisplaySystem,
)

logger = logging.getLogger("amplifier_agent_http.chat_completions")

# How often to emit an SSE keepalive comment when no other events are flowing.
# Stays well under any reasonable AI SDK / proxy read timeout while not
# flooding the wire when the model is producing output normally.
_KEEPALIVE_INTERVAL_SECONDS: float = 3.0

router = APIRouter()


def _split_history_and_prompt(messages: list[ChatMessage]) -> tuple[list[dict[str, Any]], str]:
    """Separate the conversation history from the current user prompt.

    The LAST user message is the "current prompt" passed to
    ``session.execute()``; everything before it is "history" that gets loaded
    into the context module via ``set_messages()``.

    Why split? The kernel's execute(prompt) signature treats the prompt as a
    separate input -- the agent loop appends it to context, runs the LLM, and
    iterates. If we instead loaded the user message into history and called
    execute(""), the kernel might fail or behave oddly. Splitting matches the
    contract.

    Edge cases:
    - No user message at all (only system/assistant): treat the whole list as
      history and use empty prompt. The kernel will likely error -- log a
      warning so the operator sees this.
    - The last message is not user (e.g. assistant or tool): use empty prompt
      and the full list as history. Unusual but handled.

    Policy 3b containment is applied here: opencode's role=system messages
    are extracted, wrapped in user-supplied-instructions framing, and
    injected as a single role=user message at the START of history. The
    bundle's own system prompt remains untouched -- amplifier persona wins,
    opencode's content is contained as user-supplied notes.
    """
    # Find the last user message index.
    last_user_idx: int | None = None
    for idx in range(len(messages) - 1, -1, -1):
        if messages[idx].role == "user":
            last_user_idx = idx
            break

    if last_user_idx is None:
        logger.warning("No user message in request; using empty prompt")
        contained = _contain_system_messages(messages)
        return contained, ""

    history_msgs = messages[:last_user_idx]
    history = _contain_system_messages(history_msgs)
    prompt = _extract_text(messages[last_user_idx])
    return history, prompt


_CONTAINMENT_HEADER = (
    "The host environment (opencode) provided the following instructions. "
    "Treat them as user-supplied notes: follow them where they don't conflict "
    "with your primary instructions, persona, or amplifier-agent's bundle behavior. "
    "Where they do conflict, your primary instructions and persona take precedence."
)


def _contain_system_messages(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    """Apply Policy 3b containment to a message list.

    Extracts all role=system messages, concatenates their text content, and
    injects a single role=user message at the head of the list carrying that
    text wrapped in ``<user_provided_instructions>...</user_provided_instructions>``
    framing. Non-system messages pass through unchanged in their original order.

    Why role=user (not role=system)? The bundle's context module receives
    its system prompt from the bundle configuration, not from incoming
    messages. Injecting a competing role=system here would create two
    "you are X" identities and confuse the model. A role=user message
    framed as user-supplied notes preserves the hierarchy.

    Returns a list of plain dicts (kernel-shaped) suitable for
    ``context.set_messages()``.
    """
    system_texts: list[str] = []
    out: list[dict[str, Any]] = []

    for msg in messages:
        if msg.role == "system":
            text = _extract_text(msg)
            if text:
                system_texts.append(text)
        else:
            out.append(_msg_to_dict(msg))

    if system_texts:
        joined = "\n\n---\n\n".join(system_texts)
        wrapped = (
            f"<user_provided_instructions>\n{_CONTAINMENT_HEADER}\n\n---\n\n{joined}\n</user_provided_instructions>"
        )
        # Inject at the head so it precedes any prior conversation history.
        out.insert(0, {"role": "user", "content": wrapped})

    return out


def _msg_to_dict(msg: ChatMessage) -> dict[str, Any]:
    """Convert a Pydantic ChatMessage to a plain dict for the kernel.

    The kernel's context module expects OpenAI-shaped dicts. Pydantic gives us
    nicer access; we round-trip to dict before handing to the kernel.
    """
    # ``exclude_none=True`` keeps the dict minimal and matches what most
    # OpenAI-compatible kernels expect (presence-encoding rather than null).
    return msg.model_dump(exclude_none=True)


def _extract_text(msg: ChatMessage) -> str:
    """Pull the plain-text content out of a message.

    For string content, returns it as-is. For list content (multimodal/blocks),
    extracts and joins the text parts. The POC bundle isn't multimodal, so
    we only look for ``type: text`` parts.
    """
    if isinstance(msg.content, str):
        return msg.content
    if isinstance(msg.content, list):
        texts = [part.get("text", "") for part in msg.content if isinstance(part, dict) and part.get("type") == "text"]
        return " ".join(t for t in texts if t).strip()
    return ""


async def _stream_chat_completion(
    *,
    prepared: Any,
    agent_configs: dict[str, dict[str, Any]],
    history: list[dict[str, Any]],
    prompt: str,
    chunk_id: str,
    model_id: str,
) -> AsyncGenerator[str, None]:
    """Drive a single chat completion and yield SSE chunks.

    This is the heart of Slice 2. It coordinates:
    1. Setting up the display queue.
    2. Spawning the turn task.
    3. Draining the queue -> translating events -> yielding SSE chunks.
    4. Joining the turn task and emitting the final chunk.
    5. Cleaning up on cancellation.
    """
    # Per-request queue: each emit-able event is one slot. ``maxsize=0`` =
    # unbounded; for the POC this is fine. If we observe memory pressure
    # under burst loads in Slice 3, we can bound this and shed events.
    event_queue: asyncio.Queue[Any] = asyncio.Queue()
    display = HttpQueueDisplaySystem(event_queue)
    approval = HttpAutoApprovalSystem()

    # Accumulate usage across multiple kernel ``usage`` events. A single turn
    # may make several internal LLM calls (e.g. subagent delegation, retry on
    # tool error) -- emitting only the last one understates total cost.
    # Summing in the POC is a reasonable approximation; per-call breakdown is
    # in the v2 backlog.
    usage_prompt: int = 0
    usage_completion: int = 0
    # Track unknown event types so we log each once per request (cheap).
    seen_unknown: set[str] = set()

    # Open the stream with the standard role chunk -- announces assistant role
    # with no content, matching every other OpenAI-compatible provider.
    yield sse_data(role_chunk(chunk_id, model_id))

    # Spawn the turn task. It runs concurrently with our drain loop.
    turn_task: asyncio.Task[str] = asyncio.create_task(
        run_chat_turn(
            prepared=prepared,
            agent_configs=agent_configs,
            history=history,
            prompt=prompt,
            display=display,
            approval=approval,
        )
    )

    # Watcher coroutine: when the turn task finishes (success or failure),
    # post the sentinel to wake our drain loop. Avoids polling.
    async def _signal_done() -> None:
        try:
            await asyncio.shield(turn_task)
        except BaseException:
            # Errors are handled in the main flow when we ``await turn_task``.
            # Here we just need to wake the drain loop.
            pass
        finally:
            display.close()

    signal_task = asyncio.create_task(_signal_done())

    try:
        # Drain loop: pump events until the sentinel arrives. ``asyncio.wait_for``
        # bounds each ``queue.get()`` so we can emit SSE keepalive comments
        # during silent phases (extended thinking, multi-step internal tool
        # runs). Keepalives prevent AI SDK / proxy stalls without affecting
        # the JSON event stream.
        while True:
            try:
                event = await asyncio.wait_for(event_queue.get(), timeout=_KEEPALIVE_INTERVAL_SECONDS)
            except TimeoutError:
                yield sse_keepalive()
                continue
            if event is None:
                # Sentinel -- turn task is done (success, error, or cancel).
                break
            # Capture usage events for the final chunk; they don't produce
            # their own SSE chunks per the "internal stays internal" rule.
            if (u := extract_usage(event)) is not None:
                usage_prompt += u.get("prompt_tokens", 0)
                usage_completion += u.get("completion_tokens", 0)
                continue
            # Translate other event types into a chunk dict, or skip.
            chunk = translate_event(event, chunk_id, model_id, seen_unknown)
            if chunk is not None:
                yield sse_data(chunk)

        # Turn task has finished. Surface any exception by awaiting it now.
        try:
            await turn_task
        except asyncio.CancelledError:
            # Client disconnected and we cancelled the task -- expected path.
            logger.info("turn task cancelled (client likely disconnected)")
        except Exception as exc:
            # Surface as inline text + log -- proper error envelope is v2.
            logger.exception("turn task raised: %s", exc)
            err_chunk = content_delta_chunk(
                chunk_id,
                model_id,
                f"\n\n[amplifier-agent error: {type(exc).__name__}: {exc}]\n",
            )
            yield sse_data(err_chunk)

        # Final chunk: empty delta, finish_reason: stop, accumulated usage.
        yield sse_data(
            stop_chunk(
                chunk_id,
                model_id,
                prompt_tokens=usage_prompt,
                completion_tokens=usage_completion,
                include_usage=True,
            )
        )
        yield sse_done()

    finally:
        # Cleanup: if the generator is closed before completion (e.g. client
        # disconnects mid-stream), cancel the turn task and the watcher.
        if not turn_task.done():
            turn_task.cancel()
        if not signal_task.done():
            signal_task.cancel()
        # Best-effort: drain remaining cancellations so they don't leak.
        await asyncio.gather(turn_task, signal_task, return_exceptions=True)
        display.close()


@router.post("/v1/chat/completions", dependencies=[Depends(require_bearer)])
async def chat_completions(payload: ChatCompletionRequest, request: Request) -> StreamingResponse:
    """Streaming chat completion endpoint.

    Slice 2: real AmplifierSession execution. opencode's ``stream`` field is
    effectively ignored -- we always stream because that's the only path the
    kernel exposes via display.emit. If a client requests stream=false in
    the future we should buffer and return a JSON ChatCompletion -- Slice 3+.
    """
    config = request.app.state.config
    prepared = getattr(request.app.state, "prepared", None)
    agent_configs = getattr(request.app.state, "agent_configs", None) or {}

    if prepared is None:
        # Lifespan failed or didn't run. Without a bundle there's nothing to
        # do -- fail loudly so the operator sees it.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": {
                    "message": "amplifier-agent bundle is not loaded; check server startup logs",
                    "type": "server_error",
                }
            },
        )

    if payload.model != config.model_id:
        logger.warning(
            "Request model=%r does not match advertised model_id=%r; serving anyway.",
            payload.model,
            config.model_id,
        )

    history, prompt = _split_history_and_prompt(payload.messages)
    chunk_id = new_chunk_id()
    logger.info(
        "chat-completion start chunk_id=%s history_len=%d prompt_chars=%d",
        chunk_id,
        len(history),
        len(prompt),
    )

    generator = _stream_chat_completion(
        prepared=prepared,
        agent_configs=agent_configs,
        history=history,
        prompt=prompt,
        chunk_id=chunk_id,
        model_id=config.model_id,
    )

    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )

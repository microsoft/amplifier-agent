"""HTTP lifecycle and projection through the public Python binding."""

import asyncio
import hmac
import json
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from amplifier_agent import AgentError, AgentOptions, Session, SessionOptions, Turn, create_agent
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from ._projection import InvalidRequest, error_body, project_request
from ._settings import Settings


def _status(code: str) -> int:
    return {
        "invalid_input": 400,
        "selector_rejected": 404,
        "approval_denied": 403,
        "provider_failed": 502,
        "engine_unavailable": 502,
    }.get(code, 500)


def _error(error: AgentError) -> dict[str, Any]:
    return error_body(error.code, error.category, error.message, error.remedy)


def _internal_error() -> dict[str, Any]:
    return error_body(
        "internal_failed",
        "internal",
        "The request could not complete.",
        "Check the server diagnostics before retrying.",
    )


async def _close(session: Session) -> None:
    cleanup = asyncio.create_task(session.close())
    interrupted = False
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            interrupted = True
    cleanup.result()
    if interrupted:
        raise asyncio.CancelledError


class _TurnResponse(Response):
    def __init__(self, session: Session, turn: Turn, model: str, streaming: bool) -> None:
        super().__init__()
        self.session = session
        self.turn = turn
        self.streaming = streaming
        self.identity = {
            "id": f"chatcmpl-{uuid.uuid4().hex}",
            "created": int(time.time()),
            "model": model,
        }

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def disconnected() -> None:
            while (await receive())["type"] != "http.disconnect":
                pass

        work = asyncio.create_task(self._respond(scope, receive, send))
        watcher = asyncio.create_task(disconnected())
        try:
            done, _ = await asyncio.wait((work, watcher), return_when=asyncio.FIRST_COMPLETED)
            if work in done:
                await work
        finally:
            watcher.cancel()
            if not work.done():
                work.cancel()
            try:
                await asyncio.gather(work, watcher, return_exceptions=True)
            finally:
                await _close(self.session)

    async def _respond(self, scope: Scope, receive: Receive, send: Send) -> None:
        started = False
        first = True

        async def frame(body: dict[str, Any] | str) -> None:
            nonlocal started
            if not started:
                await send(
                    {
                        "type": "http.response.start",
                        "status": 200,
                        "headers": [
                            (b"content-type", b"text/event-stream; charset=utf-8"),
                            (b"cache-control", b"no-cache"),
                        ],
                    }
                )
                started = True
            data = body if isinstance(body, str) else json.dumps(body, separators=(",", ":"))
            await send(
                {
                    "type": "http.response.body",
                    "body": f"data: {data}\n\n".encode(),
                    "more_body": True,
                }
            )

        async def fail(body: dict[str, Any], status: int) -> None:
            if started:
                await frame(body)
                await send({"type": "http.response.body", "body": b"", "more_body": False})
            else:
                await JSONResponse(body, status_code=status)(scope, receive, send)

        try:
            async for event in self.turn.events():
                if event.type == "output_delta" and self.streaming:
                    delta = {"content": "".join(part.text for part in event.payload.content)}
                    if first:
                        delta["role"] = "assistant"
                        first = False
                    await frame(
                        {
                            **self.identity,
                            "object": "chat.completion.chunk",
                            "choices": [
                                {"index": 0, "delta": delta, "finish_reason": None},
                            ],
                        }
                    )
                elif event.type == "terminal":
                    result = event.payload
                    if result.state != "success":
                        if result.error is None:
                            await fail(_internal_error(), 500)
                        else:
                            await fail(_error(result.error), _status(result.error.code))
                    elif self.streaming:
                        await frame(
                            {
                                **self.identity,
                                "object": "chat.completion.chunk",
                                "choices": [
                                    {"index": 0, "delta": {}, "finish_reason": "stop"},
                                ],
                            }
                        )
                        await frame("[DONE]")
                        await send({"type": "http.response.body", "body": b"", "more_body": False})
                    else:
                        content = "".join(part.text for part in result.content or [])
                        await JSONResponse(
                            {
                                **self.identity,
                                "object": "chat.completion",
                                "choices": [
                                    {
                                        "index": 0,
                                        "message": {"role": "assistant", "content": content},
                                        "finish_reason": "stop",
                                    },
                                ],
                            }
                        )(scope, receive, send)
                    return
            await fail(_internal_error(), 500)
        except AgentError as error:
            await fail(_error(error), _status(error.code))
        except Exception:
            await fail(_internal_error(), 500)


def create_app(settings: Settings | None = None, options: AgentOptions | None = None) -> Starlette:
    settings = Settings.from_environment() if settings is None else settings
    options = AgentOptions() if options is None else options
    created = int(time.time())

    @asynccontextmanager
    async def lifespan(app: Starlette):
        agent = await create_agent(options)
        app.state.agent = agent
        try:
            yield
        finally:
            await agent.close()

    def authorize(request: Request) -> JSONResponse | None:
        supplied = request.headers.get("authorization", "")
        expected = f"Bearer {settings.token}"
        if hmac.compare_digest(supplied.encode(), expected.encode()):
            return None
        return JSONResponse(
            error_body(
                "invalid_input",
                "input",
                "A valid bearer token is required.",
                "Supply the server token in the Authorization header.",
            ),
            status_code=401,
        )

    async def models(request: Request) -> Response:
        refused = authorize(request)
        if refused is not None:
            return refused
        return JSONResponse(
            {
                "object": "list",
                "data": [
                    {
                        "id": settings.model,
                        "object": "model",
                        "created": created,
                        "owned_by": "amplifier-agent",
                    }
                ],
            }
        )

    async def completion(request: Request) -> Response:
        refused = authorize(request)
        if refused is not None:
            return refused
        try:
            try:
                body = await request.json()
            except (ValueError, UnicodeDecodeError):
                raise InvalidRequest("request", "Supply a JSON chat-completions request.") from None
            model, streaming, turn_input = project_request(body)
        except InvalidRequest as error:
            return JSONResponse(
                error_body(
                    "invalid_input",
                    "input",
                    f"Request field '{error.field}' cannot be honored.",
                    error.remedy,
                    error.field,
                ),
                status_code=400,
            )
        if model != settings.model:
            return JSONResponse(
                error_body(
                    "selector_rejected",
                    "selection",
                    f"Model '{model}' is not configured.",
                    "Select a model returned by /v1/models.",
                    "model",
                ),
                status_code=404,
            )
        session = None
        try:
            session = await request.app.state.agent.create_session(
                SessionOptions(persistence="ephemeral")
            )
            turn = await session.start_turn(turn_input)
            return _TurnResponse(session, turn, model, streaming)
        except BaseException as error:
            if session is not None:
                await _close(session)
            if isinstance(error, AgentError):
                return JSONResponse(_error(error), status_code=_status(error.code))
            raise

    return Starlette(
        lifespan=lifespan,
        routes=[
            Route("/v1/models", models, methods=["GET"]),
            Route("/v1/chat/completions", completion, methods=["POST"]),
        ],
    )

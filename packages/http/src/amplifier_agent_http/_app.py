"""HTTP lifecycle and projection through the public Python binding."""

import asyncio
from contextlib import asynccontextmanager
import copy
import dataclasses
import hmac
import json
import time
from typing import Any
import uuid

from amplifier_agent import (
    AgentError,
    AgentOptions,
    DiscoveryOptions,
    Session,
    SessionOptions,
    Turn,
    TurnResult,
    create_agent,
    list_models,
    list_providers,
)
from amplifier_agent._binding._factory import lacks_approval_policy
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from amplifier_agent_http._projection import (
    InvalidRequestError,
    error_body,
    project_request,
    project_usage,
    request_param,
)
from amplifier_agent_http._settings import Settings


def _status(code: str) -> int:
    return {
        "invalid_input": 400,
        "image_unsupported": 400,
        "selector_rejected": 404,
        "approval_denied": 403,
        "provider_failed": 502,
        "engine_unavailable": 502,
    }.get(code, 500)


def _error(error: AgentError, param: str | None = None) -> dict[str, Any]:
    return error_body(error.code, error.category, error.message, error.remedy, param)


def _usage(result: TurnResult) -> dict[str, Any]:
    projected = project_usage(result.usage)
    return {} if projected is None else {"usage": projected}


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
                                **_usage(result),
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
                                **_usage(result),
                            }
                        )(scope, receive, send)
                    return
            await fail(_internal_error(), 500)
        except AgentError as error:
            await fail(_error(error), _status(error.code))
        except Exception:
            await fail(_internal_error(), 500)


def refuse_unapproved_tools(options: AgentOptions) -> None:
    """Refuse an agent with tools and no static policy, since this face has no approval channel."""
    if lacks_approval_policy(options):
        raise AgentError(
            "approval_unavailable",
            "approval",
            "The agent has tools and no approval policy, and this face cannot ask for approval.",
            "Set AMPLIFIER_AGENT_APPROVALS to 'allow' or 'deny', set \"approvals\" in the config file, "
            "or pass approvals or tools=[] in AgentOptions.",
        )


def create_app(settings: Settings | None = None, options: AgentOptions | None = None) -> Starlette:
    settings = Settings.from_environment() if settings is None else settings
    options = AgentOptions() if options is None else options
    created = int(time.time())

    @asynccontextmanager
    async def lifespan(app: Starlette):
        refuse_unapproved_tools(options)
        agent = await create_agent(options)
        app.state.agent = agent
        # Discovery reads the environment the server's agents get, captured as they capture it.
        app.state.discovery = DiscoveryOptions(environment=copy.deepcopy(options.environment))
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

    async def providers(request: Request) -> Response:
        refused = authorize(request)
        if refused is not None:
            return refused
        try:
            listed = await list_providers(request.app.state.discovery)
        except AgentError as error:
            return JSONResponse(_error(error), status_code=_status(error.code))
        return JSONResponse({"object": "list", "data": [dataclasses.asdict(record) for record in listed]})

    async def provider_models(request: Request) -> Response:
        refused = authorize(request)
        if refused is not None:
            return refused
        try:
            listed = await list_models(request.path_params["provider"], request.app.state.discovery)
        except AgentError as error:
            return JSONResponse(_error(error), status_code=_status(error.code))
        data = [
            {name: value for name, value in dataclasses.asdict(record).items() if value is not None}
            for record in listed
        ]
        return JSONResponse({"object": "list", "data": data})

    async def completion(request: Request) -> Response:
        refused = authorize(request)
        if refused is not None:
            return refused
        try:
            try:
                body = await request.json()
            except (ValueError, UnicodeDecodeError):
                raise InvalidRequestError("request", "Supply a JSON chat-completions request.") from None
            model, streaming, turn_input = project_request(body)
        except InvalidRequestError as error:
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
            session = await request.app.state.agent.create_session(SessionOptions(persistence="ephemeral"))
            turn = await session.start_turn(turn_input)
            return _TurnResponse(session, turn, model, streaming)
        except BaseException as error:
            if session is not None:
                await _close(session)
            if isinstance(error, AgentError):
                field = (error.details or {}).get("field") if error.code == "invalid_input" else None
                return JSONResponse(_error(error, request_param(field, body)), status_code=_status(error.code))
            raise

    return Starlette(
        lifespan=lifespan,
        routes=[
            Route("/v1/models", models, methods=["GET"]),
            Route("/v1/providers", providers, methods=["GET"]),
            Route("/v1/providers/{provider}/models", provider_models, methods=["GET"]),
            Route("/v1/chat/completions", completion, methods=["POST"]),
        ],
    )

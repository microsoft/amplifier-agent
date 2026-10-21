"""HTTP lifecycle and projection through the public Python binding."""

import asyncio
from collections.abc import Callable
from contextlib import asynccontextmanager
import copy
import dataclasses
from datetime import UTC, datetime
import hmac
import json
import time
from typing import Any
import uuid

from amplifier_agent import (
    AgentError,
    AgentOptions,
    ApprovalResponse,
    DiscoveryOptions,
    Event,
    Session,
    SessionOptions,
    Turn,
    TurnInput,
    TurnResult,
    create_agent,
    list_models,
    list_providers,
)
from amplifier_agent._binding._factory import approval_policy, lacks_approval_policy
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from amplifier_agent_http._projection import (
    SESSION_HEADER,
    InvalidRequestError,
    context_size,
    continuation,
    error_body,
    project_request,
    project_turn_request,
    project_usage,
    request_param,
    selection,
    session_id,
    user_messages,
)
from amplifier_agent_http._settings import Settings
from amplifier_agent_http._turns import Approvals, TurnScope, current_scope, event_line


def _status(code: str) -> int:
    return {
        "invalid_input": 400,
        "image_unsupported": 400,
        "selector_rejected": 404,
        "busy": 409,
        "approval_denied": 403,
        "provider_failed": 502,
        "engine_unavailable": 502,
    }.get(code, 500)


def _error(error: AgentError, param: str | None = None) -> dict[str, Any]:
    return error_body(error.code, error.category, error.message, error.remedy, param)


def _usage(result: TurnResult, context: dict[str, int] | None) -> dict[str, Any]:
    projected = project_usage(result.usage, context)
    return {} if projected is None else {"usage": projected}


def _internal_failure() -> AgentError:
    return AgentError(
        "internal_failed",
        "internal",
        "The request could not complete.",
        "Check the server diagnostics before retrying.",
    )


def _internal_error() -> dict[str, Any]:
    return _error(_internal_failure())


def _busy(session: str) -> dict[str, Any]:
    return error_body(
        "busy",
        "turn",
        f"Session '{session}' has a turn in progress.",
        "Wait for that request to finish, then send the conversation again.",
    )


def _unknown_model(model: str) -> JSONResponse:
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


class _TurnStream(Response):
    """A started turn answering one request; a closed connection cancels the turn and keeps the session."""

    def __init__(self, session: Session, turn: Turn) -> None:
        super().__init__()
        self.session = session
        self.closed = False
        self.turn = turn

    def closing(self) -> None:
        """Settle what the request still holds open, before the session is released."""

    async def _respond(self, scope: Scope, receive: Receive, send: Send) -> None:
        raise NotImplementedError

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
                self.closing()
                await self.release()

    async def release(self) -> None:
        if not self.closed:
            self.closed = True
            await _close(self.session)


class _TurnResponse(_TurnStream):
    def __init__(self, session: Session, turn: Turn, model: str, streaming: bool) -> None:
        super().__init__(session, turn)
        self.streaming = streaming
        self.identity = {
            "id": f"chatcmpl-{uuid.uuid4().hex}",
            "created": int(time.time()),
            "model": model,
        }

    async def _respond(self, scope: Scope, receive: Receive, send: Send) -> None:
        started = False
        first = True
        reasoning: list[str] = []
        # Whether reasoning deltas arrived since the last reasoning_final, which then repeats them.
        streamed_reasoning = False
        # The turn's last context_size progress payload, for usage.
        context: dict[str, int] | None = None

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

        async def chunk(delta: dict[str, Any]) -> None:
            nonlocal first
            if first:
                delta = {"role": "assistant", **delta}
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

        try:
            async for event in self.turn.events():
                if event.type == "progress":
                    size = context_size(event.payload.data)
                    context = context if size is None else size
                elif event.type == "output_delta" and self.streaming:
                    await chunk({"content": "".join(part.text for part in event.payload.content)})
                elif event.type in ("reasoning_delta", "reasoning_final"):
                    # http-face.v1 section 3: a final repeats its deltas, so only a final without them is new.
                    text = event.payload.text
                    if event.type == "reasoning_final":
                        text = "" if streamed_reasoning else text
                        streamed_reasoning = False
                    else:
                        streamed_reasoning = True
                    if text:
                        reasoning.append(text)
                        if self.streaming:
                            await chunk({"reasoning_content": text})
                elif event.type == "terminal":
                    result = event.payload
                    # Release the session before the client sees the end, so its next request finds it free.
                    await self.release()
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
                                **_usage(result, context),
                            }
                        )
                        await frame("[DONE]")
                        await send({"type": "http.response.body", "body": b"", "more_body": False})
                    else:
                        message = {"role": "assistant", "content": "".join(part.text for part in result.content or [])}
                        if reasoning:
                            message["reasoning_content"] = "".join(reasoning)
                        await JSONResponse(
                            {
                                **self.identity,
                                "object": "chat.completion",
                                "choices": [
                                    {
                                        "index": 0,
                                        "message": message,
                                        "finish_reason": "stop",
                                    },
                                ],
                                **_usage(result, context),
                            }
                        )(scope, receive, send)
                    return
            await fail(_internal_error(), 500)
        except AgentError as error:
            await fail(_error(error), _status(error.code))
        except Exception:
            await fail(_internal_error(), 500)


class _EventsResponse(_TurnStream):
    """The turn's events, one turn-events.v1 envelope per server-sent event, ending with terminal."""

    def __init__(self, session: Session, turn: Turn, scope: TurnScope) -> None:
        super().__init__(session, turn)
        self.scope = scope

    def closing(self) -> None:
        self.scope.cancel_open()

    async def _respond(self, scope: Scope, receive: Receive, send: Send) -> None:
        last: Event | None = None

        async def line(body: bytes, more: bool) -> None:
            if last is None:
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
            await send({"type": "http.response.body", "body": body, "more_body": more})

        try:
            async for event in self.turn.events():
                self.scope.observe(event)
                terminal = event.type == "terminal"
                if terminal:
                    # Release the session before the client sees the end, so its next request finds it free.
                    await self.release()
                await line(event_line(event), not terminal)
                last = event
                if terminal:
                    return
            error = _internal_failure()
        except AgentError as failed:
            error = failed
        except Exception:
            error = _internal_failure()
        await self.release()
        if last is None:
            await JSONResponse(_error(error), status_code=_status(error.code))(scope, receive, send)
            return
        # http-turns.v1 section 3: once the stream has begun, a failure is the terminal event.
        ending = Event(
            last.contract_version,
            last.session_id,
            last.turn_id,
            last.sequence + 1,
            "terminal",
            TurnResult("failure", error=error),
            datetime.now(UTC),
        )
        await line(event_line(ending), False)


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
        policy, app.state.working_directory = approval_policy(options)
        # The face answers every approval itself, so a turns request can narrow the server's policy.
        app.state.approvals = None if policy is None else Approvals(policy)
        agent = await create_agent(
            options if app.state.approvals is None else dataclasses.replace(options, approvals=app.state.approvals)
        )
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

    def model_entry(name: str, **fields: Any) -> dict[str, Any]:
        return {"id": name, "object": "model", "created": created, "owned_by": "amplifier-agent", **fields}

    async def provider_names(app: Starlette) -> frozenset[str]:
        """Every provider the binding registers, read once."""
        if getattr(app.state, "providers", None) is None:
            app.state.providers = frozenset(record.provider for record in await list_providers(app.state.discovery))
        return app.state.providers

    async def models(request: Request) -> Response:
        refused = authorize(request)
        if refused is not None:
            return refused
        discovery = request.app.state.discovery
        try:
            reachable = [
                record.provider
                for record in await list_providers(discovery)
                if record.installed and record.credentials in ("found", "not_required")
            ]
        except AgentError as error:
            return JSONResponse(_error(error), status_code=_status(error.code))
        listings = await asyncio.gather(
            *(list_models(provider, discovery) for provider in reachable), return_exceptions=True
        )
        data = [model_entry(settings.model)]
        for provider, listed in zip(reachable, listings, strict=True):
            if isinstance(listed, AgentError):
                # A provider that cannot list its models offers none to select.
                continue
            if isinstance(listed, BaseException):
                raise listed
            for record in listed:
                fields = {name: value for name, value in dataclasses.asdict(record).items() if value is not None}
                data.append(model_entry(f"{provider}/{fields.pop('id')}", **fields))
        return JSONResponse({"object": "list", "data": data})

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

    async def own_selection(app: Starlette) -> tuple[str, str]:
        """The configured agent's own provider and model, read once from a fresh session."""
        if getattr(app.state, "own", None) is None:
            probe = await app.state.agent.create_session(SessionOptions(persistence="ephemeral"))
            try:
                app.state.own = (probe.info.provider, probe.info.model)
            finally:
                await _close(probe)
        return app.state.own

    async def select(app: Starlette, session: Session, selected: tuple[str, str] | None, fresh: bool) -> None:
        """Run `session` on the requested selection, as Session.set_model does, when it differs.

        A `fresh` session was just created and so already runs the agent's own selection.
        """
        if selected is None and fresh:
            return
        target = selected or await own_selection(app)
        if (session.info.provider, session.info.model) != target:
            await session.set_model(*target)

    async def held(
        app: Starlette, sid: str, turn_input: TurnInput, selected: tuple[str, str] | None
    ) -> tuple[Session, Turn] | None:
        """The turn on server-held session `sid`, or None when the request and session disagree.

        http-face.v1 section 12: they agree when the request holds one more user message than the session has
        turns, and a session that does not exist agrees with one user message and is created.
        """
        agent = app.state.agent
        users = user_messages(turn_input)
        created = False
        try:
            session = await agent.resume_session(sid)
        except AgentError as error:
            if error.code != "not_found":
                raise
            if users != 1:
                return None
            session = await agent.create_session(SessionOptions(session_id=sid, persistence="durable"))
            created = True
        try:
            if not created and users != len(session.history) + 1:
                await _close(session)
                return None
            await select(app, session, selected, created)
            return session, await session.start_turn(continuation(turn_input))
        except BaseException:
            await _close(session)
            if created:
                # Nothing ran, so the session this request created is not kept.
                await agent.delete_session(sid)
            raise

    def invalid(error: InvalidRequestError) -> JSONResponse:
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

    async def parse(
        request: Request, project: Callable[[Any], tuple[str, bool, TurnInput]]
    ) -> Response | tuple[Any, str, bool, TurnInput, str | None]:
        """The request body, model, projected flag, turn input, and session id, or the refusal."""
        refused = authorize(request)
        if refused is not None:
            return refused
        try:
            try:
                body = await request.json()
            except (ValueError, UnicodeDecodeError):
                raise InvalidRequestError("request", "Supply a JSON chat-completions request.") from None
            model, flag, turn_input = project(body)
            sid = session_id(request.headers.get(SESSION_HEADER))
            if sid is not None:
                continuation(turn_input)
        except InvalidRequestError as error:
            return invalid(error)
        return body, model, flag, turn_input, sid

    async def begin(
        request: Request, body: Any, model: str, turn_input: TurnInput, sid: str | None
    ) -> Response | tuple[Session, Turn]:
        """The started turn on the session the request selects, or the refusal."""
        try:
            selected = selection(model, settings.model)
        except ValueError:
            return _unknown_model(model)
        if selected is not None:
            try:
                registered = await provider_names(request.app)
            except AgentError as error:
                return JSONResponse(_error(error), status_code=_status(error.code))
            if selected[0] not in registered:
                return _unknown_model(model)
        if sid is not None:
            try:
                found = await held(request.app, sid, turn_input, selected)
            except AgentError as error:
                if error.code in ("session_in_use", "already_exists", "busy"):
                    # Another request holds the session, or is creating it.
                    return JSONResponse(_busy(sid), status_code=409)
                field = (error.details or {}).get("field") if error.code == "invalid_input" else None
                param = request_param(field, body, len(body["messages"]) - 1)
                return JSONResponse(_error(error, param), status_code=_status(error.code))
            if found is not None:
                return found
        session = None
        try:
            session = await request.app.state.agent.create_session(SessionOptions(persistence="ephemeral"))
            await select(request.app, session, selected, True)
            return session, await session.start_turn(turn_input)
        except BaseException as error:
            if session is not None:
                await _close(session)
            if isinstance(error, AgentError):
                field = (error.details or {}).get("field") if error.code == "invalid_input" else None
                return JSONResponse(_error(error, request_param(field, body)), status_code=_status(error.code))
            raise

    async def completion(request: Request) -> Response:
        parsed = await parse(request, project_request)
        if isinstance(parsed, Response):
            return parsed
        body, model, streaming, turn_input, sid = parsed
        started = await begin(request, body, model, turn_input, sid)
        if isinstance(started, Response):
            return started
        return _TurnResponse(*started, model, streaming)

    async def turns(request: Request) -> Response:
        parsed = await parse(request, project_turn_request)
        if isinstance(parsed, Response):
            return parsed
        body, model, asks, turn_input, sid = parsed
        scope = TurnScope(asks, request.app.state.working_directory)
        # The turn's tasks copy this context when the turn starts, so the face's approval handler finds the scope.
        token = current_scope.set(scope)
        try:
            started = await begin(request, body, model, turn_input, sid)
        finally:
            current_scope.reset(token)
        if isinstance(started, Response):
            return started
        return _EventsResponse(*started, scope)

    async def approve(request: Request) -> Response:
        refused = authorize(request)
        if refused is not None:
            return refused
        try:
            try:
                body = await request.json()
            except (ValueError, UnicodeDecodeError):
                raise InvalidRequestError("request", "Supply a JSON approval answer.") from None
            if not isinstance(body, dict):
                raise InvalidRequestError("request", "Supply the answer as an object with decision and reason.")
            extra = sorted(body.keys() - {"decision", "reason"})
            if extra:
                raise InvalidRequestError(extra[0], f"Remove {extra[0]}; an answer holds decision and reason.")
            if body.get("decision") not in ("allow", "deny", "cancel"):
                raise InvalidRequestError("decision", "Set decision to 'allow', 'deny', or 'cancel'.")
            reason = body.get("reason")
            if reason is not None and not isinstance(reason, str):
                raise InvalidRequestError("reason", "Supply reason as a string, or omit it.")
        except InvalidRequestError as error:
            return invalid(error)
        request_id = request.path_params["request_id"]
        approvals: Approvals | None = request.app.state.approvals
        if approvals is None or not approvals.answer(request_id, ApprovalResponse(body["decision"], reason)):
            return JSONResponse(
                error_body(
                    "not_found",
                    "approval",
                    f"Approval request '{request_id}' is not open.",
                    "Answer an approval_request from a running turn that asked, before its deadline.",
                ),
                status_code=404,
            )
        return Response(status_code=204)

    return Starlette(
        lifespan=lifespan,
        routes=[
            Route("/v1/models", models, methods=["GET"]),
            Route("/v1/providers", providers, methods=["GET"]),
            Route("/v1/providers/{provider}/models", provider_models, methods=["GET"]),
            Route("/v1/chat/completions", completion, methods=["POST"]),
            Route("/v1/amplifier/turns", turns, methods=["POST"]),
            Route("/v1/amplifier/approvals/{request_id}", approve, methods=["POST"]),
        ],
    )

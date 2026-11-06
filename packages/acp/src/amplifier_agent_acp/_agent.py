"""The ACP agent: one per connection, with one binding agent and session per ACP session (acp-face.v1)."""

import asyncio
import contextlib
from datetime import UTC, datetime
from functools import partial
from importlib.metadata import version
import logging
from pathlib import Path
from typing import Any, Literal, cast
import uuid

from acp import PROTOCOL_VERSION, Agent, RequestError
from acp.agent.connection import AgentSideConnection
from acp.connection import StreamDirection, StreamEvent
from acp.schema import (
    AcpMcpServer,
    AgentCapabilities,
    AuthenticateResponse,
    AuthMethodAgent,
    ClientCapabilities,
    CloseSessionResponse,
    ConfigOptionUpdate,
    DeleteSessionResponse,
    HttpMcpServer,
    Implementation,
    InitializeResponse,
    ListSessionsResponse,
    LoadSessionResponse,
    McpCapabilities,
    McpServerStdio,
    NewSessionResponse,
    PermissionOption,
    PromptCapabilities,
    PromptResponse,
    RequestPermissionResponse,
    ResumeSessionResponse,
    SessionAdditionalDirectoriesCapabilities,
    SessionCapabilities,
    SessionCloseCapabilities,
    SessionConfigOptionSelect,
    SessionDeleteCapabilities,
    SessionInfo,
    SessionInfoUpdate,
    SessionListCapabilities,
    SessionResumeCapabilities,
    SetSessionConfigOptionResponse,
    SseMcpServer,
    ToolCallUpdate,
)
from acp.stdio import stdio_streams
from amplifier_agent import Agent as BindingAgent
from amplifier_agent import (
    AgentError,
    AgentOptions,
    McpServer,
    ProviderRecord,
    Session,
    SessionOptions,
    Turn,
    TurnInput,
    TurnResult,
    create_agent,
    list_providers,
)
from amplifier_agent._binding._factory import approval_policy, selected_provider

from amplifier_agent_acp._approvals import MODES, Approvals
from amplifier_agent_acp._config import Models, config_options, discover
from amplifier_agent_acp._content import Block, turn_content
from amplifier_agent_acp._errors import busy, closing, internal, not_held, refusal, request_error
from amplifier_agent_acp._updates import Update, View, replay

AUTH_METHOD = "provider-credentials"
# How long, after stdin closes, requests already received may take to be answered before the face closes.
SHUTDOWN_SECONDS = 10.0
# The largest message read from stdin, as run_agent allows: prompts carry images and embedded resources.
STDIO_LIMIT = 50 * 1024 * 1024

# Methods whose response may be followed by the full model list, and which name their session.
_OFFERING = ("session/load", "session/resume", "session/set_config_option")

McpServers = list[HttpMcpServer | SseMcpServer | AcpMcpServer | McpServerStdio]

logger = logging.getLogger(__name__)


class _Held:
    """One ACP session: its binding agent and session, what its client was shown, and its approvals."""

    def __init__(self, agent: BindingAgent, session: Session, view: View, approvals: Approvals) -> None:
        self.agent = agent
        self.session = session
        self.view = view
        self.approvals = approvals
        self.turn: Turn | None = None
        self.busy = False
        self.cancel_requested = False
        # Whether the response that opened the session is out, and whether it was offered every model since.
        self.opened = False
        self.models_shown = False

    def config_options(self, models: Models | None) -> list[SessionConfigOptionSelect]:
        return config_options(self.session.info, models, self.approvals.mode)

    async def cancel(self) -> None:
        self.approvals.cancel_open()
        if self.turn is None:
            self.cancel_requested = self.busy
            return
        with contextlib.suppress(AgentError):
            await self.turn.cancel()

    async def close(self) -> None:
        try:
            await self.cancel()
        finally:
            try:
                await self.session.close()
            finally:
                await self.agent.close()


def _directory(cwd: str) -> Path:
    path = Path(cwd)
    if not path.is_absolute() or not path.is_dir():
        raise refusal(
            f"The cwd '{cwd}' is not an absolute path to an existing directory.",
            "Send the absolute path of the project directory.",
        )
    return path


def _timestamp(moment: datetime) -> str:
    """An RFC 3339 UTC time."""
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _no_provider(records: list[ProviderRecord], defaults: list[str]) -> None:
    """Fail auth required when no provider is set and none the engine defaults to has credentials."""
    if any(record.provider in defaults and record.credentials != "missing" for record in records):
        return
    variables = [
        variable
        for record in records
        if record.provider in defaults and record.installed
        for variable in record.credential_variables
    ]
    raise RequestError(
        -32000,
        f"No provider is configured, and no provider's credentials were found. Set one of {', '.join(variables)} "
        "in the agent's environment, then authenticate again.",
        {"providers": defaults, "credential_variables": variables},
    )


def _mcp_servers(servers: McpServers | None) -> list[McpServer] | None:
    if not servers:
        return None
    converted = []
    for server in servers:
        if isinstance(server, HttpMcpServer):
            headers = {header.name: header.value for header in server.headers}
            converted.append(McpServer(server.name, "http", url=server.url, headers=headers or None))
        elif isinstance(server, McpServerStdio):
            env = {variable.name: variable.value for variable in server.env}
            converted.append(McpServer(server.name, "stdio", command=server.command, args=server.args, env=env or None))
        else:
            raise refusal(
                f"MCP server '{server.name}' uses the {server.type} transport, which this agent cannot connect to.",
                "Configure it as a stdio or http MCP server.",
            )
    return converted


# A plain class rather than an `acp.Agent` subclass: the router answers -32601 for a method this class lacks, where an
# inherited protocol stub would answer success. It therefore does not satisfy the `acp.Agent` protocol, and callers
# cast it, as the SDK's own connection does once it has the instance.
class AcpAgent:
    def __init__(self) -> None:
        self._client: AgentSideConnection | None = None
        self._terminal_output = False
        self._sessions: dict[str, _Held] = {}
        # The host's static approval policy, once read.
        self._policy: Literal["allow", "deny"] | None = None
        self._policy_read = False
        self._discovery: asyncio.Future[Models] | None = None
        # Futures that resolve once the response that opened a session has been written, by session.
        self._written: dict[str, list[asyncio.Future[None]]] = {}
        # Client requests not yet answered, and the session each session/load or session/resume names.
        self._unanswered: set[str | int | None] = set()
        self._answered = asyncio.Event()
        self._answered.set()
        # The session each client request that names one is about, for the requests that offer models afterwards.
        self._request_sessions: dict[str | int | None, str] = {}
        # Opens in flight, so concurrent loads and resumes of one session share a single handle.
        self._opens: dict[str, asyncio.Task[_Held]] = {}
        self._background: set[asyncio.Task[None]] = set()
        self._closing = False

    def on_connect(self, conn: AgentSideConnection) -> None:
        self._client = conn

    def observe(self, event: StreamEvent) -> None:
        """Track the requests the client sent and the responses written to them."""
        message = event.message
        request_id = message.get("id")
        if not isinstance(request_id, (str, int, type(None))) or "id" not in message:
            return
        if event.direction == StreamDirection.INCOMING:
            method = message.get("method")
            if method is None:
                return
            self._unanswered.add(request_id)
            self._answered.clear()
            params = message.get("params")
            if method in _OFFERING and isinstance(params, dict):
                session_id = params.get("sessionId")
                if isinstance(session_id, str):
                    self._request_sessions[request_id] = session_id
            return
        if "method" in message:
            return
        self._unanswered.discard(request_id)
        if not self._unanswered:
            self._answered.set()
        session_id = self._request_sessions.pop(request_id, None)
        result = message.get("result")
        if session_id is None and isinstance(result, dict) and isinstance(result.get("sessionId"), str):
            session_id = result["sessionId"]
        for future in self._written.pop(session_id, []) if session_id is not None else []:
            if not future.done():
                future.set_result(None)

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: ClientCapabilities | None = None,
        client_info: Implementation | None = None,
        **kwargs: Any,
    ) -> InitializeResponse:
        meta = client_capabilities.field_meta if client_capabilities is not None else None
        self._terminal_output = isinstance(meta, dict) and meta.get("terminal_output") is True
        return InitializeResponse(
            protocol_version=PROTOCOL_VERSION,
            agent_capabilities=AgentCapabilities(
                load_session=True,
                prompt_capabilities=PromptCapabilities(image=True, audio=False, embedded_context=True),
                mcp_capabilities=McpCapabilities(http=True, sse=False),
                session_capabilities=SessionCapabilities(
                    list=SessionListCapabilities(),
                    resume=SessionResumeCapabilities(),
                    close=SessionCloseCapabilities(),
                    delete=SessionDeleteCapabilities(),
                    additional_directories=SessionAdditionalDirectoriesCapabilities(),
                ),
            ),
            auth_methods=[
                AuthMethodAgent(
                    id=AUTH_METHOD,
                    name="Provider credentials",
                    description="Set the selected provider's credential variables in the agent's environment.",
                )
            ],
            agent_info=Implementation(name="amplifier-agent", version=version("amplifier-agent-acp")),
        )

    async def authenticate(self, method_id: str, **kwargs: Any) -> AuthenticateResponse:
        if method_id != AUTH_METHOD:
            raise refusal(f"Authentication method '{method_id}' does not exist.", f"Use '{AUTH_METHOD}'.")
        await self._require_credentials()
        return AuthenticateResponse()

    async def new_session(
        self,
        cwd: str,
        additional_directories: list[str] | None = None,
        mcp_servers: McpServers | None = None,
        **kwargs: Any,
    ) -> NewSessionResponse:
        _directory(cwd)
        await self._require_credentials()
        session_id = str(uuid.uuid4())
        held = await self._open(session_id, cwd, additional_directories, mcp_servers, True)
        self._offer_models(session_id, held)
        return NewSessionResponse(session_id=session_id, config_options=held.config_options(None))

    async def load_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: McpServers | None = None,
        additional_directories: list[str] | None = None,
        **kwargs: Any,
    ) -> LoadSessionResponse:
        held = await self._acquire(session_id, cwd, additional_directories, mcp_servers)
        for record in held.session.history:
            for update in replay(held.view, record, session_id):
                await self._send(session_id, update)
        self._offer_models(session_id, held)
        return LoadSessionResponse(config_options=held.config_options(self._models_now()))

    async def resume_session(
        self,
        session_id: str,
        cwd: str,
        additional_directories: list[str] | None = None,
        mcp_servers: McpServers | None = None,
        **kwargs: Any,
    ) -> ResumeSessionResponse:
        held = await self._acquire(session_id, cwd, additional_directories, mcp_servers)
        self._offer_models(session_id, held)
        return ResumeSessionResponse(config_options=held.config_options(self._models_now()))

    async def close_session(self, session_id: str, **kwargs: Any) -> CloseSessionResponse:
        held = self._sessions.pop(session_id, None)
        if held is not None:
            try:
                await held.close()
            except AgentError as error:
                raise request_error(error) from None
        return CloseSessionResponse()

    async def delete_session(self, session_id: str, **kwargs: Any) -> DeleteSessionResponse:
        held = self._sessions.pop(session_id, None)
        try:
            if held is not None:
                try:
                    await held.cancel()
                    await held.session.close()
                    await held.agent.delete_session(session_id)
                finally:
                    await held.agent.close()
            else:
                async with await create_agent(AgentOptions(approvals="deny")) as agent:
                    await agent.delete_session(session_id)
        except AgentError as error:
            raise request_error(error) from None
        return DeleteSessionResponse()

    async def list_sessions(
        self, cwd: str | None = None, cursor: str | None = None, **kwargs: Any
    ) -> ListSessionsResponse:
        directory = Path.cwd() if cwd is None else _directory(cwd)
        try:
            async with await create_agent(AgentOptions(working_directory=directory, approvals="deny")) as agent:
                records = await agent.list_sessions()
        except AgentError as error:
            raise request_error(error) from None
        target = directory.resolve()
        records = [record for record in records if Path(record.working_directory).resolve() == target]
        records.sort(key=lambda record: record.updated_at, reverse=True)
        return ListSessionsResponse(
            sessions=[
                SessionInfo(
                    session_id=record.session_id,
                    cwd=str(directory),
                    title=record.title,
                    updated_at=_timestamp(record.updated_at),
                )
                for record in records
            ]
        )

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        held = self._sessions.get(session_id)
        if held is not None and held.busy:
            await held.cancel()

    async def set_config_option(
        self, config_id: str, session_id: str, value: str | bool, **kwargs: Any
    ) -> SetSessionConfigOptionResponse:
        held = self._held(session_id)
        if config_id == "approvals" and held.approvals.mode is not None:
            mode = next((mode for mode in MODES if mode == value), None)
            if mode is None:
                raise refusal(
                    f"The approvals mode {value!r} does not exist.", "Set approvals to ask, allow-reads, or allow-all."
                )
            # The binding takes no part, so a turn may be running: the mode decides its later requests.
            held.approvals.mode = mode
            return self._changed(session_id, held)
        if held.busy:
            raise busy(session_id)
        info = held.session.info
        if config_id == "model" and isinstance(value, str):
            provider, separator, model = value.partition("/")
            if not separator or not provider or not model:
                raise refusal(f"The model '{value}' is not a '<provider>/<model>' value.", "Choose a model option.")
            selection = (provider, model, info.reasoning_effort)
        elif config_id == "reasoning_effort" and isinstance(value, str):
            selection = (info.provider, info.model, value)
        else:
            raise refusal(
                f"The config option '{config_id}' does not take the value {value!r}.",
                "Set a config option the session offers to one of its values.",
            )
        held.busy = True
        try:
            await held.session.set_model(*selection)
        except AgentError as error:
            raise request_error(error) from None
        finally:
            held.busy = False
        return self._changed(session_id, held)

    def _changed(self, session_id: str, held: _Held) -> SetSessionConfigOptionResponse:
        """The answer to a config change: every config option at its new value."""
        models = self._models_now()
        if models is None:
            # Discovery failed or has not finished: try again, and offer the list after this response.
            self._offer_models(session_id, held)
        return SetSessionConfigOptionResponse(config_options=held.config_options(models))

    async def prompt(self, session_id: str, prompt: list[Block], **kwargs: Any) -> PromptResponse:
        held = self._held(session_id)
        content = turn_content(prompt)
        if self._closing:
            raise closing()
        if held.busy:
            raise busy(session_id)
        held.busy = True
        held.cancel_requested = False
        held.view.begin_turn()
        try:
            try:
                turn = await held.session.start_turn(TurnInput(content))
            except AgentError as error:
                raise request_error(error) from None
            held.turn = turn
            if held.cancel_requested:
                await held.cancel()
            result = await self._run(session_id, held, turn)
        finally:
            held.turn = None
            held.busy = False
        if result.state == "success":
            info = held.session.info
            await self._send(
                session_id,
                SessionInfoUpdate(
                    session_update="session_info_update", title=info.title, updated_at=_timestamp(info.updated_at)
                ),
            )
            return PromptResponse(stop_reason="end_turn")
        if result.state == "cancelled":
            return PromptResponse(stop_reason="cancelled")
        raise internal() if result.error is None else request_error(result.error)

    async def _run(self, session_id: str, held: _Held, turn: Turn) -> TurnResult:
        """Send every update of the turn, settle every tool call it showed, and return its result."""
        view = held.view
        result: TurnResult | None = None
        try:
            async for event in turn.events():
                for update in view.updates(event):
                    await self._send(session_id, update)
                if event.type == "tool_call":
                    view.sightings.see(event.payload.call)
                elif event.type == "terminal":
                    result = event.payload
        except AgentError as error:
            result = TurnResult("failure", error=error)
        except BaseException:
            # The request was cancelled or failed, so the turn it started ends too, and every call it showed settles.
            held.approvals.cancel_open()
            with contextlib.suppress(Exception):
                await asyncio.shield(turn.cancel())
            with contextlib.suppress(Exception):
                for update in view.settle():
                    await self._send(session_id, update)
            raise
        for update in view.settle():
            await self._send(session_id, update)
        return TurnResult("failure", error=None) if result is None else result

    def _held(self, session_id: str) -> _Held:
        held = self._sessions.get(session_id)
        if held is None:
            raise not_held(session_id)
        return held

    async def _acquire(
        self,
        session_id: str,
        cwd: str,
        additional_directories: list[str] | None,
        mcp_servers: McpServers | None,
    ) -> _Held:
        """The session's live handle, opening it once however many requests ask for it at the same time."""
        held = self._sessions.get(session_id)
        if held is not None:
            return held
        opening = self._opens.get(session_id)
        if opening is None:
            opening = asyncio.create_task(self._open(session_id, cwd, additional_directories, mcp_servers, False))
            self._opens[session_id] = opening

            def forget(done: asyncio.Task[_Held]) -> None:
                if self._opens.get(session_id) is done:
                    del self._opens[session_id]
                # Every request awaiting the open reports its failure, so the task's own copy is not lost.
                if not done.cancelled():
                    done.exception()

            opening.add_done_callback(forget)
        return await asyncio.shield(opening)

    async def _open(
        self,
        session_id: str,
        cwd: str,
        additional_directories: list[str] | None,
        mcp_servers: McpServers | None,
        create: bool,
    ) -> _Held:
        directory = _directory(cwd)
        servers = _mcp_servers(mcp_servers)
        view = View(directory, self._terminal_output)
        directories = [directory, *(directory / path for path in additional_directories or [])]
        approvals = Approvals(
            self._static_policy(), view, partial(self._send, session_id), partial(self._ask, session_id), directories
        )
        options = AgentOptions(
            working_directory=directory,
            additional_directories=list(additional_directories) if additional_directories else None,
            mcp_servers=servers,
            approvals=approvals,
        )
        try:
            agent = await create_agent(options)
        except AgentError as error:
            raise request_error(error) from None
        try:
            if create:
                session = await agent.create_session(SessionOptions(session_id=session_id, persistence="durable"))
            else:
                session = await agent.resume_session(session_id)
        except BaseException as error:
            await agent.close()
            if isinstance(error, AgentError):
                raise request_error(error) from None
            raise
        view.count_history(session.history)
        held = _Held(agent, session, view, approvals)
        self._sessions[session_id] = held
        return held

    def _static_policy(self) -> Literal["allow", "deny"] | None:
        """The host's static approval policy, read once."""
        if not self._policy_read:
            try:
                policy, _ = approval_policy(AgentOptions())
            except AgentError as error:
                raise request_error(error) from None
            self._policy = "allow" if policy == "allow" else "deny" if policy == "deny" else None
            self._policy_read = True
        return self._policy

    async def _require_credentials(self) -> None:
        try:
            records = await list_providers()
            try:
                provider = selected_provider(AgentOptions())
            except AgentError as error:
                # With no provider set, the engine defaults to one of these when its credentials are found.
                defaults = (error.details or {}).get("providers")
                if error.code != "engine_unavailable" or not isinstance(defaults, list):
                    raise
                _no_provider(records, defaults)
                raise
        except AgentError as error:
            raise request_error(error) from None
        record = next((record for record in records if record.provider == provider), None)
        if record is None or record.credentials != "missing":
            return
        variables = record.credential_variables
        remedy = (
            f"Set {' or '.join(variables)} in the agent's environment, then authenticate again."
            if variables
            else f"Sign in to {record.display_name} as its documentation describes, then authenticate again."
        )
        raise RequestError(
            -32000,
            f"The {record.display_name} provider has no credentials. {remedy}",
            {"provider": provider, "credential_variables": list(variables)},
        )

    async def _send(self, session_id: str, update: Update) -> None:
        if self._client is None:
            return
        # Once stdin closes, the SDK marks the connection disconnected and session_update raises ConnectionError,
        # though stdout stays open. The update is dropped and the turn runs on to its end.
        with contextlib.suppress(ConnectionError):
            await self._client.session_update(session_id, update)

    async def _ask(
        self, session_id: str, tool_call_id: str, options: list[PermissionOption]
    ) -> RequestPermissionResponse:
        if self._client is None:
            raise ConnectionError("The client is not connected.")
        return await self._client.request_permission(session_id, ToolCallUpdate(tool_call_id=tool_call_id), options)

    def _models_now(self) -> Models | None:
        discovery = self._discovery
        if discovery is None or not discovery.done() or discovery.cancelled() or discovery.exception() is not None:
            return None
        return discovery.result()

    async def _models(self) -> Models | None:
        if self._discovery is None:
            self._discovery = asyncio.ensure_future(discover())
        discovery = self._discovery
        try:
            return await asyncio.shield(discovery)
        except AgentError as error:
            logger.warning("Model discovery failed: %s %s", error.message, error.remedy)
        except Exception:
            logger.exception("Model discovery failed")
        if self._discovery is discovery:
            self._discovery = None
        return None

    def _offer_models(self, session_id: str, held: _Held) -> None:
        """Send every model as a config option once discovery ends and the response that opened the session is out."""
        written: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._written.setdefault(session_id, []).append(written)

        async def offer() -> None:
            await written
            held.opened = True
            models = await self._models()
            if models is None:
                return
            # Sessions whose own discovery failed get the list now too.
            for target_id, target in list(self._sessions.items()):
                if target is held or (target.opened and not target.models_shown):
                    target.models_shown = True
                    await self._send(
                        target_id,
                        ConfigOptionUpdate(
                            session_update="config_option_update",
                            config_options=target.config_options(models),
                        ),
                    )

        task = asyncio.create_task(offer())
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def shutdown(self) -> None:
        """End every turn, answer the requests already received, then close every session and agent."""
        self._closing = True
        for held in list(self._sessions.values()):
            try:
                await held.cancel()
            except Exception:
                logger.exception("Cancelling a turn failed")
        if self._unanswered:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._answered.wait(), SHUTDOWN_SECONDS)
        for task in list(self._background):
            task.cancel()
        if self._discovery is not None:
            self._discovery.cancel()
        sessions = list(self._sessions.values())
        self._sessions.clear()
        for held in sessions:
            try:
                await held.close()
            except Exception:
                logger.exception("Closing a session failed")


async def serve(output: asyncio.StreamWriter | None = None, incoming: asyncio.StreamReader | None = None) -> None:
    """Serve one client until it closes its end, then shut the face down. Defaults to stdin and stdout."""
    if output is None or incoming is None:
        incoming, output = await stdio_streams(limit=STDIO_LIMIT)
    face = AcpAgent()
    connection = AgentSideConnection(cast(Agent, face), output, incoming, listening=False, observers=[face.observe])
    try:
        await connection.listen()
        await face.shutdown()
    finally:
        await asyncio.shield(connection.close())

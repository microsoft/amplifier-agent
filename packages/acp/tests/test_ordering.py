import asyncio
from typing import Any

from acp import PROTOCOL_VERSION, RequestError
from acp.connection import Connection
from acp.schema import TextContentBlock, ToolCallStart
from amplifier_agent_acp._agent import AcpAgent, _Held
import pytest

from tests.support.engine import provision

WRITE = {"tool": {"name": "write_file", "arguments": {"file_path": "notes.txt", "content": "hello\n"}}}
OTHER = {"tool": {"name": "write_file", "arguments": {"file_path": "other.txt", "content": "hi\n"}}}
READ = {"tool": {"name": "read_file", "arguments": {"file_path": "notes.txt"}}}


def _update(message: dict[str, Any], kind: str | None = None) -> dict[str, Any] | None:
    if message.get("method") != "session/update":
        return None
    update = message["params"]["update"]
    return update if kind is None or update["sessionUpdate"] == kind else None


def _index(wire: list[dict[str, Any]], found) -> int:
    return next(index for index, message in enumerate(wire) if found(message))


def _asked(wire: list[dict[str, Any]]) -> list[str]:
    """The toolCallId of each permission request, in order."""
    return [
        message["params"]["toolCall"]["toolCallId"]
        for message in wire
        if message.get("method") == "session/request_permission"
    ]


def _approvals(options: list[Any]) -> str | None:
    """The approvals mode among a response's config options, or None when it has none."""
    return next((option.current_value for option in options if option.id == "approvals"), None)


def _assert_every_tool_call_settled(wire: list[dict[str, Any]]) -> None:
    updates = [(index, update) for index, message in enumerate(wire) if (update := _update(message))]
    calls = [(index, update["toolCallId"]) for index, update in updates if update["sessionUpdate"] == "tool_call"]
    assert calls
    for shown, tool_call_id in calls:
        settled = [
            index
            for index, update in updates
            if update["sessionUpdate"] == "tool_call_update"
            and update["toolCallId"] == tool_call_id
            and update.get("status") in ("completed", "failed")
        ]
        assert any(index > shown for index in settled), tool_call_id


async def _session(connection, tmp_path) -> str:
    await connection.initialize(protocol_version=PROTOCOL_VERSION)
    return (await connection.new_session(cwd=str(tmp_path))).session_id


def _prompt(connection, session: str):
    return connection.prompt(session_id=session, prompt=[TextContentBlock(type="text", text="Write the notes.")])


async def test_nothing_for_a_session_precedes_its_new_session_response(connect, monkeypatch, tmp_path):
    respond = Connection._run_request

    async def slowly(self, message, handler=None):
        # A response that takes longer to write than the session's first update takes to be ready.
        if handler is not None:
            await asyncio.wait({handler})
        await asyncio.sleep(0.2)
        await respond(self, message, handler)

    monkeypatch.setattr(Connection, "_run_request", slowly)
    async with connect() as (connection, recorder):
        session = await _session(connection, tmp_path)
        await recorder.until(lambda message: _update(message, "config_option_update") is not None)
    wire = recorder.wire
    answered = _index(wire, lambda message: message.get("result", {}).get("sessionId") == session)
    first = _index(wire, lambda message: message.get("params", {}).get("sessionId") == session)
    assert answered < first


async def test_a_tool_call_is_shown_before_its_permission_request(connect, monkeypatch, tmp_path):
    provision(monkeypatch, [WRITE, {"text": "Done"}])
    send = AcpAgent._send

    async def slowly(self, session_id, update):
        # A tool_call that takes longer to send than the engine takes to ask about it.
        if isinstance(update, ToolCallStart):
            await asyncio.sleep(0.2)
        await send(self, session_id, update)

    monkeypatch.setattr(AcpAgent, "_send", slowly)
    async with connect("allow_once") as (connection, recorder):
        session = await _session(connection, tmp_path)
        response = await _prompt(connection, session)
    assert response.stop_reason == "end_turn"
    wire = recorder.wire
    asked = _index(wire, lambda message: message.get("method") == "session/request_permission")
    tool_call_id = wire[asked]["params"]["toolCall"]["toolCallId"]
    shown = _index(wire, lambda message: (_update(message, "tool_call") or {}).get("toolCallId") == tool_call_id)
    assert shown < asked


async def test_every_update_of_a_turn_precedes_its_prompt_response(connect, monkeypatch, tmp_path):
    provision(monkeypatch, [{"chunks": ["Writing ", "now."], **WRITE}, {"chunks": ["Do", "ne"]}])
    async with connect("allow_once") as (connection, recorder):
        session = await _session(connection, tmp_path)
        await recorder.until(lambda message: _update(message, "config_option_update") is not None)
        response = await _prompt(connection, session)
        # A response written after the prompt's: any turn update sent late is on the wire before it.
        await connection.close_session(session_id=session)
    assert response.stop_reason == "end_turn"
    wire = recorder.wire
    answered = _index(wire, lambda message: "stopReason" in message.get("result", {}))
    assert any(_update(message, "agent_message_chunk") for message in wire[:answered])
    assert any(_update(message, "tool_call_update") for message in wire[:answered])
    assert _update(wire[answered - 1], "session_info_update")
    assert not [message for message in wire[answered + 1 :] if _update(message)]


async def test_cancel_settles_every_tool_call_while_a_permission_request_is_open(connect, monkeypatch, tmp_path):
    provision(monkeypatch, [WRITE, {"text": "Done"}])
    async with connect(None) as (connection, recorder):
        session = await _session(connection, tmp_path)
        turn = asyncio.create_task(_prompt(connection, session))
        await recorder.until(lambda message: message.get("method") == "session/request_permission")
        await connection.cancel(session_id=session)
        response = await asyncio.wait_for(turn, 30)
    assert response.stop_reason == "cancelled"
    _assert_every_tool_call_settled(recorder.wire)
    assert not (tmp_path / "notes.txt").exists()


async def test_cancel_settles_every_tool_call_while_the_provider_works(connect, monkeypatch, tmp_path):
    provision(monkeypatch, [WRITE, {"block": True}])
    async with connect("allow_once") as (connection, recorder):
        session = await _session(connection, tmp_path)
        turn = asyncio.create_task(_prompt(connection, session))
        await recorder.until(lambda message: (_update(message, "tool_call_update") or {}).get("status") == "completed")
        await connection.cancel(session_id=session)
        response = await asyncio.wait_for(turn, 30)
    assert response.stop_reason == "cancelled"
    _assert_every_tool_call_settled(recorder.wire)


async def test_a_config_change_during_a_turn_is_busy(connect, monkeypatch, tmp_path):
    provision(monkeypatch, [WRITE, {"text": "Done"}])
    async with connect(None) as (connection, recorder):
        session = await _session(connection, tmp_path)
        turn = asyncio.create_task(_prompt(connection, session))
        await recorder.until(lambda message: message.get("method") == "session/request_permission")
        with pytest.raises(RequestError) as raised:
            await connection.set_config_option(config_id="model", session_id=session, value="anthropic/other")
        await connection.cancel(session_id=session)
        await asyncio.wait_for(turn, 30)
    assert raised.value.code == -32603
    assert raised.value.data == {"code": "busy", "category": "turn", "retryable": False}


async def test_stdin_closing_answers_the_prompt_in_flight_and_closes_its_session(connect, monkeypatch, tmp_path):
    provision(monkeypatch, [WRITE, {"block": True}])
    closed: list[str] = []
    close = _Held.close

    async def recorded(self):
        closed.append(self.session.info.session_id)
        await close(self)

    monkeypatch.setattr(_Held, "close", recorded)
    async with connect("allow_once") as (connection, recorder):
        session = await _session(connection, tmp_path)
        turn = asyncio.create_task(_prompt(connection, session))
        await recorder.until(lambda message: (_update(message, "tool_call_update") or {}).get("status") == "completed")
        assert recorder.stdin is not None
        assert recorder.face is not None
        recorder.stdin.write_eof()
        response = await asyncio.wait_for(turn, 30)
        await asyncio.wait_for(recorder.face, 30)
    assert response.stop_reason == "cancelled"
    assert closed == [session]


async def test_allow_always_answers_later_matching_requests_of_the_session(connect, monkeypatch, tmp_path):
    other = {"tool": {"name": "write_file", "arguments": {"file_path": "other.txt", "content": "hi\n"}}}
    provision(monkeypatch, [WRITE, other, {"text": "Done"}])
    async with connect("allow_always") as (connection, recorder):
        session = await _session(connection, tmp_path)
        response = await _prompt(connection, session)
    assert response.stop_reason == "end_turn"
    asked = [message for message in recorder.wire if message.get("method") == "session/request_permission"]
    assert len(asked) == 1
    assert (tmp_path / "notes.txt").exists()
    assert (tmp_path / "other.txt").exists()


async def test_a_host_allow_policy_asks_nothing(connect, isolated_host, monkeypatch, tmp_path):
    isolated_host.write_text('{"approvals": "allow"}')
    provision(monkeypatch, [WRITE, {"text": "Done"}])
    async with connect(None) as (connection, recorder):
        session = await _session(connection, tmp_path)
        response = await asyncio.wait_for(_prompt(connection, session), 30)
    assert response.stop_reason == "end_turn"
    assert not [message for message in recorder.wire if message.get("method") == "session/request_permission"]
    settled = [
        update
        for message in recorder.wire
        if (update := _update(message, "tool_call_update")) and update.get("status") == "completed"
    ]
    assert settled[0]["content"][0]["type"] == "diff"


async def test_allow_reads_runs_a_read_inside_the_project_and_asks_for_an_edit(connect, monkeypatch, tmp_path):
    (tmp_path / "notes.txt").write_text("old\n")
    provision(monkeypatch, [READ, WRITE, {"text": "Done"}])
    async with connect("allow_once") as (connection, recorder):
        await connection.initialize(protocol_version=PROTOCOL_VERSION)
        opened = await connection.new_session(cwd=str(tmp_path))
        response = await _prompt(connection, opened.session_id)
    assert response.stop_reason == "end_turn"
    approvals = next(option for option in opened.config_options if option.id == "approvals")
    assert (approvals.category, approvals.current_value) == ("mode", "allow-reads")
    assert [choice.value for choice in approvals.options] == ["ask", "allow-reads", "allow-all"]
    wire = recorder.wire
    kinds = {update["toolCallId"]: update["kind"] for message in wire if (update := _update(message, "tool_call"))}
    assert sorted(kinds.values()) == ["edit", "read"]
    assert [kinds[tool_call_id] for tool_call_id in _asked(wire)] == ["edit"]
    read = next(tool_call_id for tool_call_id, kind in kinds.items() if kind == "read")
    shown = _index(wire, lambda message: (_update(message, "tool_call") or {}).get("toolCallId") == read)
    allowed = _index(
        wire,
        lambda message: (
            (_update(message, "tool_call_update") or {}).get("toolCallId") == read
            and message["params"]["update"].get("status") == "in_progress"
        ),
    )
    assert shown < allowed
    _assert_every_tool_call_settled(wire)


@pytest.mark.parametrize(
    "tool",
    [
        pytest.param(lambda outside: {"name": "read_file", "arguments": {"file_path": str(outside)}}, id="absolute"),
        pytest.param(lambda outside: {"name": "read_file", "arguments": {"file_path": "../secret.txt"}}, id="parent"),
        pytest.param(lambda outside: {"name": "read_file", "arguments": {"file_path": "link.txt"}}, id="symlink"),
        pytest.param(lambda outside: {"name": "glob", "arguments": {"pattern": "../*.txt"}}, id="glob pattern"),
        pytest.param(
            lambda outside: {"name": "grep", "arguments": {"pattern": "secret", "path": str(outside.parent)}},
            id="grep path",
        ),
    ],
)
async def test_allow_reads_asks_for_a_read_outside_the_project(connect, monkeypatch, tmp_path, tool):
    project, outside = tmp_path / "project", tmp_path / "secret.txt"
    project.mkdir()
    outside.write_text("secret\n")
    (project / "link.txt").symlink_to(outside)
    provision(monkeypatch, [{"tool": tool(outside)}, {"text": "Done"}])
    async with connect("allow_once") as (connection, recorder):
        session = await _session(connection, project)
        response = await _prompt(connection, session)
    assert response.stop_reason == "end_turn"
    assert len(_asked(recorder.wire)) == 1


async def test_ask_mode_asks_for_a_read(connect, monkeypatch, tmp_path):
    (tmp_path / "notes.txt").write_text("old\n")
    provision(monkeypatch, [READ, {"text": "Done"}])
    async with connect("allow_once") as (connection, recorder):
        session = await _session(connection, tmp_path)
        changed = await connection.set_config_option(config_id="approvals", session_id=session, value="ask")
        response = await _prompt(connection, session)
    assert response.stop_reason == "end_turn"
    assert _approvals(changed.config_options) == "ask"
    assert len(_asked(recorder.wire)) == 1


async def test_allow_all_asks_nothing(connect, monkeypatch, tmp_path):
    provision(monkeypatch, [WRITE, {"text": "Done"}])
    async with connect(None) as (connection, recorder):
        session = await _session(connection, tmp_path)
        await connection.set_config_option(config_id="approvals", session_id=session, value="allow-all")
        response = await asyncio.wait_for(_prompt(connection, session), 30)
    assert response.stop_reason == "end_turn"
    assert not _asked(recorder.wire)
    assert (tmp_path / "notes.txt").read_text() == "hello\n"


async def test_an_approvals_change_during_a_turn_applies_to_its_next_request(connect, monkeypatch, tmp_path):
    provision(monkeypatch, [WRITE, OTHER, {"text": "Done"}])
    async with connect(None) as (connection, recorder):
        session = await _session(connection, tmp_path)
        turn = asyncio.create_task(_prompt(connection, session))
        await recorder.until(lambda message: message.get("method") == "session/request_permission")
        changed = await connection.set_config_option(config_id="approvals", session_id=session, value="allow-all")
        recorder.answer = "allow_once"
        recorder.release.set()
        response = await asyncio.wait_for(turn, 30)
    assert response.stop_reason == "end_turn"
    assert _approvals(changed.config_options) == "allow-all"
    assert len(_asked(recorder.wire)) == 1
    assert (tmp_path / "other.txt").exists()

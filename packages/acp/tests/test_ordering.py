import asyncio
from typing import Any

from acp import PROTOCOL_VERSION
from acp.connection import Connection
from acp.schema import TextContentBlock, ToolCallStart
from amplifier_agent_acp._agent import AcpAgent

from tests.support.engine import provision

WRITE = {"tool": {"name": "write_file", "arguments": {"file_path": "notes.txt", "content": "hello\n"}}}


def _update(message: dict[str, Any], kind: str | None = None) -> dict[str, Any] | None:
    if message.get("method") != "session/update":
        return None
    update = message["params"]["update"]
    return update if kind is None or update["sessionUpdate"] == kind else None


def _index(wire: list[dict[str, Any]], found) -> int:
    return next(index for index, message in enumerate(wire) if found(message))


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

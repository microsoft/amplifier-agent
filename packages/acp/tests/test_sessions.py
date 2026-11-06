from datetime import datetime
from typing import Any

from acp import PROTOCOL_VERSION
from acp.schema import TextContentBlock

from tests.support.engine import provision

WRITE = {"tool": {"name": "write_file", "arguments": {"file_path": "notes.txt", "content": "hello\n"}}}
# Updates a replay has no counterpart for: usage, the model list, the session's title, and an approval's progress.
_UNREPLAYED = ("usage_update", "config_option_update", "session_info_update")


def _updates(wire: list[dict[str, Any]], session: str) -> list[dict[str, Any]]:
    """The updates sent for `session`, without those a replay does not repeat, and without diffs from snapshots."""
    shown = []
    for message in wire:
        if message.get("method") != "session/update" or message["params"]["sessionId"] != session:
            continue
        update = dict(message["params"]["update"])
        if update["sessionUpdate"] in _UNREPLAYED or update.get("status") == "in_progress":
            continue
        if update["sessionUpdate"] == "tool_call_update":
            update.pop("content", None)
        shown.append(update)
    return shown


def _prompt(connection, session: str):
    return connection.prompt(session_id=session, prompt=[TextContentBlock(type="text", text="Write the notes.")])


async def test_load_replays_a_turn_as_its_live_updates(connect, monkeypatch, tmp_path):
    provision(monkeypatch, [WRITE, {"text": "Done"}])
    async with connect("allow_once") as (connection, recorder):
        await connection.initialize(protocol_version=PROTOCOL_VERSION)
        session = (await connection.new_session(cwd=str(tmp_path))).session_id
        start = len(recorder.wire)
        await _prompt(connection, session)
        live = _updates(recorder.wire[start:], session)
        await connection.close_session(session_id=session)
        start = len(recorder.wire)
        await connection.load_session(cwd=str(tmp_path), session_id=session)
        replayed = _updates(recorder.wire[start:], session)
    assert replayed[0] == {
        "sessionUpdate": "user_message_chunk",
        "content": {"type": "text", "text": "Write the notes."},
    }
    assert [update["sessionUpdate"] for update in live] == ["tool_call", "tool_call_update", "agent_message_chunk"]
    assert replayed[1:] == live


async def test_list_answers_the_sessions_of_one_directory_newest_first(connect, monkeypatch, tmp_path):
    here, elsewhere = tmp_path / "here", tmp_path / "elsewhere"
    here.mkdir()
    elsewhere.mkdir()
    provision(monkeypatch, [{"text": "Done"}, {"text": "Again"}])
    async with connect() as (connection, recorder):
        await connection.initialize(protocol_version=PROTOCOL_VERSION)
        first = (await connection.new_session(cwd=str(here))).session_id
        second = (await connection.new_session(cwd=str(here))).session_id
        await connection.new_session(cwd=str(elsewhere))
        await _prompt(connection, first)
        listed = await connection.list_sessions(cwd=str(here))
        # The listing leaves the session this process holds working.
        again = await _prompt(connection, first)
    assert again.stop_reason == "end_turn"
    assert listed.next_cursor is None
    assert [(info.session_id, info.cwd) for info in listed.sessions] == [(first, str(here)), (second, str(here))]
    assert listed.sessions[0].title == "Write the notes."
    assert listed.sessions[0].updated_at is not None
    assert datetime.fromisoformat(listed.sessions[0].updated_at).tzinfo is not None
    info = next(
        message["params"]["update"]
        for message in recorder.wire
        if message.get("method") == "session/update"
        and message["params"]["update"]["sessionUpdate"] == "session_info_update"
    )
    assert info["title"] == "Write the notes."

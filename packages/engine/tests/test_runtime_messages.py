import asyncio

from amplifier_agent_engine._records import AgentError
from amplifier_agent_engine._runtime.server import MESSAGE_LIMIT, RuntimeServer

LIMIT = 64


def server_with_capture(monkeypatch):
    server = RuntimeServer(message_limit=LIMIT)
    messages = []

    async def capture(value):
        messages.append(value)

    monkeypatch.setattr(server, "send", capture)
    return server, messages


async def read(server, *lines):
    reader = asyncio.StreamReader(limit=LIMIT)
    reader.feed_data(b"".join(lines))
    reader.feed_eof()
    await server.read(reader)
    await asyncio.gather(*server.tasks)


def test_the_default_limit_fits_any_provider_request():
    assert MESSAGE_LIMIT == 512 * 1024 * 1024


async def test_an_oversized_request_fails_by_its_id_and_reading_continues(monkeypatch):
    server, messages = server_with_capture(monkeypatch)
    oversized = b'{"id":"7","method":"session.run","params":{"input":"' + b"x" * 200 + b'"}}\n'
    await read(server, oversized, b'{"id":"8","method":"no.such"}\n')
    refused, unknown = messages
    assert refused["id"] == "7"
    error = refused["error"]
    assert isinstance(error, AgentError)
    assert error.code == "invalid_input"
    assert error.details == {"bytes": len(oversized) - 1, "limit": LIMIT}
    assert f"{len(oversized) - 1} bytes" in error.message
    assert error.remedy
    assert unknown["id"] == "8"
    assert "no.such" in unknown["error"].message


async def test_an_oversized_tool_result_fails_its_callback(monkeypatch):
    server, messages = server_with_capture(monkeypatch)
    future = asyncio.get_running_loop().create_future()
    server.callbacks["cb-1"] = future
    server.callback_context["cb-1"] = {"call_id": "call-1"}
    line = b'{"method":"callback.resolve","params":{"callback_id":"cb-1","result":"' + b"x" * 200 + b'"}}\n'
    await read(server, line)
    assert messages == []
    assert future.done()
    error = server.callback_errors["cb-1"]
    assert error.code == "tool_result_invalid"
    assert error.correlation_id == "call-1"


async def test_an_unidentified_oversized_message_is_refused_without_ending_the_reader(monkeypatch):
    server, messages = server_with_capture(monkeypatch)
    await read(server, b"[" + b"1," * 100 + b"1]\n", b'{"id":"9","method":"no.such"}\n')
    refused, unknown = messages
    assert "id" not in refused
    assert refused["error"].code == "invalid_input"
    assert unknown["id"] == "9"

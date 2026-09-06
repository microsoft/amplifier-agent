import asyncio

import pytest
from amplifier_agent_engine._records import AgentError, ToolFailed, ToolOutcomeUnknown
from amplifier_agent_engine._runtime.server import RuntimeServer


async def callback(monkeypatch, kind):
    server = RuntimeServer()
    frames = []
    sent = asyncio.Event()

    async def capture(frame):
        frames.append(frame)
        sent.set()

    monkeypatch.setattr(server, "send", capture)
    field = "call_id" if kind == "tool" else "request_id"
    args = {"context" if kind == "tool" else "request": {field: "original"}}
    pending = asyncio.create_task(server.callback(kind, args))
    await asyncio.wait_for(sent.wait(), 2)
    reply = {"callback_id": frames[0]["callback_id"], field: "original", "result": "done"}
    return server, frames, pending, reply


@pytest.mark.parametrize("kind", ["tool", "approval"])
async def test_wrong_callback_correlation_is_a_named_failure(monkeypatch, kind):
    server, _, pending, reply = await callback(monkeypatch, kind)
    reply["call_id" if kind == "tool" else "request_id"] = "different"
    await server.dispatch({"method": "callback.resolve", "params": reply})
    with pytest.raises(AgentError) as caught:
        await asyncio.wait_for(pending, 2)
    assert caught.value.code == ("tool_result_invalid" if kind == "tool" else "approval_invalid")
    assert caught.value.correlation_id == "original"
    assert not (server.callbacks or server.callback_errors or server.callback_context)


@pytest.mark.parametrize("kind", ["tool", "approval"])
async def test_second_resolution_before_settlement_is_invalid(monkeypatch, kind):
    server, _, pending, reply = await callback(monkeypatch, kind)
    await server.dispatch({"method": "callback.resolve", "params": reply})
    await server.dispatch({"method": "callback.resolve", "params": reply})
    with pytest.raises(AgentError) as caught:
        await asyncio.wait_for(pending, 2)
    assert caught.value.code == ("tool_result_invalid" if kind == "tool" else "approval_invalid")
    assert not (server.callbacks or server.callback_errors or server.callback_context)


@pytest.mark.parametrize("kind", ["tool", "approval"])
async def test_late_resolution_has_no_effect(monkeypatch, kind):
    server, _, pending, reply = await callback(monkeypatch, kind)
    await server.dispatch({"method": "callback.resolve", "params": reply})
    assert await asyncio.wait_for(pending, 2) == "done"
    await server.dispatch({"method": "callback.resolve", "params": {**reply, "result": "late"}})
    assert not (server.callbacks or server.callback_errors or server.callback_context)


async def test_cancelled_callback_awaiter_preserves_actual_completion(monkeypatch):
    server, frames, pending, reply = await callback(monkeypatch, "tool")
    pending.cancel()
    await asyncio.sleep(0)
    assert not pending.done()
    assert frames[-1]["event"] == "callback_cancel"
    await server.dispatch({"method": "callback.resolve", "params": reply})
    assert await asyncio.wait_for(pending, 2) == "done"


@pytest.mark.parametrize("kind,error,expected", [
    ("tool", "tool_failed", ToolFailed),
    ("tool", "tool_completion_unknown", ToolOutcomeUnknown),
    ("approval", "approval_unavailable", AgentError),
])
async def test_callback_failure_keeps_its_authoritative_kind(monkeypatch, kind, error, expected):
    server, _, pending, reply = await callback(monkeypatch, kind)
    reply.pop("result")
    reply["error"] = {"kind": error, "message": "The executor disconnected."}
    await server.dispatch({"method": "callback.resolve", "params": reply})
    with pytest.raises(expected) as caught:
        await asyncio.wait_for(pending, 2)
    if isinstance(caught.value, AgentError):
        assert caught.value.code == error

import asyncio
import copy

import pytest
from amplifier_agent_engine._engine.configuration import resolve
from amplifier_agent_engine._engine.state import EngineAgent
from amplifier_agent_engine._engine.storage import storage_error
from amplifier_agent_engine._records import AgentError, AgentOptions, TextPart, TurnInput


class CheckpointRuntime:
    def __init__(self):
        self.messages = []
        self.block_snapshot = False
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = False

    async def execute(self, input, observer):
        self.messages.extend(part.text for part in input.content)
        observer.output("Reply")

    async def settle(self, resolutions):
        pass

    async def snapshot(self):
        if self.block_snapshot:
            self.entered.set()
            await self.release.wait()
        return {"messages": copy.deepcopy(self.messages)}

    async def restore(self, snapshot):
        self.messages = copy.deepcopy(snapshot["messages"])

    def request_cancel(self):
        pass

    async def close(self):
        self.closed = True


def agent(tmp_path, runtime):
    async def factory():
        return CheckpointRuntime()

    return EngineAgent(resolve(AgentOptions(storage=tmp_path)), runtime, factory)


async def test_failed_checkpoint_never_reports_success_and_retains_previous_commit(
    monkeypatch, tmp_path
):
    runtime = CheckpointRuntime()
    first = agent(tmp_path, runtime)
    session = await first.create_session()
    session_id = session.info.session_id
    try:
        assert (await session.run(TurnInput([TextPart("Committed")]))).state == "success"
        history = session.history

        def fail(*args, **kwargs):
            raise storage_error()

        monkeypatch.setattr(first._store, "save", fail)
        failed = await session.run(TurnInput([TextPart("Uncommitted")]))
        assert failed.state == "failure"
        assert failed.error.code == "internal_failed"
        assert session.history[-1].result == failed
        with pytest.raises(AgentError) as error:
            await session.start_turn(TurnInput([TextPart("Cannot continue")]))
        assert error.value.code == "internal_failed"
    finally:
        await first.close()
    restored_runtime = CheckpointRuntime()
    restored = agent(tmp_path, restored_runtime)
    try:
        resumed = await restored.resume_session(session_id)
        assert resumed.history == history
        assert restored_runtime.messages == ["Committed"]
    finally:
        await restored.close()


@pytest.mark.parametrize("fail_checkpoint", [False, True])
async def test_cancellation_during_checkpoint_keeps_cancelled_terminal(
    monkeypatch, tmp_path, fail_checkpoint
):
    runtime = CheckpointRuntime()
    owner = agent(tmp_path, runtime)
    session = await owner.create_session()
    session_id = session.info.session_id
    runtime.block_snapshot = True
    if fail_checkpoint:

        def fail(*args, **kwargs):
            raise storage_error()

        monkeypatch.setattr(owner._store, "save", fail)
    turn = await session.start_turn(TurnInput([TextPart("Input")]))
    cancelling = None
    try:
        await asyncio.wait_for(runtime.entered.wait(), 2)
        cancelling = asyncio.create_task(turn.cancel())
        await asyncio.sleep(0)
        runtime.release.set()
        await asyncio.wait_for(cancelling, 2)
        events = [event async for event in turn.events()]
        result = events[-1].payload
        assert result.state == "cancelled"
        assert result.error.code == "turn_cancelled"
        assert session.history[-1].result == result
        if fail_checkpoint:
            assert result.error.details["persistence_error"]["code"] == "internal_failed"
        await turn.cancel()
    finally:
        runtime.release.set()
        await owner.close()
        if cancelling is not None:
            await asyncio.gather(cancelling, return_exceptions=True)
    restored = agent(tmp_path, CheckpointRuntime())
    try:
        resumed = await restored.resume_session(session_id)
        if fail_checkpoint:
            assert resumed.history == []
        else:
            assert resumed.history[-1].result == result
    finally:
        await restored.close()


async def test_agent_close_during_fork_snapshot_does_not_deadlock_or_create_child(tmp_path):
    runtime = CheckpointRuntime()
    owner = agent(tmp_path, runtime)
    session = await owner.create_session()
    runtime.block_snapshot = True
    forking = asyncio.create_task(session.fork())
    closing = None
    try:
        await asyncio.wait_for(runtime.entered.wait(), 2)
        closing = asyncio.create_task(owner.close())
        await asyncio.sleep(0)
        runtime.release.set()
        await asyncio.wait_for(closing, 2)
        with pytest.raises(AgentError) as error:
            await asyncio.wait_for(forking, 2)
        assert error.value.code == "closed"
        assert runtime.closed
        assert len(owner._store.list()) == 1
    finally:
        runtime.release.set()
        await owner.close()
        await asyncio.gather(forking, return_exceptions=True)
        if closing is not None:
            await asyncio.gather(closing, return_exceptions=True)

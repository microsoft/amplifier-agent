"""Prepared engine authority reaches the actual disposable Bash spawn boundary."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace

from amplifier_agent_engine._engine.builtin_tools import PipeDrains, bash_tool
from amplifier_agent_engine._engine.configuration import ResolvedConfig
from amplifier_agent_engine._engine.effects import PolicyStop, RecoveryState, execute_tool
from amplifier_agent_engine._engine.tools import SCHEMA, RegisteredTool, ToolRegistry
from amplifier_agent_engine._records import ApprovalResponse, ToolContext, _ToolNotExecutedError
import pytest


def runtime_at(path):
    config = ResolvedConfig(
        "fixture",
        "fixture",
        None,
        (),
        None,
        path / "sessions",
        {},
        None,
        None,
        working_directory=path,
        environment={"FIXTURE_VALUE": "original"},
        builtin_tools=("bash",),
    )
    runtime = SimpleNamespace(config=config, registry=ToolRegistry(), pipe_drains=PipeDrains())
    runtime.registry.add(bash_tool(runtime))
    return runtime


class Turn:
    def __init__(self):
        self.cancelled = False
        self.recovery = RecoveryState()
        self.effect_call_ids = set()
        self.tool_parts = {}
        self.events = []

    def emit(self, name, payload):
        self.events.append((name, payload))

    def stop(self, state, error):
        return PolicyStop(error)


def prepared(runtime, arguments):
    tool = runtime.registry.tools["bash"]
    assert tool.prepare is not None
    invocation = tool.prepare(arguments, lambda: True)
    assert invocation is not None
    return invocation


def test_scope_matches_across_invocations_without_session_or_request_ids(tmp_path):
    first, second = runtime_at(tmp_path), runtime_at(tmp_path)
    arguments = {"command": "printf fixture"}
    assert prepared(first, arguments).approval == prepared(second, arguments).approval
    assert "original" not in prepared(first, arguments).approval.description


@pytest.mark.parametrize(
    "change", ["command", "timeout", "background", "environment", "directory", "registry", "configuration"]
)
def test_changed_direct_authority_changes_key(tmp_path, change):
    runtime = runtime_at(tmp_path)
    arguments = {"command": "printf fixture"}
    before = prepared(runtime, arguments).approval.key
    if change == "command":
        arguments["command"] = "printf different"
    elif change == "timeout":
        arguments["timeout"] = 31
    elif change == "background":
        arguments["run_in_background"] = True
    elif change == "environment":
        runtime.config.environment["FIXTURE_VALUE"] = "changed"
    elif change == "directory":
        directory = tmp_path / "other"
        directory.mkdir()
        runtime = runtime_at(directory)
    elif change == "registry":

        async def handler(arguments, context):
            return "Unused fixture"

        runtime.registry.add(RegisteredTool("other", "Other", {"$schema": SCHEMA}, handler, "caller"))
    else:
        runtime.config = replace(runtime.config, tool_result_max_bytes=15)
    assert prepared(runtime, arguments).approval.key != before


async def test_prepared_bash_runs_once_with_frozen_arguments_and_refuses_reentry(tmp_path):
    runtime = runtime_at(tmp_path)
    arguments = {"command": 'printf %s "$FIXTURE_VALUE" >> receipt.txt'}
    invocation = prepared(runtime, arguments)
    arguments["command"] = "printf wrong > wrong.txt"
    try:
        await asyncio.wait_for(invocation.handler({}, ToolContext("one")), 5)
        assert (tmp_path / "receipt.txt").read_text() == "original"
        assert not (tmp_path / "wrong.txt").exists()
        with pytest.raises(_ToolNotExecutedError, match="changed or expired"):
            await asyncio.wait_for(invocation.handler({}, ToolContext("two")), 5)
        assert (tmp_path / "receipt.txt").read_text() == "original"
    finally:
        await runtime.pipe_drains.close()


async def test_environment_drift_during_approval_refuses_before_effect(tmp_path):
    runtime = runtime_at(tmp_path)
    observed = []

    async def approve(request):
        observed.append(request)
        runtime.config.environment["FIXTURE_VALUE"] = "changed"
        return ApprovalResponse("allow")

    turn = Turn()
    try:
        with pytest.raises(PolicyStop):
            await execute_tool(
                turn,
                replace(runtime.config, approvals=approve),
                runtime.registry.tools["bash"],
                "one",
                {"command": "printf forbidden > receipt.txt"},
            )
        assert observed[0].reusable_approval is not None
        assert not (tmp_path / "receipt.txt").exists()
        result = next(payload.resolution for name, payload in turn.events if name == "tool_result")
        assert result.outcome == "cancelled"
    finally:
        await runtime.pipe_drains.close()


@pytest.mark.parametrize("change", ["environment", "cancelled", "unknown"])
async def test_queued_spawn_rechecks_after_approval_and_scheduling(tmp_path, monkeypatch, change):
    runtime, turn = runtime_at(tmp_path), Turn()
    entered, release = asyncio.Event(), asyncio.Event()
    create_task = asyncio.create_task

    def schedule(coroutine, **kwargs):
        if coroutine.cr_code.co_name != "spawn":
            return create_task(coroutine, **kwargs)

        async def held():
            entered.set()
            await release.wait()
            return await coroutine

        return create_task(held(), **kwargs)

    monkeypatch.setattr(asyncio, "create_task", schedule)
    operation = create_task(
        execute_tool(
            turn,
            replace(runtime.config, approvals="allow"),
            runtime.registry.tools["bash"],
            "one",
            {"command": "printf forbidden > receipt.txt"},
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), 5)
        if change == "environment":
            runtime.config.environment["FIXTURE_VALUE"] = "changed"
        elif change == "cancelled":
            turn.cancelled = True
        else:
            turn.recovery.uncertain_call = "earlier-unknown"
        release.set()
        with pytest.raises(asyncio.CancelledError if change == "cancelled" else PolicyStop):
            await asyncio.wait_for(operation, 5)
        assert not (tmp_path / "receipt.txt").exists()
    finally:
        release.set()
        await asyncio.gather(operation, return_exceptions=True)
        await runtime.pipe_drains.close()


@pytest.mark.parametrize("source", ["caller", "mcp"])
async def test_same_named_unowned_tool_cannot_supply_reusable_scope(tmp_path, source):
    requests = []

    async def approve(request):
        requests.append(request)
        return ApprovalResponse("allow")

    async def handler(arguments, context):
        return "scripted"

    runtime = runtime_at(tmp_path)
    tool = RegisteredTool("bash", "Unowned", {"$schema": SCHEMA, "type": "object"}, handler, source)
    result = await execute_tool(Turn(), replace(runtime.config, approvals=approve), tool, "one", {"command": "same"})
    assert result.outcome == "completed"
    assert requests[0].reusable_approval is None


@pytest.mark.parametrize("background", [False, True])
async def test_replaced_working_directory_refuses_foreground_and_background_spawn(tmp_path, background):
    directory = tmp_path / "work"
    directory.mkdir()
    runtime = runtime_at(directory)
    invocation = prepared(runtime, {"command": "printf forbidden > receipt.txt", "run_in_background": background})
    directory.rename(tmp_path / "previous")
    directory.mkdir()
    try:
        with pytest.raises(_ToolNotExecutedError, match="changed or expired"):
            await asyncio.wait_for(invocation.handler({}, ToolContext("call")), 5)
        assert not (directory / "receipt.txt").exists()
        assert not (tmp_path / "previous" / "receipt.txt").exists()
    finally:
        await runtime.pipe_drains.close()


async def test_loaded_executor_scope_does_not_require_packaged_python_source_files(tmp_path, monkeypatch):
    import sys

    from amplifier_agent_engine._engine import bash_approval, builtin_tools
    from amplifier_module_tool_bash import BashTool

    runtime = runtime_at(tmp_path)
    for module in (bash_approval, builtin_tools, sys.modules[BashTool.__module__]):
        monkeypatch.setattr(module, "__file__", str(tmp_path / "not-shipped.py"))
    invocation = prepared(runtime, {"command": "printf approved > receipt.txt"})
    try:
        await asyncio.wait_for(invocation.handler({}, ToolContext("call")), 5)
        assert (tmp_path / "receipt.txt").read_text() == "approved"
    finally:
        await runtime.pipe_drains.close()

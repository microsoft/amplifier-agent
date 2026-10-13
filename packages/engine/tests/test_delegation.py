"""The delegate tool: tool-name recovery, concurrent delegations, and whole reasoning blocks from delegated work."""

import asyncio
import os
from typing import Any

from amplifier_agent_engine._engine import assembly
from amplifier_agent_engine._engine.adapters import DelegatedObserver
from amplifier_agent_engine._records import AgentOptions, SessionOptions, TextPart, TurnInput
from amplifier_core.message_models import ChatResponse
import pytest

from tests.support.engine import provision_many
from tests.support.scripted_provider import ScriptedFactory


@pytest.fixture(autouse=True)
def host(monkeypatch, tmp_path):
    for key in os.environ:
        if key.startswith("AMPLIFIER_AGENT_"):
            monkeypatch.delenv(key)
    path = tmp_path / "host.json"
    path.write_text("{}")
    monkeypatch.setenv("AMPLIFIER_AGENT_CONFIG", str(path))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


async def run_turn(tools: list[Any]) -> list[Any]:
    agent = await assembly.create_engine(
        AgentOptions(provider="anthropic", model="claude-sonnet-5", tools=tools, approvals="allow")
    )
    try:
        session = await agent.create_session(SessionOptions(persistence="ephemeral"))
        turn = await session.start_turn(TurnInput([TextPart("Delegate")]))
        return [event async for event in turn.events()]
    finally:
        await agent.close()


def delegate_results(events: list[Any]) -> list[Any]:
    calls = {
        event.payload.call.call_id
        for event in events
        if event.type == "tool_call" and event.payload.call.name == "delegate"
    }
    return [
        event.payload.resolution
        for event in events
        if event.type == "tool_result" and event.payload.resolution.call_id in calls
    ]


def offered_tools(request: dict[str, Any]) -> list[str]:
    return sorted(tool["name"] for tool in request.get("tools") or [])


async def test_functions_prefixed_tool_names_are_accepted(monkeypatch):
    arguments = {"instruction": "Count files.", "tools": ["functions.read_file", "read_file", "functions.glob"]}
    probes = provision_many(
        monkeypatch,
        [{"tool": {"name": "delegate", "arguments": arguments}}, {"text": "Parent complete"}],
        [{"text": "Child complete"}],
    )
    events = await run_turn(["delegate", "glob", "read_file", "bash"])

    assert events[-1].payload.state == "success"
    [result] = delegate_results(events)
    assert result.outcome == "completed"
    assert result.content == "Child complete"
    assert offered_tools(probes[1].requests[0]) == ["glob", "read_file"]


async def test_an_unknown_tool_name_fails_the_call_and_lists_the_available_tools(monkeypatch):
    arguments = {"instruction": "Count files.", "tools": ["functions.read_file", "shell"]}
    probes = provision_many(
        monkeypatch,
        [{"tool": {"name": "delegate", "arguments": arguments}}, {"text": "Parent complete"}],
    )
    events = await run_turn(["delegate", "glob", "read_file"])

    assert events[-1].payload.state == "success"
    [result] = delegate_results(events)
    assert result.outcome == "failed"
    assert result.error.code == "tool_failed"
    assert "shell" in result.error.message
    assert "Available tools: glob, read_file." in result.error.message
    assert len(probes) == 1


async def test_at_most_four_delegations_run_at_the_same_time(monkeypatch):
    running = 0
    peak = 0
    providers = 0

    async def factory(config: Any, coordinator: Any) -> Any:
        nonlocal providers
        if coordinator.session_id.startswith("probe-"):
            return await ScriptedFactory(None)(config, coordinator)
        providers += 1
        if providers == 1:
            calls = [{"name": "delegate", "arguments": {"instruction": f"Task {index}."}} for index in range(5)]
            return await ScriptedFactory([{"tools": calls}, {"text": "Parent complete"}])(config, coordinator)
        provider = await ScriptedFactory([{"text": "Child complete"}])(config, coordinator)
        complete = provider.complete

        async def tracked(request: Any, **kwargs: Any) -> ChatResponse:
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            try:
                await asyncio.sleep(0.05)
                return await complete(request, **kwargs)
            finally:
                running -= 1

        monkeypatch.setattr(provider, "complete", tracked)
        return provider

    monkeypatch.setattr(assembly, "_provider_factory", factory)
    events = await run_turn(["delegate", "read_file"])

    assert events[-1].payload.state == "success"
    results = delegate_results(events)
    assert [result.outcome for result in results] == ["completed"] * 5
    assert providers == 6
    assert peak == 4


class ReasoningParent:
    """Records reasoning as a turn emits it: each final carries the deltas since the previous final."""

    reasoning_ceiling = "high"

    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []
        self._buffer: list[str] = []

    def reasoning(self, text: str, *, final: bool = False) -> None:
        if final:
            self.events.append(("reasoning_final", "".join(self._buffer)))
            self._buffer.clear()
        else:
            self._buffer.append(text)
            self.events.append(("reasoning_delta", text))


def test_concurrent_children_send_whole_reasoning_blocks():
    parent: Any = ReasoningParent()
    first = DelegatedObserver(parent, "claude-sonnet-5", None)
    second = DelegatedObserver(parent, "claude-sonnet-5", None)

    first.reasoning("First ")
    second.reasoning("Second ")
    first.reasoning("thought.")
    second.reasoning("idea.")
    second.reasoning("", final=True)
    first.reasoning("", final=True)

    assert parent.events == [
        ("reasoning_delta", "Second idea."),
        ("reasoning_final", "Second idea."),
        ("reasoning_delta", "First thought."),
        ("reasoning_final", "First thought."),
    ]


def test_an_unclosed_reasoning_block_is_forwarded_when_the_child_ends():
    parent: Any = ReasoningParent()
    child = DelegatedObserver(parent, "claude-sonnet-5", None)

    child.reasoning("Partial")
    child.flush_reasoning()
    child.flush_reasoning()

    assert parent.events == [("reasoning_delta", "Partial"), ("reasoning_final", "Partial")]

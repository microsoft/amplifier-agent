"""Replacing a session's provider and model between turns.

agent-interface.v1 sections 1, 3, and 5: session.set_model, SessionRecord provider, model,
and reasoning_effort, resume on the saved selection, fork on the current selection, and
explicit selections honored. A recording provider stands in for every provider the engine
builds and records which provider and model each request reached.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import json
import os
from typing import Any

from amplifier_agent_engine._engine import assembly
from amplifier_agent_engine._engine.providers import create_provider
from amplifier_agent_engine._records import AgentError, AgentOptions, SessionOptions, TextPart, TurnInput
from amplifier_core.message_models import ChatResponse, TextBlock, ThinkingBlock, Usage
import pytest

from tests.support.scripted_provider import ScriptedFactory, ScriptedProvider

SIGNATURE = "opaque-reasoning-seal"


@pytest.fixture(autouse=True)
def host(monkeypatch, tmp_path):
    for key in os.environ:
        if key.startswith("AMPLIFIER_AGENT_"):
            monkeypatch.delenv(key)
    path = tmp_path / "host.json"
    path.write_text("{}")
    monkeypatch.setenv("AMPLIFIER_AGENT_CONFIG", str(path))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return path


class RecordingProvider(ScriptedProvider):
    def __init__(self, owner: "Providers", config: Any, coordinator: Any) -> None:
        super().__init__(ScriptedFactory(), config.model, coordinator)
        self.owner, self.provider_id = owner, config.provider
        self.name = config.provider

    async def complete(self, request: Any, **kwargs: Any) -> ChatResponse:
        payload = request.model_dump(mode="json")
        self.owner.requests.append((self.provider_id, kwargs.get("model", self.model), payload))
        self.owner.entered.set()
        if self.owner.gate is not None:
            await self.owner.gate.wait()
        content: list[Any] = [TextBlock(text="Reply")]
        reasoning = self.owner.reasoning.get(self.provider_id)
        if reasoning is not None:
            content.insert(0, reasoning)
        return ChatResponse(content=content, usage=Usage(input_tokens=7, output_tokens=2, total_tokens=9))


class Providers:
    """Every provider the engine builds, the requests each received, and an optional gate holding them."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, dict[str, Any]]] = []
        self.entered = asyncio.Event()
        self.gate: asyncio.Event | None = None
        # Reasoning data a provider attaches to each of its answers.
        self.reasoning: dict[str, ThinkingBlock] = {}
        # Providers built by the real factory, so their credential checks run.
        self.real: set[str] = set()

    async def __call__(self, config: Any, coordinator: Any) -> Any:
        if config.provider in self.real:
            return await create_provider(config, coordinator)
        return RecordingProvider(self, config, coordinator)


@pytest.fixture
def providers(monkeypatch):
    recorded = Providers()
    monkeypatch.setattr(assembly, "_provider_factory", recorded)
    return recorded


@asynccontextmanager
async def engine(**fields: Any) -> AsyncIterator[Any]:
    fields.setdefault("provider", "anthropic")
    fields.setdefault("model", "claude-sonnet-5")
    agent = await assembly.create_engine(AgentOptions(**fields))
    try:
        yield agent
    finally:
        await agent.close()


async def run(session: Any, model: str | None = None) -> list[Any]:
    turn = await session.start_turn(TurnInput([TextPart("Reply")], model=model))
    return [event async for event in turn.events()]


def selection(events: list[Any]) -> tuple[str, str]:
    assert events[0].type == "turn_started"
    actual = events[0].payload.primary_actual
    return actual.provider, actual.model


def succeeded(events: list[Any]) -> None:
    assert events[-1].type == "terminal"
    assert events[-1].payload.state == "success", events[-1].payload.error


def refused(error: AgentError, code: str) -> None:
    assert error.code == code
    assert error.message.strip()
    assert error.remedy.strip()


def ephemeral(effort: str | None = None) -> SessionOptions:
    return SessionOptions(persistence="ephemeral", reasoning_effort=effort)


async def test_next_turn_runs_on_the_new_selection(providers):
    async with engine() as agent:
        session = await agent.create_session(ephemeral())
        first = await run(session)
        await session.set_model(provider="openai", model="gpt-6.1-sol")
        second = await run(session)
    succeeded(first)
    succeeded(second)
    assert selection(first) == ("anthropic", "claude-sonnet-5")
    assert selection(second) == ("openai", "gpt-6.1-sol")
    assert [(provider, model) for provider, model, _ in providers.requests] == [
        ("anthropic", "claude-sonnet-5"),
        ("openai", "gpt-6.1-sol"),
    ]
    assert {(entry.provider, entry.model) for entry in second[-1].payload.usage.entries} == {("openai", "gpt-6.1-sol")}


async def test_conversation_carries_across_the_switch(providers):
    async with engine() as agent:
        session = await agent.create_session(ephemeral())
        turn = await session.start_turn(TurnInput([TextPart("Remember the word kestrel.")]))
        _ = [event async for event in turn.events()]
        await session.set_model(provider="openai", model="gpt-6.1-sol")
        succeeded(await run(session))
    provider, _, request = providers.requests[-1]
    assert provider == "openai"
    assert "kestrel" in json.dumps(request["messages"])


async def test_set_model_during_an_active_turn_is_busy(providers):
    providers.gate = asyncio.Event()
    async with engine() as agent:
        session = await agent.create_session(ephemeral())
        turn = await session.start_turn(TurnInput([TextPart("Reply")]))
        await providers.entered.wait()
        with pytest.raises(AgentError) as caught:
            await session.set_model(provider="openai", model="gpt-6.1-sol")
        refused(caught.value, "busy")
        providers.gate.set()
        held = [event async for event in turn.events()]
        await session.set_model(provider="openai", model="gpt-6.1-sol")
        after = await run(session)
    succeeded(held)
    assert selection(held) == ("anthropic", "claude-sonnet-5")
    assert selection(after) == ("openai", "gpt-6.1-sol")


async def test_set_model_on_a_closed_session_is_closed(providers):
    async with engine() as agent:
        session = await agent.create_session(ephemeral())
        await session.close()
        with pytest.raises(AgentError) as caught:
            await session.set_model(provider="openai", model="gpt-6.1-sol")
    refused(caught.value, "closed")


@pytest.mark.parametrize("provider", ["not-a-provider", "", "OpenAI"])
async def test_unknown_provider_is_invalid_input_and_leaves_the_session(providers, provider):
    async with engine() as agent:
        session = await agent.create_session(ephemeral())
        with pytest.raises(AgentError) as caught:
            await session.set_model(provider=provider, model="x")
        info = session.info
        after = await run(session)
    refused(caught.value, "invalid_input")
    assert (info.provider, info.model) == ("anthropic", "claude-sonnet-5")
    succeeded(after)
    assert selection(after) == ("anthropic", "claude-sonnet-5")


async def test_missing_credentials_fail_provider_failed_and_leave_the_session(providers):
    providers.real.add("gemini")
    environment = {"GEMINI_API_KEY": "", "GOOGLE_API_KEY": ""}
    async with engine(environment=environment) as agent:
        session = await agent.create_session(ephemeral())
        with pytest.raises(AgentError) as caught:
            await session.set_model(provider="gemini", model="gemini-3.8-flash")
        info = session.info
        after = await run(session)
    refused(caught.value, "provider_failed")
    assert "GEMINI_API_KEY" in caught.value.remedy or "GOOGLE_API_KEY" in caught.value.remedy
    assert (info.provider, info.model) == ("anthropic", "claude-sonnet-5")
    succeeded(after)
    assert selection(after) == ("anthropic", "claude-sonnet-5")


async def test_session_record_carries_the_selection(providers):
    async with engine() as agent:
        session = await agent.create_session(ephemeral())
        before = session.info
        await session.set_model(provider="openai", model="gpt-6.1-sol", reasoning_effort="low")
        after = session.info
    assert (before.provider, before.model) == ("anthropic", "claude-sonnet-5")
    assert (after.provider, after.model, after.reasoning_effort) == ("openai", "gpt-6.1-sol", "low")


async def test_absent_reasoning_effort_keeps_the_session_value(providers):
    async with engine() as agent:
        session = await agent.create_session(ephemeral("low"))
        await session.set_model(provider="openai", model="gpt-6.1-sol")
        events = await run(session)
        info = session.info
    assert events[0].payload.reasoning_effort == "low"
    assert info.reasoning_effort == "low"


async def test_named_reasoning_effort_applies_to_later_turns(providers):
    async with engine() as agent:
        session = await agent.create_session(ephemeral())
        await session.set_model(provider="openai", model="gpt-6.1-sol", reasoning_effort="high")
        events = await run(session)
    assert events[0].payload.reasoning_effort == "high"


async def test_reasoning_effort_the_new_model_does_not_take_is_selector_rejected(providers):
    async with engine() as agent:
        session = await agent.create_session(ephemeral())
        with pytest.raises(AgentError) as caught:
            await session.set_model(provider="openai", model="gpt-5.5-pro", reasoning_effort="low")
        info = session.info
    refused(caught.value, "selector_rejected")
    assert (info.provider, info.model) == ("anthropic", "claude-sonnet-5")


async def test_selection_is_saved_and_resume_uses_it(providers):
    async with engine() as agent:
        session = await agent.create_session(SessionOptions(session_id="switched-session", persistence="durable"))
        succeeded(await run(session))
        await session.set_model(provider="openai", model="gpt-6.1-sol", reasoning_effort="low")
        succeeded(await run(session))
    async with engine() as agent:
        (listed,) = [record for record in await agent.list_sessions() if record.session_id == "switched-session"]
        resumed = await agent.resume_session("switched-session")
        info = resumed.info
        events = await run(resumed)
    assert (listed.provider, listed.model, listed.reasoning_effort) == ("openai", "gpt-6.1-sol", "low")
    assert (info.provider, info.model, info.reasoning_effort) == ("openai", "gpt-6.1-sol", "low")
    succeeded(events)
    assert events[0].payload.continuation == "resumed"
    assert selection(events) == ("openai", "gpt-6.1-sol")
    assert events[0].payload.reasoning_effort == "low"


async def test_fork_inherits_the_current_selection(providers):
    async with engine() as agent:
        parent = await agent.create_session(ephemeral())
        succeeded(await run(parent))
        await parent.set_model(provider="openai", model="gpt-6.1-sol")
        child = await parent.fork()
        info = child.info
        events = await run(child)
    assert (info.provider, info.model) == ("openai", "gpt-6.1-sol")
    succeeded(events)
    assert selection(events) == ("openai", "gpt-6.1-sol")


@pytest.mark.parametrize(
    ("first", "second", "reasoning"),
    [
        (
            ("anthropic", "claude-sonnet-5"),
            ("openai", "gpt-6.1-sol"),
            ThinkingBlock(thinking="plan", signature=SIGNATURE),
        ),
        (
            ("openai", "gpt-6.1-sol"),
            ("anthropic", "claude-sonnet-5"),
            ThinkingBlock(thinking="plan", content=[SIGNATURE, "rs_1"]),
        ),
        (
            ("gemini", "gemini-3.8-flash"),
            ("anthropic", "claude-sonnet-5"),
            ThinkingBlock(thinking="plan", signature=SIGNATURE),
        ),
    ],
    ids=["anthropic-signature", "openai-encrypted-content", "gemini-signature"],
)
async def test_provider_only_reasoning_data_is_dropped_on_provider_change(providers, first, second, reasoning):
    providers.reasoning[first[0]] = reasoning
    async with engine(provider=first[0], model=first[1]) as agent:
        session = await agent.create_session(ephemeral())
        succeeded(await run(session))
        succeeded(await run(session))
        await session.set_model(provider=second[0], model=second[1])
        succeeded(await run(session))
    same, switched = providers.requests[1], providers.requests[2]
    # The provider that produced it still receives it, so the fixture really carries reasoning data.
    assert same[0] == first[0]
    assert SIGNATURE in json.dumps(same[2]["messages"])
    assert switched[0] == second[0]
    assert SIGNATURE not in json.dumps(switched[2]["messages"])
    assert "Reply" in json.dumps(switched[2]["messages"])


async def test_explicit_higher_turn_model_is_honored(providers):
    async with engine() as agent:
        session = await agent.create_session(ephemeral())
        events = await run(session, model="claude-opus-5")
    succeeded(events)
    assert selection(events) == ("anthropic", "claude-opus-5")
    assert providers.requests[-1][1] == "claude-opus-5"


async def test_explicit_higher_session_model_is_honored(providers):
    async with engine() as agent:
        session = await agent.create_session(SessionOptions(persistence="ephemeral", model="claude-opus-5"))
        events = await run(session)
    succeeded(events)
    assert selection(events) == ("anthropic", "claude-opus-5")

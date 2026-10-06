"""Selection internals an evaluation cannot observe: replay conversion, the saved provider, and caches.

agent-interface.v1 sections 1, 3, and 5.
"""

import os

from amplifier_agent_engine._engine import assembly
from amplifier_agent_engine._engine.providers import create_provider
from amplifier_agent_engine._engine.selection import without_reasoning
from amplifier_agent_engine._records import AgentError, AgentOptions, SessionOptions, TextPart, TurnInput
import pytest

from tests.support.engine import provision
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
    return path


def test_replay_keeps_text_and_tool_pairs_without_sealed_reasoning():
    messages = [
        {"role": "user", "content": "Run it"},
        {
            "role": "assistant",
            "thinking_block": {"type": "thinking", "thinking": "plan", "signature": "seal"},
            "content": [
                {"type": "thinking", "thinking": "plan", "signature": "seal"},
                {"type": "redacted_thinking", "data": "sealed"},
                {"type": "text", "text": "Running."},
                {"type": "tool_call", "id": "call_1", "name": "bash", "input": {}, "signature": "seal"},
            ],
            "tool_calls": [{"id": "call_1", "tool": "bash", "arguments": {}, "signature": "seal"}],
        },
        {"role": "tool", "tool_call_id": "call_1", "name": "bash", "content": "ok"},
    ]
    replay = without_reasoning(messages)
    assert "seal" not in str(replay)
    assert "sealed" not in str(replay)
    assert len(replay) == len(messages)
    assistant = replay[1]
    assert assistant["content"] == [
        {"type": "text", "text": "Running."},
        {"type": "tool_call", "id": "call_1", "name": "bash", "input": {}},
    ]
    assert assistant["tool_calls"] == [{"id": "call_1", "tool": "bash", "arguments": {}}]
    assert replay[2] == messages[2]
    assert messages[1]["content"][0]["signature"] == "seal"


async def test_resume_whose_saved_provider_has_no_credentials_fails_provider_failed(monkeypatch, tmp_path):
    scripted = ScriptedFactory([{"text": "Reply"}])
    real = set()

    async def factory(config, coordinator):
        if config.provider in real:
            return await create_provider(config, coordinator)
        return await scripted(config, coordinator)

    monkeypatch.setattr(assembly, "_provider_factory", factory)
    options = AgentOptions(
        provider="anthropic",
        model="claude-sonnet-5",
        sessions_directory=tmp_path,
        environment={"GEMINI_API_KEY": "", "GOOGLE_API_KEY": ""},
    )
    agent = await assembly.create_engine(options)
    try:
        session = await agent.create_session(SessionOptions(session_id="saved-gemini"))
        await session.set_model(provider="gemini", model="gemini-3.8-flash")
        await session.close()
    finally:
        await agent.close()
    real.add("gemini")
    agent = await assembly.create_engine(options)
    try:
        with pytest.raises(AgentError) as caught:
            await agent.resume_session("saved-gemini")
        assert caught.value.code == "provider_failed"
        assert "GEMINI_API_KEY" in caught.value.remedy
        # The refused resume holds no lease, so the session can still be deleted.
        await agent.delete_session("saved-gemini")
    finally:
        await agent.close()


async def test_listed_sessions_report_the_effective_reasoning_effort(monkeypatch, tmp_path):
    provision(monkeypatch)
    agent = await assembly.create_engine(
        AgentOptions(provider="anthropic", model="claude-sonnet-5", sessions_directory=tmp_path)
    )
    try:
        session = await agent.create_session(SessionOptions(session_id="default-effort"))
        info = session.info
        await session.close()
        (listed,) = await agent.list_sessions()
    finally:
        await agent.close()
    assert info.reasoning_effort == "medium"
    assert (listed.provider, listed.model, listed.reasoning_effort) == ("anthropic", "claude-sonnet-5", "medium")


async def test_image_capabilities_are_asked_per_provider_and_model(monkeypatch):
    probe = provision(monkeypatch, [{"text": "Reply"}])
    agent = await assembly.create_engine(AgentOptions(provider="anthropic", model="claude-sonnet-5"))
    asked = []

    async def capabilities(model):
        asked.append(model)
        return frozenset({"vision"})

    try:
        session = await agent.create_session(SessionOptions(persistence="ephemeral"))
        monkeypatch.setattr(session.runtime, "model_capabilities", capabilities)
        for provider in ("anthropic", "openai", "anthropic"):
            assert await agent.model_capabilities(session.runtime, provider, "shared-name") == {"vision"}
    finally:
        await agent.close()
    assert asked == ["shared-name", "shared-name"]
    assert probe.requests == []


async def test_set_model_within_the_provider_runs_the_named_model(monkeypatch):
    provision(monkeypatch, [{"text": "Reply"}, {"text": "Reply"}])
    agent = await assembly.create_engine(AgentOptions(provider="anthropic", model="claude-sonnet-5"))
    try:
        session = await agent.create_session(SessionOptions(persistence="ephemeral"))
        await session.run(TurnInput([TextPart("First")]))
        await session.set_model(provider="anthropic", model="claude-opus-5")
        turn = await session.start_turn(TurnInput([TextPart("Second")]))
        events = [event async for event in turn.events()]
    finally:
        await agent.close()
    assert events[-1].payload.state == "success"
    assert events[0].payload.primary_actual.model == "claude-opus-5"

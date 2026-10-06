"""The reasoning effort: vocabulary, default, precedence, model support, and host config.

agent-interface.v1 sections 1, 2, and 5; host-config.v1 sections 1 to 3; turn-events.v1
turn_started.reasoning_effort. The scripted provider records each ChatRequest the engine
sends, so ``reasoning_effort`` there is the value primary or delegated work runs at.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import json
import os
from typing import Any

from amplifier_agent_engine._engine import assembly
from amplifier_agent_engine._engine.configuration import resolve
from amplifier_agent_engine._engine.provider_policy import PROVIDERS
from amplifier_agent_engine._records import AgentError, AgentOptions, SessionOptions, TextPart, TurnInput
import pytest

from tests.support.engine import provision, provision_many

ORDER = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
UNREGISTERED = ["Low", "LOW", " low", "low ", "", "extreme", "very-high", 1, True, ["low"], {"effort": "low"}]


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


@pytest.fixture
def probe(monkeypatch):
    return provision(monkeypatch, [{"text": "Reply"} for _ in range(4)])


@asynccontextmanager
async def engine(**fields: Any) -> AsyncIterator[Any]:
    fields.setdefault("provider", "anthropic")
    fields.setdefault("model", "claude-sonnet-5")
    agent = await assembly.create_engine(AgentOptions(**fields))
    try:
        yield agent
    finally:
        await agent.close()


async def turn_events(session: Any, effort: str | None = None) -> list[Any]:
    turn = await session.start_turn(TurnInput([TextPart("Reply")], reasoning_effort=effort))
    return [event async for event in turn.events()]


def started_effort(events: list[Any]) -> str | None:
    assert events[0].type == "turn_started"
    return events[0].payload.reasoning_effort


def refused(error: AgentError, code: str) -> None:
    assert error.code == code
    assert error.message.strip()
    assert error.remedy.strip()


def ephemeral(effort: str | None = None) -> SessionOptions:
    return SessionOptions(persistence="ephemeral", reasoning_effort=effort)


@pytest.mark.parametrize("value", UNREGISTERED)
async def test_unregistered_agent_value_fails_at_construction(probe, value):
    with pytest.raises(AgentError) as caught:
        await assembly.create_engine(AgentOptions(reasoning_effort=value))
    refused(caught.value, "invalid_input")
    assert "reasoning_effort" in caught.value.message
    assert probe.requests == []


@pytest.mark.parametrize("value", UNREGISTERED)
async def test_unregistered_session_value_fails_at_create_session(probe, value):
    async with engine() as agent:
        with pytest.raises(AgentError) as caught:
            await agent.create_session(ephemeral(value))
    refused(caught.value, "invalid_input")
    assert "reasoning_effort" in caught.value.message
    assert probe.requests == []


@pytest.mark.parametrize("value", UNREGISTERED)
async def test_unregistered_turn_value_fails_at_start_turn(probe, value):
    async with engine() as agent:
        session = await agent.create_session(ephemeral())
        with pytest.raises(AgentError) as caught:
            await session.start_turn(TurnInput([TextPart("Reply")], reasoning_effort=value))
    refused(caught.value, "invalid_input")
    assert "reasoning_effort" in caught.value.message
    assert probe.requests == []


@pytest.mark.parametrize("value", ORDER)
async def test_every_registered_value_is_accepted_where_support_is_unknowable(probe, value):
    async with engine(provider="chat-completions", model="fixture-model", reasoning_effort=value) as agent:
        session = await agent.create_session(ephemeral(value))
        events = await turn_events(session, value)
    assert events[-1].payload.state == "success"
    assert started_effort(events) == value


async def test_absent_everywhere_runs_at_medium(probe):
    async with engine() as agent:
        session = await agent.create_session(ephemeral())
        events = await turn_events(session)
    assert events[-1].payload.state == "success"
    assert probe.requests[-1]["reasoning_effort"] == "medium"
    assert started_effort(events) == "medium"


@pytest.mark.parametrize(
    ("agent_value", "session_value", "turn_value", "expected"),
    [
        ("high", None, None, "high"),
        ("max", None, None, "max"),
        ("low", None, None, "low"),
        (None, "low", None, "low"),
        (None, None, "low", "low"),
        (None, "medium", "medium", "medium"),
        ("high", "low", None, "low"),
        ("high", "medium", "low", "low"),
        ("max", None, "xhigh", "xhigh"),
    ],
)
async def test_refinement_runs_at_the_most_specific_value(probe, agent_value, session_value, turn_value, expected):
    async with engine(reasoning_effort=agent_value) as agent:
        session = await agent.create_session(ephemeral(session_value))
        events = await turn_events(session, turn_value)
    assert events[-1].payload.state == "success"
    assert probe.requests[-1]["reasoning_effort"] == expected
    assert started_effort(events) == expected


@pytest.mark.parametrize(
    ("agent_value", "session_value"),
    [(None, "high"), (None, "max"), ("low", "medium"), ("high", "xhigh")],
)
async def test_session_value_above_the_agent_value_is_honored(probe, agent_value, session_value):
    async with engine(reasoning_effort=agent_value) as agent:
        session = await agent.create_session(ephemeral(session_value))
        events = await turn_events(session)
    assert events[-1].payload.state == "success"
    assert probe.requests[-1]["reasoning_effort"] == session_value
    assert started_effort(events) == session_value


@pytest.mark.parametrize(
    ("agent_value", "session_value", "turn_value"),
    [(None, None, "high"), ("low", None, "medium"), ("high", "low", "medium"), (None, "low", "high")],
)
async def test_turn_value_above_the_session_value_is_honored(probe, agent_value, session_value, turn_value):
    async with engine(reasoning_effort=agent_value) as agent:
        session = await agent.create_session(ephemeral(session_value))
        events = await turn_events(session, turn_value)
    assert events[-1].payload.state == "success"
    assert probe.requests[-1]["reasoning_effort"] == turn_value
    assert started_effort(events) == turn_value


async def test_turn_value_refines_only_its_own_turn(probe):
    async with engine(reasoning_effort="high") as agent:
        session = await agent.create_session(ephemeral("medium"))
        first = await turn_events(session, "low")
        second = await turn_events(session)
    assert [started_effort(first), started_effort(second)] == ["low", "medium"]
    assert [request["reasoning_effort"] for request in probe.requests] == ["low", "medium"]


@pytest.mark.parametrize(("agent_value", "turn_value"), [(None, None), ("high", None), (None, "low"), ("max", "max")])
async def test_model_without_reasoning_effort_runs_without_one(probe, agent_value, turn_value):
    async with engine(provider="openai", model="gpt-4.1", reasoning_effort=agent_value) as agent:
        session = await agent.create_session(ephemeral())
        events = await turn_events(session, turn_value)
    assert events[-1].payload.state == "success"
    assert probe.requests[-1]["reasoning_effort"] is None
    assert started_effort(events) is None


@pytest.mark.parametrize(
    ("provider", "model", "ceiling", "label"),
    [
        ("anthropic", "claude-sonnet-5", "high", "minimal"),
        ("anthropic", "claude-sonnet-5", "high", "none"),
        ("openai", "gpt-5.5-pro", "high", "low"),
        ("openai", "gpt-6-astra", "high", "none"),
        ("gemini", "gemini-3.7-flash", "high", "minimal"),
        ("gemini", "gemini-3.5-flash", "high", "none"),
    ],
)
async def test_turn_label_the_model_does_not_take_is_rejected_not_substituted(probe, provider, model, ceiling, label):
    async with engine(provider=provider, model=model, reasoning_effort=ceiling) as agent:
        session = await agent.create_session(ephemeral())
        with pytest.raises(AgentError) as caught:
            await session.start_turn(TurnInput([TextPart("Reply")], reasoning_effort=label))
    refused(caught.value, "selector_rejected")
    assert probe.requests == []


async def test_session_label_the_model_does_not_take_is_rejected(probe):
    async with engine() as agent:
        with pytest.raises(AgentError) as caught:
            await agent.create_session(ephemeral("minimal"))
    refused(caught.value, "selector_rejected")
    assert probe.requests == []


@pytest.mark.parametrize(
    ("provider", "model", "label"),
    [
        ("anthropic", "claude-sonnet-5", "minimal"),
        ("gemini", "gemini-3.5-flash", "xhigh"),
        ("gemini", "gemini-3.5-flash", "max"),
    ],
)
async def test_agent_label_the_model_does_not_take_is_rejected_at_construction(probe, provider, model, label):
    with pytest.raises(AgentError) as caught:
        await assembly.create_engine(AgentOptions(provider=provider, model=model, reasoning_effort=label))
    refused(caught.value, "selector_rejected")
    assert probe.requests == []


@pytest.mark.parametrize(
    ("agent_value", "turn_value", "expected"), [(None, None, None), (None, "low", "low"), ("high", None, "high")]
)
async def test_unknowable_support_reports_only_a_named_value(probe, agent_value, turn_value, expected):
    async with engine(provider="chat-completions", model="fixture-model", reasoning_effort=agent_value) as agent:
        session = await agent.create_session(ephemeral())
        events = await turn_events(session, turn_value)
    assert events[-1].payload.state == "success"
    assert started_effort(events) == expected


@pytest.mark.parametrize(
    ("environment", "file", "option", "expected"),
    [
        ("low", None, None, "low"),
        (None, "high", None, "high"),
        ("low", "high", None, "low"),
        ("low", "high", "minimal", "minimal"),
        (None, None, "xhigh", "xhigh"),
    ],
)
async def test_host_config_resolves_below_agent_options(monkeypatch, host, probe, environment, file, option, expected):
    if environment is not None:
        monkeypatch.setenv("AMPLIFIER_AGENT_REASONING_EFFORT", environment)
    if file is not None:
        host.write_text(json.dumps({"reasoning_effort": file}))
    async with engine(provider="chat-completions", model="fixture-model", reasoning_effort=option) as agent:
        session = await agent.create_session(ephemeral())
        events = await turn_events(session)
    assert started_effort(events) == expected


@pytest.mark.parametrize("value", ["Low", " low", "low ", "", "extreme"])
async def test_host_environment_value_parses_strictly(monkeypatch, probe, value):
    monkeypatch.setenv("AMPLIFIER_AGENT_REASONING_EFFORT", value)
    with pytest.raises(AgentError) as caught:
        await assembly.create_engine(AgentOptions())
    refused(caught.value, "invalid_input")
    assert "reasoning_effort" in caught.value.message.lower() + caught.value.remedy.lower()
    assert probe.requests == []


@pytest.mark.parametrize("value", ["Low", " low", "", "extreme", 1, True, ["low"]])
async def test_host_file_value_parses_strictly(host, probe, value):
    host.write_text(json.dumps({"reasoning_effort": value}))
    with pytest.raises(AgentError) as caught:
        await assembly.create_engine(AgentOptions())
    refused(caught.value, "invalid_input")
    assert "reasoning_effort" in caught.value.message
    assert probe.requests == []


async def test_shadowed_host_value_is_not_read(monkeypatch, host, probe):
    host.write_text(json.dumps({"reasoning_effort": "LOW"}))
    monkeypatch.setenv("AMPLIFIER_AGENT_REASONING_EFFORT", "Extreme")
    async with engine(provider="chat-completions", model="fixture-model", reasoning_effort="low") as agent:
        session = await agent.create_session(ephemeral())
        events = await turn_events(session)
    assert started_effort(events) == "low"


EFFORT_SETTINGS = [
    *[(provider, "reasoning", {"effort": "low"}) for provider in ("openai", "azure-openai", "openai-chatgpt", "vllm")],
    *[(provider, "reasoning_effort", "low") for provider in sorted(PROVIDERS)],
    ("anthropic", "thinking", {"type": "adaptive"}),
    ("anthropic", "output_config", {"effort": "low"}),
    ("gemini", "thinking_config", {"thinking_level": "LOW"}),
    ("ollama", "think", True),
]


@pytest.mark.parametrize(("provider", "key", "value"), EFFORT_SETTINGS)
def test_extra_request_params_cannot_set_the_reasoning_effort(host, provider, key, value):
    host.write_text(json.dumps({"extra_request_params": {provider: {key: value}}}))
    with pytest.raises(AgentError) as caught:
        resolve(AgentOptions(provider=provider, model="fixture-model"))
    refused(caught.value, "invalid_input")
    assert f"extra_request_params.{provider}.{key}" in caught.value.message
    assert "reasoning_effort" in caught.value.remedy


def test_anthropic_output_config_without_effort_is_still_accepted(host):
    budget = {"task_budget": {"type": "tokens", "total": 20000}}
    host.write_text(json.dumps({"extra_request_params": {"anthropic": {"output_config": budget}}}))
    assert resolve(AgentOptions(provider="anthropic", model="claude-sonnet-5")).extra_request_params == {
        "output_config": budget
    }


@pytest.mark.parametrize(
    ("agent_value", "turn_value", "ceiling"), [(None, "low", "low"), ("low", None, "low"), (None, None, "medium")]
)
async def test_delegated_work_never_exceeds_the_effective_ceiling(monkeypatch, agent_value, turn_value, ceiling):
    probes = provision_many(
        monkeypatch,
        [
            {"tool": {"name": "delegate", "arguments": {"instruction": "Answer briefly."}}},
            {"text": "Parent complete"},
        ],
        [{"text": "Child complete"}],
    )
    async with engine(reasoning_effort=agent_value, approvals="allow") as agent:
        session = await agent.create_session(ephemeral())
        events = await turn_events(session, turn_value)
    assert events[-1].payload.state == "success"
    parent, child = probes
    assert {request["reasoning_effort"] for request in parent.requests} == {ceiling}
    assert child.requests
    for request in child.requests:
        assert request["reasoning_effort"] in ORDER[: ORDER.index(ceiling) + 1]

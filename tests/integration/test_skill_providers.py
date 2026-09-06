import json

import pytest
from amplifier_agent import (
    AgentOptions,
    ApprovalResponse,
    SessionOptions,
    TextPart,
    Tool,
    TurnInput,
    create_agent,
)

from conformance.fixtures.http_server import socket_server
from conformance.fixtures.provider_services import KEY_ENV, MODELS, URL_ENV, provider_service


@pytest.mark.parametrize("provider", MODELS)
@pytest.mark.parametrize("deny_hook", [False, True])
async def test_named_skill_hooks_preserve_provider_policy_and_effect_authority(
    monkeypatch, tmp_path, provider, deny_hook,
):
    skill = tmp_path / "skills" / "review"
    skill.mkdir(parents=True)
    agents = tmp_path / "agents"
    agents.mkdir()
    ledger = tmp_path / "order.txt"
    (agents / "reviewer.md").write_text(
        "---\nmeta:\n  name: reviewer\ntools: [counter, bash]\n---\n"
        "Review marker: named-agent-instructions.\n"
    )
    hooks = {
        event: [{
            **({"matcher": "counter"} if event != "Stop" else {}),
            "hooks": [{
                "type": "command", "command": f"printf '{marker}\\n' >> '{ledger}'",
                "timeout": 5,
            }],
        }]
        for event, marker in (("PreToolUse", "before"), ("PostToolUse", "after"), ("Stop", "stop"))
    }
    (skill / "SKILL.md").write_text(
        "---\nname: review\ndescription: Record a review.\ncontext: fork\nagent: reviewer\n"
        f"hooks: {json.dumps(hooks)}\n---\nRecord the review with counter.\n"
    )
    requests, effects, approvals = [], [], []

    async def counter(arguments, context):
        effects.append(arguments)
        with ledger.open("a") as output:
            output.write("counter\n")
        return "Review recorded"

    async def approve(request):
        approvals.append(request)
        if request.name == "bash":
            assert str(ledger) in request.summary
            if deny_hook:
                assert not ledger.exists()
                return ApprovalResponse("deny")
        return ApprovalResponse("allow")

    responses = [
        provider_service(provider, requests, tool="load_skill", tool_arguments={"name": "review"}),
        provider_service(provider, requests, tool="counter", tool_arguments={"value": "review"}),
        provider_service(provider, requests),
    ]

    async def application(scope, receive, send):
        selected = responses[min(len(requests), 2)] if scope["type"] == "http" else responses[-1]
        await selected(scope, receive, send)

    async with socket_server(application) as url:
        monkeypatch.setenv(KEY_ENV[provider], "fixture-api-key")
        monkeypatch.setenv(URL_ENV[provider], url)
        async with await create_agent(AgentOptions(
            provider=provider, model=MODELS[provider], skills=[str(tmp_path)], approvals=approve,
            instructions="Retain host-instruction-marker.",
            tools=[Tool("counter", "Record a review", {
                "$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
            }, counter)],
        )) as agent:
            async with await agent.create_session(SessionOptions(persistence="ephemeral")) as session:
                turn = await session.start_turn(TurnInput([TextPart("Load the review skill.")]))
                events = [event async for event in turn.events()]
    result = events[-1].payload
    assert result.state == ("rejected" if deny_hook else "success"), result.error
    assert events[0].type == "turn_started"
    assert sum(event.type == "terminal" for event in events) == 1
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    calls = [event.payload.call.call_id for event in events if event.type == "tool_call"]
    resolutions = [
        event.payload.resolution.call_id for event in events if event.type == "tool_result"
    ]
    assert len(set(calls)) == len(calls)
    assert sorted(calls) == sorted(resolutions)
    decisions = [
        event.payload.resolution.request_id for event in events if event.type == "approval_decision"
    ]
    assert sorted(request.request_id for request in approvals) == sorted(decisions)
    assert "host-instruction-marker" in json.dumps(requests[1])
    assert "named-agent-instructions" in json.dumps(requests[1])
    if deny_hook:
        assert result.error.code == "approval_denied"
        assert not ledger.exists()
        assert not effects
        assert len(requests) == 2
    else:
        assert effects == [{"value": "review"}]
        assert ledger.read_text() == "before\ncounter\nafter\nstop\n"
        assert len(requests) == 4
        assert result.usage.entries[0].tokens_in == 80
        assert (result.usage.entries[0].provider, result.usage.entries[0].model) == (
            provider, MODELS[provider],
        )

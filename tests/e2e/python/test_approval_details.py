import copy
from pathlib import Path

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

from conformance.fixtures.engine import provision as provision_engine

pytestmark = pytest.mark.production_only

SCHEMA = "https://json-schema.org/draft/2020-12/schema"
MCP_SERVICE = Path(__file__).parents[3] / "conformance/fixtures/mcp_service.py"


def provision(monkeypatch, name, arguments):

    factory = provision_engine(monkeypatch, [
        {"tool": {"name": name, "arguments": arguments}}, {"text": "Done"},
    ])
    return factory


def options(**kwargs):
    return AgentOptions(provider="anthropic", model="claude-sonnet-5", **kwargs)


@pytest.mark.parametrize("decision", ["allow", "deny"])
async def test_run_approval_reviews_caller_payload_without_rewriting_arguments(
    monkeypatch, decision,
):
    arguments = {"target": "staging", "payload": ["value\n\x1b[2J", {"count": 2 ** 60 + 1}],
                 "api_key": "credential-value"}
    provision(monkeypatch, "publish", arguments)
    calls = []
    requests = []

    async def approve(request):
        assert not calls
        assert '"target": "staging"' in request.summary
        assert r"value\n\u001b[2J" in request.summary
        assert "credential-value" not in request.summary and "[redacted]" in request.summary
        requests.append(copy.deepcopy(request))
        request.summary = "Caller-owned copy"
        return ApprovalResponse(decision)

    async def execute(received, context):
        calls.append((copy.deepcopy(received), context.call_id))
        assert requests and context.call_id == requests[0].call_id
        return "Published"

    tool = Tool("publish", "Publish a payload.", {"$schema": SCHEMA, "type": "object"}, execute)
    async with await create_agent(options(approvals=approve, tools=[tool])) as agent:
        async with await agent.create_session(SessionOptions(persistence="ephemeral")) as session:
            result = await session.run(TurnInput([TextPart("Publish the payload.")]))
    assert len(requests) == 1
    assert result.state == ("success" if decision == "allow" else "rejected")
    assert calls == ([(arguments, requests[0].call_id)] if decision == "allow" else [])


@pytest.mark.parametrize("decision", ["allow", "deny"])
async def test_run_approval_sees_skill_shell_command_and_its_actual_directory(
    monkeypatch, tmp_path, decision,
):
    skill = tmp_path / "skills" / "receipt"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: receipt\ndescription: Record a receipt.\n---\n"
        "Recorded receipt: !`printf skill-receipt > receipt.txt`\n"
    )
    provision(monkeypatch, "load_skill", {"name": "receipt", "arguments": "review-me"})
    requests = []

    async def approve(request):
        requests.append(request)
        assert not (skill / "receipt.txt").exists()
        if request.name == "load_skill":
            assert '"name": "receipt"' in request.summary
            assert '"arguments": "review-me"' in request.summary
            return ApprovalResponse("allow")
        assert request.name == "bash"
        assert "printf skill-receipt > receipt.txt" in request.summary
        assert str(skill) in request.summary
        return ApprovalResponse(decision)

    async with await create_agent(options(approvals=approve, skills=[str(skill)])) as agent:
        async with await agent.create_session(SessionOptions(persistence="ephemeral")) as session:
            result = await session.run(TurnInput([TextPart("Load the receipt skill.")]))
    assert [request.name for request in requests] == ["load_skill", "bash"]
    assert result.state == ("success" if decision == "allow" else "rejected")
    assert (skill / "receipt.txt").exists() == (decision == "allow")


async def test_streamed_approval_detail_preserves_call_arguments_and_event_pair_order(monkeypatch):
    arguments = {"target": "staging", "api_key": "executor-credential", "payload": "x" * 10_000}
    original = copy.deepcopy(arguments)
    provision(monkeypatch, "publish", arguments)
    requests = []
    calls = []

    async def approve(request):
        assert not calls
        assert len(request.summary) <= 4096
        assert "[truncated]" in request.summary and "[redacted]" in request.summary
        assert "executor-credential" not in request.summary
        requests.append(copy.deepcopy(request))
        request.summary = "A caller mutation"
        return ApprovalResponse("allow")

    async def execute(received, context):
        calls.append(copy.deepcopy(received))
        assert requests
        return "Published"

    tool = Tool("publish", "Publish a payload.", {"$schema": SCHEMA, "type": "object"}, execute)
    async with await create_agent(options(approvals=approve, tools=[tool])) as agent:
        async with await agent.create_session(SessionOptions(persistence="ephemeral")) as session:
            turn = await session.start_turn(TurnInput([TextPart("Publish the payload.")]))
            events = [event async for event in turn.events()]
    assert events[-1].payload.state == "success"
    paired = [event for event in events if event.type in (
        "tool_call", "approval_request", "approval_decision", "tool_result",
    )]
    assert [event.type for event in paired] == [
        "tool_call", "approval_request", "approval_decision", "tool_result",
    ]
    assert paired[0].payload.call.arguments == original == arguments
    assert calls == [original]
    assert paired[1].payload.request == requests[0]
    assert paired[2].payload.resolution.request_id == requests[0].request_id
    assert paired[3].payload.resolution.call_id == paired[0].payload.call.call_id

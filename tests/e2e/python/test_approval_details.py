import copy
from pathlib import Path

from amplifier_agent import AgentOptions, ApprovalResponse, SessionOptions, TextPart, Tool, TurnInput, create_agent
import pytest

from tests.support.engine import provision as provision_engine

SCHEMA = "https://json-schema.org/draft/2020-12/schema"
MCP_SERVICE = Path(__file__).parents[3] / "tests/support/mcp_service.py"


def provision(monkeypatch, name, arguments):

    factory = provision_engine(
        monkeypatch,
        [
            {"tool": {"name": name, "arguments": arguments}},
            {"text": "Done"},
        ],
    )
    return factory


def options(**kwargs):
    return AgentOptions(provider="anthropic", model="claude-sonnet-5", **kwargs)


@pytest.mark.parametrize("decision", ["allow", "deny"])
async def test_run_approval_reviews_caller_payload_without_rewriting_arguments(
    monkeypatch,
    decision,
):
    arguments = {
        "target": "staging",
        "payload": ["value\n\x1b[2J", {"count": 2**60 + 1}],
        "api_key": "credential-value",
    }
    provision(monkeypatch, "publish", arguments)
    calls = []
    requests = []

    async def approve(request):
        assert not calls
        assert '"target": "staging"' in request.summary
        assert r"value\n\u001b[2J" in request.summary
        assert "credential-value" not in request.summary
        assert "[redacted]" in request.summary
        assert request.arguments == arguments
        requests.append(copy.deepcopy(request))
        request.summary = "Caller-owned copy"
        request.arguments["target"] = "production"
        return ApprovalResponse(decision)

    async def execute(received, context):
        calls.append((copy.deepcopy(received), context.call_id))
        assert requests
        assert context.call_id == requests[0].call_id
        return "Published"

    tool = Tool("publish", "Publish a payload.", {"$schema": SCHEMA, "type": "object"}, execute)
    async with (
        await create_agent(options(approvals=approve, tools=[tool])) as agent,
        await agent.create_session(SessionOptions(persistence="ephemeral")) as session,
    ):
        result = await session.run(TurnInput([TextPart("Publish the payload.")]))
    assert len(requests) == 1
    assert result.state == "success"
    assert calls == ([(arguments, requests[0].call_id)] if decision == "allow" else [])


@pytest.mark.parametrize("decision", ["allow", "deny"])
async def test_run_approval_sees_skill_shell_command_and_its_actual_directory(
    monkeypatch,
    tmp_path,
    decision,
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

    async with (
        await create_agent(options(approvals=approve, skills=[str(skill)])) as agent,
        await agent.create_session(SessionOptions(persistence="ephemeral")) as session,
    ):
        result = await session.run(TurnInput([TextPart("Load the receipt skill.")]))
    assert [request.name for request in requests] == ["load_skill", "bash"]
    assert requests[1].arguments == {"command": "printf skill-receipt > receipt.txt"}
    assert result.state == "success"
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
        assert "[truncated]" in request.summary
        assert "[redacted]" in request.summary
        assert "executor-credential" not in request.summary
        assert request.arguments == original
        requests.append(copy.deepcopy(request))
        request.summary = "A caller mutation"
        return ApprovalResponse("allow")

    async def execute(received, context):
        calls.append(copy.deepcopy(received))
        assert requests
        return "Published"

    tool = Tool("publish", "Publish a payload.", {"$schema": SCHEMA, "type": "object"}, execute)
    async with (
        await create_agent(options(approvals=approve, tools=[tool])) as agent,
        await agent.create_session(SessionOptions(persistence="ephemeral")) as session,
    ):
        turn = await session.start_turn(TurnInput([TextPart("Publish the payload.")]))
        events = [event async for event in turn.events()]
    assert events[-1].payload.state == "success"
    paired = [
        event
        for event in events
        if event.type
        in (
            "tool_call",
            "approval_request",
            "approval_decision",
            "tool_result",
        )
    ]
    assert [event.type for event in paired] == [
        "tool_call",
        "approval_request",
        "approval_decision",
        "tool_result",
    ]
    assert paired[0].payload.call.arguments == original == arguments
    assert calls == [original]
    assert paired[1].payload.request == requests[0]
    assert paired[1].payload.request.arguments == paired[0].payload.call.arguments
    assert paired[2].payload.resolution.request_id == requests[0].request_id
    assert paired[3].payload.resolution.call_id == paired[0].payload.call.call_id


@pytest.mark.parametrize("policy", ["handler", "static"])
async def test_denied_call_returns_to_the_model_and_the_turn_continues(monkeypatch, policy):
    probe = provision(monkeypatch, "publish", {"target": "production"})
    calls = []

    async def approve(request):
        return ApprovalResponse("deny", "Publish to staging only.")

    async def execute(received, context):
        calls.append(received)
        return "Published"

    tool = Tool("publish", "Publish a payload.", {"$schema": SCHEMA, "type": "object"}, execute)
    async with (
        await create_agent(
            options(approvals=approve if policy == "handler" else "deny", tools=[tool], tool_error_policy="stop")
        ) as agent,
        await agent.create_session(SessionOptions(persistence="ephemeral")) as session,
    ):
        turn = await session.start_turn(TurnInput([TextPart("Publish the payload.")]))
        events = [event async for event in turn.events()]
    assert calls == []
    assert [event.type for event in events if event.type.startswith(("tool_", "approval_"))] == [
        "tool_call",
        "approval_request",
        "approval_decision",
        "tool_result",
    ]
    (decision,) = [event.payload.resolution for event in events if event.type == "approval_decision"]
    assert decision.decision == "deny"
    (resolution,) = [event.payload.resolution for event in events if event.type == "tool_result"]
    assert resolution.outcome == "cancelled"
    assert resolution.error is not None
    assert resolution.error.code == "approval_denied"
    assert resolution.error.category == "approval"
    if policy == "handler":
        assert decision.reason == "Publish to staging only."
        assert "Publish to staging only." in resolution.error.message
    assert events[-1].payload.state == "success"
    assert events[-1].payload.error is None
    (message,) = [message for message in probe.requests[1]["messages"] if message["role"] == "tool"]
    assert "approval_denied" in str(message["content"])
    assert ("Publish to staging only." in str(message["content"])) == (policy == "handler")


@pytest.mark.parametrize(
    ("answer", "state", "code"),
    [("cancel", "cancelled", "approval_cancelled"), ("allow-later", "failure", "approval_invalid")],
)
async def test_cancel_and_invalid_answers_still_end_the_turn(monkeypatch, answer, state, code):
    provision(monkeypatch, "publish", {"target": "production"})

    async def approve(request):
        return ApprovalResponse(answer)

    async def execute(received, context):
        raise AssertionError("A refused call must not run.")

    tool = Tool("publish", "Publish a payload.", {"$schema": SCHEMA, "type": "object"}, execute)
    async with (
        await create_agent(options(approvals=approve, tools=[tool])) as agent,
        await agent.create_session(SessionOptions(persistence="ephemeral")) as session,
    ):
        result = await session.run(TurnInput([TextPart("Publish the payload.")]))
    assert result.state == state
    assert result.error is not None
    assert result.error.code == code

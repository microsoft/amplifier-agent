"""Public approval scope comes from the executor, across the binding boundary."""

import asyncio

from amplifier_agent import AgentOptions, ApprovalResponse, SessionOptions, TextPart, TurnInput, create_agent

from tests.support.engine import provision


async def test_builtin_bash_public_scope_binds_real_disposable_execution(monkeypatch, tmp_path):
    provision(
        monkeypatch,
        [{"tool": {"name": "bash", "arguments": {"command": "printf approved > receipt.txt"}}}, {"text": "Done"}],
    )
    requests = []

    async def approve(request):
        requests.append(request)
        assert not (tmp_path / "receipt.txt").exists()
        return ApprovalResponse("allow")

    options = AgentOptions(
        provider="anthropic", model="claude-sonnet-5", tools=["bash"], working_directory=tmp_path, approvals=approve
    )
    async with (
        asyncio.timeout(10),
        await create_agent(options) as agent,
        await agent.create_session(SessionOptions(persistence="ephemeral")) as session,
    ):
        result = await session.run(TurnInput([TextPart("Record one receipt.")]))
    assert result.state == "success"
    assert (tmp_path / "receipt.txt").read_text() == "approved"
    assert len(requests) == 1
    scope = getattr(requests[0], "reusable_approval", None)
    assert scope is not None
    assert type(scope).__module__ == "amplifier_agent._records"
    assert scope.version == 1
    assert scope.authority_lifetime == "request"
    assert len(scope.key) == 64

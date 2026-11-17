"""Public MCP approval evidence with inert local servers and a scripted model."""

import asyncio
import json
import os
from pathlib import Path
import socket
import sys

from amplifier_agent import AgentOptions, ApprovalResponse, McpServer, SessionOptions, TextPart, TurnInput, create_agent
import pytest

from tests.support.engine import provision

SERVICE = Path(__file__).parents[3] / "tests/support/mcp_service.py"


@pytest.mark.parametrize("transport", ["stdio", "http"])
async def test_mcp_public_scope_binds_actual_local_dispatch(monkeypatch, tmp_path, transport):
    ledger = tmp_path / "effects.jsonl"
    environment = {"PATH": os.defpath, "HOME": str(tmp_path), "MCP_LEDGER": str(ledger), "MCP_CAPTURED": "synthetic"}
    monkeypatch.setattr(os, "environ", environment)
    process = None
    server = McpServer("ledger", "stdio", command=sys.executable, args=[str(SERVICE)])
    if transport == "http":
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(SERVICE),
            "--transport",
            "http",
            "--port",
            str(port),
            env=environment,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            async with asyncio.timeout(5):
                while True:
                    try:
                        _, writer = await asyncio.open_connection("127.0.0.1", port)
                        writer.close()
                        await writer.wait_closed()
                        break
                    except OSError:
                        await asyncio.sleep(0.02)
        except BaseException:
            if process.returncode is None:
                process.terminate()
                await asyncio.wait_for(process.wait(), 5)
            raise
        server = McpServer("ledger", "http", url=f"http://127.0.0.1:{port}/mcp", headers={"Authorization": "synthetic"})
    requests = []

    async def approve(request):
        assert not ledger.exists()
        assert request.arguments == {"value": "one"}
        scope = request.reusable_approval
        assert scope is not None
        assert type(scope).__module__ == "amplifier_agent._records"
        assert scope.producer == "com.microsoft.amplifier-agent.approvals/1"
        assert scope.authority_lifetime == "request"
        assert len(scope.key) == 64
        assert "synthetic" not in scope.description
        requests.append(request)
        return ApprovalResponse("allow")

    try:
        provision(
            monkeypatch, [{"tool": {"name": "mcp_ledger_record", "arguments": {"value": "one"}}}, {"text": "Done"}]
        )
        options = AgentOptions(
            provider="anthropic",
            model="claude-sonnet-5",
            tools=[],
            mcp_servers=[server],
            working_directory=tmp_path,
            approvals=approve,
        )
        async with (
            asyncio.timeout(15),
            await create_agent(options) as agent,
            await agent.create_session(SessionOptions(persistence="ephemeral")) as session,
        ):
            result = await session.run(TurnInput([TextPart("Record the synthetic receipt once.")]))
        assert result.state == "success"
        assert len(requests) == 1
        rows = [json.loads(line) for line in ledger.read_text().splitlines()]
        assert len(rows) == 1
        assert rows[0]["value"] == "one"
        assert rows[0]["captured"] == "synthetic"
    finally:
        if process is not None and process.returncode is None:
            process.terminate()
            await asyncio.wait_for(process.wait(), 5)

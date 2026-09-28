"""Inject incompatible startup behavior into the scripted test runtime."""

import os
from pathlib import Path


def install() -> None:
    mode = os.environ.get("SCRIPTED_STARTUP_MODE")
    if mode is None:
        return
    if mode == "unavailable":
        raise SystemExit(42)
    if mode != "version_mismatch":
        raise ValueError("Unknown scripted startup mode")

    from amplifier_agent_engine._runtime.server import RuntimeServer

    dispatch = RuntimeServer.dispatch

    async def incompatible(server, message):
        if message.get("method") == "hello":
            await server.send(
                {
                    "id": message.get("id"),
                    "result": {"contract_versions": ["agent-interface/999"]},
                }
            )
            return
        if message.get("method") == "agent.create":
            ledger = os.environ.get("SCRIPTED_RUNTIME_LOG")
            if ledger:
                Path(ledger).write_text("agent.create\n")
        await dispatch(server, message)

    setattr(RuntimeServer, "dispatch", incompatible)

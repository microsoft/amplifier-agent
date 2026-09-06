"""Inject incompatible startup behavior into the conformance runtime."""

import os
from pathlib import Path


def install() -> None:
    mode = os.environ.get("CONFORMANCE_STARTUP_MODE")
    callback_fault = os.environ.get("CONFORMANCE_CALLBACK_FAULT")
    if mode is None and callback_fault is None:
        return
    if mode == "unavailable":
        raise SystemExit(42)
    if mode not in (None, "version_mismatch"):
        raise ValueError("Unknown conformance startup mode")
    if callback_fault not in (None, "wrong_correlation", "duplicate_result", "late_result"):
        raise ValueError("Unknown conformance callback fault")

    from amplifier_agent_engine._runtime.server import RuntimeServer

    dispatch = RuntimeServer.dispatch
    delayed = []

    async def incompatible(server, message):
        if message.get("method") == "session.start_turn" and delayed:
            for reply in delayed[:]:
                await dispatch(server, reply)
            delayed.clear()
            ledger = os.environ.get("CONFORMANCE_BOOTSTRAP_OBSERVATIONS")
            if ledger:
                with Path(ledger).open("a") as output:
                    output.write("late-resolution\n")
        if mode == "version_mismatch" and message.get("method") == "hello":
            await server.send({
                "id": message.get("id"),
                "result": {"contract_versions": ["agent-interface/999"]},
            })
            return
        if message.get("method") == "callback.resolve" and callback_fault:
            if callback_fault == "wrong_correlation":
                message = {**message, "params": {**message["params"], "call_id": "wrong-call"}}
            elif callback_fault == "duplicate_result":
                await dispatch(server, message)
            elif callback_fault == "late_result" and "call_id" in message["params"]:
                params = {key: value for key, value in message["params"].items() if key != "error"}
                delayed.append({**message, "params": {**params, "result": "Late definitive success"}})
        if message.get("method") == "agent.create":
            ledger = os.environ.get("CONFORMANCE_BOOTSTRAP_OBSERVATIONS")
            if ledger:
                Path(ledger).write_text("agent.create\n")
        await dispatch(server, message)

    RuntimeServer.dispatch = incompatible

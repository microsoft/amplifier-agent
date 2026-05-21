#!/usr/bin/env python3
"""Conformance runner — Python.

Loads a YAML fixture (Plan 2 loader), drives JsonRpcClient through a
ScriptedTransport that replays server_to_client frames in script order,
captures all observable events, evaluates fixture assertions, and emits
a structured JSON conformance report to stdout.

Usage:
    python runner_py.py <fixture_path>

Exit code 0 = all assertions passed, 1 = one or more failures.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from amplifier_agent_client.jsonrpc import JsonRpcClient

from amplifier_agent_lib.protocol.conformance.loader import Fixture, load_fixture

# ---------------------------------------------------------------------------
# ScriptedTransport
# ---------------------------------------------------------------------------


class ScriptedTransport:
    """Stub transport that replays server_to_client frames in script order.

    When ``send()`` is called (by the JSON-RPC client sending a
    client_to_server frame), the transport:
    1. Advances past the corresponding client_to_server entry in the script.
    2. Synchronously delivers all subsequent server_to_client frames to
       registered callbacks, stopping before the next client_to_server frame.

    This allows JSON-RPC Futures to be resolved synchronously during
    ``send()``, so ``await rpc.call(...)`` returns without suspending.
    """

    def __init__(self, script: list[dict[str, Any]]) -> None:
        self._script = script
        self._pos = 0
        self._frame_cbs: list[Any] = []

    def on_frame(self, cb: Any) -> None:
        """Register a callback for incoming (server_to_client) frames."""
        self._frame_cbs.append(cb)

    def send(self, obj: Any) -> None:
        """Consume the next client frame and replay subsequent server frames."""
        # Advance past the current client_to_server frame.
        while self._pos < len(self._script):
            frame = self._script[self._pos]
            self._pos += 1
            if frame["direction"] == "client_to_server":
                break

        # Deliver all subsequent server_to_client frames.
        while self._pos < len(self._script):
            frame = self._script[self._pos]
            if frame["direction"] == "client_to_server":
                break  # Stop: the next client action has not happened yet.
            wire = _to_wire(frame)
            self._pos += 1
            for cb in self._frame_cbs:
                cb(wire)


def _to_wire(frame: dict[str, Any]) -> dict[str, Any]:
    """Convert a fixture script frame to a JSON-RPC wire frame dict."""
    wire: dict[str, Any] = {}
    for key in ("id", "method", "params", "result", "error"):
        if key in frame:
            wire[key] = frame[key]
    return wire


# ---------------------------------------------------------------------------
# run_fixture
# ---------------------------------------------------------------------------


async def run_fixture(fixture_path: str | Path) -> dict[str, Any]:
    """Load and execute a fixture, returning a conformance report dict."""
    fixture = load_fixture(fixture_path)
    transport = ScriptedTransport(fixture.script)
    rpc = JsonRpcClient(transport)

    # all_notifs: every notification seen by the consumer (includes synthesized).
    # engine_notifs: only notifications that came from the scripted transport.
    all_notifs: list[dict[str, Any]] = []
    engine_notifs: list[dict[str, Any]] = []

    def on_notif(notif: dict[str, Any]) -> None:
        engine_notifs.append(notif)
        all_notifs.append(notif)

    rpc.on_notification(on_notif)

    responses: dict[int, Any] = {}
    errors: dict[int, Any] = {}

    for frame in fixture.script:
        if frame["direction"] != "client_to_server":
            continue

        method: str = frame["method"]
        params: Any = frame.get("params")
        frame_id: int = frame.get("id", 0)

        try:
            result = await rpc.call(method, params)
            responses[frame_id] = result

            # L14 safety net: after turn/submit, synthesize result/final if the
            # engine omitted it but provided a non-null reply.
            if method == "turn/submit":
                saw_final = any(n.get("method") == "result/final" for n in engine_notifs)
                reply: str | None = result.get("reply") if isinstance(result, dict) else None
                if not saw_final and reply is not None:
                    session_id: str = (params or {}).get("sessionId", "")
                    turn_id: str = (params or {}).get("turnId", "")
                    synth: dict[str, Any] = {
                        "method": "result/final",
                        "params": {
                            "sessionId": session_id,
                            "turnId": turn_id,
                            "text": reply,
                            "synthesized": True,
                        },
                    }
                    all_notifs.append(synth)  # NOT added to engine_notifs

        except Exception as exc:
            errors[frame_id] = exc

    return _evaluate(fixture, all_notifs, engine_notifs, responses, errors)


# ---------------------------------------------------------------------------
# _evaluate
# ---------------------------------------------------------------------------


def _evaluate(
    fixture: Fixture,
    all_notifs: list[dict[str, Any]],
    engine_notifs: list[dict[str, Any]],
    responses: dict[int, Any],
    errors: dict[int, Any],
) -> dict[str, Any]:
    """Evaluate fixture assertions against captured events and return a report."""
    results: list[dict[str, Any]] = []

    for assertion in fixture.assertions:
        kind: str = assertion["kind"]

        if kind == "notification_emitted":
            method = assertion["method"]
            payload_contains: dict[str, Any] | None = assertion.get("payload_contains")
            passed = False
            for notif in all_notifs:
                if notif.get("method") != method:
                    continue
                if payload_contains is not None:
                    notif_params = notif.get("params") or {}
                    if not _dict_contains(notif_params, payload_contains):
                        continue
                passed = True
                break
            results.append({
                "kind": kind,
                "passed": passed,
                "detail": f"notification {method!r} {'found' if passed else 'not found'}",
            })

        elif kind == "no_notification":
            method = assertion["method"]
            source: str | None = assertion.get("source")
            # When source == "engine", only check engine-emitted notifications.
            check_list = engine_notifs if source == "engine" else all_notifs
            found = any(n.get("method") == method for n in check_list)
            passed = not found
            results.append({
                "kind": kind,
                "passed": passed,
                "detail": f"notification {method!r} {'unexpectedly found' if not passed else 'correctly absent'}",
            })

        elif kind == "error_returned":
            assertion_id: int | None = assertion.get("id")
            code: str | None = assertion.get("code")
            if assertion_id in errors:
                error_str = str(errors[assertion_id])
                passed = code is None or code in error_str
            else:
                passed = False
            results.append({
                "kind": kind,
                "passed": passed,
                "detail": f"error for id={assertion_id}: {'found' if passed else 'not found'}",
            })

        elif kind == "response_matches":
            assertion_id = assertion.get("id")
            expected: dict[str, Any] = assertion.get("result", {})
            actual = responses.get(assertion_id)
            passed = actual is not None and isinstance(actual, dict) and _dict_contains(actual, expected)
            results.append({
                "kind": kind,
                "passed": passed,
                "detail": f"response for id={assertion_id}: {'matches' if passed else 'no match'}",
            })

        else:
            # Unknown assertion kinds are skipped with ok=True per spec.
            results.append({
                "kind": kind,
                "passed": True,
                "detail": f"kind {kind!r} not evaluated (skipped)",
            })

    return {
        "fixture": fixture.name,
        "language": "python",
        "passed": all(r["passed"] for r in results),
        "assertions": results,
    }


def _dict_contains(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    """Return True if all key-value pairs in *expected* are present in *actual*."""
    for k, v in expected.items():
        if k not in actual:
            return False
        if isinstance(v, dict) and isinstance(actual[k], dict):
            if not _dict_contains(actual[k], v):
                return False
        elif actual[k] != v:
            return False
    return True


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    """CLI entry point: runner_py.py <fixture_path>"""
    if len(argv) < 2:
        print("Usage: runner_py.py <fixture_path>", file=sys.stderr)
        return 1

    fixture_path = argv[1]
    report = asyncio.run(run_fixture(fixture_path))
    print(json.dumps(report))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))

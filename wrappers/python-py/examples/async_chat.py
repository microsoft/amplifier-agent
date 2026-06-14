"""Async chat example — exercise the full async surface of amplifier-agent-py.

Demonstrates:
  - spawn_agent() with explicit configuration
  - Async iteration over DisplayEvents
  - Discrimination by event.type (init / activity / notification / result / error)
  - Inline display.on_event callback (sync)
  - Clean disposal via try/finally

Run:
    # 1. Install the engine binary (BYO-engine model)
    uv tool install amplifier-agent

    # 2. Configure provider credentials the engine needs
    export ANTHROPIC_API_KEY=sk-ant-...

    # 3. Run this example
    python examples/async_chat.py "Summarize what amplifier-agent is in two sentences."
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid

from amplifier_agent_py import (
    AaaError,
    DisplayEvent,
    spawn_agent,
)

# Provider credential env vars the engine commonly looks for at startup.
# We forward them via env.extra so they bypass the wrapper's strict
# DEFAULT_ALLOWLIST (which intentionally excludes secrets by default).
_PROVIDER_KEYS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_ENDPOINT",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
)


def _forward_provider_credentials() -> dict[str, str]:
    """Pick up provider credentials from the caller's environment.

    Returns a dict suitable for passing as ``env={"extra": ...}``.  Empty if
    the caller has no provider keys set — the engine will then fail with
    "No providers available" and the example reports the error.
    """
    return {k: os.environ[k] for k in _PROVIDER_KEYS if k in os.environ}


def on_wire_event(event: DisplayEvent) -> None:
    """Sync callback invoked for each wire-protocol notification.

    Mirrors what a host UI would do — surface tool calls and thinking deltas
    as they happen.  Errors thrown here are swallowed by the wrapper so they
    cannot poison the stream.
    """
    if event.type != "notification":
        return
    method = event.method
    params = event.params if isinstance(event.params, dict) else {}
    if method == "tool/started":
        name = params.get("name", "?")
        print(f"  [tool/started] {name}", file=sys.stderr)
    elif method == "tool/completed":
        name = params.get("name", "?")
        duration = params.get("durationMs", 0)
        print(f"  [tool/completed] {name} ({duration} ms)", file=sys.stderr)
    elif method == "thinking/delta":
        text = params.get("text", "")
        if text:
            print(f"  [thinking] {text}", file=sys.stderr)


async def run(prompt: str) -> int:
    """Spawn the agent, submit a prompt, and stream events to stdout/stderr.

    Returns the process exit code (0 on success).
    """
    session_id = f"async-chat-{uuid.uuid4().hex[:8]}"

    try:
        handle = await spawn_agent(
            session_id=session_id,
            display_mode="ndjson",
            approval={"mode": "yes"},
            display={"on_event": on_wire_event},
            # Forward provider credentials via env.extra.  The wrapper's
            # DEFAULT_ALLOWLIST intentionally excludes secrets; hosts must
            # opt in by passing each key the engine needs.
            env={"extra": _forward_provider_credentials()},
            timeout_ms=5 * 60 * 1000,  # 5 minute cap
        )
    except AaaError as e:
        print(f"spawn failed: {e.code} — {e.remediation or e}", file=sys.stderr)
        return 1

    info = handle.get_engine_info()
    print(
        f"engine: {info.engine_version} (protocol {info.protocol_version}) @ {info.binary_path}",
        file=sys.stderr,
    )

    try:
        async for event in handle.submit(prompt):
            if event.type == "init":
                print(f"[init] session_id={event.session_id}", file=sys.stderr)
            elif event.type == "activity":
                # Heartbeat — uncomment for verbose tracing.
                # print(".", end="", flush=True, file=sys.stderr)
                pass
            elif event.type == "notification":
                # Already surfaced via on_wire_event; nothing more to do here.
                pass
            elif event.type == "result":
                print(event.text)
                return 0
            elif event.type == "error":
                print(
                    f"[error] {event.code} ({event.classification}/{event.severity}): {event.message}",
                    file=sys.stderr,
                )
                if event.stderr_tail:
                    print(f"--- stderr tail ---\n{event.stderr_tail}", file=sys.stderr)
                return 1
    finally:
        await handle.dispose()

    return 1


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: async_chat.py <prompt>", file=sys.stderr)
        return 2
    prompt = " ".join(sys.argv[1:])
    return asyncio.run(run(prompt))


if __name__ == "__main__":
    sys.exit(main())

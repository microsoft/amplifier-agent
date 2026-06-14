"""Sync chat example — uses the sync convenience wrapper.

Demonstrates spawn_agent_sync() + the context manager pattern for Python hosts
that are not asyncio-native (Django, Flask, CLI scripts, notebooks).

Run:
    uv tool install amplifier-agent
    export ANTHROPIC_API_KEY=sk-ant-...
    python examples/sync_chat.py "What can you do?"
"""

from __future__ import annotations

import os
import sys
import uuid

from amplifier_agent_py import AaaError, spawn_agent_sync

# Forward provider credentials via env.extra so they bypass the strict
# DEFAULT_ALLOWLIST.  See async_chat.py for the same pattern explained.
_PROVIDER_KEYS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_ENDPOINT",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
)


def _forward_provider_credentials() -> dict[str, str]:
    return {k: os.environ[k] for k in _PROVIDER_KEYS if k in os.environ}


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: sync_chat.py <prompt>", file=sys.stderr)
        return 2
    prompt = " ".join(sys.argv[1:])

    session_id = f"sync-chat-{uuid.uuid4().hex[:8]}"

    try:
        with spawn_agent_sync(
            session_id=session_id,
            display_mode="ndjson",
            approval={"mode": "yes"},
            env={"extra": _forward_provider_credentials()},
            timeout_ms=5 * 60 * 1000,
        ) as handle:
            info = handle.get_engine_info()
            print(
                f"engine: {info.engine_version} (protocol {info.protocol_version})",
                file=sys.stderr,
            )

            for event in handle.submit(prompt):
                if event.type == "result":
                    print(event.text)
                    return 0
                if event.type == "error":
                    print(
                        f"[error] {event.code}: {event.message}",
                        file=sys.stderr,
                    )
                    return 1
    except AaaError as e:
        print(f"spawn failed: {e.code} — {e.remediation or e}", file=sys.stderr)
        return 1

    return 1


if __name__ == "__main__":
    sys.exit(main())

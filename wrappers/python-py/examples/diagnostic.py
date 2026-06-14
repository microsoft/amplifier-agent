"""Diagnostic example — verifies the wrapper can find and probe the engine.

Runs only the spawn-time gates: binary discovery, env build, version probe,
protocol check.  Does NOT submit a turn, so it does NOT require provider
credentials.  Useful for verifying install correctness in CI or a DTU.

Exits 0 on success, non-zero on any failure with an actionable message.

Run:
    uv tool install amplifier-agent
    python examples/diagnostic.py
"""

from __future__ import annotations

import asyncio
import sys

from amplifier_agent_py import (
    PROTOCOL_VERSION_REQUIRED_BY_WRAPPER,
    AaaError,
    resolve_binary_path,
    spawn_agent,
)


async def run() -> int:
    # Phase 1: binary discovery.
    try:
        bin_path = resolve_binary_path()
    except AaaError as e:
        print(f"FAIL: {e.code} — {e.remediation}", file=sys.stderr)
        return 1
    print(f"OK   binary discovered: {bin_path}")

    # Phase 2: spawn_agent (runs version probe + protocol check, no subprocess yet).
    try:
        handle = await spawn_agent(session_id="diag-1")
    except AaaError as e:
        print(f"FAIL: {e.code} — {e.remediation}", file=sys.stderr)
        if e.stderr_tail:
            print(f"--- stderr tail ---\n{e.stderr_tail}", file=sys.stderr)
        return 1

    info = handle.get_engine_info()
    print(f"OK   protocol version: wrapper={PROTOCOL_VERSION_REQUIRED_BY_WRAPPER} engine={info.protocol_version}")
    print(f"OK   engine version:   {info.engine_version}")
    if info.bundle_digest:
        print(f"OK   bundle digest:    {info.bundle_digest}")
    print("OK   spawn_agent() returned a SessionHandle without launching the engine subprocess.")

    await handle.dispose()
    return 0


def main() -> int:
    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Slow-MCP fixture binary for the SC-B orphan-cleanup conformance test.

This script simulates an MCP child that ignores SIGTERM. The wrapper's
``cancel()`` path is required by amendment §5.2 / §8.1 A4' to:

1. Send SIGTERM to the engine's process group (which transitively reaches
   every MCP child the engine launched as a session leader, per
   SC-B / commit ``efe0765``).
2. Wait up to 5 seconds (``SIGKILL_GRACE_MS`` in
   ``wrappers/typescript/src/session.ts``) for the group to exit.
3. Escalate to SIGKILL on the same group if anything is still alive.

This binary's only job is to *fail step 2*: it installs a no-op SIGTERM
handler so the signal is delivered but ignored, then sleeps long enough
that the test will *always* observe the SIGKILL escalation path unless
the wrapper's cancel is broken.

The body is deliberately the exact snippet from the design plan
(``docs/plans/2026-05-24-mode-a-pivot-phase-b-wrapper-conformance.md``
§"Task 19 / Step 1") so the fixture has zero accidental behaviour
beyond what the spec mandates.

NOTE: This is NOT a real MCP server. It performs no stdio handshake,
emits no JSON-RPC, and does not respond to ``initialize``. For the
purposes of SC-B the only contract that matters is "the engine
spawned a child process under its process group, and that child
ignores SIGTERM". Anything past that point is irrelevant to the
orphan-cleanup assertion.
"""

import signal
import sys
import time

# Install a no-op SIGTERM handler. Per signal(7), the handler will be invoked
# on receipt but the process will not terminate — exactly the malicious-MCP
# scenario SC-B must defend against.
signal.signal(signal.SIGTERM, lambda *_: None)

# Print a readiness marker on stderr so the harness (or a human debugger)
# can confirm the child actually launched before the wrapper's cancel fires.
# stdout is reserved for MCP wire traffic in a real server; we keep it clean
# even though no traffic is emitted here.
sys.stderr.write("slow-mcp started\n")
sys.stderr.flush()

# Sleep far longer than the wrapper's 10s grace+kill window. The wrapper MUST
# SIGKILL us — if it doesn't, the harness will observe a still-alive PID well
# past the 10s budget and fail the SC-B assertion. 120s is comfortably above
# any plausible scheduling jitter on a loaded CI box.
time.sleep(120)

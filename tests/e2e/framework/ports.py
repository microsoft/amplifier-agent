"""Every in-DTU TCP port the e2e suite binds, declared in ONE place.

**Ports must be unique across suites, not merely within a suite.** Most of these
servers are started by session-scoped fixtures, so a port stays bound from the
moment its suite first runs until the ENTIRE pytest session ends -- teardown does
not happen when the owning suite finishes. Two suites that pick the same number
therefore collide deterministically (``[Errno 98] address already in use``)
whenever they run in the same session, while each still passes in isolation.
That failure mode is invisible until someone runs the suites together, which is
why the numbers live here rather than as literals in each conftest.

Adding a suite that needs its own server? Add its port here first, and pick a
value no other entry uses.
"""

from __future__ import annotations

# The shared session-wide HTTP server (`server` fixture in tests/e2e/conftest.py).
# Most HTTP cases talk to this one.
SHARED_SERVER_PORT = 9099

# suites/raw_capture -- needs a server started WITH `--config host-config-raw.json`,
# which the shared server is not.
RAW_CAPTURE_PORT = 9098

# suites/shadowing -- needs a server booted AFTER its colliding skill is seeded,
# because skill discovery is frozen at server startup.
SHADOWING_PORT = 9097

# suites/raw_capture -- a SECOND server for that suite, started with no `--config`
# at all and only `$AMPLIFIER_AGENT_CONFIG` in its environment. It cannot share
# RAW_CAPTURE_PORT: both are session-scoped and must run concurrently, and the pair
# is only meaningful if they differ in exactly one thing (how the config path
# reaches the process).
RAW_CAPTURE_ENV_PORT = 9096


def self_safe_pkill(port: int) -> str:
    """Return a ``pkill`` command that kills our server on ``port`` and not itself.

    Scoped by ``--port <n>`` so servers on the other ports above are never
    collateral. The last digit is bracketed ("909[8]") so the pkill command line
    cannot match ITS OWN regex: ``pkill -f`` searches full argv, and this
    process's argv carries the literal text "909[8]", which the regex "909[8]"
    does not match. Without the bracket, pkill can kill itself and orphan the
    very server it was meant to stop.
    """
    digits = str(port)
    pattern = f"{digits[:-1]}[{digits[-1]}]"
    return f"pkill -f -- '--port {pattern}' || true"

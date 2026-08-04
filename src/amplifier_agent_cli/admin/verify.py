"""Admin command: verify — verify installation and hook coverage.

Provides self-test commands for validating the installation.

--check-hooks: Verifies that the streaming hook exposes the required
minimum set of canonical wire events, gating the streaming-hook
implementation (Task 11) and Phase 2.0c exit.
"""

from __future__ import annotations

import asyncio
import importlib
import sys

import click

# ---------------------------------------------------------------------------
# Minimum wire-event set required for streaming-hook coverage
# ---------------------------------------------------------------------------

_MINIMUM_SET: tuple[str, ...] = (
    "result/delta",
    "result/final",
    "tool/started",
    "tool/completed",
    "usage",
)


# ---------------------------------------------------------------------------
# Internal check functions
# ---------------------------------------------------------------------------


async def _check_hooks() -> None:
    """Verify streaming hook exposes the minimum required wire events.

    Imports ``amplifier_agent_lib.bundle.hook_streaming`` and checks that
    ``CANONICAL_WIRE_EVENTS`` covers all events in ``_MINIMUM_SET``, and that
    a callable ``mount`` is exposed.  Hook is mounted programmatically by
    ``_runtime.make_turn_handler`` at session-creation time; live-coordinator
    mounting is exercised end-to-end by the ``tests/e2e/suites/streaming``
    suite, which runs the real CLI and HTTP server in a DTU.

    Exits 0 on success; exits 1 with a ``[FAIL]`` message on any failure.
    """
    try:
        streaming_mod = importlib.import_module("amplifier_agent_lib.bundle.hook_streaming")
    except ImportError as exc:
        click.echo(f"[FAIL] amplifier_agent_lib.bundle.hook_streaming not importable: {exc}")
        sys.exit(1)

    wire_events = getattr(streaming_mod, "CANONICAL_WIRE_EVENTS", None)
    if wire_events is None:
        click.echo("[FAIL] hook_streaming.CANONICAL_WIRE_EVENTS attribute not found")
        sys.exit(1)

    missing = [e for e in _MINIMUM_SET if e not in wire_events]
    if missing:
        click.echo("[FAIL] missing canonical wire events: " + ", ".join(missing))
        sys.exit(1)

    mount = getattr(streaming_mod, "mount", None)
    if not callable(mount):
        click.echo("[FAIL] hook_streaming.mount is not a callable — programmatic mount would fail")
        sys.exit(1)

    click.echo("[ OK ] hook coverage passes — all minimum-set events present")


# ---------------------------------------------------------------------------
# 'verify' command
# ---------------------------------------------------------------------------


@click.command()
@click.option(
    "--check-hooks",
    "check_hooks",
    is_flag=True,
    default=False,
    help="Verify that the streaming hook exposes the required canonical wire events.",
)
def verify(check_hooks: bool) -> None:
    """Verify the installation and hook coverage."""
    if check_hooks:
        asyncio.run(_check_hooks())
        return

    click.echo("[ OK ] verify: nothing to check (use --check-hooks for hook coverage)")

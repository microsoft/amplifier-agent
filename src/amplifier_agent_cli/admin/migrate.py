"""Admin command: migrate — user-invoked storage layout migrations.

Runs both storage migrations in order:
  1. migrate_legacy_sessions_if_needed()  — workspace flat-to-nested migration
  2. maybe_migrate_legacy_xdg_storage()   — XDG-to-~/.amplifier-agent/ migration

Neither migration runs automatically anywhere in the engine. Use this command
to migrate legacy storage on demand. Idempotent: safe to run multiple times.
"""

from __future__ import annotations

import json
import sys

import click

from amplifier_agent_lib.migration import (
    maybe_migrate_legacy_xdg_storage,
    migrate_legacy_sessions_if_needed,
)

__all__ = ["migrate_command"]


@click.command(name="migrate")
@click.option(
    "--output",
    "output",
    type=click.Choice(["text", "json"]),
    default="text",
    help="Output format.",
)
def migrate_command(output: str) -> None:
    """Migrate legacy storage layouts to current. Idempotent; safe to run multiple times. Reports what was moved."""
    try:
        sessions = migrate_legacy_sessions_if_needed()
    except Exception as exc:
        if output == "json":
            click.echo(json.dumps({"error": f"sessions-migration-failed: {exc}"}))
        else:
            click.echo(f"Error: sessions migration failed: {exc}", err=True)
        sys.exit(1)

    try:
        xdg = maybe_migrate_legacy_xdg_storage()
    except Exception as exc:
        if output == "json":
            click.echo(json.dumps({"error": f"xdg-migration-failed: {exc}"}))
        else:
            click.echo(f"Error: XDG migration failed: {exc}", err=True)
        sys.exit(1)

    payload = {
        "sessions_migration": {
            "migrated": sessions.migrated,
            "skipped": sessions.skipped,
            "collided": sessions.collided,
        },
        "xdg_migration": {
            "migrated": xdg.migrated,
            "skipped": xdg.skipped,
            "collided": xdg.collided,
            "from_xdg": xdg.from_xdg,
        },
    }

    if output == "json":
        click.echo(json.dumps(payload))
    else:
        # Sessions migration report
        if sessions.skipped:
            click.echo("\u21bb sessions migration: already done (nothing to move)")
        elif sessions.migrated > 0:
            n = sessions.migrated
            click.echo(f"\u2713 sessions migration: moved {n} session{'s' if n != 1 else ''} to workspaces/_legacy/")
            if sessions.collided > 0:
                nc = sessions.collided
                click.echo(
                    f"  ! {nc} collision{'s' if nc != 1 else ''} \u2014 source{'s' if nc != 1 else ''} left in place"
                )
        else:
            click.echo("  (sessions migration: nothing to move)")

        # XDG migration report — mirrors the style formerly in update.py
        if xdg.skipped:
            click.echo("\u21bb XDG storage migration: already done (sentinel present)")
        elif xdg.migrated > 0:
            n = xdg.migrated
            click.echo(
                f"\u2713 migrated {n} legacy XDG storage director{'y' if n == 1 else 'ies'} to ~/.amplifier-agent/"
            )
            if xdg.collided > 0:
                nc = xdg.collided
                click.echo(
                    f"  ! {nc} director{'y' if nc == 1 else 'ies'} "
                    "skipped (target already exists \u2014 legacy copy left in place)"
                )
        else:
            click.echo("  (no XDG legacy storage found to migrate)")

    sys.exit(0)

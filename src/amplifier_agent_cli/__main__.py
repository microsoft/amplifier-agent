"""amplifier-agent CLI dispatcher.

This module is the entry point for the ``amplifier-agent`` command.  It owns:
- Stdout/stderr discipline: the CLI layer may print to stderr freely; stdout is
  reserved for structured output (JSON-RPC responses, etc.) in Mode A.
- Subcommand routing: all business logic lives in amplifier_agent_lib; this
  module only wires click commands to the engine.
- Lib is mode-agnostic: no I/O is performed here beyond CLI dispatch.

Registered subcommands:
  run          — Mode A single-turn (run "prompt")
  doctor       — Self-diagnostics
  migrate      — Migrate legacy storage layouts to current (user-invoked)
  prepare      — Pre-warm the bundle cache
  verify       — Verify install integrity and hook coverage
  version      — Engine and protocol version
  update       — Check for and install the latest release
  config show  — Show resolved configuration with source annotations
  cache clear  — Clear the prepared-bundle XDG cache
  models list  — Enumerate available models from providers
  skills list  — List user-invocable skills
  modes list   — List shipped modes
  serve        — HTTP server (chat-completions, status, stop, restart)
  auth         — Manage provider credentials
  providers    — Manage provider configuration
"""

from __future__ import annotations

import os
import sys

import click

from amplifier_agent_cli import __version__
from amplifier_agent_cli.admin.auth import auth_group as _auth_group
from amplifier_agent_cli.admin.cache_clear import cache_group as _cache_group
from amplifier_agent_cli.admin.config_show import config_group as _config_group
from amplifier_agent_cli.admin.doctor import doctor as _doctor_command
from amplifier_agent_cli.admin.migrate import migrate_command as _migrate_command
from amplifier_agent_cli.admin.models import models_group as _models_group
from amplifier_agent_cli.admin.modes import modes_group as _modes_group
from amplifier_agent_cli.admin.prepare import prepare as _prepare_command
from amplifier_agent_cli.admin.providers import providers_group as _providers_group
from amplifier_agent_cli.admin.serve import serve_group as _serve_group
from amplifier_agent_cli.admin.skills import skills_group as _skills_group
from amplifier_agent_cli.admin.update import update_command as _update_command
from amplifier_agent_cli.admin.verify import verify as _verify_command
from amplifier_agent_cli.admin.version_info import version_command as _version_command
from amplifier_agent_cli.modes.single_turn import run as _run_command


@click.group(invoke_without_command=True)
@click.version_option(version=__version__, prog_name="amplifier-agent")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """amplifier-agent — Amplifier-as-Agent CLI."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


cli.add_command(_run_command)
cli.add_command(_doctor_command)
cli.add_command(_migrate_command)
cli.add_command(_prepare_command)
cli.add_command(_verify_command)
cli.add_command(_version_command)
cli.add_command(_update_command)
cli.add_command(_config_group, name="config")
cli.add_command(_cache_group, name="cache")
cli.add_command(_models_group, name="models")
cli.add_command(_skills_group, name="skills")
cli.add_command(_modes_group, name="modes")
cli.add_command(_serve_group, name="serve")
cli.add_command(_auth_group, name="auth")
cli.add_command(_providers_group, name="providers")


def _force_utf8_io() -> None:
    """Make stdout and stderr UTF-8 unless the operator picked an encoding.

    Python selects the console code page for stdio, which on Windows is a
    legacy single-byte encoding (cp1252 on the guests we test). Any non-ASCII
    character then raises UnicodeEncodeError at write time -- after the turn has
    run and been paid for, which is the worst possible moment to fail. This is
    not an edge case: em dashes appear in this CLI's own --help text and in
    bundled skill descriptions, so a reply quoting either one is enough.

    Not Windows-specific, and not gated on the platform. POSIX under LC_ALL=C
    selects ASCII for the same reason and fails the same way; the trigger is a
    stdio encoding that cannot represent the payload, not the OS.

    PYTHONIOENCODING is honored when set. An operator who named an encoding
    outranks this default, including when they name a narrow one deliberately.

    Best-effort by construction: a replaced or non-reconfigurable stream is
    skipped rather than fought, because failing here would break the CLI in
    exactly the environments this is meant to protect.
    """
    if os.environ.get("PYTHONIOENCODING"):
        return
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        current = (getattr(stream, "encoding", "") or "").lower().replace("-", "_")
        if current in ("utf_8", "utf8"):
            continue
        try:
            reconfigure(encoding="utf-8")
        except (OSError, ValueError):  # pragma: no cover - stream refused
            pass


def main() -> None:
    """Entry point referenced by pyproject.toml [project.scripts]."""
    _force_utf8_io()
    try:
        cli(standalone_mode=True)
    except KeyboardInterrupt:
        print("\n[info] Interrupted", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()  # pragma: no cover

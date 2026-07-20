"""Admin commands: ``modes`` subgroup with the ``list`` command.

Enumerates the modes the agent ships and discovers. Discovery is delegated to
:mod:`amplifier_agent_lib.resources`, the single source of truth shared with
the HTTP ``GET /v1/modes`` route so the two surfaces always agree.

Note the deliberate name: the group callable is ``modes_group`` (not ``modes``)
to avoid colliding with the existing ``amplifier_agent_cli.modes`` package when
imported into the CLI dispatcher.

Stdout discipline (per amplifier-agent AGENTS.md): with ``--json`` the only
thing written to stdout is the JSON payload — a list of ``{"name",
"description"}`` objects. All diagnostics go to stderr.
"""

from __future__ import annotations

import contextlib
import json
import sys

import click

from amplifier_agent_cli.tty_detect import is_stdout_tty


@click.group(name="modes")
def modes_group() -> None:
    """Enumerate the modes available to the agent."""


@modes_group.command(name="list")
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit the mode list as JSON to stdout (machine-readable).",
)
@click.option(
    "--output",
    "output_mode",
    type=click.Choice(["auto", "json", "table"], case_sensitive=False),
    default="auto",
    show_default=True,
    help="Output format. Ignored when --json is passed.",
)
def modes_list(as_json: bool, output_mode: str) -> None:
    """List the shipped modes."""
    if as_json or output_mode == "json":
        resolved = "json"
    elif output_mode == "table":
        resolved = "table"
    else:
        resolved = "table" if is_stdout_tty() else "json"

    real_stdout = sys.stdout
    from amplifier_agent_lib.resources import list_modes

    if resolved == "json":
        with contextlib.redirect_stdout(sys.stderr):
            modes = list_modes()
        real_stdout.write(json.dumps(modes) + "\n")
        real_stdout.flush()
    else:
        modes = list_modes()
        _render_table(modes)


def _render_table(modes: list[dict[str, str]]) -> None:
    """Render the mode list as a 2-column aligned table to stdout."""
    headers = ("NAME", "DESCRIPTION")
    rows = [(m["name"], m.get("description", "")) for m in modes]
    name_width = max((len(h) for h in (headers[0], *[r[0] for r in rows])), default=len(headers[0]))

    def _fmt(name: str, desc: str) -> str:
        return f"{name.ljust(name_width)}  {desc}".rstrip()

    click.echo(_fmt(*headers))
    for name, desc in rows:
        click.echo(_fmt(name, desc))

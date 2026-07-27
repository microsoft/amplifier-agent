"""Admin commands: ``modes`` subgroup with the ``list`` command.

Enumerates the modes the agent ships and discovers. Discovery is delegated to
:mod:`amplifier_agent_lib.resources`, the single source of truth shared with
the HTTP ``GET /v1/modes`` route so the two surfaces always agree.

Note the deliberate name: the group callable is ``modes_group`` (not ``modes``)
to avoid colliding with the existing ``amplifier_agent_cli.modes`` package when
imported into the CLI dispatcher.

Stdout discipline (per amplifier-agent AGENTS.md): with ``--json`` the only
thing written to stdout is the JSON payload — a list of ``{"name",
"description", "source", "shadowed"}`` objects, where ``source`` is the winning
``<name>.md`` path and ``shadowed`` is a (possibly empty) list of ``{"source"}``
entries naming every same-named file that lost the first-match-wins race. All
diagnostics go to stderr.
"""

from __future__ import annotations

import contextlib
import json
import sys
from typing import Any

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


#: Appended to a table row whose name was also found in a lower-priority root,
#: and reused as the bullet of the footer that expands those conflicts.
_CONFLICT_MARKER = "(!)"


def _render_table(modes: list[dict[str, Any]]) -> None:
    """Render the mode list as a 2-column aligned table to stdout.

    Discovery is first-match-wins, so a same-named mode in a lower-priority root
    is silently discarded. A user whose override was ignored had no way to see
    that from this table, so any entry carrying a non-empty ``shadowed`` list is
    marked with ``(!)`` and expanded in a footer naming both the file that runs
    and every file that lost. No footer is printed when there are no conflicts.
    """
    headers = ("NAME", "DESCRIPTION")
    rows = [(m["name"], m.get("description", ""), bool(m.get("shadowed"))) for m in modes]
    name_width = max((len(h) for h in (headers[0], *[r[0] for r in rows])), default=len(headers[0]))

    def _fmt(name: str, desc: str, conflicted: bool = False) -> str:
        line = f"{name.ljust(name_width)}  {desc}".rstrip()
        return f"{line}  {_CONFLICT_MARKER}" if conflicted else line

    click.echo(_fmt(*headers))
    for name, desc, conflicted in rows:
        click.echo(_fmt(name, desc, conflicted))

    _render_conflicts(modes)


def _render_conflicts(modes: list[dict[str, Any]]) -> None:
    """Print the shadowing footer, or nothing when no name collided."""
    conflicted = [m for m in modes if m.get("shadowed")]
    if not conflicted:
        return

    noun = "conflict" if len(conflicted) == 1 else "conflicts"
    click.echo("")
    click.echo(f"{_CONFLICT_MARKER} {len(conflicted)} name {noun}. The file under 'runs' is the one that runs:")
    for mode in conflicted:
        click.echo(f"  {mode['name']}")
        click.echo(f"    runs:     {mode.get('source', '')}")
        for loser in mode.get("shadowed") or []:
            click.echo(f"    shadowed: {loser.get('source', '')}")

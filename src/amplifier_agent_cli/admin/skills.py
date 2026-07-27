"""Admin commands: ``skills`` subgroup with the ``list`` command.

Enumerates the user-invocable (slash-command) skills the agent ships and
discovers. Discovery is delegated to
:mod:`amplifier_agent_lib.resources`, the single source of truth shared with
the HTTP ``GET /v1/skills`` route so the two surfaces always agree.

Stdout discipline (per amplifier-agent AGENTS.md): with ``--json`` the only
thing written to stdout is the JSON payload — a list of ``{"name",
"description", "source", "shadowed"}`` objects, where ``source`` is the winning
``SKILL.md`` path and ``shadowed`` is a (possibly empty) list of ``{"source"}``
entries naming every same-named file that lost the first-match-wins race. All
diagnostics (and any noise from preparing the bundle to make discovery
importable) go to stderr.
"""

from __future__ import annotations

import contextlib
import json
import sys
from typing import Any

import click

from amplifier_agent_cli.tty_detect import is_stdout_tty
from amplifier_agent_lib.config import ConfigError, load_config


@click.group(name="skills")
def skills_group() -> None:
    """Enumerate the skills available to the agent."""


@skills_group.command(name="list")
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit the skill list as JSON to stdout (machine-readable).",
)
@click.option(
    "--output",
    "output_mode",
    type=click.Choice(["auto", "json", "table"], case_sensitive=False),
    default="auto",
    show_default=True,
    help="Output format. Ignored when --json is passed.",
)
@click.option(
    "--config",
    "config_path",
    default=None,
    type=click.Path(),
    help="Host config file; its skills.skills locations are added to discovery.",
)
def skills_list(as_json: bool, output_mode: str, config_path: str | None) -> None:
    """List the user-invocable skills (slash-command skills)."""
    # Resolve output format: --json wins; otherwise 'auto' -> table on a TTY,
    # json when piped/redirected.
    if as_json or output_mode == "json":
        resolved = "json"
    elif output_mode == "table":
        resolved = "table"
    else:
        resolved = "table" if is_stdout_tty() else "json"

    try:
        config = load_config(config_arg=config_path)
    except ConfigError as exc:
        click.echo(f"# skills list: {exc.message}", err=True)
        sys.exit(2)

    # Keep stdout pristine for the JSON payload: divert any stray stdout writes
    # from discovery/bundle-prepare to stderr, then emit the payload to the real
    # stdout captured before redirection.
    real_stdout = sys.stdout
    from amplifier_agent_lib.resources import list_skills

    if resolved == "json":
        with contextlib.redirect_stdout(sys.stderr):
            skills = list_skills(config)
        real_stdout.write(json.dumps(skills) + "\n")
        real_stdout.flush()
    else:
        skills = list_skills(config)
        _render_table(skills)


#: Appended to a table row whose name was also found in a lower-priority root,
#: and reused as the bullet of the footer that expands those conflicts.
_CONFLICT_MARKER = "(!)"


def _render_table(skills: list[dict[str, Any]]) -> None:
    """Render the skill list as a 2-column aligned table to stdout.

    Discovery is first-match-wins, so a same-named skill in a lower-priority
    root is silently discarded. A user whose override was ignored had no way to
    see that from this table, so any entry carrying a non-empty ``shadowed``
    list is marked with ``(!)`` and expanded in a footer naming both the file
    that runs and every file that lost. No footer is printed when there are no
    conflicts.
    """
    headers = ("NAME", "DESCRIPTION")
    rows = [(s["name"], s.get("description", ""), bool(s.get("shadowed"))) for s in skills]
    name_width = max((len(h) for h in (headers[0], *[r[0] for r in rows])), default=len(headers[0]))

    def _fmt(name: str, desc: str, conflicted: bool = False) -> str:
        line = f"{name.ljust(name_width)}  {desc}".rstrip()
        return f"{line}  {_CONFLICT_MARKER}" if conflicted else line

    click.echo(_fmt(*headers))
    for name, desc, conflicted in rows:
        click.echo(_fmt(name, desc, conflicted))

    _render_conflicts(skills)


def _render_conflicts(skills: list[dict[str, Any]]) -> None:
    """Print the shadowing footer, or nothing when no name collided."""
    conflicted = [s for s in skills if s.get("shadowed")]
    if not conflicted:
        return

    noun = "conflict" if len(conflicted) == 1 else "conflicts"
    click.echo("")
    click.echo(f"{_CONFLICT_MARKER} {len(conflicted)} name {noun}. The file under 'runs' is the one that runs:")
    for skill in conflicted:
        click.echo(f"  {skill['name']}")
        click.echo(f"    runs:     {skill.get('source', '')}")
        for loser in skill.get("shadowed") or []:
            click.echo(f"    shadowed: {loser.get('source', '')}")

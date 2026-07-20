"""Admin commands: ``skills`` subgroup with the ``list`` command.

Enumerates the user-invocable (slash-command) skills the agent ships and
discovers. Discovery is delegated to
:mod:`amplifier_agent_lib.resources`, the single source of truth shared with
the HTTP ``GET /v1/skills`` route so the two surfaces always agree.

Stdout discipline (per amplifier-agent AGENTS.md): with ``--json`` the only
thing written to stdout is the JSON payload — a list of ``{"name",
"description"}`` objects. All diagnostics (and any noise from preparing the
bundle to make discovery importable) go to stderr.
"""

from __future__ import annotations

import contextlib
import json
import sys

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


def _render_table(skills: list[dict[str, str]]) -> None:
    """Render the skill list as a 2-column aligned table to stdout."""
    headers = ("NAME", "DESCRIPTION")
    rows = [(s["name"], s.get("description", "")) for s in skills]
    name_width = max((len(h) for h in (headers[0], *[r[0] for r in rows])), default=len(headers[0]))

    def _fmt(name: str, desc: str) -> str:
        return f"{name.ljust(name_width)}  {desc}".rstrip()

    click.echo(_fmt(*headers))
    for name, desc in rows:
        click.echo(_fmt(name, desc))

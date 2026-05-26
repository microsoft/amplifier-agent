"""Admin command: version — emit engine version and protocol version.

Emits version information in two formats:
  - Plain (default): amplifier-agent <version> (wire <protocolVersion>)
  - JSON (--json flag): {"version": <version>, "protocolVersion": <protocolVersion>}

Used by wrappers for pre-spawn protocol version probes.
"""

from __future__ import annotations

import json

import click

from amplifier_agent_lib import __version__

#: The wire protocol version implemented by this engine build.
#: Wrappers must match this exactly (unless allowProtocolSkew is set).
PROTOCOL_VERSION: str = "0.1.0"


@click.command(name="version")
@click.option("--json", "emit_json", is_flag=True, default=False, help="Emit JSON payload.")
def version_command(emit_json: bool) -> None:
    """Show engine version and wire protocol version."""
    if emit_json:
        click.echo(json.dumps({"version": __version__, "protocolVersion": PROTOCOL_VERSION}))
    else:
        click.echo(f"amplifier-agent {__version__} (wire {PROTOCOL_VERSION})")

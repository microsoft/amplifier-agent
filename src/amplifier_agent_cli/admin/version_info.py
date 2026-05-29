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

# Re-export PROTOCOL_VERSION from the protocol package — the engine's wire
# truth source — rather than restating it here, so the admin/version surface
# can never drift from what `engine.py` and `modes/single_turn.py` actually
# speak. (Prior to this, a hardcoded "0.1.0" survived the 0.2.0 protocol bump
# and shipped a misleading `version --json` payload.)
from amplifier_agent_lib.protocol import PROTOCOL_VERSION

__all__ = ["PROTOCOL_VERSION", "version_command"]


@click.command(name="version")
@click.option("--json", "emit_json", is_flag=True, default=False, help="Emit JSON payload.")
def version_command(emit_json: bool) -> None:
    """Show engine version and wire protocol version."""
    if emit_json:
        click.echo(json.dumps({"version": __version__, "protocolVersion": PROTOCOL_VERSION}))
    else:
        click.echo(f"amplifier-agent {__version__} (wire {PROTOCOL_VERSION})")

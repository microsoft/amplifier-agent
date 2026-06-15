"""CLI entry point for the amplifier-agent HTTP face.

Usage:
    amplifier-agent-http serve [--host HOST] [--port PORT]
"""

import logging
import os

import click
import uvicorn


@click.group()
def cli() -> None:
    """amplifier-agent HTTP face."""


@cli.command()
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind address.")
@click.option("--port", default=9099, show_default=True, type=int, help="Bind port.")
@click.option(
    "--log-level",
    default="info",
    show_default=True,
    type=click.Choice(["debug", "info", "warning", "error", "critical"]),
)
def serve(host: str, port: int, log_level: str) -> None:
    """Start the HTTP server.

    POC scope: single-user local. Bind to 127.0.0.1 by default; only bind to
    0.0.0.0 explicitly when you understand the auth/exposure tradeoff (the POC
    only ships a shared-secret bearer check).
    """
    # Make sure uvicorn's logger output is visible.
    logging.basicConfig(level=getattr(logging, log_level.upper()))

    # Surface the api key once at startup so the user sees what to put in
    # opencode.json. Print to stderr; never put credentials on stdout.
    api_key = os.environ.get("AMPLIFIER_AGENT_HTTP_API_KEY", "local-dev-secret")
    click.echo(f"amplifier-agent HTTP face listening on http://{host}:{port}", err=True)
    click.echo(f"  API key: {api_key} (set AMPLIFIER_AGENT_HTTP_API_KEY to change)", err=True)
    click.echo("  Model:   amplifier (set AMPLIFIER_AGENT_HTTP_MODEL_ID to change)", err=True)

    uvicorn.run(
        "amplifier_agent_http.app:app",
        host=host,
        port=port,
        log_level=log_level,
        # POC: no reload, no workers > 1 (single-user local).
        reload=False,
        workers=1,
    )


def main() -> None:
    """Console-script entry point."""
    cli()


if __name__ == "__main__":
    main()

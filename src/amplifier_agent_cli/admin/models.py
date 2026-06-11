"""Admin commands: models subgroup with the 'list' command.

Provides model enumeration for the registered provider.

The 'list' subcommand loads the named provider module, instantiates it, and
calls list_models().  No fallback is applied — exceptions propagate and Click
converts them to exit code 2.  Provider-loading logic is ported from
amplifier_app_cli.provider_loader.
"""

from __future__ import annotations

import click


@click.group(name="models")
def models_group() -> None:
    """Enumerate models available from a provider."""


@models_group.command(name="list")
@click.option(
    "--provider",
    "provider_name",
    required=True,
    help="Provider identifier (e.g. anthropic, openai).",
)
@click.option(
    "--output",
    "output_mode",
    type=click.Choice(["auto", "json", "table"], case_sensitive=False),
    default="auto",
    show_default=True,
    help="Output format.",
)
@click.option(
    "--timeout",
    "timeout_seconds",
    type=float,
    default=15.0,
    show_default=True,
    help="Request timeout in seconds.",
)
def models_list(
    provider_name: str,
    output_mode: str,
    timeout_seconds: float,
) -> None:
    """List models available from a provider."""
    raise click.ClickException("not implemented")

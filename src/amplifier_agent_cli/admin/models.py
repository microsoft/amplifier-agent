"""Admin commands: models subgroup with the 'list' command.

Provides model enumeration for the registered provider.

The 'list' subcommand loads the named provider module, instantiates it, and
calls list_models().  No fallback is applied — exceptions propagate and Click
converts them to exit code 2.  Provider-loading logic is ported from
amplifier_app_cli.provider_loader.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import logging
from typing import Any

import click

logger = logging.getLogger(__name__)


def _get_provider_module_name(provider_id: str) -> str:
    """Convert provider ID to Python module name.

    Args:
        provider_id: Provider ID (e.g., "provider-anthropic" or "anthropic")

    Returns:
        Python module name (e.g., "amplifier_module_provider_anthropic")
    """
    # Normalize provider ID
    if provider_id.startswith("provider-"):
        provider_id = provider_id[9:]

    return f"amplifier_module_provider_{provider_id.replace('-', '_')}"


def _load_provider_module(provider_id: str) -> Any:
    """Load a provider module.

    Tries entry points first, then direct import.

    Args:
        provider_id: Provider ID (e.g., "provider-anthropic")

    Returns:
        Loaded Python module

    Raises:
        ImportError: If module cannot be loaded
    """
    # Normalize to full module ID
    module_id = provider_id if provider_id.startswith("provider-") else f"provider-{provider_id}"

    # Try entry point first
    try:
        eps = importlib.metadata.entry_points(group="amplifier.modules")
        for ep in eps:
            if ep.name == module_id:
                # Entry point loads the mount function, get its module
                mount_fn = ep.load()
                return importlib.import_module(mount_fn.__module__.rsplit(".", 1)[0])
    except Exception as e:
        logger.debug(f"Entry point lookup failed for {module_id}: {e}")

    # Try direct import
    module_name = _get_provider_module_name(provider_id)
    try:
        return importlib.import_module(module_name)
    except ImportError as e:
        raise ImportError(f"Could not load provider module '{provider_id}': {e}") from e


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

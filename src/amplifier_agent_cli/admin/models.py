"""Admin commands: models subgroup with the 'list' command.

Provides model enumeration for the registered provider.

The 'list' subcommand loads the named provider module, instantiates it, and
calls list_models().  No fallback is applied — exceptions propagate and Click
converts them to exit code 2.  Provider-loading logic is ported from
amplifier_app_cli.provider_loader.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.metadata
import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

import click

from amplifier_agent_cli.provider_sources import PROVIDER_CATALOG
from amplifier_agent_cli.tty_detect import is_stdout_tty

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1


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


def load_provider_class(provider_id: str) -> type | None:
    """Load a provider class for configuration purposes.

    This is a lightweight load that doesn't require a full coordinator.
    Returns the provider class (e.g., AnthropicProvider) that can be
    instantiated to query get_info() and list_models().

    Args:
        provider_id: Provider ID (e.g., "provider-anthropic" or "anthropic")

    Returns:
        Provider class if found, None otherwise
    """
    try:
        module = _load_provider_module(provider_id)

        # Look for provider class in module's __all__ or by convention
        # Convention: {Name}Provider (e.g., AnthropicProvider)
        provider_name = provider_id.replace("provider-", "") if provider_id.startswith("provider-") else provider_id
        class_name = f"{provider_name.title().replace('-', '')}Provider"

        # Try exact match first
        if hasattr(module, class_name):
            return getattr(module, class_name)

        # Try from __all__
        if hasattr(module, "__all__"):
            for name in module.__all__:
                if name.endswith("Provider"):
                    cls = getattr(module, name, None)
                    if cls and isinstance(cls, type):
                        return cls

        # Try any class ending in Provider
        for name in dir(module):
            if name.endswith("Provider") and not name.startswith("_"):
                cls = getattr(module, name, None)
                if cls and isinstance(cls, type):
                    return cls

        logger.warning(f"No provider class found in module for '{provider_id}'")
        return None

    except ImportError as e:
        logger.debug(f"Could not load provider class for '{provider_id}': {e}")
        return None


def _try_instantiate_provider(provider_class: type) -> object | None:
    """Try to instantiate a provider class using known constructor signatures.

    Attempts six common constructor signatures in order and returns the first
    successful instance.  The ``collected_config`` / ``_resolve_env_placeholder``
    machinery is intentionally omitted — placeholder connection values are used
    so that ``list_models()`` can be invoked without a live configuration.

    Args:
        provider_class: The provider class to instantiate.

    Returns:
        An instance of *provider_class* on success, ``None`` if all approaches fail.
    """
    api_key = ""
    base_url = "http://placeholder"
    host = "http://localhost:11434"
    instantiation_errors = (TypeError, ValueError, RuntimeError)

    # Approach 1: standard Anthropic/OpenAI (api_key, config)
    try:
        return provider_class(api_key=api_key, config={})
    except instantiation_errors:
        pass

    # Approach 2: Azure-style (base_url, api_key, config)
    try:
        return provider_class(base_url=base_url, api_key=api_key, config={})
    except instantiation_errors:
        pass

    # Approach 3: VLLM-style (base_url, config)
    try:
        return provider_class(base_url=base_url, config={})
    except instantiation_errors:
        pass

    # Approach 4: Ollama-style (host, config)
    try:
        return provider_class(host=host, config={})
    except instantiation_errors:
        pass

    # Approach 5: config-only
    try:
        return provider_class(config={})
    except instantiation_errors:
        pass

    # Approach 6: no-arg
    try:
        return provider_class()
    except instantiation_errors:
        pass

    return None


def list_provider_models(
    provider_id: str,
    timeout_seconds: float = 15.0,
) -> list[Any]:
    """Load a provider and return its available models.

    Analogous to provider_loader.get_provider_models but with an async timeout
    and no fallback — auth/API/connection errors propagate to the caller.
    Returns an empty list only when the provider cannot be loaded, instantiated,
    or lacks a ``list_models`` method.

    Args:
        provider_id: Provider ID (e.g., "anthropic").
        timeout_seconds: Timeout applied to the async list_models call.

    Returns:
        List of model objects returned by the provider.

    Raises:
        Any exception raised by list_models() itself (e.g., auth errors).
    """
    provider_class = load_provider_class(provider_id)
    if not provider_class:
        return []

    provider = _try_instantiate_provider(provider_class)
    if provider is None:
        logger.debug("Could not instantiate provider class for '%s'", provider_id)
        return []

    provider_obj: Any = provider  # cast so attribute access is type-safe below
    if not hasattr(provider_obj, "list_models"):
        logger.debug("Provider '%s' has no list_models method", provider_id)
        return []

    list_models_fn = provider_obj.list_models

    if asyncio.iscoroutinefunction(list_models_fn):

        async def _list_and_cleanup() -> list[Any]:
            try:
                return await asyncio.wait_for(list_models_fn(), timeout=timeout_seconds)
            finally:
                try:
                    await provider_obj.close()
                except Exception:
                    pass

        return asyncio.run(_list_and_cleanup())

    return list_models_fn()


def _render_json(provider_name: str, models: list[Any]) -> None:
    """Render the model list as a JSON envelope to stdout."""
    payload = {
        "schema_version": SCHEMA_VERSION,
        "provider": provider_name,
        "fetched_at": datetime.now(UTC).isoformat(),
        "models": [m.model_dump() if hasattr(m, "model_dump") else dict(m) for m in models],
    }
    click.echo(json.dumps(payload, indent=2))


def _render_table(models: list[Any]) -> None:
    """Render the model list as a 4-column aligned table to stdout."""
    headers = ("ID", "DISPLAY NAME", "CONTEXT", "CAPABILITIES")

    rows: list[tuple[str, str, str, str]] = []
    for m in models:
        data = m.model_dump() if hasattr(m, "model_dump") else dict(m)
        row = (
            str(data.get("id", "")),
            str(data.get("display_name", "")),
            str(data.get("context_window", "")),
            ", ".join(data.get("capabilities", None) or []),
        )
        rows.append(row)

    # Compute column widths as max of header and cell lengths
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def _fmt(cells: tuple[str, str, str, str]) -> str:
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells)).rstrip()

    click.echo(_fmt(headers))
    for row in rows:
        click.echo(_fmt(row))


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
    if provider_name not in PROVIDER_CATALOG:
        known = sorted(PROVIDER_CATALOG.keys())
        raise click.ClickException(f"Unknown provider {provider_name!r}. Known providers: {known}.")

    # Resolve 'auto' → 'table' on a TTY, 'json' when piped/redirected
    if output_mode == "auto":
        resolved_output = "table" if is_stdout_tty() else "json"
    else:
        resolved_output = output_mode

    try:
        models = list_provider_models(provider_name, timeout_seconds=timeout_seconds)
    except Exception as exc:
        click.echo(
            f"# {provider_name}: list_models() failed: {type(exc).__name__}: {exc}",
            err=True,
        )
        sys.exit(2)

    if not models:
        click.echo(
            f"# {provider_name}: no live model list available; enter a model/deployment name manually or use catalog defaults.",
            err=True,
        )

    if resolved_output == "json":
        _render_json(provider_name, models)
    else:
        _render_table(models)

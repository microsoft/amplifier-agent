"""Admin commands: models subgroup with the 'list' command.

Provides model enumeration for the registered provider.

The 'list' subcommand resolves provider credentials from environment
variables (per :mod:`amplifier_agent_cli.provider_sources.PROVIDER_CATALOG`),
loads the named provider module, instantiates it with the resolved
credentials, and calls ``list_models()``.

User-visible contract:

==============================================  ====  ==========================================
Scenario                                        Exit  Stderr
==============================================  ====  ==========================================
anthropic/openai with valid key, API succeeds      0  empty
anthropic/openai env var missing/empty             2  "<PROVIDER>_API_KEY not set"
anthropic/openai env var rejected by API           2  propagated SDK error
azure-openai with key (always empty by design)     0  advisory: "enter deployment manually"
ollama daemon down/unreachable                     0  advisory: "no live model list available"
provider module not pip-installed                  2  "provider module not installed"
unknown ``--provider`` value                       1  "Unknown provider 'X'."
==============================================  ====  ==========================================
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.metadata
import json
import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any

import click

from amplifier_agent_cli.provider_sources import (
    PROVIDER_CATALOG,
    PROVIDER_CREDENTIAL_VARS,
)
from amplifier_agent_cli.tty_detect import is_stdout_tty

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1


class ProviderCredentialsMissingError(Exception):
    """Raised when a provider's required credentials env var is unset/empty.

    Mapped to exit code 2 by the CLI layer.  The message includes the env var
    name so the user knows exactly what to set.
    """


class ProviderModuleNotInstalledError(Exception):
    """Raised when the provider's Python module is not pip-installed.

    Mapped to exit code 2 by the CLI layer.  Distinct from a legitimate empty
    model list (e.g. azure-openai by design, or an ollama daemon that's down):
    those exit 0 with an advisory.  This signals an installation gap.
    """


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


def _resolve_provider_credentials(provider_id: str) -> dict[str, str]:
    """Resolve per-provider connection credentials from environment variables.

    Mirrors :func:`amplifier_app_cli.provider_loader._resolve_env_placeholder`,
    but bound to the static :data:`PROVIDER_CATALOG` env-var mapping rather
    than a generic ``${VAR}`` placeholder.  The reference implementation in
    ``amplifier_app_cli.provider_loader`` reads collected user config; this
    port reads env vars directly so the CLI's documented "set env var, run"
    UX (see CHEATSHEET) works without a settings file.

    Args:
        provider_id: Short-name from :data:`PROVIDER_CATALOG`
            (e.g. ``"anthropic"``).  Unknown values return ``{}``.

    Returns:
        Dict with provider-specific connection fields.  For api-key-based
        providers: ``{"api_key": "<env-value>"}``.  For ollama:
        ``{"host": "<env-value-or-default>"}``.  Empty dict for unknown
        providers (caller handles validation separately).

    Raises:
        ProviderCredentialsMissingError: If the provider requires an API key
            (anthropic, openai, azure-openai) and neither the preferred env
            var nor any registered legacy env var is set.
    """
    # Ollama is special: it's a host URL, not an api key, and an unreachable
    # daemon is exit-0 + advisory rather than an error. Handle it explicitly.
    if provider_id == "ollama":
        # Either OLLAMA_HOST (catalog-preferred) or OLLAMA_BASE_URL.
        host = os.environ.get("OLLAMA_HOST") or os.environ.get("OLLAMA_BASE_URL") or "http://localhost:11434"
        return {"host": host}

    env_vars = PROVIDER_CREDENTIAL_VARS.get(provider_id)
    if not env_vars:
        # Unknown / future providers: return empty. The caller's PROVIDER_CATALOG
        # guard validates the name; if we get here on a known-but-unmapped
        # provider, the constructor-shape probing in _try_instantiate_provider
        # will still try the no-arg / config-only signatures.
        return {}

    primary_var = env_vars[0]
    value = os.environ.get(primary_var, "")
    if not value:
        for legacy_var in env_vars[1:]:
            value = os.environ.get(legacy_var, "")
            if value:
                break

    if not value:
        legacy_clause = ""
        if len(env_vars) > 1:
            legacy_names = ", ".join(env_vars[1:])
            legacy_clause = f" (legacy {legacy_names} also unset)"
        raise ProviderCredentialsMissingError(
            f"{primary_var} not set{legacy_clause}; cannot fetch live model list. "
            "Set the env var or choose a different provider.",
        )
    return {"api_key": value}


def _try_instantiate_provider(
    provider_class: type,
    credentials: dict[str, str] | None = None,
) -> object | None:
    """Try to instantiate a provider class using known constructor signatures.

    Attempts six common constructor signatures in order and returns the first
    successful instance.  Connection values are taken from *credentials*
    (typically produced by :func:`_resolve_provider_credentials` from env
    vars); when *credentials* is ``None`` or empty, falls back to placeholder
    defaults — used by tests and by the small set of providers whose
    ``list_models()`` does not require live config.

    Args:
        provider_class: The provider class to instantiate.
        credentials: Optional mapping of connection fields:
            ``{"api_key": "...", "host": "...", "base_url": "..."}``.
            Keys not provided fall back to placeholder defaults.

    Returns:
        An instance of *provider_class* on success, ``None`` if all approaches fail.
    """
    creds = credentials or {}
    api_key = creds.get("api_key", "")
    base_url = creds.get("base_url", "http://placeholder")
    host = creds.get("host", "http://localhost:11434")
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

    Resolves the provider's connection credentials from env vars first
    (raising :class:`ProviderCredentialsMissingError` if the required env var
    is not set), then loads the provider module (raising
    :class:`ProviderModuleNotInstalledError` on ImportError so the CLI can
    distinguish "module missing" from "module returned empty"), then calls
    ``list_models()``.  Auth/API/connection errors raised by ``list_models()``
    itself propagate to the caller.

    Returns an empty list only when the provider class is loadable but cannot
    be instantiated, or has no ``list_models`` method.  An empty list is *not*
    a generic catch-all for failure — see the contract table in the module
    docstring.

    Args:
        provider_id: Provider ID (e.g., "anthropic").
        timeout_seconds: Timeout applied to the async list_models call.

    Returns:
        List of model objects returned by the provider.

    Raises:
        ProviderCredentialsMissingError: Required env var unset for the provider.
        ProviderModuleNotInstalledError: Provider's Python module not installed.
        Any exception raised by ``list_models()`` itself (e.g., auth errors).
    """
    # Resolve credentials BEFORE attempting any module load so the user gets
    # the cheaper, more actionable error first when the env var is missing.
    credentials = _resolve_provider_credentials(provider_id)

    # Load the module directly so we can distinguish ImportError (module not
    # installed) from "module loaded but no Provider class found".  The
    # public ``load_provider_class`` keeps its silent-None behaviour for
    # other callers; this function takes the strict path.
    try:
        _load_provider_module(provider_id)
    except ImportError as exc:
        raise ProviderModuleNotInstalledError(
            f"provider module not installed for '{provider_id}'. "
            f"Run 'amplifier-agent run --provider {provider_id} <prompt>' once "
            f"to install it, then retry.",
        ) from exc

    provider_class = load_provider_class(provider_id)
    if not provider_class:
        # Module loaded but no Provider class found — rare; treat as empty.
        return []

    provider = _try_instantiate_provider(provider_class, credentials=credentials)
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
    except ProviderCredentialsMissingError as exc:
        click.echo(f"# {provider_name}: {exc}", err=True)
        sys.exit(2)
    except ProviderModuleNotInstalledError as exc:
        click.echo(f"# {provider_name}: {exc}", err=True)
        sys.exit(2)
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

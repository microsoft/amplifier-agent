"""Provider name → module URI + config template mapping.

Used by ``modes/single_turn.py`` (and Mode B's inline ``_StdioEngine``) to
inject a provider entry into the prepared bundle's ``mount_plan["providers"]``
slot after ``load_and_prepare_cached()`` returns. The injection happens
per-invocation, so env-var-derived credentials are never baked into the
pickle cache on disk.

This mirrors:

* ``amplifier_app_cli.provider_sources.DEFAULT_PROVIDER_SOURCES`` (the
  name → git URI map),
* ``amplifier_app_openclaw.runner._inject_user_providers`` (the
  "don't clobber existing" mount injection),

but the source of truth here is ``provider_detect.detect_provider()`` — the
short provider name returned by the env-var precedence walk — not a
hand-rolled ``settings.yaml`` config. That keeps the CLI's documented
"set env var, run agent" UX (see CHEATSHEET §2) intact: zero settings
files, the user sets ``ANTHROPIC_API_KEY`` (or one of the supported peers)
and the matching provider module is mounted.

Per the broader baked-in-bundle architectural revisit
(``docs/designs/2026-05-19-baked-in-bundle-revisit.md``, D6), the
relationship between this catalog and app-cli's is itself a question for
that design pass; this module is the minimum-viable step that gets a
working CLI today without committing to either eventual answer.
"""

from __future__ import annotations

import os
from typing import Any, Final, TypedDict


class _CatalogEntry(TypedDict):
    """Static catalog row for one provider."""

    module: str
    source: str
    env_var: str
    default_model: str


#: Map provider short-name (matches ``provider_detect.KNOWN_PROVIDERS``) →
#: the catalog row used to construct a ``mount_plan["providers"]`` entry.
#:
#: Default models mirror app-cli's published settings template where known
#: (anthropic → ``claude-opus-4-5`` per the explorer's investigation on
#: 2026-05-19), and use conservative current-generation defaults otherwise.
#: Models can be overridden later via a CLI flag once one exists.
PROVIDER_CATALOG: Final[dict[str, _CatalogEntry]] = {
    "anthropic": {
        "module": "provider-anthropic",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",
        "env_var": "ANTHROPIC_API_KEY",
        "default_model": "claude-opus-4-5",
    },
    "openai": {
        "module": "provider-openai",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-openai@main",
        "env_var": "OPENAI_API_KEY",
        "default_model": "gpt-4o",
    },
    "azure-openai": {
        "module": "provider-azure-openai",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-azure-openai@main",
        "env_var": "AZURE_OPENAI_KEY",
        "default_model": "gpt-4o",
    },
    "ollama": {
        "module": "provider-ollama",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-ollama@main",
        "env_var": "OLLAMA_HOST",
        "default_model": "llama3.2",
    },
}


def build_provider_entry(provider_name: str) -> dict[str, Any]:
    """Build a ``mount_plan["providers"]`` entry for one provider.

    Resolves the env var declared in the catalog to its current value. The
    resolution is intentionally per-invocation rather than at module import
    time so that:

    * the prepared-bundle pickle on disk never contains secrets,
    * users who export the env var after first install (or rotate keys)
      pick up the new value without having to ``cache clear``.

    The mount entry follows the shape app-cli's ``runtime/config.py`` writes
    into ``prepared.mount_plan["providers"]``: ``module``, ``source``, plus a
    ``config`` dict containing ``api_key``, ``default_model``, and a
    ``priority`` integer (``1`` here — there's only ever one provider
    mounted in this CLI, but the kernel reads the field).

    Args:
        provider_name: One of ``PROVIDER_CATALOG`` keys (e.g. ``"anthropic"``).

    Returns:
        The mount-plan entry dict, ready to be appended to
        ``prepared.mount_plan["providers"]``.

    Raises:
        ValueError: If *provider_name* is not in ``PROVIDER_CATALOG``.
    """
    entry = PROVIDER_CATALOG.get(provider_name)
    if entry is None:
        known = sorted(PROVIDER_CATALOG.keys())
        raise ValueError(
            f"Unknown provider {provider_name!r}. Known providers: {known}.",
        )

    return {
        "module": entry["module"],
        "source": entry["source"],
        "config": {
            "api_key": os.environ.get(entry["env_var"], ""),
            "default_model": entry["default_model"],
            "priority": 1,
        },
    }


def inject_provider(prepared: Any, provider_name: str) -> None:
    """Inject one provider entry into ``prepared.mount_plan["providers"]``.

    No-op if ``mount_plan`` already declares a non-empty ``providers`` list
    — mirrors openclaw's ``_inject_user_providers`` "don't clobber existing"
    rule. This keeps the door open for a future bundle.md that declares its
    own providers; the CLI-layer injection is a default, not an override.

    Args:
        prepared: The prepared bundle returned from
            ``load_and_prepare_cached()``. Must expose a mutable
            ``mount_plan`` dict attribute.
        provider_name: One of ``PROVIDER_CATALOG`` keys.

    Raises:
        ValueError: If *provider_name* is not in ``PROVIDER_CATALOG``.
    """
    if prepared.mount_plan.get("providers"):
        return
    prepared.mount_plan["providers"] = [build_provider_entry(provider_name)]

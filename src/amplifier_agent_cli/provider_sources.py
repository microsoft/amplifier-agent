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

but the source of truth here is the resolved provider short-name from
config / bundle.md ``default_provider`` (D6, E5) — not a hand-rolled
``settings.yaml`` config. That keeps the CLI's documented
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
import sys
from typing import Any, Final, TypedDict


class _CatalogEntry(TypedDict):
    """Static catalog row for one provider."""

    module: str
    source: str
    env_var: str
    legacy_env_vars: tuple[str, ...]
    default_model: str


_LEGACY_ENV_VAR_NOTICE_EMITTED: set[str] = set()


def _emit_legacy_env_var_notice(legacy_var: str, preferred_var: str) -> None:
    """Emit a one-time stderr warning when a legacy env var supplies credentials."""
    if legacy_var in _LEGACY_ENV_VAR_NOTICE_EMITTED:
        return
    _LEGACY_ENV_VAR_NOTICE_EMITTED.add(legacy_var)
    print(
        f"[WARN] {legacy_var} is deprecated; please set {preferred_var} instead. "
        f"Support for {legacy_var} will be removed in a future release.",
        file=sys.stderr,
    )


#: Canonical list of provider short-names this CLI knows how to mount.
#: Used by callers that need to validate a resolved provider name (e.g.
#: --provider override) against the supported set. Kept in sync with
#: ``PROVIDER_CATALOG.keys()``.
KNOWN_PROVIDERS: Final[tuple[str, ...]] = ("anthropic", "openai", "azure-openai", "ollama")


#: Map provider short-name (matches ``KNOWN_PROVIDERS``) →
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
        "legacy_env_vars": (),
        "default_model": "claude-opus-4-5",
    },
    "openai": {
        "module": "provider-openai",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-openai@main",
        "env_var": "OPENAI_API_KEY",
        "legacy_env_vars": (),
        # gpt-5.5 chosen so the bundle's default `extended_thinking: true` lands
        # on a model that actually accepts the resulting `reasoning.effort`
        # parameter. With gpt-4o (non-reasoning), the OpenAI API 400s on every
        # turn out of the box. Consumers can override via bundle config.
        "default_model": "gpt-5.5",
    },
    "azure-openai": {
        "module": "provider-azure-openai",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-azure-openai@main",
        # Preferred env var — matches the README, the upstream
        # ``amplifier-module-provider-azure-openai`` module, and the Azure
        # OpenAI Python SDK convention.
        "env_var": "AZURE_OPENAI_API_KEY",
        # Accepted for backwards compatibility with the CLI's earlier
        # ``AZURE_OPENAI_KEY`` spelling. Triggers a one-time stderr
        # deprecation notice when consulted. Removable in a future release.
        "legacy_env_vars": ("AZURE_OPENAI_KEY",),
        "default_model": "gpt-4o",
    },
    "ollama": {
        "module": "provider-ollama",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-ollama@main",
        "env_var": "OLLAMA_HOST",
        "legacy_env_vars": (),
        "default_model": "llama3.2",
    },
}


def build_provider_entry(
    provider_name: str, model_override: str | None = None, effort_override: str | None = None
) -> dict[str, Any]:
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
        model_override: When provided, replaces the catalog's ``default_model`` in
            the returned config. Allows callers to pin a specific model without
            modifying the catalog.
        effort_override: When provided, injects an ``effort`` key into the returned
            config dict. Omitted entirely when ``None`` so the provider module sees
            no ``effort`` field for its default behaviour.

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

    preferred_var = entry["env_var"]
    api_key = os.environ.get(preferred_var, "")
    if not api_key:
        # Fall back to legacy env vars (e.g. AZURE_OPENAI_KEY) for backwards
        # compat. Emits a one-time stderr warning when a legacy var supplies
        # the credential so users have a chance to migrate.
        for legacy_var in entry["legacy_env_vars"]:
            legacy_value = os.environ.get(legacy_var, "")
            if legacy_value:
                _emit_legacy_env_var_notice(legacy_var, preferred_var)
                api_key = legacy_value
                break

    config: dict[str, Any] = {
        "api_key": api_key,
        "default_model": model_override or entry["default_model"],
        "priority": 1,
    }
    if effort_override is not None:
        config["effort"] = effort_override
    return {"module": entry["module"], "source": entry["source"], "config": config}


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

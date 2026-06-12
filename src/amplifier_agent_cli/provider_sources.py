"""Provider name → module URI mapping (bootstrap-only catalog).

Used by ``modes/single_turn.py`` (and Mode B's inline ``_StdioEngine``) to
inject a provider entry into the prepared bundle's ``mount_plan["providers"]``
slot after ``load_and_prepare_cached()`` returns. The injection happens
per-invocation, so env-var-derived credentials are never baked into the
pickle cache on disk.

Architectural alignment (Q1 follow-up, 2026-06-11)
==================================================

This module mirrors ``amplifier_app_cli.provider_loader``'s
``DEFAULT_PROVIDER_SOURCES`` pattern: the catalog is **bootstrap-only** —
it tells the kernel *where to install a provider from* and nothing else.
Everything else (default model, credential env vars, credential field
shape, display name) flows from ``provider.get_info()`` at runtime, so
the catalog can never drift from provider truth.

Two small static structures live here:

* :data:`PROVIDER_CATALOG` — ``{provider_name: {"module", "source"}}``.
  The 5-field shape was shrunk on 2026-06-11 after the ollama
  ``default_model`` was found drifted (catalog said ``"llama3.2"``,
  provider's own ``get_info().defaults["model"]`` says ``"llama3.2:3b"``).
  Removing ``default_model`` from the catalog eliminates that drift class.

* :data:`PROVIDER_CREDENTIAL_VARS` — small auxiliary
  ``{provider_name: (primary_env, *legacy_envs)}`` mapping. Mirrors
  ``amplifier_app_cli.provider_loader.PROVIDER_CREDENTIAL_VARS``: a scoped
  fallback used only for env-var name resolution. Kept separate from the
  install catalog so the install catalog stays bootstrap-only.

Per the broader baked-in-bundle architectural revisit
(``docs/designs/2026-05-19-baked-in-bundle-revisit.md``, D6), the
relationship between this catalog and app-cli's remains a question for
that design pass; the shrink reduces the surface area that has to be
reconciled when that work lands.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Final, TypedDict


class _CatalogEntry(TypedDict):
    """Bootstrap-only catalog row.

    Holds the two fields the kernel needs *before* the provider module
    exists locally — namely, what to install and where to fetch it from.
    Everything else flows from ``provider.get_info()`` once the module is
    loaded.
    """

    module: str
    source: str


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
#: ``models list --provider <name>``, or aggregate iteration in admin
#: commands) against the supported set. Kept in sync with
#: ``PROVIDER_CATALOG.keys()``.
KNOWN_PROVIDERS: Final[tuple[str, ...]] = ("anthropic", "openai", "azure-openai", "ollama")


#: Map provider short-name → bootstrap catalog row.
#:
#: Mirrors ``amplifier_app_cli.provider_loader.DEFAULT_PROVIDER_SOURCES``.
#: Only ``module`` and ``source`` live here; default models and env var
#: names are intentionally absent so the catalog can never drift from
#: provider truth. See :data:`PROVIDER_CREDENTIAL_VARS` for env vars and
#: ``provider.get_info().defaults["model"]`` for default models.
PROVIDER_CATALOG: Final[dict[str, _CatalogEntry]] = {
    "anthropic": {
        "module": "provider-anthropic",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",
    },
    "openai": {
        "module": "provider-openai",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-openai@main",
    },
    "azure-openai": {
        "module": "provider-azure-openai",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-azure-openai@main",
    },
    "ollama": {
        "module": "provider-ollama",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-ollama@main",
    },
}


#: Map provider short-name → ``(primary_env, *legacy_envs)``.
#:
#: Small auxiliary mapping used by :func:`build_provider_entry` and
#: :func:`amplifier_agent_cli.admin.models._resolve_provider_credentials`
#: to look up the env var name(s) that carry a provider's credentials.
#: The first entry is the preferred name (matches the provider module's
#: documented variable); any remaining entries are deprecated aliases,
#: kept for backwards compatibility, that trigger a one-time stderr
#: deprecation notice when consulted.
#:
#: Intentionally NOT folded into :data:`PROVIDER_CATALOG`: the install
#: catalog is bootstrap-only and stays free of runtime concerns like
#: credentials. Mirrors amplifier-app-cli's separation of
#: ``DEFAULT_PROVIDER_SOURCES`` (install) from
#: ``PROVIDER_CREDENTIAL_VARS`` (env var lookup).
PROVIDER_CREDENTIAL_VARS: Final[dict[str, tuple[str, ...]]] = {
    "anthropic": ("ANTHROPIC_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    # Preferred AZURE_OPENAI_API_KEY matches the README, the upstream
    # amplifier-module-provider-azure-openai module, and the Azure OpenAI
    # Python SDK convention. AZURE_OPENAI_KEY is the legacy alias still
    # accepted for backwards compatibility.
    "azure-openai": ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_KEY"),
    "ollama": ("OLLAMA_HOST",),
}


def _resolve_env_credential(provider_name: str) -> str:
    """Resolve the credential env var for *provider_name* to its current value.

    Reads :data:`PROVIDER_CREDENTIAL_VARS` for the primary env var, falling
    back through any registered legacy aliases. Falling back to a legacy
    alias emits a one-time deprecation notice on stderr. Returns ``""``
    when no var is set — the caller (kernel mount, or the admin
    ``models list`` command) decides whether an empty credential is an
    error or a no-op.
    """
    env_vars = PROVIDER_CREDENTIAL_VARS.get(provider_name, ())
    if not env_vars:
        return ""
    primary_var = env_vars[0]
    value = os.environ.get(primary_var, "")
    if value:
        return value
    for legacy_var in env_vars[1:]:
        legacy_value = os.environ.get(legacy_var, "")
        if legacy_value:
            _emit_legacy_env_var_notice(legacy_var, primary_var)
            return legacy_value
    return ""


def _reassert_protected_keys(config: dict[str, Any], *, api_key: str, priority: int) -> None:
    """Re-assert engine-owned keys after an ``extra_config`` overlay.

    ``api_key`` (env-resolved per-invocation) and ``priority`` (mount slot
    machinery) are not user-tunable via ``host_config.json``. Re-asserting
    them after ``config.update(extra_config)`` ensures a stale config file
    cannot silently downgrade a fresh credential or override the mount
    priority. New engine-owned keys belong here.
    """
    config["api_key"] = api_key
    config["priority"] = priority


def build_provider_entry(
    provider_name: str,
    model_override: str | None = None,
    effort_override: str | None = None,
    extra_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a ``mount_plan["providers"]`` entry for one provider.

    Resolves the credential env var (per :data:`PROVIDER_CREDENTIAL_VARS`)
    to its current value. The resolution is intentionally per-invocation
    rather than at module import time so that:

    * the prepared-bundle pickle on disk never contains secrets,
    * users who export the env var after first install (or rotate keys)
      pick up the new value without having to ``cache clear``.

    The mount entry follows the shape app-cli's ``runtime/config.py``
    writes into ``prepared.mount_plan["providers"]``: ``module``,
    ``source``, and a ``config`` dict.  ``config`` always contains
    ``api_key`` (resolved from the env var, possibly empty) and
    ``priority`` (``1`` — there's only ever one provider mounted in
    this CLI). ``default_model`` and ``effort`` appear only when the
    caller passes an override; otherwise they're omitted entirely so
    the provider's own ``get_info().defaults`` wins.

    Args:
        provider_name: One of ``PROVIDER_CATALOG`` keys (e.g. ``"anthropic"``).
        model_override: When provided, injected as ``config["default_model"]``.
            When ``None`` (the common case), ``default_model`` is omitted from
            the returned config — the provider self-describes its default via
            ``get_info().defaults["model"]``. Mirrors amplifier-app-cli's
            "no hard-coded provider defaults" rule.
        effort_override: When provided, injects ``config["effort"]``.
            Omitted entirely when ``None`` so the provider sees no
            ``effort`` field and falls back to its own default behaviour.
        extra_config: Optional dict of pass-through provider configuration
            sourced from ``host_config["provider"]["config"]``. Overlaid on
            top of the base ``{api_key, priority}`` config AFTER any
            ``model_override`` / ``effort_override`` are applied, so the
            host config has the final word on knobs like ``temperature``,
            ``max_tokens``, ``thinking_budget_tokens``, and any future
            provider-specific keys. Engine-asserted keys (``api_key``,
            ``priority``) are re-asserted after the overlay so a stale
            config file cannot downgrade a fresh credential or override
            the mount priority.

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

    api_key = _resolve_env_credential(provider_name)
    priority = 1
    config: dict[str, Any] = {"api_key": api_key, "priority": priority}
    if model_override is not None:
        config["default_model"] = model_override
    if effort_override is not None:
        config["effort"] = effort_override
    if extra_config:
        config.update(extra_config)
        _reassert_protected_keys(config, api_key=api_key, priority=priority)
    return {"module": entry["module"], "source": entry["source"], "config": config}


def inject_provider(
    prepared: Any,
    provider_name: str,
    model_override: str | None = None,
    effort_override: str | None = None,
    extra_config: dict[str, Any] | None = None,
) -> None:
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
        model_override: Forwarded to :func:`build_provider_entry`.
        effort_override: Forwarded to :func:`build_provider_entry`.
        extra_config: Forwarded to :func:`build_provider_entry`. Carries the
            full ``host_config["provider"]["config"]`` dict so the host can
            parameterize the mounted provider end-to-end through a single
            source of truth.

    Raises:
        ValueError: If *provider_name* is not in ``PROVIDER_CATALOG``.
    """
    if prepared.mount_plan.get("providers"):
        return
    prepared.mount_plan["providers"] = [
        build_provider_entry(
            provider_name,
            model_override=model_override,
            effort_override=effort_override,
            extra_config=extra_config,
        )
    ]

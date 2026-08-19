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

Credential-resolution convergence (Phase 1)
============================================

Prior to this pass, THREE call sites independently re-implemented the
env→file precedence chain: this module's (now-deleted) ``_resolve_env_credential``
(env-only... plus file), ``admin.models._resolve_provider_credentials``
(env-ONLY, no file fallback — the divergence that caused ``models list``,
``run``, and ``serve`` startup to disagree about which providers were
configured), and inline env-lookups in ``admin.auth`` / ``admin.config_show``.

:func:`resolve_credential_detailed` is now the single canonical resolver.
Every other call site (``build_provider_entry``, ``admin.models.list_provider_models``,
``admin.auth`` auth_list/auth_status, the HTTP serve lifespan's auto-enable
path) calls it directly or through :func:`resolve_provider_credentials` /
:func:`enumerate_resolvable_providers`.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Any, Final, TypedDict


class ProviderCredentialsMissingError(RuntimeError):
    """Raised when a required, key-based provider has no resolvable credential.

    Canonical home (moved here from ``amplifier_agent_cli.admin.models`` as
    part of Phase 1 credential-resolution convergence — this module is
    where credential resolution now lives). Re-exported from
    ``admin.models`` for backwards compatibility with existing imports
    (``amplifier_agent_http.app`` imports it from there).
    """


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
KNOWN_PROVIDERS: Final[tuple[str, ...]] = (
    "anthropic",
    "openai",
    "azure-openai",
    "ollama",
    "github-copilot",
    "openai-chatgpt",
    "chat-completions",
    "gemini",
    "vllm",
)


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
    "github-copilot": {
        "module": "provider-github-copilot",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-github-copilot@main",
    },
    "openai-chatgpt": {
        "module": "provider-openai-chatgpt",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-openai-chatgpt@main",
    },
    "chat-completions": {
        "module": "provider-chat-completions",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-chat-completions@main",
    },
    "gemini": {
        "module": "provider-gemini",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-gemini@main",
    },
    "vllm": {
        "module": "provider-vllm",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-vllm@main",
    },
}


#: Placeholder API key for a keyless vLLM server.
#:
#: The provider module's own ``mount()`` defaults ``VLLM_API_KEY`` to this exact
#: string, because a self-hosted vLLM commonly needs no auth but the OpenAI SDK
#: it wraps still insists on *some* value. Mirroring that default here is what
#: keeps ``models list --provider vllm`` working: unlike ``run``, that path
#: builds the provider straight from the resolved credential fields, so an
#: absent key would otherwise reach the constructor as ``""``. See the vllm
#: branch of :func:`resolve_provider_credentials` for the full story.
#:
#: It is a placeholder, not a secret. :func:`build_provider_entry` must not let
#: it overwrite a real key supplied through host config's ``provider.config``.
VLLM_KEYLESS_API_KEY: Final[str] = "EMPTY"


#: Map provider short-name → ``(primary_env, *legacy_envs)``.
#:
#: Small auxiliary mapping used by :func:`resolve_credential_detailed` to
#: look up the env var name(s) that carry a provider's credentials. The
#: first entry is the preferred name (matches the provider module's
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
    # Only GITHUB_TOKEN, deliberately. The provider resolves its own four-var chain
    # (COPILOT_AGENT_TOKEN -> COPILOT_GITHUB_TOKEN -> GH_TOKEN -> GITHUB_TOKEN, first
    # non-empty wins; sdk_adapter/client.py:44-49). Entries past index 0 here are treated
    # as DEPRECATED aliases and emit a one-time stderr notice, which those are not -- so
    # listing them would produce a spurious deprecation warning. amplifier-agent only
    # needs one var to answer "is this provider configured".
    "github-copilot": ("GITHUB_TOKEN",),
    # Google GenAI SDK accepts BOTH GOOGLE_API_KEY and GEMINI_API_KEY as
    # first-class (GOOGLE_API_KEY takes precedence). Only the primary is listed
    # here: entries past index 0 are treated as deprecated aliases and emit a
    # spurious stderr deprecation notice, which GEMINI_API_KEY is not. A
    # GEMINI_API_KEY-only user is still served by the module's own env read at
    # mount; amplifier-agent only needs one var to answer "is this configured".
    "gemini": ("GOOGLE_API_KEY",),
}

#: Ollama's own env var chain includes a second, non-legacy alias
#: (``OLLAMA_BASE_URL``) that most Ollama-adjacent tooling recognizes.
#: Kept separate from :data:`PROVIDER_CREDENTIAL_VARS` (rather than
#: appended as a "legacy" entry) because it is NOT deprecated — it does
#: not trigger :func:`_emit_legacy_env_var_notice`.
_OLLAMA_BASE_URL_ENV: Final[str] = "OLLAMA_BASE_URL"
_OLLAMA_DEFAULT_HOST: Final[str] = "http://localhost:11434"


#: Separator between a reseller's provider name and the model id it resells.
#:
#: ``/`` matches the OpenRouter convention and is safe through opencode's parsers:
#: ``parseModel`` and friends split on the FIRST separator and rejoin the rest, and
#: ``parseModelSelection`` exact-matches the full remainder against its model map
#: before falling back to any right-hand split. So ``amplifier/github-copilot/x``
#: resolves to provider ``amplifier``, model ``github-copilot/x``.
MODEL_ID_SEPARATOR: Final[str] = "/"


#: Map reseller provider short-name → suffix appended to its models' display names.
#:
#: A *reseller* serves models it did not originate, which other providers also serve:
#: GitHub Copilot serves ``claude-sonnet-5``, and so does anthropic, under a
#: byte-identical id. That collision has to be resolved in two places, and membership
#: of this map drives BOTH so they cannot disagree:
#:
#: * **Ids** are namespaced ``<provider>/<id>`` (:func:`namespace_model_id`) so the
#:   two models stay separately addressable. Without this, whichever provider is
#:   enumerated last silently wins the registry key and captures the other's traffic.
#: * **Display names** get the suffix stored here, because a namespaced id is not
#:   what a picker shows. amplifier-app-opencode maps ``display_name`` onto opencode's
#:   per-model ``name``, which its model dialog renders verbatim.
#:
#: Native providers are absent from this map and stay bare on both counts -- their
#: ids are already unambiguous, and namespacing them would break every existing
#: client and config keyed on the bare id.
RESELLER_PROVIDERS: Final[dict[str, str]] = {
    "github-copilot": " (GitHub)",
}


def namespace_model_id(provider: str | None, model_id: str) -> str:
    """Qualify *model_id* with *provider* when that provider is a reseller.

    Native providers return *model_id* unchanged. Idempotent: an id already carrying
    its provider's prefix is returned as-is.
    """
    if not provider or provider not in RESELLER_PROVIDERS:
        return model_id
    prefix = f"{provider}{MODEL_ID_SEPARATOR}"
    if model_id.startswith(prefix):
        return model_id
    return f"{prefix}{model_id}"


def split_model_id(model_id: str) -> tuple[str | None, str]:
    """Split a possibly-namespaced id into ``(reseller_provider, bare_id)``.

    Only a recognised reseller prefix is stripped, so an id that merely *contains* a
    separator (some upstreams ship ids like ``vendor/model``) survives intact.
    Returns ``(None, model_id)`` when no reseller prefix applies.
    """
    for provider in RESELLER_PROVIDERS:
        prefix = f"{provider}{MODEL_ID_SEPARATOR}"
        if model_id.startswith(prefix):
            return provider, model_id[len(prefix) :]
    return None, model_id


def decorate_display_name(provider: str | None, display_name: str) -> str:
    """Append *provider*'s display suffix to *display_name*, if it is a reseller.

    Shared by the HTTP ``/v1/models`` route and the CLI ``models list`` table so the
    two surfaces cannot drift. Idempotent: a name that already carries the suffix is
    returned unchanged, so double-decoration is harmless.
    """
    suffix = RESELLER_PROVIDERS.get(provider or "")
    if not suffix or display_name.endswith(suffix):
        return display_name
    return f"{display_name}{suffix}"


@dataclass(frozen=True)
class CredentialResolution:
    """Full detail of one provider's credential resolution outcome.

    Never raises — callers that need "missing credential" to be an error
    ask for it explicitly via :func:`resolve_provider_credentials`'s
    ``required=True``. This dataclass is the shared vocabulary consumed by
    ``build_provider_entry``, ``admin.models.list_provider_models``,
    ``admin.auth`` (auth_list/auth_status), and the ``providers list``
    admin command — so a single resolution pass is described identically
    everywhere.

    Attributes:
        provider: The provider short-name resolved (e.g. ``"anthropic"``).
        resolved: Whether a usable credential/config was found. For
            ollama, ``False`` when only the built-in default host applies
            (no explicit configuration) — the provider is *usable* but not
            considered "configured" for auto-enable purposes.
        source: One of ``"env"``, ``"file"``, ``"default"``, ``"none"``.
        env_var: The specific env var name involved — the var that
            actually supplied the value when ``source == "env"``,
            otherwise the primary/preferred var name (useful for
            "export <VAR>" hints). ``None`` for unknown providers.
        fields: Unmasked credential/config fields ready to merge into a
            provider mount config (e.g. ``{"api_key": ...}`` or
            ``{"host": ...}``, plus ``{"endpoint": ...}`` for azure-openai
            when resolvable). Never logged or displayed directly by
            callers that must not leak key material.
    """

    provider: str
    resolved: bool
    source: str
    env_var: str | None
    fields: dict[str, str] = field(default_factory=dict)


def _maybe_attach_azure_endpoint(provider_name: str, fields: dict[str, str]) -> None:
    """Attach an ``endpoint`` field for azure-openai when one is resolvable.

    Checks ``AZURE_OPENAI_ENDPOINT`` env first, then the persisted
    credentials file's ``endpoint`` field. No-op for every other provider,
    and no-op when neither source has a value (omitted rather than set to
    ``""`` — matches the spec's "omit if empty" instruction).
    """
    if provider_name != "azure-openai":
        return
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
    if not endpoint:
        # Local import: avoid a module-load-time cycle with admin.auth,
        # which imports KNOWN_PROVIDERS / PROVIDER_CREDENTIAL_VARS from
        # this module.
        from amplifier_agent_cli.admin.auth import resolve_field_from_file

        endpoint = resolve_field_from_file(provider_name, "endpoint")
    if endpoint:
        fields["endpoint"] = endpoint


def resolve_credential_detailed(provider_name: str) -> CredentialResolution:
    """Resolve full credential detail for *provider_name*. Never raises.

    This is the ONE canonical resolution chain for the whole CLI + HTTP
    face. Resolution order (gh/aws/claude convention, "env-first"):

      1. Primary shell env var (``PROVIDER_CREDENTIAL_VARS[name][0]``,
         or ``OLLAMA_HOST`` for ollama).
      2. Legacy/alias env var(s) — ``OLLAMA_BASE_URL`` for ollama, or any
         ``PROVIDER_CREDENTIAL_VARS[name][1:]`` alias for key providers
         (emits a one-time deprecation notice for true legacy aliases).
      3. Persisted credentials file (``~/.amplifier-agent/credentials.json``)
         — managed by ``amplifier-agent auth set/list/remove``.
      4. Nothing resolvable: ``source="none"`` for key providers (the
         caller decides whether that's fatal), ``source="default"`` for
         ollama (its built-in localhost default still makes it usable,
         just not considered "explicitly configured").

    The env-first order is deliberate: shells, CI runners, and ad-hoc
    overrides should ALWAYS win over the persisted file so users can
    point at a different key for one invocation without disturbing their
    stored configuration.

    Args:
        provider_name: A provider short-name. Unknown names (not in
            :data:`KNOWN_PROVIDERS` / :data:`PROVIDER_CREDENTIAL_VARS`)
            resolve to ``source="none"``, ``resolved=False``, ``fields={}``.

    Returns:
        A :class:`CredentialResolution` describing the outcome.
    """
    if provider_name == "ollama":
        host = os.environ.get("OLLAMA_HOST", "")
        if host:
            return CredentialResolution(
                provider=provider_name, resolved=True, source="env", env_var="OLLAMA_HOST", fields={"host": host}
            )
        base_url = os.environ.get(_OLLAMA_BASE_URL_ENV, "")
        if base_url:
            return CredentialResolution(
                provider=provider_name,
                resolved=True,
                source="env",
                env_var=_OLLAMA_BASE_URL_ENV,
                fields={"host": base_url},
            )

        # Local import: avoid a module-load-time cycle with admin.auth.
        from amplifier_agent_cli.admin.auth import resolve_field_from_file

        file_host = resolve_field_from_file(provider_name, "host")
        if file_host:
            return CredentialResolution(
                provider=provider_name, resolved=True, source="file", env_var="OLLAMA_HOST", fields={"host": file_host}
            )
        return CredentialResolution(
            provider=provider_name,
            resolved=False,
            source="default",
            env_var="OLLAMA_HOST",
            fields={"host": _OLLAMA_DEFAULT_HOST},
        )

    if provider_name == "openai-chatgpt":
        # ChatGPT subscription provider (provider-openai-chatgpt): authenticates
        # via OAuth device-code, NOT an api key. There is no credential env var;
        # the module caches its OAuth tokens to a file and refreshes them itself.
        # We report "resolvable" iff that token cache exists and parses with a
        # token present -- an honest signal of whether a prior device-code login
        # happened -- without ever reading or emitting the token material. The
        # module's own ``login_on_mount`` drives the interactive device-code flow
        # at mount time when no cache exists. ``auth set`` is refused for this
        # provider (see _CONFIG_CREDENTIAL_UNSUPPORTED in admin.auth) since there
        # is no static key to store.
        import json
        from pathlib import Path

        token_file = Path("~/.amplifier/openai-chatgpt-oauth.json").expanduser()
        try:
            data = json.loads(token_file.read_text())
            has_token = isinstance(data, dict) and bool(data.get("access_token") or data.get("refresh_token"))
        except (OSError, ValueError):
            has_token = False
        if has_token:
            return CredentialResolution(
                provider=provider_name,
                resolved=True,
                source="file",
                env_var=None,
                fields={},
            )
        return CredentialResolution(
            provider=provider_name,
            resolved=False,
            source="none",
            env_var=None,
            fields={},
        )

    if provider_name == "chat-completions":
        # Endpoint-agnostic OpenAI Chat Completions provider. Its required
        # "credential" is ``base_url`` (the server to talk to), NOT an api key --
        # local servers (llama.cpp, vLLM, LM Studio, LocalAI) commonly need no
        # key at all. This needs a dedicated branch rather than a
        # PROVIDER_CREDENTIAL_VARS entry because the generic branch below routes
        # the primary env var's value into ``fields["api_key"]``; here the value
        # must land in ``fields["base_url"]`` instead. Without base_url the
        # provider cannot serve a request, so absent base_url is honestly
        # ``resolved=False, source="none"`` (there is no usable localhost default
        # to fall back to, unlike ollama).
        base_url = os.environ.get("CHAT_COMPLETIONS_BASE_URL", "")
        if not base_url:
            return CredentialResolution(
                provider=provider_name,
                resolved=False,
                source="none",
                env_var="CHAT_COMPLETIONS_BASE_URL",
                fields={},
            )
        cc_fields: dict[str, str] = {"base_url": base_url}
        # api_key is optional: only inject when explicitly set, so a host-config
        # value (or the module's own "not-needed" default) is not clobbered by an
        # empty env var during protected-key re-assertion in build_provider_entry.
        cc_api_key = os.environ.get("CHAT_COMPLETIONS_API_KEY", "")
        if cc_api_key:
            cc_fields["api_key"] = cc_api_key
        return CredentialResolution(
            provider=provider_name,
            resolved=True,
            source="env",
            env_var="CHAT_COMPLETIONS_BASE_URL",
            fields=cc_fields,
        )

    if provider_name == "vllm":
        # vLLM provider: talks the OpenAI Responses API to a self-hosted or
        # remote vLLM server. base_url is the required "credential" (which server
        # to reach); VLLM_API_KEY is optional -- local vLLM needs no auth (the
        # module defaults it to "EMPTY"). Dedicated branch so base_url lands in
        # fields["base_url"], not fields["api_key"] (same reasoning as
        # chat-completions). Absent base_url is honestly resolved=False/source=none:
        # there is no usable default to guess (the module's localhost:8000 fallback
        # is not something amplifier-agent should claim is "configured").
        base_url = os.environ.get("VLLM_BASE_URL", "")
        if not base_url:
            return CredentialResolution(
                provider=provider_name,
                resolved=False,
                source="none",
                env_var="VLLM_BASE_URL",
                fields={},
            )
        # api_key is ALWAYS carried here, unlike chat-completions above, and falls
        # back to the same placeholder the provider module's own mount() uses.
        #
        # The asymmetry is deliberate. `run` resolves the key inside the module
        # (`os.environ.get("VLLM_API_KEY", "EMPTY")`), where *absent* correctly
        # yields the default. `models list` does not go through mount() at all: it
        # builds the provider directly from these fields via
        # _try_instantiate_provider, which falls back to ``api_key=""`` for a field
        # that is not present. VLLMProvider's signature is
        # ``(base_url, *, api_key="EMPTY", config=...)``, so it matches that
        # helper's base_url+api_key+config attempt and receives the empty string,
        # overriding its own default -- and the OpenAI SDK rejects an empty key
        # with "Missing credentials ... set OPENAI_API_KEY", which names nothing
        # the user can act on. Carrying the placeholder makes the two paths agree
        # and keeps the common keyless local server working in both.
        vllm_fields: dict[str, str] = {
            "base_url": base_url,
            "api_key": os.environ.get("VLLM_API_KEY", "") or VLLM_KEYLESS_API_KEY,
        }
        return CredentialResolution(
            provider=provider_name,
            resolved=True,
            source="env",
            env_var="VLLM_BASE_URL",
            fields=vllm_fields,
        )

    env_vars = PROVIDER_CREDENTIAL_VARS.get(provider_name)
    if not env_vars:
        return CredentialResolution(provider=provider_name, resolved=False, source="none", env_var=None, fields={})

    primary_var = env_vars[0]
    value = os.environ.get(primary_var, "")
    if value:
        fields: dict[str, str] = {"api_key": value}
        _maybe_attach_azure_endpoint(provider_name, fields)
        return CredentialResolution(
            provider=provider_name, resolved=True, source="env", env_var=primary_var, fields=fields
        )

    for legacy_var in env_vars[1:]:
        legacy_value = os.environ.get(legacy_var, "")
        if legacy_value:
            _emit_legacy_env_var_notice(legacy_var, primary_var)
            fields = {"api_key": legacy_value}
            _maybe_attach_azure_endpoint(provider_name, fields)
            return CredentialResolution(
                provider=provider_name, resolved=True, source="env", env_var=legacy_var, fields=fields
            )

    # Local import: avoid a module-load-time cycle with admin.auth
    # (admin.auth imports KNOWN_PROVIDERS / PROVIDER_CREDENTIAL_VARS from
    # this module).
    from amplifier_agent_cli.admin.auth import resolve_credential_from_file

    file_key = resolve_credential_from_file(provider_name)
    if file_key:
        fields = {"api_key": file_key}
        _maybe_attach_azure_endpoint(provider_name, fields)
        return CredentialResolution(
            provider=provider_name, resolved=True, source="file", env_var=primary_var, fields=fields
        )

    return CredentialResolution(provider=provider_name, resolved=False, source="none", env_var=primary_var, fields={})


def resolve_provider_credentials(provider_name: str, *, required: bool = False) -> dict[str, str]:
    """Resolve *provider_name*'s credential/config fields as a plain dict.

    Thin wrapper over :func:`resolve_credential_detailed` returning just
    ``.fields`` — the shape every mount-config / provider-instantiation
    call site actually consumes (``{"api_key": ...}``, ``{"host": ...}``,
    etc.).

    Args:
        provider_name: A provider short-name.
        required: When ``True``, raise :class:`ProviderCredentialsMissingError`
            if *provider_name* is a known key-based provider (anthropic,
            openai, azure-openai) with no resolvable credential
            (``source == "none"``). Ollama and unknown provider names
            never raise regardless of ``required`` — ollama always has a
            usable default host, and "unknown provider" is a different
            failure mode the caller (e.g. ``PROVIDER_CATALOG.get``) should
            surface itself.

    Returns:
        ``resolution.fields`` (a fresh dict; safe for callers to mutate).
        ``{}`` for unknown provider names.
    """
    resolution = resolve_credential_detailed(provider_name)
    if required and resolution.source == "none" and provider_name in PROVIDER_CREDENTIAL_VARS:
        env_vars = PROVIDER_CREDENTIAL_VARS[provider_name]
        primary_var = env_vars[0]
        legacy_clause = f" (legacy {', '.join(env_vars[1:])} also unset)" if len(env_vars) > 1 else ""
        raise ProviderCredentialsMissingError(
            f"{primary_var} not set{legacy_clause} and no credentials.json entry for "
            f"{provider_name!r}; cannot fetch live model list. Run "
            f"`amplifier-agent auth set {provider_name} <key>`, export {primary_var}, "
            "or choose a different provider."
        )
    return dict(resolution.fields)


def enumerate_resolvable_providers() -> list[str]:
    """Return the subset of :data:`KNOWN_PROVIDERS` with a resolved credential.

    Used by the HTTP serve lifespan to auto-enable providers when no
    explicit ``host_config.providers`` block is declared (Phase 1 serve
    auto-enable). Ollama is included only when its host was explicitly
    configured (env or file) — its built-in localhost default
    (``source == "default"``) does NOT count as "resolvable" here, so a
    bare install doesn't silently auto-enroll a local Ollama daemon that
    may not even be running.
    """
    return [name for name in KNOWN_PROVIDERS if resolve_credential_detailed(name).resolved]


def _reassert_protected_keys(config: dict[str, Any], *, creds: dict[str, str], priority: int) -> None:
    """Re-assert engine-owned keys after an ``extra_config`` overlay.

    ``creds`` (env/file-resolved per-invocation credential fields —
    ``api_key``, ``host``, ``endpoint``, etc.) and ``priority`` (mount
    slot machinery) are not user-tunable via ``host_config.json``.
    Re-asserting them after ``config.update(extra_config)`` ensures a
    stale config file cannot silently downgrade a fresh credential or
    override the mount priority. New engine-owned keys belong here.
    """
    config["priority"] = priority
    for key, value in creds.items():
        config[key] = value


def provider_config_from_host(host_config: dict[str, Any] | None) -> dict[str, Any] | None:
    """Derive the provider ``extra_config`` overlay from a loaded host config.

    Single source of truth for both faces. The CLI (``modes/single_turn.py``) and the
    HTTP face (``amplifier_agent_http/_session_runner.py``) inject providers at
    different points in their lifecycles; routing both through here is what stops
    ``--config`` from meaning one thing under ``run`` and another under ``serve``.

    Two inputs are folded together:

    * ``debug.rawLlmPayloads`` -> ``raw``. A provider-agnostic switch, since every
      provider module reads the same ``raw`` key.
    * ``provider.config`` -> verbatim pass-through (``default_model``, ``effort``,
      ``temperature``, any future provider-specific key).

    ``provider.config`` is applied last, so an explicit ``provider.config.raw`` wins
    over the debug block. That ordering is deliberate: the debug switch is sugar, and
    the low-level key stays the final say.

    Returns ``None`` when nothing is configured, matching ``extra_config``'s
    "no overlay" sentinel.
    """
    if not isinstance(host_config, dict):
        return None

    overlay: dict[str, Any] = {}

    debug_block = host_config.get("debug")
    if isinstance(debug_block, dict) and debug_block.get("rawLlmPayloads") is True:
        # `is True` rather than truthiness: the loader already rejects non-booleans,
        # and this keeps the guarantee intact for any caller that skipped the loader.
        overlay["raw"] = True

    provider_block = host_config.get("provider")
    if isinstance(provider_block, dict):
        provider_config = provider_block.get("config")
        if isinstance(provider_config, dict):
            overlay.update(provider_config)

    return overlay or None


def build_provider_entry(
    provider_name: str,
    model_override: str | None = None,
    effort_override: str | None = None,
    extra_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a ``mount_plan["providers"]`` entry for one provider.

    Resolves credentials via :func:`resolve_provider_credentials` (the
    canonical resolver — see module docstring). The resolution is
    intentionally per-invocation rather than at module import time so
    that:

    * the prepared-bundle pickle on disk never contains secrets,
    * users who export the env var after first install (or rotate keys)
      pick up the new value without having to ``cache clear``.

    The mount entry follows the shape app-cli's ``runtime/config.py``
    writes into ``prepared.mount_plan["providers"]``: ``module``,
    ``source``, and a ``config`` dict. ``config`` always contains the
    resolved credential fields (``api_key`` for key providers; ``host``
    for ollama; possibly empty when nothing resolved) and ``priority``
    (``1`` — there's only ever one provider mounted in this CLI).
    ``default_model`` and ``effort`` appear only when the caller passes
    an override; otherwise they're omitted entirely so the provider's own
    ``get_info().defaults`` wins.

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
            top of the base credential/priority config AFTER any
            ``model_override`` / ``effort_override`` are applied, so the
            host config has the final word on knobs like ``temperature``,
            ``max_tokens``, ``thinking_budget_tokens``, and any future
            provider-specific keys. Engine-asserted keys (credential
            fields, ``priority``) are re-asserted after the overlay so a
            stale config file cannot downgrade a fresh credential or
            override the mount priority.

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

    creds = resolve_provider_credentials(provider_name)
    priority = 1
    config: dict[str, Any] = {"priority": priority, **creds}
    if model_override is not None:
        config["default_model"] = model_override
    if effort_override is not None:
        config["effort"] = effort_override
    if extra_config:
        config.update(extra_config)

    # A placeholder credential stands in for the provider module's own default
    # (see :data:`VLLM_KEYLESS_API_KEY`), so it is not the kind of engine-resolved
    # value protected-key re-assertion exists to defend. Dropping it from the
    # re-assertion set when host config supplied a real api_key preserves the
    # guarantee the chat-completions branch documents: a key set in
    # provider.config must not be silently replaced by a "no key needed" default.
    # A genuinely resolved VLLM_API_KEY is not a placeholder and still wins.
    protected = dict(creds)
    if protected.get("api_key") == VLLM_KEYLESS_API_KEY and (extra_config or {}).get("api_key"):
        del protected["api_key"]

    _reassert_protected_keys(config, creds=protected, priority=priority)
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


#: Map provider short-name → routing-matrix file name (``routing/<name>.yaml``
#: inside the ``amplifier-bundle-routing-matrix`` bundle).
#:
#: Per the 2026-06-15 design discussion (see workspace one-pager), amplifier-agent
#: picks the matrix automatically based on the active provider rather than asking
#: the user to choose. The mapping reflects the rejected-vs-accepted distinction
#: from that meeting:
#:
#: * Anthropic / OpenAI / Ollama → that provider's own within-provider matrix.
#:   These are single-provider, single-model-family catalogs.
#: * Azure OpenAI → ``openai`` matrix. Azure OpenAI serves the same model family
#:   as OpenAI-direct; reusing the OpenAI matrix avoids maintaining a near-
#:   duplicate file. A dedicated ``azure-openai.yaml`` can be authored later if
#:   the SKU/multiplier landscape diverges.
#: * (Future) GitHub Copilot → ``copilot`` matrix. GHCP is ONE provider that
#:   internally serves multiple model families (Claude, GPT, Gemini); the
#:   ``copilot.yaml`` matrix is Mallory's curated multiplier-aware ordering for
#:   that within-GHCP cross-model selection. Not in :data:`PROVIDER_CATALOG`
#:   yet — the mapping is here so it activates automatically once the provider
#:   module lands.
#:
#: Providers not in this map fall through to the bundle's hardcoded
#: ``default_matrix`` (currently ``balanced`` per ``bundle.md``).
PROVIDER_MATRIX_MAP: Final[dict[str, str]] = {
    "anthropic": "anthropic",
    "openai": "openai",
    "azure-openai": "openai",
    "ollama": "ollama",
    # Activates automatically when github-copilot is added to PROVIDER_CATALOG.
    "github-copilot": "copilot",
}


def inject_routing_matrix(prepared: Any, provider_name: str) -> None:
    """Override the ``hooks-routing`` module's ``default_matrix`` to match the
    active provider.

    Walks ``prepared.mount_plan["hooks"]``, finds the ``hooks-routing`` entry,
    and rewrites its ``config["default_matrix"]`` to the matrix file that
    matches the active provider (per :data:`PROVIDER_MATRIX_MAP`).

    No-op when any of the following hold:
      * routing-matrix is not in the bundle (no ``hooks-routing`` entry),
      * the active provider is not in :data:`PROVIDER_MATRIX_MAP` (the
        bundle's hardcoded ``default_matrix`` stays in effect),
      * ``mount_plan`` has no ``hooks`` section at all.

    Mirrors the :func:`inject_provider` pattern: mutate the prepared bundle's
    mount_plan in place, after the cache returns and before the kernel mounts.

    Why a per-invocation override and not a bundle.md edit?

      * The bundle is sealed and cached by sha256 of ``bundle.md``. Encoding
        per-provider matrix selection in ``bundle.md`` would mean either a
        static default that doesn't track the active provider, or a templated
        bundle that breaks the cache invariant.
      * Per-invocation injection at the same seam where provider credentials
        are resolved keeps the routing decision adjacent to the provider
        decision they depend on. One env-precedence resolution drives both.

    Args:
        prepared: The prepared bundle from ``load_and_prepare_cached()``.
            Must expose a mutable ``mount_plan`` dict attribute.
        provider_name: The active provider short-name (one of
            ``PROVIDER_CATALOG`` keys, typically).
    """
    matrix_name = PROVIDER_MATRIX_MAP.get(provider_name)
    if matrix_name is None:
        return
    hooks = prepared.mount_plan.get("hooks") or []
    for entry in hooks:
        if not isinstance(entry, dict):
            continue
        if entry.get("module") != "hooks-routing":
            continue
        config = dict(entry.get("config") or {})
        config["default_matrix"] = matrix_name
        entry["config"] = config
        return

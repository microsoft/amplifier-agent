"""FastAPI application factory and lifespan.

Slice 2 lifespan loads the PreparedBundle ONCE at process start and caches it
on ``app.state``. Per-request handlers reuse this bundle via
``run_chat_turn`` rather than re-mounting modules on each call. This is the
"D6 boot split" pattern from the design doc applied at the simplest scale:
one process, one bundle, one user, mounts cached.

The bundle load is async, so it runs inside the lifespan rather than at
import time. Failures here will surface at process startup, not at first
request -- by design. A misconfigured bundle should fail loudly and early.
"""

import asyncio
import logging
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from amplifier_agent_cli.admin.models import (
    ProviderCredentialsMissingError,
    ProviderModuleNotInstalledError,
    list_provider_models,
)
from amplifier_agent_cli.provider_sources import PROVIDER_CATALOG, enumerate_resolvable_providers
from amplifier_agent_http._config import load_config
from amplifier_agent_http._session_runner import hydrate_agent_configs
from amplifier_agent_http.routes import chat_completions, models, modes, skills
from amplifier_agent_lib import resources
from amplifier_agent_lib._runtime import prepare_bundle_for_session
from amplifier_agent_lib.bundle.cache import load_and_prepare_cached
from amplifier_agent_lib.config import ConfigError
from amplifier_agent_lib.config import load_config as load_host_config
from amplifier_agent_lib.persistence import resolve_workspace

logger = logging.getLogger("amplifier_agent_http")


def _resolve_aaa_version() -> str:
    """Resolve the amplifier-agent package version from installed metadata.

    Mirrors the pattern used inside ``amplifier_agent_lib.__init__``. We do it
    here directly instead of importing ``__version__`` from the lib because
    the lib's ``__version__`` is computed via ``importlib.metadata`` inside a
    try/except, which pyright sometimes fails to resolve as an exported name
    across a freshly-added sibling package.
    """
    try:
        return _pkg_version("amplifier-agent")
    except PackageNotFoundError:
        # Editable install before metadata is registered, or a bare checkout.
        # The cache key just needs to be stable; the value is opaque.
        return "0.0.0+unknown"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Wire process-wide state on startup; release on shutdown.

    Slice 2: load PreparedBundle, hydrate agent overlays, stash on app.state.
    Per-request handlers use these via app.state lookup.
    """
    config = load_config()
    app.state.config = config

    logger.info(
        "amplifier-agent HTTP face starting -- model_id=%r api_key_set=%r",
        config.model_id,
        bool(config.api_key),
    )
    aaa_version = _resolve_aaa_version()
    logger.info("Loading prepared bundle (aaa_version=%s) ...", aaa_version)
    prepared = await load_and_prepare_cached(aaa_version=aaa_version)

    # NOTE: provider injection is now PER REQUEST (in ``_session_runner.run_chat_turn``).
    # We no longer call ``inject_provider`` once at lifespan -- the wire's
    # ``model`` field determines which provider serves each request via the
    # ``served_models_registry`` we build below. The registry is populated from
    # ``host_config.providers`` when explicitly declared (--config), and
    # otherwise auto-enabled from whichever providers have resolvable
    # credentials (env var or ``credentials.json`` -- see
    # ``amplifier_agent_cli.provider_sources.enumerate_resolvable_providers``).
    # See ``run_chat_turn`` for the per-request swap (under the existing
    # ``_create_session_lock``).

    # Load the optional host-config file (``--config <path>``). This is what
    # customizes a given amplifier-agent process: provider selection, MCP
    # servers, skills configuration, etc. Everything else (ServerConfig,
    # port, bind, api_key) is wire-shape concern. Schema is closed at the
    # top level (D7); the loader enforces validation and raises ConfigError
    # which we propagate so startup fails loudly on a bad config.
    host_config: dict[str, Any] = {}
    if config.host_config_path:
        try:
            host_config = load_host_config(config_arg=config.host_config_path) or {}
        except ConfigError as exc:
            logger.error(
                "Failed to load host config from %r: %s (%s)",
                config.host_config_path,
                exc.message,
                exc.code,
            )
            raise
        logger.info("Host config loaded from %s", config.host_config_path)
    app.state.host_config = host_config

    # Resolve the workspace slug ONCE at startup -- mirrors the CLI's
    # ``--workspace`` flag (D1) via ``resolve_workspace``: explicit env
    # ``AMPLIFIER_AGENT_HTTP_WORKSPACE`` / ``AMPLIFIER_AGENT_WORKSPACE`` > cwd.
    # This slug determines where the context-intelligence hook lands
    # per-session events on disk.
    #
    # POC scope: server-process workspace, single tenant. Per-request
    # workspace override (correlating to opencode's sessionID, etc.) requires
    # per-session mount-plan isolation -- on the v2 design backlog as the
    # clone-return variant of ``prepare_bundle_for_session``.
    resolved_workspace = resolve_workspace(
        argv_workspace=config.workspace,
        env={},  # env was already collapsed into ``config.workspace`` in load_config
        cwd=Path.cwd(),
    )

    # Apply the bundle-prep transforms: mcp.configPath → env (D4),
    # merge_config overlay onto mount_plan (D5), and Fix C hook-context-
    # intelligence workspace seed. Single source of truth shared with the
    # CLI's ``make_turn_handler``. Mutates ``prepared.mount_plan`` in place.
    #
    # ``approval.mode`` is intentionally NOT applied even when present in
    # host_config: the HTTP face uses ``HttpAutoApprovalSystem`` (auto-allow)
    # since the chat-completions wire has no human-in-the-loop seam.
    prepare_bundle_for_session(
        prepared,
        host_config=host_config,
        workspace=resolved_workspace,
    )
    logger.info(
        "workspace resolved to %r; bundle prepared via prepare_bundle_for_session",
        resolved_workspace,
    )

    # Trigger module installation for all known provider modules.
    #
    # ``amplifier-agent run`` installs provider modules lazily during
    # ``create_session() → session.initialize()``, where the kernel calls
    # ``prepared.resolver.async_resolve(module_id, source_hint)`` for each
    # entry in ``mount_plan["providers"]``.  The lifespan never calls
    # ``create_session()``, so on a fresh install the provider Python packages
    # are not in the tool venv when ``list_provider_models()`` tries to import
    # them below — resulting in ``ProviderModuleNotInstalledError``.
    #
    # Calling ``async_resolve`` here is the same install trigger ``run`` uses:
    # - Fast path (warm cache): module already in ``resolver._paths`` → returns
    #   immediately with no subprocess.
    # - Lazy path (cold / first-ever boot): ``ModuleActivator.activate()`` runs
    #   ``uv pip install --editable <source>`` and adds the path to the resolver.
    # Both paths make the module importable before the providers loop runs.
    #
    # bundle.md declares all 4 providers in its top-level ``providers:`` section
    # so ``bundle.prepare(install_deps=True)`` installs them during cold-prepare
    # (and post-install).  This ``async_resolve`` loop is the belt-and-suspenders
    # for the serve path: it is always idempotent, never re-installs on warm cache.
    for _cat_name, _cat_entry in PROVIDER_CATALOG.items():
        try:
            await prepared.resolver.async_resolve(_cat_entry["module"], _cat_entry["source"])
            logger.info("Provider module ready: %s", _cat_entry["module"])
        except Exception as exc:  # broad: activation failure is non-fatal here
            # Log and continue.  A provider whose module can't be activated will
            # fail loudly in the providers loop below with a structured error.
            logger.warning(
                "Could not ensure provider module %r is installed (%s: %s); "
                "list_provider_models() will report the failure.",
                _cat_entry["module"],
                type(exc).__name__,
                exc,
            )

    app.state.prepared = prepared
    app.state.resolved_workspace = resolved_workspace
    app.state.agent_configs = hydrate_agent_configs(prepared)

    # Enumerate the shipped skills/modes ONCE, now that the bundle is prepared
    # (which put the tool-skills / hooks-mode discovery packages on sys.path).
    # These back GET /v1/skills and GET /v1/modes via the SAME helpers the CLI
    # uses (``amplifier-agent skills list`` / ``modes list``) -- single source
    # of truth so the e2e CLI<->HTTP parity assertions hold. Pass host_config so
    # configured skill dirs are honored; list_modes ignores it (modes use
    # conventional search paths). Discovery is best-effort: a failure here must
    # not take down a server whose core (chat-completions) is healthy, so we log
    # and serve an empty list rather than exiting.
    try:
        app.state.available_skills = resources.list_skills(host_config or None)
        app.state.available_modes = resources.list_modes(host_config or None)
        logger.info(
            "Discovered %d user-invocable skill(s) and %d mode(s).",
            len(app.state.available_skills),
            len(app.state.available_modes),
        )
    except Exception as exc:  # broad: discovery failure is non-fatal for serving
        app.state.available_skills = []
        app.state.available_modes = []
        logger.warning(
            "Skills/modes discovery failed (%s: %s); /v1/skills and /v1/modes will report empty lists.",
            type(exc).__name__,
            exc,
        )

    # Explicit per-provider model enumeration.  ``host_config.providers`` is
    # authoritative: we load exactly the providers declared there, fail loudly
    # on any that cannot initialize.  The previous behavior (iterate
    # KNOWN_PROVIDERS, skip silently, fall back to a placeholder) is removed.
    #
    # ``list_provider_models`` is sync and may call ``asyncio.run()`` internally;
    # wrap each call in ``to_thread`` so the lifespan event loop is not blocked.
    # served_models_registry: maps wire model id -> provider id (e.g. "anthropic")
    # so chat_completions can route the per-request inject_provider() call.
    providers_block: dict[str, Any] = (app.state.host_config or {}).get("providers") or {}

    if not providers_block:
        # No explicit `--config` providers block. Auto-enable from whatever
        # credentials are resolvable (env var or credentials.json set via
        # `amplifier-agent auth set <provider> <key>`) rather than requiring
        # every user to hand-author a host_config just to boot the server.
        # Explicit `--config` providers still win: this branch only runs when
        # that block is absent or empty.
        resolvable = enumerate_resolvable_providers()
        if not resolvable:
            logger.error(
                "amplifier-agent serve chat-completions: no providers configured and no "
                "resolvable credentials. Set one via `amplifier-agent auth set <provider> "
                "<key>`, export the provider's env var (e.g. ANTHROPIC_API_KEY), or pass "
                "--config with an explicit `providers:` block."
            )
            sys.exit(2)
        providers_block = {name: {"module": PROVIDER_CATALOG[name]["module"]} for name in resolvable}
        logger.info(
            "No --config providers declared; auto-enabled from resolvable credentials: %s",
            resolvable,
        )

    app.state.available_models = []
    app.state.served_models_registry = {}
    errors: list[str] = []

    for provider_id, entry in providers_block.items():
        # NOTE: `list_provider_models` (and the credential resolver it calls
        # internally) key on the short provider name (e.g. "anthropic"), not
        # the installed Python module name (e.g. "provider-anthropic"). The
        # `module` field on the entry is for module *installation* only --
        # passing it here as the credential-resolution key silently failed to
        # resolve (this was the serve-startup half of the credential-
        # resolution divergence bug).
        module_id = entry.get("module", provider_id)
        provider_config = entry.get("config", {})
        try:
            provider_models = await asyncio.to_thread(
                list_provider_models,
                provider_id,
                15.0,
                provider_config,  # extra_config — passes per-provider config through
            )
        except ProviderCredentialsMissingError as exc:
            errors.append(f"provider {provider_id!r}: credentials missing — {exc}")
            continue
        except ProviderModuleNotInstalledError as exc:
            errors.append(f"provider {provider_id!r}: module {module_id!r} not installed — {exc}")
            continue
        except Exception as exc:
            errors.append(f"provider {provider_id!r}: failed to enumerate models — {type(exc).__name__}: {exc}")
            continue
        if not provider_models:
            # 0-models tolerance: demote from fatal to a warning and skip this
            # provider from the served registry. Startup only fails below when
            # NO provider (across the whole block) contributed any models --
            # a single provider returning an empty list (azure-openai by
            # design, an ollama daemon that happens to be down, etc.) should
            # not take down a server that has other working providers.
            logger.warning(
                "Provider %r (module %r) returned 0 models; skipping from served registry.",
                provider_id,
                module_id,
            )
            continue
        for m in provider_models:
            d = m.model_dump() if hasattr(m, "model_dump") else dict(m)
            d["_provider"] = provider_id
            app.state.available_models.append(d)
            app.state.served_models_registry[d["id"]] = provider_id
        logger.info("Loaded %d models from provider %r", len(provider_models), provider_id)

    if errors:
        # Non-fatal by itself: log for diagnostics. A provider that hard-fails
        # (missing credentials, module not installed, etc.) should not take
        # down the whole server if at least one other declared/auto-enabled
        # provider succeeded. Startup only fails below when the served
        # registry ends up empty across ALL providers.
        logger.error(
            "amplifier-agent serve had errors initializing %d of %d declared providers "
            "(continuing with any providers that succeeded):\n  %s",
            len(errors),
            len(providers_block),
            "\n  ".join(errors),
        )

    if not app.state.served_models_registry:
        logger.error(
            "amplifier-agent serve: no provider produced any models (all %d declared "
            "provider(s) failed or returned empty lists). Cannot start.",
            len(providers_block),
        )
        sys.exit(2)

    logger.info(
        "Prepared bundle loaded with providers; %d agents hydrated. Ready to serve.",
        len(app.state.agent_configs),
    )

    # Write the state file so lifecycle commands (serve status / stop / restart)
    # can discover the running server without re-parsing CLI flags.
    # providers_summary maps provider_id -> model count for the status display.
    from amplifier_agent_cli.admin.serve_lifecycle import (
        remove_state_file,
        write_state_file,
    )

    providers_summary: dict[str, int] = {}
    for m in app.state.available_models:
        pid_ = m.get("_provider", "unknown")
        providers_summary[pid_] = providers_summary.get(pid_, 0) + 1

    write_state_file(
        {
            "pid": os.getpid(),
            "started_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "host": app.state.config.host,
            "port": app.state.config.port,
            "api_key": app.state.config.api_key,
            "workspace": app.state.resolved_workspace,
            "host_config_path": app.state.config.host_config_path or None,
            "providers_summary": providers_summary,
        }
    )
    logger.info("State file written; server is discoverable via 'amplifier-agent serve status'.")

    try:
        yield
    finally:
        logger.info("amplifier-agent HTTP face shutting down")
        remove_state_file()


def build_app() -> FastAPI:
    """Construct a FastAPI app instance.

    Kept as a factory so tests can build their own without import side effects.
    """
    app = FastAPI(
        title="amplifier-agent HTTP face",
        version="0.0.2-poc",
        lifespan=lifespan,
        # OpenAPI docs are useful for debugging the wire shape.
        docs_url="/docs",
        redoc_url=None,
    )
    app.include_router(models.router)
    app.include_router(skills.router)
    app.include_router(modes.router)
    app.include_router(chat_completions.router)
    return app


# Module-level app for `uvicorn amplifier_agent_http.app:app`.
app = build_app()

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

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path

from fastapi import FastAPI

from amplifier_agent_cli.provider_sources import inject_provider
from amplifier_agent_http._config import load_config
from amplifier_agent_http._session_runner import hydrate_agent_configs
from amplifier_agent_http.routes import chat_completions, models
from amplifier_agent_lib.bundle.cache import load_and_prepare_cached
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

    # Inject the provider mount-plan entry from environment credentials.
    # The bundle.md only declares the provider module for cold-prepare; the
    # actual {api_key, default_model} entry is layered in at runtime by the
    # host. The CLI does this in modes/single_turn.py; we do the same. Reads
    # ANTHROPIC_API_KEY (or other supported peer env vars) and writes the
    # entry into prepared.mount_plan["providers"]. No-op if the bundle
    # already declared a non-empty providers list.
    #
    # POC scope: hardcode "anthropic" (the bundle's default). Multi-provider
    # routing via model name / config flag is in the v2 backlog.
    inject_provider(prepared, "anthropic")

    # Resolve the workspace slug ONCE at startup -- mirrors the CLI's
    # ``--workspace`` flag (D1) via ``resolve_workspace``: explicit env
    # ``AMPLIFIER_AGENT_HTTP_WORKSPACE`` / ``AMPLIFIER_AGENT_WORKSPACE`` > cwd.
    # This slug determines where the context-intelligence hook lands
    # per-session events on disk (under
    # ``~/.amplifier-agent/state/workspaces/<slug>/sessions/<sid>/``).
    #
    # Fix C: pre-seed ``project_slug`` (and ``workspace`` alias) into the
    # ``hook-context-intelligence`` module's own config. The hook reads
    # from its own config FIRST, then ``coordinator.config`` (D5, written
    # post-create_session), then a slugified ``session.working_dir`` (which
    # produces the unwieldy bundle-install-dir slug we're avoiding).
    # ``session:start`` fires INSIDE ``create_session()``, so by the time
    # D5 writes land it's too late for the first event. Seeding the hook
    # config in lifespan makes the slug available from the very first event.
    #
    # POC scope: server-process workspace, single tenant. Per-request
    # workspace override is in the v2 backlog -- supporting it cleanly
    # requires per-session mount-plan isolation (or a clone-on-write
    # mutation of ``prepared.mount_plan``) to avoid the race between
    # concurrent requests overwriting each other's hook config.
    resolved_workspace = resolve_workspace(
        argv_workspace=config.workspace,
        env={},  # the env-var fallback inside resolve_workspace reads its OWN env;
        # we already collapsed env into ``config.workspace`` in load_config
        # (HTTP-face-specific var > ecosystem var). Pass an empty mapping so
        # the env tier is a no-op and only argv > cwd applies.
        cwd=Path.cwd(),
    )
    seeded_any = False
    for entry in prepared.mount_plan.get("hooks") or []:
        if entry.get("module") == "hook-context-intelligence":
            hook_cfg = dict(entry.get("config") or {})
            hook_cfg["project_slug"] = resolved_workspace
            hook_cfg["workspace"] = resolved_workspace
            entry["config"] = hook_cfg
            seeded_any = True
            break
    logger.info(
        "workspace resolved to %r; hook-context-intelligence config %s",
        resolved_workspace,
        "pre-seeded (Fix C)" if seeded_any else "NOT FOUND in mount_plan",
    )

    app.state.prepared = prepared
    app.state.resolved_workspace = resolved_workspace
    app.state.agent_configs = hydrate_agent_configs(prepared)
    logger.info(
        "Prepared bundle loaded with provider; %d agents hydrated. Ready to serve.",
        len(app.state.agent_configs),
    )

    try:
        yield
    finally:
        logger.info("amplifier-agent HTTP face shutting down")


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
    app.include_router(chat_completions.router)
    return app


# Module-level app for `uvicorn amplifier_agent_http.app:app`.
app = build_app()

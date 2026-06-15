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

from fastapi import FastAPI

from amplifier_agent_cli.provider_sources import inject_provider
from amplifier_agent_http._config import load_config
from amplifier_agent_http._session_runner import hydrate_agent_configs
from amplifier_agent_http.routes import chat_completions, models
from amplifier_agent_lib.bundle.cache import load_and_prepare_cached

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

    app.state.prepared = prepared
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

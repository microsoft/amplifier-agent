"""Tests for the provider-module install trigger in the serve lifespan.

``amplifier-agent serve chat-completions`` calls ``list_provider_models()``
BEFORE any ``AmplifierSession`` is created.  On a fresh install the provider
Python packages are not yet in the tool venv (the lazy-install that ``run``
gets via ``create_session() → session.initialize()`` never fires for ``serve``).

The lifespan fix calls ``prepared.resolver.async_resolve(module_id, source)``
for every entry in ``PROVIDER_CATALOG`` before entering the providers loop.
This is the same underlying install trigger that ``run`` uses.

Three scenarios are covered:

1. Lifespan calls the install trigger for every CATALOG provider.
2. If the install trigger raises, the lifespan still reaches the providers
   loop; that loop surfaces the failure as a collected error and exits 2.
3. On a warm cache (module already installed) the call completes quickly
   without a no-op error path.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI

from amplifier_agent_cli.admin.models import ProviderModuleNotInstalledError
from amplifier_agent_cli.provider_sources import PROVIDER_CATALOG
from amplifier_agent_http._config import ServerConfig
from amplifier_agent_http.app import lifespan

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _server_config() -> ServerConfig:
    return ServerConfig(
        api_key="test-api-key",
        model_id="test-model",
        model_display_name="Test",
        host="127.0.0.1",
        port=9099,
        workspace=None,
        host_config_path="/tmp/test-host-config.json",
    )


@pytest.fixture()
def base_mocks(tmp_path):
    """Patch every heavy-weight lifespan dependency.

    Returns a dict of mock objects keyed by their symbolic name so individual
    tests can adjust return values / side effects.

    ``resolver_mock.async_resolve`` is an ``AsyncMock`` by default so it
    can be awaited without network access; override its side_effect to
    simulate cold-install or already-installed scenarios.
    """
    prepared_mock = MagicMock()
    prepared_mock.mount_plan = {}
    resolver_mock = MagicMock()
    resolver_mock.async_resolve = AsyncMock(return_value=None)
    prepared_mock.resolver = resolver_mock

    host_cfg = {"providers": {"anthropic": {}}}

    with (
        patch("amplifier_agent_http.app.load_config", return_value=_server_config()),
        patch(
            "amplifier_agent_http.app.load_and_prepare_cached",
            new_callable=AsyncMock,
            return_value=prepared_mock,
        ) as m_prep,
        patch(
            "amplifier_agent_http.app.load_host_config",
            return_value=host_cfg,
        ) as m_host,
        patch(
            "amplifier_agent_http.app.resolve_workspace",
            return_value="test-workspace",
        ),
        patch("amplifier_agent_http.app.prepare_bundle_for_session"),
        patch(
            "amplifier_agent_http.app.hydrate_agent_configs",
            return_value={},
        ),
        patch("amplifier_agent_http.app._resolve_aaa_version", return_value="0.0.0+test"),
        patch("amplifier_agent_cli.admin.serve_lifecycle.write_state_file"),
        patch("amplifier_agent_cli.admin.serve_lifecycle.remove_state_file"),
    ):
        yield {
            "load_and_prepare_cached": m_prep,
            "load_host_config": m_host,
            "prepared": prepared_mock,
            "resolver": resolver_mock,
        }


# ---------------------------------------------------------------------------
# Test 1: lifespan calls the install trigger for every CATALOG provider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lifespan_calls_install_trigger(base_mocks) -> None:
    """Lifespan must call resolver.async_resolve() for every PROVIDER_CATALOG entry.

    This verifies the wire-up: before the providers loop runs, each known
    provider module has had its install path triggered.
    """
    model_mock = MagicMock()
    model_mock.model_dump.return_value = {"id": "claude-opus-4-5"}

    app = FastAPI()
    with patch(
        "amplifier_agent_http.app.list_provider_models",
        return_value=[model_mock],
    ):
        async with lifespan(app):
            pass

    resolver = base_mocks["resolver"]
    # async_resolve must have been called at least once per CATALOG provider.
    assert resolver.async_resolve.call_count >= len(PROVIDER_CATALOG)

    # Verify the module IDs that were passed match CATALOG entries.
    called_modules = {call.args[0] for call in resolver.async_resolve.call_args_list}
    expected_modules = {entry["module"] for entry in PROVIDER_CATALOG.values()}
    assert expected_modules == called_modules


# ---------------------------------------------------------------------------
# Test 2: install failure is non-fatal; failure surfaces in providers loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lifespan_install_failure_still_fails_loud(base_mocks) -> None:
    """If resolver.async_resolve() raises, lifespan continues to the providers loop.

    The providers loop then calls list_provider_models(), which raises
    ProviderModuleNotInstalledError because the module is still uninstalled.
    The error is collected and lifespan exits 2.
    """
    base_mocks["resolver"].async_resolve = AsyncMock(side_effect=RuntimeError("uv install failed: network unreachable"))

    app = FastAPI()
    with (
        patch(
            "amplifier_agent_http.app.list_provider_models",
            side_effect=ProviderModuleNotInstalledError("anthropic module not installed"),
        ),
        pytest.raises(SystemExit) as exc_info,
    ):
        async with lifespan(app):
            pass  # pragma: no cover

    # Collected errors → exit 2 (same as test_lifespan_exits_when_provider_module_not_installed).
    assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# Test 3: warm-cache path — module already installed, async_resolve fast-path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lifespan_install_idempotent_warm_cache(base_mocks) -> None:
    """When modules are already installed, async_resolve() returns quickly (no-op).

    The lifespan must still CALL async_resolve (belt-and-suspenders) but the
    fast path inside BundleModuleResolver returns immediately when the module
    is already in resolver._paths.  We verify that a fast async_resolve does
    not prevent the lifespan from completing successfully.
    """
    # async_resolve returns immediately (simulates warm cache / already installed).
    base_mocks["resolver"].async_resolve = AsyncMock(return_value=None)

    model_mock = MagicMock()
    model_mock.model_dump.return_value = {"id": "claude-opus-4-5"}

    app = FastAPI()
    with patch(
        "amplifier_agent_http.app.list_provider_models",
        return_value=[model_mock],
    ):
        async with lifespan(app):
            # Lifespan completed without errors — server is ready.
            assert "claude-opus-4-5" in app.state.served_models_registry

    # async_resolve was still called (we always call it, idempotent).
    assert base_mocks["resolver"].async_resolve.call_count >= len(PROVIDER_CATALOG)

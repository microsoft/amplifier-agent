"""Tests for the explicit-providers lifespan boot semantics.

Verifies that:
- Server exits 2 when ``host_config.providers`` is absent, empty, or wrong type.
- Server exits 2 when any declared provider fails (missing credentials,
  missing module, list_models() raises, list_models() returns 0 models).
- Server boots successfully when all providers initialise correctly.
- Each provider's ``config`` block is forwarded as ``extra_config`` to
  ``list_provider_models``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI

from amplifier_agent_cli.admin.models import (
    ProviderCredentialsMissingError,
    ProviderModuleNotInstalledError,
)
from amplifier_agent_http._config import ServerConfig
from amplifier_agent_http.app import lifespan

# ---------------------------------------------------------------------------
# Shared mock helpers
# ---------------------------------------------------------------------------


def _server_config(host_config_path: str | None = "/tmp/test-host-config.json") -> ServerConfig:
    return ServerConfig(
        api_key="test-api-key",
        model_id="test-model",
        model_display_name="Test",
        workspace=None,
        host_config_path=host_config_path,
    )


def _model_info(model_id: str, provider_id: str) -> dict[str, Any]:
    """Minimal model-info dict that passes the lifespan's model_dump path."""
    m = MagicMock()
    m.model_dump.return_value = {"id": model_id}
    return m


@pytest.fixture()
def base_mocks(tmp_path):
    """Patch every heavy-weight lifespan dependency except list_provider_models.

    Returns a dict of mock objects keyed by their symbolic name so individual
    tests can adjust return values / side effects without re-declaring patches.
    """
    prepared_mock = MagicMock()
    prepared_mock.mount_plan = {}

    with (
        patch("amplifier_agent_http.app.load_config", return_value=_server_config()) as m_load_cfg,
        patch(
            "amplifier_agent_http.app.load_and_prepare_cached",
            new_callable=AsyncMock,
            return_value=prepared_mock,
        ) as m_prep,
        patch(
            "amplifier_agent_http.app.load_host_config",
            return_value={},
        ) as m_host,
        patch(
            "amplifier_agent_http.app.resolve_workspace",
            return_value="test-workspace",
        ) as m_ws,
        patch("amplifier_agent_http.app.prepare_bundle_for_session") as m_pbs,
        patch(
            "amplifier_agent_http.app.hydrate_agent_configs",
            return_value={},
        ) as m_hydrate,
        patch("amplifier_agent_http.app._resolve_aaa_version", return_value="0.0.0+test"),
    ):
        yield {
            "load_config": m_load_cfg,
            "load_and_prepare_cached": m_prep,
            "load_host_config": m_host,
            "resolve_workspace": m_ws,
            "prepare_bundle_for_session": m_pbs,
            "hydrate_agent_configs": m_hydrate,
            "prepared": prepared_mock,
        }


# ---------------------------------------------------------------------------
# Exit-2 scenarios
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lifespan_exits_when_providers_block_missing(base_mocks) -> None:
    """No ``providers`` key in host_config → exit 2."""
    base_mocks["load_host_config"].return_value = {}  # no providers key
    app = FastAPI()

    with pytest.raises(SystemExit) as exc_info:
        async with lifespan(app):
            pass  # pragma: no cover

    assert exc_info.value.code == 2


@pytest.mark.asyncio
async def test_lifespan_exits_when_providers_block_empty_dict(base_mocks) -> None:
    """``providers: {}`` → exit 2 (must declare at least one provider)."""
    base_mocks["load_host_config"].return_value = {"providers": {}}
    app = FastAPI()

    with pytest.raises(SystemExit) as exc_info:
        async with lifespan(app):
            pass  # pragma: no cover

    assert exc_info.value.code == 2


@pytest.mark.asyncio
async def test_lifespan_exits_when_providers_block_wrong_type(base_mocks) -> None:
    """``providers: "not-a-dict"`` → ConfigError raised by load_config before lifespan runs.

    The validator in loader.py raises ConfigError, which propagates up through
    the lifespan's load_host_config call and is re-raised.
    """
    from amplifier_agent_lib.config import ConfigError

    base_mocks["load_host_config"].side_effect = ConfigError(
        code="config_invalid_type",
        message="`providers` must be a JSON object",
        classification="protocol",
    )
    app = FastAPI()

    with pytest.raises(ConfigError) as exc_info:
        async with lifespan(app):
            pass  # pragma: no cover

    assert exc_info.value.code == "config_invalid_type"


@pytest.mark.asyncio
async def test_lifespan_exits_when_provider_credentials_missing(base_mocks) -> None:
    """Provider with missing credentials → collected error → exit 2."""
    base_mocks["load_host_config"].return_value = {"providers": {"anthropic": {}}}
    app = FastAPI()

    with (
        patch(
            "amplifier_agent_http.app.list_provider_models",
            side_effect=ProviderCredentialsMissingError("ANTHROPIC_API_KEY not set"),
        ),
        pytest.raises(SystemExit) as exc_info,
    ):
        async with lifespan(app):
            pass  # pragma: no cover

    assert exc_info.value.code == 2


@pytest.mark.asyncio
async def test_lifespan_exits_when_provider_module_not_installed(base_mocks) -> None:
    """Provider module not installed → collected error → exit 2."""
    base_mocks["load_host_config"].return_value = {"providers": {"openai": {}}}
    app = FastAPI()

    with (
        patch(
            "amplifier_agent_http.app.list_provider_models",
            side_effect=ProviderModuleNotInstalledError("openai provider not installed"),
        ),
        pytest.raises(SystemExit) as exc_info,
    ):
        async with lifespan(app):
            pass  # pragma: no cover

    assert exc_info.value.code == 2


@pytest.mark.asyncio
async def test_lifespan_exits_when_provider_returns_no_models(base_mocks) -> None:
    """list_models() returns [] → collected error → exit 2."""
    base_mocks["load_host_config"].return_value = {"providers": {"anthropic": {}}}
    app = FastAPI()

    with (
        patch("amplifier_agent_http.app.list_provider_models", return_value=[]),
        pytest.raises(SystemExit) as exc_info,
    ):
        async with lifespan(app):
            pass  # pragma: no cover

    assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# Happy-path scenarios
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lifespan_succeeds_with_two_providers(base_mocks) -> None:
    """Two providers load successfully → registry contains both providers' models."""
    anthropic_model = MagicMock()
    anthropic_model.model_dump.return_value = {"id": "claude-3-5-sonnet-20241022"}

    openai_model = MagicMock()
    openai_model.model_dump.return_value = {"id": "gpt-4o"}

    base_mocks["load_host_config"].return_value = {
        "providers": {
            "anthropic": {},
            "openai": {},
        }
    }
    app = FastAPI()

    def _side_effect(provider_id: str, timeout: float, extra_config: dict | None = None):
        if provider_id == "anthropic":
            return [anthropic_model]
        if provider_id == "openai":
            return [openai_model]
        return []

    with patch("amplifier_agent_http.app.list_provider_models", side_effect=_side_effect):
        async with lifespan(app):
            # Verify registry mappings
            assert app.state.served_models_registry["claude-3-5-sonnet-20241022"] == "anthropic"
            assert app.state.served_models_registry["gpt-4o"] == "openai"
            # Verify available_models has both, tagged with _provider
            model_ids = {m["id"] for m in app.state.available_models}
            assert "claude-3-5-sonnet-20241022" in model_ids
            assert "gpt-4o" in model_ids
            providers_in_models = {m["_provider"] for m in app.state.available_models}
            assert "anthropic" in providers_in_models
            assert "openai" in providers_in_models


@pytest.mark.asyncio
async def test_lifespan_passes_extra_config_to_list_provider_models(base_mocks) -> None:
    """Provider ``config`` block is forwarded as ``extra_config`` to list_provider_models."""
    extra_cfg = {"base_url": "https://api.openai.com/v1", "filtered": False}
    base_mocks["load_host_config"].return_value = {
        "providers": {
            "openai": {"config": extra_cfg},
        }
    }
    app = FastAPI()

    model_mock = MagicMock()
    model_mock.model_dump.return_value = {"id": "gpt-4o"}

    calls: list[tuple] = []

    def _capture(provider_id: str, timeout: float, extra_config: dict | None = None):
        calls.append((provider_id, timeout, extra_config))
        return [model_mock]

    with patch("amplifier_agent_http.app.list_provider_models", side_effect=_capture):
        async with lifespan(app):
            pass

    assert len(calls) == 1
    called_provider, _timeout, called_extra = calls[0]
    assert called_provider == "openai"
    assert called_extra == extra_cfg

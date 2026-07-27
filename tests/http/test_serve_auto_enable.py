"""Tests for the serve auto-enable lifespan path (Phase 1 spec section 3).

Before this pass, ``serve`` startup REQUIRED an explicit ``--config`` with a
non-empty ``providers:`` block, even when credentials were already configured
via ``amplifier-agent auth set`` or a provider env var. These tests verify:

- When no explicit ``host_config.providers`` block is present, serve
  auto-enables from whatever providers ``enumerate_resolvable_providers()``
  reports (env or credentials.json), and boots without requiring --config.
- Explicit ``--config`` providers still win when present.
- Startup exits 2 only when NO provider resolves any credentials at all.
- 0-models tolerance: a provider returning zero models is demoted to a
  warning and skipped from the served registry; startup only fails when the
  served_models_registry ends up empty across ALL providers.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI

from amplifier_agent_http._config import ServerConfig
from amplifier_agent_http.app import lifespan


def _server_config(host_config_path: str | None = "/tmp/test-host-config.json") -> ServerConfig:
    return ServerConfig(
        api_key="test-api-key",
        model_id="test-model",
        model_display_name="Test",
        host="127.0.0.1",
        port=9099,
        workspace=None,
        host_config_path=host_config_path,
    )


@pytest.fixture(autouse=True)
def _clear_provider_env(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate env AND the real ``~/.amplifier-agent`` credentials store.

    ``host_config_path`` defaults to non-``None`` above so ``load_config`` is
    always invoked (mirrors production: the lifespan only skips it when truly
    no ``--config``/``$AMPLIFIER_AGENT_CONFIG`` was supplied); tests override
    ``base_mocks["load_host_config"].return_value`` to control the parsed
    providers block directly, so the literal path here is never read.
    """
    for var in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_KEY",
        "OLLAMA_HOST",
        "OLLAMA_BASE_URL",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AMPLIFIER_AGENT_HOME", str(tmp_path))


@pytest.fixture()
def base_mocks(tmp_path):
    """Patch every heavy-weight lifespan dependency except list_provider_models
    and the credential-resolution layer under test."""
    prepared_mock = MagicMock()
    prepared_mock.mount_plan = {}
    prepared_mock.resolver.async_resolve = AsyncMock(return_value=None)

    with (
        patch("amplifier_agent_http.app.load_config", return_value=_server_config()) as m_load_cfg,
        patch(
            "amplifier_agent_http.app.load_and_prepare_cached",
            new_callable=AsyncMock,
            return_value=prepared_mock,
        ) as m_prep,
        patch("amplifier_agent_http.app.load_host_config", return_value={}) as m_host,
        patch("amplifier_agent_http.app.resolve_workspace", return_value="test-workspace") as m_ws,
        patch("amplifier_agent_http.app.prepare_bundle_for_session") as m_pbs,
        patch("amplifier_agent_http.app.hydrate_agent_configs", return_value={}) as m_hydrate,
        patch("amplifier_agent_http.app._resolve_aaa_version", return_value="0.0.0+test"),
        patch("amplifier_agent_cli.admin.serve_lifecycle.write_state_file") as m_write_sf,
        patch("amplifier_agent_cli.admin.serve_lifecycle.remove_state_file") as m_remove_sf,
    ):
        yield {
            "load_config": m_load_cfg,
            "load_and_prepare_cached": m_prep,
            "load_host_config": m_host,
            "resolve_workspace": m_ws,
            "prepare_bundle_for_session": m_pbs,
            "hydrate_agent_configs": m_hydrate,
            "prepared": prepared_mock,
            "write_state_file": m_write_sf,
            "remove_state_file": m_remove_sf,
        }


def _model(model_id: str) -> Any:
    m = MagicMock()
    m.model_dump.return_value = {"id": model_id}
    return m


@pytest.mark.asyncio
async def test_serve_auto_enables_after_auth_set(base_mocks, monkeypatch: pytest.MonkeyPatch) -> None:
    """No --config providers, credentials.json has anthropic only -> auto-enabled,
    boots without sys.exit."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-value")
    base_mocks["load_host_config"].return_value = {}  # no providers block at all
    app = FastAPI()

    with patch(
        "amplifier_agent_http.app.list_provider_models",
        return_value=[_model("claude-3-5-sonnet-20241022")],
    ):
        async with lifespan(app):
            # Filter out synthetic ``mode-<name>`` aliases (registered from the
            # vendored bundle modes) so this asserts the real provider->model
            # registration exactly, independent of mode-alias advertisement.
            real = {k: v for k, v in app.state.served_models_registry.items() if not k.startswith("mode-")}
            assert real == {"claude-3-5-sonnet-20241022": "anthropic"}


@pytest.mark.asyncio
async def test_serve_exits_when_no_credentials(base_mocks) -> None:
    """No --config providers block and nothing resolvable -> sys.exit(2)."""
    base_mocks["load_host_config"].return_value = {}
    app = FastAPI()

    with pytest.raises(SystemExit) as exc_info:
        async with lifespan(app):
            pass  # pragma: no cover

    assert exc_info.value.code == 2


@pytest.mark.asyncio
async def test_serve_explicit_config_overrides_autoenable(base_mocks, monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit host_config.providers wins even when other credentials are resolvable."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-value")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-value-2")
    base_mocks["load_host_config"].return_value = {"providers": {"openai": {}}}
    app = FastAPI()

    calls: list[str] = []

    def _capture(provider_id: str, timeout: float, extra_config: dict | None = None):
        calls.append(provider_id)
        return [_model("gpt-4o")]

    with patch("amplifier_agent_http.app.list_provider_models", side_effect=_capture):
        async with lifespan(app):
            pass

    # Only the explicitly-declared provider is queried, not the auto-enable set.
    assert calls == ["openai"]


@pytest.mark.asyncio
async def test_serve_zero_models_provider_not_fatal(base_mocks, monkeypatch: pytest.MonkeyPatch) -> None:
    """One provider returns 0 models, another returns >=1 -> startup succeeds;
    the 0-model provider is warned-and-skipped, not counted as a fatal error."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-value")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-value-2")
    base_mocks["load_host_config"].return_value = {
        "providers": {"anthropic": {}, "openai": {}},
    }
    app = FastAPI()

    def _side_effect(provider_id: str, timeout: float, extra_config: dict | None = None):
        if provider_id == "anthropic":
            return []  # 0 models: should be a warning, not fatal
        return [_model("gpt-4o")]

    with patch("amplifier_agent_http.app.list_provider_models", side_effect=_side_effect):
        async with lifespan(app):
            assert "anthropic" not in {p for p in app.state.served_models_registry.values()}
            # Filter out synthetic ``mode-<name>`` aliases (registered from the
            # vendored bundle modes over the surviving base provider) so this
            # asserts the real provider->model registration exactly.
            real = {k: v for k, v in app.state.served_models_registry.items() if not k.startswith("mode-")}
            assert real == {"gpt-4o": "openai"}


@pytest.mark.asyncio
async def test_serve_exits_when_all_providers_return_zero_models(base_mocks, monkeypatch: pytest.MonkeyPatch) -> None:
    """When EVERY declared/auto-enabled provider returns 0 models, startup still
    fails (served_models_registry is empty across all providers)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-value")
    base_mocks["load_host_config"].return_value = {"providers": {"anthropic": {}}}
    app = FastAPI()

    with (
        patch("amplifier_agent_http.app.list_provider_models", return_value=[]),
        pytest.raises(SystemExit) as exc_info,
    ):
        async with lifespan(app):
            pass  # pragma: no cover

    assert exc_info.value.code == 2

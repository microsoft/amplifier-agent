"""Build execution dependencies without exposing their configuration to callers."""

from __future__ import annotations

from typing import Any

from .._records import AgentError, AgentOptions
from .configuration import ResolvedConfig, resolve
from .state import EngineAgent


async def _anthropic_provider(config: ResolvedConfig, coordinator: Any) -> Any:
    from amplifier_module_provider_anthropic import AnthropicProvider

    if not config.api_key:
        raise AgentError(
            "engine_unavailable",
            "lifecycle",
            "The selected provider credential is missing.",
            "Set ANTHROPIC_API_KEY before constructing the agent.",
        )
    return AnthropicProvider(
        api_key=config.api_key,
        coordinator=coordinator,
        config={
            "default_model": config.model,
            "base_url": config.base_url or "https://api.anthropic.com",
            "max_retries": 0,
            "fallback_on_overload": False,
            "refusal_fallback_enabled": False,
            "persist_fallback_state": False,
            "rate_limit_state_path": "",
            "extra_request_params": config.extra_request_params,
        },
    )


_provider_factory = _anthropic_provider


async def create_engine(options: AgentOptions) -> EngineAgent:
    config = resolve(options)
    provider_factory = _provider_factory

    async def runtime() -> Any:
        try:
            from .adapters import AmplifierRuntime

            instance = AmplifierRuntime(config)
            await instance.initialize(provider_factory)
            return instance
        except AgentError:
            raise
        except Exception as exc:
            raise AgentError(
                "engine_unavailable",
                "lifecycle",
                "The installed execution dependencies could not be initialized.",
                "Reinstall the package with its declared dependencies and construct the agent again.",
            ) from exc

    return EngineAgent(config, await runtime(), runtime)

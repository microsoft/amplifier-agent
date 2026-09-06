"""Build execution dependencies without exposing their configuration to callers."""

from __future__ import annotations

from typing import Any

from .._records import AgentError, AgentOptions
from .configuration import resolve
from .providers import create_provider
from .state import EngineAgent

_provider_factory = create_provider


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

"""Build execution dependencies without exposing their configuration to callers."""

from __future__ import annotations

import uuid
from typing import Any

from .._records import AgentError, AgentOptions
from .configuration import resolve
from .providers import create_provider
from .state import EngineAgent

_provider_factory = create_provider


async def create_engine(options: AgentOptions) -> EngineAgent:
    config = resolve(options)
    provider_factory = _provider_factory

    async def runtime(session_id: str, parent_id: str | None, resumed: bool, capture: bool = True) -> Any:
        try:
            from .adapters import AmplifierRuntime

            instance = AmplifierRuntime(
                config, session_id=session_id, parent_id=parent_id, resumed=resumed, capture=capture
            )
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

    # Every dependency initializes once before the agent exists. The probe writes no
    # capture, so its identity never appears beside real sessions; the "probe-" prefix
    # lets provider fixtures recognize it.
    probe = await runtime(f"probe-{uuid.uuid4()}", None, False, capture=False)
    await probe.close()
    return EngineAgent(config, runtime)

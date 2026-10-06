"""Build execution dependencies without exposing their configuration to callers."""

from __future__ import annotations

from typing import Any
import uuid

from amplifier_agent_engine._engine.configuration import ResolvedConfig, resolve
from amplifier_agent_engine._engine.providers import create_provider
from amplifier_agent_engine._engine.selection import connected
from amplifier_agent_engine._engine.state import EngineAgent
from amplifier_agent_engine._records import AgentError, AgentOptions

_provider_factory = create_provider


async def create_engine(options: AgentOptions) -> EngineAgent:
    config = resolve(options)
    provider_factory = _provider_factory

    async def runtime(
        session_id: str,
        parent_id: str | None,
        resumed: bool,
        capture: bool = True,
        selected: ResolvedConfig | None = None,
    ) -> Any:
        try:
            from amplifier_agent_engine._engine.adapters import AmplifierRuntime

            instance = AmplifierRuntime(
                selected or config, session_id=session_id, parent_id=parent_id, resumed=resumed, capture=capture
            )
            # A session's own selection fails as a selection does; the agent's fails construction.
            await instance.initialize(provider_factory if selected is None else connected(provider_factory))
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

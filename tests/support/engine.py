"""Replace the engine's provider with scripted model responses for public API tests."""

from typing import Any

from amplifier_core.message_models import ChatResponse

from tests.support.scripted_provider import ScriptedFactory


def provision(monkeypatch, script=None):
    from amplifier_agent_engine._engine import assembly

    probe = ScriptedFactory(script)
    monkeypatch.setattr(assembly, "_provider_factory", probe)
    return probe


def provision_many(monkeypatch, *scripts):
    from amplifier_agent_engine._engine import assembly

    probes = []

    async def next_provider(config, coordinator):
        if coordinator.session_id.startswith("probe-"):
            # The engine's readiness probe never completes a request.
            return await ScriptedFactory(None)(config, coordinator)
        probe = ScriptedFactory(scripts[len(probes)])
        probes.append(probe)
        provider = await probe(config, coordinator)
        complete = provider.complete

        async def observed(request: Any, **kwargs: Any) -> ChatResponse:
            probe.selected_models.append(kwargs["model"])
            return await complete(request, **kwargs)

        setattr(provider, "complete", observed)
        return provider

    monkeypatch.setattr(assembly, "_provider_factory", next_provider)
    return probes

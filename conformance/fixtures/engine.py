"""Select an engine and record scripted model work for public API tests."""

import os

from conformance.fixtures.scripted_provider import ScriptedFactory


def replacement_factory(monkeypatch, script=None):
    from amplifier_agent_engine._engine import assembly

    from conformance.fixtures import replacement

    probe = replacement.Probe(script)
    monkeypatch.setattr(assembly, "create_engine", replacement.create_engine)
    monkeypatch.setattr(replacement, "probe_factory", lambda: probe)
    return probe


def provision(monkeypatch, script=None):
    if os.environ.get("CONFORMANCE_ENGINE") == "replacement":
        return replacement_factory(monkeypatch, script)

    from amplifier_agent_engine._engine import assembly

    probe = ScriptedFactory(script)
    monkeypatch.setattr(assembly, "_provider_factory", probe)
    return probe


def provision_many(monkeypatch, *scripts):
    from amplifier_agent_engine._engine import assembly

    probes = []
    if os.environ.get("CONFORMANCE_ENGINE") == "replacement":
        from conformance.fixtures import replacement

        def next_probe():
            probe = replacement.Probe(scripts[len(probes)])
            probes.append(probe)
            return probe

        monkeypatch.setattr(assembly, "create_engine", replacement.create_engine)
        monkeypatch.setattr(replacement, "probe_factory", next_probe)
    else:
        async def next_provider(config, coordinator):
            probe = ScriptedFactory(scripts[len(probes)])
            probe.selected_models = []
            probes.append(probe)
            provider = await probe(config, coordinator)
            complete = provider.complete

            async def observed(request, **kwargs):
                probe.selected_models.append(kwargs["model"])
                return await complete(request, **kwargs)

            provider.complete = observed
            return provider

        monkeypatch.setattr(assembly, "_provider_factory", next_provider)
    return probes


def install(script=None):
    """Install the selected fixture in a disposable subprocess."""
    from amplifier_agent_engine._engine import assembly

    if os.environ.get("CONFORMANCE_ENGINE") == "replacement":
        from conformance.fixtures import replacement

        probe = replacement.Probe(script)
        assembly.create_engine = replacement.create_engine
        replacement.probe_factory = lambda: probe
    else:
        probe = ScriptedFactory(script)
        assembly._provider_factory = probe
    return probe

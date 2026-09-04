import pytest

from conformance.drivers.python import run
from conformance.fixtures.scripted_provider import SCENARIOS, ScriptedFactory
from conformance.observations import discriminate, verify


@pytest.mark.parametrize("case", SCENARIOS, ids=lambda case: case["id"])
async def test_public_python_scenarios(case, monkeypatch):
    from amplifier_agent_engine._engine import assembly

    probe = ScriptedFactory(case["provider"])
    monkeypatch.setattr(assembly, "_provider_factory", probe)
    observed = await run(case, probe)
    discriminate(case, observed)
    for key in ("state", "text"):
        if key in case["expected"]:
            with pytest.raises(AssertionError):
                verify(case, {**observed, key: "wrong"})

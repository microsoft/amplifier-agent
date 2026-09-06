import pytest

from conformance.drivers.python import run
from conformance.fixtures.engine import provision
from conformance.fixtures.scripted_provider import SCENARIOS
from conformance.observations import discriminate, verify


@pytest.mark.parametrize("case", SCENARIOS, ids=lambda case: case["id"])
async def test_public_python_scenarios(case, monkeypatch):

    probe = provision(monkeypatch, case["provider"])
    observed = await run(case, probe)
    discriminate(case, observed)
    for key in ("state", "text"):
        if key in case["expected"]:
            with pytest.raises(AssertionError):
                verify(case, {**observed, key: "wrong"})

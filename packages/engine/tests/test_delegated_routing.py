from decimal import Decimal

import pytest
from amplifier_agent_engine._engine import routing
from amplifier_agent_engine._records import AgentError


@pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini", "azure-openai"])
async def test_general_role_preserves_an_exact_unknown_model(provider):
    assert await routing.delegated_model(provider, "account-deployment") == "account-deployment"


async def test_economy_role_filters_before_upstream_selection(monkeypatch):
    monkeypatch.setattr(routing, "_rates", lambda provider: {
        "unknown-cheap": {"input": Decimal("0")},
        "gpt-5": {"input": Decimal("1")},
    })
    # The routing candidate catalog cannot grant authority absent from select().
    assert await routing.delegated_model("openai", "gpt-5", role="economy") == "gpt-5"


async def test_nested_economy_role_stays_below_current_ceiling():
    assert await routing.delegated_model(
        "anthropic", "claude-opus-5", role="economy",
    ) == "claude-sonnet-5"
    assert await routing.delegated_model(
        "anthropic", "claude-sonnet-5", role="economy",
    ) == "claude-sonnet-5"
    with pytest.raises(AgentError) as caught:
        await routing.delegated_model("anthropic", "claude-sonnet-5", model="claude-opus-5")
    assert caught.value.code == "selector_rejected"


async def test_conflicting_and_unknown_role_fail_before_work():
    with pytest.raises(AgentError) as conflict:
        await routing.delegated_model("openai", "gpt-5", model="gpt-5", role="general")
    assert conflict.value.code == "invalid_input"
    with pytest.raises(AgentError) as unknown:
        await routing.delegated_model("openai", "gpt-5", role="other")
    assert unknown.value.code == "selector_rejected"

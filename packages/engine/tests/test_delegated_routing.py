from decimal import Decimal

from amplifier_agent_engine._engine import provider_policy, routing
from amplifier_agent_engine._records import AgentError
import pytest


@pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini", "azure-openai"])
async def test_general_role_preserves_an_exact_unknown_model(provider):
    assert await routing.delegated_model(provider, "account-deployment") == "account-deployment"


async def test_economy_role_filters_before_upstream_selection(monkeypatch):
    monkeypatch.setattr(
        routing,
        "_rates",
        lambda provider: {
            "unknown-cheap": {"input": Decimal("0")},
            "gpt-5": {"input": Decimal("1")},
        },
    )
    # The routing candidate catalog cannot grant authority absent from select().
    assert await routing.delegated_model("openai", "gpt-5", role="economy") == "gpt-5"


@pytest.mark.parametrize(
    ("ceiling", "legacy_economy"),
    [
        ("claude-opus-5-5", "claude-sonnet-5-5"),
        ("claude-opus-5", "claude-sonnet-5"),
        ("claude-sonnet-5-5", "claude-sonnet-5-5"),
        ("claude-sonnet-5", "claude-sonnet-5"),
    ],
)
@pytest.mark.parametrize("corrected_catalog", [False, True], ids=["legacy", "corrected"])
async def test_nested_economy_role_stays_below_current_ceiling(monkeypatch, ceiling, legacy_economy, corrected_catalog):
    # Selection follows complete price dimensions, not the generation in a name.
    # The corrected catalog adds 5.5 with equal input/output/write and lower read.
    rates = {
        model: {
            dimension: Decimal(value)
            for dimension, value in zip(("input", "output", "read", "write"), prices, strict=True)
        }
        for model, prices in {
            "claude-sonnet-5": ("2", "10", ".20", "2.50"),
            "claude-opus-5": ("5", "25", ".50", "6.25"),
        }.items()
    }
    if corrected_catalog:
        rates["claude-sonnet-5-5"] = {**rates["claude-sonnet-5"], "read": Decimal(".10")}
    monkeypatch.setattr(routing, "_rates", lambda provider: rates)
    monkeypatch.setattr(provider_policy, "_rates", lambda provider: rates)
    economy = "claude-sonnet-5-5" if corrected_catalog else legacy_economy
    assert await routing.delegated_model("anthropic", ceiling, role="economy") == economy


@pytest.mark.parametrize(
    ("ceiling", "model"), [("claude-opus-5-5", "claude-sonnet-5-5"), ("claude-opus-5", "claude-sonnet-5")]
)
async def test_each_sonnet_generation_runs_below_its_opus(ceiling, model):
    assert await routing.delegated_model("anthropic", ceiling, model=model) == model


@pytest.mark.parametrize(
    ("ceiling", "model"),
    [
        ("claude-sonnet-5-5", "claude-opus-5-5"),
        ("claude-sonnet-5", "claude-opus-5"),
        # Without catalog evidence only the established same-generation pair is allowed.
        ("claude-opus-5", "claude-sonnet-5-5"),
    ],
)
async def test_models_without_an_established_ordering_are_rejected(monkeypatch, ceiling, model):
    monkeypatch.setattr(provider_policy, "_rates", lambda provider: {})
    with pytest.raises(AgentError) as caught:
        await routing.delegated_model("anthropic", ceiling, model=model)
    assert caught.value.code == "selector_rejected"


async def test_conflicting_and_unknown_role_fail_before_work():
    with pytest.raises(AgentError) as conflict:
        await routing.delegated_model("openai", "gpt-5", model="gpt-5", role="general")
    assert conflict.value.code == "invalid_input"
    with pytest.raises(AgentError) as unknown:
        await routing.delegated_model("openai", "gpt-5", role="other")
    assert unknown.value.code == "selector_rejected"

from decimal import Decimal
from types import SimpleNamespace

import pytest
from amplifier_agent_engine._engine.configuration import resolve, select
from amplifier_agent_engine._engine.provider_policy import response_usage, settings
from amplifier_agent_engine._engine.providers import create_provider
from amplifier_agent_engine._records import AgentError, AgentOptions


def test_settings_snapshot_and_layer_precedence(monkeypatch, tmp_path):
    config = tmp_path / "config.json"
    config.write_text(
        '{"provider":"openai","model":"file-model","workspace":"billing",'
        '"extra_request_params":{"openai":{"store":"false","metadata":{"team":"one"}}}}'
    )
    monkeypatch.setenv("AMPLIFIER_AGENT_CONFIG", str(config))
    monkeypatch.setenv("AMPLIFIER_AGENT_MODEL", "environment-model")
    monkeypatch.setenv("OPENAI_API_KEY", "first-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://first.example/v1")
    options = AgentOptions(model="code-model")
    resolved = resolve(options)
    options.model = "changed-model"
    monkeypatch.setenv("OPENAI_API_KEY", "second-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://second.example/v1")
    config.write_text("{}")
    assert (resolved.provider, resolved.model, resolved.workspace) == (
        "openai",
        "code-model",
        "billing",
    )
    assert resolved.api_key == "first-key"
    assert resolved.base_url == "https://first.example/v1"
    assert resolved.extra_request_params == {"store": False, "metadata": {"team": "one"}}


def test_misspelled_host_environment_names_remedy(monkeypatch):
    monkeypatch.setenv("AMPLIFIER_AGENT_MODLE", "model")
    with pytest.raises(AgentError) as caught:
        resolve(AgentOptions())
    assert caught.value.details == {"field": "AMPLIFIER_AGENT_MODLE"}
    assert "AMPLIFIER_AGENT_MODEL" in caught.value.remedy


@pytest.mark.parametrize("value", ["true", "yes", "False", "", 0, 1, [], {}])
def test_ambiguous_boolean_is_refused(value):
    with pytest.raises(AgentError, match="boolean"):
        settings("openai", {"store": value})


@pytest.mark.parametrize(
    "field",
    [
        "input",
        "instructions",
        "conversation",
        "previous_response_id",
        "tools",
        "toolConfig",
        "systemInstruction",
        "cachedContent",
        "extra_body",
    ],
)
def test_request_overrides_cannot_replace_semantics(field):
    with pytest.raises(AgentError) as caught:
        settings("openai", {field: {"id": "remote"}})
    assert field in caught.value.message


def test_gemini_unknown_setting_is_not_silently_ignored():
    with pytest.raises(AgentError, match="unsupported request setting"):
        settings("gemini", {"temperatur": 0.5})


def test_unknown_ceiling_order_is_refused_without_restricting_identical_selection():
    assert select("custom-model", "custom-model", "vllm") == "custom-model"
    with pytest.raises(AgentError) as caught:
        select("other-model", "custom-model", "vllm")
    assert caught.value.code == "selector_rejected"
    assert select("gpt-5.4", "gpt-5.5", "openai") == "gpt-5.4"
    with pytest.raises(AgentError):
        select("gpt-5.5", "gpt-5.4", "openai")


def test_usage_preserves_unknown_large_counters_and_decimal():
    response = SimpleNamespace(
        usage=SimpleNamespace(
            input_tokens=2**60 + 7,
            output_tokens=None,
            cache_read_tokens=0,
            cache_write_tokens=None,
            cost_usd=Decimal("0.00000000000000000017"),
        )
    )
    result = response_usage(response, "openai", "gpt-5")
    assert result.tokens_in == 2**60 + 7
    assert result.tokens_out is None
    assert result.cache_read_tokens == 0
    assert result.cache_write_tokens is None
    assert result.cost == {"USD": Decimal("0.00000000000000000017")}
    response.usage.cost_usd = 0.1
    with pytest.raises(AgentError, match="inexact cost"):
        response_usage(response, "openai", "gpt-5")


@pytest.mark.parametrize("fault", ["drop", "duplicate", "reshape"])
def test_native_input_conversion_refuses_loss_or_marker_leak(fault):
    from amplifier_agent_engine._engine.provider_inputs import response_roles

    def convert(messages):
        marker = messages[0]["content"]
        converted = {"role": "user", "content": [{"type": "input_text", "text": marker}]}
        if fault == "drop":
            return []
        if fault == "duplicate":
            return [converted, converted.copy()]
        return [{"role": "user", "content": [{"type": "input_text", "text": "prefix " + marker}]}]

    with pytest.raises(AgentError) as caught:
        response_roles(
            [{"role": "user", "content": [{"type": "text", "text": "supplied"}]}], convert
        )
    assert caught.value.code == "provider_failed"
    assert caught.value.remedy


async def _constructed(monkeypatch, provider, model, base_url_env, base_url):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    if base_url is None:
        monkeypatch.delenv(base_url_env, raising=False)
    else:
        monkeypatch.setenv(base_url_env, base_url)
    return await create_provider(resolve(AgentOptions(provider=provider, model=model)), None)


@pytest.mark.parametrize(
    ("base_url", "counted"),
    [(None, True), ("https://api.openai.com/v1/", True), ("https://proxy.example/v1", False)],
)
async def test_openai_native_input_count_follows_client_route(monkeypatch, base_url, counted):
    provider = await _constructed(monkeypatch, "openai", "gpt-6-sol", "OPENAI_BASE_URL", base_url)
    assert provider._provider_count_available() is counted


@pytest.mark.parametrize(
    ("base_url", "reason"),
    [(None, None), ("https://proxy.example", "unsupported_route")],
)
async def test_gemini_native_input_count_follows_client_route(monkeypatch, base_url, reason):
    provider = await _constructed(
        monkeypatch, "gemini", "gemini-2.5-pro", "GOOGLE_GEMINI_BASE_URL", base_url
    )
    assert provider._native_counting_unavailable_reason("gemini-2.5-pro") == reason

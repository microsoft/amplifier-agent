"""Provider selection, request settings, and exact accounting at the owned boundary."""

from __future__ import annotations

import copy
from decimal import Decimal
from typing import Any

from .._records import AgentError, UsageEntry

PROVIDERS = frozenset(
    {
        "anthropic",
        "openai",
        "azure-openai",
        "ollama",
        "github-copilot",
        "openai-chatgpt",
        "chat-completions",
        "gemini",
        "vllm",
    }
)


def rejected(model: Any, ceiling: str | None = None) -> AgentError:
    return AgentError(
        "selector_rejected",
        "selection",
        "The requested model selection cannot be honored.",
        f"Select {ceiling} or a model with a verified lower price."
        if ceiling
        else "Select a model available through the configured provider and account.",
        details={"model": model, **({"ceiling": ceiling} if ceiling else {})},
    )


def select(model: str | None, ceiling: str, provider: str = "anthropic") -> str:
    if model is None or model == ceiling:
        return ceiling
    if not isinstance(model, str) or not model:
        raise rejected(model, ceiling)
    # These two Anthropic selections have an established ordering in the engine.
    # Other models require provider rate evidence, never name-based inference.
    if provider == "anthropic" and (ceiling, model) == ("claude-opus-5", "claude-sonnet-5"):
        return model
    rates = _rates(provider)
    lower, upper = rates.get(model), rates.get(ceiling)
    if lower and upper and lower.keys() == upper.keys():
        if all(lower[key] <= upper[key] for key in lower):
            return model
    raise rejected(model, ceiling)


def _rates(provider: str) -> dict[str, dict[str, Decimal]]:
    """Use exact, complete rate dimensions from the installed provider catalog."""
    import importlib

    if provider not in {"anthropic", "openai", "gemini"}:
        return {}
    module = importlib.import_module(f"amplifier_module_provider_{provider}._cost")
    result = {}
    tiered = getattr(module, "_LONG_RATES", {})
    for model, rates in getattr(module, "_RATES", {}).items():
        if model not in tiered and rates and all(isinstance(v, Decimal) for v in rates.values()):
            result[model] = rates
    return result


def _invalid(field: str, message: str, remedy: str) -> AgentError:
    return AgentError(
        "invalid_input", "input", f"{field}: {message}", remedy, details={"field": field}
    )


def settings(provider: str, supplied: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(supplied)
    forbidden = {
        "model",
        "messages",
        "input",
        "contents",
        "system",
        "instructions",
        "system_instruction",
        "systemInstruction",
        "tools",
        "tool_choice",
        "tool_config",
        "toolConfig",
        "stream",
        "stream_options",
        "previous_response_id",
        "conversation",
        "session",
        "session_id",
        "cached_content",
        "cachedContent",
        "http_options",
        "extra_body",
        "extra_headers",
        "extra_query",
        "api_key",
        "base_url",
        "endpoint",
    }
    protected = {key.replace("_", "").lower() for key in forbidden}
    for key in value:
        path = f"extra_request_params.{provider}.{key}"
        if key.replace("_", "").lower() in protected:
            raise _invalid(
                path,
                "this setting changes contracted conversation behavior.",
                f"Remove {key} from extra_request_params.",
            )
        if key in {"store", "background", "parallel_tool_calls"}:
            item = value[key]
            if isinstance(item, bool):
                continue
            if isinstance(item, str) and item in {"false", "0", "no"}:
                value[key] = False
            else:
                raise _invalid(
                    path,
                    "expected a boolean.",
                    "Use a JSON boolean, or false, 0, or no as a string.",
                )
    if value.get("background") and value.get("store") is not True:
        raise _invalid(
            f"extra_request_params.{provider}.background",
            "background work requires explicit retention opt-in.",
            "Remove background or explicitly set store to true in the same settings.",
        )
    if "truncation" in value and value["truncation"] != "disabled":
        raise _invalid(
            f"extra_request_params.{provider}.truncation",
            "request truncation would discard authoritative input.",
            "Remove truncation or set it to disabled.",
        )
    if provider == "openai-chatgpt":
        unsupported = {
            "max_output_tokens",
            "temperature",
            "truncation",
            "parallel_tool_calls",
            "include",
        }
        for key in value.keys() & unsupported:
            raise _invalid(
                f"extra_request_params.{provider}.{key}",
                "the ChatGPT backend does not accept this setting.",
                f"Remove {key}.",
            )
        if value.get("store") is True:
            raise _invalid(
                f"extra_request_params.{provider}.store",
                "the ChatGPT backend requires store to be false.",
                "Remove store or set it to false.",
            )
    if provider == "gemini":
        from google.genai.types import GenerateContentConfig

        for key in value.keys() - GenerateContentConfig.model_fields.keys():
            raise _invalid(
                f"extra_request_params.gemini.{key}",
                "unsupported request setting.",
                "Use a supported Gemini GenerateContentConfig field.",
            )
    if provider == "github-copilot" and value:
        key = next(iter(value))
        raise _invalid(
            f"extra_request_params.github-copilot.{key}",
            "the Copilot SDK cannot honor arbitrary request settings.",
            "Remove this request setting.",
        )
    return value


def annotate_response(response: Any, native: Any, provider: str, model: str = "") -> Any:
    """Retain native measurement presence before upstream default values obscure it."""
    actual = getattr(native, "model", None) or getattr(native, "model_version", None) or model
    native_usage = getattr(native, "usage", None)
    if provider == "gemini":
        native_usage = getattr(native, "usage_metadata", None)
    present: dict[str, bool] = {}
    if provider == "gemini":
        present = {
            "input_tokens": getattr(native_usage, "prompt_token_count", None) is not None,
            "output_tokens": getattr(native_usage, "candidates_token_count", None) is not None,
            "cache_read_tokens": getattr(native_usage, "cached_content_token_count", None)
            is not None,
            "cache_write_tokens": False,
        }
    elif provider == "anthropic":
        present = {
            "input_tokens": getattr(native_usage, "input_tokens", None) is not None,
            "output_tokens": getattr(native_usage, "output_tokens", None) is not None,
            "cache_read_tokens": getattr(native_usage, "cache_read_input_tokens", None) is not None,
            "cache_write_tokens": getattr(native_usage, "cache_creation_input_tokens", None)
            is not None,
        }
    else:
        present = {
            "input_tokens": getattr(native_usage, "input_tokens", None) is not None,
            "output_tokens": getattr(native_usage, "output_tokens", None) is not None,
        }
    usage = response.usage
    if usage is not None:
        updates = {key: None for key, known in present.items() if not known}
        if not present.get("input_tokens") or not present.get("output_tokens"):
            updates["cost_usd"] = None
        usage = usage.model_copy(update=updates)
    metadata = dict(response.metadata or {})
    if provider == "gemini":
        # Replay signatures already live on content blocks; SDK debug objects are not transcript data.
        metadata.pop("raw_response", None)
    return response.model_copy(
        update={
            "usage": usage if native_usage is not None else None,
            "agent_actual_model": actual or None,
            "metadata": metadata,
        }
    )


def response_selection(response: Any, requested_model: str) -> str:
    actual = getattr(response, "agent_actual_model", None) or requested_model
    aliases = {"gpt-5.6": "gpt-5.6-sol"}
    if actual != requested_model and aliases.get(requested_model) != actual:
        raise rejected(actual, requested_model)
    return actual


def response_usage(response: Any, provider: str, requested_model: str) -> UsageEntry | None:
    usage = response.usage
    if usage is None:
        return None
    model = response_selection(response, requested_model)
    counters = {}
    for name, source in (
        ("tokens_in", "input_tokens"),
        ("tokens_out", "output_tokens"),
        ("cache_read_tokens", "cache_read_tokens"),
        ("cache_write_tokens", "cache_write_tokens"),
    ):
        value = getattr(usage, source, None)
        if value is not None and (type(value) is not int or value < 0):
            raise AgentError(
                "provider_failed",
                "provider",
                "The provider reported invalid usage.",
                "Use a provider that reports nonnegative exact integer counters.",
                details={"provider": provider, "field": name},
            )
        counters[name] = value
    cost = getattr(usage, "cost_usd", None)
    if cost is not None:
        if not isinstance(cost, (Decimal, str)):
            raise AgentError(
                "provider_failed",
                "provider",
                "The provider reported inexact cost.",
                "Use a provider that supplies exact decimal cost data.",
            )
        cost = Decimal(cost)
        if not cost.is_finite() or cost < 0:
            raise AgentError(
                "provider_failed",
                "provider",
                "The provider reported invalid cost.",
                "Use a provider that supplies nonnegative finite decimal cost data.",
            )
    return UsageEntry(provider, model, **counters, cost={"USD": cost} if cost is not None else None)

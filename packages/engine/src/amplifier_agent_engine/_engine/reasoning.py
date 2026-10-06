"""The reasoning effort: vocabulary, default, and per-model support.

agent-interface.v1 section 5. Support comes from each provider module's own model
knowledge; the engine never infers it from names.
"""

from __future__ import annotations

from typing import Any

from amplifier_agent_engine._records import AgentError

EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
DEFAULT = "medium"
# Support where a provider module cannot say which models take an effort.
UNKNOWABLE = None
# A vllm endpoint serves arbitrary self-hosted models, so no table speaks for it.
_RESPONSES = frozenset({"openai", "azure-openai", "openai-chatgpt"})


def invalid(field: str, value: Any, source: str | None = None) -> AgentError:
    origin = f" from {source}" if source else ""
    return AgentError(
        "invalid_input",
        "input",
        f"{field}: {value!r}{origin} is not a registered reasoning effort.",
        f"Set reasoning_effort to one of: {', '.join(EFFORTS)}, or omit it.",
        details={"field": field},
    )


def parse(value: Any, field: str, source: str | None = None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value not in EFFORTS:
        raise invalid(field, value, source)
    return value


def support(provider: str, model: str, instance: Any = None) -> tuple[str, ...] | None:
    """The labels ``model`` takes: empty when it takes none, None when unknowable."""
    if provider == "anthropic":
        from amplifier_module_provider_anthropic import AnthropicProvider

        capabilities = AnthropicProvider._get_capabilities(model)
        return tuple(capabilities.supported_efforts) if capabilities.supports_thinking else ()
    if provider in _RESPONSES:
        import amplifier_module_provider_openai as openai
        from amplifier_module_provider_openai._capabilities import get_capabilities

        if not get_capabilities(model).supports_reasoning:
            return ()
        allowed: Any = EFFORTS
        if model.startswith("gpt-5.5-pro"):
            allowed = openai._GPT_5_5_PRO_ALLOWED_EFFORTS
        elif model in openai.GPT_6_SOL_LUNA_MODELS:
            allowed = openai._GPT_6_SOL_LUNA_ALLOWED_EFFORTS
        elif model in openai.GPT_6_MODELS:
            allowed = openai._GPT_6_ASTRA_ALLOWED_EFFORTS
        return tuple(effort for effort in EFFORTS if effort in allowed)
    if provider == "gemini":
        import amplifier_module_provider_gemini as gemini

        levels = gemini._supported_thinking_levels(model)
        if levels is None:
            return ()
        # A label the module would map to another level would be substituted.
        return tuple(effort for effort in EFFORTS if effort in levels and gemini._EFFORT_TO_LEVEL.get(effort) == effort)
    if provider == "github-copilot":
        lookup = getattr(instance, "_lookup_copilot_model_info", None)
        info = lookup(model) if callable(lookup) else None
        if info is None:
            return UNKNOWABLE
        if not info.supports_reasoning_effort:
            return ()
        offered = tuple(info.supported_reasoning_efforts)
        return tuple(effort for effort in EFFORTS if effort in offered) if offered else UNKNOWABLE
    return UNKNOWABLE


def unsupported(provider: str, model: str, value: str, labels: tuple[str, ...]) -> AgentError:
    return AgentError(
        "selector_rejected",
        "selection",
        f"Model {model!r} does not take reasoning effort {value!r}.",
        f"Name one of: {', '.join(labels)}, or select a model that takes {value!r}.",
        details={"provider": provider, "model": model, "reasoning_effort": value},
    )


def check(provider: str, model: str, named: str | None, instance: Any = None) -> None:
    """Refuse a named value the selected model is known not to take."""
    if named is None:
        return
    labels = support(provider, model, instance)
    if labels and named not in labels:
        raise unsupported(provider, model, named, labels)


def sent(provider: str, model: str, named: str | None, instance: Any = None) -> str | None:
    """The value primary work sends: named or the default, where the model takes it."""
    labels = support(provider, model, instance)
    if labels is UNKNOWABLE:
        return named
    if not labels:
        return None
    if named is not None:
        if named not in labels:
            raise unsupported(provider, model, named, labels)
        return named
    return DEFAULT if DEFAULT in labels else None


def delegated(provider: str, model: str, ceiling: str, parent: str | None, instance: Any = None) -> str | None:
    """The most a delegated model takes without exceeding the ceiling."""
    labels = support(provider, model, instance)
    if labels is UNKNOWABLE:
        return parent
    allowed = [effort for effort in labels if EFFORTS.index(effort) <= EFFORTS.index(ceiling)]
    return allowed[-1] if allowed else None

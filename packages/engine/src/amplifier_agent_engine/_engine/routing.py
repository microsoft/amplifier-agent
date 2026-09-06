"""Resolve delegated roles after filtering candidates through the owned ceiling."""

from __future__ import annotations

from typing import Any

from .._records import AgentError
from .provider_policy import _rates, select


async def delegated_model(
    provider: str, ceiling: str, *, model: str | None = None, role: str | None = None
) -> str:
    if model is not None:
        if role is not None:
            raise AgentError(
                "invalid_input", "input", "Delegation accepts either model or model_role.",
                "Choose a named model or a role, not both.",
            )
        return select(model, ceiling, provider)
    role = role or "general"
    if role not in {"general", "economy"}:
        raise AgentError(
            "selector_rejected", "selection", "The delegated model role is unknown.",
            "Use general to inherit the current model or economy for a verified lower price.",
        )

    from amplifier_module_hooks_routing.resolver import resolve_model_role

    candidates = [ceiling]
    if role == "economy":
        rates = _rates(provider)
        ordered = sorted(rates, key=lambda name: (sum(rates[name].values()), name))
        if provider == "anthropic":
            ordered = ["claude-sonnet-5", "claude-opus-5"]
        candidates = []
        for candidate in ordered:
            try:
                select(candidate, ceiling, provider)
            except AgentError:
                continue
            candidates.append(candidate)
        if ceiling not in candidates:
            candidates.append(ceiling)
    matrix: dict[str, Any] = {
        role: {"candidates": [{"provider": provider, "model": name} for name in candidates]}
    }
    resolved = await resolve_model_role([role], matrix, {provider: object()})
    if len(resolved) != 1 or resolved[0]["provider"] != provider:
        raise AgentError(
            "selector_rejected", "selection", "Delegation could not resolve an allowed model.",
            "Use an explicit model within the current agent's ceiling.",
        )
    return select(resolved[0]["model"], ceiling, provider)

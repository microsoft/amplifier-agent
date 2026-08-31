"""Which provider family a model under test belongs to, and its constants.

Every arm has to answer the same three questions for whatever `--model` it was
handed: which env vars carry the credential and endpoint, which provider module
to configure, and what opencode calls that provider. This module is the single
place those answers live, so the adapters branch on one derived value instead
of each re-deriving it from the model string.

Deliberately not a plugin framework: a third family is a third entry in
`_FAMILIES` plus whatever genuinely differs at the two or three call sites.

The model -> family rule is a prefix test (`gpt-` is OpenAI, everything else is
Anthropic) rather than a lookup table, so a newly released model id works
without editing this file. The one table that DOES need a per-model entry is
`metrics.MODEL_RATES_PER_M`, and only for the opencode arm's cost recompute.
"""

from __future__ import annotations

from dataclasses import dataclass

ANTHROPIC = "anthropic"
OPENAI = "openai"

#: Benchmark-wide reasoning-effort pin for the OpenAI family. Changing this one
#: value re-pins all three arms (amplifier-agent, amplifier-foundation,
#: opencode-vanilla) together; it is deliberately the only place the literal
#: appears, so the arms cannot drift apart into an invalid comparison.
#:
#: Accepted by provider-openai (validated at mount) as one of: none, minimal,
#: low, medium, high, xhigh, max. opencode spells the same knob
#: `provider.<id>.models.<model>.options.reasoningEffort` and otherwise applies
#: its own built-in default of "medium" to any model id containing "gpt-5";
#: this pin overrides exactly that.
#:
#: ANTHROPIC IS NOT PINNED. That family controls reasoning through a thinking
#: token budget, a different mechanism with a different unit, and pinning it is
#: out of scope -- so nothing on the Anthropic path reads this constant.
REASONING_EFFORT = "high"


@dataclass(frozen=True)
class ProviderFamily:
    """Everything the three in-scope adapters need for one provider family."""

    #: Family id, ``anthropic`` or ``openai``. Adapters branch on this only
    #: where the SHAPE of the config differs, not merely a value.
    name: str

    #: Env vars the launch profile's `passthrough.services` forwards into the
    #: container. Nothing here reads their VALUES -- only the names, so the
    #: secret stays out of our argv and logs.
    api_key_env: str
    base_url_env: str

    #: Fallback base URL, used only where the existing config already had one.
    #: None means "no default, the env var is required" -- the OpenAI endpoint
    #: under test is not the public default, so silently falling back to it
    #: would benchmark a different backend.
    default_base_url: str | None

    #: amplifier-agent host-config `provider.module` short name. Valid values
    #: come from amplifier-agent/src/amplifier_agent_cli/provider_sources.py.
    agent_module: str

    #: amplifier-foundation settings.yaml provider module + its source, same
    #: pairing as provider_sources.py's PROVIDER_SOURCES table.
    foundation_module: str
    foundation_source: str

    #: opencode provider id. Also the `<id>/<model>` prefix opencode wants for
    #: `--model`, `model`, and `small_model`.
    opencode_provider_id: str

    #: ai-sdk package opencode loads for this provider.
    opencode_npm: str

    #: True when the passthrough base URL is an SDK-style host root that
    #: opencode's ai-sdk provider needs a trailing `/v1` appended to. The
    #: Anthropic SDK appends /v1 itself so its passthrough value lacks it;
    #: OPENAI_BASE_URL already carries it and must not get a second one.
    opencode_base_url_needs_v1: bool


_FAMILIES: dict[str, ProviderFamily] = {
    ANTHROPIC: ProviderFamily(
        name=ANTHROPIC,
        api_key_env="ANTHROPIC_API_KEY",
        base_url_env="ANTHROPIC_BASE_URL",
        default_base_url="https://api.anthropic.com",
        agent_module="anthropic",
        foundation_module="provider-anthropic",
        foundation_source=(
            "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main"
        ),
        opencode_provider_id="anthropic",
        opencode_npm="@ai-sdk/anthropic",
        opencode_base_url_needs_v1=True,
    ),
    OPENAI: ProviderFamily(
        name=OPENAI,
        api_key_env="OPENAI_API_KEY",
        base_url_env="OPENAI_BASE_URL",
        default_base_url=None,
        agent_module="openai",
        foundation_module="provider-openai",
        foundation_source=(
            "git+https://github.com/microsoft/amplifier-module-provider-openai@main"
        ),
        opencode_provider_id="openai",
        opencode_npm="@ai-sdk/openai",
        opencode_base_url_needs_v1=False,
    ),
}


def provider_family(model: str) -> ProviderFamily:
    """Map a model id to its provider family.

    ``gpt-*`` is OpenAI; everything else is Anthropic, which keeps every
    pre-existing model id (claude-sonnet-5, claude-opus-5, ...) on exactly the
    path it was on before this function existed.

    Examples:
        >>> provider_family("claude-sonnet-5").name
        'anthropic'
        >>> provider_family("gpt-5.6-terra").name
        'openai'
    """
    return _FAMILIES[OPENAI] if model.startswith("gpt-") else _FAMILIES[ANTHROPIC]


__all__ = [
    "ANTHROPIC",
    "OPENAI",
    "REASONING_EFFORT",
    "ProviderFamily",
    "provider_family",
]

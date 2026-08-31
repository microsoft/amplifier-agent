"""Which provider family a model id belongs to, and the per-family constants.

The harness benchmarks ONE model at a time across every arm, but that model may
be served by either Anthropic or OpenAI. Every arm needs the same four facts --
which credential env vars to forward, which API host to open in the egress
allowlist, which provider module to configure, and which opencode provider id to
write -- so they live here once instead of being re-derived (differently) in
four adapters.

Selection is derived from the bare model id at RUNTIME. pier splits `--model` on
the first `/` and hands the adapter only the right-hand side, so the
`anthropic/` or `openai/` prefix a user types never reaches this module. The
model id itself is the only signal available, which is why the test is on the
`gpt-` prefix rather than on a provider label.
"""

from __future__ import annotations

OPENAI = "openai"
ANTHROPIC = "anthropic"


def provider_family(model: str) -> str:
    """Return ``"openai"`` or ``"anthropic"`` for a bare model id.

    Anthropic is the default because it is the harness's historical single
    provider: an unrecognised id keeps every arm on exactly the path it took
    before OpenAI support existed, rather than failing closed on a new one.

        >>> provider_family("claude-sonnet-5")
        'anthropic'
        >>> provider_family("gpt-5.6-terra")
        'openai'
    """
    return OPENAI if (model or "").startswith("gpt-") else ANTHROPIC


#: Credential env var forwarded into the container, per family.
API_KEY_VAR = {
    ANTHROPIC: "ANTHROPIC_API_KEY",
    OPENAI: "OPENAI_API_KEY",
}

#: Endpoint override env var read on the HOST, per family.
BASE_URL_VAR = {
    ANTHROPIC: "ANTHROPIC_BASE_URL",
    OPENAI: "OPENAI_BASE_URL",
}

#: Fallback endpoint when the host sets no override. Note the shapes differ:
#: the Anthropic SDK wants the host root and appends `/v1` itself, while the
#: OpenAI SDK wants the API root INCLUDING `/v1`.
DEFAULT_BASE_URL = {
    ANTHROPIC: "https://api.anthropic.com",
    OPENAI: "https://api.openai.com/v1",
}

#: Host always opened in the egress allowlist for the family, on top of the
#: parsed hostname of any BASE_URL override. deep-swe tasks run with
#: `network_mode = "no-network"`, so an absent host here is not a slow request
#: -- it is a proxy-blocked one, reported as a network error rather than a
#: model error.
DEFAULT_API_HOST = {
    ANTHROPIC: "api.anthropic.com",
    OPENAI: "api.openai.com",
}

#: Reasoning effort pinned for the OPENAI family, benchmark-wide. This single
#: value is the pin for all three arms -- amplifier-agent, foundation, and
#: opencode-vanilla each spell it differently on the wire
#: (`reasoning_effort` in host-config.json, `reasoning_effort` in settings.yaml,
#: `options.reasoningEffort` in opencode.json), but all three read it from here,
#: so re-pinning the benchmark is a one-line change.
#:
#: Stated EXPLICITLY rather than left to defaults: opencode silently applies
#: `reasoningEffort: "medium"` to any model id containing "gpt-5", so an
#: unpinned run would benchmark the arms against different effort levels.
#:
#: OPENAI ONLY. The Anthropic family uses a thinking-token BUDGET instead, which
#: this constant does not express and which is deliberately not pinned here --
#: adding it to an Anthropic branch would be a different mechanism wearing the
#: same name.
REASONING_EFFORT = "high"

#: Provider module name used in `amplifier-agent`'s host-config.json. These are
#: the friendly names its config merger accepts (`_PROVIDER_NAME_TO_MODULE_KEY`).
AGENT_PROVIDER_MODULE = {
    ANTHROPIC: "anthropic",
    OPENAI: "openai",
}

#: Provider module + git source written into foundation's settings.yaml.
FOUNDATION_PROVIDER_MODULE = {
    ANTHROPIC: "provider-anthropic",
    OPENAI: "provider-openai",
}
FOUNDATION_PROVIDER_SOURCE = {
    ANTHROPIC: "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",
    OPENAI: "git+https://github.com/microsoft/amplifier-module-provider-openai@main",
}

#: opencode provider id (also the `<provider>/<model>` prefix) and the ai-sdk
#: npm package that backs it.
OPENCODE_PROVIDER_ID = {
    ANTHROPIC: "anthropic",
    OPENAI: "openai",
}
OPENCODE_NPM = {
    ANTHROPIC: "@ai-sdk/anthropic",
    OPENAI: "@ai-sdk/openai",
}

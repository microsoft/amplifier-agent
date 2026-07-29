"""Case data for the GitHub Copilot provider.

Everything here describes a provider that amplifier-agent does NOT yet support, so
every case in this module is expected to fail today (see test_github_copilot.py,
which marks each one xfail-strict). The current failure is a config-validation
rejection of ``provider.module: github-copilot`` before any network call happens.

Coverage is deliberately spread across three model families served by the one
provider, because the provider bridges three different backends and "it works for
Claude" says nothing about the GPT or Gemini paths:

    claude-sonnet-5    Anthropic family
    gpt-5.6-sol        OpenAI family
    gemini-3.6-flash   Google family

Those ids were read from the live Copilot model list for this account on 2026-07-28 (21
models), not from the provider module's README. The README is stale: it lists none of
gemini-3.6-flash, claude-opus-5 or grok-4.5, all of which the account actually serves.
The served set still depends on the Copilot plan, so if a case fails with an unknown-model
error rather than a config error, re-check with ``amplifier-agent models list github-copilot``.

Note there is no bare ``gpt-5.6``: the live list offers gpt-5.6-sol, gpt-5.6-terra and
gpt-5.6-luna. ``sol`` is an arbitrary pick among the three (272k context, same as terra);
swap it freely if one of the variants turns out to matter.
"""

from __future__ import annotations

from typing import Any

from framework.assertions import expect_contains
from framework.harness import E2ECase, Step

# Provider id as it appears in host-config ``provider.module`` and in the
# non-standard ``_provider`` field amplifier-agent adds to /v1/models entries.
PROVIDER_ID = "github-copilot"

# The label requirement: Copilot-served models must be distinguishable from every
# other provider's models in the picker. amplifier-app-opencode maps /v1/models
# ``display_name`` straight onto opencode's per-model ``name``, which is what the
# model dialog renders, so the suffix has to live on ``display_name``.
GITHUB_SUFFIX = " (GitHub)"

# Copilot resells models other providers also serve under byte-identical ids, so its
# wire ids are namespaced to stay separately addressable. The display suffix above is
# the human-facing half of the same fix; this is the machine-facing half.
NAMESPACE_PREFIX = f"{PROVIDER_ID}/"

# In-DTU locations. The suite conftest pushes the fixtures here at test time.
DTU_DIR = "/root/e2e/ghcp"
SECRET_PATH = f"{DTU_DIR}/secret.txt"

# Nonce proving a real tool call happened. It appears in exactly one place in this
# repository -- the fixture file -- and never in a prompt, so a model can only emit
# it by actually reading the file. Guessing it is not plausible.
SECRET_TOKEN = "GHCP-TOOLCALL-OK-Q9F3"

# (slug, model id). The slug keys the per-model host-config fixture filename.
MODELS: tuple[tuple[str, str], ...] = (
    ("claude", "claude-sonnet-5"),
    ("gpt", "gpt-5.6-sol"),
    ("gemini", "gemini-3.6-flash"),
)


def config_path(slug: str) -> str:
    """In-DTU path of the host-config selecting the model for ``slug``."""
    return f"{DTU_DIR}/host-config-ghcp-{slug}.json"


def expect_github_labelled(parsed: Any) -> None:
    """Assert Copilot models are served, namespaced, and suffixed.

    The DTU auto-enables every provider whose credentials resolve, so both
    ``anthropic`` and ``github-copilot`` are live here and both serve
    ``claude-sonnet-5`` under a byte-identical upstream id. That makes this the one
    case that exercises the collision end to end.

    Five failures worth telling apart, so each gets its own message:
      1. no github-copilot models served at all (provider not registered/enumerated)
      2. a Copilot id is not namespaced (the collision fix regressed)
      3. duplicate ids on the wire (namespacing failed to disambiguate)
      4. a Copilot model is missing the display suffix
      5. the suffix leaked onto another provider's models
    """
    entries = parsed.get("data") if isinstance(parsed, dict) else parsed
    assert isinstance(entries, list) and entries, f"no models in payload: {parsed!r}"

    ghcp = [e for e in entries if e.get("_provider") == PROVIDER_ID]
    assert ghcp, (
        f"no models served by {PROVIDER_ID!r}; providers seen: {sorted({str(e.get('_provider')) for e in entries})}"
    )

    unqualified = [e.get("id") for e in ghcp if not str(e.get("id", "")).startswith(NAMESPACE_PREFIX)]
    assert not unqualified, f"{PROVIDER_ID} model ids not namespaced {NAMESPACE_PREFIX!r}: {unqualified}"

    ids = [str(e.get("id")) for e in entries]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    assert not dupes, f"duplicate model ids on the wire (collision unresolved): {dupes}"

    unlabelled = [e.get("id") for e in ghcp if not str(e.get("display_name", "")).endswith(GITHUB_SUFFIX)]
    assert not unlabelled, f"{PROVIDER_ID} models missing {GITHUB_SUFFIX!r} suffix: {unlabelled}"

    leaked = [
        e.get("id")
        for e in entries
        if e.get("_provider") != PROVIDER_ID and str(e.get("display_name", "")).endswith(GITHUB_SUFFIX)
    ]
    assert not leaked, f"non-{PROVIDER_ID} models wrongly suffixed {GITHUB_SUFFIX!r}: {leaked}"


# One single-shot round-trip per model: the cheapest possible "this model answers at
# all" signal, so a multi-turn or tool-call red can be attributed to the feature under
# test rather than to the model being unreachable.
BASIC: list[E2ECase] = [
    E2ECase(
        f"ghcp-{slug}-basic-reply",
        "cli",
        ["run", "-y", "--config", config_path(slug), "reply with a short greeting"],
        check=None,
    )
    for slug, _model in MODELS
]

# Multi-turn. The provider flattens history into a single prompt per call and uses an
# ephemeral upstream session, so continuity is entirely amplifier-agent's transcript
# doing the work. That makes this the case most likely to expose a wiring mistake.
MULTITURN: list[E2ECase] = [
    E2ECase(
        f"ghcp-{slug}-multiturn",
        "cli-multi",
        [],
        steps=(
            Step(
                [
                    "run",
                    "-y",
                    "--config",
                    config_path(slug),
                    "--session-id",
                    "{SID}",
                    "Remember that I like bananas",
                ]
            ),
            Step(
                [
                    "run",
                    "-y",
                    "--config",
                    config_path(slug),
                    "--session-id",
                    "{SID}",
                    "--resume",
                    "What do I like?",
                ],
                check=expect_contains("bananas"),
            ),
        ),
    )
    for slug, _model in MODELS
]

# Tool calling, asserted by observable effect rather than by introspecting the tool
# protocol: the nonce can only reach stdout if the model actually issued a read of
# SECRET_PATH and got the result back. That keeps the assertion structural (per
# docs/E2E_TESTING.md) while still proving the whole call/return round trip.
#
# This is the case most at risk from the provider's deny-by-default tool policy: it
# denies execution at the SDK and hands tool calls back to amplifier-agent's
# orchestrator to run. If that handoff is wrong, this reds while BASIC stays green.
TOOLCALL: list[E2ECase] = [
    E2ECase(
        f"ghcp-{slug}-toolcall",
        "cli",
        [
            "run",
            "-y",
            "--config",
            config_path(slug),
            f"Read the file {SECRET_PATH} and reply with its exact contents.",
        ],
        check=expect_contains(SECRET_TOKEN),
    )
    for slug, _model in MODELS
]

# The labelling requirement, checked on the surface the opencode launcher actually
# reads. HTTP-only: the CLI cases above pin one model each via host-config and never
# see the full served set.
LABEL: list[E2ECase] = [
    E2ECase(
        "ghcp-models-labelled",
        "http",
        ("GET", "/v1/models"),
        check=expect_github_labelled,
    ),
]

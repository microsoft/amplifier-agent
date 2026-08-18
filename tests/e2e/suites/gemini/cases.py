"""Case definitions for the ``gemini`` provider suite.

Scope is deliberately narrow. PR #129 registered gemini as the eighth provider:
a catalog entry, a credential variable, an accepted ``provider.module`` value,
and an install-only bundle stub. Nothing about the provider's own behaviour is
ours to test -- that belongs to ``amplifier-module-provider-gemini``. So this
suite proves the four things amplifier-agent itself is now responsible for:

    1. gemini appears in the credential-resolution report with the right module
       and the right env var
    2. its models can be listed
    3. a host config naming it passes validation
    4. a real session actually runs on it

Assertions are on the public payloads documented in
``docs/spec/providers-and-models.md`` and ``docs/spec/cli.md``, never on log
output or internals.
"""

from __future__ import annotations

from typing import Any

from framework.assertions import expect_contains
from framework.harness import E2ECase

#: Provider name as it appears in configuration, ``auth`` subcommands, and
#: ``models list --provider``. See docs/spec/providers-and-models.md.
PROVIDER_ID = "gemini"

#: Module the catalog maps ``gemini`` onto.
PROVIDER_MODULE = "provider-gemini"

#: The one credential variable amplifier-agent consults. The Google GenAI SDK
#: also accepts GEMINI_API_KEY, but GOOGLE_API_KEY takes precedence and is the
#: sole entry in PROVIDER_CREDENTIAL_VARS, so it is the canonical one here too.
CREDENTIAL_VAR = "GOOGLE_API_KEY"

#: In-DTU seed directory and the host config pushed into it by conftest.
DTU_DIR = "/root/e2e/gemini"
CONFIG_PATH = f"{DTU_DIR}/host-config-gemini.json"

#: Single literal the smoke prompt asks for. Short, unambiguous, and unlikely
#: to appear by accident in a refusal or an error string.
PING_TOKEN = "PONG"


def _providers_rows(parsed: Any) -> list[dict[str, Any]]:
    """Pull the row list out of a ``providers list --json`` payload."""
    assert isinstance(parsed, dict), f"providers list did not emit a JSON object: {parsed!r}"
    rows = parsed.get("providers")
    assert isinstance(rows, list), f"payload has no 'providers' list: {parsed!r}"
    return [r for r in rows if isinstance(r, dict)]


def expect_gemini_registered(parsed: Any) -> None:
    """gemini is in the credential report, wired to the right module and var.

    Split into separate assertions because the four failures are genuinely
    different problems: not in KNOWN_PROVIDERS, wrong catalog entry, wrong
    credential variable, and "the DTU never received the key". The last one is
    the reason this suite has no separate ghcp-style token guard test -- this
    case already proves the passthrough reached the container.
    """
    rows = _providers_rows(parsed)
    names = [r.get("name") for r in rows]

    assert PROVIDER_ID in names, (
        f"{PROVIDER_ID!r} missing from `providers list`; it is not in KNOWN_PROVIDERS. Saw: {names}"
    )

    row = next(r for r in rows if r.get("name") == PROVIDER_ID)

    assert row.get("module") == PROVIDER_MODULE, (
        f"{PROVIDER_ID} maps to module {row.get('module')!r}, expected {PROVIDER_MODULE!r}"
    )
    assert row.get("env_var") == CREDENTIAL_VAR, (
        f"{PROVIDER_ID} reports credential var {row.get('env_var')!r}, expected {CREDENTIAL_VAR!r}"
    )
    assert row.get("resolvable") is True and row.get("source") == "env", (
        f"{PROVIDER_ID} did not resolve from the environment "
        f"(resolvable={row.get('resolvable')!r}, source={row.get('source')!r}). "
        f"{CREDENTIAL_VAR} is not reaching the DTU even though the suite guard saw it."
    )


def expect_gemini_models(parsed: Any) -> None:
    """``models list --provider gemini`` returns a usable live listing."""
    assert isinstance(parsed, dict), f"models list did not emit a JSON object: {parsed!r}"
    assert parsed.get("schema_version") == 1, f"unexpected schema_version: {parsed.get('schema_version')!r}"
    assert parsed.get("provider") == PROVIDER_ID, f"payload reports provider {parsed.get('provider')!r}"

    models = parsed.get("models")
    assert isinstance(models, list) and models, (
        f"{PROVIDER_ID} returned no models. An empty list is a legal answer for some providers, "
        f"but not for gemini: it means the live query reached nothing usable. Payload: {parsed!r}"
    )
    missing = [m for m in models if not (isinstance(m, dict) and m.get("id"))]
    assert not missing, f"model entries without an 'id': {missing!r}"


def expect_config_accepts_gemini(parsed: Any) -> None:
    """A host config naming ``gemini`` parses cleanly.

    ``config show`` is a diagnostic command and always exits 0, capturing a
    rejection into ``host_config.parse_error`` rather than a non-zero exit. So
    the absence of that key is the actual assertion: it is what proves
    ``gemini`` is in the closed ``provider.module`` set.
    """
    assert isinstance(parsed, dict), f"config show did not emit a JSON object: {parsed!r}"
    host_config = parsed.get("host_config")
    assert isinstance(host_config, dict), f"payload has no 'host_config' block: {parsed!r}"

    assert "parse_error" not in host_config, (
        f"host config naming provider.module={PROVIDER_ID!r} was rejected: {host_config.get('parse_error')!r}. "
        f"Expected it to be accepted (error code config_invalid_provider_module means the "
        f"provider is missing from the valid module set)."
    )

    provider_block = (host_config.get("parsed") or {}).get("provider")
    assert isinstance(provider_block, dict) and provider_block.get("module") == PROVIDER_ID, (
        f"parsed host config does not report provider.module={PROVIDER_ID!r}: {provider_block!r}"
    )


#: Catalog and credential wiring. No model is invoked.
WIRING: list[E2ECase] = [
    E2ECase(
        "gemini-providers-list",
        "cli",
        ["providers", "list", "--json"],
        check=expect_gemini_registered,
    ),
    E2ECase(
        "gemini-config-accepted",
        "cli",
        ["config", "show", "--config", CONFIG_PATH],
        check=expect_config_accepts_gemini,
    ),
]

#: Live query against Google, but no completion.
MODELS: list[E2ECase] = [
    E2ECase(
        "gemini-models-list",
        "cli",
        ["models", "list", "--provider", PROVIDER_ID, "--output", "json"],
        check=expect_gemini_models,
    ),
]

#: One real session. The cheapest possible proof that the provider module
#: mounts, authenticates, and completes end to end.
SMOKE: list[E2ECase] = [
    E2ECase(
        "gemini-basic-reply",
        "cli",
        [
            "run",
            "-y",
            "--config",
            CONFIG_PATH,
            f"Reply with exactly the word {PING_TOKEN} and nothing else.",
        ],
        check=expect_contains(PING_TOKEN),
    ),
]

"""Case definitions for the ``vllm`` provider suite.

Scope is deliberately narrow, and narrower still than the gemini suite's. PR #130
registered vllm as the ninth provider: a catalog entry, a dedicated credential
branch keyed on an *endpoint* rather than an API key, an accepted
``provider.module`` value, and an install-only bundle stub. Nothing about the
provider's own behaviour is ours to test -- that belongs to
``amplifier-module-provider-vllm``. So this suite proves the four things
amplifier-agent itself is now responsible for:

    1. vllm appears in the credential-resolution report with the right module
       and the right env var, and resolves from the environment
    2. its models can be listed off the configured server
    3. a host config naming it passes validation
    4. a real session actually runs on it

Model quality is explicitly out of scope. A self-hosted vLLM server is usually
running a small open-weight model, so the smoke case asks for one literal token
and nothing more. Anything that depended on instruction-following fidelity,
tool-calling competence, or reasoning quality would make this suite a flaky
report on the operator's model choice rather than on amplifier-agent's wiring.

Assertions are on the public payloads documented in
``docs/spec/providers-and-models.md`` and ``docs/spec/cli.md``, never on log
output or internals.
"""

from __future__ import annotations

from typing import Any

from framework.harness import E2ECase

#: Provider name as it appears in configuration, ``auth`` subcommands, and
#: ``models list --provider``. See docs/spec/providers-and-models.md.
PROVIDER_ID = "vllm"

#: Module the catalog maps ``vllm`` onto.
PROVIDER_MODULE = "provider-vllm"

#: The one variable amplifier-agent consults to decide *which server to talk to*.
#: Unlike every keyed provider, vllm's credential is an endpoint, so this lands in
#: ``fields["base_url"]`` rather than ``fields["api_key"]``. ``VLLM_API_KEY`` is
#: optional and deliberately not asserted on: a local vLLM server needs none, and
#: making its presence part of the contract would break the common setup.
CREDENTIAL_VAR = "VLLM_BASE_URL"

#: In-DTU seed directory and the host config rendered into it by conftest.
DTU_DIR = "/root/e2e/vllm"
CONFIG_PATH = f"{DTU_DIR}/host-config-vllm.json"

#: Single literal the smoke prompt asks for. Short, unambiguous, and unlikely
#: to appear by accident in a refusal or an error string. Matched case-insensitively
#: and as a substring, so a small model that wraps it in pleasantries still passes --
#: the case is proving the round trip completed, not that the model obeys precisely.
PING_TOKEN = "PONG"


def _providers_rows(parsed: Any) -> list[dict[str, Any]]:
    """Pull the row list out of a ``providers list --json`` payload."""
    assert isinstance(parsed, dict), f"providers list did not emit a JSON object: {parsed!r}"
    rows = parsed.get("providers")
    assert isinstance(rows, list), f"payload has no 'providers' list: {parsed!r}"
    return [r for r in rows if isinstance(r, dict)]


def expect_vllm_registered(parsed: Any) -> None:
    """vllm is in the credential report, wired to the right module and variable.

    Split into separate assertions because the four failures are genuinely
    different problems: not in KNOWN_PROVIDERS, wrong catalog entry, wrong
    credential variable, and "the endpoint never reached the DTU". The last one
    is the reason this suite needs no separate passthrough-guard test -- this
    case already proves the var survived the localhost rewrite into the container.
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


def expect_vllm_models(parsed: Any) -> None:
    """``models list --provider vllm`` returns a usable live listing.

    An empty list is a legal answer for some providers but not for this one: vllm
    only resolves at all when an endpoint is configured, and the suite guard has
    already proved that endpoint is serving ``/v1/models``. Empty here means the
    agent queried something other than the configured server.
    """
    assert isinstance(parsed, dict), f"models list did not emit a JSON object: {parsed!r}"
    assert parsed.get("schema_version") == 1, f"unexpected schema_version: {parsed.get('schema_version')!r}"
    assert parsed.get("provider") == PROVIDER_ID, f"payload reports provider {parsed.get('provider')!r}"

    models = parsed.get("models")
    assert isinstance(models, list) and models, (
        f"{PROVIDER_ID} returned no models, but the suite guard reached the server's /v1/models. Payload: {parsed!r}"
    )
    missing = [m for m in models if not (isinstance(m, dict) and m.get("id"))]
    assert not missing, f"model entries without an 'id': {missing!r}"


def expect_config_accepts_vllm(parsed: Any) -> None:
    """A host config naming ``vllm`` parses cleanly.

    ``config show`` is a diagnostic command and always exits 0, capturing a
    rejection into ``host_config.parse_error`` rather than a non-zero exit. So
    the absence of that key is the actual assertion: it is what proves ``vllm``
    is in the closed ``provider.module`` set.
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


def expect_ping(parsed: Any) -> None:
    """The reply mentions the ping token.

    Substring, case-insensitive, and tolerant of surrounding text on purpose. A
    small open-weight model may prepend a reasoning preamble or wrap the answer in
    a sentence; that is a fact about the operator's model, not a defect in the
    provider wiring this suite exists to test. What would genuinely fail is the
    token never appearing at all, which is what an auth failure, a wrong endpoint,
    or a broken mount actually looks like.
    """
    text = str(parsed)
    assert PING_TOKEN.lower() in text.lower(), (
        f"expected {PING_TOKEN!r} somewhere in the reply; the session did not complete "
        f"against the vLLM server. Got:\n{text}"
    )


#: Catalog and credential wiring. No model is invoked.
WIRING: list[E2ECase] = [
    E2ECase(
        "vllm-providers-list",
        "cli",
        ["providers", "list", "--json"],
        check=expect_vllm_registered,
    ),
    E2ECase(
        "vllm-config-accepted",
        "cli",
        ["config", "show", "--config", CONFIG_PATH],
        check=expect_config_accepts_vllm,
    ),
]

#: Live query against the configured vLLM server, but no completion.
MODELS: list[E2ECase] = [
    E2ECase(
        "vllm-models-list",
        "cli",
        ["models", "list", "--provider", PROVIDER_ID, "--output", "json"],
        check=expect_vllm_models,
    ),
]

#: One real session. The cheapest possible proof that the provider module
#: mounts, reaches the server, and completes end to end.
SMOKE: list[E2ECase] = [
    E2ECase(
        "vllm-basic-reply",
        "cli",
        [
            "run",
            "-y",
            "--config",
            CONFIG_PATH,
            f"Reply with exactly the word {PING_TOKEN} and nothing else.",
        ],
        check=expect_ping,
    ),
]

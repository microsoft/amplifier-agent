"""Shared provider-credential env-var isolation for the test suite.

Several fixtures need the same guarantee: "no provider looks configured
unless this test configured it." Each one used to hardcode its own list of
env vars, and that is exactly how ``GITHUB_TOKEN`` slipped through when
``github-copilot`` was added -- five separate lists named four providers'
vars and none of them was updated, so any developer with ``GITHUB_TOKEN``
exported saw provider-enumeration tests fail.

:data:`PROVIDER_ENV_VARS` is *derived* from
:data:`~amplifier_agent_cli.provider_sources.PROVIDER_CREDENTIAL_VARS`, so a
sixth provider is covered the moment it is registered there. Do not hand-add
provider vars below; add them to the map.
"""

from __future__ import annotations

from typing import Final

import pytest

from amplifier_agent_cli.provider_sources import PROVIDER_CREDENTIAL_VARS

#: Credential-bearing vars that are deliberately NOT in
#: ``PROVIDER_CREDENTIAL_VARS`` but still need clearing.
#:
#: The first two are read directly by ``provider_sources`` rather than through
#: the map (``_OLLAMA_BASE_URL_ENV`` is a non-deprecated alias, and the azure
#: endpoint is attached alongside the api_key). The remaining three are the
#: Copilot provider module's own token chain: they do not affect agent-side
#: resolution, so they stay out of the map by design, but clearing them keeps a
#: developer shell from reaching the provider during a test.
_EXTRA_CREDENTIAL_ENV_VARS: Final[tuple[str, ...]] = (
    "OLLAMA_BASE_URL",
    "AZURE_OPENAI_ENDPOINT",
    "COPILOT_AGENT_TOKEN",
    "COPILOT_GITHUB_TOKEN",
    "GH_TOKEN",
)


def _collect_env_vars() -> tuple[str, ...]:
    """Flatten every provider's env vars, preserving order and de-duplicating."""
    seen: dict[str, None] = {}
    for names in PROVIDER_CREDENTIAL_VARS.values():
        for name in names:
            seen[name] = None
    for name in _EXTRA_CREDENTIAL_ENV_VARS:
        seen[name] = None
    return tuple(seen)


#: Every env var that can make a provider look configured.
PROVIDER_ENV_VARS: Final[tuple[str, ...]] = _collect_env_vars()


def clear_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Delete every provider-credential env var for the duration of a test."""
    for var in PROVIDER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)

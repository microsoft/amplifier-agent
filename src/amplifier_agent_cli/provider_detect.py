"""Provider auto-detection from environment variables.

Precedence order: ANTHROPIC_API_KEY > OPENAI_API_KEY > AZURE_OPENAI_KEY > OLLAMA_HOST.
The --provider CLI flag overrides env-var detection entirely.
"""

from __future__ import annotations

import os
from typing import Final

KNOWN_PROVIDERS: Final[tuple[str, ...]] = ("anthropic", "openai", "azure-openai", "ollama")

_DETECTION_ORDER: Final[tuple[tuple[str, str], ...]] = (
    ("ANTHROPIC_API_KEY", "anthropic"),
    ("OPENAI_API_KEY", "openai"),
    ("AZURE_OPENAI_KEY", "azure-openai"),
    ("OLLAMA_HOST", "ollama"),
)


class ProviderNotConfigured(Exception):
    """Raised when no provider can be determined from env vars or an explicit override."""

    code: str = "provider_not_configured"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def detect_provider(override: str | None) -> str:
    """Return the active provider name.

    If *override* is given, validate it against KNOWN_PROVIDERS and return it.
    Otherwise, walk _DETECTION_ORDER and return the first provider whose env
    var is set to a truthy value.  Raise ProviderNotConfigured if nothing is
    configured.
    """
    if override is not None:
        if override not in KNOWN_PROVIDERS:
            known = ", ".join(KNOWN_PROVIDERS)
            raise ProviderNotConfigured(f"Unknown provider '{override}'. Known providers: {known}.")
        return override

    for env_var, provider in _DETECTION_ORDER:
        if os.environ.get(env_var):
            return provider

    raise ProviderNotConfigured(
        "No provider configured. Set one of the following environment variables: "
        "ANTHROPIC_API_KEY, OPENAI_API_KEY, AZURE_OPENAI_KEY, OLLAMA_HOST. "
        "Alternatively, pass --provider <name> on the command line. "
        "See the README for setup instructions."
    )

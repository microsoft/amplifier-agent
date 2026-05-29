"""Provider auto-detection from environment variables.

Precedence order: ANTHROPIC_API_KEY > OPENAI_API_KEY > AZURE_OPENAI_API_KEY > OLLAMA_HOST.
The --provider CLI flag overrides env-var detection entirely.

Azure note: the documented (and upstream-module-preferred) env var is
``AZURE_OPENAI_API_KEY``. For backwards compatibility with the CLI's earlier
``AZURE_OPENAI_KEY`` spelling, the legacy name is still accepted as a
deprecated alias and triggers a one-time stderr warning. This mirrors the
behavior of ``amplifier-module-provider-azure-openai`` itself, which checks
``AZURE_OPENAI_API_KEY`` first and falls back to ``AZURE_OPENAI_KEY``.
"""

from __future__ import annotations

import os
import sys
from typing import Final

KNOWN_PROVIDERS: Final[tuple[str, ...]] = ("anthropic", "openai", "azure-openai", "ollama")

#: Detection slots, walked in precedence order. Each slot lists the preferred
#: env var first, then any legacy aliases. The first slot with a truthy value
#: (in any of its vars) wins. When a legacy alias supplies the value, a
#: one-time stderr deprecation notice is emitted.
_DETECTION_ORDER: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    # (provider_name, (preferred_var, *legacy_aliases))
    ("anthropic", ("ANTHROPIC_API_KEY",)),
    ("openai", ("OPENAI_API_KEY",)),
    ("azure-openai", ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_KEY")),
    ("ollama", ("OLLAMA_HOST",)),
)

_DEPRECATION_NOTICE_EMITTED: set[str] = set()


def _emit_deprecation_notice(legacy_var: str, preferred_var: str) -> None:
    """Emit a one-time stderr warning when a legacy env var triggers detection."""
    if legacy_var in _DEPRECATION_NOTICE_EMITTED:
        return
    _DEPRECATION_NOTICE_EMITTED.add(legacy_var)
    print(
        f"[WARN] {legacy_var} is deprecated; please set {preferred_var} instead. "
        f"Support for {legacy_var} will be removed in a future release.",
        file=sys.stderr,
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
    var is set to a truthy value.  If no preferred env var is set, fall back
    to _LEGACY_DETECTION_ORDER (emitting a one-time deprecation notice on
    stderr).  Raise ProviderNotConfigured if nothing is configured.
    """
    if override is not None:
        if override not in KNOWN_PROVIDERS:
            known = ", ".join(KNOWN_PROVIDERS)
            raise ProviderNotConfigured(f"Unknown provider '{override}'. Known providers: {known}.")
        return override

    for provider, env_vars in _DETECTION_ORDER:
        preferred_var = env_vars[0]
        for index, env_var in enumerate(env_vars):
            if os.environ.get(env_var):
                if index > 0:
                    # Legacy alias supplied the value — warn the user.
                    _emit_deprecation_notice(env_var, preferred_var)
                return provider

    raise ProviderNotConfigured(
        "No provider configured. Set one of the following environment variables: "
        "ANTHROPIC_API_KEY, OPENAI_API_KEY, AZURE_OPENAI_API_KEY, OLLAMA_HOST. "
        "Alternatively, pass --provider <name> on the command line. "
        "See the README for setup instructions."
    )

"""Admin command: config show — print resolved config as JSON with source annotations.

Precedence (highest to lowest):
  CLI flags > env vars > XDG config file > compiled defaults.

Phase 2 scope: surface provider + XDG config/cache/state paths.
Reading config.toml is a follow-up; non-env fields are annotated source='default'.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import click

from amplifier_agent_cli.provider_detect import (
    _DETECTION_ORDER as _PROVIDER_ENV_ORDER,
)
from amplifier_agent_cli.provider_detect import (
    ProviderNotConfigured,
    detect_provider,
)


def _annotate_env_or_default(env_var: str, default: Path) -> dict[str, Any]:
    """Return a value/source dict for an XDG path env var.

    If *env_var* is set and non-empty, returns its value annotated as
    ``env:<env_var>``.  Otherwise returns the *default* path string annotated
    as ``default``.
    """
    value = os.environ.get(env_var)
    if value:
        return {"value": value, "source": f"env:{env_var}"}
    return {"value": str(default), "source": "default"}


def _resolve_provider() -> dict[str, Any]:
    """Determine the active provider and where that decision came from.

    Walk the detection order; if any env var is set, return its value + source.
    If none are set, attempt detect_provider(override=None) for a 'default'
    result (covers any future file-based config).  On ProviderNotConfigured,
    return value=None, source='unset'.
    """
    for env_var, provider_name in _PROVIDER_ENV_ORDER:
        if os.environ.get(env_var):
            return {"value": provider_name, "source": f"env:{env_var}"}

    try:
        name = detect_provider(override=None)
        return {"value": name, "source": "default"}
    except ProviderNotConfigured:
        return {"value": None, "source": "unset"}


@click.group()
def config_group() -> None:
    """Inspect resolved config."""


@config_group.command(name="show")
def config_show() -> None:
    """Print resolved configuration as JSON with source annotations."""
    home = Path(os.environ.get("HOME", str(Path.home())))

    payload: dict[str, Any] = {
        "provider": _resolve_provider(),
        "xdg_config_home": _annotate_env_or_default("XDG_CONFIG_HOME", home / ".config"),
        "xdg_cache_home": _annotate_env_or_default("XDG_CACHE_HOME", home / ".cache"),
        "xdg_state_home": _annotate_env_or_default("XDG_STATE_HOME", home / ".local" / "state"),
    }

    click.echo(json.dumps(payload, indent=2))

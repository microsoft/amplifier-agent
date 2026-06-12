"""Admin command: config show — print resolved config as JSON with source annotations.

Precedence (highest to lowest):
  CLI flags > env vars > config file > compiled defaults.

Phase 2 scope: surface provider + storage home path.
Reading config.toml is a follow-up; non-env fields are annotated source='default'.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import click
import yaml

from amplifier_agent_lib.bundle import BUNDLE_MD


def _annotate_env_or_default(env_var: str, default: Path) -> dict[str, Any]:
    """Return a value/source dict for a path env var.

    If *env_var* is set and non-empty, returns its value annotated as
    ``env:<env_var>``.  Otherwise returns the *default* path string annotated
    as ``default``.
    """
    value = os.environ.get(env_var)
    if value:
        return {"value": value, "source": f"env:{env_var}"}
    return {"value": str(default), "source": "default"}


def _resolve_host_config(config_arg: str | None) -> dict[str, Any]:
    """Report the resolved host config path + source + parsed values (D8).

    Precedence: --config flag > $AMPLIFIER_AGENT_CONFIG env > none. Never
    raises: parse failures are captured into ``parse_error`` with
    ``parsed=None`` so ``config show`` remains a diagnostic command that
    always exits 0.
    """
    if config_arg is not None:
        result: dict[str, Any] = {"path": config_arg, "source": "--config flag"}
    elif env_val := os.environ.get("AMPLIFIER_AGENT_CONFIG"):
        result = {"path": env_val, "source": "$AMPLIFIER_AGENT_CONFIG env"}
    else:
        return {"path": None, "source": "none", "parsed": None}

    # Local import: keep the CLI start-up cost off the no-config path and
    # avoid coupling the admin module to the lib config package at import
    # time.
    from amplifier_agent_lib.config import ConfigError, load_config

    try:
        result["parsed"] = load_config(config_arg=config_arg)
    except ConfigError as exc:
        result["parsed"] = None
        result["parse_error"] = {"code": exc.code, "message": exc.message}
    return result


def _resolve_skills(config_arg: str | None) -> dict[str, Any]:
    """Report the post-merge skills block (D8 + D11/D12).

    Reads the bundle's `tool-skills` static config from bundle.md, then
    overlays the host_config `skills:` block using the same merge semantics
    the runtime uses:
      - `skills.skills` is list-concatenated (bundle-first, host-appended).
      - `skills.visibility` is dict-overlaid (host wins on key collisions).

    Never raises. Parse failures on the host config surface as
    ``parse_error`` while the bundle defaults are still reported so the
    operator sees what would compose absent the broken override.
    """
    # Local imports to keep startup cost off the no-config path.
    from amplifier_agent_lib.config import ConfigError, load_config

    # Bundle defaults — read from the manifest the same way _resolve_provider does.
    bundle_skills: list[str] = []
    bundle_visibility: dict[str, Any] = {}
    try:
        manifest = yaml.safe_load(BUNDLE_MD.read_text(encoding="utf-8").split("---\n")[1])
        if isinstance(manifest, dict):
            for entry in manifest.get("tools") or []:
                if isinstance(entry, dict) and entry.get("module") == "tool-skills":
                    cfg = entry.get("config") or {}
                    if isinstance(cfg, dict):
                        raw_skills = cfg.get("skills")
                        if isinstance(raw_skills, list):
                            bundle_skills = [s for s in raw_skills if isinstance(s, str)]
                        raw_vis = cfg.get("visibility")
                        if isinstance(raw_vis, dict):
                            bundle_visibility = dict(raw_vis)
                    break
    except Exception:
        # Diagnostic-only path — never fail config show.
        pass

    merged_skills = list(bundle_skills)
    merged_visibility = dict(bundle_visibility)
    parse_error: dict[str, str] | None = None

    try:
        parsed = load_config(config_arg=config_arg)
    except ConfigError as exc:
        parsed = None
        parse_error = {"code": exc.code, "message": exc.message}

    if isinstance(parsed, dict):
        host_skills_block = parsed.get("skills")
        if isinstance(host_skills_block, dict):
            host_list = host_skills_block.get("skills")
            if isinstance(host_list, list):
                merged_skills.extend(s for s in host_list if isinstance(s, str))
            host_vis = host_skills_block.get("visibility")
            if isinstance(host_vis, dict):
                merged_visibility.update(host_vis)

    result: dict[str, Any] = {"skills": merged_skills, "visibility": merged_visibility}
    if parse_error is not None:
        result["parse_error"] = parse_error
    return result


def _resolve_provider() -> dict[str, Any]:
    """Determine the active provider from the vendored bundle.md default (D6).

    Reads ``default_provider:`` from ``bundle.md``'s YAML frontmatter. Returns
    ``source='bundle.default_provider'`` when present, ``source='unset'`` when
    the field is missing/non-string, and ``source='error'`` if the manifest
    cannot be parsed.
    """
    try:
        manifest = yaml.safe_load(BUNDLE_MD.read_text(encoding="utf-8").split("---\n")[1])
    except Exception:
        return {"value": None, "source": "error"}
    default = manifest.get("default_provider") if isinstance(manifest, dict) else None
    if isinstance(default, str):
        return {"value": default, "source": "bundle.default_provider"}
    return {"value": None, "source": "unset"}


@click.group()
def config_group() -> None:
    """Inspect resolved config."""


@config_group.command(name="show")
@click.option(
    "--config",
    "config_path",
    default=None,
    type=click.Path(),
    help="Path to host config file.",
)
def config_show(config_path: str | None) -> None:
    """Print resolved configuration as JSON with source annotations."""
    from amplifier_agent_lib.persistence import amplifier_agent_home

    payload: dict[str, Any] = {
        "provider": _resolve_provider(),
        "host_config": _resolve_host_config(config_path),
        "skills": _resolve_skills(config_path),
        "amplifier_agent_home": _annotate_env_or_default("AMPLIFIER_AGENT_HOME", amplifier_agent_home()),
    }

    click.echo(json.dumps(payload, indent=2))

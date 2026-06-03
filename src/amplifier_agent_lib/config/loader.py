"""Host configuration loader for amplifier-agent.

Resolution order (first present wins):

1. ``--config <path>`` argv flag (passed in as ``config_arg``).
2. ``$AMPLIFIER_AGENT_CONFIG`` environment variable.

Per D1, when **neither** tier is present, :func:`load_config` returns
``None`` — the caller (engine/CLI) treats this as "no host config" and
falls back to hardcoded defaults.  Per D5, a JSON ``null`` literal at the
document root is normalized to an empty dict (matching the omitted-block
semantics).  Per D7, the top-level schema is closed: non-mapping roots
and unknown top-level keys both raise :class:`ConfigError`.

:class:`ConfigError` is defined here (rather than the package
``__init__``) so this module can raise it without creating a circular
import.  The package ``__init__`` re-exports it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from amplifier_agent_lib.protocol.errors import AaaError

__all__ = ["ConfigError", "load_config"]

_VALID_TOP_LEVEL_KEYS = frozenset({"mcp", "approval", "provider", "allowProtocolSkew", "skills"})
_VALID_PROVIDER_MODULES = frozenset({"anthropic", "openai", "azure-openai", "ollama"})
# D11 closes the ``skills.*`` inner shape against this set.  Per D11 this is a
# closed inner shape (config_invalid_type), distinct from the closed top-level
# schema (D7, config_unknown_key).  D7 pass-through applies one level deeper,
# inside ``skills.visibility`` — see _validate_skills_block for that boundary.
_ALLOWED_SKILLS_SUBKEYS = frozenset({"skills", "visibility"})


def _validate_approval_patterns(approval_block: Any, path: Path) -> None:
    """Enforce that ``approval.patterns`` (when present) is a list of strings.

    D7 type guard.  JSON parses literal types unambiguously, so the YAML
    Norway problem does not apply here; this guard exists so a host that
    passes a non-string item gets a clear configuration error rather than
    an opaque downstream surprise in hooks-approval pattern matching.
    """
    if not isinstance(approval_block, dict):
        # Absent or non-mapping approval block: bundle default applies (D5).
        return
    patterns = approval_block.get("patterns")
    if patterns is None:
        return
    if not isinstance(patterns, list):
        raise ConfigError(
            code="config_invalid_type",
            message=(f"approval.patterns at {path} must be a JSON array of strings, got {type(patterns).__name__}."),
            classification="protocol",
        )
    for i, item in enumerate(patterns):
        if not isinstance(item, str):
            raise ConfigError(
                code="config_invalid_type",
                message=(
                    f"approval.patterns[{i}] at {path} must be a string, "
                    f"got {type(item).__name__} ({item!r}). "
                    f"Each member of approval.patterns must be a JSON string literal."
                ),
                classification="protocol",
            )


def _validate_skills_block(skills_block: Any, path: Path) -> None:
    """Enforce that ``skills.skills`` (when present) is a list of strings.

    D11 + D7 type guard.  The ``skills.skills`` key is a list of source URIs
    (git URIs, workspace-relative paths, or user-home paths) that the
    downstream skills loader resolves into mounted skill bundles.  Catching
    a non-list or non-string-member value here gives the operator a clear
    parse-time error rather than an opaque failure deep in the skills
    loader.  Mirrors :func:`_validate_approval_patterns` exactly: when the
    block is absent, non-mapping, or omits the ``skills`` key, the bundle
    default applies (D5) and no error is raised.
    """
    if not isinstance(skills_block, dict):
        # Absent or non-mapping skills block: bundle default applies (D5).
        return
    # D11: close the ``skills.*`` inner shape against {skills, visibility}.
    # This is config_invalid_type (closed inner shape) — NOT config_unknown_key,
    # which D7 reserves for top-level keys.  D7 pass-through applies only one
    # level deeper (inside ``skills.visibility``), so unknown sub-keys at the
    # ``skills.*`` level must raise loudly at parse time rather than silently
    # propagating to the skills module.
    unknown = set(skills_block.keys()) - _ALLOWED_SKILLS_SUBKEYS
    if unknown:
        raise ConfigError(
            code="config_invalid_type",
            message=(
                f"Unknown sub-keys under skills.*: {sorted(unknown)}. Allowed: {sorted(_ALLOWED_SKILLS_SUBKEYS)}."
            ),
            classification="protocol",
        )
    # D11 + D7 shape guard for the ``visibility`` sub-block.  When present,
    # ``skills.visibility`` must be a JSON object (dict) so the downstream
    # skills module receives a mapping it can interpret.  Per D11 the inner
    # keys (``enabled``, ``inject_role``, ``max_skills_visible``, etc.) are
    # pass-through and the module owns their validation — the loader does
    # NOT iterate inner keys here.  This keeps loader responsibility narrow
    # (shape only) and lets the skills module evolve its accepted keys
    # independently of the loader's release cadence.
    if "visibility" in skills_block:
        visibility = skills_block["visibility"]
        if not isinstance(visibility, dict):
            raise ConfigError(
                code="config_invalid_type",
                message=(f"skills.visibility at {path} must be a dict (JSON object), got {type(visibility).__name__}."),
                classification="protocol",
            )
    skills = skills_block.get("skills")
    if skills is None:
        return
    if not isinstance(skills, list):
        raise ConfigError(
            code="config_invalid_type",
            message=(f"skills.skills at {path} must be a JSON array (list) of strings, got {type(skills).__name__}."),
            classification="protocol",
        )
    for i, item in enumerate(skills):
        if not isinstance(item, str):
            raise ConfigError(
                code="config_invalid_type",
                message=(
                    f"skills.skills[{i}] at {path} must be a string, "
                    f"got {type(item).__name__} ({item!r}). "
                    f"Each member of skills.skills must be a JSON string literal (a source URI)."
                ),
                classification="protocol",
            )


def _validate_provider_module(provider_block: Any, path: Path) -> None:
    """Enforce that ``provider.module`` (when present) is a supported provider.

    A3 cross-module validation / D7 type guard.  The merger silently falls
    through on invalid ``provider.module`` (defensive: preserve bundle
    default).  The loader catches it loudly so the operator sees the error
    at parse time rather than as a silent no-op much later.  When the
    ``provider`` block is absent or the ``module`` key is omitted, the
    bundle's ``default_provider`` applies (D6) and no error is raised.
    """
    if not isinstance(provider_block, dict):
        # Absent or non-mapping provider block: bundle default applies (D6).
        return
    module = provider_block.get("module")
    if module is None:
        # Omitted module key: bundle's default_provider applies (D6).
        return
    if module not in _VALID_PROVIDER_MODULES:
        raise ConfigError(
            code="config_invalid_provider_module",
            message=(f"provider.module at {path} must be one of {sorted(_VALID_PROVIDER_MODULES)}, got {module!r}."),
            classification="protocol",
        )


class ConfigError(AaaError):
    """Recoverable configuration error raised by loader/merger.

    Subclasses :class:`AaaError` so the CLI's existing error-envelope
    machinery catches it and emits a §4.1 envelope.  Defaults
    ``classification`` to ``"protocol"`` so the wrapper exits with the
    protocol exit code (2) unless the caller explicitly overrides it.
    """

    def __init__(
        self,
        *,
        code: str,
        message: str,
        classification: str | None = "protocol",
    ) -> None:
        super().__init__(
            code=code,
            message=message,
            classification=classification,
        )


def load_config(config_arg: str | None) -> dict[str, Any] | None:
    """Load host configuration following the documented resolution order.

    Args:
        config_arg: Value of the ``--config <path>`` CLI flag, or ``None``
            if the flag was not supplied.

    Returns:
        The parsed configuration dict if a resolution tier was present,
        or ``None`` when neither the ``--config`` flag nor the
        ``$AMPLIFIER_AGENT_CONFIG`` env var is set (D1).

    Raises:
        ConfigError: When the resolved file is missing or unreadable
            (D2, ``code='config_unreadable'``,
            ``classification='protocol'``).  Note that setting
            ``$AMPLIFIER_AGENT_CONFIG`` is treated as an affirmative
            declaration; a missing path from env raises just like a
            missing path from the flag (not silently ignored).
        ConfigError: When the resolved file contains malformed JSON
            (D7, ``code='config_malformed_json'``,
            ``classification='protocol'``).
        ConfigError: When the parsed JSON root is not a mapping
            (D7, ``code='config_malformed_json'``,
            ``classification='protocol'``).
        ConfigError: When the top-level mapping contains keys outside
            the closed schema (D7, ``code='config_unknown_key'``,
            ``classification='protocol'``).
    """
    path_str = config_arg or os.environ.get("AMPLIFIER_AGENT_CONFIG") or None
    if path_str is None:
        return None
    path = Path(path_str)
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError, IsADirectoryError, OSError) as exc:
        raise ConfigError(
            code="config_unreadable",
            message=f"Cannot read config at {path}: {exc.__class__.__name__}: {exc}",
            classification="protocol",
        ) from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            code="config_malformed_json",
            message=f"Failed to parse JSON at {path}: {exc}",
            classification="protocol",
        ) from exc
    if parsed is None:
        # D5: null literal at the root → empty dict, matching omitted-block semantics.
        return {}
    if not isinstance(parsed, dict):
        raise ConfigError(
            code="config_malformed_json",
            message=(f"Config root at {path} must be a JSON object, got {type(parsed).__name__}."),
            classification="protocol",
        )
    unknown = set(parsed) - _VALID_TOP_LEVEL_KEYS
    if unknown:
        raise ConfigError(
            code="config_unknown_key",
            message=(
                f"Unknown top-level config key(s): {sorted(unknown)}. "
                f"Valid keys: {sorted(_VALID_TOP_LEVEL_KEYS)}. "
                f"amplifier-agent's config schema is closed at the top level (D7)."
            ),
            classification="protocol",
        )
    _validate_approval_patterns(parsed.get("approval"), path)
    _validate_provider_module(parsed.get("provider"), path)
    _validate_skills_block(parsed.get("skills"), path)
    return parsed

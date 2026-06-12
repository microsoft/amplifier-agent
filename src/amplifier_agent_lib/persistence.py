"""Filesystem path helpers for amplifier-agent.

This module is pure path computation — it never creates directories.
All paths are rooted at a single home directory:

    Default: ~/.amplifier-agent/
    Override: $AMPLIFIER_AGENT_HOME

Sub-layout:
    <home>/cache/    — prepared-bundle cache
    <home>/config/   — host config
    <home>/state/    — workspaces, sessions, transcripts, audits
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Mapping
from pathlib import Path

from amplifier_agent_lib import __version__

APP_NAME = "amplifier-agent"

# Workspace slug grammar (D3). Lowercase alphanumerics + hyphens, 1-64 chars,
# must start with [a-z0-9]. Leading '_' is reserved for AAA-internal
# workspaces (e.g. "_legacy", I7) and is therefore unreachable via this regex.
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class WorkspaceError(ValueError):
    """Raised when a workspace slug fails the D3 grammar."""


def validate_slug(value: str) -> str:
    """Return ``value`` if it matches the D3 slug grammar, else raise.

    Path-traversal (``..``, ``/``), uppercase, the reserved ``_`` prefix,
    over-length, and empty values are all rejected here, before the value
    can ever be joined into a filesystem path.
    """
    if not SLUG_RE.match(value):
        raise WorkspaceError(f"invalid workspace slug: {value!r}; must match [a-z0-9][a-z0-9-]{{0,63}}")
    return value


def _slugify(text: str) -> str:
    """Lowercase, collapse non-alphanumeric runs to '-', strip ends.

    Returns ``"default"`` for input that slugifies to empty.
    Non-ASCII characters are stripped (é → dropped); pre-normalize input
    if you need transliteration.
    """
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return text or "default"


def derive_workspace_from_cwd(cwd: Path) -> str:
    """Derive a stable, valid workspace slug from a working directory (D4).

    Same cwd always produces the same slug (I5). An 8-char SHA256 of the
    resolved absolute path disambiguates same-basename repos. The result is
    valid by construction (slugify + 48-char bound + hash suffix), so the
    reserved ``_`` prefix is unreachable and no validate_slug call is needed.
    """
    basename = cwd.name or "default"
    slug_base = _slugify(basename)[:48].rstrip("-") or "default"
    cwd_hash = hashlib.sha256(str(cwd.resolve()).encode()).hexdigest()[:8]
    return f"{slug_base}-{cwd_hash}"


def resolve_workspace(
    argv_workspace: str | None,
    env: Mapping[str, str],
    cwd: Path,
) -> str:
    """Resolve the workspace identifier (D2). First non-empty hit wins.

    Order: argv flag > ``AMPLIFIER_AGENT_WORKSPACE`` env var > cwd-derived.
    Never returns None or empty. Whitespace-only values in either tier
    are treated as absent (a user typing ``--workspace "  "`` is forgiven
    the same way an empty env var is). Non-empty explicit values are
    validated; the cwd-derived fallback is valid by construction (D4).
    """
    argv_stripped = (argv_workspace or "").strip()
    if argv_stripped:
        return validate_slug(argv_stripped)
    env_value = env.get("AMPLIFIER_AGENT_WORKSPACE", "").strip()
    if env_value:
        return validate_slug(env_value)
    return derive_workspace_from_cwd(cwd)


def _home() -> Path:
    """Return the current user's home directory."""
    return Path(os.environ.get("HOME", os.path.expanduser("~")))


def amplifier_agent_home() -> Path:
    """Single root for all amplifier-agent on-disk state.

    Default: ~/.amplifier-agent/
    Override: $AMPLIFIER_AGENT_HOME
    """
    override = os.environ.get("AMPLIFIER_AGENT_HOME")
    if override:
        return Path(override).expanduser()
    return _home() / ".amplifier-agent"


def cache_root() -> Path:
    """Return the cache root for this app.

    Resolves to <amplifier_agent_home>/cache/.
    Override the entire tree via $AMPLIFIER_AGENT_HOME.
    """
    return amplifier_agent_home() / "cache"


def config_root() -> Path:
    """Return the config root for this app.

    Resolves to <amplifier_agent_home>/config/.
    Override the entire tree via $AMPLIFIER_AGENT_HOME.
    """
    return amplifier_agent_home() / "config"


def state_root() -> Path:
    """Return the state root for this app.

    Resolves to <amplifier_agent_home>/state/.
    Override the entire tree via $AMPLIFIER_AGENT_HOME.
    """
    return amplifier_agent_home() / "state"


def workspaces_root() -> Path:
    """Return the root that buckets session state by workspace (D8).

    Layout: ``<state_root>/workspaces/<workspace>/sessions/<session_id>/``.
    Pure path computation; never creates directories.
    """
    return state_root() / "workspaces"


def prepared_bundle_dir(*, version: str | None = None) -> Path:
    """Return the directory for prepared bundles at the given version.

    Defaults to the current package __version__.  Bumping the version
    automatically invalidates all previously-prepared bundles.
    """
    v = version if version is not None else __version__
    return cache_root() / "prepared" / v


def session_state_dir(session_id: str) -> Path:
    """Return the state directory for the given session.

    Raises ValueError if *session_id* is empty, contains a forward slash,
    backslash, or the path-traversal component '..'.
    """
    if not session_id:
        raise ValueError("session_id must not be empty")
    if "/" in session_id:
        raise ValueError("session_id must not contain '/'")
    if "\\" in session_id:
        raise ValueError("session_id must not contain '\\'")
    if session_id == "..":
        raise ValueError("session_id must not be '..'")
    return state_root() / "sessions" / session_id

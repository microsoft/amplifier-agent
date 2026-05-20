"""XDG-compliant filesystem path helpers for amplifier-agent.

This module is pure path computation — it never creates directories.
All paths follow the XDG Base Directory Specification:
  https://specifications.freedesktop.org/basedir-spec/basedir-spec-latest.html
"""

from __future__ import annotations

import os
from pathlib import Path

from amplifier_agent_lib import __version__

APP_NAME = "amplifier-agent"


def _home() -> Path:
    """Return the current user's home directory."""
    return Path(os.environ.get("HOME", os.path.expanduser("~")))


def cache_root() -> Path:
    """Return the cache root for this app.

    Uses $XDG_CACHE_HOME/<APP_NAME> if set, else ~/.cache/<APP_NAME>.
    """
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else _home() / ".cache"
    return base / APP_NAME


def config_root() -> Path:
    """Return the config root for this app.

    Uses $XDG_CONFIG_HOME/<APP_NAME> if set, else ~/.config/<APP_NAME>.
    """
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else _home() / ".config"
    return base / APP_NAME


def state_root() -> Path:
    """Return the state root for this app.

    Uses $XDG_STATE_HOME/<APP_NAME> if set, else ~/.local/state/<APP_NAME>.
    """
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg) if xdg else _home() / ".local" / "state"
    return base / APP_NAME


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

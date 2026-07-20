"""E2E harness state file.

Persists the warm-DTU coordinates so the e2e CLI (`up`/`refresh`/`run`/`down`)
and pytest's session fixtures attach to the same live instance instead of
launching their own.

The state file lives under the system temp directory. It is ephemeral runtime
state -- a pointer to the current warm DTU -- not something to keep across
reboots or commit to the repo. A single fixed path is consistent with the
single-instance ``aa-e2e`` DTU/Gitea naming: one warm environment per machine.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

STATE_PATH = Path(tempfile.gettempdir()) / "amplifier-agent-e2e" / "state.json"


def read_state() -> dict[str, Any] | None:
    """Return the persisted state dict, or None if no state file exists."""
    if not STATE_PATH.exists():
        return None
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def write_state(state: dict[str, Any]) -> None:
    """Persist the state dict, creating the parent directory if needed."""
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def clear_state() -> None:
    """Remove the state file if it exists."""
    STATE_PATH.unlink(missing_ok=True)

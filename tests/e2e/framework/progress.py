"""Tiny timestamped progress logger for the e2e harness.

The DTU/Gitea steps are slow (container launch, installs) and otherwise silent, so
this prints ``[HH:MM:SS +elapsed] message`` to stdout and flushes immediately, giving
a live sense of what the harness is doing and how long each phase takes.
"""

from __future__ import annotations

import time

_START = time.monotonic()


def log(message: str) -> None:
    """Print a timestamped, elapsed-annotated progress line and flush."""
    elapsed = time.monotonic() - _START
    stamp = time.strftime("%H:%M:%S", time.localtime())
    print(f"[{stamp} +{elapsed:5.1f}s] {message}", flush=True)


def sub(message: str) -> None:
    """Print an indented detail line under the current step."""
    log(f"  - {message}")

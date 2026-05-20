"""TTY detection helpers for stdin and stdout.

Wraps os.isatty(fd) with defensive error handling so callers can safely
check whether stdin or stdout is attached to a terminal without worrying
about closed file descriptors or exotic platforms.
"""

from __future__ import annotations

import os


def is_stdin_tty() -> bool:
    """Return True if stdin (fd 0) is a TTY, False otherwise.

    Any OSError (e.g. closed fd, non-fd-backed stream) is swallowed and
    returns False.
    """
    try:
        return os.isatty(0)
    except OSError:
        return False


def is_stdout_tty() -> bool:
    """Return True if stdout (fd 1) is a TTY, False otherwise.

    Any OSError (e.g. closed fd, non-fd-backed stream) is swallowed and
    returns False.
    """
    try:
        return os.isatty(1)
    except OSError:
        return False

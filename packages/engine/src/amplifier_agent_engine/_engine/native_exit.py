"""Let native completion threads leave Python before the interpreter finalizes.

amplifier-core resolves each awaitable on a Tokio thread that attaches to Python and calls
``loop.call_soon_threadsafe``. That call releases the GIL while it writes the loop's wake-up
byte, and the waiting coroutine can finish first. If the program then exits, CPython 3.12
terminates the Tokio thread when it asks for the GIL again, and its Rust cleanup frees
Python objects without the GIL while finalization runs: the process dies with SIGSEGV after
all its work succeeded. Exit handlers run before finalization starts, so waiting there until
no untracked thread is still inside ``call_soon_threadsafe`` closes that window. Other untracked
threads, such as execnet's, are left alone so they never delay exit.
"""

from __future__ import annotations

import sys
import threading
import time
from types import FrameType

_WAIT_SECONDS = 1.0


def _completions_in_flight() -> bool:
    known = {thread.ident for thread in threading.enumerate()}
    for ident, frame in sys._current_frames().items():
        if ident in known:
            continue
        current: FrameType | None = frame
        while current is not None:
            if current.f_code.co_name == "call_soon_threadsafe":
                return True
            current = current.f_back
    return False


def wait_for_native_threads(timeout: float = _WAIT_SECONDS) -> None:
    deadline = time.monotonic() + timeout
    while _completions_in_flight() and time.monotonic() < deadline:
        time.sleep(0.001)

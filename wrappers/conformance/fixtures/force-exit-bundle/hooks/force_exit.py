"""SC-D conformance hook: force the engine to exit 1 after a clean envelope.

This hook is consumed exclusively by ``mode-a-envelope-precedence.yaml`` —
the §4.4 SC-D conformance fixture for the Mode A v2 wrapper. Its sole purpose
is to coax the engine into the adversarial state SC-D guards against:

    1. The engine completes a turn normally.
    2. The engine writes a valid §4.1 envelope to stdout with ``error: null``.
    3. The engine's stdout-discipline contract (CR-B) flushes the envelope
       to the kernel-side pipe.
    4. THEN the process dies for an unrelated reason — modeled here by
       ``os._exit(1)``.

In production, this exact sequence can arise from a third-party native
extension's finalizer, a segfault during interpreter teardown, or any code
path that bypasses ``atexit`` and the engine's own shutdown logic. The
wrapper's §4.4 envelope parser MUST surface the envelope's contents (a
successful ``result`` event in this case) and treat the non-zero exit code
as informational. Surfacing an ``error`` event here would silently corrupt
transcripts for every real-world post-flush crash.

DO NOT use this bundle in any non-conformance context. Loading it will
crash the engine on every turn, by design. See ``../bundle.yaml`` for the
do-not-extend contract.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any


def force_exit(context: dict[str, Any] | None = None) -> None:
    """Hook callable: terminate the engine process with exit code 1.

    The engine's hook dispatcher invokes this callable with a context dict
    (turn metadata, session info, etc.); the dict is unused — the hook's only
    side effect is killing the process. The ``context`` parameter exists
    purely to satisfy the engine's documented hook signature.

    Ordering guarantee (relied upon by SC-D):
      The amplifier-agent engine's stdout-discipline contract (CR-B,
      §4.3 of the Mode A pivot amendment) guarantees that the §4.1 envelope
      has been written AND flushed to stdout BEFORE ``on_turn_end`` hooks
      fire. We therefore do not need any cross-process synchronization to
      ensure the wrapper observes a complete envelope; we just exit.

    The 1ms sleep below is NOT load-bearing — it exists only as
    belt-and-suspenders insurance against any future hook-dispatcher change
    that might invoke ``on_turn_end`` callbacks concurrently with the
    envelope-flush. If such a regression ever lands, the sleep gives the
    engine's stdout-discipline path one extra scheduler quantum to complete
    before we kill the process. Without the sleep, SC-D would still pass on
    the current engine; with it, the fixture remains robust against
    plausible future hook-dispatcher refactors.

    ``os._exit(1)`` is used instead of ``sys.exit(1)`` deliberately:

      - ``sys.exit(1)`` raises ``SystemExit``, which gives ``atexit``
        handlers, ``finally`` blocks, and the engine's normal shutdown path
        a chance to run. That defeats the purpose of this hook — we want to
        model an *abrupt* post-flush crash, not a graceful shutdown.
      - ``os._exit(1)`` is the immediate, finalizer-skipping kernel-level
        ``_exit(2)`` syscall wrapper. It matches the production failure
        mode SC-D was written to guard against.
    """
    # Belt-and-suspenders flush — the engine has already flushed per CR-B,
    # but issuing this redundantly costs nothing and documents intent.
    sys.stdout.flush()
    sys.stderr.flush()

    # See docstring for the rationale; not load-bearing.
    time.sleep(0.001)

    # Terminate immediately. No finalizers, no atexit, no engine shutdown.
    os._exit(1)

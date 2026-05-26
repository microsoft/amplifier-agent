"""amplifier_agent_lib — mode-agnostic Amplifier agent engine library.

This package is transport-free: it never reads from stdin or writes to stdout
directly.  All I/O flows through ProtocolPoints injected at Engine.boot().

See docs/designs/aaa-v2-design-checkpoint.md §5 for the naming rationale and
the "Critical invariant" that this separation enables (two invocation modes,
one engine implementation).
"""

from __future__ import annotations

__version__ = "0.2.0"
__all__ = ["__version__"]

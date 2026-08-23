"""amplifier-agent HTTP face.

OpenAI Chat Completions-compatible HTTP server wrapping AmplifierSession.

POC scope:
- Slice 1: Stub server, hardcoded SSE response, no AmplifierSession.
- Slice 2: Real AmplifierSession with context.set_messages() seeding.
- Slice 3: Containment, keepalives, cancellation discipline.

See: amplifier-opencode-poc-plan.md
"""

from __future__ import annotations

# Bind amplifier_foundation's storage root into amplifier-agent's own tree
# before anything in this package imports amplifier_foundation.
#
# amplifier_agent_lib's own ``__init__`` performs the same bind, but this
# package does not import amplifier_agent_lib at package scope, so relying on
# that would make correctness depend on which submodule an embedder happens to
# import first.  ``bind()`` is idempotent, so doing it in both places costs
# nothing and removes the ordering hazard.
from amplifier_agent_lib.foundation_home import bind as _bind_foundation_home

_bind_foundation_home()

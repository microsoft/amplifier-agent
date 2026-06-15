"""amplifier-agent HTTP face.

OpenAI Chat Completions-compatible HTTP server wrapping AmplifierSession.

POC scope:
- Slice 1: Stub server, hardcoded SSE response, no AmplifierSession.
- Slice 2: Real AmplifierSession with context.set_messages() seeding.
- Slice 3: Containment, keepalives, cancellation discipline.

See: amplifier-opencode-poc-plan.md
"""

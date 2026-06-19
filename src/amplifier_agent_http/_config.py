"""Server configuration loaded from environment variables.

POC-grade: env-var only. Settings panel / config file is a v2 concern.
"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ServerConfig:
    """Server-side configuration."""

    api_key: str
    """Shared secret required in Authorization: Bearer <api_key>. POC default
    is a fixed string; production deployments should set
    AMPLIFIER_AGENT_HTTP_API_KEY."""

    model_id: str
    """The model id surfaced on GET /v1/models and accepted in chat-completions
    requests. POC ships exactly one model."""

    model_display_name: str
    """Human-readable model name (returned in `models.data[*].name` for hosts
    that read it; not part of strict OpenAI spec but harmless)."""

    workspace: str | None
    """Optional workspace override for this server's session bucket.

    Read from ``AMPLIFIER_AGENT_HTTP_WORKSPACE`` (HTTP-face-specific) or
    ``AMPLIFIER_AGENT_WORKSPACE`` (ecosystem-shared). When set, all sessions
    persist their context-intelligence events at
    ``~/.amplifier-agent/state/workspaces/<workspace>/sessions/<sid>/...``
    instead of the cwd-derived fallback (which produces unwieldy
    bundle-install-dir slugs).

    None means "fall back to ``persistence.derive_workspace_from_cwd``" --
    the same behaviour the CLI face uses when ``--workspace`` is omitted.

    POC: server-process scope only. Per-request workspace override (e.g. via
    ``payload.workspace`` or a custom header) is in the v2 backlog -- the
    context-intelligence hook reads from its own module config FIRST (Fix C
    seeding at lifespan), so a per-request override at the
    ``coordinator.config`` level (D5) would lose to the lifespan seed.
    Supporting it cleanly requires per-session mount-plan isolation."""


def load_config() -> ServerConfig:
    """Load ServerConfig from environment."""
    return ServerConfig(
        api_key=os.environ.get("AMPLIFIER_AGENT_HTTP_API_KEY", "local-dev-secret"),
        model_id=os.environ.get("AMPLIFIER_AGENT_HTTP_MODEL_ID", "amplifier"),
        model_display_name=os.environ.get("AMPLIFIER_AGENT_HTTP_MODEL_NAME", "Amplifier"),
        # Prefer the HTTP-face-specific env var when set; fall back to the
        # ecosystem-shared one (which the CLI also reads via
        # persistence.resolve_workspace). Empty / whitespace = unset.
        workspace=(
            os.environ.get("AMPLIFIER_AGENT_HTTP_WORKSPACE") or os.environ.get("AMPLIFIER_AGENT_WORKSPACE") or None
        ),
    )

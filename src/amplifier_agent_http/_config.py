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


def load_config() -> ServerConfig:
    """Load ServerConfig from environment."""
    return ServerConfig(
        api_key=os.environ.get("AMPLIFIER_AGENT_HTTP_API_KEY", "local-dev-secret"),
        model_id=os.environ.get("AMPLIFIER_AGENT_HTTP_MODEL_ID", "amplifier"),
        model_display_name=os.environ.get("AMPLIFIER_AGENT_HTTP_MODEL_NAME", "Amplifier"),
    )

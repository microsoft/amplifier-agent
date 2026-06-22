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

    host: str
    """Bind address for the HTTP server.

    Read from ``AMPLIFIER_AGENT_HTTP_BIND``. Defaults to ``127.0.0.1``.
    Written into the state file at startup so lifecycle commands
    (``serve status / stop / restart``) know the wire address without
    re-parsing CLI flags."""

    port: int
    """Bind port for the HTTP server.

    Read from ``AMPLIFIER_AGENT_HTTP_PORT``. Defaults to ``9099``.
    Written into the state file alongside ``host``."""

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

    host_config_path: str | None
    """Optional path to a host-config YAML/JSON file (the same file format
    ``amplifier-agent run --config`` consumes).

    Read from ``AMPLIFIER_AGENT_HTTP_CONFIG_PATH``. When set, the lifespan
    loads it via ``amplifier_agent_lib.config.load_config()`` and overlays
    the parsed ``host_config`` dict over ``prepared.mount_plan`` per the
    same D5 path used by the CLI's ``make_turn_handler``:

    - ``mcp.configPath`` -> sets ``AMPLIFIER_MCP_CONFIG`` env var
    - ``tools.*``, ``hooks.*``, ``providers.*`` -> ``merge_config`` overlay
      onto ``mount_plan["tools"|"hooks"|"providers"][*]["config"]``
    - ``approval.mode`` -> intentionally ignored by the HTTP face (which
      uses ``HttpAutoApprovalSystem``; approval is a per-call interactive
      decision with no human-in-the-loop in the HTTP turn model)

    None means "no overlay" -- the bundle's static config wins.

    The ``--config`` flag on ``amplifier-agent serve chat-completions``
    sets the env var before uvicorn starts so the lifespan can read it."""


def load_config() -> ServerConfig:
    """Load ServerConfig from environment."""
    raw_port = os.environ.get("AMPLIFIER_AGENT_HTTP_PORT", "9099")
    try:
        port = int(raw_port)
    except ValueError:
        port = 9099

    return ServerConfig(
        api_key=os.environ.get("AMPLIFIER_AGENT_HTTP_API_KEY", "local-dev-secret"),
        model_id=os.environ.get("AMPLIFIER_AGENT_HTTP_MODEL_ID", "amplifier"),
        model_display_name=os.environ.get("AMPLIFIER_AGENT_HTTP_MODEL_NAME", "Amplifier"),
        host=os.environ.get("AMPLIFIER_AGENT_HTTP_BIND", "127.0.0.1"),
        port=port,
        # Prefer the HTTP-face-specific env var when set; fall back to the
        # ecosystem-shared one (which the CLI also reads via
        # persistence.resolve_workspace). Empty / whitespace = unset.
        workspace=(
            os.environ.get("AMPLIFIER_AGENT_HTTP_WORKSPACE") or os.environ.get("AMPLIFIER_AGENT_WORKSPACE") or None
        ),
        # Path to the host-config YAML/JSON file. Set by the CLI's
        # ``--config`` flag (via the env var) or by env directly. None means
        # no overlay -- bundle.md's static config wins. Same file format as
        # ``amplifier-agent run --config``.
        host_config_path=os.environ.get("AMPLIFIER_AGENT_HTTP_CONFIG_PATH") or None,
    )

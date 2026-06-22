"""``amplifier-agent serve <wire>`` -- HTTP server lifecycle commands.

This subgroup folds in what used to live in the now-removed
``amplifier-agent-http`` console script. Goal: one CLI, one config surface.
The same ``--workspace`` slug, the same provider/credential plumbing
applies whether the user runs an in-process turn (``run``) or stands up a
long-running wire face (``serve <wire>``).

``serve`` is a subgroup rather than a flat command so additional wire faces
can be added without restructuring the CLI surface. Today only
``chat-completions`` exists; future wires (e.g. ``responses``, ``acp``,
``mcp``) plug in as sibling commands.
"""

from __future__ import annotations

import logging
import os
import signal
from pathlib import Path

import click
import uvicorn

from amplifier_agent_cli.admin.serve_lifecycle import (
    remove_state_file,
    restart_command,
    status_command,
    stop_command,
)


@click.group(name="serve")
def serve_group() -> None:
    """Start a wire face for amplifier-agent."""


# Register lifecycle subcommands on the group.
serve_group.add_command(status_command, name="status")
serve_group.add_command(stop_command, name="stop")
serve_group.add_command(restart_command, name="restart")


@serve_group.command(name="chat-completions")
@click.option(
    "--bind",
    "host",
    default="127.0.0.1",
    show_default=True,
    metavar="HOST",
    help=(
        "Bind address. Defaults to localhost. Only set to 0.0.0.0 if you "
        "understand the auth/exposure tradeoff -- the POC only ships a "
        "shared-secret bearer check."
    ),
)
@click.option(
    "--port",
    default=9099,
    show_default=True,
    type=int,
    help="Bind port.",
)
@click.option(
    "--api-key",
    default=None,
    metavar="KEY",
    help=(
        "Shared secret required in ``Authorization: Bearer <key>``. Defaults "
        "to ``$AMPLIFIER_AGENT_HTTP_API_KEY`` if set, else ``local-dev-secret``."
    ),
)
@click.option(
    "--workspace",
    default=None,
    metavar="SLUG",
    help=(
        "Workspace slug for session bucketing under "
        "``~/.amplifier-agent/state/workspaces/<slug>/``. Defaults to "
        "``$AMPLIFIER_AGENT_HTTP_WORKSPACE`` > ``$AMPLIFIER_AGENT_WORKSPACE`` "
        "> cwd-derived. Matches the ``run`` command's ``--workspace`` flag."
    ),
)
@click.option(
    "--model-id",
    default=None,
    metavar="ID",
    help=(
        "The model id surfaced on ``GET /v1/models``. Defaults to "
        "``$AMPLIFIER_AGENT_HTTP_MODEL_ID`` if set, else ``amplifier``."
    ),
)
@click.option(
    "--config",
    "config_path",
    default=None,
    metavar="PATH",
    type=click.Path(),
    help=(
        "Path to a host-config JSON file -- the same file format "
        "``amplifier-agent run --config`` consumes. JSON only; the engine "
        "loader rejects YAML. Schema is closed at the top level (D7); "
        "valid keys are ``mcp``, ``approval``, ``provider``, "
        "``allowProtocolSkew``, ``skills``. The lifespan applies "
        "``mcp.configPath`` to the ``AMPLIFIER_MCP_CONFIG`` env var and "
        "calls ``merge_config`` to overlay the remaining keys onto the "
        "matching bundle modules. ``approval`` is intentionally ignored "
        "by the HTTP face (auto-allow only). Path is resolved relative "
        "to your cwd before being passed to the server."
    ),
)
@click.option(
    "--log-level",
    default="info",
    show_default=True,
    type=click.Choice(["debug", "info", "warning", "error", "critical"]),
    help="uvicorn + amplifier-agent log level.",
)
def chat_completions(
    host: str,
    port: int,
    api_key: str | None,
    workspace: str | None,
    model_id: str | None,
    config_path: str | None,
    log_level: str,
) -> None:
    """Start the OpenAI Chat Completions wire face.

    Runs the amplifier-agent HTTP server with the OpenAI Chat Completions
    surface at ``POST /v1/chat/completions`` and ``GET /v1/models``.
    Compatible with opencode, OpenWebUI, LiteLLM, and any other client
    that speaks the OpenAI streaming chat-completions protocol.

    The server runs single-process / single-worker by design -- it wraps
    one ``PreparedBundle`` and one in-process session loop. For multi-user
    deployments you'd front it with a reverse proxy and run multiple
    instances; per-instance state isolation is handled by the workspace
    slug.
    """
    # Resolve env-var fallbacks. Flags win when explicitly provided.
    # We write into the process environment because the HTTP face's
    # ``_config.load_config()`` reads from env. Keeping that contract in
    # place (rather than wiring kwargs all the way through the FastAPI
    # lifespan) means env-only deployments stay supported.
    if api_key is not None:
        os.environ["AMPLIFIER_AGENT_HTTP_API_KEY"] = api_key
    if workspace is not None:
        os.environ["AMPLIFIER_AGENT_HTTP_WORKSPACE"] = workspace
    if model_id is not None:
        os.environ["AMPLIFIER_AGENT_HTTP_MODEL_ID"] = model_id
    if config_path is not None:
        # Resolve relative to cwd, expand ~, fail-fast if the file is not
        # present. ``run --config`` does the same -- catching a typo here
        # is much friendlier than a stack trace inside the FastAPI lifespan.
        resolved_config_path = Path(config_path).expanduser().resolve()
        if not resolved_config_path.is_file():
            raise click.UsageError(f"--config path does not exist or is not a file: {resolved_config_path}")
        os.environ["AMPLIFIER_AGENT_HTTP_CONFIG_PATH"] = str(resolved_config_path)

    # Expose host and port via env so load_config() can stash them in the
    # state file (which the lifecycle commands read to know the wire address).
    os.environ["AMPLIFIER_AGENT_HTTP_BIND"] = host
    os.environ["AMPLIFIER_AGENT_HTTP_PORT"] = str(port)

    # Resolve the values that will actually be used, so we can echo them
    # to stderr (handy for opencode.json setup).
    resolved_api_key = os.environ.get("AMPLIFIER_AGENT_HTTP_API_KEY", "local-dev-secret")
    resolved_workspace = (
        os.environ.get("AMPLIFIER_AGENT_HTTP_WORKSPACE")
        or os.environ.get("AMPLIFIER_AGENT_WORKSPACE")
        or "(cwd-derived)"
    )
    resolved_model_id = os.environ.get("AMPLIFIER_AGENT_HTTP_MODEL_ID", "amplifier")
    resolved_config = os.environ.get("AMPLIFIER_AGENT_HTTP_CONFIG_PATH") or "(none)"

    logging.basicConfig(level=getattr(logging, log_level.upper()))

    # Stderr-only banner. Never put credentials on stdout (a piped client
    # parsing stdout would be poisoned by it).
    click.echo(f"amplifier-agent chat-completions listening on http://{host}:{port}", err=True)
    click.echo(f"  API key:   {resolved_api_key}", err=True)
    click.echo(f"  Model:     {resolved_model_id}", err=True)
    click.echo(f"  Workspace: {resolved_workspace}", err=True)
    click.echo(f"  Config:    {resolved_config}", err=True)

    # Belt-and-suspenders: remove the state file on SIGTERM/SIGINT from the
    # outer process context. uvicorn handles the actual shutdown sequence;
    # the lifespan's finally block is the primary cleanup path.  These
    # handlers ensure cleanup even if the lifespan teardown is skipped (e.g.
    # when the server is killed before lifespan has finished setting up).
    def _cleanup_state(_signum: int, _frame: object) -> None:
        remove_state_file()

    signal.signal(signal.SIGTERM, _cleanup_state)
    signal.signal(signal.SIGINT, _cleanup_state)

    uvicorn.run(
        "amplifier_agent_http.app:app",
        host=host,
        port=port,
        log_level=log_level,
        # Single-user local: no reload, single worker. Multi-user
        # deployments need their own process supervisor anyway.
        reload=False,
        workers=1,
    )

"""Fixtures for the raw-capture suite: seed the debug host-config into the DTU.

The CLI face just needs the config file on disk. The HTTP face additionally needs a
server started WITH that config, because the shared ``server`` fixture in
``tests/e2e/conftest.py`` starts ``serve chat-completions`` with no ``--config`` at
all. A second server on its own port keeps the two faces independent.

A third server (``raw_server_env``) reaches the same config through
``$AMPLIFIER_AGENT_CONFIG`` instead of ``--config``. That is the route a host which
exports the variable once and spawns the server as a child takes, and it was silently
inert until the HTTP face learned the fallback.
"""

from __future__ import annotations

import json
import shlex
import time
from collections.abc import Generator
from pathlib import Path

import pytest
from framework import dtu, ports

FIXTURES = Path(__file__).parent / "fixtures"

# In-DTU paths and ports.
CFG_RAW = "/root/e2e/host-config-raw.json"
# Owned by this suite; see framework/ports.py for the full allocation and for why
# these numbers must not be duplicated across suites.
RAW_PORT = ports.RAW_CAPTURE_PORT
RAW_ENV_PORT = ports.RAW_CAPTURE_ENV_PORT
RAW_TOKEN = "local-dev-secret"

# Kill only OUR server, and never this pkill itself -- see ports.self_safe_pkill.
_RAW_PKILL = ports.self_safe_pkill(RAW_PORT)
_RAW_ENV_PKILL = ports.self_safe_pkill(RAW_ENV_PORT)

# Family of the model host-config-raw.json selects ("claude-sonnet-5"), so the HTTP
# face runs the same model as the CLI face and a difference cannot be explained by
# model choice.
_CLI_MODEL_HINT = "sonnet"


def _await_ready(dtu_id: str, *, base_url: str, log_path: str, failure_hint: str) -> None:
    """Poll ``/v1/models`` until it answers 200, or fail with the server's own log.

    Shared by both servers this suite starts so they wait identically. A difference
    in readiness handling would be a difference between the two config routes that
    has nothing to do with the contract under test.
    """
    probe = f"curl -s -o /dev/null -w '%{{http_code}}' -H 'Authorization: Bearer {RAW_TOKEN}' {base_url}/v1/models"
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        result = dtu.exec_json(dtu_id, ["bash", "-lc", probe])
        if result.get("exit_code") == 0 and result.get("stdout", "").strip() == "200":
            return
        time.sleep(3)

    log = dtu.exec_json(dtu_id, ["bash", "-lc", f"cat {shlex.quote(log_path)} 2>/dev/null || true"])
    pytest.fail(f"{failure_hint}\n{log_path}:\n{log.get('stdout', '')}")


def _resolve_model_id(dtu_id: str, server: dict[str, str]) -> str:
    """Return a served model id from ``GET /v1/models`` on ``server``."""
    cmd = f"curl -s -H 'Authorization: Bearer {server['token']}' {server['base_url']}/v1/models"
    result = dtu.exec_json(dtu_id, ["bash", "-lc", cmd])
    assert result.get("exit_code") == 0, f"/v1/models failed: {result.get('stderr')}"
    data = json.loads(result["stdout"])
    models = data.get("data") or []
    assert models, f"no served models: {data}"

    ids = [m["id"] for m in models]
    for candidate in ids:
        if _CLI_MODEL_HINT in candidate:
            return candidate
    return ids[0]


@pytest.fixture(scope="session")
def raw_config(dtu_id: str) -> str:
    """Push the ``debug.rawLlmPayloads`` host-config into the DTU; return its in-DTU path."""
    dtu.push_file(dtu_id, str(FIXTURES / "host-config-raw.json"), CFG_RAW)
    return CFG_RAW


@pytest.fixture(scope="session")
def raw_server(dtu_id: str, raw_config: str) -> Generator[dict[str, str], None, None]:
    """Start ``serve chat-completions --config <raw_config>`` inside the DTU on RAW_PORT.

    This is the path amplifier-app-opencode takes: it spawns the agent with
    ``--config``, which becomes ``AMPLIFIER_AGENT_HTTP_CONFIG_PATH``. Yields base_url
    and bearer token.
    """
    base_url = f"http://localhost:{RAW_PORT}"

    start = (
        "mkdir -p /root/e2e && "
        "nohup amplifier-agent serve chat-completions "
        f"--bind 0.0.0.0 --port {RAW_PORT} --api-key {RAW_TOKEN} "
        f"--config {shlex.quote(raw_config)} "
        f">/root/e2e/serve-raw.log 2>&1 &"
    )
    dtu.exec_json(dtu_id, ["bash", "-lc", start])

    _await_ready(
        dtu_id,
        base_url=base_url,
        log_path="/root/e2e/serve-raw.log",
        failure_hint=(
            "amplifier-agent server (raw config) did not become ready in the DTU.\n"
            "Until debug.rawLlmPayloads is a valid host-config key this is the expected\n"
            "failure: the loader rejects the config with config_unknown_key at startup."
        ),
    )

    try:
        yield {"base_url": base_url, "token": RAW_TOKEN}
    finally:
        # Scope the kill to this port so the other suites' servers survive.
        dtu.exec_json(dtu_id, ["bash", "-lc", _RAW_PKILL])


@pytest.fixture(scope="session")
def raw_server_env(dtu_id: str, raw_config: str) -> Generator[dict[str, str], None, None]:
    """Start ``serve chat-completions`` with NO ``--config``, only ``$AMPLIFIER_AGENT_CONFIG``.

    Differs from ``raw_server`` in exactly one thing: how the host-config path reaches
    the process. This is the route a host takes when it exports the variable once for
    ``run`` and spawns ``serve`` as a child that inherits the environment, which is
    what ``amplifier-app-opencode`` does (``subprocess.Popen`` with no ``env=``).

    The variable is assigned inline on the launch command rather than exported. Every
    suite shares one DTU, and the shared ``server`` fixture in ``tests/e2e/conftest.py``
    starts ``serve`` with no ``--config`` at all -- an exported value would reconfigure
    that server too, and silently change what every other HTTP case is testing.
    """
    base_url = f"http://localhost:{RAW_ENV_PORT}"

    start = (
        "mkdir -p /root/e2e && "
        f"AMPLIFIER_AGENT_CONFIG={shlex.quote(raw_config)} "
        "nohup amplifier-agent serve chat-completions "
        f"--bind 0.0.0.0 --port {RAW_ENV_PORT} --api-key {RAW_TOKEN} "
        f">/root/e2e/serve-raw-env.log 2>&1 &"
    )
    dtu.exec_json(dtu_id, ["bash", "-lc", start])

    _await_ready(
        dtu_id,
        base_url=base_url,
        log_path="/root/e2e/serve-raw-env.log",
        failure_hint=(
            "amplifier-agent server (env-var config) did not become ready in the DTU.\n"
            "A config_unreadable or config_unknown_key failure here means the HTTP face\n"
            "now reaches the loader through $AMPLIFIER_AGENT_CONFIG but rejects the file."
        ),
    )

    try:
        yield {"base_url": base_url, "token": RAW_TOKEN}
    finally:
        # Scope the kill to this port so the other suites' servers survive.
        dtu.exec_json(dtu_id, ["bash", "-lc", _RAW_ENV_PKILL])


@pytest.fixture(scope="session")
def raw_model_id(dtu_id: str, raw_server: dict[str, str]) -> str:
    """Resolve a served model id at runtime from GET /v1/models on the raw-config server."""
    return _resolve_model_id(dtu_id, raw_server)


@pytest.fixture(scope="session")
def raw_env_model_id(dtu_id: str, raw_server_env: dict[str, str]) -> str:
    """Resolve a served model id from the env-var-configured server.

    Resolved separately from ``raw_model_id`` rather than reused: the served model
    list is derived from the host config each server loaded, so borrowing the other
    server's id would assume the very thing this suite is proving.
    """
    return _resolve_model_id(dtu_id, raw_server_env)

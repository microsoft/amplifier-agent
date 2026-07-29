"""Fixtures for the raw-capture suite: seed the debug host-config into the DTU.

The CLI face just needs the config file on disk. The HTTP face additionally needs a
server started WITH that config, because the shared ``server`` fixture in
``tests/e2e/conftest.py`` starts ``serve chat-completions`` with no ``--config`` at
all. A second server on its own port keeps the two faces independent.
"""

from __future__ import annotations

import json
import shlex
import time
from collections.abc import Generator
from pathlib import Path

import pytest
from framework import dtu

FIXTURES = Path(__file__).parent / "fixtures"

# In-DTU paths and ports.
CFG_RAW = "/root/e2e/host-config-raw.json"
RAW_PORT = 9098  # distinct from the shared `server` fixture's 9099
RAW_TOKEN = "local-dev-secret"

# Family of the model host-config-raw.json selects ("claude-sonnet-5"), so the HTTP
# face runs the same model as the CLI face and a difference cannot be explained by
# model choice.
_CLI_MODEL_HINT = "sonnet"


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

    probe = f"curl -s -o /dev/null -w '%{{http_code}}' -H 'Authorization: Bearer {RAW_TOKEN}' {base_url}/v1/models"
    deadline = time.monotonic() + 60
    ready = False
    while time.monotonic() < deadline:
        result = dtu.exec_json(dtu_id, ["bash", "-lc", probe])
        if result.get("exit_code") == 0 and result.get("stdout", "").strip() == "200":
            ready = True
            break
        time.sleep(3)

    if not ready:
        log = dtu.exec_json(dtu_id, ["bash", "-lc", "cat /root/e2e/serve-raw.log 2>/dev/null || true"])
        pytest.fail(
            "amplifier-agent server (raw config) did not become ready in the DTU.\n"
            "Until debug.rawLlmPayloads is a valid host-config key this is the expected\n"
            "failure: the loader rejects the config with config_unknown_key at startup.\n"
            f"serve-raw.log:\n{log.get('stdout', '')}"
        )

    try:
        yield {"base_url": base_url, "token": RAW_TOKEN}
    finally:
        # Scope the kill to this port so the shared `server` fixture on 9099 survives.
        dtu.exec_json(dtu_id, ["bash", "-lc", f"pkill -f 'amplifier-agent serve.*{RAW_PORT}' || true"])


@pytest.fixture(scope="session")
def raw_model_id(dtu_id: str, raw_server: dict[str, str]) -> str:
    """Resolve a served model id at runtime from GET /v1/models on the raw-config server."""
    cmd = f"curl -s -H 'Authorization: Bearer {raw_server['token']}' {raw_server['base_url']}/v1/models"
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

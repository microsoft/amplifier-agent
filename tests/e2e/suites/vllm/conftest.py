"""Suite fixtures: endpoint gating, model discovery, and host-config seeding.

Like ``gemini`` and unlike ``github_copilot``, this suite SKIPS rather than failing
when it is not configured. It goes further, though: gemini skips only when its key is
absent, whereas this suite also skips when the endpoint is *set but unreachable*.

The difference is what the two variables mean. A missing API key is a setup mistake
with one cause. A vLLM endpoint is a server someone has to be running -- on their own
hardware, with a GPU, hosting a model they chose. It will be down far more often than
it is misconfigured, and "the operator's server is not up right now" is not a defect
in amplifier-agent. Failing the default ``cli.py run`` for that would make the suite a
report on the state of someone's GPU box.

So: no endpoint, no run, no failure. Unreachable endpoint, no run, no failure. The
skip messages say exactly which of the two happened and what to do about it.
"""

from __future__ import annotations

import json
import shlex
import tempfile
from pathlib import Path
from typing import Any

import pytest
from framework import dtu

from suites.vllm.cases import CONFIG_PATH, CREDENTIAL_VAR, DTU_DIR

#: Probes for the variable's presence inside the container without echoing its value.
#: ``bash -lc`` matters: the export lives in /etc/profile.d/zz-vllm.sh, which only a
#: login shell sources.
_PRESENCE_PROBE = f'if [ -n "${CREDENTIAL_VAR}" ]; then echo endpoint=present; else echo endpoint=missing; fi'

_SKIP_UNSET = (
    f"{CREDENTIAL_VAR} is not set inside the DTU, so the vllm suite is skipped. "
    f"To run it: start a vLLM server, then export {CREDENTIAL_VAR} (e.g. "
    f"http://localhost:8007/v1) on the host and re-provision "
    f"(`uv run python tests/e2e/framework/cli.py up`). Optionally also export "
    f"VLLM_MODEL to pin a model id, and VLLM_API_KEY if your server requires auth. "
    f"The value is baked into the container at launch, so exporting it against an "
    f"already-running DTU has no effect. `localhost` is rewritten to the bridge "
    f"gateway IP automatically -- write the URL as you would use it on the host."
)


def _skip_unreachable(base_url: str, detail: str) -> str:
    return (
        f"{CREDENTIAL_VAR} is set inside the DTU ({base_url}) but the server did not "
        f"answer GET {base_url}/models from inside the container, so the vllm suite is "
        f"skipped rather than failed: an unreachable endpoint is a fact about the "
        f"operator's server, not a defect in amplifier-agent.\n"
        f"Check that the vLLM server is running and bound to 0.0.0.0 (not 127.0.0.1), "
        f"and that the host firewall permits traffic from the container bridge.\n"
        f"Probe detail: {detail}"
    )


@pytest.fixture(scope="session")
def vllm_endpoint(dtu_id: str) -> dict[str, Any]:
    """Skip unless a vLLM server is configured AND answering inside the DTU.

    Both checks run inside the container rather than on the host, because that is
    where they matter: the host can have the variable exported and the DTU still not
    have it if the container predates the export, and the host can reach
    ``localhost:8007`` while the container cannot reach the gateway IP at all.

    Returns the resolved ``base_url`` and the model ids the server advertises.
    """
    presence = dtu.exec_json(dtu_id, ["bash", "-lc", _PRESENCE_PROBE])
    if "endpoint=present" not in str(presence.get("stdout", "")):
        pytest.skip(_SKIP_UNSET)

    # Read back the value the container actually holds -- the post-rewrite gateway
    # URL, which is not what the host exported and is worth reporting in failures.
    resolved = dtu.exec_json(dtu_id, ["bash", "-lc", f'printf "%s" "${CREDENTIAL_VAR}"'])
    base_url = str(resolved.get("stdout", "")).strip()

    # One request that doubles as the reachability probe and the model discovery.
    # --fail-with-body so a 4xx/5xx is a non-zero exit rather than a body we would
    # then have to parse to notice the failure.
    probe = dtu.exec_json(
        dtu_id,
        ["bash", "-lc", f"curl -sS --fail-with-body --max-time 15 {shlex.quote(base_url)}/models"],
    )
    if probe.get("exit_code") != 0:
        pytest.skip(_skip_unreachable(base_url, f"exit={probe.get('exit_code')} {probe.get('stderr', '')}".strip()))

    try:
        payload = json.loads(str(probe.get("stdout", "")))
    except json.JSONDecodeError as exc:
        pytest.skip(_skip_unreachable(base_url, f"/models did not return JSON: {exc}"))

    model_ids = [m["id"] for m in payload.get("data", []) if isinstance(m, dict) and m.get("id")]
    if not model_ids:
        pytest.skip(_skip_unreachable(base_url, f"/models returned no model ids: {payload!r}"))

    return {"base_url": base_url, "model_ids": model_ids}


@pytest.fixture(scope="session")
def vllm_model(dtu_id: str, vllm_endpoint: dict[str, Any]) -> str:
    """The model id to pin in the host config.

    ``VLLM_MODEL`` wins when the operator set it. Otherwise the first id the server
    advertises is used, which is the right default for a single-model vLLM process
    (the common case -- vLLM serves one model per instance).

    Falling back to discovery rather than to the provider module's own default is
    deliberate. The module defaults to a model id that has no reason to exist on an
    arbitrary self-hosted server, so leaving ``default_model`` unset would turn a
    perfectly good setup into a confusing 404 from the operator's own server.
    """
    requested = str(dtu.exec_json(dtu_id, ["bash", "-lc", 'printf "%s" "$VLLM_MODEL"']).get("stdout", "")).strip()
    if requested:
        return requested
    return str(vllm_endpoint["model_ids"][0])


@pytest.fixture(scope="session")
def vllm_config(dtu_id: str, vllm_endpoint: dict[str, Any], vllm_model: str) -> str:
    """Render the vllm host config, push it into the DTU, return its in-container path.

    The config is generated rather than a static fixture file because the model id is
    not knowable ahead of time -- it is whatever the operator's server is serving.

    ``base_url`` is deliberately NOT written here. ``VLLM_BASE_URL`` already resolves
    from the environment, and ``_reassert_protected_keys`` re-asserts env-resolved
    credential fields on top of any ``provider.config`` overlay, so a ``base_url`` in
    this file would be silently overwritten. Writing it would create the false
    impression that the host config is what points at the server.
    """
    config = {
        "provider": {"module": "vllm", "config": {"default_model": vllm_model}},
        "approval": {"mode": "yes"},
    }

    with tempfile.TemporaryDirectory(prefix="aa-e2e-vllm-") as tmp:
        local = Path(tmp) / "host-config-vllm.json"
        local.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        dtu.exec_json(dtu_id, ["mkdir", "-p", DTU_DIR])
        dtu.push_file(dtu_id, str(local), CONFIG_PATH)

    return CONFIG_PATH

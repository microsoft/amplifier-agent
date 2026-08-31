"""The amplifier-agent adapter.

Drives amplifier-agent's own CLI non-interactively inside a DTU, the same
contract deep-swe's amplifier-agent runner uses: a host-config JSON pinning
approval mode and provider/model, a session id, and the prompt delivered via
`$(cat ...)` rather than string interpolation.

The bake profile (profiles/agents/amplifier-agent.bake.yaml) installs the CLI
with no provider, model, or secret baked in -- those are per-run choices, so
they get written here, at trial time, instead.
"""

from __future__ import annotations

import json
import os
import shlex
import uuid

from jobbench import images
from jobbench.agents.base import Adapter, AdapterError, register
from jobbench.dtu import DTU
from jobbench.providers import OPENAI, REASONING_EFFORT, provider_family

DEFAULT_MODEL = "claude-sonnet-5"
HOST_CONFIG_PATH = "/root/host-config.json"

# Defense-in-depth PATH write, matching the precedent in
# agents/amplifier-agent-local/install.yaml: the DTU CLI's own login shell
# already sources /etc/profile.d/dtu-env.sh (which includes /root/.local/bin,
# where `uv tool install` places amplifier-agent), but the agent under test
# should not depend on that engine behavior to find its own binary.
_PATH_PROFILE_SCRIPT = "/etc/profile.d/jobbench-amplifier-agent.sh"


@register
class AmplifierAgentAdapter(Adapter):
    name = "amplifier-agent"
    image_alias = images.agent_alias("amplifier-agent")
    session_dirs = ("/root/.amplifier-agent/state/workspaces",)
    metrics_source = "events"

    def __init__(self) -> None:
        # Generated once per adapter instance (trial.py calls agents.get()
        # fresh per trial) so `command()` can embed it without taking
        # arguments -- the contract has no other place to source it from.
        self._session_id = uuid.uuid4().hex[:12]

    async def configure(self, dtu: DTU, *, model: str) -> None:
        """Write the per-trial host-config and guarantee PATH.

        The provider module is derived from the model (see
        jobbench.providers), so the same adapter serves both families.

        No API key is written here -- the family's key env var reaches the
        container through the launch profile's `passthrough.services`, so
        it never touches disk in plaintext under our control.
        """
        family = provider_family(model)
        provider_config: dict[str, str] = {"default_model": model}
        if family.name == OPENAI:
            # provider-openai would otherwise fall back to the OpenAI SDK's
            # own env/default resolution, which is invisible in the captured
            # config and can silently point this arm at a different endpoint
            # than the other arms. Pin it in the config instead.
            #
            # The base URL is not a secret (unlike the API key, which still
            # only ever arrives via passthrough), so resolving it host-side
            # is safe. Required, not defaulted: the host must already have it
            # set for passthrough to forward it at all.
            base_url = os.environ.get(family.base_url_env)
            if not base_url:
                raise AdapterError(
                    f"amplifier-agent configure failed: {family.base_url_env} is not set on "
                    f"the host, so model {model!r} has no endpoint to target"
                )
            provider_config["base_url"] = base_url
            # Benchmark-wide pin (jobbench.providers.REASONING_EFFORT).
            # provider-openai validates this at mount; left unset, the model's
            # own default effort applies and would differ from the other arms.
            provider_config["reasoning_effort"] = REASONING_EFFORT
        config = {
            "approval": {"mode": "yes"},
            "provider": {"module": family.agent_module, "config": provider_config},
        }
        payload = json.dumps(config)
        script = (
            f"printf %s {shlex.quote(payload)} > {HOST_CONFIG_PATH} && "
            f"printf 'export PATH=\"/root/.local/bin:$PATH\"\\n' > {_PATH_PROFILE_SCRIPT}"
        )
        result = await dtu.exec_cmd(["bash", "-c", script])
        if result.returncode != 0:
            raise AdapterError(
                f"amplifier-agent configure failed (exit {result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )

    def command(self) -> list[str]:
        """argv equivalent of:

            cd /workspace && amplifier-agent run -y --config /root/host-config.json \\
              --session-id <session-id> --output json "$(cat /workspace/prompt.txt)"

        Run through `bash -c` (not `-lc`) because it needs shell constructs
        (`&&`, `$(...)`) in one command; the DTU CLI's `exec` already wraps
        every command in an outer login shell, so this is not a double wrap.
        The prompt is substituted via `$(cat ...)` inside that single quoted
        script, never interpolated into the Python string -- task prompts
        contain quotes and newlines that would otherwise corrupt the argv.
        """
        script = (
            "cd /workspace && amplifier-agent run -y "
            f"--config {HOST_CONFIG_PATH} --session-id {self._session_id} "
            '--output json "$(cat /workspace/prompt.txt)"'
        )
        return ["bash", "-c", script]


__all__ = ["DEFAULT_MODEL", "AmplifierAgentAdapter"]

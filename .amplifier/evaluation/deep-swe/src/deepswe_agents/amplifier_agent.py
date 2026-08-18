"""amplifier-agent standalone CLI, driven directly."""

from __future__ import annotations

import json
import shlex
from typing import Any

from pier.environments.base import BaseEnvironment
from pier.models.agent.install import InstallStep

from deepswe_agents.base import (
    DEFAULT_AMPLIFIER_AGENT_REF,
    UV_PRELUDE,
    AmplifierBaseAgent,
    _as_bool,
)

HOST_CONFIG_PATH = "/root/host-config.json"

# Explicit session id. WITHOUT `--session-id` the runtime mints a telemetry-only
# `ephemeral-<hex>` id and writes NO persistence at all -- no transcript.jsonl,
# no metadata.json, no audits/. A fixed value is safe and deterministic: every
# trial gets a fresh container. `--fresh` is deliberately NOT passed -- it is a
# no-op in a fresh container and would destroy the prior trajectory if pier ever
# retried in place.
SESSION_ID = "deepswe-trial"


class AmplifierAgent(AmplifierBaseAgent):
    LOCAL_SOURCE_PACKAGE = "amplifier-agent"
    VERSION_BINARY = "amplifier-agent"

    # Session state (events.jsonl with per-response token usage and cost) lives
    # under the agent's default home. Pulled to <trial>/agent/sessions/ so the
    # metrics pass can read it host-side.
    SESSION_DIRS = ("/root/.amplifier-agent/state/workspaces",)

    def __init__(
        self,
        *args: Any,
        amplifier_agent_ref: str = DEFAULT_AMPLIFIER_AGENT_REF,
        raw_llm_payloads: Any = False,
        **kwargs: Any,
    ):
        super().__init__(*args, **kwargs)
        self._amplifier_agent_ref = amplifier_agent_ref
        # Reachable as `--ak raw_llm_payloads=true`; pier hands it over as a
        # string, hence the coercion rather than a bare truthiness test.
        self._raw_llm_payloads = _as_bool(raw_llm_payloads)

    @staticmethod
    def name() -> str:
        return "amplifier-agent"

    async def setup(self, environment: BaseEnvironment) -> None:
        await super().setup(environment)
        # Always state the capture mode: a trial log must never be ambiguous
        # about whether its events.jsonl carries full prompts.
        self.logger.info(
            "[%s] raw LLM payload capture: %s",
            self.name(),
            "ON (debug.rawLlmPayloads)" if self._raw_llm_payloads else "off",
        )

    def _host_config(self) -> str:
        config: dict[str, Any] = {
            "approval": {"mode": "yes"},
            "provider": {
                "module": "anthropic",
                "config": {"default_model": self.model},
            },
        }
        if self._raw_llm_payloads:
            # Adds `data.raw` (full messages/system/tools/model/max_tokens) to
            # every `llm:request` event. Must be a real JSON boolean -- a string
            # is rejected by the runtime. OPT-IN because it multiplies the size
            # of events.jsonl on a run that makes hundreds of LLM calls.
            config["debug"] = {"rawLlmPayloads": True}
        return json.dumps(config)

    def agent_install_steps(self) -> list[InstallStep]:
        return [
            *UV_PRELUDE,
            InstallStep(
                user="root",
                run=(
                    'export PATH="$HOME/.local/bin:$PATH"\n'
                    f"uv tool install --reinstall --force --from "
                    f'"{self._amplifier_agent_ref}" amplifier-agent\n'
                    "amplifier-agent-post-install || true"
                ),
            ),
            InstallStep(
                user="root",
                run=f"echo {shlex.quote(self._host_config())} > {HOST_CONFIG_PATH}",
            ),
            InstallStep(
                user="root",
                run='export PATH="$HOME/.local/bin:$PATH"; amplifier-agent --version',
            ),
        ]

    def run_command(self, instruction_path: str) -> str:
        # `--session-id` is what makes the run persist a trajectory at all (see
        # SESSION_ID). `--output json` appends the final envelope -- including
        # metadata.durationMs -- to agent.log; it does NOT suppress the
        # human-readable `[usage]` lines, so nothing is lost by adding it.
        #
        # The `--` before the prompt is load-bearing: without it, any instruction
        # whose text begins with `-` is parsed as a flag and the CLI exits with a
        # usage error before the agent ever runs.
        return (
            f"amplifier-agent run -y --config {HOST_CONFIG_PATH} "
            f"--session-id {shlex.quote(SESSION_ID)} --output json "
            f'-- "$(cat {instruction_path})"'
        )

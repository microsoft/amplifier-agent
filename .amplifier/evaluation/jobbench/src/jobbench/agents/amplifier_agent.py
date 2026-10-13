"""The amplifier-agent adapter, driving the agent through its Python binding.

The bake profile (profiles/agents/amplifier-agent.bake.yaml) installs the binding and its engine
into /opt/amplifier-agent so the image carries every dependency. `configure` reinstalls both at
the run's ref, then pushes `amplifier-agent-driver` (amplifier_agent_driver/, kept byte-identical
to deep-swe's copy). The driver holds one durable session: the task turn, then a recovery turn
whenever a tool outcome came back `unknown`.
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Any

from jobbench import images, prompt
from jobbench.agents.base import Adapter, AdapterError, register
from jobbench.dtu import DTU
from jobbench.providers import OPENAI, REASONING_EFFORT, provider_family

REPO_URL = "https://github.com/microsoft/amplifier-agent"
VENV = "/opt/amplifier-agent"
DRIVER_SOURCE = Path(__file__).resolve().parent / "amplifier_agent_driver"
DRIVER_DIR = "/opt/amplifier-agent-driver"
DRIVER_FILE = "amplifier_agent_driver.py"
SHIM_DIR = "/root/.local/bin"
SHIM_PATH = f"{SHIM_DIR}/amplifier-agent-driver"
SHIM = f'#!/bin/sh\nexec {VENV}/bin/python {DRIVER_DIR}/{DRIVER_FILE} "$@"\n'
RUN_DIR = "/root/amplifier-agent-run"

# The binding pins its engine to a release tag, so every install overrides the engine to the same
# source as the binding.
OVERRIDES_PATH = "/tmp/aa-overrides.txt"

# Dependencies come from the baked image, so the install fetches and builds only the two packages.
INSTALL_TIMEOUT_S = 600.0


def install_script(ref: str) -> str:
    """Shell script that installs the binding and its engine at `ref` into the baked venv."""
    url = f"git+{REPO_URL}@{ref}"
    engine = f"amplifier-agent-engine @ {url}#subdirectory=packages/engine"
    binding = f"amplifier-agent @ {url}#subdirectory=packages/python"
    return (
        'export PATH="$HOME/.local/bin:$PATH" && '
        f"printf '%s\\n' {shlex.quote(engine)} > {OVERRIDES_PATH} && "
        f"uv pip install --compile-bytecode --python {VENV}/bin/python "
        f"--overrides {OVERRIDES_PATH} {shlex.quote(binding)}"
    )


@register
class AmplifierAgentAdapter(Adapter):
    name = "amplifier-agent"
    image_alias = images.agent_alias("amplifier-agent")
    session_dirs = (RUN_DIR,)
    config_files = (f"{RUN_DIR}/run-config.json",)
    metrics_source = "events"

    def __init__(self, *, ref: str | None = None, raw_llm_payloads: bool = False) -> None:
        # `ref` is the git ref every trial of a run installs; run.py resolves it once per run.
        self._ref = ref
        self._raw_llm_payloads = raw_llm_payloads
        # Set by configure(); command() has no other place to source the model from.
        self._model: str | None = None

    async def configure(self, dtu: DTU, *, model: str) -> None:
        """Install the agent at the run's ref, then the driver and its shim, and check the driver.

        No secret is written: the binding reads the family's API key and base URL from the
        container environment that the launch profile's `passthrough.services` populates.
        """
        if self._ref is None:
            raise AdapterError(
                f"{self.name} configure failed: no ref to install; run it through `run.py run`, "
                "which resolves one per run, or pass ref= to agents.get"
            )
        family = provider_family(model)
        if family.name == OPENAI and not os.environ.get(family.base_url_env):
            # Required, not defaulted: passthrough forwards only what the host has, and the
            # public default would benchmark another backend.
            raise AdapterError(
                f"{self.name} configure failed: {family.base_url_env} is not set on the host, "
                f"so model {model!r} has no endpoint to target"
            )
        self._model = model
        await self._run(dtu, install_script(self._ref), "install", timeout_s=INSTALL_TIMEOUT_S)
        # The DTU login shell sources /etc/profile.d, so the agent's own shell commands find the
        # shim without the absolute path.
        profile_script = f"/etc/profile.d/jobbench-{self.name}.sh"
        await self._run(dtu, f"mkdir -p {DRIVER_DIR} {SHIM_DIR}", "mkdir")
        await dtu.file_push(DRIVER_SOURCE / DRIVER_FILE, f"{DRIVER_DIR}/{DRIVER_FILE}")
        await self._run(
            dtu,
            f"printf %s {shlex.quote(SHIM)} > {SHIM_PATH} && chmod +x {SHIM_PATH} && "
            f"printf 'export PATH=\"{SHIM_DIR}:$PATH\"\\n' > {profile_script}",
            "shim",
        )
        await self._run(dtu, f"{SHIM_PATH} --version", "driver --version")

    async def _run(self, dtu: DTU, script: str, step: str, **exec_options: Any) -> None:
        result = await dtu.exec_cmd(["bash", "-c", script], **exec_options)
        if result.returncode != 0:
            raise AdapterError(
                f"{self.name} configure failed at {step} (exit {result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )

    def command(self) -> list[str]:
        """argv equivalent of:

            amplifier-agent-driver --prompt-file /workspace/prompt.txt \\
              --working-directory /workspace --provider <id> --model <model> \\
              [--reasoning-effort medium] --out /root/amplifier-agent-run [--raw-llm-payloads]

        The shim is called by absolute path so the command does not depend on PATH. The prompt
        is read by the driver from the file, never interpolated.
        """
        if self._model is None:
            raise AdapterError(f"{self.name} command() called before configure()")
        family = provider_family(self._model)
        effort = f" --reasoning-effort {REASONING_EFFORT}" if family.name == OPENAI else ""
        raw = " --raw-llm-payloads" if self._raw_llm_payloads else ""
        script = (
            f"{SHIM_PATH} --prompt-file {prompt.PROMPT_PATH} "
            f"--working-directory {prompt.WORKSPACE} --provider {family.agent_module} "
            f"--model {shlex.quote(self._model)}{effort} --out {RUN_DIR}{raw}"
        )
        return ["bash", "-c", script]


__all__ = ["AmplifierAgentAdapter", "install_script"]

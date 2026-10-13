"""amplifier-agent, driven through its Python binding.

The binding and its engine are installed into their own venv and run by `amplifier-agent-driver`
(amplifier_agent_driver/), which holds one durable session: the task turn, then a recovery turn
whenever a tool outcome came back `unknown`.
"""

from __future__ import annotations

import shlex
from importlib import resources
from typing import Any

from pier.environments.base import BaseEnvironment
from pier.models.agent.install import InstallStep

from deepswe_agents.base import (
    DEFAULT_AMPLIFIER_AGENT_REF,
    UV_PRELUDE,
    WORKDIR,
    AmplifierBaseAgent,
    _as_bool,
)
from deepswe_agents.providers import AGENT_PROVIDER_MODULE, OPENAI, REASONING_EFFORT

VENV = "/opt/amplifier-agent"
DRIVER_DIR = "/opt/amplifier-agent-driver"
DRIVER_FILE = "amplifier_agent_driver.py"
SHIM_DIR = "/root/.local/bin"
SHIM_PATH = f"{SHIM_DIR}/amplifier-agent-driver"
RUN_DIR = "/root/amplifier-agent-run"
SHIM = f'#!/bin/sh\nexec {VENV}/bin/python {DRIVER_DIR}/{DRIVER_FILE} "$@"\n'

# The binding pins its engine to a release tag, so every install overrides the engine to the same
# source as the binding.
OVERRIDES_PATH = "/tmp/aa-overrides.txt"


def driver_source() -> str:
    """Text of the packaged driver script."""
    package = resources.files("deepswe_agents") / "amplifier_agent_driver"
    return (package / DRIVER_FILE).read_text(encoding="utf-8")


class AmplifierAgent(AmplifierBaseAgent):
    LOCAL_SOURCE_PACKAGE = "amplifier-agent"
    VERSION_BINARY = "amplifier-agent-driver"
    SESSION_DIRS = (RUN_DIR,)
    METRICS_SOURCE = "events"

    def __init__(
        self,
        *args: Any,
        amplifier_agent_ref: str = DEFAULT_AMPLIFIER_AGENT_REF,
        raw_llm_payloads: Any = False,
        **kwargs: Any,
    ):
        super().__init__(*args, **kwargs)
        self._amplifier_agent_ref = amplifier_agent_ref
        # Reachable as `--ak raw_llm_payloads=true`; pier passes it as a string.
        self._raw_llm_payloads = _as_bool(raw_llm_payloads)

    @staticmethod
    def name() -> str:
        return "amplifier-agent"

    async def setup(self, environment: BaseEnvironment) -> None:
        await super().setup(environment)
        self.logger.info(
            "[%s] raw LLM payload capture: %s",
            self.name(),
            "ON (--raw-llm-payloads)" if self._raw_llm_payloads else "off",
        )

    def _git_spec(self, package: str, subdirectory: str) -> str:
        return f"{package} @ {self._amplifier_agent_ref}#subdirectory={subdirectory}"

    def agent_install_steps(self) -> list[InstallStep]:
        engine = self._git_spec("amplifier-agent-engine", "packages/engine")
        binding = self._git_spec("amplifier-agent", "packages/python")
        return [
            *UV_PRELUDE,
            InstallStep(
                user="root",
                run=(
                    'export PATH="$HOME/.local/bin:$PATH"\n'
                    f"uv venv --python 3.13 {VENV}\n"
                    f"printf '%s\\n' {shlex.quote(engine)} > {OVERRIDES_PATH}\n"
                    f"uv pip install --compile-bytecode --python {VENV}/bin/python "
                    f"--overrides {OVERRIDES_PATH} {shlex.quote(binding)}"
                ),
            ),
            InstallStep(user="root", run=f"mkdir -p {DRIVER_DIR} {SHIM_DIR}"),
            InstallStep(
                user="root",
                run=f"printf %s {shlex.quote(driver_source())} > {DRIVER_DIR}/{DRIVER_FILE}",
            ),
            InstallStep(
                user="root",
                run=f"printf %s {shlex.quote(SHIM)} > {SHIM_PATH} && chmod +x {SHIM_PATH}",
            ),
            InstallStep(
                user="root",
                run='export PATH="$HOME/.local/bin:$PATH"; amplifier-agent-driver --version',
            ),
        ]

    async def _install_local_source(self, environment: BaseEnvironment) -> None:
        dest = await self._upload_local_source(environment)
        engine, binding = f"{dest}/packages/engine", f"{dest}/packages/python"
        await self.exec_as_root(
            environment,
            self._wrap(
                f'echo "amplifier-agent-engine @ file://{engine}" > {OVERRIDES_PATH} && '
                f"uv pip install --compile-bytecode --python {VENV}/bin/python "
                f"--overrides {OVERRIDES_PATH} {engine} {binding}"
            ),
            env=self.agent_env(),
        )
        await self._log_installed_version(environment, "amplifier-agent-driver --version")

    def run_command(self, instruction_path: str) -> str:
        family = self.provider_family
        effort = f" --reasoning-effort {REASONING_EFFORT}" if family == OPENAI else ""
        raw = " --raw-llm-payloads" if self._raw_llm_payloads else ""
        return (
            f"amplifier-agent-driver --prompt-file {instruction_path} "
            f"--working-directory {WORKDIR} --provider {AGENT_PROVIDER_MODULE[family]} "
            f"--model {shlex.quote(self.model)}{effort} --out {RUN_DIR}{raw}"
        )

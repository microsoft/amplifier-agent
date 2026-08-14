"""OpenCode frontend backed by amplifier-agent (amplifier-app-opencode)."""

from __future__ import annotations

from typing import Any

from pier.models.agent.install import InstallStep

from deepswe_agents.base import (
    DEFAULT_AMPLIFIER_AGENT_REF,
    OPENCODE_PRELUDE,
    UV_PRELUDE,
    AmplifierBaseAgent,
)

# amplifier-agent imports httpx at import time, on the path of every CLI command.
# It used to arrive transitively via `mcp`; that pull no longer exists, so it is
# pinned here for anyone installing an older SHA via --ak amplifier_agent_ref.
AMPLIFIER_AGENT_WITH = "httpx>=0.27,<1"

# Tracks the default branch so we benchmark the app as it actually ships.
# Override with --ak amplifier_app_opencode_ref=...@<sha> for a reproducible run.
DEFAULT_AMPLIFIER_APP_OPENCODE_REF = "git+https://github.com/microsoft/amplifier-app-opencode"


class OpencodeAmplifierAgent(AmplifierBaseAgent):
    LOCAL_SOURCE_PACKAGE = "amplifier-agent"
    VERSION_BINARY = "opencode"

    def __init__(
        self,
        *args: Any,
        amplifier_agent_ref: str = DEFAULT_AMPLIFIER_AGENT_REF,
        amplifier_app_opencode_ref: str = DEFAULT_AMPLIFIER_APP_OPENCODE_REF,
        **kwargs: Any,
    ):
        super().__init__(*args, **kwargs)
        self._amplifier_agent_ref = amplifier_agent_ref
        self._amplifier_app_opencode_ref = amplifier_app_opencode_ref

    @staticmethod
    def name() -> str:
        return "opencode-amplifier-agent"

    def agent_install_steps(self) -> list[InstallStep]:
        return [
            *UV_PRELUDE,
            InstallStep(
                user="root",
                run=(
                    'export PATH="$HOME/.local/bin:$PATH"; '
                    f'uv tool install --with "{AMPLIFIER_AGENT_WITH}" '
                    f'"{self._amplifier_agent_ref}"'
                ),
            ),
            *OPENCODE_PRELUDE,
            InstallStep(
                user="root",
                run=(
                    'export PATH="$HOME/.local/bin:$HOME/.opencode/bin:$PATH"\n'
                    f'uv tool install --from "{self._amplifier_app_opencode_ref}" '
                    "amplifier-app-opencode\n"
                    "amplifier-opencode --help >/dev/null"
                ),
            ),
            InstallStep(
                user="root",
                env={"DEBIAN_FRONTEND": "noninteractive"},
                run="apt-get update -qq && apt-get install -y --no-install-recommends jq",
            ),
        ]

    def run_command(self, instruction_path: str) -> str:
        return (
            f"amplifier-opencode launch -- run --auto --model amplifier/{self.model} "
            f'"$(cat {instruction_path})"'
        )

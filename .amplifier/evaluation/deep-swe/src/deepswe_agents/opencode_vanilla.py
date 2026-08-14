"""Stock OpenCode talking straight to Anthropic. The control arm."""

from __future__ import annotations

import json
import shlex
from typing import Any

from pier.models.agent.install import InstallStep

from deepswe_agents.base import OPENCODE_PRELUDE, AmplifierBaseAgent
from deepswe_agents.metrics import MODEL_RATES_PER_M

# The `cost` block written into opencode.json is BEST EFFORT only: opencode
# ignores the `cost.cache` override, so the published dollar figure comes from
# `metrics.parse_opencode_db`, which recomputes cost from the recorded token
# counts. See its docstring for why.


class OpencodeVanillaAgent(AmplifierBaseAgent):
    # No Amplifier component to replace.
    LOCAL_SOURCE_PACKAGE = None
    VERSION_BINARY = "opencode"

    # opencode records usage in SQLite, not events.jsonl.
    METRICS_SOURCE = "opencode_db"

    # Collect the WHOLE data dir, not just the db file. opencode runs SQLite in
    # WAL mode, so the newest writes -- including, in practice, the entire final
    # session -- live in the `opencode.db-wal` sidecar. Pulling `opencode.db`
    # alone yields a stale database that silently under-reports. `download_dir`
    # is directory-granular, so taking the parent is what keeps the sidecars
    # co-located for sqlite3 to replay.
    SESSION_DIRS = ("/root/.local/share/opencode",)

    @staticmethod
    def name() -> str:
        return "opencode-vanilla"

    def agent_env(self) -> dict[str, str]:
        """Normalize ANTHROPIC_BASE_URL for opencode's ai-sdk provider.

        The two clients disagree on what "base URL" means:
          * the Anthropic SDK (amplifier-agent) wants the host root and appends
            `/v1` itself   -> https://api.anthropic.com
          * ai-sdk `@ai-sdk/anthropic` (opencode) treats it as the full API root
            and appends only `/messages` -> needs https://api.anthropic.com/v1

        Forwarding the host value unchanged makes opencode request
        `https://api.anthropic.com/messages`, which 404s. opencode reports that
        as a bare `Error: Not Found` and aborts the run -- with no mention of a
        URL, which makes it look like a model or auth problem.
        """
        env = super().agent_env()
        base = env.get("ANTHROPIC_BASE_URL")
        if base:
            trimmed = base.rstrip("/")
            if not trimmed.endswith("/v1"):
                env["ANTHROPIC_BASE_URL"] = f"{trimmed}/v1"
        return env

    def _model_entry(self) -> dict[str, Any]:
        entry: dict[str, Any] = {"name": self.model}
        rates = MODEL_RATES_PER_M.get(self.model)
        if rates:
            entry["cost"] = {
                "input": rates["input"],
                "output": rates["output"],
                "cache": {"read": rates["cache_read"], "write": rates["cache_write"]},
            }
        return entry

    def _opencode_config(self) -> str:
        model = self.model
        return json.dumps(
            {
                "$schema": "https://opencode.ai/config.json",
                "model": f"anthropic/{model}",
                # Pin the SMALL model to the benchmark model. opencode uses a
                # separate "small" model for the session-title agent, and its
                # default family priority ends at claude-haiku -- a model this
                # endpoint does not serve. That request fails with a bare
                # `AI_APICallError: Not Found` and kills the process (exit 1)
                # before any task work happens. It was the single most common
                # failure of this arm.
                "small_model": f"anthropic/{model}",
                "provider": {
                    "anthropic": {
                        "npm": "@ai-sdk/anthropic",
                        "models": {model: self._model_entry()},
                    }
                },
            }
        )

    def agent_install_steps(self) -> list[InstallStep]:
        return [
            *OPENCODE_PRELUDE,
            InstallStep(user="root", run='mkdir -p "$HOME/.config/opencode"'),
            InstallStep(
                user="root",
                run=(
                    f"echo {shlex.quote(self._opencode_config())} "
                    '> "$HOME/.config/opencode/opencode.json"'
                ),
            ),
            InstallStep(
                user="root",
                run='export PATH="$HOME/.opencode/bin:$PATH"; opencode --version',
            ),
        ]

    async def run(self, instruction, environment, context) -> None:  # type: ignore[override]
        try:
            await super().run(instruction, environment, context)
        finally:
            # Guarded: on the timeout path this `finally` runs while the
            # coroutine is being cancelled, and a bare await here would itself
            # raise CancelledError -- a BaseException that the handler inside
            # _dump_opencode_log cannot catch -- masking the real timeout with a
            # confusing secondary exception.
            await self._await_guarded(self._dump_opencode_log(environment), "opencode log dump")

    async def _dump_opencode_log(self, environment) -> None:
        """Surface opencode's own log.

        opencode reports failures to stdout as a bare `Error: <msg>` with no
        context; the detail (including the failing request) only lands in
        ~/.local/share/opencode/log/.
        """
        try:
            res = await self.exec_as_agent(
                environment,
                self._wrap(
                    "echo '--- OPENCODE LOG ---'; "
                    'tail -80 "$HOME"/.local/share/opencode/log/*.log 2>&1 '
                    "|| echo '(no opencode log)'"
                ),
                env=self.agent_env(),
                timeout_sec=60,
            )
            self.logger.warning("[opencode log]\n%s", (getattr(res, "stdout", "") or "").strip())
        except Exception as exc:  # noqa: BLE001 - diagnostics must never break a trial
            self.logger.warning(f"could not read opencode log: {exc}")

    def run_command(self, instruction_path: str) -> str:
        return f'opencode run --model anthropic/{self.model} --auto "$(cat {instruction_path})"'

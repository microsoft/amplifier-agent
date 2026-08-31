"""Stock OpenCode talking straight to the model vendor. The control arm.

Provider (Anthropic or OpenAI) is derived from the model under test; see
`deepswe_agents.providers`.
"""

from __future__ import annotations

import json
import shlex
from typing import Any

from pier.models.agent.install import InstallStep

from deepswe_agents.base import OPENCODE_PRELUDE, AmplifierBaseAgent
from deepswe_agents.metrics import MODEL_RATES_PER_M
from deepswe_agents.providers import (
    ANTHROPIC,
    OPENAI,
    OPENCODE_NPM,
    OPENCODE_PROVIDER_ID,
    REASONING_EFFORT,
)

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

        ANTHROPIC-ONLY. The two Anthropic clients disagree on what "base URL"
        means:
          * the Anthropic SDK (amplifier-agent) wants the host root and appends
            `/v1` itself   -> https://api.anthropic.com
          * ai-sdk `@ai-sdk/anthropic` (opencode) treats it as the full API root
            and appends only `/messages` -> needs https://api.anthropic.com/v1

        Forwarding the host value unchanged makes opencode request
        `https://api.anthropic.com/messages`, which 404s. opencode reports that
        as a bare `Error: Not Found` and aborts the run -- with no mention of a
        URL, which makes it look like a model or auth problem.

        OPENAI_BASE_URL has no such mismatch: both sides already mean the `/v1`
        API root, so the OpenAI family is passed through untouched. Applying
        this fixup there would append a second `/v1` and break every request.
        """
        env = super().agent_env()
        if self.provider_family != ANTHROPIC:
            return env
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
        if self.provider_family == OPENAI:
            # MODEL-level, not provider-level. The provider `options` block
            # (which carries baseURL) does NOT reach the wire with this key --
            # only `provider.<id>.models.<model>.options.reasoningEffort` does.
            #
            # This overrides opencode's own built-in default, which stamps
            # `reasoningEffort: "medium"` onto any model id containing "gpt-5".
            # Without this the arm would silently benchmark medium while the
            # other two ran at REASONING_EFFORT. Only that one default is
            # replaced; opencode's other gpt-5 defaults still apply.
            entry["reasoning"] = True
            entry["options"] = {"reasoningEffort": REASONING_EFFORT}
        return entry

    def _opencode_config(self) -> str:
        model = self.model
        family = self.provider_family
        provider_id = OPENCODE_PROVIDER_ID[family]
        provider: dict[str, Any] = {
            "npm": OPENCODE_NPM[family],
            "models": {model: self._model_entry()},
        }
        if family == OPENAI:
            # opencode reads the endpoint from provider.<id>.options.baseURL,
            # NOT from the provider root. Same shape pier's own opencode
            # adapter writes (pier/agents/installed/opencode.py). Put at the
            # root it is silently ignored and every request goes to the public
            # OpenAI endpoint instead of the one under benchmark.
            provider["options"] = {"baseURL": self.base_url()}
        return json.dumps(
            {
                "$schema": "https://opencode.ai/config.json",
                "model": f"{provider_id}/{model}",
                # Pin the SMALL model to the benchmark model. opencode uses a
                # separate "small" model for the session-title agent, and its
                # default family priority ends at a cheap model this endpoint
                # may not serve (claude-haiku on the Anthropic side). That
                # request fails with a bare `AI_APICallError: Not Found` and
                # kills the process (exit 1) before any task work happens. It
                # was the single most common failure of this arm.
                "small_model": f"{provider_id}/{model}",
                "provider": {provider_id: provider},
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
        # The `--` before the prompt is load-bearing: without it, any instruction
        # whose text begins with `-` is parsed as a flag, opencode prints its usage
        # banner, and the agent never runs. yargs is configured with
        # `populate--: true`, and opencode's run command merges `argv["--"]` back
        # into the message, so the prompt still arrives intact.
        provider_id = OPENCODE_PROVIDER_ID[self.provider_family]
        return (
            f'opencode run --model {provider_id}/{self.model} --auto -- "$(cat {instruction_path})"'
        )

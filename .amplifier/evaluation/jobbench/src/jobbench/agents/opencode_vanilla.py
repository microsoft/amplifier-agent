"""Stock OpenCode talking straight to Anthropic. The control arm.

Ports deep-swe's OpencodeVanillaAgent
(../deep-swe/src/deepswe_agents/opencode_vanilla.py) to jobbench's Adapter
contract. One structural difference: deep-swe normalizes ANTHROPIC_BASE_URL by
overriding the Python-side env dict pier hands to `environment.exec`. jobbench's
DTU.exec_cmd has no such hook -- there is no channel to inject env into the
remote container process from here. The normalization is done instead inline
in the command's own shell script, re-exporting ANTHROPIC_BASE_URL for that
one invocation from whatever value the launch profile's passthrough.services
already put in the container's environment.

The `cost` block written into opencode.json is best-effort only: opencode
ignores the `cost.cache` override, so the published dollar figure actually
comes from `metrics.parse_opencode_db`, which recomputes cost from the
recorded token counts against MODEL_RATES_PER_M. See that function's
docstring for why.
"""

from __future__ import annotations

import json
from typing import Any

from jobbench import images
from jobbench.agents.base import Adapter, AdapterError, register
from jobbench.dtu import DTU
from jobbench.metrics import MODEL_RATES_PER_M

OPENCODE_CONFIG_PATH = "$HOME/.config/opencode/opencode.json"
_HEREDOC_MARKER = "JOBBENCH_OPENCODE_EOF"


def _model_entry(model: str) -> dict[str, Any]:
    entry: dict[str, Any] = {"name": model}
    rates = MODEL_RATES_PER_M.get(model)
    if rates:
        entry["cost"] = {
            "input": rates["input"],
            "output": rates["output"],
            "cache": {"read": rates["cache_read"], "write": rates["cache_write"]},
        }
    return entry


def _opencode_config(model: str) -> str:
    return json.dumps(
        {
            "$schema": "https://opencode.ai/config.json",
            "model": f"anthropic/{model}",
            # Pin the SMALL model to the benchmark model too. opencode uses a
            # separate "small" model for its session-title agent, and its
            # default family priority ends at claude-haiku -- a model this
            # endpoint may not serve. That request fails with a bare
            # `AI_APICallError: Not Found` and kills the process (exit 1)
            # before any task work happens; this was deep-swe's single most
            # common failure mode for this arm.
            "small_model": f"anthropic/{model}",
            "provider": {
                "anthropic": {
                    "npm": "@ai-sdk/anthropic",
                    "models": {model: _model_entry(model)},
                }
            },
        }
    )


# Normalizes ANTHROPIC_BASE_URL for opencode's ai-sdk provider before the run.
#
# The two clients disagree on what "base URL" means:
#   * the Anthropic SDK (amplifier-agent, amplifier-foundation) wants the host
#     root and appends /v1 itself   -> https://api.anthropic.com
#   * ai-sdk's @ai-sdk/anthropic (opencode) treats it as the full API root and
#     appends only /messages        -> needs https://api.anthropic.com/v1
#
# Forwarding the passthrough value unchanged makes opencode request
# https://api.anthropic.com/messages, which 404s -- reported as a bare
# `Error: Not Found` with no mention of a URL, which looks like a model or
# auth problem instead of what it is.
_BASE_URL_NORMALIZE = (
    'base="${ANTHROPIC_BASE_URL%/}"; '
    'case "$base" in */v1) ;; *) base="$base/v1" ;; esac; '
    'export ANTHROPIC_BASE_URL="$base"'
)


@register
class OpencodeVanillaAdapter(Adapter):
    name = "opencode-vanilla"
    image_alias = images.agent_alias("opencode-vanilla")

    # opencode runs SQLite in WAL mode, so the newest writes -- including, in
    # practice, the entire final session -- live in the opencode.db-wal
    # sidecar. Collecting the whole data dir (not just the db file) is what
    # keeps the sidecar co-located for sqlite3 to replay; pulling opencode.db
    # alone yields a stale database that silently under-reports.
    session_dirs = ("/root/.local/share/opencode",)
    metrics_source = "opencode_db"

    def __init__(self) -> None:
        # Set by configure(); command() has no other place to source the
        # model from, per the Adapter contract.
        self._model: str | None = None

    async def configure(self, dtu: DTU, *, model: str) -> None:
        """Write opencode.json at TRIAL time: model, small_model, cost table.

        No secret here -- opencode reads ANTHROPIC_API_KEY straight from the
        container environment the launch profile's passthrough.services
        already populated.
        """
        self._model = model
        config_json = _opencode_config(model)
        script = (
            'mkdir -p "$HOME/.config/opencode" && '
            f"cat > \"{OPENCODE_CONFIG_PATH}\" <<'{_HEREDOC_MARKER}'\n"
            f"{config_json}\n"
            f"{_HEREDOC_MARKER}"
        )
        result = await dtu.exec_cmd(["bash", "-c", script])
        if result.returncode != 0:
            raise AdapterError(
                f"opencode-vanilla configure failed (exit {result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )

    def command(self) -> list[str]:
        """argv equivalent of:

            cd /workspace && opencode run --model anthropic/<model> --auto \\
              "$(cat /workspace/prompt.txt)"

        preceded by the ANTHROPIC_BASE_URL normalization above, scoped to this
        one exec (there is no persistent env to poison for a later step).
        """
        if self._model is None:
            raise AdapterError("opencode-vanilla command() called before configure()")
        script = (
            f"{_BASE_URL_NORMALIZE}; "
            "cd /workspace && "
            f"opencode run --model anthropic/{self._model} --auto "
            '"$(cat /workspace/prompt.txt)"'
        )
        return ["bash", "-c", script]


__all__ = ["OpencodeVanillaAdapter"]

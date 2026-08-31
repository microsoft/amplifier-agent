"""Stock OpenCode talking straight to the provider. The control arm.

Ports deep-swe's OpencodeVanillaAgent
(../deep-swe/src/deepswe_agents/opencode_vanilla.py) to jobbench's Adapter
contract. One structural difference: deep-swe normalizes ANTHROPIC_BASE_URL by
overriding the Python-side env dict pier hands to `environment.exec`. jobbench's
DTU.exec_cmd has no such hook -- there is no channel to inject env into the
remote container process from here. The normalization is done instead inline
in the command's own shell script, re-exporting ANTHROPIC_BASE_URL for that
one invocation from whatever value the launch profile's passthrough.services
already put in the container's environment.

Which provider opencode is pointed at follows from the model under test (see
jobbench.providers). Only the Anthropic path needs the base-URL rewrite above;
the OpenAI path pins its endpoint in opencode.json instead, because
OPENAI_BASE_URL is already the full API root ai-sdk wants.

The `cost` block written into opencode.json is best-effort only: opencode
ignores the `cost.cache` override, so the published dollar figure actually
comes from `metrics.parse_opencode_db`, which recomputes cost from the
recorded token counts against MODEL_RATES_PER_M. See that function's
docstring for why.
"""

from __future__ import annotations

import json
import os
from typing import Any

from jobbench import images
from jobbench.agents.base import Adapter, AdapterError, register
from jobbench.dtu import DTU
from jobbench.metrics import MODEL_RATES_PER_M
from jobbench.providers import OPENAI, REASONING_EFFORT, provider_family

OPENCODE_CONFIG_PATH = "$HOME/.config/opencode/opencode.json"
_HEREDOC_MARKER = "JOBBENCH_OPENCODE_EOF"


def _model_entry(model: str) -> dict[str, Any]:
    """The `provider.<id>.models.<model>` block for one model.

    The OpenAI branch pins reasoning effort here and NOWHERE ELSE in this
    config. opencode merges the per-model `options` dict over its own defaults
    (packages/opencode/src/session/llm/request.ts:91) and forwards the result
    to the wire (transform.ts:1414); the PROVIDER-level `options` block does
    not carry this key through -- only connection settings like baseURL
    survive there. Left unset, opencode applies a built-in
    `reasoningEffort: "medium"` to any model id containing "gpt-5"
    (transform.ts:1289-1291), so this override is what keeps the control arm
    at the same effort as the two amplifier arms.

    `reasoning: true` is not strictly required for options.reasoningEffort to
    reach the wire (transform.ts:1364 is an OR), but it is the accurate
    declaration for a reasoning model, so it is stated rather than implied.

    Anthropic models get neither key: that family budgets thinking tokens
    instead, and pinning it is out of scope.
    """
    entry: dict[str, Any] = {"name": model}
    rates = MODEL_RATES_PER_M.get(model)
    if rates:
        entry["cost"] = {
            "input": rates["input"],
            "output": rates["output"],
            "cache": {"read": rates["cache_read"], "write": rates["cache_write"]},
        }
    if provider_family(model).name == OPENAI:
        entry["reasoning"] = True
        entry["options"] = {"reasoningEffort": REASONING_EFFORT}
    return entry


def _opencode_config(model: str, base_url: str | None = None) -> str:
    """opencode.json for one model. `base_url`, when given, pins the endpoint.

    The endpoint belongs under `provider.<id>.options.baseURL` -- that options
    dict is what opencode forwards to the ai-sdk provider factory (same shape
    as amplifier-app-opencode's own writer, cli.py:427-432). A `baseURL` at the
    provider root is silently ignored.

    `base_url` is None for the Anthropic path, which instead rewrites
    ANTHROPIC_BASE_URL in the run command (see _BASE_URL_NORMALIZE). Leaving
    that path's JSON untouched keeps the control arm byte-identical to every
    run recorded before this adapter learned about a second family.
    """
    family = provider_family(model)
    prefix = family.opencode_provider_id
    provider_block: dict[str, Any] = {"npm": family.opencode_npm}
    if base_url is not None:
        provider_block["options"] = {"baseURL": base_url}
    provider_block["models"] = {model: _model_entry(model)}
    return json.dumps(
        {
            "$schema": "https://opencode.ai/config.json",
            "model": f"{prefix}/{model}",
            # Pin the SMALL model to the benchmark model too. opencode uses a
            # separate "small" model for its session-title agent, and its
            # default family priority ends at a cheap model (claude-haiku,
            # gpt-*-mini) this endpoint may not serve. That request fails with
            # a bare `AI_APICallError: Not Found` and kills the process
            # (exit 1) before any task work happens; this was deep-swe's
            # single most common failure mode for this arm.
            "small_model": f"{prefix}/{model}",
            "provider": {prefix: provider_block},
        }
    )


# Normalizes ANTHROPIC_BASE_URL for opencode's ai-sdk provider before the run.
# ANTHROPIC ONLY -- see `opencode_base_url_needs_v1` in jobbench.providers.
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
#
# OPENAI_BASE_URL is already the full API root (it ends in /v1), so the OpenAI
# path must NOT run this -- a second /v1 would 404 the same way. The `case`
# below happens to be idempotent, but the OpenAI path skips it outright and
# pins its endpoint in opencode.json instead, which is inspectable after the
# fact in the captured config.
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

        No secret here -- opencode reads the family's API key env var
        (ANTHROPIC_API_KEY / OPENAI_API_KEY) straight from the container
        environment the launch profile's passthrough.services already
        populated. The heredoc below stays QUOTED precisely so nothing in
        this JSON can expand against that environment.

        The base URL is not a secret, so the OpenAI path resolves it
        host-side and writes it into the config, which also makes the
        endpoint under test visible in the captured artifact. Required, not
        defaulted: the host must already have it set for passthrough to
        forward it at all, and defaulting to api.openai.com could silently
        benchmark a different backend.
        """
        self._model = model
        family = provider_family(model)
        base_url: str | None = None
        if family.name == OPENAI:
            base_url = os.environ.get(family.base_url_env)
            if not base_url:
                raise AdapterError(
                    f"opencode-vanilla configure failed: {family.base_url_env} is not set "
                    f"on the host, so model {model!r} has no endpoint to target"
                )
        config_json = _opencode_config(model, base_url)
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

            cd /workspace && opencode run --model <provider>/<model> --auto \\
              "$(cat /workspace/prompt.txt)"

        On the Anthropic path this is preceded by the ANTHROPIC_BASE_URL
        normalization above, scoped to this one exec (there is no persistent
        env to poison for a later step). The OpenAI path has nothing to
        normalize -- its endpoint is already pinned in opencode.json.
        """
        if self._model is None:
            raise AdapterError("opencode-vanilla command() called before configure()")
        family = provider_family(self._model)
        prefix = f"{_BASE_URL_NORMALIZE}; " if family.opencode_base_url_needs_v1 else ""
        script = (
            f"{prefix}"
            "cd /workspace && "
            f"opencode run --model {family.opencode_provider_id}/{self._model} --auto "
            '"$(cat /workspace/prompt.txt)"'
        )
        return ["bash", "-c", script]


__all__ = ["OpencodeVanillaAdapter"]

"""The amplifier-foundation adapter (full `amplifier run` CLI, a pinned bundle).

Ports deep-swe's AmplifierFoundationAgent
(../deep-swe/src/deepswe_agents/amplifier_foundation.py) to jobbench's Adapter
contract. Two differences from that reference, both load-bearing:

1. deep-swe's workdir is /app; ours is /workspace (see jobbench.prompt).
2. deep-swe writes settings.yaml through pier's own env-dict exec, so the
   secret rides in a Python-side env mapping that never touches argv. jobbench's
   DTU.exec_cmd has no such hook -- it only shells out to the DTU CLI locally,
   with no channel to inject env into the remote container process. The same
   unquoted-heredoc trick is used instead, but the ${...} references expand
   against the CONTAINER's own environment (already populated by the launch
   profile's passthrough.services), not a dict we control. Either way the
   secret value itself never appears in our argv or logs -- only the literal
   reference (e.g. `${ANTHROPIC_API_KEY}`, `${OPENAI_API_KEY}`) does.

The bake profile (profiles/agents/amplifier-foundation.bake.yaml) installs the
CLI and pre-warms the default bundle's module resolution, with no provider,
model, or secret baked in -- those are per-run choices, written here instead.
"""

from __future__ import annotations

import os

from jobbench import images
from jobbench.agents.base import Adapter, AdapterError, register
from jobbench.dtu import DTU
from jobbench.providers import ANTHROPIC, OPENAI, REASONING_EFFORT, provider_family

SETTINGS_PATH = "$HOME/.amplifier/settings.yaml"

# anchors is deep-swe's coding-oriented default and may not be the right
# bundle for JobBench's knowledge-work tasks. Overridable two ways: the
# adapter constructor kwarg below (`agents.get("amplifier-foundation",
# bundle=...)`) and `run.py run --bundle`, which resolves to this kwarg.
DEFAULT_BUNDLE = (
    "git+https://github.com/microsoft/amplifier-foundation@main"
    "#subdirectory=bundles/anchors/bundle.md"
)

_HEREDOC_MARKER = "JOBBENCH_SETTINGS_EOF"


def _settings_yaml(model: str) -> str:
    """The settings.yaml body for one model, provider family and all.

    Written for an UNQUOTED heredoc: the ${...} references below expand INSIDE
    the container against its own environment, so the secret value never
    crosses into our Python process, argv, or logs -- only the literal
    reference does. Nothing here reads an env var host-side.

    Both provider modules read base_url from CONFIG ONLY; neither has a runtime
    env fallback. Omitting the key would silently send this arm to the vendor's
    public endpoint while every other arm hits the configured one, i.e.
    benchmarking a different backend. The Anthropic `:-https://api.anthropic.com`
    default only fires if a launch profile forgot to pass ANTHROPIC_BASE_URL
    through, and is kept only because api.anthropic.com genuinely is that
    family's endpoint. There is deliberately NO equivalent default for OpenAI:
    the endpoint under test is not necessarily the public one, so an unset
    OPENAI_BASE_URL must fail loudly rather than quietly re-target the run.

    enable_1m_context / enable_prompt_caching are provider-anthropic config
    keys. They are dropped for provider-openai rather than passed inertly, so
    the captured settings.yaml describes only knobs that actually exist. The
    mirror image is reasoning_effort, a provider-openai key written only on
    that branch: it pins the benchmark-wide effort level (see
    jobbench.providers.REASONING_EFFORT) so this arm matches the other two.
    Anthropic has no equivalent -- it budgets thinking tokens instead -- so
    that branch is left alone deliberately, not by omission.

    No routing.matrix key in either branch (re-introduces role-based fan-out to
    a different model, which would invalidate a single-model comparison) and no
    bundle: key (the run command pins the bundle explicitly, so a stray bundle:
    entry here would never be read anyway).
    """
    family = provider_family(model)
    if family.default_base_url is not None:
        base_url = f"${{{family.base_url_env}:-{family.default_base_url}}}"
    else:
        base_url = f"${{{family.base_url_env}}}"
    lines = [
        "config:",
        "  providers:",
        f"    - module: {family.foundation_module}",
        f"      source: {family.foundation_source}",
        "      config:",
        f"        api_key: ${{{family.api_key_env}}}",
        f"        base_url: {base_url}",
        f"        default_model: {model}",
    ]
    if family.name == ANTHROPIC:
        lines += [
            "        enable_1m_context: 'true'",
            "        enable_prompt_caching: 'true'",
        ]
    elif family.name == OPENAI:
        lines.append(f"        reasoning_effort: {REASONING_EFFORT}")
    lines.append("        priority: 1")
    return "\n".join(lines) + "\n"


@register
class AmplifierFoundationAdapter(Adapter):
    name = "amplifier-foundation"
    image_alias = images.agent_alias("amplifier-foundation")

    # `amplifier run` writes one session tree per project slug (the cwd with
    # separators replaced). Collecting the whole projects/ parent rather than
    # computing one slug survives a slug surprise and matches deep-swe's own
    # choice. A bundle whose session composes more than one logging hook (the
    # default anchors bundle does: hooks-logging + context-intelligence
    # logging) writes events.jsonl twice; that is de-duplicated by
    # metrics.parse_events on response identity, not by which file we collect.
    session_dirs = ("/root/.amplifier/projects",)
    metrics_source = "events"

    def __init__(self, *, bundle: str = DEFAULT_BUNDLE) -> None:
        self._bundle = bundle

    async def configure(self, dtu: DTU, *, model: str) -> None:
        """Write settings.yaml at TRIAL time, never at bake time.

        A bake-time write becomes an image layer; baking the API key there
        would put the secret in the image. The model is also a per-run
        choice, so it belongs here too, not in the bake profile.
        """
        family = provider_family(model)
        if family.default_base_url is None and family.base_url_env not in os.environ:
            # Presence check only -- the VALUE is never read here; the heredoc
            # below expands it inside the container instead. Without a default
            # to fall back on, an unset var would expand to an empty base_url
            # and surface as an opaque provider error mid-run. The host is the
            # right place to check: passthrough only forwards vars set here.
            raise AdapterError(
                f"amplifier-foundation configure failed: {family.base_url_env} is not set "
                f"on the host, so model {model!r} has no endpoint to target"
            )
        settings = _settings_yaml(model)
        script = (
            'mkdir -p "$HOME/.amplifier" && '
            f'cat > "{SETTINGS_PATH}" <<{_HEREDOC_MARKER}\n'
            f"{settings}"
            f"{_HEREDOC_MARKER}"
        )
        result = await dtu.exec_cmd(["bash", "-c", script])
        if result.returncode != 0:
            raise AdapterError(
                f"amplifier-foundation configure failed (exit {result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )

    def command(self) -> list[str]:
        """argv equivalent of:

            cd /workspace && amplifier run --bundle '<bundle>' --mode single \\
              --output-format json "$(cat /workspace/prompt.txt)"

        No --model flag: it requires --provider alongside it, and the model
        is already pinned by default_model in settings.yaml.
        """
        script = (
            "cd /workspace && "
            f"amplifier run --bundle '{self._bundle}' "
            "--mode single --output-format json "
            '"$(cat /workspace/prompt.txt)"'
        )
        return ["bash", "-c", script]


__all__ = ["DEFAULT_BUNDLE", "AmplifierFoundationAdapter"]

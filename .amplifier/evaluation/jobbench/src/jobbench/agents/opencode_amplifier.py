"""OpenCode frontend backed by amplifier-agent (amplifier-app-opencode).

Ports deep-swe's OpencodeAmplifierAgent
(../deep-swe/src/deepswe_agents/opencode_amplifier.py) to jobbench's Adapter
contract, with no stubbing and no retries beyond what deep-swe already does.

KNOWN ISSUE affecting this arm's results. Tool results are lost somewhere in
the opencode round trip, so the model repeatedly re-decides it has not read
files it already read. The provider injects a synthetic
"[SYSTEM ERROR: Tool result missing from conversation history]" message
(amplifier-module-provider-anthropic/__init__.py:2014) and the model narrates
it back in its own words. Measured on one task: 22 to 27 occurrences per run,
and roughly double the wall-clock of the other three agents, while still
exiting 0 with plausible deliverables and a plausible score. The control that
isolates it is opencode-vanilla, which is the same CLI and model talking
directly to Anthropic and never exhibits it.

Root cause is outside this harness, so nothing here works around it. Affected
runs are flagged via `warnings` in trial.json (see trial._detect_tool_result_loss)
so the contaminated figures cannot be mistaken for a clean measurement.

configure() does no I/O beyond recording the model: neither deep-swe's
reference nor this port writes any settings file for this agent, since
amplifier-agent (wrapped by amplifier-opencode) reads ANTHROPIC_API_KEY and
ANTHROPIC_BASE_URL straight from the container environment the launch
profile's passthrough.services already populated -- the same assumption
jobbench's own amplifier-agent adapter makes.

session_dirs is deliberately empty. Nothing under /root was found to hold an
amplifier-agent-style session tree when running through the opencode
wrapper -- see the module-level note below for what was checked.
"""

from __future__ import annotations

from jobbench import images
from jobbench.agents.base import Adapter, register
from jobbench.dtu import DTU

# amplifier-agent imports httpx at import time, on the path of every CLI
# command. It used to arrive transitively via `mcp`; that pull no longer
# exists, so it is pinned here, matching deep-swe's own pin.
AMPLIFIER_AGENT_WITH = "httpx>=0.27,<1"


@register
class OpencodeAmplifierAdapter(Adapter):
    name = "opencode-amplifier"
    image_alias = images.agent_alias("opencode-amplifier")

    # No known location under /root persists an amplifier-agent-style session
    # tree (events.jsonl or equivalent) when amplifier-agent is driven through
    # the amplifier-opencode wrapper rather than its own CLI. Checked and
    # ruled out: /root/.amplifier-agent/state/workspaces (empty -- that path
    # is amplifier-agent's OWN CLI session store, not touched by the
    # amplifier-opencode launcher), /root/.local/share/opencode (opencode's
    # own SQLite usage store, which records opencode's own token accounting,
    # not amplifier-agent's -- and this agent's failure mode below means
    # opencode itself likely never reaches a billable turn either).
    # Left empty rather than guessed: an unpulled telemetry path is honestly
    # not_available; a wrong guess would silently pull nothing every time and
    # look identical to "no telemetry exists" without ever being caught.
    session_dirs: tuple[str, ...] = ()
    metrics_source = "events"

    def __init__(self) -> None:
        # Set by configure(); command() has no other place to source the
        # model from, per the Adapter contract.
        self._model: str | None = None

    async def configure(self, dtu: DTU, *, model: str) -> None:
        del dtu  # No per-trial config file needed; see module docstring.
        self._model = model

    def command(self) -> list[str]:
        """argv equivalent of:

            cd /workspace && amplifier-opencode launch -- run --auto \\
              --model amplifier/<model> "$(cat /workspace/prompt.txt)"
        """
        if self._model is None:
            raise RuntimeError("opencode-amplifier command() called before configure()")
        script = (
            "cd /workspace && "
            f"amplifier-opencode launch -- run --auto --model amplifier/{self._model} "
            '"$(cat /workspace/prompt.txt)"'
        )
        return ["bash", "-c", script]


__all__ = ["AMPLIFIER_AGENT_WITH", "OpencodeAmplifierAdapter"]

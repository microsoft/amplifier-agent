"""Amplifier agent adapters for the deep-swe benchmark.

Short CLI names -> pier `--agent-import-path` values.
"""

AGENTS = {
    "amplifier-agent": "deepswe_agents.amplifier_agent:AmplifierAgent",
    "amplifier-foundation": "deepswe_agents.amplifier_foundation:AmplifierFoundationAgent",
    "opencode-amplifier-agent": "deepswe_agents.opencode_amplifier:OpencodeAmplifierAgent",
    "opencode-vanilla": "deepswe_agents.opencode_vanilla:OpencodeVanillaAgent",
}

#: Agents that accept `--local-source` (they install an Amplifier component).
LOCAL_SOURCE_AGENTS = {
    "amplifier-agent",
    "amplifier-foundation",
    "opencode-amplifier-agent",
}

__all__ = ["AGENTS", "LOCAL_SOURCE_AGENTS"]

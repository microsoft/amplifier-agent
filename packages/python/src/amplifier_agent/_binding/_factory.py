"""Composition boundary for the in-process binding."""

from amplifier_agent._ports import AgentPort, DiscoveryPort
from amplifier_agent._records import AgentError, AgentOptions

VERSIONS = ("agent-interface/1", "turn-events/1", "language-binding/1", "host-config/1")


def _unavailable() -> AgentError:
    return AgentError(
        "engine_unavailable",
        "lifecycle",
        "The installed execution dependencies are unavailable.",
        "Reinstall the library with its declared dependencies.",
    )


async def connect(options: AgentOptions) -> AgentPort:
    try:
        from amplifier_agent_engine import _records as engine_records
        from amplifier_agent_engine._engine.assembly import create_engine
    except (ImportError, OSError) as exc:
        raise _unavailable() from exc

    from amplifier_agent._binding._engine_adapter import AgentAdapter, RecordBridge

    bridge = RecordBridge(engine_records)
    target = await bridge.call(create_engine, bridge.to_engine(options))
    port = AgentAdapter(target, bridge)
    if not set(VERSIONS).issubset(port.contract_versions):
        await port.close()
        raise AgentError(
            "contract_version_mismatch",
            "lifecycle",
            "The installed package cannot satisfy the required contracts.",
            "Install matching library and runtime assets.",
        )
    return port


def discovery() -> DiscoveryPort:
    try:
        from amplifier_agent_engine import _records as engine_records
        from amplifier_agent_engine._engine import discovery as engine_discovery
    except (ImportError, OSError) as exc:
        raise _unavailable() from exc

    from amplifier_agent._binding._engine_adapter import DiscoveryAdapter, RecordBridge

    return DiscoveryAdapter(engine_discovery, RecordBridge(engine_records))


def lacks_approval_policy(options: AgentOptions) -> bool:
    """Whether the agent these options resolve to has tools and no approval handler or policy."""
    try:
        from amplifier_agent_engine import _records as engine_records
        from amplifier_agent_engine._engine.configuration import resolve
    except (ImportError, OSError):
        # connect reports the missing dependencies as engine_unavailable.
        return False

    from amplifier_agent._binding._engine_adapter import RecordBridge

    bridge = RecordBridge(engine_records)
    config = bridge.read(lambda: resolve(bridge.to_engine(options)))
    has_tools = bool(config.tools or config.builtin_tools or config.skills or config.mcp_servers)
    return has_tools and config.approvals is None

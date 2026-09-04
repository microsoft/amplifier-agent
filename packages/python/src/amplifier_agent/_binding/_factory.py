"""Composition boundary for the in-process binding."""

from .._ports import AgentPort
from .._records import AgentError, AgentOptions

VERSIONS = ("agent-interface/1", "turn-events/1", "language-binding/1", "host-config/1")


async def connect(options: AgentOptions) -> AgentPort:
    try:
        from amplifier_agent_engine import _records as engine_records
        from amplifier_agent_engine._engine.assembly import create_engine
    except (ImportError, OSError) as exc:
        raise AgentError(
            "engine_unavailable",
            "lifecycle",
            "The installed execution dependencies are unavailable.",
            "Reinstall the library with its declared dependencies.",
        ) from exc

    from ._engine_adapter import AgentAdapter, RecordBridge

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

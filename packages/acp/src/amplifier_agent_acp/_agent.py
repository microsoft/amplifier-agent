"""The ACP agent: one per connection."""

from importlib.metadata import version
from typing import Any

from acp import PROTOCOL_VERSION
from acp.schema import ClientCapabilities, Implementation, InitializeResponse


# A plain class rather than an `acp.Agent` subclass: the router answers -32601 for a method this class lacks, where an
# inherited protocol stub would answer success. It therefore does not satisfy the `acp.Agent` protocol, and callers of
# `run_agent` cast it, as the SDK's own connection does once it has the instance.
class AcpAgent:
    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: ClientCapabilities | None = None,
        client_info: Implementation | None = None,
        **kwargs: Any,
    ) -> InitializeResponse:
        return InitializeResponse(
            protocol_version=PROTOCOL_VERSION,
            agent_info=Implementation(name="amplifier-agent", version=version("amplifier-agent-acp")),
        )

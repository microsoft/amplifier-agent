"""Host-tool placeholder for opencode-delegated tools.

opencode declares its host-side tools (read, edit, bash, glob, grep, webfetch,
...) on every chat-completion request via ``tools[]``. amplifier registers a
``HostToolProxy`` for each so the LLM can pick them. When the LLM does pick one,
the kernel calls ``proxy.execute()`` -- which has nothing to actually do because
the real implementation lives in opencode. The proxy raises ``HostToolYield``
to escape the orchestrator cleanly; the HTTP face catches the signal and emits
the OpenAI ``finish_reason: "tool_calls"`` terminal SSE chunk so opencode runs
the tool host-side and re-POSTs with the result.

The host-tool hook (sibling module) listens for ``tool:pre`` and emits the
matching ``tool_calls/delta`` display event before this proxy raises -- so the
HTTP face has already serialized the wire-shape tool_calls chunks by the time
the signal escapes.

POC behaviour:
- ``execute()`` always raises ``HostToolYield`` for every host-tool dispatch.
  There is no per-instance signalling state -- the proxy IS a host-tool, so the
  raise is unconditional.
- The kernel's ``Tool`` protocol requires ``execute(input_data) -> ToolResult``,
  but we never reach the return path -- ``HostToolYield`` is a BaseException
  that escapes loop-streaming's narrow ``except Exception`` guards.
- Parallel tool calls within a single LLM turn are NOT handled correctly in the
  POC: ``asyncio.gather()`` cancels sibling tasks when one raises, so only the
  first host-tool yield gets its display event emitted reliably. Single tool
  call per turn is the supported case. Multi-tool parallel handling is in the
  v2 backlog.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from amplifier_core import ToolResult

from amplifier_agent_http._host_tool_signal import HostToolYield

logger = logging.getLogger(__name__)


class HostToolProxy:
    """Placeholder Tool for an opencode-declared host tool.

    Constructed per request from one entry of opencode's ``tools[]`` field.
    The OpenAI tool spec shape is::

        {
          "type": "function",
          "function": {
            "name": "read",
            "description": "Read a file from disk",
            "parameters": { ... JSON schema ... }
          }
        }

    The constructor takes the unwrapped ``function`` dict.
    """

    def __init__(self, *, name: str, description: str, parameters: dict[str, Any]) -> None:
        self._name = name
        self._description = description
        self._parameters = parameters

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def input_schema(self) -> dict[str, Any]:
        return self._parameters

    async def execute(self, input_data: Any) -> ToolResult:
        """Yield control back to the host by raising ``HostToolYield``.

        The HTTP face's stream generator catches ``HostToolYield`` and emits the
        ``finish_reason: "tool_calls"`` terminal chunk. The tool_calls SSE
        deltas themselves are emitted by the host-tool hook on ``tool:pre``
        BEFORE this method runs, so the wire is already populated by the time
        we raise.

        ``input_data`` is the arguments dict the LLM produced. It is forwarded
        on the raise so the HTTP face has access if needed (currently the hook
        already serialized the same data).

        Returning normally is unreachable -- the raise is unconditional. The
        ``ToolResult`` return type is on the signature to satisfy the kernel's
        ``Tool`` protocol type checker; the body never executes a ``return``.
        """
        # Normalize argument shape -- the kernel may pass either a dict or a
        # JSON-serialized string depending on the provider's tool-call
        # parsing. The proxy doesn't care about the contents; we only need
        # something to attach to the signal for debugging.
        arguments: dict[str, Any]
        if isinstance(input_data, dict):
            arguments = input_data
        elif isinstance(input_data, str):
            try:
                arguments = json.loads(input_data) if input_data else {}
            except json.JSONDecodeError:
                arguments = {"_raw": input_data}
        else:
            arguments = {"_raw": str(input_data)}

        # tool_call_id is not directly visible to the proxy via the Tool
        # protocol -- the kernel only passes ``input_data``. The HTTP face
        # doesn't actually need it here because the hook on ``tool:pre``
        # already emitted the tool_calls SSE delta with the correct id. We
        # carry the placeholder anyway in case a future consumer needs it.
        logger.debug("HostToolProxy.execute() raising HostToolYield for %r", self._name)
        raise HostToolYield(
            tool_call_id="",  # already emitted by the hook on tool:pre
            name=self._name,
            arguments=arguments,
        )
        # Unreachable -- present only so the return type annotation is honest.
        return ToolResult(success=False, error={"message": "host-tool proxy unreachable"})

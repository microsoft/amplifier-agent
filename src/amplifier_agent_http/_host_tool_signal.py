"""Host-tool yield signal.

When the LLM decides to call a tool that opencode runs host-side, amplifier's
agent loop has nothing to actually execute. The placeholder Tool registered for
the call raises ``HostToolYield`` to escape the orchestrator cleanly.

We deliberately subclass ``BaseException`` (not ``Exception``) so the signal
slips past loop-streaming's narrow ``except Exception`` guards at the tool
dispatch (``_execute_tool_only`` L1133) and outer safety net (L1203), and past
``AmplifierSession.execute()``'s narrow ``except Exception``. It propagates as
far as the HTTP face, which catches it in the chat-completion stream generator
and emits the OpenAI ``finish_reason: "tool_calls"`` terminal chunk before
ending POST 1's SSE stream.

This is intentionally narrower than ``CancelledError``: only one specific signal
class escapes the loop, and only for this one specific exit path. Everything
else still gets caught and reported normally.

POST 2 (the follow-up request opencode sends with the tool result) is handled
by the existing stateless-replay path: opencode includes the prior assistant
turn (with tool_calls) and the new ``{role: "tool", tool_call_id, content}``
message in ``messages[]``, and the next ``run_chat_turn`` invocation reseeds
the kernel context from that history. No cross-POST coroutine bookkeeping is
required for the POC; the v2 backlog covers the in-process resumption path.
"""

from __future__ import annotations

from typing import Any


class HostToolYield(BaseException):
    """Raised by ``HostToolProxy.execute()`` to yield control to the host.

    Carries the LLM's full tool call so the HTTP face can serialize it into
    OpenAI ``delta.tool_calls`` SSE chunks. One yield per call site.

    Attributes
    ----------
    tool_call_id:
        The kernel's tool-call correlation id (set by the provider when parsing
        the LLM response). Round-trips on the wire as ``tool_calls[].id`` and
        on POST 2 as ``messages[-1].tool_call_id``.
    name:
        The host-tool name the LLM picked (e.g. ``"read"``).
    arguments:
        The arguments dict the LLM produced. Serialized to JSON for the wire's
        ``function.arguments`` field.
    """

    def __init__(self, *, tool_call_id: str, name: str, arguments: dict[str, Any]) -> None:
        super().__init__(f"host-tool yield: {name} (tool_call_id={tool_call_id})")
        self.tool_call_id = tool_call_id
        self.name = name
        self.arguments = arguments

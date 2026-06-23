"""Client-authoritative transcript reconciliation for the chat-completions HTTP face.

The chat-completions wire protocol is stateless — every turn the client sends
the full conversation history. With session resume on the server side, the
kernel will have its own stored transcript copy. On divergence (user edited a
past message, opencode rewound, anything), the client's view wins by fiat.

This module is HTTP-face-private. The CLI face (``amplifier-agent run``)
uses a different mechanism (``_runtime.py:_repair_loaded_transcript_if_needed``).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from amplifier_foundation.session import diagnose_transcript, repair_transcript

if TYPE_CHECKING:
    from amplifier_agent_lib.session_store import SessionStore

logger = logging.getLogger(__name__)


def reconcile_client_history(
    *,
    client_messages: list[dict[str, Any]],
    session_id: str,
    store: SessionStore,
) -> list[dict[str, Any]]:
    """Repair the client-sent transcript (if broken), then persist as authoritative.

    Step 1 — Layer-1 repair: run foundation's ``diagnose_transcript`` /
    ``repair_transcript`` against the incoming messages. Catches orphaned
    ``tool_use`` blocks (no paired ``tool_result``), ordering violations, and
    incomplete assistant turns that would otherwise cause Anthropic to reject
    the next LLM call with HTTP 400. Healthy transcripts pass through
    unchanged.

    Step 2 — Persist the (now repaired) client view to the session store so
    the kernel's resume path loads from a clean state.

    Mirrors the CLI face's ``_runtime.py:_repair_loaded_transcript_if_needed``
    pattern, but for the HTTP wire's client-authoritative model: we trust the
    client's view of the conversation, while still defending Anthropic's
    API contract before replay.

    The repair runs every turn but is essentially free on healthy
    transcripts (Layer 1, pure, <10ms — annotate + diagnose).

    Parameters
    ----------
    client_messages:
        The full ``messages`` array from the chat-completions request body,
        already converted to kernel-compatible dicts.
    session_id:
        The deterministic amplifier sid derived from ``X-Client-Session-Id``.
    store:
        The ``SessionStore`` instance the HTTP face shares with the kernel
        resume mechanism.  Same store, same on-disk location.

    Returns
    -------
    list[dict]
        The client's messages, repaired if broken and stripped of any
        ``line_num`` annotations.  Returned for caller's convenience so
        the caller doesn't have to re-reference ``client_messages``
        downstream.
    """
    if client_messages:
        # Foundation's diagnose_transcript prefers line_num annotations for
        # the incomplete-turns fallback path. SessionStore doesn't annotate
        # them, so add them to shallow copies before diagnosing. repair_transcript's
        # output strips line_num itself; we strip again here defensively in case
        # the healthy path is hit (no repair invocation).
        annotated = [{**m, "line_num": i + 1} for i, m in enumerate(client_messages)]
        diagnosis = diagnose_transcript(annotated)

        if diagnosis["status"] != "healthy":
            repaired = repair_transcript(annotated, diagnosis)
            client_messages = [{k: v for k, v in m.items() if k != "line_num"} for m in repaired]
            logger.warning(
                "Client-sent transcript was broken — repaired before reconcile. "
                "failure_modes=%s orphaned_tool_ids=%s misplaced_tool_ids=%s "
                "incomplete_turns=%d entries_before=%d entries_after=%d session=%s",
                diagnosis["failure_modes"],
                diagnosis["orphaned_tool_ids"],
                diagnosis["misplaced_tool_ids"],
                len(diagnosis["incomplete_turns"]),
                len(annotated),
                len(client_messages),
                session_id,
            )

    store.save(
        session_id,
        client_messages,
        metadata={"last_turn": "client_reconciled"},
    )
    return client_messages

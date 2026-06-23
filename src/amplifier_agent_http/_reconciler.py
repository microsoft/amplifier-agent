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

if TYPE_CHECKING:
    from amplifier_agent_lib.session_store import SessionStore

logger = logging.getLogger(__name__)


def reconcile_client_history(
    *,
    client_messages: list[dict[str, Any]],
    session_id: str,
    store: SessionStore,
) -> list[dict[str, Any]]:
    """Persist the client's view as authoritative and return it for replay.

    Chat-completions is client-authoritative: opencode (and any conforming
    OpenAI-compatible client) sends the full conversation every turn.  Whatever
    we have stored locally is at best a copy.  On any divergence the client's
    view wins.

    We persist over the stored copy (idempotent on healthy resumes — same
    content) so the next turn's load is consistent, then return for replay.
    No divergence detection, no special events, no ceremony.

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
        The client's messages, unchanged.  Returned for caller's convenience
        so the caller doesn't have to re-reference ``client_messages``
        downstream.
    """
    store.save(
        session_id,
        client_messages,
        metadata={"last_turn": "client_reconciled"},
    )
    return client_messages

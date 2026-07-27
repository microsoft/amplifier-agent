"""Shared helpers for driving chat-completion turns against the in-DTU HTTP server.

These started life as private functions inside ``test_sigil_dispatch.py``. A second
suite module (``test_skill_body_persistence.py``) needs the identical mechanics, so
they were MOVED here verbatim rather than copied: one definition, one place to fix
a curl-quoting or status-parsing bug.

Why these live in the suite and not in ``framework/harness.py``: ``E2ECase.command``
for ``kind="http"`` is a ``(method, path)`` tuple and the shared runner emits a
literal curl string carrying only an auth header. It cannot send a request body,
which every chat-completions case needs. ``framework/`` stays untouched
(docs/E2E_TESTING.md: "stable; rarely touched").
"""

from __future__ import annotations

import json
import shlex
from uuid import uuid4

from framework import dtu

# Marker curl appends after the body so status is readable from the same stream.
_META = "__META__"


def new_session_id(prefix: str) -> str:
    """Mint a unique session id.

    Uniqueness matters twice over: the session record is append-only per id, so a
    reused id would blend turns and corrupt the classification, and it makes the
    resolved session directory unambiguous.
    """
    return f"e2e-{prefix}-{uuid4().hex[:12]}"


def post_chat(dtu_id: str, base_url: str, token: str, session_id: str, body: dict) -> tuple[str, str]:
    """POST ``body`` to /v1/chat/completions from INSIDE the DTU.

    curl runs in the container so ``localhost`` resolves to the in-DTU server.

    ``X-Session-Id`` pins the on-disk session bucket to ``http-<session_id>``
    (``routes/chat_completions.py``), which is how these tests know which record
    to classify. The alternative, picking the newest directory by mtime, would
    race any other traffic on the shared server.

    ``stream`` is forced off so the reply is one buffered JSON body. The payload
    goes through ``shlex.quote``; these bodies carry adversarial punctuation and a
    hand-rolled quote wrapper breaks on the first apostrophe.

    Returns ``(http_status, raw_body)``.
    """
    payload = json.dumps({**body, "stream": False})
    cmd = (
        f"curl -s -X POST {base_url}/v1/chat/completions "
        f"-H 'Authorization: Bearer {token}' "
        f"-H 'Content-Type: application/json' "
        f"-H 'X-Session-Id: {session_id}' "
        f"-w '\\n{_META}%{{http_code}}' "
        f"--data-binary {shlex.quote(payload)}"
    )
    result = dtu.exec_json(dtu_id, ["bash", "-lc", cmd])
    assert result.get("exit_code") == 0, f"curl failed: exit={result.get('exit_code')} stderr={result.get('stderr')}"

    raw = result.get("stdout", "")
    raw_body, _, status_line = raw.rpartition(f"\n{_META}")
    return status_line.strip(), raw_body


def assistant_text(raw_body: str) -> str:
    """Extract ``choices[0].message.content`` from a non-streaming completion."""
    obj = json.loads(raw_body)
    return obj["choices"][0]["message"]["content"] or ""

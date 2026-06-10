"""Session persistence for the AAA engine (A2 — CR-1).

Persists session transcript as JSONL and metadata as JSON under
``<root>/sessions/<session_id>/``.

Pattern lifted near-verbatim from ``amplifier_app_cli.session_store`` and
trimmed to the minimal contract required by Design §4.6.

Note: ``write_with_backup`` from ``amplifier_foundation`` is synchronous — it
must be called without ``await``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from amplifier_foundation import write_with_backup

from amplifier_agent_lib.persistence import workspaces_root

logger = logging.getLogger(__name__)


class SessionStore:
    """JSONL transcript + JSON metadata persistence.

    Layout::

        <root>/sessions/<session_id>/transcript.jsonl
        <root>/sessions/<session_id>/metadata.json
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def session_dir(self, session_id: str) -> Path:
        """Return the directory used to persist ``session_id``."""
        return self.root / "sessions" / session_id

    def save(
        self,
        session_id: str,
        transcript: list[dict],
        metadata: dict,
    ) -> None:
        """Persist ``transcript`` (JSONL) and ``metadata`` (JSON) for ``session_id``."""
        d = self.session_dir(session_id)
        d.mkdir(parents=True, exist_ok=True)

        transcript_content = "\n".join(json.dumps(msg) for msg in transcript)
        write_with_backup(d / "transcript.jsonl", transcript_content)
        write_with_backup(d / "metadata.json", json.dumps(metadata, indent=2))

    def load(self, session_id: str) -> tuple[list[dict], dict] | None:
        """Load persisted state for ``session_id``.

        Checks the current workspace first; if absent, walks every other
        workspace under ``workspaces_root()`` and returns the first match
        (D10 cross-workspace resume fallback). Returns ``(transcript,
        metadata)`` or ``None`` if found nowhere.
        """
        found = self._read_session_dir(self.session_dir(session_id))
        if found is not None:
            return found

        ws_root = workspaces_root()
        if not ws_root.exists():
            return None
        current_ws = self.root.name
        for ws_dir in ws_root.iterdir():
            if not ws_dir.is_dir() or ws_dir.name == current_ws:
                continue
            candidate = ws_dir / "sessions" / session_id
            found = self._read_session_dir(candidate)
            if found is not None:
                logger.info(
                    "resume: found %s in workspace %s (current=%s)",
                    session_id,
                    ws_dir.name,
                    current_ws,
                )
                return found
        return None

    def _read_session_dir(self, d: Path) -> tuple[list[dict], dict] | None:
        """Read transcript.jsonl + metadata.json from ``d`` or return None."""
        transcript_file = d / "transcript.jsonl"
        metadata_file = d / "metadata.json"
        if not transcript_file.exists():
            return None
        transcript: list[dict] = []
        raw = transcript_file.read_text(encoding="utf-8")
        for line in raw.splitlines():
            if line:
                transcript.append(json.loads(line))
        metadata: dict = {}
        if metadata_file.exists():
            metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
        return transcript, metadata

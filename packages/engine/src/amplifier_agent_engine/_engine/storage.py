"""Amplifier session layout, per-turn commit records, and process-owned session leases."""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from amplifier_foundation.io.files import write_with_backup
from amplifier_foundation.serialization import sanitize_message
from amplifier_foundation.session import SessionHistoryError, SessionHistoryStore

from .._records import (
    AgentError,
    ConversationMessage,
    TextPart,
    TurnInput,
    TurnRecord,
    TurnResult,
    Usage,
    UsageEntry,
)

logger = logging.getLogger(__name__)

TURNS_FILENAME = "turns.jsonl"

_RECORDS = {
    cls.__name__: cls
    for cls in (ConversationMessage, TextPart, TurnInput, TurnRecord, TurnResult, Usage, UsageEntry)
}


def storage_error() -> AgentError:
    return AgentError(
        "internal_failed",
        "session",
        "The local session transcript could not be read or committed.",
        "Check the storage directory, available space, and transcript integrity before resuming.",
    )


def session_error(code: str) -> AgentError:
    message, remedy = {
        "already_exists": ("The session id already exists.", "Create a session with another id."),
        "not_found": (
            "The durable session does not exist.",
            "Use an id returned by list_sessions.",
        ),
        "session_in_use": (
            "The durable session already has a live handle.",
            "Close the existing handle before resuming or deleting this session.",
        ),
    }[code]
    return AgentError(code, "session", message, remedy)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _encode(value: Any) -> Any:
    if isinstance(value, AgentError):
        return {"error": _encode(vars(value))}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        if type(value).__name__ not in _RECORDS:
            raise ValueError("Unsupported transcript record")
        return {"record": type(value).__name__, "fields": _encode(vars(value))}
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Nonfinite transcript cost")
        return {"decimal": str(value)}
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("Nonstring transcript key")
        return {"mapping": {key: _encode(item) for key, item in value.items()}}
    if isinstance(value, list):
        return [_encode(item) for item in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise ValueError("Unsupported transcript value")


def _decode(value: Any) -> Any:
    if isinstance(value, list):
        return [_decode(item) for item in value]
    if not isinstance(value, dict):
        return value
    if set(value) == {"mapping"} and isinstance(value["mapping"], dict):
        return {key: _decode(item) for key, item in value["mapping"].items()}
    if set(value) == {"decimal"}:
        result = Decimal(value["decimal"])
        if result.is_finite():
            return result
    if set(value) == {"error"}:
        fields = _decode(value["error"])
        names = {"code", "category", "message", "remedy", "retryable", "correlation_id", "details"}
        error = AgentError(**{name: item for name, item in fields.items() if name in names})
        for name, item in fields.items():
            if name not in names:
                if "." not in name or name.startswith("_"):
                    raise ValueError("Unknown transcript error field")
                setattr(error, name, item)
        return error
    if set(value) == {"record", "fields"} and value["record"] in _RECORDS:
        cls = _RECORDS[value["record"]]
        fields = _decode(value["fields"])
        names = {field.name for field in dataclasses.fields(cls)}
        record = cls(**{name: item for name, item in fields.items() if name in names})
        for name, item in fields.items():
            if name not in names:
                if "." not in name or name.startswith("_"):
                    raise ValueError("Unknown transcript record field")
                setattr(record, name, item)
        return record
    raise ValueError("Invalid transcript encoding")


@dataclass
class CommittedTurn:
    """One `turns.jsonl` line: a public turn record and the transcript length at its commit."""

    turn: TurnRecord
    messages: int

    def dumps(self) -> str:
        return json.dumps(
            {"turn": _encode(self.turn), "messages": self.messages},
            allow_nan=False,
            separators=(",", ":"),
        )

    @classmethod
    def loads(cls, text: str) -> CommittedTurn:
        def invalid(value: str) -> None:
            raise ValueError(f"Invalid JSON number {value}")

        data = json.loads(text, parse_constant=invalid)
        if not isinstance(data, dict) or set(data) != {"turn", "messages"}:
            raise ValueError("Invalid turn record envelope")
        turn = _decode(data["turn"])
        count = data["messages"]
        if not isinstance(turn, TurnRecord) or type(count) is not int or count < 0:
            raise ValueError("Invalid turn record")
        return cls(turn, count)


@dataclass
class LoadedSession:
    messages: list[dict[str, Any]]
    turns: list[CommittedTurn]
    metadata: dict[str, Any]


class SessionLease:
    def __init__(self, descriptor: int) -> None:
        self._descriptor: int | None = descriptor

    def close(self) -> None:
        if self._descriptor is not None:
            os.close(self._descriptor)
            self._descriptor = None


class SessionStore:
    def __init__(self, root: Path, workspace: str) -> None:
        self.workspace = workspace
        self._workspace_dir = root / "workspaces" / workspace
        self.sessions_dir = self._workspace_dir / "sessions"
        self._locks_dir = self._workspace_dir / "locks"

    def session_dir(self, session_id: str) -> Path:
        return self.sessions_dir / session_id

    def _history(self, session_id: str) -> SessionHistoryStore:
        return SessionHistoryStore(self.session_dir(session_id), session_id=session_id)

    def _turns_path(self, session_id: str) -> Path:
        return self.session_dir(session_id) / TURNS_FILENAME

    @staticmethod
    def _has_transcript(directory: Path) -> bool:
        transcript = directory / "transcript.jsonl"
        return transcript.exists() or transcript.with_suffix(".jsonl.backup").exists()

    def exists(self, session_id: str) -> bool:
        return self._has_transcript(self.session_dir(session_id))

    def _lease(self, session_id: str, conflict: str) -> SessionLease:
        descriptor = None
        try:
            self._locks_dir.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(self._locks_dir / session_id, os.O_CREAT | os.O_RDWR, 0o600)
            if os.name == "nt":
                import msvcrt

                os.write(descriptor, b"\0")
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return SessionLease(descriptor)
        except BlockingIOError as exc:
            if descriptor is not None:
                os.close(descriptor)
            raise session_error(conflict) from exc
        except OSError as exc:
            if descriptor is not None:
                os.close(descriptor)
            if os.name == "nt" and exc.errno in (13, 36):
                raise session_error(conflict) from exc
            raise storage_error() from exc

    def create_lease(self, session_id: str) -> SessionLease:
        if self.exists(session_id):
            raise session_error("already_exists")
        lease = self._lease(session_id, "already_exists")
        try:
            if self.exists(session_id):
                raise session_error("already_exists")
            return lease
        except BaseException:
            lease.close()
            raise

    def reserve(
        self,
        session_id: str,
        metadata: dict[str, Any],
        messages: list[dict[str, Any]],
        turns: list[CommittedTurn],
    ) -> None:
        """Create the session directory holding a transcript, so the id exists from now on."""
        directory = self.session_dir(session_id)
        created = not directory.exists()
        try:
            directory.mkdir(parents=True, exist_ok=True)
            history = self._history(session_id)
            history.save_messages(messages, preserve_system=True, sanitizer=sanitize_message)
            if turns:
                self._write_turns(session_id, turns)
            history.save_metadata(
                {
                    "session_id": session_id,
                    "created": now(),
                    "last_updated": now(),
                    "workspace": self.workspace,
                    "persistence": "durable",
                    "turn_count": len(turns),
                    **metadata,
                }
            )
        except Exception as exc:
            if created:
                shutil.rmtree(directory, ignore_errors=True)
            else:
                for name in ("transcript.jsonl", TURNS_FILENAME, "metadata.json"):
                    Path(directory / name).unlink(missing_ok=True)
            raise storage_error() from exc

    def _write_turns(self, session_id: str, turns: list[CommittedTurn]) -> None:
        content = "".join(turn.dumps() + "\n" for turn in turns)
        write_with_backup(self._turns_path(session_id), content)

    def commit(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
        turns: list[CommittedTurn],
        metadata: dict[str, Any],
    ) -> None:
        """Persist a settled turn: transcript, then the commit point, then session facts."""
        history = self._history(session_id)
        try:
            history.save_messages(messages, preserve_system=True, sanitizer=sanitize_message)
            self._write_turns(session_id, turns)
        except Exception as exc:
            raise storage_error() from exc
        try:
            history.save_metadata(
                {"turn_count": len(turns), "last_updated": now(), **metadata},
                merge_metadata=True,
            )
        except Exception:
            logger.warning("Session %s metadata was not refreshed after commit", session_id)

    def _read_turns(self, session_id: str) -> list[CommittedTurn]:
        path = self._turns_path(session_id)
        present = False
        for candidate in (path, path.with_suffix(".jsonl.backup")):
            try:
                text = candidate.read_text(encoding="utf-8")
            except FileNotFoundError:
                continue
            present = True
            try:
                return [CommittedTurn.loads(line) for line in text.splitlines() if line.strip()]
            except (ValueError, TypeError, KeyError):
                continue
        if present:
            raise ValueError("Invalid turn records")
        return []

    def load(self, session_id: str) -> LoadedSession:
        """Read the committed state: turns, the transcript truncated to the commit point, facts."""
        history = self._history(session_id)
        try:
            turns = self._read_turns(session_id)
            messages = history.load_messages()
            metadata = history.load_metadata()
        except (SessionHistoryError, OSError, ValueError, TypeError, KeyError) as exc:
            raise storage_error() from exc
        boundary = turns[-1].messages if turns else 0
        if len(messages) < boundary:
            raise storage_error()
        return LoadedSession(messages[:boundary], turns, metadata)

    def resume(self, session_id: str) -> tuple[SessionLease, LoadedSession]:
        if not self.exists(session_id):
            raise session_error("not_found")
        lease = self._lease(session_id, "session_in_use")
        try:
            if not self.exists(session_id):
                raise session_error("not_found")
            return lease, self.load(session_id)
        except BaseException:
            lease.close()
            raise

    def list_ids(self) -> list[str]:
        if not self.sessions_dir.is_dir():
            return []
        try:
            return sorted(
                path.name
                for path in self.sessions_dir.iterdir()
                if path.is_dir() and "_" not in path.name and self._has_transcript(path)
            )
        except OSError as exc:
            raise storage_error() from exc

    def delete(self, session_id: str) -> None:
        if not self.exists(session_id):
            raise session_error("not_found")
        lease = self._lease(session_id, "session_in_use")
        try:
            if not self.exists(session_id):
                raise session_error("not_found")
            shutil.rmtree(self.session_dir(session_id))
        except OSError as exc:
            raise storage_error() from exc
        finally:
            lease.close()

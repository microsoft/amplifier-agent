"""Transactional local checkpoints and process-owned session leases."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

from .._records import (
    AgentError,
    ConversationMessage,
    SessionRecord,
    TextPart,
    TurnInput,
    TurnRecord,
    TurnResult,
    Usage,
    UsageEntry,
)

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


@dataclasses.dataclass
class Checkpoint:
    provider: str
    model: str
    accepted: bool
    inherited: bool
    history: list[TurnRecord]
    runtime: dict[str, Any]

    def dumps(self) -> str:
        encoded = _encode(vars(self))
        checksum = hashlib.sha256(
            json.dumps(encoded, allow_nan=False, sort_keys=True).encode()
        ).hexdigest()
        return json.dumps(
            {"version": 1, "checkpoint": encoded, "checksum": checksum},
            allow_nan=False,
            separators=(",", ":"),
        )

    @classmethod
    def loads(cls, text: str) -> Checkpoint:
        def invalid(value: str) -> None:
            raise ValueError(f"Invalid JSON number {value}")

        data = json.loads(text, parse_constant=invalid)
        if not isinstance(data, dict) or set(data) != {"version", "checkpoint", "checksum"}:
            raise ValueError("Invalid transcript envelope")
        if data["version"] != 1:
            raise ValueError("Unsupported transcript version")
        checksum = hashlib.sha256(
            json.dumps(data["checkpoint"], allow_nan=False, sort_keys=True).encode()
        ).hexdigest()
        if data["checksum"] != checksum:
            raise ValueError("Transcript checksum mismatch")
        value = cls(**_decode(data["checkpoint"]))
        if (
            not isinstance(value.provider, str)
            or not value.provider
            or not isinstance(value.model, str)
            or not value.model
            or type(value.accepted) is not bool
            or type(value.inherited) is not bool
            or not isinstance(value.history, list)
            or not all(isinstance(turn, TurnRecord) for turn in value.history)
            or not isinstance(value.runtime, dict)
        ):
            raise ValueError("Invalid transcript checkpoint")
        return value


class SessionLease:
    def __init__(self, descriptor: int) -> None:
        self._descriptor: int | None = descriptor

    def close(self) -> None:
        if self._descriptor is not None:
            os.close(self._descriptor)
            self._descriptor = None


class SessionStore:
    def __init__(self, root: Path, workspace: str) -> None:
        self._directory = root / "workspaces" / workspace
        self._database = self._directory / "sessions.sqlite3"

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = None
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self._database, timeout=0.2)
            connection.execute("PRAGMA synchronous=FULL")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError("Unsupported session database version")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, checkpoint TEXT NOT NULL)"
            )
            if version == 0:
                connection.execute("PRAGMA user_version=1")
            with connection:
                yield connection
        except (OSError, sqlite3.Error, ValueError, TypeError, KeyError) as exc:
            raise storage_error() from exc
        finally:
            if connection is not None:
                connection.close()

    def exists(self, session_id: str) -> bool:
        if not self._database.exists():
            return False
        with self._connection() as connection:
            return (
                connection.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone()
                is not None
            )

    def _lease(self, session_id: str, conflict: str) -> SessionLease:
        descriptor = None
        try:
            directory = self._directory / "locks"
            directory.mkdir(parents=True, exist_ok=True)
            key = hashlib.sha256(session_id.encode()).hexdigest()
            descriptor = os.open(directory / key, os.O_CREAT | os.O_RDWR, 0o600)
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

    def resume(self, session_id: str) -> tuple[SessionLease, Checkpoint]:
        if not self.exists(session_id):
            raise session_error("not_found")
        lease = self._lease(session_id, "session_in_use")
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "SELECT checkpoint FROM sessions WHERE id=?", (session_id,)
                ).fetchone()
                if row is None:
                    raise session_error("not_found")
                checkpoint = Checkpoint.loads(row[0])
            return lease, checkpoint
        except BaseException:
            lease.close()
            raise

    def save(self, session_id: str, checkpoint: Checkpoint, *, create: bool = False) -> None:
        with self._connection() as connection:
            payload = checkpoint.dumps()
            if create:
                try:
                    connection.execute("INSERT INTO sessions VALUES (?, ?)", (session_id, payload))
                except sqlite3.IntegrityError as exc:
                    raise session_error("already_exists") from exc
            else:
                result = connection.execute(
                    "UPDATE sessions SET checkpoint=? WHERE id=?", (payload, session_id)
                )
                if result.rowcount != 1:
                    raise session_error("not_found")

    def list(self) -> list[SessionRecord]:
        if not self._database.exists():
            return []
        with self._connection() as connection:
            return [
                SessionRecord(row[0], "durable")
                for row in connection.execute("SELECT id FROM sessions ORDER BY id")
            ]

    def delete(self, session_id: str) -> None:
        if not self.exists(session_id):
            raise session_error("not_found")
        lease = self._lease(session_id, "session_in_use")
        try:
            with self._connection() as connection:
                result = connection.execute("DELETE FROM sessions WHERE id=?", (session_id,))
                if result.rowcount != 1:
                    raise session_error("not_found")
        finally:
            lease.close()

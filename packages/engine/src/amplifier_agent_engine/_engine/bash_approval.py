"""One prepared Bash invocation, rechecked at the process-admission boundary."""

from collections.abc import Callable
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any

from amplifier_agent_engine._records import ReusableApproval, _ToolNotExecutedError

PRODUCER = "com.microsoft.amplifier-agent.approvals/1"
# Rechecked at admission but outside the key. The environment holds volatile and secret
# values, and the registry changes with engine releases and with unrelated tools.
UNKEYED = frozenset({"environment", "registry"})


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def identity(path: Path, directory: bool = False) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    before = resolved.stat()
    if directory:
        if not stat.S_ISDIR(before.st_mode):
            raise ValueError("Working directory is not a directory")
        return {
            "path": str(resolved),
            "device": before.st_dev,
            "inode": before.st_ino,
            "mode": before.st_mode,
            "owner": before.st_uid,
            "group": before.st_gid,
        }
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("Executor is not a regular file")
    with resolved.open("rb") as source:
        opened = os.fstat(source.fileno())
        fingerprint = hashlib.file_digest(source, "sha256").hexdigest()
        after = os.fstat(source.fileno())
    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if path.resolve(strict=True) != resolved or any(
        getattr(before, name) != getattr(current, name)
        for current in (opened, after, resolved.stat())
        for name in fields
    ):
        raise ValueError("Executor changed during inspection")
    return {"path": str(resolved), "sha256": fingerprint, **{name: getattr(after, name) for name in fields}}


class BashApproval:
    def __init__(self, facts: Callable[[], dict[str, Any]], allowed: Callable[[], bool]) -> None:
        self.facts = facts
        self.allowed = allowed
        self.expected = copy.deepcopy(facts())
        self.active = True
        self.consumed = False
        self.error: _ToolNotExecutedError | None = None
        self.descriptor = ReusableApproval(
            1,
            PRODUCER,
            digest({"producer": PRODUCER, "scope": {k: v for k, v in self.expected.items() if k not in UNKEYED}}),
            "Run this exact Bash invocation with the same direct shell and directory.",
            "request",
        )

    def admit(self, command: str, timeout: int | None, background: bool) -> tuple[str, str, dict[str, str]]:
        expected = self.expected
        invocation = expected["invocation"]
        try:
            current = self.facts()
        except (OSError, ValueError, RuntimeError):
            current = None
        if (
            not self.active
            or self.consumed
            or not self.allowed()
            or current != expected
            or command != invocation["command"]
            or background != invocation["background"]
            or (not background and timeout != invocation["timeout"])
        ):
            self.error = _ToolNotExecutedError(
                "The prepared Bash authority changed or expired. Request a new approval before running this command.",
                "Start another turn to request approval for the current invocation.",
            )
            raise self.error
        # There is no await between this consumption and the owned spawn call.
        self.consumed = True
        return expected["shell"]["path"], expected["directory"]["path"], dict(expected["environment"])

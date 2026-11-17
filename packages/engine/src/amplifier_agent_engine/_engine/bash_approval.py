"""One prepared Bash invocation, rechecked at the process-admission boundary."""

from collections.abc import Callable
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from types import CodeType, FunctionType, ModuleType
from typing import Any

from amplifier_agent_engine._records import ReusableApproval, _ToolNotExecutedError

PRODUCER = "com.microsoft.amplifier-agent.approvals/1"


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def module_identity(module: ModuleType) -> dict[str, str | None]:
    """Bind loaded Python instructions, including nested spawn code in frozen builds.

    Frozen modules need not retain a readable source file. Source path hashes also
    describe disk contents rather than the instructions this process will execute.
    """
    functions: list[tuple[str, str]] = []

    def constant(value: Any) -> Any:
        if isinstance(value, CodeType):
            return {
                "code": value.co_code.hex(),
                "constants": [constant(item) for item in value.co_consts],
                "names": value.co_names,
                "variables": value.co_varnames,
                "free": value.co_freevars,
                "cells": value.co_cellvars,
                "arguments": [value.co_argcount, value.co_posonlyargcount, value.co_kwonlyargcount],
                "locals": value.co_nlocals,
                "stack": value.co_stacksize,
                "flags": value.co_flags,
                "exceptions": value.co_exceptiontable.hex(),
            }
        if isinstance(value, tuple):
            return {"tuple": [constant(item) for item in value]}
        if isinstance(value, frozenset):
            return {"frozenset": sorted(digest(constant(item)) for item in value)}
        if isinstance(value, bytes):
            return {"bytes": value.hex()}
        if value is Ellipsis:
            return {"ellipsis": True}
        if isinstance(value, complex):
            return {"complex": [value.real, value.imag]}
        if value is None or type(value) in (str, int, float, bool):
            return value
        raise ValueError("Executor has an unsupported code constant")

    def record(name: str, value: Any) -> None:
        if isinstance(value, (classmethod, staticmethod)):
            value = value.__func__
        if isinstance(value, FunctionType):
            functions.append((name, digest(constant(value.__code__))))

    for name, value in sorted(vars(module).items()):
        if getattr(value, "__module__", None) != module.__name__:
            continue
        record(name, value)
        if isinstance(value, type):
            for member, function in sorted(vars(value).items()):
                record(f"{name}.{member}", function)
    if not functions:
        raise ValueError("Executor code is unavailable")
    return {"module": module.__name__, "python": sys.implementation.cache_tag, "sha256": digest(functions)}


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
            digest({"producer": PRODUCER, "scope": self.expected}),
            "Run this exact Bash invocation with the same direct shell, directory and environment.",
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
                "The prepared Bash authority changed or expired. Request a new approval before running this command."
            )
            raise self.error
        # There is no await between this consumption and the owned spawn call.
        self.consumed = True
        return expected["shell"]["path"], expected["directory"]["path"], dict(expected["environment"])

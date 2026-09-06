"""Produce bounded approval previews without changing executor arguments."""

from __future__ import annotations

import json
import re
from itertools import chain
from typing import Any

SUMMARY_LIMIT = 4096
_CONTEXT_LIMIT = 1024
_TRUNCATED = " [truncated]"
_PRIORITY = (
    "command", "file_path", "path", "url", "query", "pattern", "name", "instruction",
    "old_string", "new_string", "content", "working_directory",
)
_SECRETS = (
    "password", "passwd", "pwd", "passphrase", "secret", "secrets", "secretkey", "token",
    "apikey", "accesskey", "accesskeyid", "authorization", "cookie", "privatekey",
    "credential", "credentials",
)


class _Preview:
    def __init__(self, limit: int = SUMMARY_LIMIT) -> None:
        self.parts: list[str] = []
        self.remaining = limit - len(_TRUNCATED)
        self.truncated = False
        self.nodes = 64

    def append(self, text: str) -> None:
        self.parts.append(text[:self.remaining])
        if len(text) > self.remaining:
            self.truncated = True
        self.remaining = max(0, self.remaining - len(text))

    def string(self, value: str, limit: int = 1024) -> None:
        if len(value) > limit:
            value = value[:limit] + _TRUNCATED
        self.append(json.dumps(value, ensure_ascii=True))

    def value(self, value: Any, depth: int = 0) -> None:
        if not self.remaining:
            self.truncated = True
            return
        if self.nodes == 0 or depth > 4:
            self.append('"[truncated]"')
            return
        self.nodes -= 1
        if isinstance(value, dict):
            self.append("{")
            keys = chain((key for key in _PRIORITY if key in value),
                         (key for key in value if key not in _PRIORITY))
            for index, key in enumerate(keys):
                if index:
                    self.append(", ")
                if index == 12 or not self.remaining or not self.nodes:
                    self.append("[truncated]")
                    break
                self.string(key, 128)
                self.append(": ")
                normalized = re.sub(r"[^a-z0-9]", "", key[:128].lower())
                if len(key) > 128:
                    self.string("[truncated: value omitted for long key]")
                elif normalized in ("auth", "authentication") or normalized.endswith(_SECRETS):
                    self.string("[redacted]")
                else:
                    self.value(value[key], depth + 1)
            self.append("}")
        elif isinstance(value, list):
            self.append("[")
            for index, item in enumerate(value):
                if index:
                    self.append(", ")
                if index == 12 or not self.remaining or not self.nodes:
                    self.append("[truncated]")
                    break
                self.value(item, depth + 1)
            self.append("]")
        elif isinstance(value, str):
            self.string(value)
        elif isinstance(value, int) and value.bit_length() > 1024:
            self.string("[truncated: large integer]")
        else:
            self.append(json.dumps(value, ensure_ascii=True, allow_nan=False))

    def finish(self) -> str:
        return "".join(self.parts) + (_TRUNCATED if self.truncated else "")


def approval_summary(
    source: str, name: str, arguments: dict[str, Any], context: dict[str, Any] | None = None,
) -> str:
    preview = _Preview()
    preview.append(f"Run {source} tool ")
    preview.string(name, 128)
    preview.append(".")
    if context:
        context_preview = _Preview(_CONTEXT_LIMIT)
        context_preview.value(context)
        preview.append(" Context: ")
        preview.append(context_preview.finish())
        preview.append(".")
    preview.append(" Arguments: ")
    preview.value(arguments)
    return preview.finish()

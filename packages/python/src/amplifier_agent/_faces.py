"""File snapshots, file changes, and tool-call sightings shared by the faces over this binding."""

import asyncio
from dataclasses import dataclass
import difflib
import os
from pathlib import Path
from typing import Any, Literal

from amplifier_agent._records import ToolCall

# Built-in tools whose effect is writing the files their arguments name.
FILE_TOOLS = ("write_file", "edit_file", "apply_patch")
# apply_patch's V4A markers that name a file the patch adds, updates, deletes, or moves to.
_PATCH_MARKERS = ("*** Add File: ", "*** Update File: ", "*** Delete File: ", "*** Move to: ")

# A file's bytes before or after a call: None when it does not exist, False when it cannot be read.
Snapshot = list[tuple[Path, bytes | Literal[False] | None]]


def targets(call: ToolCall, working_directory: Path) -> list[Path]:
    """The files a built-in file tool's arguments name, resolved as the tool resolves them."""
    if call.source != "built-in" or call.name not in FILE_TOOLS:
        return []
    names: list[str] = []
    if call.name == "apply_patch":
        patch = call.arguments.get("patch")
        if isinstance(patch, str):
            for line in patch.split("\n"):
                line = line.rstrip()
                names.extend(line[len(marker) :] for marker in _PATCH_MARKERS if line.startswith(marker))
    else:
        name = call.arguments.get("file_path")
        # An @mention path resolves through the tool's own bundle lookup, which a face cannot repeat.
        if isinstance(name, str) and name and not name.startswith("@"):
            names.append(name)
    paths: list[Path] = []
    for name in names:
        path = Path(name) if call.name == "apply_patch" else Path(name).expanduser()
        path = Path(os.path.normpath(path if path.is_absolute() else working_directory / path))
        if path not in paths:
            paths.append(path)
    return paths


def _read(path: Path) -> bytes | Literal[False] | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError:
        return False


def _text(data: bytes | None) -> str | None:
    if data is None:
        return ""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return None if "\x00" in text else text


def snapshot(paths: list[Path]) -> Snapshot:
    return [(path, _read(path)) for path in paths]


@dataclass(frozen=True)
class TextChange:
    """A text file's content before and after a call."""

    path: Path
    # None when the call added the file.
    old_text: str | None
    # Empty when the call deleted the file.
    new_text: str
    deleted: bool


def text_changes(before: Snapshot) -> list[TextChange]:
    """One entry per text file whose content differs from `before`, in the order of `before`."""
    changes = []
    for path, old in before:
        new = _read(path)
        if old is False or new is False or old == new:
            continue
        old_text, new_text = _text(old), _text(new)
        if old_text is None or new_text is None:
            continue
        changes.append(TextChange(path, None if old is None else old_text, new_text, new is None))
    return changes


def file_changes(before: Snapshot, working_directory: Path) -> list[dict[str, Any]]:
    """One unified patch with line counts per text file whose content differs from `before`."""
    changes = []
    for change in text_changes(before):
        try:
            shown = change.path.relative_to(working_directory).as_posix()
        except ValueError:
            shown = str(change.path)
        added = change.old_text is None
        lines = []
        for line in difflib.unified_diff(
            (change.old_text or "").splitlines(keepends=True),
            change.new_text.splitlines(keepends=True),
            "/dev/null" if added else shown,
            "/dev/null" if change.deleted else shown,
        ):
            lines.append(line if line.endswith("\n") else f"{line}\n\\ No newline at end of file\n")
        body = lines[2:]
        changes.append(
            {
                "path": shown,
                "change": "added" if added else "deleted" if change.deleted else "modified",
                "patch": "".join(lines),
                "additions": sum(line.startswith("+") for line in body),
                "deletions": sum(line.startswith("-") for line in body),
            }
        )
    return changes


class CallSightings:
    """The tool calls a turn's consumer has seen, and a wait for one it has not seen yet."""

    def __init__(self) -> None:
        self._calls: dict[str, ToolCall] = {}
        self._seen: dict[str, asyncio.Event] = {}

    def _signal(self, call_id: str) -> asyncio.Event:
        return self._seen.setdefault(call_id, asyncio.Event())

    def see(self, call: ToolCall) -> None:
        self._calls[call.call_id] = call
        self._signal(call.call_id).set()

    def get(self, call_id: str) -> ToolCall | None:
        return self._calls.get(call_id)

    async def wait(self, call_id: str) -> ToolCall:
        """The call once its consumer has seen it."""
        # The engine emits tool_call before it asks for approval, so the consumer sees the call while this waits.
        await self._signal(call_id).wait()
        return self._calls[call_id]

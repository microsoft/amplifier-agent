"""Tool-call kinds, titles, locations, previews, and results in ACP's shape (acp-face.v1 section 6)."""

import json
import os
from pathlib import Path
import re
import signal
from typing import Any, Literal

from acp.schema import (
    ContentToolCallContent,
    FileEditToolCallContent,
    TerminalToolCallContent,
    TextContentBlock,
    ToolCallLocation,
)
from amplifier_agent import ToolCall, ToolResolution
from amplifier_agent._faces import TextChange, targets

Kind = Literal["read", "edit", "delete", "move", "search", "execute", "think", "fetch", "switch_mode", "other"]
Content = ContentToolCallContent | FileEditToolCallContent | TerminalToolCallContent

_KINDS: dict[str, Kind] = {
    "read_file": "read",
    "write_file": "edit",
    "edit_file": "edit",
    "apply_patch": "edit",
    "glob": "search",
    "grep": "search",
    "bash": "execute",
    "web_fetch": "fetch",
    "web_search": "fetch",
    "delegate": "think",
    "load_skill": "other",
}


def _argument(call: ToolCall, name: str) -> str:
    value = call.arguments.get(name)
    return value if isinstance(value, str) else ""


def _first_line(text: str) -> str:
    lines = text.strip().splitlines()
    return lines[0] if lines else ""


def _path(name: str, working_directory: Path) -> Path | None:
    """The absolute path a file tool resolves `name` to, or None for an @mention or no name."""
    if not name or name.startswith("@"):
        return None
    path = Path(name).expanduser()
    return Path(os.path.normpath(path if path.is_absolute() else working_directory / path))


def _shown(path: Path, working_directory: Path) -> str:
    try:
        return path.relative_to(working_directory).as_posix()
    except ValueError:
        return str(path)


def _shown_name(name: str, working_directory: Path) -> str:
    path = _path(name, working_directory)
    return name if path is None else _shown(path, working_directory)


def is_builtin(call: ToolCall, name: str) -> bool:
    return call.source == "built-in" and call.name == name


def kind(call: ToolCall) -> Kind:
    return _KINDS.get(call.name, "other") if call.source == "built-in" else "other"


def title(call: ToolCall, working_directory: Path) -> str:
    if call.source != "built-in" or call.name not in _KINDS:
        return call.name
    match call.name:
        case "read_file":
            return f"Read {_shown_name(_argument(call, 'file_path'), working_directory)}"
        case "write_file":
            return f"Write {_shown_name(_argument(call, 'file_path'), working_directory)}"
        case "edit_file":
            return f"Edit {_shown_name(_argument(call, 'file_path'), working_directory)}"
        case "apply_patch":
            paths = ", ".join(_shown(path, working_directory) for path in targets(call, working_directory))
            return f"Patch {paths}" if paths else "Patch"
        case "glob":
            return f"Find {_argument(call, 'pattern')}"
        case "grep":
            return f"Search {_argument(call, 'pattern')}"
        case "bash":
            return _first_line(_argument(call, "command")) or call.name
        case "web_fetch":
            return f"Fetch {_argument(call, 'url')}"
        case "web_search":
            return f"Search the web: {_argument(call, 'query')}"
        case "delegate":
            return f"Delegate: {_first_line(_argument(call, 'instruction'))}"
        case _:
            return f"Load skill {_argument(call, 'name')}"


def locations(call: ToolCall, working_directory: Path) -> list[ToolCallLocation] | None:
    if not is_builtin(call, "read_file"):
        return None
    path = _path(_argument(call, "file_path"), working_directory)
    if path is None:
        return None
    offset = call.arguments.get("offset")
    line = offset if type(offset) is int and offset >= 1 else 1
    return [ToolCallLocation(path=str(path), line=line)]


def _current_text(path: Path) -> str | Literal[False] | None:
    """The file's text, None when it does not exist, False when it is unreadable or not text."""
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError:
        return False
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return False if "\x00" in text else text


def preview(call: ToolCall, working_directory: Path) -> list[Content] | None:
    """The diff a write_file or edit_file call's arguments describe, before it runs."""
    if is_builtin(call, "write_file"):
        path = _path(_argument(call, "file_path"), working_directory)
        content = call.arguments.get("content")
        if path is None or not isinstance(content, str):
            return None
        current = _current_text(path)
        if current is False:
            return None
        return [FileEditToolCallContent(type="diff", path=str(path), old_text=current, new_text=content)]
    if is_builtin(call, "edit_file"):
        path = _path(_argument(call, "file_path"), working_directory)
        old, new = call.arguments.get("old_string"), call.arguments.get("new_string")
        if path is None or not isinstance(old, str) or not isinstance(new, str):
            return None
        return [FileEditToolCallContent(type="diff", path=str(path), old_text=old, new_text=new)]
    return None


def diffs(changes: list[TextChange]) -> list[Content]:
    return [
        FileEditToolCallContent(type="diff", path=str(change.path), old_text=change.old_text, new_text=change.new_text)
        for change in changes
    ]


def _text(text: str) -> ContentToolCallContent:
    return ContentToolCallContent(type="content", content=TextContentBlock(type="text", text=text))


def _json_string(content: str, key: str) -> str | None:
    """A top-level string field of JSON text that may have been cut short, as far as it reaches."""
    start = content.find(f'"{key}": "')
    if start < 0:
        return None
    begin = start + len(key) + 5
    end, escaped = len(content), False
    for index in range(begin, len(content)):
        char = content[index]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            end = index
            break
    segment = content[begin:end]
    # A cut can split an escape sequence, which is at most six characters long.
    for trim in range(7):
        try:
            return json.loads(f'"{segment[: len(segment) - trim]}"')
        except ValueError:
            continue
    return None


def _shell(resolution: ToolResolution) -> tuple[str, int | None]:
    """A shell call's combined output and exit code, from the tool's JSON result when it has one."""
    content = resolution.content or ""
    try:
        parsed: Any = json.loads(content)
    except ValueError:
        parsed = None
    if isinstance(parsed, dict):
        streams = [parsed.get("stdout"), parsed.get("stderr")]
        code = parsed.get("returncode")
    else:
        # A result cut at the engine's size limit is no longer JSON. Its marker follows the cut on a line of its
        # own, and JSON text has no raw newline.
        body, marker = content.rsplit("\n", 1) if resolution.truncated and "\n" in content else (content, None)
        streams = [_json_string(body, "stdout"), _json_string(body, "stderr")]
        if streams == [None, None]:
            return content, None
        found = re.search(r'"returncode": (-?\d+)', body)
        code = int(found.group(1)) if found else None
        if marker is not None:
            streams.append(f"\n{marker}")
    output = "".join(part for part in streams if isinstance(part, str))
    return output, code if type(code) is int else None


def _exit(terminal_id: str, code: int | None) -> dict[str, Any]:
    """The terminal_exit meta: a negative return code is the signal that ended the command."""
    if code is None or code >= 0:
        return {"terminal_id": terminal_id, "exit_code": code, "signal": None}
    try:
        name: str | None = signal.Signals(-code).name
    except ValueError:
        name = None
    return {"terminal_id": terminal_id, "exit_code": None, "signal": name}


def result_content(
    call: ToolCall | None, resolution: ToolResolution, changes: list[TextChange], terminal: bool
) -> tuple[list[Content], dict[str, Any] | None]:
    """The content and `_meta` of the update that settles a call."""
    error: list[Content] = []
    if resolution.outcome != "completed" and resolution.error is not None:
        error = [_text(f"{resolution.error.message} {resolution.error.remedy}")]
    if changes:
        return diffs(changes), None
    if call is not None and is_builtin(call, "bash"):
        output, code = _shell(resolution)
        if terminal:
            terminal_id = resolution.call_id
            meta = {
                "terminal_output": {"terminal_id": terminal_id, "data": output},
                "terminal_exit": _exit(terminal_id, code),
            }
            return [TerminalToolCallContent(type="terminal", terminal_id=terminal_id), *error], meta
        shown: list[Content] = [_text(f"```console\n{output.rstrip()}\n```")] if output else []
        return [*shown, *error], None
    text: list[Content] = [_text(resolution.content)] if resolution.content else []
    return [*text, *error], None

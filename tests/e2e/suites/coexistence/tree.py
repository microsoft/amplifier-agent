"""Cheap, comparable snapshots of a directory tree living inside the DTU.

The coexistence suite's central claim is a NEGATIVE one: after amplifier-agent has
been exercised hard, amplifier-app-cli's tree at ``~/.amplifier`` is byte-for-byte
the same tree it was before. Proving a negative needs a comparable representation
of "the whole tree", and that representation has to be cheap enough to take twice
around a real workload.

So a snapshot is path + size + mtime for every file and symlink, plus the set of
directories.

Deliberately NOT a content hash of every file. app-cli's cache is a few thousand
files of cloned git repositories, and hashing all of it twice would dominate the
runtime of the suite for no real gain: the failure mode this suite exists to catch
is amplifier-agent CREATING, DELETING, or REWRITING entries in a tree it does not
own. Every one of those changes size or mtime. A write that lands on an existing
git object, preserves its byte count, and restores its mtime is not a thing git
clones do, and it is not a thing a cache-root misconfiguration would produce.

Directories are listed separately from files because a size+mtime listing of files
alone cannot see an empty directory being created, and "amplifier-agent made a
directory in app-cli's tree" is exactly the kind of first symptom worth catching.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass

from framework import dtu


@dataclass(frozen=True)
class TreeState:
    """One snapshot of a tree: files keyed by path, plus the directory set.

    Attributes:
        root: The absolute in-DTU path the snapshot was taken of.
        files: Relative path -> ``(size_bytes, mtime)``. ``mtime`` is kept as the
            raw string ``find`` printed so no float rounding can invent a diff.
        dirs: Relative paths of every directory below ``root`` (``.`` included).
    """

    root: str
    files: dict[str, tuple[int, str]]
    dirs: frozenset[str]


# Separates the file listing from the directory listing in one exec. Chosen so it
# cannot collide with a path: no filename contains a newline-delimited banner.
_SEPARATOR = "===DIRS==="


def snapshot(dtu_id: str, root: str, *, exclude: tuple[str, ...] = ()) -> TreeState:
    """Record the state of ``root`` inside the DTU.

    Args:
        dtu_id: The warm DTU instance id.
        root: Absolute in-DTU path to record.
        exclude: Relative path prefixes to drop from the snapshot. A prefix matches
            an entry that equals it or that sits below it.

    Returns:
        A TreeState. Taking two of these around a workload and comparing them is
        the whole point; see ``diff``.

    Raises:
        AssertionError: If the listing command fails inside the DTU.
    """
    quoted = shlex.quote(root)
    script = (
        f"find {quoted} \\( -type f -o -type l \\) -printf '%p|%s|%T@\\n' | sort; "
        f"echo {_SEPARATOR}; "
        f"find {quoted} -type d -printf '%p\\n' | sort"
    )
    result = dtu.exec_json(dtu_id, ["bash", "-lc", script])
    assert result.get("exit_code") == 0, (
        f"could not list {root} inside the DTU (exit {result.get('exit_code')})\nstderr:\n{result.get('stderr', '')}"
    )

    raw = result.get("stdout", "")
    file_block, _, dir_block = raw.partition(f"{_SEPARATOR}\n")

    files: dict[str, tuple[int, str]] = {}
    for line in file_block.splitlines():
        if not line.strip():
            continue
        path, _, rest = line.partition("|")
        size, _, mtime = rest.partition("|")
        rel = _relative(path, root)
        if _excluded(rel, exclude):
            continue
        files[rel] = (int(size), mtime)

    dirs = {
        rel
        for rel in (_relative(line, root) for line in dir_block.splitlines() if line.strip())
        if not _excluded(rel, exclude)
    }

    return TreeState(root=root, files=files, dirs=frozenset(dirs))


def diff(before: TreeState, after: TreeState, *, limit: int = 20) -> str:
    """Return a human-readable description of what changed, or "" when identical.

    The empty string is the pass condition, so a caller reads as
    ``assert not diff(before, after), diff(before, after)``. Entries are listed by
    path with what actually changed about them, capped at ``limit`` per category,
    because a misconfigured cache root produces thousands of added paths and a raw
    dump of both trees would bury the one line that names the cause.
    """
    added_files = sorted(set(after.files) - set(before.files))
    removed_files = sorted(set(before.files) - set(after.files))
    changed_files = sorted(
        path for path in set(before.files) & set(after.files) if before.files[path] != after.files[path]
    )
    added_dirs = sorted(after.dirs - before.dirs)
    removed_dirs = sorted(before.dirs - after.dirs)

    if not (added_files or removed_files or changed_files or added_dirs or removed_dirs):
        return ""

    lines = [f"{before.root} changed while amplifier-agent ran; it must not."]

    def section(title: str, entries: list[str], describe=None) -> None:
        if not entries:
            return
        lines.append(f"  {title} ({len(entries)}):")
        for path in entries[:limit]:
            detail = f"  {describe(path)}" if describe else ""
            lines.append(f"    {path}{detail}")
        if len(entries) > limit:
            lines.append(f"    ... and {len(entries) - limit} more")

    def describe_change(path: str) -> str:
        old_size, old_mtime = before.files[path]
        new_size, new_mtime = after.files[path]
        parts = []
        if old_size != new_size:
            parts.append(f"size {old_size} -> {new_size}")
        if old_mtime != new_mtime:
            parts.append(f"mtime {old_mtime} -> {new_mtime}")
        return "(" + ", ".join(parts) + ")"

    section("added files", added_files)
    section("removed files", removed_files)
    section("changed files", changed_files, describe_change)
    section("added directories", added_dirs)
    section("removed directories", removed_dirs)
    return "\n".join(lines)


def _relative(path: str, root: str) -> str:
    """Strip ``root`` from an absolute path, returning "." for the root itself."""
    if path == root:
        return "."
    prefix = root if root.endswith("/") else root + "/"
    return path[len(prefix) :] if path.startswith(prefix) else path


def _excluded(rel: str, exclude: tuple[str, ...]) -> bool:
    """True when ``rel`` equals an excluded prefix or sits below one."""
    return any(rel == prefix or rel.startswith(prefix + "/") for prefix in exclude)

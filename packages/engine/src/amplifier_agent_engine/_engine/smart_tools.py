"""The default amplifier-smart-tools skill: the bundled copy, the refreshed copy, and the refresh between them.

The refreshed copies live under the sessions directory (host-config.v1 section 4), one directory per upstream commit,
with a pointer file naming the current one. A copy is placed whole by a rename and the pointer is replaced by a rename,
so a reader sees a complete copy or falls back to the bundled one. The copy the pointer replaced is kept until the next
replacement, so a running session never loses the files of the copy it started with.

scripts/sync_smart_tools_skill.py loads this file by path to write the bundled copy, so it imports only the standard
library at module level.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
import json
import logging
from pathlib import Path
import re
import shutil
import ssl
import tempfile
import threading
import time
from typing import Any
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

SKILL_NAME = "amplifier-smart-tools"
REPOSITORY = "https://github.com/microsoft/amplifier-smart-tools"
API = "https://api.github.com/repos/microsoft/amplifier-smart-tools"
RAW = "https://raw.githubusercontent.com/microsoft/amplifier-smart-tools"
SKILL_PATH = f"skills/{SKILL_NAME}"
REF = "main"
# The bundled copy's record of where it came from.
SOURCE_FILE = "SOURCE.json"
# The refreshed copies' pointer to the current one.
POINTER_FILE = "current.json"
BUNDLED = Path(__file__).parents[1] / "_smart_tools" / "skill"
REFRESH_INTERVAL = timedelta(hours=24)
# Staging entries older than this were left by a process that exited mid-refresh.
ABANDONED_AFTER = timedelta(hours=1)
MAX_BYTES = 1024 * 1024
MAX_ENTRIES = 100
TIMEOUT_SECONDS = 30
USER_AGENT = "amplifier-agent"
_CHUNK = 64 * 1024
_STAGING = ".staging-"
_COMMIT = re.compile(r"[0-9a-f]{40}")


def cache_directory(sessions_directory: Path) -> Path:
    return sessions_directory / "smart-tools" / "skill"


def complete(directory: Path) -> bool:
    return (directory / SKILL_NAME / "SKILL.md").is_file()


def pointer(cache: Path) -> dict[str, Any] | None:
    """The pointer record in ``cache``, or None when it is missing or unreadable."""
    try:
        record = json.loads((cache / POINTER_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or not isinstance(record.get("commit"), str):
        return None
    return record if _COMMIT.fullmatch(record["commit"]) else None


def current_copy(sessions_directory: Path) -> Path | None:
    """The complete refreshed copy the pointer names, or None."""
    cache = cache_directory(sessions_directory)
    record = pointer(cache)
    if record is None:
        return None
    directory = cache / record["commit"]
    return directory if complete(directory) else None


def skill_directory(sessions_directory: Path) -> Path:
    """The skill source to offer: the refreshed copy when it is complete and valid, else the bundled copy."""
    from amplifier_module_tool_skills.discovery import discover_skills

    copy = current_copy(sessions_directory)
    if copy is not None and SKILL_NAME in discover_skills(copy):
        return copy
    return BUNDLED


def tls_context() -> ssl.SSLContext:
    # A frozen runtime on macOS has no CA store OpenSSL can find, so the engine uses certifi's bundle. The sync script
    # loads this file outside the engine's environment, where certifi may be absent.
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


def get(url: str, accept: str, limit: int, stop: threading.Event) -> bytes:
    """The body at ``url``, refused when it exceeds ``limit`` bytes; stops early, raising, once ``stop`` is set."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    chunks: list[bytes] = []
    size = 0
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS, context=tls_context()) as response:
        while chunk := response.read(_CHUNK):
            if stop.is_set():
                raise InterruptedError("The refresh was cancelled.")
            size += len(chunk)
            if size > limit:
                raise ValueError(f"The response from {url} is larger than {limit} bytes.")
            chunks.append(chunk)
    return b"".join(chunks)


def latest_commit(ref: str, stop: threading.Event) -> str:
    """The commit ``ref`` of the repository names."""
    url = f"{API}/commits/{urllib.parse.quote(ref, safe='')}"
    body = get(url, "application/vnd.github.sha", 128, stop).decode("ascii", errors="replace").strip()
    if not _COMMIT.fullmatch(body):
        raise ValueError(f"{url} did not return a commit SHA.")
    return body


def _name(entry: Any, directory: str) -> str:
    name = entry.get("name") if isinstance(entry, dict) else None
    if not isinstance(name, str) or name in ("", ".", "..") or any(char in name for char in "/\\\0"):
        raise ValueError(f"The listing of {directory} has an entry with an unsafe name: {name!r}.")
    return name


def skill_files(commit: str, stop: threading.Event) -> list[tuple[str, ...]]:
    """The skill's files at ``commit``, as paths relative to the skill directory.

    Only regular files and directories are accepted. A link, a submodule, an unsafe name, or a skill larger than the
    bounds refuses the whole copy.
    """
    files: list[tuple[str, ...]] = []
    entries = 0
    total = 0
    pending: list[tuple[str, ...]] = [()]
    while pending:
        relative = pending.pop()
        directory = "/".join((SKILL_PATH, *relative))
        url = f"{API}/contents/{urllib.parse.quote(directory)}?ref={commit}"
        listing = json.loads(get(url, "application/vnd.github+json", MAX_BYTES, stop))
        if not isinstance(listing, list):
            raise ValueError(f"{directory} at {commit} is not a directory.")
        for entry in listing:
            entries += 1
            if entries > MAX_ENTRIES:
                raise ValueError(f"The skill has more than {MAX_ENTRIES} files and directories.")
            name = _name(entry, directory)
            kind = entry.get("type")
            if kind == "dir":
                pending.append((*relative, name))
                continue
            # A directory listing reports a submodule as a file with no download URL.
            if kind != "file" or not isinstance(entry.get("download_url"), str):
                raise ValueError(f"{directory}/{name} is not a regular file or directory.")
            size = entry.get("size")
            if type(size) is not int or size < 0:
                raise ValueError(f"{directory}/{name} has no size.")
            total += size
            if total > MAX_BYTES:
                raise ValueError(f"The skill is larger than {MAX_BYTES} bytes.")
            files.append((*relative, name))
    return files


def fetch(commit: str, destination: Path, stop: threading.Event) -> None:
    """Write the skill at ``commit`` into ``destination``, under a directory named for the skill."""
    remaining = MAX_BYTES
    for relative in skill_files(commit, stop):
        path = urllib.parse.quote("/".join((SKILL_PATH, *relative)))
        data = get(f"{RAW}/{commit}/{path}", "application/octet-stream", remaining, stop)
        remaining -= len(data)
        target = destination.joinpath(SKILL_NAME, *relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    if not complete(destination):
        raise ValueError(f"The skill at {commit} has no SKILL.md.")


def _fresh(record: dict[str, Any], now: datetime) -> bool:
    try:
        fetched = datetime.fromisoformat(record["fetched_at"])
    except (KeyError, TypeError, ValueError):
        return False
    if fetched.tzinfo is None:
        return False
    return now - REFRESH_INTERVAL < fetched <= now


def _remove_abandoned(cache: Path) -> None:
    cutoff = time.time() - ABANDONED_AFTER.total_seconds()
    for entry in cache.iterdir():
        if not entry.name.startswith(_STAGING):
            continue
        # A concurrent refresh may remove the entry first.
        with contextlib.suppress(OSError):
            if entry.stat().st_mtime < cutoff:
                if entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    entry.unlink()


def _place(commit: str, cache: Path, stop: threading.Event) -> None:
    """Download the copy at ``commit`` and rename it into place; another refresh placing it first is success."""
    with tempfile.TemporaryDirectory(prefix="amplifier-smart-tools-") as scratch:
        # The download writes outside the sessions directory, so a failed or cancelled one leaves nothing there.
        fetch(commit, Path(scratch), stop)
        if stop.is_set():
            raise InterruptedError("The refresh was cancelled.")
        cache.mkdir(parents=True, exist_ok=True)
        _remove_abandoned(cache)
        # The rename that places the copy needs it on the same file system as the cache.
        staging = Path(tempfile.mkdtemp(prefix=_STAGING, dir=cache))
        try:
            shutil.copytree(scratch, staging, dirs_exist_ok=True)
            try:
                staging.rename(cache / commit)
            except OSError:
                if not complete(cache / commit):
                    raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)


def _point(cache: Path, record: dict[str, Any]) -> None:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", prefix=_STAGING, dir=cache, delete=False) as file:
        json.dump(record, file, indent=2)
        file.write("\n")
    Path(file.name).replace(cache / POINTER_FILE)


def _prune(cache: Path, keep: set[str]) -> None:
    for entry in cache.iterdir():
        if _COMMIT.fullmatch(entry.name) and entry.name not in keep:
            shutil.rmtree(entry, ignore_errors=True)


def refresh_now(sessions_directory: Path, stop: threading.Event, now: datetime) -> str:
    """Bring the refreshed copy up to date with the repository's main branch.

    Returns "fresh" when the current copy is younger than the interval and nothing was requested, "confirmed" when it
    is still the latest commit and only its fetch time changed, and "replaced" when a new copy became current.
    """
    cache = cache_directory(sessions_directory)
    record = pointer(cache)
    if record is not None and current_copy(sessions_directory) is not None and _fresh(record, now):
        return "fresh"
    commit = latest_commit(REF, stop)
    previous = None if record is None else record["commit"]
    if not complete(cache / commit):
        _place(commit, cache, stop)
    if stop.is_set():
        raise InterruptedError("The refresh was cancelled.")
    _point(cache, {"repository": REPOSITORY, "commit": commit, "fetched_at": now.isoformat()})
    _prune(cache, {commit, *([previous] if previous else [])})
    return "confirmed" if commit == previous else "replaced"


async def refresh(sessions_directory: Path) -> None:
    """Refresh the cached skill in the background; failures are logged and the last good copy stays in place."""
    loop = asyncio.get_running_loop()
    settled: asyncio.Future[None] = loop.create_future()
    stop = threading.Event()

    def report(error: BaseException | None) -> None:
        if settled.done():
            return
        if error is None:
            settled.set_result(None)
        else:
            settled.set_exception(error)

    def work() -> None:
        error: BaseException | None = None
        try:
            refresh_now(sessions_directory, stop, datetime.now(UTC))
        except BaseException as exc:
            error = exc
        # RuntimeError: the loop closed while the refresh ran, so nobody is waiting for the result.
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(report, error)

    # A daemon thread rather than asyncio.to_thread: the default executor is joined at shutdown, so a slow download
    # would hold up the caller's process exit after the agent closed.
    threading.Thread(target=work, name="amplifier-smart-tools-refresh", daemon=True).start()
    try:
        await settled
    except asyncio.CancelledError:
        stop.set()
        raise
    except OSError as exc:
        # Offline, rate limited (403 or 429), or timed out: the next agent tries again.
        logger.debug("The smart tools skill was not refreshed: %s", exc)
    except Exception:
        logger.warning("The smart tools skill was not refreshed; the last good copy stays in use.", exc_info=True)

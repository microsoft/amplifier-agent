"""The refreshed copy of the amplifier-smart-tools skill: fetching, refresh, and which copy a session uses.

A fake GitHub replaces ``smart_tools.get``, so nothing here reaches the network.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from email.message import Message
import json
import logging
import os
from pathlib import Path
import threading
from typing import Any
import urllib.error
import urllib.parse

from amplifier_agent_engine._engine import assembly, smart_tools
from amplifier_agent_engine._records import AgentOptions, SessionOptions, TextPart, TurnInput
import pytest

from tests.support.engine import provision

REAL_GET = smart_tools.get
FIRST = "a" * 40
SECOND = "b" * 40
THIRD = "c" * 40
NOW = datetime(2026, 11, 16, 12, tzinfo=UTC)
CONTENTS = f"{smart_tools.API}/contents/"


def skill_text(description: str) -> bytes:
    return f"---\nname: {smart_tools.SKILL_NAME}\ndescription: {description}\n---\nBody of {description}\n".encode()


class FakeGitHub:
    """The three endpoints the refresh uses, serving one file tree per commit."""

    def __init__(self, head: str, description: str) -> None:
        self.head = head
        self.trees: dict[str, dict[str, bytes]] = {}
        self.listings: dict[str, Any] = {}
        self.requests: list[str] = []
        self.failure: BaseException | None = None
        self.publish(head, {"SKILL.md": skill_text(description)})

    def publish(self, commit: str, files: dict[str, bytes]) -> None:
        self.trees[commit] = {f"{smart_tools.SKILL_PATH}/{path}": data for path, data in files.items()}
        self.head = commit

    def listing(self, commit: str, directory: str) -> Any:
        if directory in self.listings:
            return self.listings[directory]
        entries: dict[str, dict[str, Any]] = {}
        for path, data in self.trees[commit].items():
            if not path.startswith(directory + "/"):
                continue
            name, _, rest = path.removeprefix(directory + "/").partition("/")
            if rest:
                entries[name] = {"name": name, "type": "dir", "size": 0, "download_url": None}
            else:
                entries[name] = {"name": name, "type": "file", "size": len(data), "download_url": f"{RAW}{path}"}
        return list(entries.values())

    def get(self, url: str, accept: str, limit: int, stop: threading.Event) -> bytes:
        self.requests.append(url)
        if self.failure is not None:
            raise self.failure
        if url == f"{smart_tools.API}/commits/main":
            assert accept == "application/vnd.github.sha"
            return self.head.encode()
        if url.startswith(CONTENTS):
            directory, _, commit = url.removeprefix(CONTENTS).partition("?ref=")
            return json.dumps(self.listing(commit, urllib.parse.unquote(directory))).encode()
        commit, _, path = url.removeprefix(f"{smart_tools.RAW}/").partition("/")
        data = self.trees[commit][urllib.parse.unquote(path)]
        assert len(data) <= limit
        return data

    def downloads(self) -> list[str]:
        return [url for url in self.requests if url.startswith(smart_tools.RAW)]


RAW = "https://raw.githubusercontent.com/microsoft/amplifier-smart-tools/main/"


@pytest.fixture(autouse=True)
def host(monkeypatch, tmp_path):
    for key in list(os.environ):
        if key.startswith("AMPLIFIER_AGENT_"):
            monkeypatch.delenv(key)
    path = tmp_path / "host.json"
    path.write_text("{}")
    monkeypatch.setenv("AMPLIFIER_AGENT_CONFIG", str(path))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


@pytest.fixture
def github(monkeypatch):
    fake = FakeGitHub(FIRST, "Upstream")
    monkeypatch.setattr(smart_tools, "get", fake.get)
    return fake


@pytest.fixture
def sessions(tmp_path):
    return tmp_path / "sessions"


def cache(sessions: Path) -> Path:
    return smart_tools.cache_directory(sessions)


def current_text(sessions: Path) -> str:
    copy = smart_tools.current_copy(sessions)
    assert copy is not None
    return (copy / smart_tools.SKILL_NAME / "SKILL.md").read_text()


def entries(sessions: Path) -> list[str]:
    return sorted(entry.name for entry in cache(sessions).iterdir())


def refresh(sessions: Path, now: datetime = NOW) -> str:
    return smart_tools.refresh_now(sessions, threading.Event(), now)


def test_a_missing_copy_is_fetched_with_three_small_requests(github, sessions):
    assert refresh(sessions) == "replaced"
    assert github.requests == [
        f"{smart_tools.API}/commits/main",
        f"{CONTENTS}skills/amplifier-smart-tools?ref={FIRST}",
        f"{smart_tools.RAW}/{FIRST}/skills/amplifier-smart-tools/SKILL.md",
    ]
    assert smart_tools.pointer(cache(sessions)) == {
        "repository": smart_tools.REPOSITORY,
        "commit": FIRST,
        "fetched_at": NOW.isoformat(),
    }
    assert "Upstream" in current_text(sessions)
    assert entries(sessions) == [FIRST, smart_tools.POINTER_FILE]


def test_nested_directories_are_fetched(github, sessions):
    github.publish(SECOND, {"SKILL.md": skill_text("Nested"), "references/deep/catalog.md": b"catalog"})
    refresh(sessions)
    copy = smart_tools.current_copy(sessions)
    assert copy is not None
    assert (copy / smart_tools.SKILL_NAME / "references" / "deep" / "catalog.md").read_bytes() == b"catalog"


def test_a_copy_younger_than_the_interval_makes_no_request(github, sessions):
    refresh(sessions, NOW - timedelta(hours=23))
    github.requests.clear()
    assert refresh(sessions) == "fresh"
    assert github.requests == []


@pytest.mark.parametrize("age", [timedelta(hours=25), timedelta(hours=-1)])
def test_an_older_or_future_dated_copy_at_the_latest_commit_only_has_its_time_updated(github, sessions, age):
    refresh(sessions, NOW - age)
    github.requests.clear()
    assert refresh(sessions) == "confirmed"
    assert github.requests == [f"{smart_tools.API}/commits/main"]
    record = smart_tools.pointer(cache(sessions))
    assert record is not None
    assert record["fetched_at"] == NOW.isoformat()


def test_a_new_commit_becomes_current_and_the_one_before_it_is_kept_until_the_next(github, sessions):
    refresh(sessions, NOW - timedelta(days=3))
    github.publish(SECOND, {"SKILL.md": skill_text("Second")})
    assert refresh(sessions, NOW - timedelta(days=2)) == "replaced"
    assert "Second" in current_text(sessions)
    assert entries(sessions) == [FIRST, SECOND, smart_tools.POINTER_FILE]
    github.publish(THIRD, {"SKILL.md": skill_text("Third")})
    assert refresh(sessions) == "replaced"
    assert "Third" in current_text(sessions)
    assert entries(sessions) == [SECOND, THIRD, smart_tools.POINTER_FILE]


def test_a_missing_current_copy_is_fetched_even_when_the_pointer_is_recent(github, sessions):
    refresh(sessions)
    (cache(sessions) / FIRST / smart_tools.SKILL_NAME / "SKILL.md").unlink()
    (cache(sessions) / FIRST / smart_tools.SKILL_NAME).rmdir()
    (cache(sessions) / FIRST).rmdir()
    assert refresh(sessions) == "confirmed"
    assert "Upstream" in current_text(sessions)


@pytest.mark.parametrize(
    "failure",
    [
        urllib.error.HTTPError(f"{smart_tools.API}/commits/main", 403, "rate limit exceeded", Message(), None),
        urllib.error.HTTPError(f"{smart_tools.API}/commits/main", 429, "too many requests", Message(), None),
        ConnectionResetError("the connection broke off"),
    ],
)
def test_a_failed_request_keeps_the_last_good_copy(github, sessions, failure):
    refresh(sessions, NOW - timedelta(days=3))
    github.publish(SECOND, {"SKILL.md": skill_text("Second")})
    github.failure = failure
    before = entries(sessions)
    with pytest.raises(type(failure)):
        refresh(sessions)
    assert "Upstream" in current_text(sessions)
    assert entries(sessions) == before


def test_a_failed_first_fetch_leaves_the_sessions_directory_untouched(github, sessions):
    github.failure = ConnectionResetError("the connection broke off")
    with pytest.raises(ConnectionResetError):
        refresh(sessions)
    assert not sessions.exists()


def entry(name: Any, kind: str = "file", size: Any = 1, download: Any = "https://example.invalid/x") -> dict:
    return {"name": name, "type": kind, "size": size, "download_url": download}


@pytest.mark.parametrize(
    ("listing", "message"),
    [
        ([entry("link", kind="symlink")], "not a regular file"),
        ([entry("module", download=None)], "not a regular file"),
        ([entry("module", kind="submodule")], "not a regular file"),
        ([entry("..")], "unsafe name"),
        ([entry("a/b")], "unsafe name"),
        ([entry("a\\b")], "unsafe name"),
        ([entry("")], "unsafe name"),
        ([entry(None)], "unsafe name"),
        ([entry("big", size=smart_tools.MAX_BYTES + 1)], "larger than"),
        ([entry(f"file-{index}") for index in range(smart_tools.MAX_ENTRIES + 1)], "more than"),
        ([entry("unsized", size="1")], "no size"),
    ],
)
def test_a_skill_with_an_unsafe_or_oversized_entry_is_refused_before_any_download(github, sessions, listing, message):
    github.listings[smart_tools.SKILL_PATH] = [entry("SKILL.md", size=10), *listing]
    with pytest.raises(ValueError, match=message):
        refresh(sessions)
    assert github.downloads() == []
    assert not sessions.exists()


def test_a_listing_that_is_not_a_directory_is_refused(github, sessions):
    github.listings[smart_tools.SKILL_PATH] = {"type": "file"}
    with pytest.raises(ValueError, match="not a directory"):
        refresh(sessions)


@pytest.mark.parametrize("body", [b"", b"main", b"A" * 40, b"<html>"])
def test_a_commit_that_is_not_a_sha_is_refused(github, sessions, body):
    github.head = body.decode()
    with pytest.raises(ValueError, match="commit SHA"):
        refresh(sessions)


def test_a_skill_without_skill_md_is_refused(github, sessions):
    github.publish(SECOND, {"README.md": b"readme"})
    with pytest.raises(ValueError, match=r"SKILL\.md"):
        refresh(sessions)
    assert not sessions.exists()


def test_concurrent_refreshes_leave_one_complete_copy(monkeypatch, github, sessions):
    barrier = threading.Barrier(4)
    serve = github.get

    def get(url: str, accept: str, limit: int, stop: threading.Event) -> bytes:
        if url.startswith(smart_tools.RAW):
            barrier.wait(timeout=10)
        return serve(url, accept, limit, stop)

    monkeypatch.setattr(smart_tools, "get", get)
    errors: list[BaseException] = []

    def run() -> None:
        try:
            refresh(sessions)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert errors == [], errors
    assert "Upstream" in current_text(sessions)
    assert entries(sessions) == [FIRST, smart_tools.POINTER_FILE]


async def test_refresh_logs_a_failure_instead_of_raising(github, sessions, caplog):
    github.failure = urllib.error.HTTPError(f"{smart_tools.API}/commits/main", 403, "rate limit", Message(), None)
    with caplog.at_level(logging.DEBUG, logger=smart_tools.__name__):
        await smart_tools.refresh(sessions)
    assert "rate limit" in caplog.text
    github.failure = None
    github.head = "not a commit"
    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger=smart_tools.__name__):
        await smart_tools.refresh(sessions)
    assert "commit SHA" in caplog.text
    assert any(record.levelno == logging.WARNING for record in caplog.records)


class Response:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def read(self, size: int) -> bytes:
        chunk, self.body = self.body[:size], self.body[size:]
        return chunk

    def __enter__(self) -> "Response":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def test_requests_name_the_agent_verify_tls_and_are_bounded(monkeypatch):
    seen: list[Any] = []

    def urlopen(request, timeout, context):
        seen.append((request, timeout, context))
        return Response(b"x" * 10)

    monkeypatch.setattr(smart_tools.urllib.request, "urlopen", urlopen)
    assert REAL_GET("https://example.invalid/a", "text/plain", 10, threading.Event()) == b"x" * 10
    with pytest.raises(ValueError, match="larger than 9 bytes"):
        REAL_GET("https://example.invalid/a", "text/plain", 9, threading.Event())
    request, timeout, context = seen[0]
    assert request.get_header("User-agent") == smart_tools.USER_AGENT
    assert request.get_header("Accept") == "text/plain"
    assert timeout == smart_tools.TIMEOUT_SECONDS
    assert context.verify_mode == smart_tools.ssl.CERT_REQUIRED
    stopped = threading.Event()
    stopped.set()
    with pytest.raises(InterruptedError):
        REAL_GET("https://example.invalid/a", "text/plain", 10, stopped)


def test_a_valid_refreshed_copy_is_offered(github, sessions):
    refresh(sessions)
    assert smart_tools.skill_directory(sessions) == cache(sessions) / FIRST


@pytest.mark.parametrize("damage", ["missing", "no pointer", "bad pointer", "unknown commit", "no skill", "unparsable"])
def test_a_missing_or_corrupt_refreshed_copy_falls_back_to_the_bundled_copy(github, sessions, damage):
    if damage != "missing":
        refresh(sessions)
        pointer = cache(sessions) / smart_tools.POINTER_FILE
        skill = cache(sessions) / FIRST / smart_tools.SKILL_NAME / "SKILL.md"
        if damage == "no pointer":
            pointer.unlink()
        elif damage == "bad pointer":
            pointer.write_text("{")
        elif damage == "unknown commit":
            pointer.write_text(json.dumps({"commit": SECOND}))
        elif damage == "no skill":
            skill.unlink()
        else:
            skill.write_text("no front matter")
    assert smart_tools.skill_directory(sessions) == smart_tools.BUNDLED


def test_the_bundled_copy_is_complete():
    assert smart_tools.complete(smart_tools.BUNDLED)
    source = json.loads((smart_tools.BUNDLED / smart_tools.SOURCE_FILE).read_text())
    assert source["repository"] == smart_tools.REPOSITORY
    assert len(source["commit"]) == 40


def load_skill_description(request) -> str:
    return next(tool for tool in request["tools"] if tool["name"] == "load_skill")["description"]


async def run_once(agent) -> None:
    session = await agent.create_session(SessionOptions(persistence="ephemeral"))
    turn = await session.start_turn(TurnInput([TextPart("Reply")]))
    events = [event async for event in turn.events()]
    assert events[-1].payload.state == "success"


def options(tmp_path: Path, sessions: Path, **fields) -> AgentOptions:
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    return AgentOptions(
        provider="anthropic",
        model="claude-sonnet-5",
        approvals="allow",
        tools=[],
        working_directory=str(work),
        sessions_directory=str(sessions),
        **fields,
    )


async def test_a_session_offers_the_refreshed_copy_when_it_is_valid(monkeypatch, github, tmp_path, sessions):
    github.publish(SECOND, {"SKILL.md": skill_text("The refreshed copy.")})
    refresh(sessions)
    probe = provision(monkeypatch, [{"text": "Done"}])
    agent = await assembly.create_engine(options(tmp_path, sessions))
    try:
        await run_once(agent)
    finally:
        await agent.close()
    assert "The refreshed copy." in load_skill_description(probe.requests[0])


async def test_construction_never_waits_for_the_refresh_and_close_cancels_it(monkeypatch, tmp_path, sessions):
    started = threading.Event()
    stopped = threading.Event()

    def get(url: str, accept: str, limit: int, stop: threading.Event) -> bytes:
        started.set()
        stop.wait(timeout=30)
        stopped.set()
        raise InterruptedError("cancelled")

    monkeypatch.setattr(smart_tools, "get", get)
    probe = provision(monkeypatch, [{"text": "Done"}])
    agent = await asyncio.wait_for(assembly.create_engine(options(tmp_path, sessions)), timeout=10)
    try:
        assert await asyncio.to_thread(started.wait, 10)
        await run_once(agent)
        assert smart_tools.SKILL_NAME in load_skill_description(probe.requests[0])
    finally:
        await asyncio.wait_for(agent.close(), timeout=10)
    assert await asyncio.to_thread(stopped.wait, 10)
    assert not cache(sessions).exists()


async def test_false_starts_no_refresh(monkeypatch, github, tmp_path, sessions):
    provision(monkeypatch, [])
    agent = await assembly.create_engine(options(tmp_path, sessions, smart_tools=False))
    await asyncio.sleep(0.1)
    await agent.close()
    assert github.requests == []
    assert agent.background == set()

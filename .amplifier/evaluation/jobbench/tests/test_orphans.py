"""Unit tests for jobbench.orphans: a DESTRUCTIVE prefix match, fully mocked.

`sweep_orphans()` destroys every DTU instance whose id starts with `jb-`. The
blast radius is real containers, so the properties worth pinning are the ones
that bound it:

  - the prefix is the ONLY selector -- a non-`jb-` instance is never touched
  - malformed CLI output (missing id, non-string id, non-dict entry) is
    skipped rather than raising mid-sweep, which would leave the rest of the
    leaked containers alive
  - `list_instances()` degrades to [] on a failing or non-JSON CLI, because a
    sweep that cannot enumerate must skip reaping, not abort the run

Both the `amplifier-digital-twin` subprocess and `DTU.destroy` are mocked in
every test here -- nothing in this file invokes the real CLI or touches a real
container. No benchmark content appears in this file.
"""

from __future__ import annotations

import asyncio

import pytest

from jobbench import orphans
from jobbench.orphans import JB_PREFIX, list_instances, sweep_orphans


class FakeProc:
    """Stand-in for the process returned by asyncio.create_subprocess_exec."""

    def __init__(self, returncode: int = 0, stdout: bytes = b"[]", stderr: bytes = b""):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr


def _fake_cli(
    monkeypatch,
    *,
    returncode: int = 0,
    stdout: bytes = b"[]",
    stderr: bytes = b"",
) -> list[tuple]:
    """Replace the CLI subprocess; returns the list argv calls are recorded to."""
    calls: list[tuple] = []

    async def _exec(*args, **kwargs):
        calls.append(args)
        return FakeProc(returncode=returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _exec)
    return calls


def _stub_instances(monkeypatch, instances: list[dict]) -> None:
    """Bypass the CLI entirely and hand `sweep_orphans` a fixed instance list."""

    async def _list() -> list[dict]:
        return instances

    monkeypatch.setattr(orphans, "list_instances", _list)


def _capture_destroys(monkeypatch) -> list[str]:
    """Replace DTU.destroy; returns the list destroyed ids are recorded to."""
    destroyed: list[str] = []

    async def _destroy(self, *, timeout_s: float = 120.0) -> None:
        destroyed.append(self.id)

    monkeypatch.setattr(orphans.DTU, "destroy", _destroy)
    return destroyed


# ---------------------------------------------------------------------------
# list_instances: best-effort enumeration
# ---------------------------------------------------------------------------


def test_list_instances_parses_a_json_array(monkeypatch):
    calls = _fake_cli(monkeypatch, stdout=b'[{"id": "jb-one"}, {"id": "other"}]')

    result = asyncio.run(list_instances())

    assert result == [{"id": "jb-one"}, {"id": "other"}]
    assert calls[0] == (orphans.CLI, "list")


def test_list_instances_returns_empty_when_cli_exits_nonzero(monkeypatch):
    """A failing CLI must not abort the run -- only skip reaping this time."""
    _fake_cli(monkeypatch, returncode=1, stdout=b"", stderr=b"daemon unreachable")

    assert asyncio.run(list_instances()) == []


def test_list_instances_returns_empty_on_non_json(monkeypatch):
    _fake_cli(monkeypatch, stdout=b"Error: something went wrong\n")

    assert asyncio.run(list_instances()) == []


def test_list_instances_returns_empty_on_empty_stdout(monkeypatch):
    _fake_cli(monkeypatch, stdout=b"")

    assert asyncio.run(list_instances()) == []


def test_list_instances_returns_empty_for_non_array_json(monkeypatch):
    """A JSON object (e.g. an error payload) is valid JSON but not a list."""
    _fake_cli(monkeypatch, stdout=b'{"error": "not a list"}')

    assert asyncio.run(list_instances()) == []


def test_list_instances_tolerates_undecodable_bytes(monkeypatch):
    """Decoding uses errors='replace', so a mangled byte stream degrades to []
    via the JSON check rather than raising UnicodeDecodeError.
    """
    _fake_cli(monkeypatch, stdout=b"\xff\xfe not json")

    assert asyncio.run(list_instances()) == []


# ---------------------------------------------------------------------------
# sweep_orphans: the prefix is the only selector
# ---------------------------------------------------------------------------


def test_only_jb_prefixed_instances_are_destroyed(monkeypatch):
    """The load-bearing safety property. Everything not named `jb-...` on this
    host -- another team's container, a hand-launched debug DTU -- belongs to
    somebody else, and the sweep must leave it running.
    """
    _stub_instances(
        monkeypatch,
        [
            {"id": "jb-leaked-one"},
            {"id": "dtu-someone-elses"},
            {"id": "jb-leaked-two"},
            {"id": "prod-database"},
            {"id": "not-jb-prefixed"},
        ],
    )
    destroyed = _capture_destroys(monkeypatch)

    returned = asyncio.run(sweep_orphans())

    assert returned == ["jb-leaked-one", "jb-leaked-two"]
    assert destroyed == ["jb-leaked-one", "jb-leaked-two"]


def test_prefix_match_is_anchored_at_the_start(monkeypatch):
    """`jb-` appearing anywhere else in the id is not a match."""
    _stub_instances(
        monkeypatch,
        [
            {"id": "my-jb-container"},
            {"id": "xjb-thing"},
            {"id": "JB-UPPERCASE"},
            {"id": "jb-real"},
        ],
    )
    destroyed = _capture_destroys(monkeypatch)

    assert asyncio.run(sweep_orphans()) == ["jb-real"]
    assert destroyed == ["jb-real"]


def test_empty_instance_list_destroys_nothing(monkeypatch):
    """The normal case: per-trial cleanup worked and there is nothing to reap."""
    _stub_instances(monkeypatch, [])
    destroyed = _capture_destroys(monkeypatch)

    assert asyncio.run(sweep_orphans()) == []
    assert destroyed == []


# ---------------------------------------------------------------------------
# sweep_orphans: malformed entries are skipped, never fatal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "malformed",
    [
        {},  # no id at all
        {"id": None},
        {"id": ""},
        {"id": 12345},  # non-string
        {"id": ["jb-list-not-a-string"]},
        {"name": "jb-wrong-key"},
    ],
    ids=["no-id", "null-id", "empty-id", "int-id", "list-id", "wrong-key"],
)
def test_malformed_entries_are_skipped_without_raising(monkeypatch, malformed):
    """A malformed entry must not abort the sweep partway through -- the
    remaining leaked containers still need reaping.
    """
    _stub_instances(monkeypatch, [malformed, {"id": "jb-good"}])
    destroyed = _capture_destroys(monkeypatch)

    assert asyncio.run(sweep_orphans()) == ["jb-good"]
    assert destroyed == ["jb-good"]


def test_sweep_degrades_to_no_op_when_listing_fails(monkeypatch):
    """End to end through the real `list_instances`: a failing CLI yields an
    empty sweep, not an exception that would take down the run around it.
    """
    _fake_cli(monkeypatch, returncode=1, stderr=b"daemon unreachable")
    destroyed = _capture_destroys(monkeypatch)

    assert asyncio.run(sweep_orphans()) == []
    assert destroyed == []


def test_sweep_uses_the_documented_prefix_constant(monkeypatch):
    """Pinned against JB_PREFIX itself so the destructive selector and the
    trial naming scheme (jobbench.trial._dtu_name) cannot drift apart silently.
    """
    _stub_instances(monkeypatch, [{"id": f"{JB_PREFIX}synthetic-trial"}])
    destroyed = _capture_destroys(monkeypatch)

    assert asyncio.run(sweep_orphans()) == [f"{JB_PREFIX}synthetic-trial"]
    assert destroyed == [f"{JB_PREFIX}synthetic-trial"]

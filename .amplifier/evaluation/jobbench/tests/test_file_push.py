"""Unit tests for `file-push` command construction and fail-loud guards.

NOTE: current DTU CLI versions auto-detect directory sources and push them
recursively regardless of `--recursive` (DTU PR #18). We still pass the
flag for directory sources as compatibility with older CLI versions that
predate auto-detect, and OMIT it for plain files (with `--recursive` the
CLI treats the destination as a parent directory instead of an exact file
path). These tests pin that argv contract for `harness.dtu.DTU.file_push`.

Directory pushes also carry a fail-loud guard: if the CLI reports success
but the directory did not land inside the DTU, the push raises instead of
proceeding silently (silently-empty mounts corrupt grading).

Adapted from amplifier-bundle-evaluation's harness test suite. The
grader._push_mounts tests were dropped -- that module isn't vendored here.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from jobbench import dtu as dtu_module
from jobbench.dtu import CLI, DTU, CommandResult, DTUError


class FakeRun:
    """Stand-in for `dtu._run` that records argv and returns a fixed result."""

    def __init__(self, returncode: int = 0, stderr: str = ""):
        self.calls: list[list[str]] = []
        self.returncode = returncode
        self.stderr = stderr

    async def __call__(self, args, *, timeout=None, env=None):
        self.calls.append(list(args))
        return (self.returncode, "", self.stderr)


class FakeExec:
    """Stand-in for `DTU.exec_cmd` (bound as an instance attribute)."""

    def __init__(self, returncode: int = 0):
        self.calls: list[list[str]] = []
        self.returncode = returncode

    async def __call__(self, command, *, timeout_s=None, stream_to_logfile=None):
        self.calls.append(list(command))
        return CommandResult(returncode=self.returncode, stdout="", stderr="", elapsed_s=0.0)


def _dtu() -> DTU:
    return DTU(id="dtu-test", profile_path="profile.yaml")


# ---------------------------------------------------------------------------
# DTU.file_push
# ---------------------------------------------------------------------------


def test_file_push_file_omits_recursive(tmp_path: Path, monkeypatch):
    src = tmp_path / "a.txt"
    src.write_text("hello", encoding="utf-8")
    run = FakeRun()
    monkeypatch.setattr(dtu_module, "_run", run)

    asyncio.run(_dtu().file_push(src, "/workspace/a.txt"))

    assert run.calls == [[CLI, "file-push", "dtu-test", str(src), "/workspace/a.txt"]]


def test_file_push_dir_adds_recursive_and_verifies(tmp_path: Path, monkeypatch):
    src = tmp_path / "data"
    src.mkdir()
    (src / "x.txt").write_text("x", encoding="utf-8")
    run = FakeRun()
    monkeypatch.setattr(dtu_module, "_run", run)
    dtu = _dtu()
    fake_exec = FakeExec(returncode=0)
    dtu.exec_cmd = fake_exec  # type: ignore[method-assign]

    asyncio.run(dtu.file_push(src, "/workspace/"))

    assert run.calls == [[CLI, "file-push", "--recursive", "dtu-test", str(src), "/workspace/"]]
    # Post-push verification checks the landed directory (name preserved).
    assert len(fake_exec.calls) == 1
    shell, flag, script = fake_exec.calls[0]
    assert (shell, flag) == ("sh", "-c")
    assert "test -d /workspace/data" in script
    assert "ls -A" in script  # non-empty source requires non-empty destination


def test_file_push_empty_dir_skips_content_check(tmp_path: Path, monkeypatch):
    src = tmp_path / "empty"
    src.mkdir()
    monkeypatch.setattr(dtu_module, "_run", FakeRun())
    dtu = _dtu()
    fake_exec = FakeExec(returncode=0)
    dtu.exec_cmd = fake_exec  # type: ignore[method-assign]

    asyncio.run(dtu.file_push(src, "/workspace/"))

    script = fake_exec.calls[0][2]
    assert "test -d /workspace/empty" in script
    assert "ls -A" not in script


def test_file_push_dir_undelivered_raises(tmp_path: Path, monkeypatch):
    src = tmp_path / "data"
    src.mkdir()
    (src / "x.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(dtu_module, "_run", FakeRun())
    dtu = _dtu()
    dtu.exec_cmd = FakeExec(returncode=1)  # type: ignore[method-assign]

    with pytest.raises(DTUError, match="missing or empty"):
        asyncio.run(dtu.file_push(src, "/workspace/"))


def test_file_push_cli_error_raises(tmp_path: Path, monkeypatch):
    src = tmp_path / "data"
    src.mkdir()
    monkeypatch.setattr(dtu_module, "_run", FakeRun(returncode=2, stderr="boom"))
    dtu = _dtu()
    fake_exec = FakeExec()
    dtu.exec_cmd = fake_exec  # type: ignore[method-assign]

    with pytest.raises(DTUError, match="file-push failed"):
        asyncio.run(dtu.file_push(src, "/workspace/"))
    assert fake_exec.calls == []  # no verification after a failed push


def test_file_push_missing_source_raises(tmp_path: Path, monkeypatch):
    run = FakeRun()
    monkeypatch.setattr(dtu_module, "_run", run)

    with pytest.raises(DTUError, match="source missing"):
        asyncio.run(_dtu().file_push(tmp_path / "nope", "/workspace/"))
    assert run.calls == []

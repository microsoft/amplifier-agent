"""Unit tests for jobbench.trial: the status decision tree and deliverable flattening.

trial.py's docstring promises trial.json "honestly distinguishes a crash, a
timeout, and a legitimate zero-deliverable run from each other and from
success". That promise is the whole basis for reading a results table: a
crashed trial and a trial that legitimately produced nothing score the same
zero, and only `status` tells them apart.

Every DTU interaction is faked here -- no container is launched, no CLI is
invoked, no agent runs. Deliverable files are synthetic scratch bytes; no real
JobBench task text, rubric text, or agent output appears in this file.

See `test_status_cli_enforced_overrun_is_classified_crashed` for a discrepancy
between this behaviour and the documented intent; it is pinned as-is, not
fixed here.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from jobbench import trial as trial_mod
from jobbench.dataset import Task
from jobbench.dtu import CommandResult, DTUError
from jobbench.metrics import NOT_AVAILABLE
from jobbench.trial import _flatten_pulled_deliverables, run_trial


def _task(tmp_path: Path) -> Task:
    """A synthetic task whose task_folder exists so the seeding step has
    something to push. Contents are scratch bytes, not benchmark text.
    """
    root = tmp_path / "task"
    folder = root / "task_folder"
    folder.mkdir(parents=True)
    (folder / "TASK_INSTRUCTIONS.txt").write_text("synthetic instructions\n", encoding="utf-8")
    return Task(split="easy", occupation="synthetic", task_num=1, root=root)


class FakeAdapter:
    """Minimal stand-in for an agents.Adapter."""

    name = "synthetic-agent"
    image_alias = "jobbench-synthetic-agent"
    session_dirs: tuple[str, ...] = ()
    metrics_source = "events"

    async def configure(self, dtu, *, model: str) -> None:
        return None

    def command(self) -> list[str]:
        return ["synthetic-agent", "run"]


class FakeDTU:
    """Stand-in for a launched DTU.

    `exec_result` is either a CommandResult to return or an exception to
    raise; `pull_writes` is a callable given the local destination for the
    deliverables pull, so a test can decide what "landed".
    """

    def __init__(self, *, exec_result, pull_writes=None, pull_raises=None):
        self.id = "jb-synthetic-0001"
        self.destroyed = False
        self._exec_result = exec_result
        self._pull_writes = pull_writes
        self._pull_raises = pull_raises
        self.pushes: list[tuple[str, str]] = []

    async def file_push(self, src, destination) -> None:
        self.pushes.append((str(src), destination))

    async def file_pull(self, remote, local) -> None:
        if self._pull_raises is not None:
            raise self._pull_raises
        if self._pull_writes is not None:
            self._pull_writes(Path(local))

    async def exec_cmd(self, command, *, timeout_s=None, stream_to_logfile=None):
        if stream_to_logfile is not None:
            stream_to_logfile.parent.mkdir(parents=True, exist_ok=True)
            stream_to_logfile.write_text(
                f"$ {' '.join(command)}\nsynthetic agent output\n"
                "\n--- stderr ---\n\n--- exit 0 (1.0s) ---\n\n",
                encoding="utf-8",
            )
        if isinstance(self._exec_result, BaseException):
            raise self._exec_result
        return self._exec_result

    async def destroy(self) -> None:
        self.destroyed = True


def _install(monkeypatch, dtu: FakeDTU) -> None:
    """Wire trial.py's collaborators to fakes: adapter registry, image alias,
    launch-profile rendering, and DTU.launch.
    """
    monkeypatch.setattr(trial_mod.agents, "get", lambda name, **kw: FakeAdapter())
    monkeypatch.setattr(trial_mod.images, "agent_alias", lambda name: "jobbench-synthetic-agent")
    monkeypatch.setattr(
        trial_mod, "_render_launch_profile", lambda alias, dest: (dest.touch(), dest)[1]
    )

    async def _launch(profile_path, *, name=None, **kwargs):
        dtu.id = name or dtu.id
        return dtu

    monkeypatch.setattr(trial_mod.DTU, "launch", _launch)


def _run(tmp_path: Path, dtu: FakeDTU, **kwargs):
    trial_dir = tmp_path / "trial"
    return asyncio.run(
        run_trial(
            "synthetic-agent",
            _task(tmp_path),
            trial_dir,
            model="synthetic-model",
            **kwargs,
        )
    )


def _writes_one_file(local: Path) -> None:
    """Simulate `file_pull` landing /workspace/output/ under deliverables/,
    which the CLI nests one level deep (see _flatten_pulled_deliverables).
    """
    nested = local / "output"
    nested.mkdir(parents=True, exist_ok=True)
    (nested / "result.txt").write_text("synthetic deliverable\n", encoding="utf-8")


def _writes_nothing(local: Path) -> None:
    (local / "output").mkdir(parents=True, exist_ok=True)


def _ok(returncode: int = 0) -> CommandResult:
    return CommandResult(returncode=returncode, stdout="", stderr="", elapsed_s=1.0)


# ---------------------------------------------------------------------------
# status decision tree
# ---------------------------------------------------------------------------


def test_status_completed_on_exit_zero_with_deliverables(tmp_path: Path, monkeypatch):
    dtu = FakeDTU(exec_result=_ok(0), pull_writes=_writes_one_file)
    _install(monkeypatch, dtu)

    result = _run(tmp_path, dtu)

    assert result.status == "completed"
    assert result.exit_code == 0
    assert result.deliverable_count == 1
    assert result.error is None


def test_status_crashed_on_nonzero_exit(tmp_path: Path, monkeypatch):
    """A non-zero agent exit is a crash even if it left files behind --
    deliverables do not launder a failed run into a success.
    """
    dtu = FakeDTU(exec_result=_ok(1), pull_writes=_writes_one_file)
    _install(monkeypatch, dtu)

    result = _run(tmp_path, dtu)

    assert result.status == "crashed"
    assert result.exit_code == 1
    assert result.deliverable_count == 1


def test_status_no_deliverables_on_exit_zero_with_empty_output(tmp_path: Path, monkeypatch):
    """A deliverable-free "success" is not success. It must be distinguishable
    from `crashed` (the agent ran fine) and from `completed` (it produced
    nothing to grade).
    """
    dtu = FakeDTU(exec_result=_ok(0), pull_writes=_writes_nothing)
    _install(monkeypatch, dtu)

    result = _run(tmp_path, dtu)

    assert result.status == "no_deliverables"
    assert result.exit_code == 0
    assert result.deliverable_count == 0
    # The agent itself succeeded; that must not be rewritten as a crash.
    assert result.error is None


def test_status_timeout_when_the_exec_layer_raises_dtuerror(tmp_path: Path, monkeypatch):
    """The only path that produces `timeout`: `dtu.exec_cmd` raising DTUError,
    which happens when the harness's own asyncio wait expires.
    """
    dtu = FakeDTU(
        exec_result=DTUError("DTU command timed out after 3720.0s: amplifier-digital-twin exec"),
        pull_writes=_writes_nothing,
    )
    _install(monkeypatch, dtu)

    result = _run(tmp_path, dtu, timeout_s=10.0)

    assert result.status == "timeout"
    assert result.exit_code is None
    assert "timed out" in (result.error or "")


def test_timeout_survives_a_later_no_deliverables_check(tmp_path: Path, monkeypatch):
    """A timed-out trial produced nothing, but must stay `timeout` -- the
    `no_deliverables` downgrade applies only to a run that exited 0.
    """
    dtu = FakeDTU(exec_result=DTUError("timed out"), pull_writes=_writes_nothing)
    _install(monkeypatch, dtu)

    assert _run(tmp_path, dtu, timeout_s=10.0).status == "timeout"


def test_timeout_still_pulls_partial_deliverables(tmp_path: Path, monkeypatch):
    """Partial output after a timeout is still signal and must be captured."""
    dtu = FakeDTU(exec_result=DTUError("timed out"), pull_writes=_writes_one_file)
    _install(monkeypatch, dtu)

    result = _run(tmp_path, dtu, timeout_s=10.0)

    assert result.status == "timeout"
    assert result.deliverable_count == 1


def test_status_crashed_when_setup_raises_before_exec(tmp_path: Path, monkeypatch):
    """A failure in seeding (or any earlier stage) is a crash, recorded with
    the reason rather than swallowed.
    """
    dtu = FakeDTU(exec_result=_ok(0))
    _install(monkeypatch, dtu)

    async def _boom(src, destination):
        raise DTUError("file-push failed: synthetic")

    dtu.file_push = _boom  # type: ignore[method-assign]

    result = _run(tmp_path, dtu)

    assert result.status == "crashed"
    assert result.exit_code is None
    assert "file-push failed" in (result.error or "")


def test_timeout_is_not_overwritten_by_a_later_crash(tmp_path: Path, monkeypatch):
    """The outer handler explicitly preserves `timeout`. If a post-timeout
    stage then blows up, the trial must still read as a timeout -- that is
    what actually happened to the agent.
    """
    dtu = FakeDTU(exec_result=DTUError("timed out"))
    _install(monkeypatch, dtu)

    async def _boom(remote, local):
        raise RuntimeError("synthetic post-timeout failure")

    dtu.file_pull = _boom  # type: ignore[method-assign]

    result = _run(tmp_path, dtu, timeout_s=10.0)

    assert result.status == "timeout"


def test_deliverable_pull_failure_does_not_mask_the_agent_result(tmp_path: Path, monkeypatch):
    """A DTUError from the deliverables pull is recorded in `error` but must
    not rewrite a successful agent run into a crash -- it is a harness-side
    retrieval problem, and the count already says nothing landed.
    """
    dtu = FakeDTU(exec_result=_ok(0), pull_raises=DTUError("file-pull failed: synthetic"))
    _install(monkeypatch, dtu)

    result = _run(tmp_path, dtu)

    assert result.status == "no_deliverables"
    assert "file-pull failed" in (result.error or "")


def test_status_cli_enforced_overrun_is_classified_crashed(tmp_path: Path, monkeypatch):
    """DISCREPANCY, pinned as observed rather than as intended.

    trial.py asks for `exec_cmd(timeout_s=timeout_s + 60)`; dtu.exec_cmd
    passes that same value to the CLI as `--timeout` and sets its own asyncio
    wait to `timeout_s + 60 + 60`. The CLI therefore enforces its limit 60s
    BEFORE the asyncio wait can fire, and it reports that enforcement as a
    non-zero exit (JSON mode catches subprocess.TimeoutExpired and exits 1),
    not as a DTUError. Only the DTUError path yields `status = "timeout"`.

    Net effect: an agent that merely overruns its wall-clock budget is
    recorded as `crashed`, and `timeout` is reachable only when the CLI
    process itself hangs past its own limit. The reason is not recorded in
    `error` either -- that field is only written on the DTUError path -- so
    the sole surviving evidence is the CLI's stderr in agent.log.

    This test pins the current behaviour so the discrepancy is visible; it is
    not an endorsement of it.
    """
    timed_out = CommandResult(
        returncode=1,
        stdout="",
        stderr="Error: Command '['incus', 'exec', ...]' timed out after 70 seconds",
        elapsed_s=70.0,
    )
    dtu = FakeDTU(exec_result=timed_out, pull_writes=_writes_nothing)
    _install(monkeypatch, dtu)

    result = _run(tmp_path, dtu, timeout_s=10.0)

    assert result.status == "crashed"
    assert result.status != "timeout"
    # The overrun is not recorded as the reason anywhere in trial.json.
    assert result.error is None


# ---------------------------------------------------------------------------
# trial.json / bookkeeping around the status
# ---------------------------------------------------------------------------


def test_trial_json_is_written_for_every_status(tmp_path: Path, monkeypatch):
    dtu = FakeDTU(exec_result=DTUError("timed out"), pull_writes=_writes_nothing)
    _install(monkeypatch, dtu)

    result = _run(tmp_path, dtu, timeout_s=10.0)

    data = json.loads((tmp_path / "trial" / "trial.json").read_text(encoding="utf-8"))
    assert data["status"] == result.status == "timeout"
    assert data["dtu_id"] == dtu.id
    assert data["warnings"] == []


def test_dtu_is_destroyed_even_when_the_trial_crashes(tmp_path: Path, monkeypatch):
    dtu = FakeDTU(exec_result=_ok(0))
    _install(monkeypatch, dtu)

    async def _boom(src, destination):
        raise DTUError("synthetic")

    dtu.file_push = _boom  # type: ignore[method-assign]

    _run(tmp_path, dtu)

    assert dtu.destroyed is True


def test_metrics_absent_is_not_available_never_zero(tmp_path: Path, monkeypatch):
    """No session telemetry must read as not_available, not a fabricated 0 --
    a zero cost and an uncollected cost are different facts.
    """
    dtu = FakeDTU(exec_result=_ok(0), pull_writes=_writes_one_file)
    _install(monkeypatch, dtu)

    result = _run(tmp_path, dtu)

    assert result.cost_usd == NOT_AVAILABLE
    assert result.total_tokens == NOT_AVAILABLE
    assert result.llm_responses == NOT_AVAILABLE


def test_status_and_warnings_stay_separate(tmp_path: Path, monkeypatch):
    """A quality warning is not a status. A run that trips the tool-result-loss
    detector still completed; the warning rides alongside.
    """
    dtu = FakeDTU(exec_result=_ok(0), pull_writes=_writes_one_file)
    _install(monkeypatch, dtu)

    async def _exec(command, *, timeout_s=None, stream_to_logfile=None):
        stream_to_logfile.parent.mkdir(parents=True, exist_ok=True)
        stream_to_logfile.write_text(
            f"$ {' '.join(command)}\n"
            f"{trial_mod.TOOL_RESULT_LOSS_SIGNATURES[0]}\n"
            "\n--- stderr ---\n\n--- exit 0 (1.0s) ---\n\n",
            encoding="utf-8",
        )
        return _ok(0)

    dtu.exec_cmd = _exec  # type: ignore[method-assign]

    result = _run(tmp_path, dtu)

    assert result.status == "completed"
    assert [w["kind"] for w in result.warnings] == ["tool_result_loss"]


# ---------------------------------------------------------------------------
# _flatten_pulled_deliverables -- decides deliverable_count, hence the status
# ---------------------------------------------------------------------------


def test_flatten_moves_nested_output_contents_up(tmp_path: Path):
    """`file_pull` of /workspace/output/ lands files at deliverables/output/<f>
    (cp -r basename convention). Downstream expects deliverables/<f>.
    """
    deliverables = tmp_path / "deliverables"
    nested = deliverables / "output"
    nested.mkdir(parents=True)
    (nested / "a.txt").write_text("a", encoding="utf-8")
    (nested / "b.txt").write_text("b", encoding="utf-8")

    _flatten_pulled_deliverables(deliverables)

    assert not nested.exists()
    assert sorted(p.name for p in deliverables.iterdir()) == ["a.txt", "b.txt"]


def test_flatten_preserves_subdirectories(tmp_path: Path):
    """A deliverable that is itself a directory moves up intact, so its files
    still count toward deliverable_count.
    """
    deliverables = tmp_path / "deliverables"
    sub = deliverables / "output" / "report"
    sub.mkdir(parents=True)
    (sub / "page1.txt").write_text("p1", encoding="utf-8")

    _flatten_pulled_deliverables(deliverables)

    assert (deliverables / "report" / "page1.txt").read_text(encoding="utf-8") == "p1"
    assert not (deliverables / "output").exists()


def test_flatten_is_a_noop_without_a_nested_output_dir(tmp_path: Path):
    """Some pulls land flat already. Flattening must not disturb them."""
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "a.txt").write_text("a", encoding="utf-8")

    _flatten_pulled_deliverables(deliverables)

    assert [p.name for p in deliverables.iterdir()] == ["a.txt"]


def test_flatten_handles_an_empty_nested_output_dir(tmp_path: Path):
    """The no_deliverables case: the directory exists but is empty. It must be
    removed cleanly so deliverable_count is 0 rather than 0-plus-a-stray-dir.
    """
    deliverables = tmp_path / "deliverables"
    (deliverables / "output").mkdir(parents=True)

    _flatten_pulled_deliverables(deliverables)

    assert not (deliverables / "output").exists()
    assert list(deliverables.iterdir()) == []


def test_flatten_missing_deliverables_dir_is_a_noop(tmp_path: Path):
    """A trial that never pulled anything must not raise here."""
    _flatten_pulled_deliverables(tmp_path / "nonexistent")


def test_flatten_overwrites_a_colliding_file(tmp_path: Path):
    """The nested copy is the freshly-pulled one and wins."""
    deliverables = tmp_path / "deliverables"
    nested = deliverables / "output"
    nested.mkdir(parents=True)
    (deliverables / "a.txt").write_text("stale", encoding="utf-8")
    (nested / "a.txt").write_text("fresh", encoding="utf-8")

    _flatten_pulled_deliverables(deliverables)

    assert (deliverables / "a.txt").read_text(encoding="utf-8") == "fresh"


def test_flatten_overwrites_a_colliding_directory(tmp_path: Path):
    deliverables = tmp_path / "deliverables"
    stale = deliverables / "report"
    stale.mkdir(parents=True)
    (stale / "old.txt").write_text("stale", encoding="utf-8")
    fresh = deliverables / "output" / "report"
    fresh.mkdir(parents=True)
    (fresh / "new.txt").write_text("fresh", encoding="utf-8")

    _flatten_pulled_deliverables(deliverables)

    assert (deliverables / "report" / "new.txt").exists()
    assert not (deliverables / "report" / "old.txt").exists()


@pytest.mark.parametrize(
    ("writer", "expected_status", "expected_count"),
    [
        (_writes_one_file, "completed", 1),
        (_writes_nothing, "no_deliverables", 0),
    ],
    ids=["one-file", "empty"],
)
def test_flattening_drives_the_no_deliverables_classification(
    tmp_path: Path, monkeypatch, writer, expected_status, expected_count
):
    """End to end: whether flattening finds files is exactly what decides
    `completed` vs `no_deliverables` for an exit-0 run.
    """
    dtu = FakeDTU(exec_result=_ok(0), pull_writes=writer)
    _install(monkeypatch, dtu)

    result = _run(tmp_path, dtu)

    assert result.status == expected_status
    assert result.deliverable_count == expected_count
    assert not (tmp_path / "trial" / "deliverables" / "output").exists()

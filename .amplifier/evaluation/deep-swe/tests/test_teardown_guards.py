"""Regression tests for cancellation-safe trial teardown.

WHY THIS FILE EXISTS. pier runs the agent as
``asyncio.wait_for(agent.run(...), timeout=agent_timeout_sec)``. When the agent
burns its budget -- a LIKELY outcome on a full-budget deep-swe run, not an edge
case -- our task is cancelled and ``run()``'s ``finally`` block executes while
cancellation is in flight. A bare ``await`` there is cancelled too, and
``except Exception`` cannot save it because ``CancelledError`` inherits from
``BaseException``.

So the timed-out trial -- the one whose trajectory, token spend and crash
evidence we most want -- is exactly the trial that would silently lose:

  - the fallback commit      -> 0-byte model.patch, total loss of partial credit
  - the session tree         -> no events.jsonl, so no token or cost accounting

`_await_guarded` prevents that. These tests pin the behaviour, including a
control test that demonstrates the loss WITHOUT the guard, so nobody can
conclude the shielding is decoration and "simplify" it away.

Run:  python3 -m pytest tests/ -x
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from deepswe_agents import base
from deepswe_agents.base import FALLBACK_MARKER, AmplifierBaseAgent

SESSION_DIR = "/root/.amplifier-agent/state/workspaces"


class StubAgent(AmplifierBaseAgent):
    """Concrete agent exposing the real teardown methods and nothing else.

    ``BaseInstalledAgent.__init__`` wants pier runtime plumbing (environment
    handles, install specs, model config) that has no bearing on the shielding
    logic under test, so it is deliberately not called. ``__abstractmethods__``
    is cleared so instantiation works whether the real pier (an ABC) or the
    conftest stub is in play.
    """

    __abstractmethods__ = frozenset()

    SESSION_DIRS = (SESSION_DIR,)

    def __init__(self, logs_dir: Path) -> None:
        self.logs_dir = logs_dir
        self.logger = logging.getLogger("test.stub-agent")

    @staticmethod
    def name() -> str:
        return "stub-agent"

    def agent_install_steps(self) -> list:
        return []

    def run_command(self, instruction_path: str) -> str:
        return "true"


class SlowEnv:
    """Environment whose transfers are slow enough to be interrupted mid-flight.

    Records what actually completed, so a test can tell "the work finished"
    from "the call returned".
    """

    def __init__(self, delay: float = 0.15) -> None:
        self.delay = delay
        self.started = asyncio.Event()
        self.completed: list[str] = []

    async def download_dir(self, source_dir: str, target_dir: Path | str) -> None:
        self.started.set()
        await asyncio.sleep(self.delay)
        target = Path(target_dir)
        target.mkdir(parents=True, exist_ok=True)
        (target / "events.jsonl").write_text('{"event":"llm:response"}\n', encoding="utf-8")
        self.completed.append(source_dir)


class HangingEnv:
    """Environment whose transfers never return (a wedged container)."""

    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def download_dir(self, source_dir: str, target_dir: Path | str) -> None:
        self.started.set()
        await asyncio.sleep(3600)


class CommitEnv:
    """Environment standing in for the container during the fallback commit."""

    def __init__(self, delay: float = 0.15) -> None:
        self.delay = delay
        self.started = asyncio.Event()
        self.commands: list[str] = []

    def agent_process_env(self, env: dict) -> dict:
        return env

    async def exec(self, command: str, env: dict | None = None, user: str | None = None):
        self.started.set()
        await asyncio.sleep(self.delay)
        self.commands.append(command)
        return SimpleNamespace(stdout=f"{FALLBACK_MARKER}: committed", stderr="")


async def _cancelled_trial(teardown, env, n_cancels: int = 1) -> None:
    """Reproduce pier's timeout: cancel a running agent task, teardown in finally.

    *n_cancels* is how many times the trial task is cancelled. The first lands
    on the simulated agent command; any further ones land while teardown is
    mid-transfer, which is the case a bare ``await`` cannot survive.
    """

    async def trial() -> None:
        try:
            await asyncio.sleep(3600)  # the agent command, still running
        finally:
            await teardown()

    task = asyncio.create_task(trial())
    await asyncio.sleep(0.02)  # let it reach the agent command
    task.cancel()  # pier's wait_for timeout fires

    for _ in range(n_cancels - 1):
        await asyncio.wait_for(env.started.wait(), timeout=5)  # transfer in flight
        task.cancel()
        await asyncio.sleep(0)  # let the cancellation be delivered

    with pytest.raises(asyncio.CancelledError):
        await task


def test_session_collection_completes_when_task_cancelled_mid_download(tmp_path):
    """THE REQUIRED PROPERTY: a timed-out trial still yields its session tree."""
    agent = StubAgent(tmp_path)
    env = SlowEnv()

    asyncio.run(
        _cancelled_trial(lambda: agent._collect_session_dirs_guarded(env), env, n_cancels=1)
    )

    assert env.completed == [SESSION_DIR]
    assert (tmp_path / "sessions" / "workspaces" / "events.jsonl").exists()


def test_session_collection_survives_repeated_cancellation(tmp_path):
    """Cancellation delivered again DURING teardown must not lose the work.

    This is what the bounded retry around `asyncio.shield` buys: the first
    cancellation interrupts our await, not the download task.
    """
    agent = StubAgent(tmp_path)
    env = SlowEnv()

    asyncio.run(
        _cancelled_trial(lambda: agent._collect_session_dirs_guarded(env), env, n_cancels=3)
    )

    assert env.completed == [SESSION_DIR]
    assert (tmp_path / "sessions" / "workspaces" / "events.jsonl").exists()


def test_unguarded_collection_loses_the_work_under_cancellation(tmp_path):
    """CONTROL: proves the shielding is load-bearing, not ceremony.

    Same scenario against the raw coroutine. `except Exception` inside it does
    NOT catch `CancelledError`, so the download dies mid-flight and the trial
    ends with no session tree -- the exact data loss the guard exists to stop.
    If this test ever starts passing with data collected, the hazard is gone
    and the guard can be reconsidered.
    """
    agent = StubAgent(tmp_path)
    env = SlowEnv()

    # _cancelled_trial already asserts the trial task ends cancelled; what
    # matters here is that the download did NOT survive it.
    asyncio.run(_cancelled_trial(lambda: agent._collect_session_dirs(env), env, n_cancels=2))

    assert env.completed == []
    assert not (tmp_path / "sessions" / "workspaces" / "events.jsonl").exists()


def test_fallback_commit_still_completes_under_cancellation(tmp_path):
    """Equivalence guard: the pre-existing commit shielding is unchanged.

    `_fallback_commit_guarded` now delegates to the shared `_await_guarded`.
    This is the property that refactor must not have broken -- losing it means
    a 0-byte model.patch on every timed-out trial.
    """
    agent = StubAgent(tmp_path)
    env = CommitEnv()

    asyncio.run(_cancelled_trial(lambda: agent._fallback_commit_guarded(env, {}), env, n_cancels=3))

    assert len(env.commands) == 1
    assert "git commit" in env.commands[0]


def test_wedged_container_cannot_stall_teardown_forever(tmp_path, monkeypatch, caplog):
    """A hung download must hit the hard timeout, not hang trial teardown."""
    monkeypatch.setattr(base, "TEARDOWN_COLLECT_TIMEOUT_SEC", 0.1)
    agent = StubAgent(tmp_path)
    env = HangingEnv()

    async def scenario() -> float:
        loop = asyncio.get_running_loop()
        start = loop.time()
        await agent._collect_session_dirs_guarded(env)
        return loop.time() - start

    with caplog.at_level(logging.WARNING):
        elapsed = asyncio.run(scenario())

    assert elapsed < 2.0, f"teardown took {elapsed:.2f}s; the hard timeout did not fire"
    assert "Could not collect session dir" in caplog.text


def test_timeout_budget_is_shared_across_session_dirs(tmp_path, monkeypatch, caplog):
    """The budget is overall, so adding SESSION_DIRS cannot extend teardown."""
    monkeypatch.setattr(base, "TEARDOWN_COLLECT_TIMEOUT_SEC", 0.1)
    agent = StubAgent(tmp_path)
    agent.SESSION_DIRS = ("/a/one", "/b/two", "/c/three")
    env = HangingEnv()

    async def scenario() -> float:
        loop = asyncio.get_running_loop()
        start = loop.time()
        await agent._collect_session_dirs_guarded(env)
        return loop.time() - start

    with caplog.at_level(logging.WARNING):
        elapsed = asyncio.run(scenario())

    # Three dirs must NOT cost three timeouts.
    assert elapsed < 0.1 * 3, f"budget was per-directory, not overall ({elapsed:.2f}s)"
    assert "budget" in caplog.text


def test_guard_gives_up_after_bounded_retries(tmp_path, caplog):
    """Relentless cancellation must terminate, not spin forever.

    The retry bound is what makes the guard safe to put in a `finally`: it
    cannot become an infinite loop that outlives the trial.
    """
    agent = StubAgent(tmp_path)

    async def scenario() -> None:
        work = asyncio.sleep(3600)  # never completes on its own
        guard = asyncio.ensure_future(agent._await_guarded(work, "Session collection"))
        for _ in range(6):  # more cancellations than the retry bound allows
            await asyncio.sleep(0.01)
            if guard.done():
                break
            guard.cancel()
        await asyncio.sleep(0.05)
        assert guard.done(), "the guard spun instead of giving up"
        with contextlib.suppress(asyncio.CancelledError):
            await guard

    with caplog.at_level(logging.WARNING):
        asyncio.run(scenario())

    assert "could not be awaited to completion" in caplog.text


def test_guarded_teardown_never_raises_on_failure(tmp_path, caplog):
    """A broken environment must be logged, never propagated out of teardown."""
    agent = StubAgent(tmp_path)

    class BoomEnv:
        async def download_dir(self, source_dir: str, target_dir: Path | str) -> None:
            raise RuntimeError("container gone")

    with caplog.at_level(logging.WARNING):
        asyncio.run(agent._collect_session_dirs_guarded(BoomEnv()))

    assert "container gone" in caplog.text

import _thread
import threading
import time

from amplifier_agent_engine._engine import native_exit


def start_untracked(release: threading.Event, *, completing: bool) -> None:
    # Started without the threading module, like a Tokio thread that attaches to Python.
    entered = threading.Event()

    def call_soon_threadsafe() -> None:
        entered.set()
        release.wait()

    def idle() -> None:
        entered.set()
        release.wait()

    _thread.start_new_thread(call_soon_threadsafe if completing else idle, ())
    assert entered.wait(5)


def test_exit_waits_until_a_native_completion_leaves_python():
    release = threading.Event()
    start_untracked(release, completing=True)
    started = time.monotonic()
    threading.Timer(0.05, release.set).start()
    native_exit.wait_for_native_threads(timeout=5)
    assert 0.05 <= time.monotonic() - started < 5
    assert not native_exit._completions_in_flight()


def test_exit_wait_is_bounded_when_a_native_completion_stays():
    release = threading.Event()
    start_untracked(release, completing=True)
    started = time.monotonic()
    native_exit.wait_for_native_threads(timeout=0.1)
    assert 0.1 <= time.monotonic() - started < 2
    release.set()


def test_exit_does_not_wait_for_other_untracked_threads():
    release = threading.Event()
    start_untracked(release, completing=False)
    started = time.monotonic()
    native_exit.wait_for_native_threads(timeout=5)
    assert time.monotonic() - started < 1
    release.set()

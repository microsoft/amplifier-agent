"""DTU-backed tests for per-turn usage on the Python wrapper SDK's terminal events.

Contract under test, on ``amplifier_agent_py``:

    ResultEvent gains  session_id, turn_id, exit_code, usage, stderr_tail
    ErrorEvent gains   session_id, turn_id, exit_code, usage
    Usage carries      input_tokens, output_tokens, cache_read_tokens,
                       cache_write_tokens, cost_usd (Decimal | None)

    spawn_agent / spawn_agent_sync gain ``stderr_tail_bytes: int | None = 4096``
        positive int -> the last N BYTES of stderr, never split mid-codepoint
        None         -> the entire stderr buffer
        0            -> disabled; the field is None

Today ``ResultEvent`` carries only ``text``, there is no ``Usage`` type, and
``stderr_tail`` is (a) present on ``ErrorEvent`` alone and (b) sliced as a ``str``, so
``STDERR_TAIL_BYTES`` counts CHARACTERS. Every case here is therefore
``xfail(strict=True)``; strict turns the moment the feature lands into a hard failure
that says "remove the marker".

Each case drives one real turn INSIDE the DTU via ``fixtures/usage_driver.py``, which
reports the terminal event as a single JSON line. The driver never asserts and never
raises on a missing attribute -- it records which contract attributes were absent, so a
failure here reads as "ResultEvent has no 'usage' attribute" rather than as a traceback
from inside the container.
"""

from __future__ import annotations

import json
import shlex
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from framework import dtu

from suites.usage.events_oracle import read_provider_usage

pytestmark = pytest.mark.dtu

# Host config seeded by DTU provisioning: anthropic / claude-sonnet-5 / approval yes.
# Same literal as the envelope half of this suite and as conftest.HOST_CONFIG; spelled
# out here rather than imported from conftest, because a conftest module is pytest's to
# load and importing one by name is not a supported entry point.
HOST_CONFIG = "/root/e2e/host-config.json"

# Short and tool-free, so the turn stays a single provider call in a single session.
PROMPT = "Reply with the single word: pong"

# Long enough that the engine's ndjson stderr stream comfortably exceeds the 4096-byte
# default cap, which is what makes "full buffer" and "default cap" distinguishable.
NOISY_PROMPT = "Write three paragraphs about the history of the banana trade."

# Non-ASCII on stderr is only reachable through the TEXT display: the ndjson renderer
# serializes with json.dumps' default ensure_ascii=True, so every non-ASCII codepoint
# leaves the process as an ASCII \uXXXX escape and a bytes-vs-chars bug would be
# invisible. The text renderer writes the reply verbatim to stderr as UTF-8.
NON_ASCII_PROMPT = "Write three paragraphs in Japanese about the history of the banana trade. Reply in Japanese only."

# Byte cap used by the bytes-not-chars case. Small enough that a character-counting
# implementation overshoots it by roughly 3x on Japanese text, and large enough that the
# tail is unambiguously inside the multibyte region rather than in the ASCII framing.
NON_ASCII_TAIL_BYTES = 512


def _session_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _run_driver(
    dtu_id: str,
    driver: str,
    engine_bin: str,
    *,
    session_id: str,
    prompt: str,
    display_mode: str | None = None,
    stderr_tail_bytes: str | None = None,
) -> dict[str, Any]:
    """Run one wrapper-driven turn in the DTU and return the driver's JSON report."""
    argv = [
        "python3",
        driver,
        "--session-id",
        session_id,
        "--prompt",
        prompt,
        "--config",
        HOST_CONFIG,
        "--engine-bin",
        engine_bin,
    ]
    if display_mode is not None:
        argv += ["--display-mode", display_mode]
    if stderr_tail_bytes is not None:
        argv += ["--stderr-tail-bytes", stderr_tail_bytes]

    # A login shell so /etc/profile.d exports (provider credential, TLS/proxy vars the
    # DTU installs) are in the environment the driver hands to the engine.
    result = dtu.exec_json(dtu_id, ["bash", "-lc", " ".join(shlex.quote(part) for part in argv)])

    stdout = result.get("stdout", "")
    lines = [line for line in stdout.splitlines() if line.strip()]
    if not lines:
        raise AssertionError(
            "the wrapper driver produced no report on stdout.\n"
            f"exit_code: {result.get('exit_code')}\nstderr:\n{result.get('stderr', '')}"
        )

    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise AssertionError(
            "the wrapper driver's last stdout line was not JSON.\n"
            f"exit_code: {result.get('exit_code')}\nstdout:\n{stdout}\nstderr:\n{result.get('stderr', '')}"
        ) from exc


def _require_ok(report: dict[str, Any]) -> None:
    """Fail with the driver's own diagnosis when it could not complete a turn."""
    if report.get("ok"):
        return

    kind = report.get("error_kind")
    if kind == "spawn_kwarg_rejected":
        pytest.fail(
            "spawn_agent_sync() rejected the 'stderr_tail_bytes' option -- the session "
            "option does not exist on this build of amplifier_agent_py.\n"
            f"driver error: {report.get('error')}"
        )
    pytest.fail(
        f"the wrapper driver could not complete a turn ({kind}): {report.get('error')}\n{report.get('traceback', '')}"
    )


def _require_attrs(report: dict[str, Any], *names: str) -> None:
    """Fail naming the exact contract attributes the terminal event is missing."""
    missing = [name for name in names if name in (report.get("missing_attrs") or [])]
    if missing:
        pytest.fail(
            f"{report.get('event_type')!r} event is missing {missing} -- the wrapper does not "
            "surface these yet.\n"
            f"attributes absent on the event: {report.get('missing_attrs')}"
        )


# --------------------------------------------------------------------------- #
# usage + identity on the terminal event
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def wrapper_turn(dtu_id: str, usage_driver: str, engine_bin: str) -> tuple[str, dict[str, Any]]:
    """Drive ONE wrapper turn and share its report across the usage and identity cases.

    Shared rather than run twice because both cases interrogate the same terminal
    event; running the turn once also means the identity assertions and the usage
    assertions are describing the same provider call, not two that happened to be
    similar.

    Deliberately does NO asserting on the report's contents. A missing ``usage``
    attribute is the expected red state, and raising it here would surface as a setup
    ERROR (which ``xfail`` does not cover) instead of a test failure.
    """
    session_id = _session_id("usage-wrap")
    report = _run_driver(
        dtu_id,
        usage_driver,
        engine_bin,
        session_id=session_id,
        prompt=PROMPT,
    )
    return session_id, report


def test_usage_result_event_carries_usage(dtu_id: str, wrapper_turn: tuple[str, dict[str, Any]]) -> None:
    """``ResultEvent.usage`` is populated and reports the real per-turn totals.

    "Real" is pinned against the same independent oracle the envelope suite uses: the
    provider's own per-call usage recorded in the session's
    ``context-intelligence/events.jsonl``. Comparing the wrapper's numbers to that
    rather than to the envelope it parsed is deliberate -- an envelope-to-wrapper
    comparison only proves the wrapper copied a field, and would agree perfectly with
    the envelope while both reported zero.
    """
    session_id, report = wrapper_turn
    _require_ok(report)
    _require_attrs(report, "usage")

    assert report.get("event_type") == "result", (
        f"expected a result event, got {report.get('event_type')!r}: {report.get('message') or report.get('code')}"
    )

    usage = report.get("usage")
    assert usage is not None, (
        "ResultEvent.usage is None on a turn that completed successfully. A turn that "
        "reached the provider always has usage to report."
    )

    missing_usage_attrs = report.get("usage_missing_attrs") or []
    assert not missing_usage_attrs, (
        f"Usage is missing {missing_usage_attrs}. The contract is "
        "input_tokens / output_tokens / cache_read_tokens / cache_write_tokens / cost_usd."
    )

    provider = read_provider_usage(dtu_id, session_id)
    context = (
        f"session={session_id}\n"
        f"wrapper usage: {json.dumps(usage, sort_keys=True)}\n"
        f"provider totals: new_input={provider.input_tokens} output={provider.output_tokens} "
        f"cache_read={provider.cache_read_tokens} cache_write={provider.cache_write_tokens} "
        f"charged_input={provider.charged_input}"
    )

    assert usage.get("output_tokens") == provider.output_tokens, (
        f"Usage.output_tokens ({usage.get('output_tokens')}) != provider-reported output "
        f"tokens ({provider.output_tokens}).\n{context}"
    )
    assert usage.get("input_tokens") == provider.charged_input, (
        f"Usage.input_tokens ({usage.get('input_tokens')}) != the provider's CHARGED input "
        f"total ({provider.charged_input}). Usage.input_tokens mirrors the envelope's "
        f"tokensIn, which is new input + cache reads + cache writes.\n{context}"
    )


def test_usage_result_event_identity(wrapper_turn: tuple[str, dict[str, Any]]) -> None:
    """The terminal event says which session and turn it belongs to, and how it exited.

    Without these a host holding several concurrent turns cannot attribute a result to
    the request that produced it, and usage numbers with no turn to attach to are not
    usable for accounting.
    """
    session_id, report = wrapper_turn
    _require_ok(report)
    _require_attrs(report, "session_id", "turn_id", "exit_code")

    assert report.get("session_id") == session_id, (
        f"ResultEvent.session_id is {report.get('session_id')!r}, expected the requested session id {session_id!r}."
    )

    turn_id = report.get("turn_id")
    assert isinstance(turn_id, str) and turn_id, f"ResultEvent.turn_id must be a non-empty string, got {turn_id!r}."

    assert report.get("exit_code") == 0, (
        f"ResultEvent.exit_code is {report.get('exit_code')!r}; a successful turn exits 0."
    )


# --------------------------------------------------------------------------- #
# stderr_tail_bytes
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TailCase:
    """One ``stderr_tail_bytes`` scenario.

    ``arg`` is what reaches the driver's ``--stderr-tail-bytes`` flag: ``None`` means
    the flag is omitted entirely, so the SDK's default applies rather than an explicitly
    passed value. ``check`` receives the driver's report.
    """

    arg: str | None
    prompt: str
    display_mode: str | None
    check: Callable[[dict[str, Any]], None]


def _check_default(report: dict[str, Any]) -> None:
    """Default (4096): the tail is present and no larger than the cap, in BYTES."""
    tail = report.get("stderr_tail")
    assert tail is not None, (
        "stderr_tail is None on a turn whose engine wrote a substantial ndjson stream to "
        "stderr. The default (4096) must produce a tail, not suppress it."
    )
    size = report["stderr_tail_utf8_len"]
    assert size <= 4096, f"stderr_tail is {size} bytes, above the 4096-byte default cap."
    assert size > 0, "stderr_tail is present but empty."


def _check_full(report: dict[str, Any]) -> None:
    """``None``: the ENTIRE buffer, which for this prompt exceeds the default cap."""
    tail = report.get("stderr_tail")
    assert tail is not None, "stderr_tail_bytes=None must return the full stderr buffer, not None."
    size = report["stderr_tail_utf8_len"]
    assert size > 4096, (
        f"stderr_tail is {size} bytes with stderr_tail_bytes=None. This turn ran with "
        "--display ndjson precisely so stderr would exceed the 4096-byte default; a value "
        "at or below it means the buffer was still being truncated."
    )


def _check_bounded(report: dict[str, Any]) -> None:
    """``512``: exactly 512 bytes retained."""
    tail = report.get("stderr_tail")
    assert tail is not None, "stderr_tail_bytes=512 must return a tail, not None."
    size = report["stderr_tail_utf8_len"]
    assert size == 512, (
        f"stderr_tail is {size} bytes with stderr_tail_bytes=512, expected exactly 512. "
        "The engine's ndjson stderr for this turn is far longer than 512 bytes and is "
        "ASCII, so no codepoint-boundary trim applies."
    )


def _check_disabled(report: dict[str, Any]) -> None:
    """``0``: the field is None, even though stderr had plenty to report."""
    assert report.get("stderr_tail") is None, (
        f"stderr_tail_bytes=0 must disable capture, but stderr_tail is {report['stderr_tail_utf8_len']} bytes."
    )


def _check_bytes_not_chars(report: dict[str, Any]) -> None:
    """Non-ASCII stderr: the cap counts BYTES, and the tail still decodes cleanly.

    This is the whole point of the fix. Slicing a ``str`` counts characters, so on
    Japanese text a 512-"byte" cap yields roughly 1536 real bytes. Slicing bytes without
    respecting codepoint boundaries yields a leading U+FFFD instead. The contract is
    both: at most N bytes, and no replacement character.
    """
    tail = report.get("stderr_tail")
    assert tail is not None, "stderr_tail is None; the non-ASCII case has nothing to measure."

    assert not report["stderr_tail_is_ascii"], (
        "the captured stderr tail is pure ASCII, so this case cannot distinguish a "
        "byte cap from a character cap. The turn was asked for a Japanese reply under "
        "--display text specifically so the reply would reach stderr as multibyte UTF-8."
    )

    size = report["stderr_tail_utf8_len"]
    chars = report["stderr_tail_char_len"]
    assert size <= NON_ASCII_TAIL_BYTES, (
        f"stderr_tail is {size} BYTES ({chars} characters) with "
        f"stderr_tail_bytes={NON_ASCII_TAIL_BYTES}. The cap is specified in bytes; this "
        "is a character count applied to a multibyte string."
    )
    assert not report["stderr_tail_has_replacement_char"], (
        "stderr_tail contains U+FFFD, so the byte slice landed mid-codepoint and was "
        "decoded with errors='replace'. A byte-bounded tail must back up to a codepoint "
        "boundary rather than emit a broken character."
    )


_TAIL_CASES = [
    pytest.param(
        TailCase(arg=None, prompt=NOISY_PROMPT, display_mode="ndjson", check=_check_default),
        id="usage-stderr-tail-default",
    ),
    pytest.param(
        TailCase(arg="none", prompt=NOISY_PROMPT, display_mode="ndjson", check=_check_full),
        id="usage-stderr-tail-full",
    ),
    pytest.param(
        TailCase(arg="512", prompt=NOISY_PROMPT, display_mode="ndjson", check=_check_bounded),
        id="usage-stderr-tail-bounded",
    ),
    pytest.param(
        TailCase(arg="0", prompt=NOISY_PROMPT, display_mode="ndjson", check=_check_disabled),
        id="usage-stderr-tail-disabled",
    ),
    pytest.param(
        TailCase(
            arg=str(NON_ASCII_TAIL_BYTES),
            prompt=NON_ASCII_PROMPT,
            display_mode="text",
            check=_check_bytes_not_chars,
        ),
        id="usage-stderr-tail-bytes-not-chars",
    ),
]


@pytest.mark.parametrize("case", _TAIL_CASES)
def test_usage_stderr_tail(dtu_id: str, usage_driver: str, engine_bin: str, case: TailCase) -> None:
    """``stderr_tail_bytes`` bounds the terminal event's stderr tail in real bytes."""
    report = _run_driver(
        dtu_id,
        usage_driver,
        engine_bin,
        session_id=_session_id("usage-tail"),
        prompt=case.prompt,
        display_mode=case.display_mode,
        stderr_tail_bytes=case.arg,
    )
    _require_ok(report)
    _require_attrs(report, "stderr_tail")

    assert report.get("event_type") == "result", (
        f"expected a result event, got {report.get('event_type')!r}: {report.get('message') or report.get('code')}"
    )

    case.check(report)

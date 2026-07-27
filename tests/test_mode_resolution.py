"""Unit tests for the shared mode resolver (``amplifier_agent_lib.mode_resolution``).

Pure and fast: no filesystem, no bundle prepare, no event loop. Every branch of
``resolve_mode`` is reachable because the candidate set is a parameter rather than
something the function discovers for itself.

The load-bearing behavior pinned here is the ``None`` vs ``[]`` distinction. Both are
falsy, so any code that checks ``if not known`` collapses two failures with opposite
blame -- "we could not enumerate the modes" (our fault, 503 / exit 1) and "we enumerated
and there are none" (the caller's fault, 400 / exit 2). The tests below assert ``is
None`` identity rather than truthiness for exactly that reason.
"""

from __future__ import annotations

import pytest

from amplifier_agent_lib import resources
from amplifier_agent_lib.mode_resolution import (
    ModeDiscoveryUnavailableError,
    ModeResolutionError,
    ModeUnknownError,
    discover_known_modes,
    resolve_mode,
)

# ---------------------------------------------------------------------------
# resolve_mode -- the happy path
# ---------------------------------------------------------------------------


def test_resolve_mode_returns_name_when_known() -> None:
    """A resolved name is returned unchanged, so callers can use the return value.

    Returning the name (rather than a bool) is what lets a call site read as a
    validating pass-through.
    """
    assert resolve_mode("plan", ["brainstorm", "plan"]) == "plan"


def test_resolve_mode_accepts_single_element_set() -> None:
    """Membership, not ordering or size, is the criterion."""
    assert resolve_mode("plan", ["plan"]) == "plan"


# ---------------------------------------------------------------------------
# resolve_mode -- unknown name (caller error)
# ---------------------------------------------------------------------------


def test_resolve_mode_raises_unknown_for_absent_name() -> None:
    """Discovery worked and the name is not in it: blame the caller, not the machinery.

    ``available`` is asserted SORTED because it is user-facing -- the CLI enumerates it
    in the rejection message, and a stable order keeps that message deterministic across
    runs and across whatever order discovery happened to walk its search paths in.
    """
    with pytest.raises(ModeUnknownError) as exc_info:
        # Deliberately unsorted input, to prove the exception does the sorting.
        resolve_mode("nope", ["plan", "brainstorm", "architect"])

    exc = exc_info.value
    assert exc.name == "nope"
    assert exc.available == ["architect", "brainstorm", "plan"]


def test_resolve_mode_unknown_message_names_the_mode_and_alternatives() -> None:
    """The message is the payload the CLI surfaces verbatim; it must be self-sufficient."""
    with pytest.raises(ModeUnknownError) as exc_info:
        resolve_mode("nope", ["plan"])

    message = str(exc_info.value)
    assert "nope" in message
    assert "plan" in message


def test_resolve_mode_raises_unknown_for_empty_known_list() -> None:
    """``known == []`` is a CALLER error, NOT an availability failure.

    This is the distinction the whole 400-vs-503 split rests on. An empty list means
    discovery RAN and authoritatively found zero modes -- so any name the caller supplied
    genuinely does not exist, and telling them so is accurate. Only ``None`` means we
    never got an answer and therefore cannot blame them.

    Conflating the two is the original bug in miniature: ``app.py``'s lifespan sets
    ``available_modes = []`` on discovery failure, so a membership-only check would report
    "unknown mode: plan" and send the user hunting for a typo that does not exist.
    """
    with pytest.raises(ModeUnknownError) as exc_info:
        resolve_mode("plan", [])

    exc = exc_info.value
    assert exc.name == "plan"
    assert exc.available == []
    # Explicitly NOT the unavailable error -- the two are never interchangeable.
    assert not isinstance(exc, ModeDiscoveryUnavailableError)


# ---------------------------------------------------------------------------
# resolve_mode -- unverifiable (server/environment error)
# ---------------------------------------------------------------------------


def test_resolve_mode_raises_unavailable_when_known_is_none() -> None:
    """``known is None`` means we could not check, which says nothing about the name.

    The requested name may well be perfectly valid; we simply have no way to know. The
    error therefore carries the name for diagnostics but makes no claim about it.
    """
    with pytest.raises(ModeDiscoveryUnavailableError) as exc_info:
        resolve_mode("plan", None)

    exc = exc_info.value
    assert exc.name == "plan"
    assert exc.cause is None
    # Must NOT be reportable as a caller error.
    assert not isinstance(exc, ModeUnknownError)


def test_unavailable_message_does_not_claim_the_name_is_wrong() -> None:
    """Wording matters here: this message is shown to a user who did nothing wrong."""
    with pytest.raises(ModeDiscoveryUnavailableError) as exc_info:
        resolve_mode("plan", None)

    message = str(exc_info.value)
    assert "could not be verified" in message
    assert "unavailable" in message


def test_unavailable_error_carries_optional_cause() -> None:
    """``cause`` lets a face log the real failure without leaking it into user-facing text."""
    cause = RuntimeError("discovery import failed")
    exc = ModeDiscoveryUnavailableError("plan", cause)

    assert exc.cause is cause
    assert "RuntimeError" in str(exc)


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        ModeUnknownError("x", []),
        ModeDiscoveryUnavailableError("x"),
    ],
)
def test_both_errors_share_a_common_base(exc: Exception) -> None:
    """A face that only needs "the mode did not resolve" can catch one clause.

    The two are separate classes because they map to different statuses/exit codes, but
    the shared base means a caller that does not care about the split is not forced to
    enumerate both -- and cannot accidentally catch only one of them.
    """
    assert isinstance(exc, ModeResolutionError)
    assert isinstance(exc, Exception)


def test_the_two_errors_are_not_substitutable() -> None:
    """Neither subclass may catch the other; the 400/503 split depends on it."""
    assert not issubclass(ModeUnknownError, ModeDiscoveryUnavailableError)
    assert not issubclass(ModeDiscoveryUnavailableError, ModeUnknownError)


# ---------------------------------------------------------------------------
# discover_known_modes -- the one place the None-on-failure convention is produced
# ---------------------------------------------------------------------------


def test_discover_known_modes_returns_names_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Projects ``resources.list_modes``' ``{name, description}`` dicts down to names.

    ``resolve_mode`` takes a flat name list, so the projection has to happen somewhere;
    doing it here keeps every call site from repeating it (and from drifting on the key).
    """
    monkeypatch.setattr(
        resources,
        "list_modes",
        lambda config=None: [
            {"name": "brainstorm", "description": "explore"},
            {"name": "plan", "description": "plan"},
        ],
    )

    assert discover_known_modes() == ["brainstorm", "plan"]


def test_discover_known_modes_forwards_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """The host config is passed through, so a configured search path is honored."""
    seen: list[object] = []

    def _fake_list_modes(config=None):
        seen.append(config)
        return []

    monkeypatch.setattr(resources, "list_modes", _fake_list_modes)
    host_config = {"modes": {"dir": "/tmp/x"}}

    discover_known_modes(host_config)

    assert seen == [host_config]


def test_discover_known_modes_returns_empty_list_when_none_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero modes discovered is a SUCCESS, and must return ``[]`` -- never ``None``.

    The counterpart to the failure case below. Success with nothing found is an
    authoritative answer, so it has to stay distinguishable from "no answer at all".
    """
    monkeypatch.setattr(resources, "list_modes", lambda config=None: [])

    result = discover_known_modes()

    assert result == []
    assert result is not None


@pytest.mark.parametrize(
    "exc",
    [
        ImportError("amplifier_module_hooks_mode is not installed"),
        RuntimeError("cannot prepare bundle from inside a running event loop"),
        OSError("permission denied walking the modes search path"),
    ],
    ids=["import-error", "runtime-error", "os-error"],
)
def test_discover_known_modes_returns_none_when_discovery_raises(
    monkeypatch: pytest.MonkeyPatch,
    exc: Exception,
) -> None:
    """Every discovery failure collapses to ``None`` -- never to ``[]``.

    ``is None`` is asserted rather than falsiness on purpose. ``[]`` is also falsy, and
    if this function ever degraded to returning it on failure the resolver would raise
    ``ModeUnknownError`` instead of ``ModeDiscoveryUnavailableError`` -- a 400 where a 503
    belongs, blaming the caller for our broken machinery. The identity check is the only
    assertion that catches that regression.

    The three exception types are unrelated on purpose: they are the real failure modes
    (hooks-mode package absent, cold-prepare attempted inside a running loop, search-path
    I/O), and they must be indistinguishable to a caller because all three mean the same
    thing -- we could not check.
    """

    def _raise(config=None):
        raise exc

    monkeypatch.setattr(resources, "list_modes", _raise)

    result = discover_known_modes()

    assert result is None, "discovery failure must be None (unverifiable), not [] (verified-empty)"


def test_discovery_failure_flows_through_to_the_unavailable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end for the pair: a broken discovery rejects the turn as a SERVER error.

    Pins the seam between the two functions -- it is the composition, not either function
    alone, that produces the 503 / exit-1 outcome.
    """

    def _raise(config=None):
        raise ImportError("boom")

    monkeypatch.setattr(resources, "list_modes", _raise)

    with pytest.raises(ModeDiscoveryUnavailableError):
        resolve_mode("plan", discover_known_modes())

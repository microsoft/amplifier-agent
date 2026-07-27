"""Shared mode-name resolution -- the single source of truth for BOTH faces.

Why this module exists
----------------------
Mode activation was implemented twice, byte-for-byte, once in
``amplifier_agent_lib._runtime`` (CLI) and once in
``amplifier_agent_http._session_runner`` (HTTP). Both warned about an unknown name and
then set ``session_state["active_mode"]`` to it anyway. Duplicated logic drifts; the two
copies had already diverged in log wording. This module is the one place the decision
lives, so there is nothing left to drift.

The contract
------------
::

    mode omitted             -> no restriction, turn runs      (per-turn-disable)
    mode named, known        -> activate, turn runs
    mode named, NOT known    -> REJECT, turn never starts      (client error)
    mode named, unverifiable -> REJECT, turn never starts      (server error)

**Core invariant:** ``session_state["active_mode"]`` is only ever set to a name that
resolved. Never to an unverified name.

That invariant, not the missing error message, is the actual severity of the old
behavior. A turn that runs with ``active_mode`` set to a name no policy backs is worse
than one that runs unrestricted: every downstream reader -- the CLI envelope's
``metadata.activeMode``, hooks-mode, a host UI -- believes a mode is active while
nothing whatsoever is enforced. Silent non-enforcement that reports itself as
enforcement is the bug.

Why modes fail closed and skills do not
---------------------------------------
A mode name always arrives through a structured channel: ``--mode`` on the CLI, or the
``[amplifier-agent:mode=<name>]`` directive over HTTP. It is never incidental prose, so
an unresolved name is unambiguously a caller error.

A skill sigil, by contrast, arrives inside the user's own prompt text, so an
unrecognized one passes through to the model as ordinary text
(``skill_dispatch.dispatch_skill_or_execute``). Rejecting it would refuse a turn over
something the user typed. The asymmetry is deliberate.

"Unknown" is not "could not verify"
-----------------------------------
These are different failures with different blame, and conflating them was half the
original bug::

    discovery ran, name not in the returned set   -> caller is wrong  -> 400 / exit 2
    discovery could not run at all                -> WE are wrong     -> 503 / exit 1

Not a theoretical distinction. ``app.py``'s lifespan catches a bare ``Exception`` around
discovery, and ``resources._ensure_discovery_importable`` genuinely raises when the
discovery packages are not importable. If a failure of the machinery were reported as
"unknown mode: plan", the user would go hunting for a typo that does not exist.

Why ``known`` is a parameter
----------------------------
``resolve_mode`` does NOT call ``resources.list_modes()`` itself. Passing the candidate
set in keeps this function pure and side-effect free, and buys three things:

* HTTP can pass its cheap lifespan snapshot instead of re-walking the filesystem from
  inside a running event loop (where cold-path discovery raises).
* The ``None`` vs ``[]`` distinction -- "could not enumerate" vs "enumerated, found
  none" -- is carried explicitly rather than being guessed from an empty list.
* Tests can drive every branch without touching the filesystem.

``discover_known_modes`` is the convenience wrapper for callers (the CLI) that do want
discovery performed for them, and it is the one place the ``None``-on-failure convention
is produced.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

logger = logging.getLogger(__name__)


class ModeResolutionError(Exception):
    """Base class for mode-resolution failures.

    Callers should catch the two concrete subclasses separately -- they map to different
    HTTP statuses and different CLI exit codes -- but this base exists so a face can
    catch both in one clause when it only needs "the mode did not resolve".
    """


class ModeUnknownError(ModeResolutionError):
    """Discovery succeeded and the requested name was not among the known modes.

    This is a CALLER error. The machinery worked; the name is wrong.

    Attributes:
        name: The mode name that was requested.
        available: Sorted list of the mode names that DO exist. May be empty, which
            means discovery genuinely found no modes (distinct from discovery failing --
            that raises ``ModeDiscoveryUnavailableError`` instead).
    """

    def __init__(self, name: str, available: Iterable[str]) -> None:
        self.name = name
        self.available = sorted(available)
        listed = ", ".join(self.available) or "none"
        super().__init__(f"mode {name!r} is not a known mode (available: {listed})")


class ModeDiscoveryUnavailableError(ModeResolutionError):
    """Mode discovery could not run, so the name could not be checked either way.

    This is a SERVER/ENVIRONMENT error, and it says NOTHING about whether the requested
    name is valid. Reporting it as "unknown mode" would blame the caller for a failure of
    our own machinery.

    Attributes:
        name: The mode name that could not be verified.
        cause: The underlying exception, when one was captured.
    """

    def __init__(self, name: str, cause: BaseException | None = None) -> None:
        self.name = name
        self.cause = cause
        detail = f" ({type(cause).__name__}: {cause})" if cause is not None else ""
        super().__init__(f"mode {name!r} could not be verified: mode discovery is unavailable{detail}")


def resolve_mode(name: str, known: list[str] | None) -> str:
    """Return ``name`` if it is a known mode, else raise.

    Pure: no I/O, no imports beyond stdlib, no global state. Every branch is reachable
    from a unit test without a filesystem.

    Args:
        name: The requested mode name.
        known: The candidate set. ``None`` means discovery could not run (raises
            ``ModeDiscoveryUnavailableError``); a list -- including an empty one --
            means discovery succeeded and is authoritative (raises ``ModeUnknownError``
            when ``name`` is absent from it).

    Returns:
        ``name``, unchanged, when it resolves.

    Raises:
        ModeDiscoveryUnavailableError: ``known`` is None.
        ModeUnknownError: ``known`` is a list and does not contain ``name``.
    """
    if known is None:
        raise ModeDiscoveryUnavailableError(name)
    if name not in known:
        raise ModeUnknownError(name, known)
    return name


def discover_known_modes(config: dict[str, Any] | None = None) -> list[str] | None:
    """Enumerate known mode names, or return ``None`` when discovery itself fails.

    The ``None``-on-failure convention is produced HERE and nowhere else, so the
    "could not verify" signal has exactly one origin.

    The broad ``except`` is deliberate and is not a fail-open: discovery can fail in
    several unrelated ways (``ImportError`` when the hooks-mode package is absent,
    ``RuntimeError`` from ``resources._ensure_discovery_importable`` when a cold prepare
    is needed inside a running event loop, ``OSError`` walking the search paths). All of
    them mean the same thing to a caller -- we could not check -- and all of them lead to
    a REJECTED turn, not a permitted one. Narrowing the clause would only convert an
    unanticipated discovery bug into an unhandled crash without making any turn safer.

    Args:
        config: Optional host config forwarded to ``resources.list_modes``.

    Returns:
        A list of mode names on success (possibly empty), or ``None`` if discovery
        could not be performed.
    """
    try:
        from amplifier_agent_lib import resources

        return [m["name"] for m in resources.list_modes(config)]
    except Exception as exc:  # see docstring: every failure means "could not check"
        logger.warning("mode discovery failed (%s: %s); mode names cannot be verified", type(exc).__name__, exc)
        return None

"""Reusable ``check`` factories for Windows e2e cases.

Each factory returns a callable taking the parsed stdout of a command and
raising AssertionError with the actual payload on mismatch. Pure logic, no
container access.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def _text(parsed: Any) -> str:
    return parsed if isinstance(parsed, str) else str(parsed)


def expect_contains(substring: str) -> Callable[[Any], None]:
    """Assert ``substring`` appears in the payload, case-insensitively."""

    def check(parsed: Any) -> None:
        text = _text(parsed)
        assert substring.lower() in text.lower(), f"expected {substring!r} in payload, got:\n{text}"

    return check


def expect_names(expected: set[str]) -> Callable[[Any], None]:
    """Assert a JSON list payload contains at least the ``expected`` names.

    Subset rather than exact equality, unlike the DTU suite's ``expect_set``.
    This image installs amplifier-agent from upstream, so it can legitimately
    carry a different set than a DTU built from the working tree. Pinning the
    exact set here would fail for a reason that says nothing about Windows.
    """

    def check(parsed: Any) -> None:
        assert isinstance(parsed, list), f"expected a list payload, got {type(parsed).__name__}:\n{parsed}"
        names = {e if isinstance(e, str) else e.get("name", "") for e in parsed}
        missing = expected - names
        assert not missing, f"missing {sorted(missing)} from payload; found {sorted(names)}"

    return check


def expect_non_empty() -> Callable[[Any], None]:
    """Assert the command produced some non-whitespace output."""

    def check(parsed: Any) -> None:
        assert _text(parsed).strip(), "expected non-empty output, got nothing"

    return check

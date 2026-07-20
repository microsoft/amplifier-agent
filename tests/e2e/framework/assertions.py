"""Shared structural checks for e2e case validation.

These are the reusable ``check`` callables plugged into ``E2ECase``/``Step``: given
the JSON-parsed (or raw string) payload, assert a structural property and raise
``AssertionError`` with the actual payload on mismatch. Feature suites compose these
rather than writing bespoke assertions per case.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def names(parsed: Any) -> set[str]:
    """Coerce parsed JSON (list of strings or list of {"name": ...}) to a name set."""
    if isinstance(parsed, dict):
        # Some CLIs wrap the list, e.g. {"skills": [...]}. Take the first list value.
        for value in parsed.values():
            if isinstance(value, list):
                parsed = value
                break
    if not isinstance(parsed, list):
        raise AssertionError(f"expected a list of names, got {type(parsed).__name__}: {parsed!r}")
    result: set[str] = set()
    for item in parsed:
        if isinstance(item, str):
            result.add(item)
        elif isinstance(item, dict) and "name" in item:
            result.add(item["name"])
        else:
            raise AssertionError(f"unexpected item shape: {item!r}")
    return result


def expect_set(expected: set[str]) -> Callable[[Any], None]:
    """Return a check asserting the parsed payload's name set equals ``expected``."""

    def check(parsed: Any) -> None:
        actual = names(parsed)
        assert actual == expected, f"expected {sorted(expected)}, got {sorted(actual)}"

    return check


def expect_contains(substring: str) -> Callable[[Any], None]:
    """Return a check asserting ``substring`` appears (case-insensitive) in the payload."""

    def check(parsed: Any) -> None:
        text = str(parsed)
        assert substring.lower() in text.lower(), f"expected {substring!r} in payload, got:\n{text}"

    return check

"""Shared structural checks for e2e case validation.

These are the reusable ``check`` callables plugged into ``E2ECase``/``Step``: given
the JSON-parsed (or raw string) payload, assert a structural property and raise
``AssertionError`` with the actual payload on mismatch. Feature suites compose these
rather than writing bespoke assertions per case.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def _entries(parsed: Any) -> list[Any]:
    """Normalize a listing payload to a bare list.

    Tolerates both shapes the two surfaces emit: the CLI's bare list and the HTTP
    envelope ``{"object": "list", "data": [...]}``. Any single list-valued wrapper
    works, e.g. ``{"skills": [...]}``.
    """
    if isinstance(parsed, dict):
        for value in parsed.values():
            if isinstance(value, list):
                return value
    if not isinstance(parsed, list):
        raise AssertionError(f"expected a list of names, got {type(parsed).__name__}: {parsed!r}")
    return parsed


def names(parsed: Any) -> set[str]:
    """Coerce parsed JSON (list of strings or list of {"name": ...}) to a name set."""
    result: set[str] = set()
    for item in _entries(parsed):
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


def expect_active_mode(expected: str | None) -> Callable[[Any], None]:
    """Return a check asserting the run envelope's ``metadata.activeMode`` equals ``expected``.

    ``run --output json`` emits the §4.1 envelope; the mode feature adds the active mode to
    ``metadata.activeMode``. ``expected=None`` asserts no mode is active (field null or absent),
    which is how an omitted ``--mode`` on a resume turn disables a previously-set mode.
    """

    def check(parsed: Any) -> None:
        if not isinstance(parsed, dict):
            raise AssertionError(f"expected an envelope object, got {type(parsed).__name__}: {parsed!r}")
        metadata = parsed.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        actual = metadata.get("activeMode")
        assert actual == expected, f"expected activeMode={expected!r}, got {actual!r}\nenvelope:\n{parsed!r}"

    return check


def _named_entry(parsed: Any, name: str) -> dict[str, Any]:
    """Return the single listing entry called ``name``, or raise with the payload.

    Zero matches and more than one match are both hard failures: a skills/modes
    listing collapses same-named files into ONE winner, so a duplicate name means
    the collapse itself broke.
    """
    entries = _entries(parsed)
    matches = [item for item in entries if isinstance(item, dict) and item.get("name") == name]
    if not matches:
        present = sorted(str(item.get("name")) for item in entries if isinstance(item, dict))
        raise AssertionError(f"no entry named {name!r}; listing contains {present}")
    if len(matches) > 1:
        raise AssertionError(f"expected exactly one entry named {name!r}, got {len(matches)}:\n{matches!r}")
    return matches[0]


def expect_shadow(name: str, *, source_contains: str, shadowed_contains: str) -> Callable[[Any], None]:
    """Return a check asserting a name collision is reported the way it resolved.

    Skills and modes are discovered first-match-wins across an ordered list of
    roots. Every listing entry therefore carries the winning file as ``source``
    and every same-named file that lost as ``shadowed: [{"source": ...}, ...]``.
    This check pins BOTH halves at once, which is the only way to state "this
    file won AND that file lost" as one assertion.

    Args:
        name: The colliding skill/mode name (exactly one entry must carry it).
        source_contains: Substring the winner's ``source`` path must contain.
        shadowed_contains: Substring that at least one ``shadowed`` entry's
            ``source`` path must contain.
    """

    def check(parsed: Any) -> None:
        entry = _named_entry(parsed, name)

        source = str(entry.get("source") or "")
        assert source_contains in source, (
            f"[{name}] wrong file won: expected source containing {source_contains!r}, got {source!r}\nentry:\n{entry!r}"
        )

        shadowed = entry.get("shadowed")
        if not isinstance(shadowed, list):
            raise AssertionError(
                f"[{name}] 'shadowed' must always be present and a list, got {shadowed!r}\nentry:\n{entry!r}"
            )
        losers = [str(item.get("source") or "") if isinstance(item, dict) else str(item) for item in shadowed]
        assert any(shadowed_contains in loser for loser in losers), (
            f"[{name}] expected a shadowed entry containing {shadowed_contains!r}, got {losers}\nentry:\n{entry!r}"
        )

    return check


def expect_no_shadows() -> Callable[[Any], None]:
    """Return a check asserting every listing entry reports an EMPTY ``shadowed``.

    The regression guard for self-shadowing: several discovery roots are
    conventional rather than absolute, so two of them routinely resolve to the
    same directory (``<cwd>/.amplifier/skills`` and ``~/.amplifier/skills`` when
    the process runs from ``$HOME``). Without collapsing roots by resolved path,
    every skill would be reported as shadowing itself.
    """

    def check(parsed: Any) -> None:
        offenders: dict[str, Any] = {}
        for entry in _entries(parsed):
            if not isinstance(entry, dict):
                raise AssertionError(f"expected listing entries to be objects, got {entry!r}")
            shadowed = entry.get("shadowed")
            if shadowed is None:
                raise AssertionError(
                    f"entry {entry.get('name')!r} has no 'shadowed' field; it is always present, "
                    f"empty when there was no collision\nentry:\n{entry!r}"
                )
            if shadowed:
                offenders[str(entry.get("name"))] = shadowed
        assert not offenders, f"expected no shadowing, got {offenders!r}"

    return check

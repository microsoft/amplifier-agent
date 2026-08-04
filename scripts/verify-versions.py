#!/usr/bin/env -S uv run python
"""verify-versions.py -- release-path gate: the protocol version must agree
across every manifest that pins it.

CONVENTION
    A cross-manifest consistency check, in the spirit of a monorepo version
    lint. Deliberately NOT a pytest test -- ``tests/`` means "e2e contract
    tests" and this is a release gate.

WHAT IT REPLACES
    ``tests/test_protocol_version_bump.py`` asserted
    ``PROTOCOL_VERSION == "0.3.0"`` -- a hardcoded literal that PINS rather than
    CHECKS. It cannot fail for the reason that actually hurts (one manifest
    bumped, the others forgotten); it only fails when someone bumps the version
    correctly, which trains people to edit the assertion and move on.

WHAT IT CHECKS
    ``AGENTS.md`` (Cross-component invariants, #1) states that bumping
    ``PROTOCOL_VERSION`` requires updating both wrappers, the conformance
    fixtures, and README.md, and landing them in ONE PR -- because splitting
    them leaves ``main`` in a state where one wrapper rejects the engine.
    Nothing mechanically enforced that. This does.

    ``src/amplifier_agent_lib/protocol/methods.py`` is the source of truth. All
    of the following must agree with it:

      - wrappers/typescript/src/index.ts       PROTOCOL_VERSION_REQUIRED_BY_WRAPPER
      - wrappers/python-py/.../_api.py         PROTOCOL_VERSION_REQUIRED_BY_WRAPPER
      - README.md                              "Protocol version: X" + the
                                               --protocol-version example
      - protocol/conformance/fixtures/*.yaml   setup.protocolVersion

    Fixtures declaring the deliberate version-skew sentinel
    ``2099-12-future-vN`` are exempt -- that value is the point of those
    fixtures (they assert an engine refuses a foreign protocol version).

PACKAGE VERSIONS
    Also reports the three published package versions. These are INDEPENDENTLY
    versioned on purpose (separate tag namespaces: ``v*``, ``wrapper-v*``,
    ``py-v*``), so they are REPORTED, never asserted equal. The report exists so
    a human or agent can eyeball drift at release time.

USAGE
    ./scripts/verify-versions.py       # from repo root, no arguments
    uv run scripts/verify-versions.py  # equivalent

EXIT CODES
    0  every protocol version pin agrees
    1  a pin disagrees, or a pin could not be located at all
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]

# Fixtures pinning this value are asserting protocol-skew refusal; they are
# meant to disagree with PROTOCOL_VERSION. See
# src/amplifier_agent_lib/protocol/conformance/fixtures/version_skew.yaml.
SKEW_SENTINEL = "2099-12-future-vN"

# (label, repo-relative path, regex with one capture group)
PROTOCOL_VERSION_SOURCES = [
    (
        "engine (source of truth)",
        "src/amplifier_agent_lib/protocol/methods.py",
        r'^PROTOCOL_VERSION\s*=\s*"([^"]+)"',
    ),
    (
        "TypeScript wrapper",
        "wrappers/typescript/src/index.ts",
        r'PROTOCOL_VERSION_REQUIRED_BY_WRAPPER\s*=\s*"([^"]+)"',
    ),
    (
        "Python wrapper",
        "wrappers/python-py/src/amplifier_agent_py/_api.py",
        r'^PROTOCOL_VERSION_REQUIRED_BY_WRAPPER\s*=\s*"([^"]+)"',
    ),
    (
        "README (prose)",
        "README.md",
        r"Protocol version:\s*\*\*`([^`]+)`\*\*",
    ),
    (
        "README (--protocol-version example)",
        "README.md",
        r"--protocol-version\s+([0-9][^\s`]*)",
    ),
]

SOURCE_OF_TRUTH_LABEL = PROTOCOL_VERSION_SOURCES[0][0]

FIXTURES_DIR = "src/amplifier_agent_lib/protocol/conformance/fixtures"

# (label, repo-relative path, kind, note)
PACKAGE_VERSION_SOURCES = [
    ("amplifier-agent (engine, PyPI)", "pyproject.toml", "toml", "tag prefix v*"),
    ("amplifier-agent-ts (npm)", "wrappers/typescript/package.json", "json", "tag prefix wrapper-v*"),
    ("amplifier-agent-py (PyPI)", "wrappers/python-py/pyproject.toml", "toml", "tag prefix py-v*"),
    ("amplifier-agent-client-ts (root)", "package.json", "json", "UNPUBLISHED ORPHAN -- not released"),
]


class Failure(Exception):
    """A verification check failed, with an actionable message."""


def _read(rel_path: str) -> str:
    path = REPO_ROOT / rel_path
    if not path.is_file():
        raise Failure(f"{rel_path} does not exist. The version pin it holds cannot be verified.")
    return path.read_text(encoding="utf-8")


def collect_protocol_versions() -> list[tuple[str, str, str]]:
    """Return (label, location, version) for every protocol version pin.

    Raises Failure if any pin cannot be located -- a pin that silently vanished
    is worse than a mismatched one, because the check would pass vacuously.
    """
    found: list[tuple[str, str, str]] = []

    for label, rel_path, pattern in PROTOCOL_VERSION_SOURCES:
        text = _read(rel_path)
        match = re.search(pattern, text, re.MULTILINE)
        if match is None:
            raise Failure(
                f"could not locate the protocol version in {rel_path} for '{label}'.\n"
                f"    Expected a line matching: {pattern}\n"
                "    Either the pin was removed or it was reformatted. If it moved, update\n"
                "    PROTOCOL_VERSION_SOURCES in scripts/verify-versions.py to match."
            )
        line_no = text[: match.start()].count("\n") + 1
        found.append((label, f"{rel_path}:{line_no}", match.group(1)))

    fixtures = sorted((REPO_ROOT / FIXTURES_DIR).glob("*.yaml"))
    if not fixtures:
        raise Failure(f"found no *.yaml under {FIXTURES_DIR} -- the fixture check would be vacuous.")

    for path in fixtures:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        setup = (data or {}).get("setup") or {}
        version = setup.get("protocolVersion")
        rel = path.relative_to(REPO_ROOT)
        if version is None:
            raise Failure(f"{rel} has no setup.protocolVersion. Every conformance fixture must declare one.")
        found.append((f"fixture {path.stem}", str(rel), str(version)))

    return found


def collect_package_versions() -> list[tuple[str, str, str, str]]:
    """Return (label, location, version, note) for each published package."""
    rows: list[tuple[str, str, str, str]] = []
    for label, rel_path, kind, note in PACKAGE_VERSION_SOURCES:
        text = _read(rel_path)
        if kind == "toml":
            version = tomllib.loads(text)["project"]["version"]
        else:
            version = json.loads(text)["version"]
        rows.append((label, rel_path, str(version), note))
    return rows


def _print_table(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> None:
    widths = [max(len(headers[i]), *(len(r[i]) for r in rows)) for i in range(len(headers))]
    fmt = "  " + "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print("  " + "  ".join("-" * w for w in widths))
    for row in rows:
        print(fmt.format(*row))


def main() -> int:
    print("verify-versions: cross-manifest protocol version consistency")
    print(f"  repo root: {REPO_ROOT}\n")

    try:
        pins = collect_protocol_versions()
        packages = collect_package_versions()
    except Failure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    expected = next(version for label, _, version in pins if label == SOURCE_OF_TRUTH_LABEL)

    rows: list[tuple[str, ...]] = []
    mismatches: list[tuple[str, str, str]] = []
    for label, location, version in pins:
        if version == expected:
            status = "ok"
        elif version == SKEW_SENTINEL:
            status = "exempt (skew sentinel)"
        else:
            status = "MISMATCH"
            mismatches.append((label, location, version))
        rows.append((label, location, version, status))

    print(f"PROTOCOL VERSION (source of truth: {expected})\n")
    _print_table(("where", "location", "value", "status"), rows)

    print("\nPACKAGE VERSIONS (independently versioned -- reported, not asserted)\n")
    _print_table(
        ("package", "location", "version", "note"),
        [(label, location, version, note) for label, location, version, note in packages],
    )

    print()
    if mismatches:
        print(
            f"FAIL: {len(mismatches)} manifest(s) disagree with the engine's PROTOCOL_VERSION ({expected}):",
            file=sys.stderr,
        )
        for label, location, version in mismatches:
            print(f"    {label}: {location} pins {version!r}, expected {expected!r}", file=sys.stderr)
        print(
            "\n  Per AGENTS.md 'Cross-component invariants' #1, a protocol bump must update\n"
            "  the engine, BOTH wrappers, the conformance fixtures, and README.md, and land\n"
            "  in ONE PR. Splitting them leaves main in a state where a wrapper rejects the\n"
            "  engine with protocol_version_mismatch.\n\n"
            f"  Fix: set every location above to {expected!r} (or bump the engine if the\n"
            "  engine is the one that is stale), then re-run this script.",
            file=sys.stderr,
        )
        return 1

    print(f"verify-versions: PASS -- all {len(pins)} protocol version pins agree on {expected}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

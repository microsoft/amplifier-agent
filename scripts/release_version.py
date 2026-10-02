"""Read, check, or set the release version that every package, pinned Git ref, and install command carries."""

import argparse
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
VERSION = r"\d+\.\d+\.\d+"

# Each pattern's single group is the version. Every listed file must contain at least one match.
SITES: dict[str, tuple[str, ...]] = {
    "packages/engine/pyproject.toml": (rf'(?m)^version = "({VERSION})"$',),
    "packages/http/pyproject.toml": (
        rf'(?m)^version = "({VERSION})"$',
        rf"microsoft/amplifier-agent@v({VERSION})#",
    ),
    "packages/python/pyproject.toml": (
        rf'(?m)^version = "({VERSION})"$',
        rf"microsoft/amplifier-agent@v({VERSION})#",
    ),
    "packages/engine/src/amplifier_agent_engine/__init__.py": (rf'(?m)^__version__ = "({VERSION})"$',),
    "packages/python/src/amplifier_agent/__init__.py": (rf'(?m)^__version__ = "({VERSION})"$',),
    "packages/typescript/package.json": (rf'(?m)^  "version": "({VERSION})",$',),
    "packages/typescript/src/version.ts": (rf'(?m)^export const version = "({VERSION})";$',),
    "README.md": (rf"--tag v({VERSION})\b",),
    "docs/install.md": (
        rf"--tag v({VERSION})\b",
        rf"--branch v({VERSION})\b",
        rf"amplifier-agent-ts@({VERSION})\b",
    ),
    "skills/amplifier-agent/SKILL.md": (rf"--branch v({VERSION})\b",),
    "evaluations/profiles/python/install.sh": (rf"--tag v({VERSION})\b",),
    "evaluations/profiles/http/install.sh": (rf"--tag v({VERSION})\b",),
    "evaluations/profiles/typescript/install.sh": (
        rf"--branch v({VERSION})\b",
        rf"amplifier-agent-ts@({VERSION})\b",
    ),
}


def found() -> dict[str, set[str]]:
    """Each file's distinct versions."""
    versions: dict[str, set[str]] = {}
    for name, patterns in SITES.items():
        text = (ROOT / name).read_text(encoding="utf-8")
        versions[name] = {match.group(1) for pattern in patterns for match in re.finditer(pattern, text)}
    return versions


def check(tag: str | None) -> str:
    versions = found()
    missing = sorted(name for name, seen in versions.items() if not seen)
    if missing:
        raise SystemExit(f"No release version found in: {', '.join(missing)}")
    distinct = set().union(*versions.values())
    if len(distinct) != 1:
        listed = "\n".join(f"  {name}: {', '.join(sorted(seen))}" for name, seen in versions.items())
        raise SystemExit(f"Release versions disagree:\n{listed}\nRun: python scripts/release_version.py set X.Y.Z")
    version = distinct.pop()
    if tag is not None and tag != f"v{version}":
        raise SystemExit(f"Tag {tag} does not match the release version v{version}")
    return version


def set_version(version: str) -> None:
    if not re.fullmatch(VERSION, version):
        raise SystemExit(f"Expected X.Y.Z, got {version!r}")
    for name, patterns in SITES.items():
        path = ROOT / name
        text = path.read_text(encoding="utf-8")
        for pattern in patterns:
            text = re.sub(pattern, lambda m: m.group(0).replace(m.group(1), version), text)
        path.write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check_parser = commands.add_parser("check", help="Fail unless every site carries one version; print it.")
    check_parser.add_argument("--tag", help="Also fail unless this tag is v<version>.")
    set_parser = commands.add_parser("set", help="Write a version to every site. Run `uv lock` afterwards.")
    set_parser.add_argument("version", help="X.Y.Z")
    args = parser.parse_args()
    if args.command == "set":
        set_version(args.version)
    print(check(getattr(args, "tag", None)))


if __name__ == "__main__":
    main()

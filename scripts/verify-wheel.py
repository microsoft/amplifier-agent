#!/usr/bin/env -S uv run python
"""verify-wheel.py -- release-path gate: the built wheel must contain every
non-Python data file the engine needs at runtime.

CONVENTION
    Build the real artifact, open it, assert its contents. This is deliberately
    NOT a pytest test -- ``tests/`` means "e2e contract tests" and this is a
    packaging gate that runs on the release path.

WHAT IT CHECKS
    1. ``uv build --wheel`` produces exactly one ``amplifier_agent-*.whl``
    2. ``protocol/spec.md`` ships
    3. ``protocol/schemas/*.schema.json`` ship
    4. the five D7 conformance fixtures ship
    5. every ``src/amplifier_agent_lib/bundle/**/*.md`` in the source tree ships
    6. the shipped ``bundle.md`` has valid YAML frontmatter, declares the bundle
       name, and references at least one amplifier module by git URL

WHY CHECK 5 MATTERS
    Nothing previously verified the shipped bundle content end-to-end. The old
    tests asserted a hardcoded set of 8 filenames, leaving
    ``bundle/skills/*/SKILL.md`` (8 paths) and ``bundle/modes/*.md`` (2 paths)
    covered by NOTHING. Add a new skill or mode, get it dropped from the wheel
    for any reason, and the build is green, the publish is green, the install is
    green -- and skill/mode discovery silently comes up short at runtime for
    every user.

    Check 5 is glob-driven against the source tree specifically so it closes
    permanently instead of needing a new assertion per file. It is mechanism-
    agnostic on purpose: it asserts the OUTCOME (this file is in the wheel), not
    the means. Two mechanisms currently put bundle markdown in the wheel, and
    the check does not care which one broke:

      - ``packages = ["src/amplifier_agent_lib"]`` in
        ``[tool.hatch.build.targets.wheel]``. This ships every file under that
        tree that is not excluded by the build config or by a VCS ignore rule --
        including ``.md``. This is what actually carries the bundle markdown
        today.
      - the hand-maintained ``force-include`` list (pyproject.toml lines 49-70).
        For the bundle ``.md`` files this is currently REDUNDANT with the
        ``packages`` entry -- verified empirically: deleting all 10
        skills/modes entries still produces a wheel containing all 10 files.
        It does still matter for anything the ``packages`` entry would exclude,
        because force-include bypasses VCS ignore rules.

    So the realistic ways a bundle ``.md`` goes missing are: a ``.gitignore``
    pattern growing to cover it (hatchling honors VCS ignores, and ``.gitignore``
    already carries ``build/`` and ``dist/``), the ``packages`` entry changing,
    a build-config ``exclude`` being added, or the content moving outside the
    packaged tree. All of those produce the same silent, shipped failure, and
    all of them fail this check.

USAGE
    ./scripts/verify-wheel.py       # from repo root, no arguments
    uv run scripts/verify-wheel.py  # equivalent

EXIT CODES
    0  wheel contains everything expected
    1  wheel is missing content or bundle.md is malformed
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"

# The D7 baseline contract fixtures. These five are the protocol's behavioural
# floor; wrappers run them to certify an engine.
REQUIRED_FIXTURES = (
    "l14_synthesis",
    "capability_negotiation",
    "subagent_lineage",
    "version_skew",
    "resume_continuity",
)

# Strategy 1 of docs/designs/2026-05-19-baked-in-bundle-decision.md: the bundle
# and its vendored agents are baked into the wheel rather than fetched at run
# time. Kept explicit (in addition to the glob in check 5) so a rename of one of
# these load-bearing files fails loudly rather than quietly passing the glob.
REQUIRED_BUNDLE_FILES = (
    "amplifier_agent_lib/bundle/bundle.md",
    "amplifier_agent_lib/bundle/context/system.md",
    "amplifier_agent_lib/bundle/agents/explorer.md",
    "amplifier_agent_lib/bundle/agents/architect.md",
    "amplifier_agent_lib/bundle/agents/builder.md",
    "amplifier_agent_lib/bundle/agents/debugger.md",
    "amplifier_agent_lib/bundle/agents/git-ops.md",
    "amplifier_agent_lib/bundle/agents/researcher.md",
)


class Failure(Exception):
    """A verification check failed, with an actionable message."""


def _build_wheel(out_dir: Path) -> Path:
    """Build the wheel into ``out_dir`` and return the path to it."""
    result = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(out_dir)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise Failure(
            "`uv build --wheel` failed. Reproduce with:\n"
            f"    uv build --wheel --out-dir /tmp/wheel\n\n{result.stderr.strip()}"
        )

    wheels = sorted(out_dir.glob("amplifier_agent-*.whl"))
    if len(wheels) != 1:
        raise Failure(f"expected exactly one amplifier_agent-*.whl, found {[w.name for w in wheels]}")
    return wheels[0]


def _packaging_hint(missing: Sequence[str]) -> str:
    """Render actionable remediation for wheel paths that failed to ship."""
    checks = "\n    ".join(f"git check-ignore -v src/{path}" for path in sorted(missing))
    entries = "\n    ".join(f'"src/{path}" = "{path}"' for path in sorted(missing))
    return (
        'These normally ship via `packages = ["src/amplifier_agent_lib"]` in\n'
        "[tool.hatch.build.targets.wheel], which includes every file under that tree\n"
        "that is not excluded by the build config or by a VCS ignore rule.\n\n"
        "1. Check whether a VCS ignore rule is swallowing them (hatchling honors these):\n\n"
        f"    {checks}\n\n"
        "2. If so, either narrow the ignore rule, or force-include the paths (which\n"
        "   bypasses VCS ignores) in [tool.hatch.build.targets.wheel.force-include]:\n\n"
        f"    {entries}\n\n"
        "3. Otherwise verify the `packages` entry and any `exclude` rules are intact."
    )


def check_spec_md(names: set[str]) -> str:
    path = "amplifier_agent_lib/protocol/spec.md"
    if path not in names:
        raise Failure(
            f'wheel is missing {path}\n\nIt is force-included in pyproject.toml as\n    "src/' + path + f'" = "{path}"'
        )
    return f"protocol spec ships ({path})"


def check_schemas(names: set[str]) -> str:
    schemas = [n for n in names if n.startswith("amplifier_agent_lib/protocol/schemas/") and n.endswith(".schema.json")]
    if not schemas:
        raise Failure(
            "wheel contains no protocol/schemas/*.schema.json files.\n\n"
            'They ship via the `packages = ["src/amplifier_agent_lib"]` entry in\n'
            "[tool.hatch.build.targets.wheel] -- check that entry is intact."
        )
    return f"{len(schemas)} protocol JSON schemas ship"


def check_fixtures(names: set[str]) -> str:
    required = {f"amplifier_agent_lib/protocol/conformance/fixtures/{stem}.yaml" for stem in REQUIRED_FIXTURES}
    missing = sorted(required - names)
    if missing:
        raise Failure(
            "wheel is missing D7 conformance fixtures:\n    " + "\n    ".join(missing) + "\n\n"
            'They ship via the `packages = ["src/amplifier_agent_lib"]` entry in\n'
            "[tool.hatch.build.targets.wheel] -- check that entry is intact."
        )
    return f"all {len(required)} D7 conformance fixtures ship"


def check_required_bundle_files(names: set[str]) -> str:
    missing = sorted(set(REQUIRED_BUNDLE_FILES) - names)
    if missing:
        raise Failure(
            "wheel is missing baked-in bundle files:\n    " + "\n    ".join(missing) + "\n\n" + _packaging_hint(missing)
        )
    return f"all {len(REQUIRED_BUNDLE_FILES)} baked-in bundle/agent files ship"


def check_every_bundle_markdown(names: set[str]) -> str:
    """Glob gate: every bundle .md in the source tree must be in the wheel.

    Mechanism-agnostic on purpose -- it asserts the outcome, not the means. See
    the module docstring for the failure modes it covers.
    """
    source_files = sorted((SRC_ROOT / "amplifier_agent_lib" / "bundle").rglob("*.md"))
    if not source_files:
        raise Failure(
            f"found no *.md under {SRC_ROOT / 'amplifier_agent_lib' / 'bundle'} -- "
            "the glob is looking in the wrong place, so this check is vacuous."
        )

    expected = {str(p.relative_to(SRC_ROOT)).replace("\\", "/") for p in source_files}
    missing = sorted(expected - names)
    if missing:
        raise Failure(
            f"{len(missing)} bundle markdown file(s) exist in src/ but are NOT in the wheel:\n    "
            + "\n    ".join(missing)
            + "\n\nHatchling only auto-includes *.py from the package dirs, so every .md\n"
            "needs an explicit force-include entry.\n\n" + _packaging_hint(missing)
        )
    return f"all {len(expected)} bundle/**/*.md files from src/ ship"


def check_bundle_md_frontmatter(zf: zipfile.ZipFile) -> str:
    """Validate the bundle.md that actually ships, not the one in src/."""
    path = "amplifier_agent_lib/bundle/bundle.md"
    content = zf.read(path).decode("utf-8")

    if not content.startswith("---\n"):
        raise Failure(f"{path} must start with '---\\n' (YAML frontmatter opening delimiter)")
    if "\n---\n" not in content:
        raise Failure(f"{path} must contain '\\n---\\n' to close the YAML frontmatter")
    if "amplifier-agent-behavioral-anchor" not in content:
        raise Failure(f"{path} must declare the bundle name 'amplifier-agent-behavioral-anchor'")
    if "github.com/microsoft/amplifier-module-" not in content:
        raise Failure(f"{path} must reference at least one microsoft/amplifier-module by git URL")

    return "shipped bundle.md has valid frontmatter, name, and module references"


def main() -> int:
    print("verify-wheel: building the wheel and inspecting its contents")
    print(f"  repo root: {REPO_ROOT}")

    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        try:
            wheel = _build_wheel(out_dir)
        except Failure as exc:
            print(f"\nFAIL: {exc}", file=sys.stderr)
            return 1

        print(f"  wheel:     {wheel.name}\n")

        with zipfile.ZipFile(wheel) as zf:
            names = {n.replace("\\", "/") for n in zf.namelist()}

            checks = [
                ("protocol spec.md", lambda: check_spec_md(names)),
                ("protocol schemas", lambda: check_schemas(names)),
                ("D7 conformance fixtures", lambda: check_fixtures(names)),
                ("baked-in bundle files", lambda: check_required_bundle_files(names)),
                ("every bundle/**/*.md (glob gate)", lambda: check_every_bundle_markdown(names)),
                ("bundle.md frontmatter", lambda: check_bundle_md_frontmatter(zf)),
            ]

            failures: list[tuple[str, str]] = []
            for label, fn in checks:
                try:
                    detail = fn()
                except Failure as exc:
                    print(f"  FAIL  {label}")
                    failures.append((label, str(exc)))
                else:
                    print(f"  OK    {label}: {detail}")

    print()
    if failures:
        for label, message in failures:
            print(f"FAIL: {label}\n\n{message}\n", file=sys.stderr)
        print(
            f"verify-wheel: FAIL -- {len(failures)} of {len(checks)} checks failed. "
            "The wheel would ship incomplete; fix pyproject.toml before releasing.",
            file=sys.stderr,
        )
        return 1

    print(f"verify-wheel: PASS -- all {len(checks)} checks passed against the built wheel.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

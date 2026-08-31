#!/usr/bin/env -S uv run python
"""verify-cache-restamp.py -- regression gate for the cross-installation
stale-cache FileNotFoundError (see bundle/cache.py::_restamp_agent_source_paths).

BUG THIS GUARDS
    ``load_and_prepare_cached``'s on-disk cache
    (``$AMPLIFIER_AGENT_HOME/cache/prepared/<aaa_version>/<bundle_sha256>/``)
    is keyed ONLY by (aaa_version, sha256(bundle.md content)) -- a per-user
    key, not a per-installation one. ``PreparedBundle.mount_plan["agents"]
    [name]["source_path"]`` is an ABSOLUTE path baked in at cold-prepare time
    against whichever installation happened to run the cold path.

    A second installation of the same version (a fresh `uv tool install`
    after a prior one was removed, a dev checkout alongside a packaged
    install, two side-by-side venvs, ...) warm-hits the SAME cache entry and
    gets back a ``PreparedBundle`` whose agent ``source_path``s point at the
    FIRST installation's site-packages tree -- which may no longer exist.
    ``make_turn_handler`` reads that path directly
    (``hydrate_agent_overlay(Path(entry["source_path"]))``), so the very
    first turn on the second installation raises::

        FileNotFoundError: [Errno 2] No such file or directory:
        '<first-installation-path>/amplifier_agent_lib/bundle/agents/explorer.md'

    even though the SECOND installation ships that exact file at its own,
    different, path.

CONVENTION
    Standalone verification script, not a pytest test -- ``tests/`` means
    "e2e contract tests" in this repo (see pytest.ini_options in
    pyproject.toml); this is a fast, hermetic, dependency-light regression
    gate for one function, matching ``scripts/verify-wheel.py``'s own
    rationale for living here instead.

WHAT IT CHECKS
    1. ``_restamp_agent_source_paths`` overwrites a stale (nonexistent)
       ``source_path`` with the current installation's real
       ``AGENTS_DIR/<name>.md`` path.
    2. It leaves an entry alone when the current installation has no file
       for that agent name (nothing to restamp to -- never invents a path).
    3. It is a no-op on a mount plan with no ``"agents"`` section.
    4. End-to-end: ``load_and_prepare_cached`` against a pickled artifact
       whose ``source_path`` values were built for a DIFFERENT (deleted)
       directory returns a ``PreparedBundle`` whose paths are fixed up to
       this process's own vendored agent files -- reading them back
       succeeds instead of raising ``FileNotFoundError``.

USAGE
    ./scripts/verify-cache-restamp.py       # from repo root, no arguments
    uv run scripts/verify-cache-restamp.py  # equivalent

EXIT CODES
    0  all checks passed
    1  a regression was detected
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import pickle
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from amplifier_agent_lib.bundle import AGENTS_DIR  # noqa: E402
from amplifier_agent_lib.bundle.cache import (  # noqa: E402
    _ARTIFACT_NAME,
    _MANIFEST_NAME,
    _restamp_agent_source_paths,
    cache_dir_for_version,
    load_and_prepare_cached,
)


class Failure(Exception):
    """A verification check failed, with an actionable message."""


def _fake_prepared(agents: dict) -> SimpleNamespace:
    """Build a minimal stand-in with the one attribute the function reads."""
    return SimpleNamespace(mount_plan={"agents": agents})


def check_restamps_stale_path() -> str:
    real_name = next(p.stem for p in AGENTS_DIR.glob("*.md"))
    stale_path = "/nonexistent-installation-xyz/amplifier_agent_lib/bundle/agents/" + real_name + ".md"
    prepared = _fake_prepared({real_name: {"name": real_name, "source_path": stale_path}})

    _restamp_agent_source_paths(prepared)

    fixed = prepared.mount_plan["agents"][real_name]["source_path"]
    expected = str(AGENTS_DIR / f"{real_name}.md")
    if fixed != expected:
        raise Failure(f"expected source_path restamped to {expected!r}, got {fixed!r}")
    if not Path(fixed).exists():
        raise Failure(f"restamped source_path {fixed!r} does not actually exist")
    return f"stale path for {real_name!r} restamped to the current installation's own file"


def check_leaves_unknown_agent_alone() -> str:
    stale_path = "/nonexistent-installation-xyz/amplifier_agent_lib/bundle/agents/no-such-agent.md"
    prepared = _fake_prepared({"no-such-agent": {"name": "no-such-agent", "source_path": stale_path}})

    _restamp_agent_source_paths(prepared)

    unchanged = prepared.mount_plan["agents"]["no-such-agent"]["source_path"]
    if unchanged != stale_path:
        raise Failure(f"expected untouched (no file to restamp to), got {unchanged!r}")
    return "unresolvable agent name left as-is rather than pointed at an invented path"


def check_noop_without_agents_section() -> str:
    prepared = SimpleNamespace(mount_plan={"tools": []})
    _restamp_agent_source_paths(prepared)  # must not raise
    if "agents" in prepared.mount_plan:
        raise Failure("an 'agents' key was unexpectedly added")
    return "no-op when mount_plan carries no 'agents' section"


def check_end_to_end_warm_path_survives_relocation() -> str:
    """Build a pickle whose paths point at a directory that no longer exists,
    then confirm the warm path fixes it up rather than raising.
    """
    real_names = sorted(p.stem for p in AGENTS_DIR.glob("*.md"))
    if not real_names:
        raise Failure(f"no vendored agent .md files found under {AGENTS_DIR}")

    aaa_version = "0.0.0-verify-cache-restamp"

    with tempfile.TemporaryDirectory() as home_tmp:
        import os

        env_backup = os.environ.get("AMPLIFIER_AGENT_HOME")
        os.environ["AMPLIFIER_AGENT_HOME"] = home_tmp
        try:
            cache_dir = cache_dir_for_version(aaa_version)
            cache_dir.mkdir(parents=True, exist_ok=True)

            # A directory that is guaranteed not to exist -- simulates a
            # since-removed prior installation.
            deleted_install = Path(tempfile.mkdtemp())
            deleted_install.rmdir()

            stale_agents = {
                name: {"name": name, "source_path": str(deleted_install / f"{name}.md")} for name in real_names
            }
            fake_prepared = _fake_prepared(stale_agents)

            artifact = cache_dir / _ARTIFACT_NAME
            manifest = cache_dir / _MANIFEST_NAME
            artifact.write_bytes(pickle.dumps(fake_prepared))
            bundle_hash = hashlib.sha256((SRC_ROOT / "amplifier_agent_lib/bundle/bundle.md").read_bytes()).hexdigest()[
                :16
            ]
            manifest.write_text(json.dumps({"aaa_version": aaa_version, "bundle_sha256_prefix": bundle_hash}))

            result = asyncio.run(load_and_prepare_cached(aaa_version=aaa_version))

            for name in real_names:
                source_path = result.mount_plan["agents"][name]["source_path"]
                if not Path(source_path).exists():
                    raise Failure(
                        f"warm path for {name!r} still points at a nonexistent path: {source_path!r} "
                        "(cross-installation stale-cache FileNotFoundError is NOT fixed)"
                    )
                # Actually read it, mirroring hydrate_agent_overlay's own read.
                Path(source_path).read_text(encoding="utf-8-sig")
        finally:
            if env_backup is None:
                os.environ.pop("AMPLIFIER_AGENT_HOME", None)
            else:
                os.environ["AMPLIFIER_AGENT_HOME"] = env_backup

    return f"warm path survives a relocated/deleted prior installation for all {len(real_names)} agents"


def main() -> int:
    print("verify-cache-restamp: exercising _restamp_agent_source_paths + load_and_prepare_cached")
    print(f"  repo root: {REPO_ROOT}\n")

    checks = [
        ("restamp a stale path", check_restamps_stale_path),
        ("leave an unresolvable agent alone", check_leaves_unknown_agent_alone),
        ("no-op without an agents section", check_noop_without_agents_section),
        ("end-to-end: warm path survives relocation", check_end_to_end_warm_path_survives_relocation),
    ]

    failures: list[tuple[str, str]] = []
    for label, fn in checks:
        try:
            detail = fn()
        except Failure as exc:
            print(f"  FAIL  {label}")
            failures.append((label, str(exc)))
        except Exception as exc:  # unexpected -- still report, don't crash silently
            print(f"  FAIL  {label} (unexpected {type(exc).__name__})")
            failures.append((label, str(exc)))
        else:
            print(f"  OK    {label}: {detail}")

    print()
    if failures:
        for label, message in failures:
            print(f"FAIL: {label}\n\n{message}\n", file=sys.stderr)
        print(
            f"verify-cache-restamp: FAIL -- {len(failures)} of {len(checks)} checks failed.",
            file=sys.stderr,
        )
        return 1

    print(f"verify-cache-restamp: PASS -- all {len(checks)} checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

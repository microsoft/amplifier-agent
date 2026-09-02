#!/usr/bin/env -S uv run python
"""verify-bundle-cache-staleness.py -- proves cortex-90ei's fix: a warm bundle-cache
hit whose baked-in agent ``source_path`` was resolved by a DIFFERENT installation
(e.g. this same uv tool venv, rebuilt onto a different Python minor version) is
detected as stale and rebuilt, instead of being returned as-is with an absolute path
that no longer exists for this process.

CONVENTION
    Same as verify-wheel.py: a standalone release/contract guard, run directly, not
    a pytest test -- see AGENTS.md's "Three tiers" (there is no unit test tier).

BACKGROUND
    ``amplifier_agent_lib/bundle/cache.py`` keys its warm cache purely on
    ``(aaa_version, sha256(bundle.md content))`` and pickles the resolved
    ``PreparedBundle`` -- including each agent's ABSOLUTE ``source_path`` -- into
    ``~/.amplifier-agent/cache/``, a single machine-global directory. That path is
    computed relative to wherever ``amplifier_agent_lib`` happened to be imported
    from at cache-write time. Nothing about the key changes when a *different*
    installation (a different venv, or the very same venv rebuilt onto a different
    Python minor version -- e.g. a uv tool venv going from 3.12 to 3.13) warm-hits
    the same entry, so the returned bundle's agent files can point at a
    ``lib/pythonX.Y/site-packages/...`` directory that does not exist under the
    CURRENT installation at all. This is exactly what took down every scheduled
    automation for the 08:37Z-11:02Z outage this item is named for: a
    ``FileNotFoundError`` for `.../amplifier_agent_lib/bundle/agents/explorer.md``
    under a `lib/python3.12` path, from a process that was actually running 3.13.

SIMULATION
    A real interpreter-version rebuild needs two real venvs, which this hermetic
    script does not have. Instead it fabricates the exact SHAPE of the bug: two
    fixture "installations" under a temp directory (.../lib/python3.12/... and
    .../lib/python3.13/...), each with its own copy of an agent .md. It seeds the
    cache as if written by the 3.12 fixture, then calls the real
    ``load_and_prepare_cached`` with ``load_and_prepare_bundle`` (the cold path)
    monkeypatched to return a fresh bundle backed by the 3.13 fixture. A correct
    fix must detect that the cached 3.12 path no longer resolves, invoke the cold
    path, and return the FRESH (3.13) bundle -- never the stale one, and never an
    unhandled exception. A second check proves the fix does not overcorrect: a
    warm entry whose path DOES still resolve must be returned without ever
    invoking the cold path.

    Only the cache module's own decision logic is exercised here -- not the real
    (heavy, network-touching) ``load_and_prepare_bundle`` cold path, which is
    already covered by ``tests/e2e/``. ``PreparedBundle``/``Bundle`` are stood in
    for by minimal picklable dataclasses; ``cache.py``'s staleness check only ever
    touches ``prepared.bundle.agents``, so nothing else about the real types is
    load-bearing for this check.

EXIT CODES
    0  staleness is detected and self-healed, and a healthy warm hit still skips
       the cold path
    1  a stale cache entry was returned as-is (regression), a healthy entry was
       needlessly rebuilt, or a check raised
"""

from __future__ import annotations

import asyncio
import json
import os
import pickle
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))


class Failure(Exception):
    """A verification check failed, with an actionable message."""


@dataclass
class _FakeBundle:
    """Minimal stand-in for amplifier_foundation.bundle.Bundle.

    ``cache.py``'s staleness check (``_stale_agent_source_paths``) only ever reads
    ``.agents``, so a full ``Bundle`` -- which drags in a real
    ``BundleModuleResolver`` to construct a genuine ``PreparedBundle`` -- is
    unnecessary machinery for exercising this one code path.
    """

    agents: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class _FakePreparedBundle:
    bundle: _FakeBundle
    marker: str  # lets a check tell "the stale fixture" from "the fresh fixture" apart


def _fixture_agent(root: Path, py_minor: str, agent_name: str) -> Path:
    """Fabricate a fake ``lib/python3.<minor>/site-packages/...`` tree with one
    vendored agent .md -- the exact shape ``loader.py`` bakes into ``source_path``.
    """
    agents_dir = root / f"lib/python3.{py_minor}/site-packages/amplifier_agent_lib/bundle/agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    agent_path = agents_dir / f"{agent_name}.md"
    agent_path.write_text(f"---\nname: {agent_name}\n---\nfixture agent under python3.{py_minor}\n")
    return agent_path


async def check_stale_cache_is_rebuilt(tmp_path: Path, bundle_cache: Any) -> str:
    """A warm entry whose source_path no longer exists must trigger the cold path."""
    agent_name = "explorer"
    aaa_version = "9.9.9-fixture-stale"

    old_install = tmp_path / "old-install"
    old_agent_path = _fixture_agent(old_install, "12", agent_name)
    new_agent_path = _fixture_agent(tmp_path / "new-install", "13", agent_name)

    # A venv rebuild replaces the whole tree -- lib/python3.12 does not linger
    # alongside lib/python3.13, it is GONE. Remove it now that we have captured
    # the absolute path it used to resolve to, so the fixture matches reality:
    # the cached path is a dangling reference, not a file that happens to exist.
    shutil.rmtree(old_install)

    # Seed a warm cache entry exactly as the cold path itself would have written
    # it, but backed by the OLD (3.12) fixture's (now-dangling) path -- simulating
    # a cache written before this venv was rebuilt onto 3.13.
    cache_dir = bundle_cache.cache_dir_for_version(aaa_version)
    cache_dir.mkdir(parents=True, exist_ok=True)
    stale = _FakePreparedBundle(
        bundle=_FakeBundle(agents={agent_name: {"source_path": str(old_agent_path)}}),
        marker="stale-3.12",
    )
    (cache_dir / bundle_cache._ARTIFACT_NAME).write_bytes(pickle.dumps(stale))
    (cache_dir / bundle_cache._MANIFEST_NAME).write_text(
        json.dumps({"aaa_version": aaa_version, "bundle_sha256_prefix": "fixture"})
    )

    # Now simulate this SAME (aaa_version, hash) key being warm-hit by the NEW
    # (3.13) installation. If it were still resolvable, the cold path must never
    # run -- so the fresh fixture proves whether it did.
    fresh = _FakePreparedBundle(
        bundle=_FakeBundle(agents={agent_name: {"source_path": str(new_agent_path)}}),
        marker="fresh-3.13",
    )

    async def _fake_cold_path() -> _FakePreparedBundle:
        return fresh

    with mock.patch.object(bundle_cache, "load_and_prepare_bundle", _fake_cold_path):
        result = await bundle_cache.load_and_prepare_cached(aaa_version)

    if result.marker == "stale-3.12":
        raise Failure(
            "load_and_prepare_cached returned the STALE cached bundle whose "
            f"source_path ({old_agent_path}) belongs to a different installation. "
            "The cache never should have been treated as a hit."
        )
    if result.marker != "fresh-3.13":
        raise Failure(f"load_and_prepare_cached returned neither fixture (marker={result.marker!r})")

    resolved = Path(result.bundle.agents[agent_name]["source_path"])
    if not resolved.exists():
        raise Failure(f"cold-path result's own source_path does not exist: {resolved}")

    # Self-healing must persist to disk, not just paper over it in memory: a
    # second warm read of the SAME cache_dir must now return the fresh fixture
    # too, never falling back to the stale one.
    reloaded = pickle.loads((cache_dir / bundle_cache._ARTIFACT_NAME).read_bytes())
    if reloaded.marker != "fresh-3.13":
        raise Failure(
            f"cache artifact on disk still carries marker={reloaded.marker!r} after "
            "the rebuild -- staleness was detected but never persisted."
        )

    return f"stale entry (dangling {old_agent_path}) was rebuilt fresh (-> {new_agent_path})"


async def check_healthy_cache_is_not_rebuilt(tmp_path: Path, bundle_cache: Any) -> str:
    """A warm entry whose source_path still resolves must NOT invoke the cold path."""
    agent_name = "explorer"
    aaa_version = "9.9.9-fixture-healthy"

    agent_path = _fixture_agent(tmp_path / "current-install", "13", agent_name)

    cache_dir = bundle_cache.cache_dir_for_version(aaa_version)
    cache_dir.mkdir(parents=True, exist_ok=True)
    healthy = _FakePreparedBundle(
        bundle=_FakeBundle(agents={agent_name: {"source_path": str(agent_path)}}),
        marker="healthy-warm-hit",
    )
    (cache_dir / bundle_cache._ARTIFACT_NAME).write_bytes(pickle.dumps(healthy))
    (cache_dir / bundle_cache._MANIFEST_NAME).write_text(
        json.dumps({"aaa_version": aaa_version, "bundle_sha256_prefix": "fixture"})
    )

    async def _cold_path_must_not_run() -> _FakePreparedBundle:
        raise Failure("the cold path ran for a healthy warm hit -- staleness check overcorrected")

    with mock.patch.object(bundle_cache, "load_and_prepare_bundle", _cold_path_must_not_run):
        result = await bundle_cache.load_and_prepare_cached(aaa_version)

    if result.marker != "healthy-warm-hit":
        raise Failure(f"expected the healthy warm-cached bundle back, got marker={result.marker!r}")

    return "healthy warm entry returned directly; cold path never invoked"


async def _amain() -> int:
    print("verify-bundle-cache-staleness: simulating a venv rebuilt onto a different Python minor version")

    with tempfile.TemporaryDirectory(prefix="aaa-cache-staleness-") as tmp:
        tmp_path = Path(tmp)

        # Route the machine-global cache root at a fixture directory for the
        # whole run -- this must never touch the real ~/.amplifier-agent/cache/.
        with mock.patch.dict(os.environ, {"AMPLIFIER_AGENT_HOME": str(tmp_path / "amplifier-agent-home")}):
            from amplifier_agent_lib.bundle import cache as bundle_cache

            checks = [
                ("stale cache entry is rebuilt", check_stale_cache_is_rebuilt),
                ("healthy cache entry is not rebuilt", check_healthy_cache_is_not_rebuilt),
            ]

            failures: list[tuple[str, str]] = []
            for label, fn in checks:
                try:
                    detail = await fn(tmp_path, bundle_cache)
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
            f"verify-bundle-cache-staleness: FAIL -- {len(failures)} of {len(checks)} checks failed.",
            file=sys.stderr,
        )
        return 1

    print(f"verify-bundle-cache-staleness: PASS -- all {len(checks)} checks passed.")
    return 0


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":
    raise SystemExit(main())

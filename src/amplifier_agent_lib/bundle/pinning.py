"""Late-binding commit resolution for floating module refs.

The problem this solves
-----------------------
``bundle.md`` declares every module at a floating ``@main``.  Foundation caches
each clone at ``<cache>/<repo>-<sha256(git_url@ref)[:16]>`` and, on a later
resolve, returns any directory that already exists and is structurally intact --
it never fetches into it, never compares refs, never checks the commit.

That behaviour is *correct reasoning on a false premise*.  Foundation is told
the identity of the content is ``main``, so it caches under that name.  But
``main`` is not an identity, it is a pointer: the hash never changes while the
commit it names does.  One directory therefore serves every commit that branch
will ever have, and in practice serves the first one forever.

Every previous remedy attacked the consequence -- delete the directory so the
next resolve has nothing to reuse.  This module attacks the premise instead.

What it does
------------
Before ``Bundle.prepare()`` runs, ask each remote what its branch currently
points at (``git ls-remote``, which transfers no repository data) and rewrite
``@main`` to that commit SHA.  Foundation then caches at
``sha256(git_url@<sha>)``, which *is* an identity:

- branch has not moved  -> same SHA -> same directory -> **nothing is downloaded**
- branch has moved      -> new SHA  -> new directory  -> cloned fresh, automatically
- offline / unreachable -> ref left as ``@main``       -> existing clone is reused

Nothing is ever deleted to make this work.  Directories become immutable and
content-addressed: one either is that commit or does not exist, so there is no
half-updated state to recover from.

Why this is not pinning
-----------------------
``bundle.md`` still says ``@main`` and is never rewritten in the repository.
The commit is resolved on the user's machine, against the branch as it exists
at that moment.  Ship a provider fix to ``main`` and the next resolution picks
it up -- no amplifier-agent release involved.  This deliberately keeps the
property recorded as a non-goal in ``docs/spec/bundle-and-cache.md``: module
sources are not pinned, and releases are not gated on module-repo state.

Foundation supports the rewritten form natively -- ``sources/git.py`` has a
dedicated ``_clone_at_commit()`` branch for full 40-character SHAs, distinct
from the ``--branch`` clone path used for named refs.

The seam
--------
``Bundle.prepare(source_resolver=...)`` is a documented extension point:
*"Optional callback (module_id, original_source) -> resolved_source.  Allows
app-layer source override policy to be applied before activation."*  Its return
value is written back into the module spec that foundation then resolves, so
the rewritten ref reaches the cache-key computation.  amplifier-app-cli already
uses this seam for its own settings overrides; amplifier-agent passed nothing.

Resolution happens in one parallel batch *before* ``prepare()`` rather than
inside the callback, because the callback is synchronous and is invoked once
per module -- doing network I/O there would serialise ~29 round trips.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import shutil
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "PinResult",
    "collect_module_sources",
    "find_drifted_modules",
    "prune_superseded_clones",
    "resolve_floating_refs",
]

#: A full 40-character hex commit SHA -- the form foundation's
#: ``_clone_at_commit()`` path recognises.  Sources already in this form are
#: left untouched: they are already content-addressed.
_FULL_SHA = re.compile(r"^[0-9a-fA-F]{40}$")

#: Per-remote timeout.  ``git ls-remote`` against GitHub completes in well
#: under a second; anything approaching this bound means the network is
#: degraded, and the fallback (leave the ref floating) is the right answer.
_LS_REMOTE_TIMEOUT_S = 10.0

#: Ceiling on the whole batch, so a pathological network cannot stall startup
#: for the sum of every per-remote timeout.
_BATCH_TIMEOUT_S = 30.0

#: Bound on concurrent git subprocesses.
_MAX_CONCURRENCY = 12

_GIT_PREFIX = "git+"

#: Metadata foundation writes beside each clone.  Records ``git_url``, ``ref``,
#: ``commit`` and ``cached_at``, which is what makes pruning possible without
#: any network access or bookkeeping of our own.
_CACHE_META_FILE = ".amplifier_cache_meta.json"


class PinResult:
    """Outcome of one resolution pass.

    Attributes:
        mapping: original source string -> rewritten source string.  Only
            contains entries that actually changed, so an empty mapping means
            "resolve nothing", not "resolution failed".
        resolved: number of sources successfully rewritten to a commit.
        skipped: number left floating (unreachable remote, timeout, already
            pinned, or a non-git source).
    """

    __slots__ = ("mapping", "pins", "resolved", "skipped")

    def __init__(
        self,
        mapping: dict[str, str],
        resolved: int,
        skipped: int,
        pins: dict[str, str] | None = None,
    ) -> None:
        self.mapping = mapping
        self.resolved = resolved
        self.skipped = skipped
        #: git_url -> commit SHA resolved this pass.  Drives pruning: a clone
        #: whose recorded url is a key here but whose recorded commit is not
        #: the value is a superseded generation.
        self.pins = pins or {}


def _split_source(source: str) -> tuple[str, str, str] | None:
    """Split ``git+<url>@<ref>#<fragment>`` into ``(url, ref, fragment)``.

    Returns ``None`` for anything that is not a git source, or that carries no
    explicit ref.  A source without a ref resolves to the remote's default
    branch, which is equally floating -- but rewriting it would require knowing
    which branch that is, and ``ls-remote HEAD`` reports the commit without
    naming the branch.  Left alone rather than guessed at; the sources this
    application ships all carry an explicit ref.

    The ``@`` is located by taking the last one and requiring no ``/`` after it,
    which distinguishes ``.../repo@main`` from a userinfo ``https://user@host/...``.
    """
    if not source.startswith(_GIT_PREFIX):
        return None

    body = source[len(_GIT_PREFIX) :]

    fragment = ""
    if "#" in body:
        body, fragment = body.split("#", 1)

    if "@" not in body:
        return None
    url, _, ref = body.rpartition("@")
    if not url or not ref or "/" in ref:
        return None

    return url, ref, fragment


def _rebuild_source(url: str, sha: str, fragment: str) -> str:
    """Reassemble a source string with *sha* substituted for the ref."""
    rebuilt = f"{_GIT_PREFIX}{url}@{sha}"
    if fragment:
        rebuilt = f"{rebuilt}#{fragment}"
    return rebuilt


def _read_cached_commits(clone_root: Path) -> dict[str, str]:
    """Return ``git_url -> newest cached commit`` from clone metadata on disk.

    Reads the ``.amplifier_cache_meta.json`` foundation writes beside every
    clone.  Purely local; no network, no bookkeeping of our own.
    """
    if not clone_root.is_dir():
        return {}
    try:
        children = sorted(clone_root.iterdir())
    except OSError:
        return {}

    newest: dict[str, tuple[str, str]] = {}  # url -> (cached_at, commit)
    for child in children:
        if not child.is_dir():
            continue
        try:
            meta = json.loads((child / _CACHE_META_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        url, commit, cached_at = meta.get("git_url"), meta.get("commit"), meta.get("cached_at", "")
        if not isinstance(url, str) or not isinstance(commit, str):
            continue
        stamp = cached_at if isinstance(cached_at, str) else ""
        if url not in newest or stamp > newest[url][0]:
            newest[url] = (stamp, commit)

    return {url: commit for url, (_stamp, commit) in newest.items()}


def collect_module_sources(mount_plan: dict[str, Any]) -> list[str]:
    """Return every module source string in *mount_plan*, de-duplicated.

    Walks exactly the shape ``Bundle.prepare()`` walks -- session orchestrator
    and context, the ``providers``/``tools``/``hooks`` lists, and the same four
    again nested under each entry in ``agents`` -- so that anything foundation
    will try to activate is offered for resolution.  A section foundation walks
    but this function misses would silently keep floating; the mirroring is
    deliberate and should be kept in step.
    """
    sources: list[str] = []
    seen: set[str] = set()

    def take(spec: Any) -> None:
        if isinstance(spec, dict):
            source = spec.get("source")
            if isinstance(source, str) and source not in seen:
                seen.add(source)
                sources.append(source)

    def take_sections(container: dict[str, Any]) -> None:
        session = container.get("session")
        if isinstance(session, dict):
            take(session.get("orchestrator"))
            take(session.get("context"))
        for section in ("providers", "tools", "hooks"):
            entries = container.get(section)
            if isinstance(entries, list):
                for entry in entries:
                    take(entry)

    take_sections(mount_plan)

    agents = mount_plan.get("agents")
    if isinstance(agents, dict):
        for agent_def in agents.values():
            if isinstance(agent_def, dict):
                take_sections(agent_def)

    return sources


async def _ls_remote(url: str, ref: str, semaphore: asyncio.Semaphore) -> str | None:
    """Return the commit *ref* currently points at on *url*, or ``None``.

    ``git ls-remote`` performs a refs-only exchange -- no objects, no working
    tree, no clone.  Measured at ~0.4s against GitHub over plain HTTPS with no
    authentication, which is what makes doing this for every module affordable.
    """
    async with semaphore:
        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "ls-remote",
                url,
                ref,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:  # git missing, fork failure
            logger.debug("ls-remote could not start for %s: %s", url, exc)
            return None

        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=_LS_REMOTE_TIMEOUT_S)
        except TimeoutError:
            proc.kill()
            # Reap the killed child so it does not linger as a zombie.
            with contextlib.suppress(Exception):
                await proc.wait()
            logger.debug("ls-remote timed out for %s@%s", url, ref)
            return None

        if proc.returncode != 0:
            logger.debug("ls-remote failed for %s@%s (rc=%s)", url, ref, proc.returncode)
            return None

    first = stdout.decode("utf-8", "replace").strip().splitlines()
    if not first:
        # Ref does not exist on the remote. Leaving it floating lets
        # foundation surface its own, clearer error.
        return None

    sha = first[0].split()[0].strip()
    return sha if _FULL_SHA.match(sha) else None


async def resolve_floating_refs(
    mount_plan: dict[str, Any],
    clone_root: Path | None = None,
) -> PinResult:
    """Resolve every floating module ref in *mount_plan* to a commit SHA.

    Never raises and never blocks indefinitely.  Degradation is per module, not
    all-or-nothing: one unreachable remote leaves that source alone while the
    rest still resolve.

    **Offline falls back to the commit already on disk, not to ``@main``.**
    Leaving the ref floating would be wrong once any SHA-keyed directory exists:
    ``@main`` and ``@<commit>`` hash to different cache keys, so falling back
    would point foundation at a directory that was never created and send it to
    clone -- precisely when the network is unavailable.  Pinning instead to the
    commit recorded in the local clone metadata reproduces the key of the
    directory that *is* there, so an offline run reuses it and succeeds.

    Args:
        mount_plan: Bundle mount plan, as returned by ``Bundle.to_mount_plan()``.
        clone_root: Foundation's clone directory, used for the offline fallback.
            When omitted, unresolvable sources are left floating.
    """
    sources = collect_module_sources(mount_plan)

    targets: list[tuple[str, str, str, str]] = []  # (source, url, ref, fragment)
    skipped = 0
    for source in sources:
        parts = _split_source(source)
        if parts is None:
            skipped += 1
            continue
        url, ref, fragment = parts
        if _FULL_SHA.match(ref):
            # Already content-addressed; nothing to gain.
            skipped += 1
            continue
        targets.append((source, url, ref, fragment))

    if not targets:
        return PinResult({}, resolved=0, skipped=skipped)

    cached = _read_cached_commits(clone_root) if clone_root is not None else {}

    semaphore = asyncio.Semaphore(_MAX_CONCURRENCY)
    tasks = [_ls_remote(url, ref, semaphore) for _, url, ref, _ in targets]

    try:
        shas = await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=_BATCH_TIMEOUT_S,
        )
    except TimeoutError:
        logger.debug("ref resolution batch exceeded %.0fs; falling back to cached commits", _BATCH_TIMEOUT_S)
        shas = [None] * len(targets)

    mapping: dict[str, str] = {}
    pins: dict[str, str] = {}
    for (source, url, _ref, fragment), sha in zip(targets, shas, strict=True):
        resolved_sha = sha if isinstance(sha, str) else None
        if resolved_sha is None:
            # Unreachable remote: hold the line at whatever is already cloned.
            fallback = cached.get(url)
            if fallback is None:
                skipped += 1
                continue
            logger.debug("using cached commit for %s (remote unreachable)", url)
            rebuilt = _rebuild_source(url, fallback, fragment)
            if rebuilt != source:
                mapping[source] = rebuilt
            skipped += 1
            continue

        pins[url] = resolved_sha
        rebuilt = _rebuild_source(url, resolved_sha, fragment)
        if rebuilt != source:
            mapping[source] = rebuilt

    return PinResult(mapping, resolved=len(mapping), skipped=skipped, pins=pins)


def prune_superseded_clones(pins: dict[str, str], clone_root: Path) -> int:
    """Remove clone directories left behind by an earlier commit of a pinned repo.

    Content-addressed directories are the price of never mutating a clone in
    place: each new commit produces a new directory and the previous one stops
    being referenced.  Without pruning, a frequently-updated module accumulates
    a directory per commit ever seen.

    This is disk reclamation on amplifier-agent's own tree, not a correctness
    mechanism -- the refresh works whether or not it runs.  It is therefore
    deliberately conservative:

    * only directories whose recorded ``git_url`` was resolved in *this* pass
      are considered, so an unrelated app's clone -- or one belonging to a
      module no longer in the bundle -- is never touched;
    * the directory holding the just-resolved commit is always kept;
    * failures are swallowed, since a directory that cannot be removed (open
      handle on Windows, permissions) is a tidiness problem and nothing more.

    Args:
        pins: ``git_url -> commit`` resolved this pass.
        clone_root: Foundation's clone directory for this application.

    Returns:
        Number of directories removed.
    """
    if not pins or not clone_root.is_dir():
        return 0

    removed = 0
    try:
        children = sorted(clone_root.iterdir())
    except OSError:
        return 0

    for child in children:
        if not child.is_dir():
            continue
        meta_path = child / _CACHE_META_FILE
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # No metadata means foundation did not write this, or wrote it
            # partially. Not ours to judge; leave it.
            continue
        url = meta.get("git_url")
        commit = meta.get("commit")
        if not isinstance(url, str) or not isinstance(commit, str):
            continue
        current = pins.get(url)
        if current is None or commit == current:
            continue
        shutil.rmtree(child, ignore_errors=True)
        if not child.exists():
            removed += 1
            logger.debug("pruned superseded clone %s (%s)", child.name, commit[:12])

    return removed


def find_drifted_modules(pins: dict[str, str], clone_root: Path) -> dict[str, tuple[str, str]]:
    """Return repositories whose branch has moved since their clone was taken.

    Compares freshly resolved commits against the ``commit`` foundation recorded
    in each clone's ``.amplifier_cache_meta.json``.  Entirely local: the network
    cost was already paid by :func:`resolve_floating_refs`, and this reads files
    that are already on disk.

    Lets ``amplifier-agent update`` answer "did anything actually change?"
    without downloading a byte.  A repository with no clone yet is not drift --
    there is nothing stale to report, and the next prepare will fetch it as a
    matter of course.

    Args:
        pins: ``git_url -> commit`` resolved this pass.
        clone_root: Foundation's clone directory for this application.

    Returns:
        ``git_url -> (cached_commit, current_commit)`` for repositories whose
        on-disk clone is behind the branch tip.
    """
    if not pins or not clone_root.is_dir():
        return {}

    try:
        children = sorted(clone_root.iterdir())
    except OSError:
        return {}

    # A repo can own several directories at once (one per commit seen). It has
    # drifted only if NONE of them holds the current commit.
    seen: dict[str, set[str]] = {}
    for child in children:
        if not child.is_dir():
            continue
        try:
            meta = json.loads((child / _CACHE_META_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        url = meta.get("git_url")
        commit = meta.get("commit")
        if isinstance(url, str) and isinstance(commit, str):
            seen.setdefault(url, set()).add(commit)

    drifted: dict[str, tuple[str, str]] = {}
    for url, current in pins.items():
        commits = seen.get(url)
        if not commits or current in commits:
            continue
        # Report the most recently cached commit as the "from" side.
        drifted[url] = (sorted(commits)[0], current)

    return drifted

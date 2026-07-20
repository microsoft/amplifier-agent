"""Shared skills/modes discovery — the single source of truth for both surfaces.

Both the CLI (``amplifier-agent skills list`` / ``modes list``) and the HTTP
routes (``GET /v1/skills`` / ``GET /v1/modes``) call the functions here so the
two surfaces enumerate byte-identical name sets. The e2e ``test_skills_parity``
/ ``test_modes_parity`` cases assert that CLI and HTTP agree, which only holds
if there is exactly one discovery implementation — this module.

Design notes
------------
* **Pure at import time.** Importing this module has no side effects; discovery
  work happens only when :func:`list_skills` / :func:`list_modes` are called.
* **Discovery modules live in the bundle, not the venv.** The upstream
  ``amplifier_module_tool_skills`` / ``amplifier_module_hooks_mode`` packages are
  git-cloned into the bundle cache and added to ``sys.path`` only when the
  bundle is *prepared*. A bare ``skills list`` process never boots a session, so
  we prepare the (cached) bundle once, lazily, to make the discovery imports
  resolvable — see :func:`_ensure_discovery_importable`.
* **"User-invocable" means slash-command, i.e. ``disable_model_invocation``.**
  The vendored council lens skills carry ``user-invocable: true`` but are
  *model*-invocable tools (no ``disable-model-invocation``), so filtering on
  ``user_invocable`` would wrongly surface all six lenses. The authoritative
  ecosystem classifier is the tool-skills visibility hook, which lists a skill
  under "User-invoked skills (available via /command)" iff
  ``disable_model_invocation`` is true. We match that predicate exactly, which
  yields precisely ``{code-review, council}`` for the built-ins.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
from pathlib import Path
from typing import Any

from amplifier_agent_lib import __version__
from amplifier_agent_lib.bundle import BUNDLE_DIR as _BUNDLE_PACKAGE_DIR

#: Directory containing the vendored built-in bundle resources (skills/, modes/).
#: Resolves correctly whether running from source or installed in a wheel because
#: it is derived from the ``amplifier_agent_lib.bundle`` package ``__file__``.
BUNDLE_DIR: Path = _BUNDLE_PACKAGE_DIR

#: Vendored built-in skill and mode directories.
_BUILTIN_SKILLS_DIR: Path = BUNDLE_DIR / "skills"
_BUILTIN_MODES_DIR: Path = BUNDLE_DIR / "modes"

#: Set once the discovery packages are importable, to avoid re-preparing.
_discovery_ready = False


def _add_module_paths_to_syspath(prepared: Any) -> None:
    """Add a prepared bundle's module source dirs to ``sys.path`` (best effort).

    ``bundle.prepare()`` normally mutates ``sys.path`` itself during module
    activation, but we also add the resolver's known module paths (and their
    parents) defensively so the discovery imports resolve on every cache path.
    """
    import sys

    candidates: list[str] = []
    for p in getattr(prepared, "bundle_package_paths", None) or []:
        candidates.append(str(p))
    resolver_paths = getattr(getattr(prepared, "resolver", None), "_paths", None)
    if isinstance(resolver_paths, dict):
        for value in resolver_paths.values():
            if value is None:
                continue
            path = Path(value)
            candidates.append(str(path))
            candidates.append(str(path.parent))
    for entry in candidates:
        if entry not in sys.path:
            sys.path.insert(0, entry)


def _ensure_discovery_importable() -> None:
    """Make the upstream discovery packages importable (lazy, idempotent).

    Fast path: if they already import (e.g. the HTTP lifespan already prepared
    the bundle), do nothing. Otherwise prepare the cached bundle to populate
    ``sys.path``. Preparing does not boot a session — it only resolves + caches
    the bundle and exposes its module packages.

    Raises:
        RuntimeError: If the packages are still not importable and we cannot
            prepare because an event loop is already running (the async caller
            must prepare the bundle before calling into discovery).
    """
    global _discovery_ready
    if _discovery_ready:
        return
    try:
        import amplifier_module_hooks_mode  # noqa: F401
        import amplifier_module_tool_skills.discovery  # noqa: F401

        _discovery_ready = True
        return
    except ImportError:
        pass

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        running_loop = False
    else:
        running_loop = True
    if running_loop:
        raise RuntimeError(
            "Skills/modes discovery packages are not importable and cannot be "
            "prepared from within a running event loop. Prepare the bundle "
            "(load_and_prepare_cached) before calling list_skills/list_modes."
        )

    from amplifier_agent_lib.bundle.cache import load_and_prepare_cached

    # Suppress any stray stdout from prepare so callers keep a clean JSON stdout.
    with contextlib.redirect_stdout(io.StringIO()):
        prepared = asyncio.run(load_and_prepare_cached(aaa_version=__version__))
    _add_module_paths_to_syspath(prepared)
    _discovery_ready = True


def _config_skill_dirs(config: dict[str, Any] | None) -> list[Path]:
    """Extract existing local skill dirs from ``config["skills"]["skills"]``.

    That config value is a list of source URIs (git URLs, workspace-relative
    paths, or home paths). Only local, existing directories are usable as
    discovery roots here; git URLs and non-existent paths are skipped.
    """
    if not isinstance(config, dict):
        return []
    skills_block = config.get("skills")
    if not isinstance(skills_block, dict):
        return []
    entries = skills_block.get("skills")
    if not isinstance(entries, list):
        return []
    dirs: list[Path] = []
    for entry in entries:
        if not isinstance(entry, str) or not entry:
            continue
        if entry.startswith("git+") or "://" in entry:
            continue
        path = Path(entry).expanduser()
        if path.is_dir():
            dirs.append(path)
    return dirs


def list_skills(config: dict[str, Any] | None = None) -> list[dict[str, str]]:
    """Return the user-invocable (slash-command) skills as ``{name, description}``.

    Discovery roots, in priority order (first match wins on name collisions):
    the vendored built-in skills dir, the tool-skills default dirs
    (``.amplifier/skills``, ``~/.amplifier/skills``), then any local dirs named
    in ``config["skills"]["skills"]``.

    A skill is included iff ``disable_model_invocation`` is true — the
    slash-command predicate the tool-skills visibility hook uses. This yields
    exactly ``{code-review, council}`` for the built-ins (the six council lens
    skills are model-invocable and are excluded).

    Args:
        config: Optional host config dict (the parsed ``--config`` JSON).

    Returns:
        List of ``{"name", "description"}`` dicts, sorted by name.
    """
    _ensure_discovery_importable()
    from amplifier_module_tool_skills.discovery import (
        discover_skills_multi_source,
        get_default_skills_dirs,
    )

    dirs: list[Path] = [_BUILTIN_SKILLS_DIR]
    dirs.extend(Path(d) for d in get_default_skills_dirs())
    dirs.extend(_config_skill_dirs(config))

    # discover_skills_multi_source dedups by name, first-match-wins, with the
    # built-in dir first — so a built-in always shadows a same-named override.
    discovered = discover_skills_multi_source(dirs)

    result = [
        {"name": meta.name, "description": meta.description or ""}
        for meta in discovered.values()
        if getattr(meta, "disable_model_invocation", False)
    ]
    result.sort(key=lambda item: item["name"])
    return result


def list_modes(config: dict[str, Any] | None = None) -> list[dict[str, str]]:
    """Return all shipped modes as ``{name, description}``.

    Search paths, in priority order: the vendored built-in modes dir, the
    launch-dir ``.amplifier/modes``, then ``~/.amplifier/modes``. No
    user-invocable filter applies — every discovered mode is listed. For the
    built-ins this yields exactly ``{plan, brainstorm}``.

    Args:
        config: Accepted for symmetry with :func:`list_skills`; currently unused
            (mode search paths are conventional, not config-driven).

    Returns:
        List of ``{"name", "description"}`` dicts, sorted by name.
    """
    _ensure_discovery_importable()
    from amplifier_module_hooks_mode import ModeDiscovery

    search_paths: list[Path] = [
        _BUILTIN_MODES_DIR,
        Path.cwd() / ".amplifier" / "modes",
        Path("~/.amplifier/modes").expanduser(),
    ]
    listings = ModeDiscovery(search_paths=search_paths).list_modes()

    seen: set[str] = set()
    result: list[dict[str, str]] = []
    for listing in listings:
        name = getattr(listing, "name", None)
        if not name or name in seen:
            continue
        seen.add(name)
        result.append({"name": name, "description": getattr(listing, "description", "") or ""})
    result.sort(key=lambda item: item["name"])
    return result

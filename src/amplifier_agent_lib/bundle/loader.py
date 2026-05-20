"""Bundle loader — cold path for turning bundle.md into a PreparedBundle.

This module is the single entry point that loads and prepares the vendored
bundle.md (or an override path for dev/testing).  It does NOT cache; caching
lives in bundle/cache.py.

Per the D4 design decision the vendored bundle is *sealed*: production callers
always pass ``override_path=None`` so they get the vendored copy.  The
``override_path`` parameter exists exclusively for dev/testing.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from amplifier_agent_lib.bundle import BUNDLE_MD

if TYPE_CHECKING:
    from amplifier_foundation.bundle._prepared import PreparedBundle


async def load_and_prepare_bundle(
    override_path: Path | None = None,
    install_deps: bool = True,
) -> PreparedBundle:
    """Load and prepare the vendored bundle.md (or an override) via amplifier-foundation.

    This is the cold path — it always resolves and prepares the bundle from
    scratch.  Caching is the caller's responsibility (see bundle/cache.py).

    Args:
        override_path: If provided, load this path instead of the vendored
            ``BUNDLE_MD``.  For dev/testing only; production callers must leave
            this as ``None`` so the sealed vendored bundle is used.
        install_deps: Whether to install Python dependencies for each module
            declared in the bundle.  Pass ``False`` in unit tests to skip
            network access and speed up the test suite.

    Returns:
        A :class:`~amplifier_foundation.bundle._prepared.PreparedBundle`
        ready for session creation.

    Raises:
        FileNotFoundError: If the resolved target path does not exist on disk.
    """
    from amplifier_foundation import load_bundle

    target: Path = override_path if override_path is not None else BUNDLE_MD

    if not target.exists():
        raise FileNotFoundError(f"Bundle file not found: {target}")

    bundle = await load_bundle(f"file://{target}")

    # Activate agent-side tool sources at compose time.  Without this call,
    # foundation's prepare() only installs the modules declared at the bundle's
    # top level — agent frontmatter ``tools:`` blocks (tool-bash, tool-filesystem,
    # tool-search, etc.) are invisible to the install loop, and child sessions
    # try to mount those tools from an empty resolver and silently fail.
    #
    # ``load_agent_metadata()`` reads each agent's ``.md`` file via
    # ``resolve_agent_path()`` (which uses ``bundle.base_path`` set by
    # ``load_bundle()``) and merges the frontmatter (tools, providers, hooks,
    # session, description, instruction) into ``bundle.agents[name]`` in-place.
    # ``Bundle.prepare()`` then walks ``mount_plan["agents"]`` and pre-activates
    # every module listed there, so the BundleModuleResolver child sessions
    # inherit already knows about tool-bash, tool-filesystem, etc.
    #
    # The method is synchronous (no ``await``).  It swallows per-agent parse
    # errors as ``logger.warning()`` so a single malformed ``.md`` doesn't abort
    # the whole cold-prepare.  Any unexpected top-level failure propagates here
    # with a clear message so the operator knows which step failed.
    #
    # Mirrors the upstream fix in
    # amplifier_app_cli/lib/bundle_loader/prepare.py:190.
    try:
        bundle.load_agent_metadata()
    except Exception as exc:  # pragma: no cover — only hits on unexpected foundation bugs
        raise RuntimeError(
            f"bundle.load_agent_metadata() failed while loading agent frontmatter "
            f"from '{target.parent / 'agents'}'. "
            f"Check that every vendored agent .md has valid YAML frontmatter. "
            f"Underlying error: {exc}"
        ) from exc

    # Enrich each declared agent with its resolved absolute source_path so
    # callers (and tests) can locate the vendored .md files without going
    # through foundation's internal resolver.  We add source_path only when
    # the file can actually be found; agents without a resolvable path are
    # left unchanged.
    for agent_name in bundle.agents:
        agent_path = bundle.resolve_agent_path(agent_name)
        if agent_path and agent_path.exists():
            bundle.agents[agent_name]["source_path"] = str(agent_path)

    prepared = await bundle.prepare(install_deps=install_deps)
    return prepared

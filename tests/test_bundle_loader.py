"""Integration tests for bundle/loader.py.

These tests exercise the REAL amplifier_foundation.load_bundle() + Bundle.prepare()
path. They are integration tests — expect seconds (cold), but never minutes.

No mocking of amplifier-foundation inside this test file.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_load_and_prepare_returns_prepared_bundle() -> None:
    """load_and_prepare_bundle() returns a non-None PreparedBundle with a mount_plan dict."""
    from amplifier_agent_lib.bundle.loader import load_and_prepare_bundle

    prepared = await load_and_prepare_bundle(install_deps=False)

    assert prepared is not None
    assert hasattr(prepared, "mount_plan")
    assert isinstance(prepared.mount_plan, dict)


@pytest.mark.asyncio
async def test_prepared_bundle_declares_context_simple() -> None:
    """Vendored bundle.md currently declares context-simple as the session context module.

    NOTE: An A7-era task intended to swap this to context-persistent (the SC-4
    contingency), but the SessionStore + IncrementalSaveHook architecture was found
    not to replay transcripts correctly (test_resume_continuity_two_turns_share_context
    fails). The swap is deferred pending Issue 2 investigation. This test guards the
    CURRENT state (context-simple) so any inadvertent bundle.md change is caught.
    """
    from amplifier_agent_lib.bundle.loader import load_and_prepare_bundle

    prepared = await load_and_prepare_bundle(install_deps=False)

    assert prepared.mount_plan["session"]["context"]["module"] == "context-simple"


@pytest.mark.asyncio
async def test_load_and_prepare_accepts_override_path(tmp_path: Path) -> None:
    """override_path parameter loads an alternate bundle instead of the vendored one."""
    from amplifier_agent_lib.bundle.loader import load_and_prepare_bundle

    # Write a minimal alt bundle.md with a distinct name
    override = tmp_path / "bundle.md"
    override.write_text("---\nbundle:\n  name: alt-bundle\n  version: 1.0.0\n---\n# Alt bundle\n")

    prepared = await load_and_prepare_bundle(override_path=override, install_deps=False)

    # Verify the correct bundle was loaded (checking the Bundle object, not mount_plan,
    # since to_mount_plan() does not include bundle metadata at the top level)
    assert prepared.bundle.name == "alt-bundle"


VENDORED_AGENT_NAMES: set[str] = {
    "explorer",
    "architect",
    "builder",
    "debugger",
    "git-ops",
    "researcher",
}
VENDORED_AGENT_FILES: set[str] = {f"{n}.md" for n in VENDORED_AGENT_NAMES}


def test_agents_dir_exposes_vendored_agents() -> None:
    """AGENTS_DIR points at the bundle/agents/ directory containing the vendored agents."""
    from amplifier_agent_lib.bundle import AGENTS_DIR

    assert AGENTS_DIR.is_dir(), f"AGENTS_DIR does not exist: {AGENTS_DIR}"
    actual_names = {p.name for p in AGENTS_DIR.iterdir() if p.suffix == ".md"}
    missing = VENDORED_AGENT_FILES - actual_names
    assert not missing, f"AGENTS_DIR missing vendored agents: {missing}"


@pytest.mark.asyncio
async def test_vendored_bundle_has_no_includes_block() -> None:
    """Vendored bundle.md must not contain `includes:` — the resolver does not handle named-bundle URIs.

    Regression guard for `No handler for URI: build-up-foundation`. Per the Strategy 1
    decision in docs/designs/2026-05-19-baked-in-bundle-decision.md, the manifest is now
    self-describing with explicit modules + agents and no foundation include.
    """
    from amplifier_agent_lib.bundle import BUNDLE_MD

    content = BUNDLE_MD.read_text()
    assert "\nincludes:" not in content, "bundle.md must not contain a top-level `includes:` block"


@pytest.mark.asyncio
async def test_vendored_bundle_declares_all_agents() -> None:
    """Vendored bundle.md declares the behavioral-anchor agent set via the agents: block."""
    from amplifier_agent_lib.bundle.loader import load_and_prepare_bundle

    prepared = await load_and_prepare_bundle(install_deps=False)

    # agents is dict[str, dict[str, Any]] — keys are agent names.
    agent_names = set(prepared.bundle.agents)
    assert agent_names >= VENDORED_AGENT_NAMES, (
        f"Expected all vendored agents; got {sorted(agent_names)}"
    )


@pytest.mark.asyncio
async def test_vendored_agents_resolve_to_wheel_local_files() -> None:
    """Each agent in the agents: block resolves to a file under the bundle/agents/ directory."""
    from amplifier_agent_lib.bundle import AGENTS_DIR
    from amplifier_agent_lib.bundle.loader import load_and_prepare_bundle

    prepared = await load_and_prepare_bundle(install_deps=False)

    for agent_name, agent_def in prepared.bundle.agents.items():
        # Foundation should have resolved each agent ref to an absolute Path under AGENTS_DIR.
        # agents is dict[str, dict[str, Any]]; the definition dict carries `source_path`.
        resolved: Path = Path(agent_def["source_path"])
        assert resolved.is_file(), f"Agent {agent_name} did not resolve to a file: {resolved}"
        assert AGENTS_DIR in resolved.parents or resolved.parent == AGENTS_DIR, (
            f"Agent {agent_name} resolved outside the vendored AGENTS_DIR: {resolved}"
        )


@pytest.mark.asyncio
async def test_explorer_agent_definition_resolves_after_prepare() -> None:
    """load_and_prepare_bundle() must produce a hydrated explorer agent definition.

    Behavioral-anchor agents do NOT declare their own tools blocks -- tools come
    from the parent bundle and are inherited via tool-delegate's
    context_inheritance.enabled: true. This test guards that the agent definition
    is at least loaded into the mount_plan after prepare() so the spawner can
    find it.
    """
    from amplifier_agent_lib.bundle.loader import load_and_prepare_bundle

    prepared = await load_and_prepare_bundle(install_deps=False)

    explorer_def = prepared.mount_plan.get("agents", {}).get("explorer")
    assert explorer_def is not None, (
        "explorer is missing from mount_plan['agents'] after prepare(). "
        "Check that load_agent_metadata() runs before prepare() in "
        "amplifier_agent_lib/bundle/loader.py."
    )


def test_bundle_module_sources_use_main_not_version_tags() -> None:
    """All git+ source URLs in bundle.md must NOT use semver tag refs like @v0.1.0.

    Defense-in-depth: the foundation modules (context-simple, tool-mcp,
    hooks-approval, etc.) are developed on `main` and do not publish git tags.
    Using a tag ref that does not exist causes a silent activation failure at
    runtime.  The convention is `@main` for all source URLs in this bundle.

    Regression guard for: hooks-approval was briefly pinned to @v0.1.0 (the
    README's package-version label) instead of @main, causing every test run
    to fail bundle prep silently.
    """
    import re

    from amplifier_agent_lib.bundle import BUNDLE_MD

    content = BUNDLE_MD.read_text(encoding="utf-8")
    # Match source lines: git+https://...@<ref>
    tag_pattern = re.compile(r"git\+https://[^\s]+@v\d+\.\d+\.\d+")
    offending = tag_pattern.findall(content)
    assert not offending, (
        "bundle.md contains git+ source URLs with semver tag refs that may not "
        "exist on the upstream repo.  Use @main instead:\n" + "\n".join(f"  {url}" for url in offending)
    )


def test_agents_dir_resolves_in_editable_install() -> None:
    """AGENTS_DIR (used at runtime by the manifest's file:// agent refs) must contain real files.

    Regression guard: if a future refactor accidentally moves AGENTS_DIR or the
    agent files diverge from it, this test will catch it before the manifest fails
    at first-run with FileNotFoundError.
    """
    from amplifier_agent_lib.bundle import AGENTS_DIR

    actual_files = {p.name: p for p in AGENTS_DIR.iterdir() if p.suffix == ".md"}

    assert VENDORED_AGENT_FILES <= set(actual_files), (
        f"missing: {VENDORED_AGENT_FILES - set(actual_files)}"
    )
    for name, path in actual_files.items():
        if name in VENDORED_AGENT_FILES:
            assert path.stat().st_size > 100, f"{name} is suspiciously small ({path.stat().st_size} bytes)"
            assert path.read_text().startswith("---\n"), f"{name} missing YAML frontmatter"

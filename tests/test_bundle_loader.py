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
    """Vendored bundle.md declares context-simple as the session context module.

    Updated from context-persistent on 2026-05-19 (Thread 1 fix). context-persistent's
    own README explicitly says 'No auto-save: Does not persist context back to files' —
    it loads memory files at session start, it does not own transcript persistence.
    Transcript persistence is a planned CLI-layer hook concern (see
    docs/designs/2026-05-19-baked-in-bundle-revisit.md).
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

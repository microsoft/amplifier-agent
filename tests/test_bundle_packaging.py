"""Tests to verify the built-in bundle.md is properly packaged and contains required content."""

import importlib.resources


def test_bundle_md_is_packaged():
    """Verify bundle.md is a file in the amplifier_agent_lib.bundle package data."""
    pkg = importlib.resources.files("amplifier_agent_lib.bundle")
    bundle_md = pkg / "bundle.md"
    # Must be accessible as a package resource (i.e., a real file in the package)
    assert bundle_md.is_file(), "bundle.md must be a file in amplifier_agent_lib.bundle package data"


def test_bundle_md_has_yaml_frontmatter():
    """Verify bundle.md starts with YAML frontmatter delimiters."""
    pkg = importlib.resources.files("amplifier_agent_lib.bundle")
    bundle_md = pkg / "bundle.md"
    content = bundle_md.read_text(encoding="utf-8")
    assert content.startswith("---\n"), "bundle.md must start with '---\\n' (YAML frontmatter)"
    assert "\n---\n" in content, "bundle.md must contain '\\n---\\n' to close the YAML frontmatter"


def test_bundle_md_declares_name_and_includes_foundation():
    """Verify bundle.md declares the bundle name and includes the amplifier-foundation bundle."""
    pkg = importlib.resources.files("amplifier_agent_lib.bundle")
    bundle_md = pkg / "bundle.md"
    content = bundle_md.read_text(encoding="utf-8")
    assert "amplifier-agent-builtin" in content, "bundle.md must declare name 'amplifier-agent-builtin'"
    assert "build-up-foundation" in content or "amplifier-foundation" in content, (
        "bundle.md must include 'build-up-foundation' or reference 'amplifier-foundation'"
    )

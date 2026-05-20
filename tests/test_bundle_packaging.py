"""Tests to verify the built-in bundle.md is properly packaged and contains required content.

Also includes a packaging regression test — the built wheel must contain the four vendored
agent files (Strategy 1 of docs/designs/2026-05-19-baked-in-bundle-decision.md).
"""

from __future__ import annotations

import importlib.resources
import subprocess
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


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


def test_bundle_md_declares_name_and_references_modules():
    """Verify bundle.md declares the bundle name and references at least one amplifier module."""
    pkg = importlib.resources.files("amplifier_agent_lib.bundle")
    bundle_md = pkg / "bundle.md"
    content = bundle_md.read_text(encoding="utf-8")
    assert "amplifier-agent-builtin" in content, "bundle.md must declare name 'amplifier-agent-builtin'"
    assert "github.com/microsoft/amplifier-module-" in content, (
        "bundle.md must reference at least one microsoft/amplifier-module by git URL"
    )


@pytest.mark.integration
def test_built_wheel_contains_all_four_vendored_agents(tmp_path: Path) -> None:
    """Build the wheel and assert the four agent markdown files are inside it."""
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path)],
        cwd=REPO_ROOT,
        check=True,
    )

    wheels = list(tmp_path.glob("amplifier_agent-*.whl"))
    assert len(wheels) == 1, f"expected exactly one wheel, found {wheels}"

    with zipfile.ZipFile(wheels[0]) as zf:
        names = set(zf.namelist())

    expected = {
        "amplifier_agent_lib/bundle/bundle.md",
        "amplifier_agent_lib/bundle/agents/explorer.md",
        "amplifier_agent_lib/bundle/agents/planner.md",
        "amplifier_agent_lib/bundle/agents/coder.md",
        "amplifier_agent_lib/bundle/agents/tester.md",
    }
    missing = expected - names
    assert not missing, f"wheel missing files: {sorted(missing)}"

"""Regression-anchor tests for tool-skills declaration in vendored bundle.md.

These tests pin the contract that the vendored bundle ships `tool-skills`
sourced from the upstream `amplifier-bundle-skills` repo, with the
project-default skills sources and visibility config baked in.

References:
- D11 (decision: bundle ships tool-skills with defaults out of the box)
- Parent plan Phase 4.1 (re-land bundle.md edit after the prior session's
  rollback).

Initial state of this file is intentional: it is written as a RED-phase
regression anchor. Running these tests against a `bundle.md` that does not
yet declare `tool-skills` will produce 4 failures (StopIteration on the
`next(...)` lookups for the entry, plus the membership check). Phase 4.1
of the parent plan lands the bundle.md edit that turns them GREEN.
"""

from __future__ import annotations

from importlib.resources import files

import yaml


def _load_manifest() -> dict:
    """Load the YAML frontmatter from the vendored bundle.md.

    The vendored manifest is delivered inside the wheel at
    `amplifier_agent_lib.bundle/bundle.md`. The file is a Markdown document
    with YAML frontmatter delimited by `---\\n`. Splitting on that
    delimiter yields ['', <yaml-text>, <markdown-body>...], so the
    frontmatter is element [1].
    """
    bundle_md_text = files("amplifier_agent_lib.bundle").joinpath("bundle.md").read_text(encoding="utf-8")
    return yaml.safe_load(bundle_md_text.split("---\n")[1])


def test_bundle_declares_tool_skills() -> None:
    """bundle.md must list tool-skills among its top-level tools."""
    manifest = _load_manifest()
    modules = [t["module"] for t in manifest["tools"]]
    assert "tool-skills" in modules


def test_tool_skills_source_points_at_bundle_skills_repo() -> None:
    """tool-skills must be sourced from the amplifier-bundle-skills subdir."""
    manifest = _load_manifest()
    entry = next(t for t in manifest["tools"] if t["module"] == "tool-skills")
    assert (
        entry["source"]
        == "git+https://github.com/microsoft/amplifier-bundle-skills@main#subdirectory=modules/tool-skills"
    )


def test_tool_skills_ships_default_skills_sources() -> None:
    """tool-skills config.skills must point at foundation's skills set.

    Behavioral-anchor convention: discovery available, but only the canonical
    foundation skills are pre-wired. Users can add `.amplifier/skills` and
    `~/.amplifier/skills` via their host config if desired.
    """
    manifest = _load_manifest()
    entry = next(t for t in manifest["tools"] if t["module"] == "tool-skills")
    skills = entry["config"]["skills"]
    assert isinstance(skills, list)
    assert "git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=skills" in skills


def test_tool_skills_ships_default_visibility() -> None:
    """tool-skills config.visibility must equal the behavioral-anchor default.

    Visibility is disabled by default in behavioral-anchor to save tokens. Hosts
    that want skills auto-injection can override via host config.
    """
    manifest = _load_manifest()
    entry = next(t for t in manifest["tools"] if t["module"] == "tool-skills")
    assert entry["config"]["visibility"] == {"enabled": False}

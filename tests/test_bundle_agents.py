"""Tests verifying the vendored sub-session agent .md files are well-formed.

Behavioral-anchor agents are intentionally lean: each declares only `meta.name`,
`meta.description`, and `model_role` in its frontmatter, with a short prose body.
Tools are NOT declared per-agent -- agents inherit the parent's tool roster via
`tool-delegate`'s `context_inheritance.enabled: true` (see bundle.md `tools:`).
"""

from __future__ import annotations

from pathlib import Path

import pytest

# The agents shipped by this bundle. Keep in sync with bundle.md `agents.include`
# and pyproject.toml `force-include`.
VENDORED_AGENTS: tuple[str, ...] = (
    "explorer",
    "architect",
    "builder",
    "debugger",
    "git-ops",
    "researcher",
)

# Resolve the source-tree agents directory directly. Using __file__ avoids the
# importlib.resources MultiplexedPath quirk for namespace packages (agents/ has
# no __init__.py).
AGENTS_DIR: Path = (
    Path(__file__).parent.parent
    / "src"
    / "amplifier_agent_lib"
    / "bundle"
    / "agents"
)


def _agents_pkg() -> Path:
    """Return Path to the bundle/agents/ directory."""
    return AGENTS_DIR


def test_agents_dir_lists_exactly_the_vendored_agents():
    """bundle/agents/ contains exactly the .md files for the declared agent set."""
    actual = {p.stem for p in _agents_pkg().iterdir() if p.suffix == ".md"}
    assert actual == set(VENDORED_AGENTS), (
        f"agents/ directory drift: expected {set(VENDORED_AGENTS)}, found {actual}"
    )


@pytest.mark.parametrize("agent_name", VENDORED_AGENTS)
def test_agent_md_is_packaged(agent_name: str):
    """Each agent .md is present as a package resource."""
    agent_md = _agents_pkg() / f"{agent_name}.md"
    assert agent_md.is_file(), f"{agent_name}.md must be a file in bundle/agents/"


@pytest.mark.parametrize("agent_name", VENDORED_AGENTS)
def test_agent_md_has_yaml_frontmatter(agent_name: str):
    """Each agent .md starts with YAML frontmatter delimiters."""
    content = (_agents_pkg() / f"{agent_name}.md").read_text(encoding="utf-8")
    assert content.startswith("---\n"), f"{agent_name}.md must start with '---\\n'"
    assert "\n---\n" in content, f"{agent_name}.md must close its YAML frontmatter"


@pytest.mark.parametrize("agent_name", VENDORED_AGENTS)
def test_agent_md_declares_matching_meta_name(agent_name: str):
    """meta.name in frontmatter matches the filename stem."""
    content = (_agents_pkg() / f"{agent_name}.md").read_text(encoding="utf-8")
    assert f"name: {agent_name}" in content, (
        f"{agent_name}.md must declare meta.name: {agent_name}"
    )


@pytest.mark.parametrize("agent_name", VENDORED_AGENTS)
def test_agent_md_declares_model_role(agent_name: str):
    """Each agent declares a model_role (single value or fallback chain)."""
    content = (_agents_pkg() / f"{agent_name}.md").read_text(encoding="utf-8")
    assert "model_role:" in content, f"{agent_name}.md must declare model_role"


@pytest.mark.parametrize("agent_name", VENDORED_AGENTS)
def test_agent_md_has_no_tools_block(agent_name: str):
    """Behavioral-anchor agents do NOT declare their own tools.

    Tools are declared at the parent (bundle) level and inherited by sub-agents
    via tool-delegate's context_inheritance.enabled: true. This test guards against
    accidentally re-introducing per-agent tools blocks (the old AAA pattern).
    """
    content = (_agents_pkg() / f"{agent_name}.md").read_text(encoding="utf-8")
    # Look for a frontmatter top-level `tools:` key. The frontmatter ends at the
    # second '---' delimiter; check only inside that region.
    fm_close = content.index("\n---\n", 4)
    frontmatter = content[:fm_close]
    # A line starting with "tools:" (no leading whitespace) is the top-level key.
    for line in frontmatter.splitlines():
        assert not line.startswith("tools:"), (
            f"{agent_name}.md must NOT declare its own tools block -- tools are inherited from the parent bundle"
        )


@pytest.mark.parametrize("agent_name", VENDORED_AGENTS)
def test_agent_md_body_has_heading(agent_name: str):
    """Each agent has a non-empty markdown body with at least one '#' heading."""
    content = (_agents_pkg() / f"{agent_name}.md").read_text(encoding="utf-8")
    fm_close = content.index("\n---\n", 4)
    body = content[fm_close + 5 :].strip()
    assert body, f"{agent_name}.md must have a non-empty body"
    assert any(line.startswith("# ") for line in body.splitlines()), (
        f"{agent_name}.md body must include at least one top-level '#' heading"
    )


@pytest.mark.parametrize("agent_name", VENDORED_AGENTS)
def test_agent_md_description_has_use_when_guidance(agent_name: str):
    """Each agent's description provides USE WHEN / DO NOT USE WHEN routing guidance.

    This is the behavioral-anchor convention -- routing guidance lives in the
    meta.description so the parent orchestrator knows when to delegate.
    """
    content = (_agents_pkg() / f"{agent_name}.md").read_text(encoding="utf-8")
    assert "USE WHEN" in content, f"{agent_name}.md description must include 'USE WHEN'"
    assert "DO NOT USE WHEN" in content, (
        f"{agent_name}.md description must include 'DO NOT USE WHEN'"
    )

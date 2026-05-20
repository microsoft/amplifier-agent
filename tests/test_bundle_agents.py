"""Tests to verify vendored agent markdown files under bundle/agents/ are packaged correctly."""

import importlib.resources


def _agents_pkg():
    return importlib.resources.files("amplifier_agent_lib.bundle") / "agents"


def test_explorer_md_is_packaged():
    """Verify explorer.md is present as a package resource in bundle/agents/."""
    explorer_md = _agents_pkg() / "explorer.md"
    assert explorer_md.is_file(), "explorer.md must be a file in amplifier_agent_lib.bundle.agents package data"


def test_explorer_md_has_yaml_frontmatter():
    """Verify explorer.md starts with YAML frontmatter delimiters."""
    explorer_md = _agents_pkg() / "explorer.md"
    content = explorer_md.read_text(encoding="utf-8")
    assert content.startswith("---\n"), "explorer.md must start with '---\\n' (YAML frontmatter)"
    assert "\n---\n" in content, "explorer.md must contain '\\n---\\n' to close YAML frontmatter"


def test_explorer_md_meta_name():
    """Verify explorer.md declares meta.name: explorer."""
    explorer_md = _agents_pkg() / "explorer.md"
    content = explorer_md.read_text(encoding="utf-8")
    assert "name: explorer" in content, "explorer.md must declare meta.name: explorer"


def test_explorer_md_model_role():
    """Verify explorer.md declares model_role with research and general."""
    explorer_md = _agents_pkg() / "explorer.md"
    content = explorer_md.read_text(encoding="utf-8")
    assert "model_role:" in content, "explorer.md must have model_role"
    assert "research" in content, "explorer.md model_role must include 'research'"
    assert "general" in content, "explorer.md model_role must include 'general'"


def test_explorer_md_tools_include_tool_delegate():
    """Verify explorer.md includes tool-delegate with exclude_tools: [tool-delegate]."""
    explorer_md = _agents_pkg() / "explorer.md"
    content = explorer_md.read_text(encoding="utf-8")
    assert "tool-delegate" in content, "explorer.md must list tool-delegate in tools"
    assert "exclude_tools" in content, "explorer.md tool-delegate must have exclude_tools config"


def test_explorer_md_tools_five_modules():
    """Verify explorer.md lists the required five tool modules."""
    explorer_md = _agents_pkg() / "explorer.md"
    content = explorer_md.read_text(encoding="utf-8")
    for module in ("tool-bash", "tool-filesystem", "tool-search", "tool-todo", "tool-delegate"):
        assert module in content, f"explorer.md must list {module} in tools"


def test_explorer_md_body_sections():
    """Verify explorer.md body contains required section headings."""
    explorer_md = _agents_pkg() / "explorer.md"
    content = explorer_md.read_text(encoding="utf-8")
    assert "# Explorer" in content, "explorer.md body must have '# Explorer' heading"
    assert "## Execution model" in content, "explorer.md body must have '## Execution model' section"
    assert "## Required inputs" in content, "explorer.md body must have '## Required inputs' section"
    assert "## Operating principles" in content, "explorer.md body must have '## Operating principles' section"
    assert "## Output contract" in content, "explorer.md body must have '## Output contract' section"


def test_explorer_md_roughly_sixty_lines():
    """Verify explorer.md has roughly 60 lines (per spec: wc -l shows roughly 60 lines).

    The upstream file (microsoft/amplifier-foundation@main experiments/build-up/agents/explorer.md)
    measures 88 lines when counted with wc -l. The spec's "roughly 60" is an approximation;
    the verbatim content requirement takes precedence. Accept 55-100 as the valid range.
    """
    explorer_md = _agents_pkg() / "explorer.md"
    content = explorer_md.read_text(encoding="utf-8")
    line_count = content.count("\n")
    # Upstream verbatim content has 88 lines; spec says "roughly 60" - accept 55-100
    assert 55 <= line_count <= 100, (
        f"explorer.md should have roughly 60+ lines (upstream verbatim is 88), got {line_count}"
    )

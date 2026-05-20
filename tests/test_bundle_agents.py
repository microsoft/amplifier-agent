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


# ---------------------------------------------------------------------------
# planner.md tests
# ---------------------------------------------------------------------------


def test_planner_md_is_packaged():
    """Verify planner.md is present as a package resource in bundle/agents/."""
    planner_md = _agents_pkg() / "planner.md"
    assert planner_md.is_file(), "planner.md must be a file in amplifier_agent_lib.bundle.agents package data"


def test_planner_md_has_yaml_frontmatter():
    """Verify planner.md starts with YAML frontmatter delimiters."""
    planner_md = _agents_pkg() / "planner.md"
    content = planner_md.read_text(encoding="utf-8")
    assert content.startswith("---\n"), "planner.md must start with '---\\n' (YAML frontmatter)"
    assert "\n---\n" in content, "planner.md must contain '\\n---\\n' to close YAML frontmatter"


def test_planner_md_meta_name():
    """Verify planner.md declares meta.name: planner."""
    planner_md = _agents_pkg() / "planner.md"
    content = planner_md.read_text(encoding="utf-8")
    assert "name: planner" in content, "planner.md must declare meta.name: planner"


def test_planner_md_model_role():
    """Verify planner.md declares model_role with reasoning and general."""
    planner_md = _agents_pkg() / "planner.md"
    content = planner_md.read_text(encoding="utf-8")
    assert "model_role:" in content, "planner.md must have model_role"
    assert "reasoning" in content, "planner.md model_role must include 'reasoning'"
    assert "general" in content, "planner.md model_role must include 'general'"


def test_planner_md_tools_include_tool_delegate():
    """Verify planner.md includes tool-delegate with exclude_tools: [tool-delegate]."""
    planner_md = _agents_pkg() / "planner.md"
    content = planner_md.read_text(encoding="utf-8")
    assert "tool-delegate" in content, "planner.md must list tool-delegate in tools"
    assert "exclude_tools" in content, "planner.md tool-delegate must have exclude_tools config"


def test_planner_md_tools_include_required_modules():
    """Verify planner.md lists the required tool modules: tool-filesystem, tool-todo, tool-delegate."""
    planner_md = _agents_pkg() / "planner.md"
    content = planner_md.read_text(encoding="utf-8")
    for module in ("tool-filesystem", "tool-todo", "tool-delegate"):
        assert module in content, f"planner.md must list {module} in tools"


def test_planner_md_body_sections():
    """Verify planner.md body contains required section headings."""
    planner_md = _agents_pkg() / "planner.md"
    content = planner_md.read_text(encoding="utf-8")
    assert "# Planner" in content, "planner.md body must have '# Planner' heading"
    assert "## Execution model" in content, "planner.md body must have '## Execution model' section"
    assert "## Core philosophy" in content, "planner.md body must have '## Core philosophy' section"
    assert "## Modes" in content, "planner.md body must have '## Modes' section"
    assert "### ANALYZE" in content, "planner.md body must have '### ANALYZE' section"
    assert "### DESIGN" in content, "planner.md body must have '### DESIGN' section"
    assert "### REVIEW" in content, "planner.md body must have '### REVIEW' section"
    assert "## Boundaries" in content, "planner.md body must have '## Boundaries' section"
    assert "## Handoff rule" in content, "planner.md body must have '## Handoff rule' section"


def test_planner_md_meta_description_modes():
    """Verify planner.md meta.description mentions all three modes."""
    planner_md = _agents_pkg() / "planner.md"
    content = planner_md.read_text(encoding="utf-8")
    assert "ANALYZE" in content, "planner.md must mention ANALYZE mode"
    assert "DESIGN" in content, "planner.md must mention DESIGN mode"
    assert "REVIEW" in content, "planner.md must mention REVIEW mode"


def test_planner_md_two_examples():
    """Verify planner.md includes at least two <example> blocks."""
    planner_md = _agents_pkg() / "planner.md"
    content = planner_md.read_text(encoding="utf-8")
    example_count = content.count("<example>")
    assert example_count >= 2, f"planner.md must include at least 2 <example> blocks, found {example_count}"


def test_planner_md_handoff_rule_coder():
    """Verify planner.md handoff rule references coder agent."""
    planner_md = _agents_pkg() / "planner.md"
    content = planner_md.read_text(encoding="utf-8")
    assert "coder" in content, "planner.md handoff rule must reference 'coder' agent"


# ---------------------------------------------------------------------------
# coder.md tests
# ---------------------------------------------------------------------------


def test_coder_md_is_packaged():
    """Verify coder.md is present as a package resource in bundle/agents/."""
    coder_md = _agents_pkg() / "coder.md"
    assert coder_md.is_file(), "coder.md must be a file in amplifier_agent_lib.bundle.agents package data"


def test_coder_md_has_yaml_frontmatter():
    """Verify coder.md starts with YAML frontmatter delimiters."""
    coder_md = _agents_pkg() / "coder.md"
    content = coder_md.read_text(encoding="utf-8")
    assert content.startswith("---\n"), "coder.md must start with '---\\n' (YAML frontmatter)"
    assert "\n---\n" in content, "coder.md must contain '\\n---\\n' to close YAML frontmatter"


def test_coder_md_meta_name():
    """Verify coder.md declares meta.name: coder."""
    coder_md = _agents_pkg() / "coder.md"
    content = coder_md.read_text(encoding="utf-8")
    assert "name: coder" in content, "coder.md must declare meta.name: coder"


def test_coder_md_model_role():
    """Verify coder.md declares model_role with coding and general."""
    coder_md = _agents_pkg() / "coder.md"
    content = coder_md.read_text(encoding="utf-8")
    assert "model_role:" in content, "coder.md must have model_role"
    assert "coding" in content, "coder.md model_role must include 'coding'"
    assert "general" in content, "coder.md model_role must include 'general'"


def test_coder_md_tools_include_required_modules():
    """Verify coder.md lists the required five tool modules."""
    coder_md = _agents_pkg() / "coder.md"
    content = coder_md.read_text(encoding="utf-8")
    for module in ("tool-bash", "tool-filesystem", "tool-search", "tool-todo", "tool-delegate"):
        assert module in content, f"coder.md must list {module} in tools"


def test_coder_md_tools_include_tool_delegate_with_config():
    """Verify coder.md includes tool-delegate with exclude_tools: [tool-delegate]."""
    coder_md = _agents_pkg() / "coder.md"
    content = coder_md.read_text(encoding="utf-8")
    assert "tool-delegate" in content, "coder.md must list tool-delegate in tools"
    assert "exclude_tools" in content, "coder.md tool-delegate must have exclude_tools config"


def test_coder_md_body_sections():
    """Verify coder.md body contains required section headings."""
    coder_md = _agents_pkg() / "coder.md"
    content = coder_md.read_text(encoding="utf-8")
    assert "# Coder" in content, "coder.md body must have '# Coder' heading"
    assert "## Required inputs" in content, "coder.md body must have '## Required inputs' section"
    assert "## Implementation loop" in content, "coder.md body must have '## Implementation loop' section"
    assert "## Discipline" in content, "coder.md body must have '## Discipline' section"
    assert "## Forbidden" in content, "coder.md body must have '## Forbidden' section"
    assert "## Output contract" in content, "coder.md body must have '## Output contract' section"


def test_coder_md_refusal_protocol():
    """Verify coder.md contains the refusal protocol for under-specified work."""
    coder_md = _agents_pkg() / "coder.md"
    content = coder_md.read_text(encoding="utf-8")
    assert "Specification incomplete" in content, (
        "coder.md must contain 'Specification incomplete' refusal protocol text"
    )


def test_coder_md_one_example():
    """Verify coder.md includes exactly one <example> block."""
    coder_md = _agents_pkg() / "coder.md"
    content = coder_md.read_text(encoding="utf-8")
    example_count = content.count("<example>")
    assert example_count == 1, f"coder.md must include exactly 1 <example> block, found {example_count}"


# ---------------------------------------------------------------------------
# tester.md tests
# ---------------------------------------------------------------------------


def test_tester_md_is_packaged():
    """Verify tester.md is present as a package resource in bundle/agents/."""
    tester_md = _agents_pkg() / "tester.md"
    assert tester_md.is_file(), "tester.md must be a file in amplifier_agent_lib.bundle.agents package data"


def test_tester_md_has_yaml_frontmatter():
    """Verify tester.md starts with YAML frontmatter delimiters."""
    tester_md = _agents_pkg() / "tester.md"
    content = tester_md.read_text(encoding="utf-8")
    assert content.startswith("---\n"), "tester.md must start with '---\\n' (YAML frontmatter)"
    assert "\n---\n" in content, "tester.md must contain '\\n---\\n' to close YAML frontmatter"


def test_tester_md_meta_name():
    """Verify tester.md declares meta.name: tester."""
    tester_md = _agents_pkg() / "tester.md"
    content = tester_md.read_text(encoding="utf-8")
    assert "name: tester" in content, "tester.md must declare meta.name: tester"


def test_tester_md_model_role_general():
    """Verify tester.md declares model_role: general."""
    tester_md = _agents_pkg() / "tester.md"
    content = tester_md.read_text(encoding="utf-8")
    assert "model_role:" in content, "tester.md must have model_role"
    assert "general" in content, "tester.md model_role must include 'general'"


def test_tester_md_tools_include_required_modules():
    """Verify tester.md lists the required five tool modules."""
    tester_md = _agents_pkg() / "tester.md"
    content = tester_md.read_text(encoding="utf-8")
    for module in ("tool-bash", "tool-filesystem", "tool-search", "tool-todo", "tool-delegate"):
        assert module in content, f"tester.md must list {module} in tools"


def test_tester_md_tools_include_tool_delegate_with_config():
    """Verify tester.md includes tool-delegate with exclude_tools: [tool-delegate]."""
    tester_md = _agents_pkg() / "tester.md"
    content = tester_md.read_text(encoding="utf-8")
    assert "tool-delegate" in content, "tester.md must list tool-delegate in tools"
    assert "exclude_tools" in content, "tester.md tool-delegate must have exclude_tools config"


def test_tester_md_body_sections():
    """Verify tester.md body contains required section headings."""
    tester_md = _agents_pkg() / "tester.md"
    content = tester_md.read_text(encoding="utf-8")
    assert "# Tester" in content, "tester.md body must have '# Tester' heading"
    assert "## Boundaries" in content, "tester.md body must have '## Boundaries' section"
    assert "## Testing principles" in content, "tester.md body must have '## Testing principles' section"
    assert "## Workflow" in content, "tester.md body must have '## Workflow' section"
    assert "## Common test commands" in content, "tester.md body must have '## Common test commands' section"
    assert "## Output contract" in content, "tester.md body must have '## Output contract' section"


def test_tester_md_one_example():
    """Verify tester.md includes exactly one <example> block."""
    tester_md = _agents_pkg() / "tester.md"
    content = tester_md.read_text(encoding="utf-8")
    example_count = content.count("<example>")
    assert example_count == 1, f"tester.md must include exactly 1 <example> block, found {example_count}"


def test_tester_md_description_key_phrases():
    """Verify tester.md description contains key phrases from spec."""
    tester_md = _agents_pkg() / "tester.md"
    content = tester_md.read_text(encoding="utf-8")
    assert "test execution" in content, "tester.md description must mention 'test execution'"
    assert "coverage" in content, "tester.md description must mention 'coverage'"
    assert "production code" in content, "tester.md description must mention 'production code'"


def test_tester_md_no_production_code_modification():
    """Verify tester.md Boundaries explicitly forbids modifying production source."""
    tester_md = _agents_pkg() / "tester.md"
    content = tester_md.read_text(encoding="utf-8")
    assert "Do not modify production source" in content or "not modify production" in content, (
        "tester.md Boundaries must explicitly forbid modifying production source"
    )

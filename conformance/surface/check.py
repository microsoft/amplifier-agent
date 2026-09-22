"""Check package exports and implementation dependency direction."""

from __future__ import annotations

import ast
import dataclasses
import importlib.util
import inspect
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCES = [ROOT / "packages" / package / "src" for package in ("python", "engine", "http")]


def within(module: str, package: str) -> bool:
    return module == package or module.startswith(package + ".")


def imports(source: str, package: str) -> list[str]:
    result = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            result.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            name = "." * node.level + (node.module or "")
            resolved = importlib.util.resolve_name(name, package) if node.level else name
            result.append(resolved)
            result.extend(
                resolved + "." + alias.name for alias in node.names if alias.name.startswith("_")
            )
    return result


def violations(module: str, dependencies: list[str]) -> list[str]:
    errors = []
    for dependency in dependencies:
        forbidden = False
        if within(module, "amplifier_agent_http"):
            forbidden = dependency.startswith("amplifier_agent.") or within(
                dependency, "amplifier_agent_engine"
            )
        elif within(module, "amplifier_agent_engine"):
            forbidden = within(dependency, "amplifier_agent") or within(
                dependency, "amplifier_agent_http"
            )
            if within(module, "amplifier_agent_engine._engine"):
                forbidden |= within(dependency, "amplifier_agent_engine._runtime")
            if module in {"amplifier_agent_engine._records", "amplifier_agent_engine._ports"}:
                forbidden |= within(dependency, "amplifier_agent_engine._engine") or within(
                    dependency, "amplifier_agent_engine._runtime"
                )
        elif within(module, "amplifier_agent"):
            forbidden = within(dependency, "amplifier_agent_http")
            if within(dependency, "amplifier_agent_engine"):
                forbidden |= module not in {
                    "amplifier_agent._binding._factory",
                    "amplifier_agent._binding._engine_adapter",
                }
            if module in {"amplifier_agent._records", "amplifier_agent._ports"}:
                forbidden |= within(dependency, "amplifier_agent._binding")
        if dependency.startswith(("amplifier_core", "amplifier_foundation", "amplifier_module_")):
            forbidden = forbidden or module not in {
                "amplifier_agent_engine._engine.adapters",
                "amplifier_agent_engine._engine.assembly",
                "amplifier_agent_engine._engine.builtin_tools",
                "amplifier_agent_engine._engine.mcp_tools",
                "amplifier_agent_engine._engine.skill_tools",
                "amplifier_agent_engine._engine.skill_agents",
                "amplifier_agent_engine._engine.provider_connections",
                "amplifier_agent_engine._engine.provider_policy",
                "amplifier_agent_engine._engine.providers",
                "amplifier_agent_engine._engine.routing",
                "amplifier_agent_engine._engine.storage",
            }
        if forbidden:
            errors.append(f"{module} imports {dependency}")
    return errors


def check() -> list[str]:
    import amplifier_agent as binding

    errors = []
    for source in SOURCES:
        paths = list(source.rglob("*.py"))
        if not paths:
            errors.append(f"No source files found at {source}")
        for path in paths:
            module = ".".join(path.relative_to(source).with_suffix("").parts)
            package = (
                module.removesuffix(".__init__")
                if path.name == "__init__.py"
                else module.rpartition(".")[0]
            )
            errors.extend(
                violations(module.removesuffix(".__init__"), imports(path.read_text(), package))
            )
    required = {
        "Agent",
        "Session",
        "Turn",
        "create_agent",
        "AgentOptions",
        "SessionOptions",
        "TextPart",
        "ConversationMessage",
        "TurnInput",
        "TurnResult",
        "TurnRecord",
        "SessionRecord",
        "TurnInfo",
        "Event",
        "Tool",
        "ToolContext",
        "ToolCall",
        "ToolResolution",
        "ToolFailed",
        "ToolOutcomeUnknown",
        "McpServer",
        "ApprovalRequest",
        "ApprovalResponse",
        "Usage",
        "UsageEntry",
        "AgentError",
        "BUILTIN_TOOLS",
        "contract_version",
        "contract_versions",
    }
    allowed = required | {
        "ApprovalDecision",
        "ApprovalHandler",
        "ApprovalRequestEvent",
        "ApprovalResolution",
        "ContentPart",
        "OutputDelta",
        "Progress",
        "ReasoningDelta",
        "ReasoningFinal",
        "Selection",
        "ToolCallEvent",
        "ToolHandler",
        "ToolResultEvent",
        "TurnStarted",
        "UsageEvent",
    }
    exports = set(binding.__all__)
    if not required <= exports or not exports <= allowed:
        errors.append(
            f"Export mismatch: missing={sorted(required - exports)}, extra={sorted(exports - allowed)}"
        )
    if binding.SessionOptions().persistence != "durable":
        errors.append("SessionOptions must retain durable persistence by default")
    expected_options = {
        "provider",
        "model",
        "instructions",
        "tools",
        "skills",
        "mcp_servers",
        "storage",
        "approvals",
        "tool_error_policy",
        "tool_result_max_bytes",
    }
    if {field.name for field in dataclasses.fields(binding.AgentOptions)} != expected_options:
        errors.append("AgentOptions fields differ from the contract mapping")
    if binding.BUILTIN_TOOLS != (
        "read_file",
        "write_file",
        "edit_file",
        "glob",
        "grep",
        "bash",
        "web_fetch",
        "web_search",
        "delegate",
    ):
        errors.append("BUILTIN_TOOLS differs from the contract's built-in tool names")
    if binding.AgentOptions().tool_error_policy != "stop":
        errors.append("AgentOptions must stop after tool errors by default")
    if binding.AgentOptions().tool_result_max_bytes != 262_144:
        errors.append("AgentOptions must bound tool results at 262144 bytes by default")
    for name in ("Agent", "Session", "Turn"):
        if inspect.signature(getattr(binding, name)).parameters:
            errors.append(f"{name} exposes construction parameters")
    if binding.contract_version != "agent-interface/1" or set(binding.contract_versions) != {
        "agent-interface/1",
        "turn-events/1",
        "language-binding/1",
        "host-config/1",
    }:
        errors.append("Contract version declarations differ from the binding mapping")
    # The lint must reject forbidden dependencies, not merely pass the current tree.
    controls = [
        ("amplifier_agent_http.app", ["amplifier_agent._records"]),
        ("amplifier_agent_http.app", ["amplifier_agent_engine"]),
        ("amplifier_agent_engine._engine.state", ["amplifier_agent"]),
        ("amplifier_agent_engine._runtime.server", ["amplifier_agent._ports"]),
        ("amplifier_agent_engine._records", ["amplifier_agent._records"]),
        ("amplifier_agent_engine._engine.state", ["amplifier_agent_engine._runtime"]),
        ("amplifier_agent._records", ["amplifier_core"]),
        ("amplifier_agent._binding._factory", ["amplifier_module_provider_openai"]),
        ("amplifier_agent_http.app", ["amplifier_foundation"]),
        ("amplifier_agent_engine._records", ["amplifier_module_tool_mcp"]),
        ("amplifier_agent._records", ["amplifier_agent_engine._records"]),
        ("amplifier_agent._binding", ["amplifier_agent_engine._engine.state"]),
    ]
    if not all(violations(module, dependencies) for module, dependencies in controls):
        errors.append("Architecture lint accepted a deliberately forbidden dependency")
    allowed_imports = [
        ("amplifier_agent_http.app", ["amplifier_agent"]),
        ("amplifier_agent_engine._engine.assembly", ["amplifier_core"]),
        ("amplifier_agent_engine._engine.providers", ["amplifier_module_provider_gemini"]),
        ("amplifier_agent_engine._engine.builtin_tools", ["amplifier_module_tool_delegate"]),
        ("amplifier_agent_engine._engine.skill_agents", ["amplifier_foundation.io.frontmatter"]),
        ("amplifier_agent._binding._engine_adapter", ["amplifier_agent_engine._records"]),
    ]
    if any(violations(module, dependencies) for module, dependencies in allowed_imports):
        errors.append("Architecture lint rejected a required dependency")
    return errors


def main() -> None:
    errors = check()
    print(
        json.dumps(
            {
                "errors": errors,
                "checks": ["python-exports", "dependency-direction", "lint-controls"],
            }
        )
    )
    raise SystemExit(bool(errors))


if __name__ == "__main__":
    main()

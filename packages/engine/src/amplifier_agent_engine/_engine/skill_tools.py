"""Load skill content while routing executable work through owned effects."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .._records import AgentError, ToolContext
from .builtin_tools import bash_tool
from .effects import resolution_text
from .skill_agents import (
    SkillAgent,
    agent_catalogue,
    parse_agent,
    read_definition,
    selection,
    tool_names,
)
from .skill_hooks import SkillHooks, parse_hooks
from .tools import SCHEMA, RegisteredTool


def refusal(message: str, remedy: str) -> AgentError:
    return AgentError("invalid_input", "input", message, remedy)


@dataclass(frozen=True)
class Skill:
    metadata: Any
    body: str
    header: dict[str, Any]
    hooks: SkillHooks
    agent: SkillAgent | None


async def prepare_skills(runtime: Any) -> list[RegisteredTool]:
    from amplifier_module_tool_skills.discovery import discover_skills
    from amplifier_module_tool_skills.preprocessing import (
        _substitute_system_variables,
        _substitute_user_variables,
    )
    from amplifier_module_tool_skills.sources import is_remote_source, resolve_skill_source

    if not runtime.config.skills:
        return []
    allowed = getattr(runtime, "allowed_tools", None)
    if allowed is not None and "load_skill" not in allowed:
        return []
    metadata_by_name: dict[str, Any] = {}
    agents: dict[str, list[Path]] = {}
    for source in runtime.config.skills:
        if is_remote_source(source):
            directory = await resolve_skill_source(source, cache_dir=runtime.config.storage / "skills")
        else:
            path = Path(source).expanduser()
            directory = path if path.is_absolute() else runtime.config.working_directory / path
            if directory.is_file() and directory.name == "SKILL.md":
                directory = directory.parent
        if directory is None or not directory.is_dir():
            raise refusal("A configured skill source could not be loaded.",
                          "Use an existing skill directory or an accessible Git source.")
        directory = directory.resolve()
        discovered = discover_skills(directory)
        if not discovered:
            raise refusal(f"No valid skills were found at {source}.",
                          "Add SKILL.md files with a name and description to the skill source.")
        for name, metadata in discovered.items():
            if not metadata.path.resolve().is_relative_to(directory):
                raise refusal(f"The skill file escapes its configured source: {metadata.path}.",
                              "Keep skill files within the configured skill source directory.")
            if name in metadata_by_name:
                raise refusal(f"Duplicate skill name: {name}.",
                              "Give skills distinct names across all configured sources.")
            metadata_by_name[name] = metadata
        for name, paths in agent_catalogue(directory, [item.path for item in discovered.values()]).items():
            agents.setdefault(name, []).extend(paths)

    skills: dict[str, Skill] = {}
    for name, metadata in metadata_by_name.items():
        header, body = read_definition(metadata.path)
        hooks = parse_hooks(header.get("hooks"), metadata.path)
        tool_names(header.get("allowed-tools", header.get("allowed_tools")))
        automatic = header.get("auto-load", header.get("auto_load", False))
        if not isinstance(automatic, bool):
            raise refusal("A skill's auto-load field must be a boolean.",
                          "Use the YAML boolean true or false without quotes.")
        if header.get("context") not in (None, "fork"):
            raise refusal(f"Unsupported skill context: {header['context']}.", "Use context: fork or omit context.")
        requested_agent = header.get("agent")
        if requested_agent is not None and (not isinstance(requested_agent, str) or not requested_agent):
            raise refusal("A skill's agent field must be a nonempty name.",
                          "Use self or the name of an agent in the configured skill source.")
        agent = None
        if metadata.agent and metadata.agent != "self":
            if metadata.context != "fork":
                raise refusal("A named skill agent requires context: fork.",
                              "Set context: fork so the named instructions and tool restrictions have their own scope.")
            paths = tuple(dict.fromkeys(agents.get(metadata.agent, [])))
            local = tuple(path for path in paths if path.parent == metadata.path.parent / "agents")
            paths = local or paths
            if len(paths) != 1:
                raise refusal(f"The skill agent {metadata.agent!r} is unavailable or ambiguous.",
                              "Include one matching agents/<name>.md in a configured skill source, or qualify its source name.")
            agent = parse_agent(paths[0])
        if automatic and hooks.commands:
            if metadata.context == "fork":
                raise refusal("A forked skill cannot automatically activate commands in its parent.",
                              "Remove auto-load and load the forked skill to execute its commands in the child.")
            declared = tool_names(header.get("allowed-tools", header.get("allowed_tools")))
            if ("bash" not in runtime.config.builtin_tools
                    or (declared is not None and "bash" not in declared)
                    or (allowed is not None and "bash" not in allowed)):
                raise refusal("Automatic skill commands require bash within the inherited tool set.",
                              "Include \"bash\" in tools and in the skill and containing agent tool restrictions.")
        skills[name] = Skill(metadata, body, header, hooks, agent)
    runtime.skill_hooks.automatic = tuple(
        skill.hooks for skill in skills.values() if skill.metadata.auto_load and skill.hooks.commands
    )
    runtime.skill_hooks.automatic_tools = {
        skill.hooks.key: tool_names(skill.header.get("allowed-tools", skill.header.get("allowed_tools")))
        for skill in skills.values() if skill.metadata.auto_load and skill.hooks.commands
    }
    runtime.skill_hooks.automatic_selection = tuple(
        skill.header for skill in skills.values() if skill.metadata.auto_load and skill.hooks.commands
    )

    async def handler(arguments: dict[str, Any], context: ToolContext) -> str:
        name = arguments["name"]
        if name not in skills:
            raise refusal(f"Unknown skill: {name}.", "Choose a skill listed by load_skill.")
        skill = skills[name]
        metadata, agent = skill.metadata, skill.agent
        fork = metadata.context == "fork"
        if fork and runtime.skill_fork:
            raise refusal("A forked skill cannot invoke another forked skill.",
                          "Load an inline skill or complete this task within the current skill.")
        access = runtime.allowed_skill_agents
        requested = metadata.agent or "self"
        if fork and access is not None and requested not in access:
            raise refusal(f"The inherited agent restrictions do not permit {requested!r}.",
                          "Choose a skill allowed by the containing agent definition.")
        selected = await selection(runtime, *([agent.selection] if agent else []), skill.header)
        if not fork and selected != runtime.require_observer().model:
            raise refusal("An inline skill cannot refine the active primary model.",
                          "Use context: fork for a skill with a different model.")
        available = runtime.skill_hooks.tools()
        effective = available.copy()
        for declared in (agent.tools if agent else None,
                         tool_names(skill.header.get("allowed-tools", skill.header.get("allowed_tools")))):
            if declared is not None:
                unknown = set(declared) - available
                if unknown:
                    raise refusal(f"The skill requests unavailable tools: {', '.join(sorted(unknown))}.",
                                  "Use tool names within the containing agent's tool set.")
                effective.intersection_update(declared)
        if skill.hooks.commands and "bash" not in effective:
            raise refusal("The skill requires shell hooks outside its inherited tool set.",
                          "Include bash in the skill and named agent tool restrictions.")
        body = _substitute_system_variables(skill.body, metadata.path.parent)
        pieces: list[str] = []
        offset = 0
        for match in re.finditer(r"!`([^`]+)`", body):
            if "bash" not in effective:
                raise refusal("The skill requires shell preprocessing outside its inherited tool set.",
                              "Include bash in the skill and named agent tool restrictions.")
            pieces.append(body[offset:match.start()])
            output = await runtime.call_tool(
                bash_tool(runtime, directory=metadata.path.parent), str(uuid.uuid4()),
                {"command": match.group(1)},
            )
            pieces.append(resolution_text(output))
            offset = match.end()
        pieces.append(body[offset:])
        body = _substitute_user_variables("".join(pieces), arguments.get("arguments"))
        if not fork:
            runtime.skill_hooks.activate(skill.hooks, tuple(sorted(effective)))
            return body
        return await runtime.delegate(
            body, model=selected, tools=tuple(sorted(effective)),
            instructions=agent.instructions if agent else None,
            skill_hooks=(skill.hooks,), skill_fork=True,
            allowed_skill_agents=agent.agents if agent else None,
        )

    names = sorted(skills)
    catalogue = "\n".join(f"{name}: {skills[name].metadata.description}" for name in names)
    return [RegisteredTool("load_skill", "Load a configured skill.\n" + catalogue, {
        "$schema": SCHEMA, "type": "object", "additionalProperties": False,
        "properties": {
            "name": {"type": "string", "enum": names},
            "arguments": {"type": "string"},
        }, "required": ["name"],
    }, handler, "built-in")]

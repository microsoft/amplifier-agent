"""Resolve skill-local agent definitions without mounting caller composition."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .._records import AgentError
from .provider_policy import _rates, select
from .routing import delegated_model

MODULE_TOOLS = {
    "tool-filesystem": ("read_file", "write_file", "edit_file"),
    "tool-bash": ("bash",), "tool-search": ("glob", "grep"),
    "tool-web": ("web_fetch", "web_search"), "tool-skills": ("load_skill",),
    "tool-delegate": ("delegate",),
}
TOOL_ALIASES = {"Read": "read_file", "Write": "write_file", "Edit": "edit_file",
                "Bash": "bash", "Glob": "glob", "Grep": "grep",
                "WebFetch": "web_fetch", "WebSearch": "web_search"}


def invalid(message: str) -> AgentError:
    return AgentError("invalid_input", "input", message,
                      "Use an agent definition in the configured skill source with inherited tools and model selection.")


def read_definition(path: Path) -> tuple[dict[str, Any], str]:
    from amplifier_foundation.io.frontmatter import parse_frontmatter

    try:
        header, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    except Exception as error:
        raise invalid(f"The skill source file could not be parsed: {path}.") from error
    if not isinstance(header, dict) or any(not isinstance(key, str) for key in header):
        raise invalid(f"The skill source frontmatter must be a mapping: {path}.")
    return header, body


def tool_names(raw: Any) -> tuple[str, ...] | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = raw.replace(",", " ").split()
    if not isinstance(raw, list):
        raise invalid("A skill agent's tools must be a list of inherited names or modules.")
    names: list[str] = []
    for value in raw:
        if isinstance(value, str):
            names.append(TOOL_ALIASES.get(value, value))
        elif isinstance(value, dict) and not set(value) - {"module", "source", "config"}:
            module = value.get("module")
            if not isinstance(module, str) or module not in MODULE_TOOLS or value.get("config"):
                raise invalid(f"The skill agent requests unsupported tool composition: {module!r}.")
            names.extend(MODULE_TOOLS[module])
        else:
            raise invalid("The skill agent contains an unsupported tool declaration.")
    if any(not name for name in names):
        raise invalid("Skill agent tool names must be nonempty strings.")
    return tuple(dict.fromkeys(names))


@dataclass(frozen=True)
class SkillAgent:
    path: Path
    instructions: str
    tools: tuple[str, ...] | None
    selection: dict[str, Any]
    agents: tuple[str, ...] | None


def parse_agent(path: Path) -> SkillAgent:
    header, body = read_definition(path)
    unknown = set(header) - {"meta", "name", "description", "version", "tools", "allowed-tools",
                             "model", "model_role", "model-role", "provider_preferences",
                             "provider-preferences", "agents"}
    if unknown:
        raise invalid(f"Unsupported skill agent fields: {', '.join(sorted(unknown))}.")
    if not body.strip():
        raise invalid(f"The skill agent has no instructions: {path}.")
    access = header.get("agents", "all")
    if access == "all":
        agents = None
    elif access == "none":
        agents = ()
    elif isinstance(access, list) and all(isinstance(name, str) and name for name in access):
        agents = tuple(access)
    else:
        raise invalid("A skill agent's agents field must be all, none, or a list of names.")
    return SkillAgent(path, body, tool_names(header.get("allowed-tools", header.get("tools"))),
                      header, agents)


def agent_catalogue(source: Path, skill_paths: list[Path]) -> dict[str, tuple[Path, ...]]:
    root = source.parent if source.name == "skills" else source
    namespace = root.name
    bundle = root / "bundle.md"
    if bundle.is_file():
        header, _ = read_definition(bundle)
        metadata = header.get("bundle", {})
        if isinstance(metadata, dict) and isinstance(metadata.get("name"), str):
            namespace = metadata["name"]
    directories = {root / "agents", source / "agents", *(path.parent / "agents" for path in skill_paths)}
    catalogue: dict[str, list[Path]] = {}
    for directory in sorted(directories):
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.md")):
            if not path.resolve().is_relative_to(root.resolve()):
                raise invalid(f"The skill agent file escapes its configured source: {path}.")
            header, _ = read_definition(path)
            metadata = header.get("meta", header)
            name = metadata.get("name", path.stem) if isinstance(metadata, dict) else path.stem
            if not isinstance(name, str) or not name:
                raise invalid(f"The skill agent name is invalid: {path}.")
            for alias in {name, f"{namespace}:{name}"}:
                catalogue.setdefault(alias, []).append(path)
    return {name: tuple(dict.fromkeys(paths)) for name, paths in catalogue.items()}


async def selection(runtime: Any, *layers: dict[str, Any]) -> str:
    model = runtime.require_observer().model
    for values in layers:
        preferences = values.get("provider_preferences", values.get("provider-preferences"))
        hint = values.get("model")
        role = values.get("model_role", values.get("model-role"))
        if preferences is not None:
            if not isinstance(preferences, list) or not preferences:
                raise invalid("Skill provider_preferences must contain provider/model mappings.")
            selected = None
            for preference in preferences:
                if not isinstance(preference, dict) or set(preference) - {"provider", "model"}:
                    raise invalid("Skill provider_preferences accepts provider and model only.")
                if not all(isinstance(preference.get(key), str) and preference[key]
                           for key in ("provider", "model")):
                    raise invalid("Skill provider_preferences requires nonempty provider and model names.")
                if preference["provider"] != runtime.config.provider:
                    continue
                pattern = preference["model"]
                candidates = [model, *sorted(_rates(runtime.config.provider))]
                for candidate in candidates:
                    if not fnmatch.fnmatchcase(candidate, pattern):
                        continue
                    try:
                        selected = select(candidate, model, runtime.config.provider)
                    except AgentError:
                        continue
                    break
                if selected is not None:
                    break
            if selected is None:
                raise AgentError("selector_rejected", "selection",
                                 "The skill has no model within the current provider and ceiling.",
                                 "Choose a matching provider/model preference within the current ceiling.")
            model = selected
        elif hint is not None:
            if not isinstance(hint, str) or not hint:
                raise invalid("A skill model must be a nonempty string.")
            model = await delegated_model(runtime.config.provider, model, model=hint)
        elif role is not None:
            if isinstance(role, list):
                role = next((name for name in role if name in ("general", "economy")), None)
            if role is None or not isinstance(role, str):
                raise invalid("A skill model_role must name general or economy.")
            model = await delegated_model(runtime.config.provider, model, role=role)
    return model

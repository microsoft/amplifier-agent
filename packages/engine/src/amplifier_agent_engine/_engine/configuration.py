"""Resolve and validate construction data once per agent."""

from __future__ import annotations

import base64
import copy
from dataclasses import dataclass, field, fields
import difflib
import json
import math
import os
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any, cast

from jsonschema.exceptions import SchemaError
from jsonschema.validators import validator_for

from amplifier_agent_engine._engine import reasoning
from amplifier_agent_engine._engine.provider_policy import PROVIDERS, settings
from amplifier_agent_engine._engine.provider_policy import select as select
from amplifier_agent_engine._records import (
    BUILTIN_TOOLS,
    AgentError,
    AgentOptions,
    ApprovalHandler,
    ConversationMessage,
    ImagePart,
    McpServer,
    SessionOptions,
    TextPart,
    Tool,
    TurnInput,
)

if TYPE_CHECKING:
    from _typeshed import DataclassInstance


def invalid(path: str, message: str, remedy: str) -> AgentError:
    return AgentError("invalid_input", "input", f"{path}: {message}", remedy, details={"field": path})


def record(value: Any, cls: type[DataclassInstance], path: str) -> None:
    if not isinstance(value, cls):
        raise invalid(path, f"expected {cls.__name__}.", f"Pass a {cls.__name__} at {path}.")
    names = {item.name for item in fields(cls)}
    for name in vars(value).keys() - names:
        nearest = difflib.get_close_matches(name, names, n=1)
        remedy = f"Use {nearest[0]} instead." if nearest else f"Remove {path}.{name}."
        raise invalid(f"{path}.{name}", "unregistered field.", remedy)


def strict_json(value: Any, path: str) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float) and math.isfinite(value):
        return
    if isinstance(value, list):
        for i, item in enumerate(value):
            strict_json(item, f"{path}[{i}]")
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for key, item in value.items():
            strict_json(item, f"{path}.{key}")
        return
    raise invalid(path, "expected strict JSON.", f"Use JSON values with finite numbers at {path}.")


@dataclass(frozen=True)
class ResolvedConfig:
    provider: str
    model: str
    instructions: str | None
    tools: tuple[Tool, ...]
    approvals: ApprovalHandler | str | None
    sessions_directory: Path
    extra_request_params: dict[str, Any]
    api_key: str | None = field(repr=False)
    base_url: str | None = field(repr=False)
    connection: dict[str, Any] = field(default_factory=dict, repr=False)
    skills: tuple[str, ...] = ()
    mcp_servers: tuple[McpServer, ...] = ()
    environment: dict[str, str] = field(default_factory=dict, repr=False)
    working_directory: Path = field(default_factory=Path.cwd)
    additional_directories: tuple[Path, ...] = ()
    tool_error_policy: str = "continue"
    tool_result_max_bytes: int | None = 131_072
    context_intelligence: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)
    builtin_tools: tuple[str, ...] = BUILTIN_TOOLS
    # The named reasoning ceiling, or None when the default applies.
    reasoning_effort: str | None = None


def resolve(options: AgentOptions) -> ResolvedConfig:
    record(options, AgentOptions, "options")
    if not isinstance(options.tool_error_policy, str) or options.tool_error_policy not in ("stop", "continue"):
        raise invalid(
            "tool_error_policy",
            "invalid tool error policy.",
            "Set tool_error_policy to 'stop' or 'continue'.",
        )
    ceiling = options.tool_result_max_bytes
    if ceiling is not None and (type(ceiling) is not int or ceiling < 1):
        raise invalid(
            "tool_result_max_bytes",
            "invalid tool result ceiling.",
            "Set tool_result_max_bytes to a positive integer or None.",
        )
    process_directory = Path.cwd()
    process_environment = dict(os.environ)
    working_directory = directory(options.working_directory, process_directory, "working_directory")
    additional = options.additional_directories
    if additional is None:
        additional = []
    if not isinstance(additional, list):
        raise invalid(
            "additional_directories",
            "expected a list of directory paths.",
            "Pass a list of existing directory paths.",
        )
    additional_directories = tuple(
        directory(item, working_directory, f"additional_directories[{index}]") for index, item in enumerate(additional)
    )
    environment = agent_environment(options.environment, process_environment)
    config_path = (
        process_directory
        / Path(os.environ.get("AMPLIFIER_AGENT_CONFIG", "~/.amplifier-agent/config.json")).expanduser()
    )
    host: dict[str, Any] = {}
    if config_path.exists():
        try:
            host = json.loads(
                config_path.read_text(),
                parse_constant=lambda v: (_ for _ in ()).throw(ValueError(v)),
            )
        except (OSError, ValueError) as exc:
            raise invalid(
                "config",
                "cannot read valid JSON settings.",
                "Correct the host configuration file and construct the agent again.",
            ) from exc
        if not isinstance(host, dict):
            raise invalid("config", "expected an object.", "Use a JSON object in the host configuration file.")
    elif "AMPLIFIER_AGENT_CONFIG" in os.environ:
        raise invalid(
            "config",
            "the configured file does not exist.",
            "Create the configuration file or remove AMPLIFIER_AGENT_CONFIG.",
        )
    registered = {
        "provider",
        "model",
        "reasoning_effort",
        "sessions_directory",
        "approvals",
        "extra_request_params",
        "context_intelligence",
    }
    for name in host.keys() - registered:
        nearest = difflib.get_close_matches(name, registered, n=1, cutoff=0)
        raise invalid(
            name,
            "unregistered host setting.",
            f"Use {nearest[0]}." if nearest else f"Remove the {name} setting.",
        )
    environment_keys = {"PROVIDER", "MODEL", "REASONING_EFFORT", "SESSIONS_DIRECTORY", "APPROVALS", "CONFIG"}
    for name in os.environ:
        if not name.startswith("AMPLIFIER_AGENT_"):
            continue
        suffix = name.removeprefix("AMPLIFIER_AGENT_")
        if suffix in environment_keys or suffix.startswith(("FACE_", "ENGINE_", "NODE_")):
            continue
        nearest = difflib.get_close_matches(suffix, environment_keys, n=1, cutoff=0)
        raise invalid(
            name,
            "unregistered host environment setting.",
            f"Use AMPLIFIER_AGENT_{nearest[0]}." if nearest else f"Remove {name}.",
        )
    sources = {"reasoning_effort": "the host configuration file"} if "reasoning_effort" in host else {}
    for name in ("provider", "model", "reasoning_effort", "sessions_directory", "approvals"):
        value = os.environ.get(f"AMPLIFIER_AGENT_{name.upper()}")
        if value is not None:
            host[name] = value
            sources[name] = f"AMPLIFIER_AGENT_{name.upper()}"
    for name in ("provider", "model", "sessions_directory"):
        value = getattr(options, name)
        if value is not None:
            host[name] = str(value) if isinstance(value, Path) else value
    provider = host.get("provider", "anthropic")
    model = host.get("model", "claude-sonnet-5")
    for name, value in (("provider", provider), ("model", model)):
        if not isinstance(value, str) or not value:
            raise invalid(name, "expected one nonempty string.", f"Provide a single {name} value.")
    if options.reasoning_effort is not None:
        reasoning_effort = reasoning.parse(options.reasoning_effort, "reasoning_effort")
    else:
        reasoning_effort = reasoning.parse(
            host.get("reasoning_effort"), "reasoning_effort", sources.get("reasoning_effort")
        )
    if provider not in PROVIDERS:
        raise AgentError(
            "selector_rejected",
            "selection",
            "The requested provider is not registered.",
            "Select one of: " + ", ".join(sorted(PROVIDERS)) + ".",
            details={"provider": provider},
        )
    reasoning.check(provider, model, reasoning_effort)
    sessions = host.get("sessions_directory")
    if sessions is None:
        sessions_directory = Path("~/.amplifier-agent/projects").expanduser() / slug(working_directory)
    elif isinstance(sessions, str) and sessions:
        sessions_directory = Path(os.path.normpath(process_directory / Path(sessions).expanduser()))
    else:
        raise invalid(
            "sessions_directory",
            "expected a nonempty path.",
            "Provide a sessions directory path, or omit it to use the default.",
        )
    skills = [] if options.skills is None else options.skills
    if not isinstance(skills, list) or any(not isinstance(item, str) or not item for item in skills):
        raise invalid("skills", "expected source locations.", "Pass a list of nonempty skill source strings.")
    mcp_servers = [] if options.mcp_servers is None else options.mcp_servers
    if not isinstance(mcp_servers, list):
        raise invalid("mcp_servers", "expected server declarations.", "Pass a list of McpServer values.")
    mcp_names = set()
    for index, server in enumerate(mcp_servers):
        path = f"mcp_servers[{index}]"
        record(server, McpServer, path)
        if not isinstance(server.name, str) or not server.name or server.name in mcp_names:
            raise invalid(
                path + ".name",
                "invalid or duplicate server name.",
                "Give each MCP server a unique nonempty name.",
            )
        mcp_names.add(server.name)
        if server.transport not in {"stdio", "http"}:
            raise invalid(path + ".transport", "unsupported transport.", "Use stdio or http.")
        if server.transport == "stdio":
            if (
                not isinstance(server.command, str)
                or not server.command
                or server.url is not None
                or server.headers is not None
            ):
                raise invalid(
                    path,
                    "invalid stdio declaration.",
                    "Set command and optional args/env for stdio servers.",
                )
        elif (
            not isinstance(server.url, str)
            or not server.url.startswith(("http://", "https://"))
            or server.command is not None
            or server.args is not None
            or server.env is not None
        ):
            raise invalid(
                path,
                "invalid HTTP declaration.",
                "Set an HTTP(S) url and optional headers for HTTP servers.",
            )
        if server.args is not None and (
            not isinstance(server.args, list) or any(not isinstance(arg, str) for arg in server.args)
        ):
            raise invalid(path + ".args", "expected text arguments.", "Pass a list of strings.")
        for member in ("env", "headers"):
            mapping = getattr(server, member)
            if mapping is not None and (
                not isinstance(mapping, dict)
                or any(not isinstance(k, str) or not isinstance(v, str) for k, v in mapping.items())
            ):
                raise invalid(path + "." + member, "expected a string map.", "Pass string keys and values.")
    if options.instructions is not None and not isinstance(options.instructions, str):
        raise invalid("instructions", "expected text.", "Provide instructions as a string.")
    if options.approvals is not None and not callable(options.approvals) and options.approvals not in ("allow", "deny"):
        raise invalid(
            "approvals",
            "invalid approval authority.",
            "Provide an approval handler, 'allow', or 'deny'.",
        )
    approvals = options.approvals
    if approvals is None and "approvals" in host:
        approvals = host["approvals"]
        if approvals not in ("allow", "deny"):
            raise invalid("approvals", "invalid approval policy.", "Set approvals to 'allow' or 'deny'.")
    if options.tools is not None and not isinstance(options.tools, list):
        raise invalid(
            "tools",
            "expected a tool list.",
            "Provide a list of Tool values and BUILTIN_TOOLS names, or omit tools.",
        )
    tools = []
    selected: list[str] = []
    names: set[str] = set()
    for index, tool in enumerate(list(BUILTIN_TOOLS) if options.tools is None else options.tools):
        path = f"tools[{index}]"
        if isinstance(tool, str):
            if tool not in BUILTIN_TOOLS:
                raise invalid(
                    path,
                    f"{tool!r} is not a built-in tool.",
                    "Name a built-in from BUILTIN_TOOLS or supply a Tool declaration.",
                )
            if tool in names:
                raise invalid(
                    path,
                    "duplicate tool name.",
                    "List each built-in once and give caller tools names not in the set.",
                )
            names.add(tool)
            selected.append(tool)
            continue
        record(tool, Tool, path)
        if not isinstance(tool.name, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", tool.name) or tool.name in names:
            raise invalid(
                f"{path}.name",
                "invalid or duplicate tool name.",
                "Give each tool a name of letters, digits, underscores, or hyphens that no other "
                "entry in tools, including BUILTIN_TOOLS names, uses.",
            )
        if not isinstance(tool.description, str) or not callable(tool.handler):
            raise invalid(
                path,
                "a tool needs a description and handler.",
                "Supply a string description and an async tool handler.",
            )
        if not isinstance(tool.input_schema, dict) or not isinstance(tool.input_schema.get("$schema"), str):
            raise invalid(
                f"{path}.input_schema",
                "missing JSON Schema dialect.",
                "Declare $schema in the tool input_schema.",
            )
        strict_json(tool.input_schema, f"{path}.input_schema")
        validator = validator_for(tool.input_schema, default=cast(Any, None))
        if validator is None:
            raise invalid(
                f"{path}.input_schema.$schema",
                "unknown JSON Schema dialect.",
                "Select a registered JSON Schema dialect such as draft 2020-12.",
            )
        try:
            validator.check_schema(tool.input_schema)
        except SchemaError as exc:
            raise invalid(
                f"{path}.input_schema",
                "the schema is invalid.",
                "Correct the schema before registering the tool.",
            ) from exc
        strict_json(tool.safety, f"{path}.safety")
        names.add(tool.name)
        tools.append(
            Tool(
                tool.name,
                tool.description,
                copy.deepcopy(tool.input_schema),
                tool.handler,
                copy.deepcopy(tool.safety),
            )
        )
    extra = host.get("extra_request_params", {})
    if not isinstance(extra, dict) or any(not isinstance(v, dict) for v in extra.values()):
        raise invalid(
            "extra_request_params",
            "expected a per-provider map.",
            "Map each provider name to a settings object.",
        )
    strict_json(extra, "extra_request_params")
    for name in extra.keys() - PROVIDERS:
        raise invalid(
            f"extra_request_params.{name}",
            "unregistered provider.",
            "Use a registered provider ID.",
        )
    selected_extra = settings(provider, extra.get(provider, {}))
    destinations = capture_destinations(host.get("context_intelligence", {}))
    from amplifier_agent_engine._engine.provider_connections import snapshot

    connection = snapshot(provider, environment)
    return ResolvedConfig(
        provider,
        model,
        options.instructions,
        tuple(tools),
        approvals,
        sessions_directory,
        copy.deepcopy(selected_extra),
        connection.get("api_key"),
        connection.get("base_url"),
        connection,
        tuple(skills),
        tuple(copy.deepcopy(mcp_servers)),
        environment,
        working_directory,
        additional_directories,
        options.tool_error_policy,
        ceiling,
        destinations,
        tuple(selected),
        reasoning_effort,
    )


def directory(value: Any, base: Path, path: str) -> Path:
    if isinstance(value, Path) or (isinstance(value, str) and value):
        location = (base / value).resolve()
        if location.is_dir():
            return location
        raise invalid(
            path,
            f"{location} is not an existing directory.",
            f"Set {path} to an existing directory, or create it first.",
        )
    if value is None and path == "working_directory":
        return base.resolve()
    raise invalid(path, "expected a nonempty path.", f"Set {path} to an existing directory path.")


def agent_environment(value: Any, process: dict[str, str]) -> dict[str, str]:
    if value is None:
        return process
    if not isinstance(value, dict):
        raise invalid(
            "environment",
            "expected a map of variable names to string values.",
            "Pass a dict of variable names to string values.",
        )
    for name, item in value.items():
        if not isinstance(name, str) or not name or "=" in name:
            raise invalid(
                f"environment.{name}" if isinstance(name, str) and name else "environment",
                "invalid variable name.",
                "Use nonempty environment variable names without '='.",
            )
        if not isinstance(item, str):
            raise invalid(
                f"environment.{name}",
                "expected a string value.",
                f"Set environment[{name!r}] to a string.",
            )
    return {**process, **value}


def slug(working_directory: Path) -> str:
    """Name a working directory's default sessions directory, as the Amplifier CLI names projects."""
    text = str(working_directory).replace("/", "-").replace("\\", "-").replace(":", "")
    return text if text.startswith("-") else "-" + text


_DESTINATION_FIELDS = {"url", "api_key", "auth_mode", "auth_resource", "include", "exclude"}


def capture_destinations(value: Any) -> dict[str, dict[str, Any]]:
    path = "context_intelligence"
    if not isinstance(value, dict):
        raise invalid(path, "expected an object.", "Map context_intelligence to { destinations }.")
    for name in value.keys() - {"destinations"}:
        raise invalid(f"{path}.{name}", "unregistered setting.", "Use destinations instead.")
    destinations = value.get("destinations", {})
    if not isinstance(destinations, dict):
        raise invalid(
            f"{path}.destinations",
            "expected a map of destination names.",
            "Map each destination name to { url, api_key | auth_mode, auth_resource, include, exclude }.",
        )
    result: dict[str, dict[str, Any]] = {}
    for name, spec in destinations.items():
        entry = f"{path}.destinations.{name}"
        if not isinstance(spec, dict):
            raise invalid(entry, "expected a destination object.", f"Use an object at {entry}.")
        for member in spec.keys() - _DESTINATION_FIELDS:
            nearest = difflib.get_close_matches(member, _DESTINATION_FIELDS, n=1, cutoff=0)
            raise invalid(f"{entry}.{member}", "unregistered destination field.", f"Use {nearest[0]}.")
        url = spec.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise invalid(f"{entry}.url", "expected an HTTP(S) url.", f"Set {entry}.url.")
        mode = spec.get("auth_mode", "static")
        if mode not in ("static", "entra"):
            raise invalid(f"{entry}.auth_mode", "unregistered auth mode.", "Use static or entra.")
        credential = spec.get("api_key" if mode == "static" else "auth_resource")
        if not isinstance(credential, str) or not credential:
            raise invalid(
                entry,
                "missing credentials.",
                "Set api_key, or set auth_mode to entra with auth_resource.",
            )
        for member in ("api_key", "auth_resource"):
            if member in spec and (not isinstance(spec[member], str) or not spec[member]):
                raise invalid(f"{entry}.{member}", "expected nonempty text.", f"Set {entry}.{member}.")
        patterns: dict[str, list[str]] = {}
        for member, default in (("include", ["**"]), ("exclude", [])):
            items = spec.get(member, default)
            if not isinstance(items, list) or any(not isinstance(item, str) for item in items):
                raise invalid(f"{entry}.{member}", "expected a pattern list.", "Pass a list of strings.")
            patterns[member] = list(items)
        result[name] = {
            "url": url,
            "auth_mode": mode,
            **({"api_key": spec["api_key"]} if "api_key" in spec else {}),
            **({"auth_resource": spec["auth_resource"]} if "auth_resource" in spec else {}),
            **patterns,
        }
    return result


def session_options(options: SessionOptions | None) -> SessionOptions:
    value = copy.deepcopy(options) if options is not None else SessionOptions()
    record(value, SessionOptions, "options")
    if value.session_id is not None and (
        not isinstance(value.session_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{7,63}", value.session_id)
    ):
        raise AgentError(
            "session_id_invalid",
            "session",
            "The supplied session_id is invalid.",
            "Use 8 to 64 lowercase letters, digits, or hyphens, starting with a letter or digit.",
        )
    if value.persistence not in ("durable", "ephemeral"):
        raise invalid("persistence", "unknown persistence value.", "Use 'durable' or 'ephemeral'.")
    reasoning.parse(value.reasoning_effort, "reasoning_effort")
    return value


IMAGE_MEDIA_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp")


def image_part(part: ImagePart, path: str) -> None:
    if not isinstance(part.media_type, str) or part.media_type not in IMAGE_MEDIA_TYPES:
        raise invalid(
            f"{path}.media_type",
            f"unregistered image media type {part.media_type!r}.",
            f"Set media_type to one of {', '.join(IMAGE_MEDIA_TYPES)}, converting the image if needed.",
        )
    remedy = "Set data to the image bytes encoded as standard base64, without a data: URL prefix."
    if not isinstance(part.data, str) or not part.data:
        raise invalid(f"{path}.data", "expected nonempty base64 image data.", remedy)
    try:
        decoded = base64.b64decode(part.data, validate=True)
    except ValueError:
        raise invalid(f"{path}.data", "the image data is not valid standard base64.", remedy) from None
    if not decoded:
        raise invalid(f"{path}.data", "the image data decodes to no bytes.", remedy)


def turn_input(value: TurnInput, *, seed_allowed: bool) -> TurnInput:
    record(value, TurnInput, "input")
    reasoning.parse(value.reasoning_effort, "input.reasoning_effort")

    def parts(content: Any, path: str, images: bool) -> None:
        if not isinstance(content, list):
            raise invalid(path, "expected content parts.", f"Provide a list of TextPart or ImagePart values at {path}.")
        for index, part in enumerate(content):
            item = f"{path}[{index}]"
            if isinstance(part, ImagePart):
                record(part, ImagePart, item)
                if part.type != "image":
                    raise invalid(f"{item}.type", "an ImagePart has type 'image'.", "Leave ImagePart.type unset.")
                if not images:
                    raise invalid(
                        item,
                        "image parts are accepted only in input.content and user messages.",
                        "Move the image into input.content or a user message, or remove it.",
                    )
                image_part(part, item)
                continue
            if not isinstance(part, TextPart):
                raise invalid(
                    item,
                    "unregistered content part.",
                    "Use a TextPart, or an ImagePart in input.content or a user message.",
                )
            record(part, TextPart, item)
            if part.type != "text" or not isinstance(part.text, str):
                raise invalid(item, "expected a text part with string text.", "Use TextPart with a string text value.")

    parts(value.content, "input.content", images=True)
    if value.history is not None:
        if not seed_allowed:
            raise invalid(
                "input.history",
                "this session cannot accept supplied history.",
                "Supply history only to an empty ephemeral session before its first accepted turn.",
            )
        if not isinstance(value.history, list):
            raise invalid(
                "input.history",
                "expected a conversation list.",
                "Supply a list of ConversationMessage values.",
            )
        for index, message in enumerate(value.history):
            path = f"input.history[{index}]"
            record(message, ConversationMessage, path)
            if message.role not in ("system", "developer", "user", "assistant"):
                raise invalid(
                    f"{path}.role",
                    "unregistered conversation role.",
                    "Use system, developer, user, or assistant.",
                )
            parts(message.content, f"{path}.content", images=message.role == "user")
    if not value.content and not value.history:
        raise invalid(
            "input.content",
            "the combined input is empty.",
            "Provide content or at least one history message.",
        )
    return copy.deepcopy(value)

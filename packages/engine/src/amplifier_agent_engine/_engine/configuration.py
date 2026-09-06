"""Resolve and validate construction data once per agent."""

from __future__ import annotations

import copy
import difflib
import json
import math
import os
import re
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, cast

from jsonschema.exceptions import SchemaError
from jsonschema.validators import validator_for

from .._records import (
    AgentError,
    AgentOptions,
    ApprovalHandler,
    ConversationMessage,
    McpServer,
    SessionOptions,
    TextPart,
    Tool,
    TurnInput,
)
from .provider_policy import PROVIDERS, settings
from .provider_policy import select as select


def invalid(path: str, message: str, remedy: str) -> AgentError:
    return AgentError(
        "invalid_input", "input", f"{path}: {message}", remedy, details={"field": path}
    )


def record(value: Any, cls: type, path: str) -> None:
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
    storage: Path
    workspace: str
    extra_request_params: dict[str, Any]
    api_key: str | None = field(repr=False)
    base_url: str | None = field(repr=False)
    connection: dict[str, Any] = field(default_factory=dict, repr=False)
    skills: tuple[str, ...] = ()
    mcp_servers: tuple[McpServer, ...] = ()
    environment: dict[str, str] = field(default_factory=dict, repr=False)
    working_directory: Path = field(default_factory=Path.cwd)
    tool_error_policy: str = "stop"


def resolve(options: AgentOptions) -> ResolvedConfig:
    record(options, AgentOptions, "options")
    if not isinstance(options.tool_error_policy, str) or options.tool_error_policy not in (
        "stop", "continue"
    ):
        raise invalid(
            "tool_error_policy",
            "invalid tool error policy.",
            "Set tool_error_policy to 'stop' or 'continue'.",
        )
    working_directory = Path.cwd()
    config_path = working_directory / Path(
        os.environ.get("AMPLIFIER_AGENT_CONFIG", "~/.amplifier-agent/config.json")
    ).expanduser()
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
            raise invalid(
                "config", "expected an object.", "Use a JSON object in the host configuration file."
            )
    elif "AMPLIFIER_AGENT_CONFIG" in os.environ:
        raise invalid(
            "config",
            "the configured file does not exist.",
            "Create the configuration file or remove AMPLIFIER_AGENT_CONFIG.",
        )
    registered = {"provider", "model", "storage", "workspace", "extra_request_params"}
    for name in host.keys() - registered:
        nearest = difflib.get_close_matches(name, registered, n=1, cutoff=0)
        raise invalid(
            name,
            "unregistered host setting.",
            f"Use {nearest[0]}." if nearest else f"Remove the {name} setting.",
        )
    environment_keys = {"PROVIDER", "MODEL", "STORAGE", "WORKSPACE", "CONFIG"}
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
    for name in ("provider", "model", "storage", "workspace"):
        value = os.environ.get(f"AMPLIFIER_AGENT_{name.upper()}")
        if value is not None:
            host[name] = value
    for name in ("provider", "model", "storage"):
        value = getattr(options, name)
        if value is not None:
            host[name] = str(value) if isinstance(value, Path) else value
    provider = host.get("provider", "anthropic")
    model = host.get("model", "claude-sonnet-5")
    for name, value in (("provider", provider), ("model", model)):
        if not isinstance(value, str) or not value:
            raise invalid(name, "expected one nonempty string.", f"Provide a single {name} value.")
    if provider not in PROVIDERS:
        raise AgentError(
            "selector_rejected",
            "selection",
            "The requested provider is not registered.",
            "Select one of: " + ", ".join(sorted(PROVIDERS)) + ".",
            details={"provider": provider},
        )
    workspace = host.get("workspace", "default")
    if not isinstance(workspace, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", workspace):
        raise invalid(
            "workspace",
            "invalid workspace slug.",
            "Use 1 to 64 lowercase letters, digits, or hyphens, starting with a letter or digit.",
        )
    storage = host.get("storage", "~/.amplifier-agent")
    if not isinstance(storage, str) or not storage:
        raise invalid("storage", "expected a nonempty path.", "Provide a storage directory path.")
    skills = [] if options.skills is None else options.skills
    if not isinstance(skills, list) or any(
        not isinstance(item, str) or not item for item in skills
    ):
        raise invalid(
            "skills", "expected source locations.", "Pass a list of nonempty skill source strings."
        )
    mcp_servers = [] if options.mcp_servers is None else options.mcp_servers
    if not isinstance(mcp_servers, list):
        raise invalid(
            "mcp_servers", "expected server declarations.", "Pass a list of McpServer values."
        )
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
            not isinstance(server.args, list)
            or any(not isinstance(arg, str) for arg in server.args)
        ):
            raise invalid(path + ".args", "expected text arguments.", "Pass a list of strings.")
        for member in ("env", "headers"):
            mapping = getattr(server, member)
            if mapping is not None and (
                not isinstance(mapping, dict)
                or any(not isinstance(k, str) or not isinstance(v, str) for k, v in mapping.items())
            ):
                raise invalid(
                    path + "." + member, "expected a string map.", "Pass string keys and values."
                )
    if options.instructions is not None and not isinstance(options.instructions, str):
        raise invalid("instructions", "expected text.", "Provide instructions as a string.")
    if (
        options.approvals is not None
        and not callable(options.approvals)
        and options.approvals not in ("allow", "deny")
    ):
        raise invalid(
            "approvals",
            "invalid approval authority.",
            "Provide an approval handler, 'allow', or 'deny'.",
        )
    if options.tools is not None and not isinstance(options.tools, list):
        raise invalid("tools", "expected a tool list.", "Provide a list of Tool values.")
    tools = []
    names: set[str] = set()
    for index, tool in enumerate(options.tools or []):
        path = f"tools[{index}]"
        record(tool, Tool, path)
        if (
            not isinstance(tool.name, str)
            or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", tool.name)
            or tool.name in names
        ):
            raise invalid(
                f"{path}.name",
                "invalid or duplicate tool name.",
                "Give each tool a unique name of letters, digits, underscores, or hyphens.",
            )
        if not isinstance(tool.description, str) or not callable(tool.handler):
            raise invalid(
                path,
                "a tool needs a description and handler.",
                "Supply a string description and an async tool handler.",
            )
        if not isinstance(tool.input_schema, dict) or not isinstance(
            tool.input_schema.get("$schema"), str
        ):
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
    from .provider_connections import snapshot

    connection = snapshot(provider)
    return ResolvedConfig(
        provider,
        model,
        options.instructions,
        tuple(tools),
        options.approvals,
        working_directory / Path(storage).expanduser(),
        workspace,
        copy.deepcopy(selected_extra),
        connection.get("api_key"),
        connection.get("base_url"),
        connection,
        tuple(skills),
        tuple(copy.deepcopy(mcp_servers)),
        dict(os.environ),
        working_directory,
        options.tool_error_policy,
    )


def session_options(options: SessionOptions | None) -> SessionOptions:
    value = copy.deepcopy(options) if options is not None else SessionOptions()
    record(value, SessionOptions, "options")
    if value.session_id is not None and (
        not isinstance(value.session_id, str)
        or not re.fullmatch(r"[a-z0-9][a-z0-9-]{7,63}", value.session_id)
    ):
        raise AgentError(
            "session_id_invalid",
            "session",
            "The supplied session_id is invalid.",
            "Use 8 to 64 lowercase letters, digits, or hyphens, starting with a letter or digit.",
        )
    if value.persistence not in ("durable", "ephemeral"):
        raise invalid("persistence", "unknown persistence value.", "Use 'durable' or 'ephemeral'.")
    return value


def turn_input(value: TurnInput, *, seed_allowed: bool) -> TurnInput:
    record(value, TurnInput, "input")

    def parts(content: Any, path: str) -> None:
        if not isinstance(content, list):
            raise invalid(
                path, "expected content parts.", f"Provide a list of TextPart values at {path}."
            )
        for index, part in enumerate(content):
            record(part, TextPart, f"{path}[{index}]")
            if part.type != "text" or not isinstance(part.text, str):
                raise invalid(
                    f"{path}[{index}]",
                    "only text content is accepted.",
                    "Use TextPart with a string text value.",
                )

    parts(value.content, "input.content")
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
            parts(message.content, f"{path}.content")
    if not value.content and not value.history:
        raise invalid(
            "input.content",
            "the combined input is empty.",
            "Provide content or at least one history message.",
        )
    return copy.deepcopy(value)

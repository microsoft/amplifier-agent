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
    SessionOptions,
    TextPart,
    Tool,
    TurnInput,
)


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


def resolve(options: AgentOptions) -> ResolvedConfig:
    record(options, AgentOptions, "options")
    config_path = Path(
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
        nearest = difflib.get_close_matches(name, registered, n=1)
        raise invalid(
            name,
            "unregistered host setting.",
            f"Use {nearest[0]}." if nearest else f"Remove the {name} setting.",
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
    if provider != "anthropic" or model not in {"claude-sonnet-5", "claude-opus-5"}:
        raise AgentError(
            "selector_rejected",
            "selection",
            "The requested provider/model selection cannot be honored.",
            "Select anthropic with claude-sonnet-5 or claude-opus-5.",
            details={"provider": provider, "model": model},
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
    for name in ("skills", "mcp_servers"):
        value = getattr(options, name)
        if value is not None and (not isinstance(value, list) or value):
            raise invalid(
                name,
                "the configured sources cannot be honored.",
                f"Remove {name} from this agent's options.",
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
    selected_extra = extra.get(provider, {})
    for name in selected_extra.keys() & {
        "model",
        "messages",
        "system",
        "tools",
        "stream",
        "previous_response_id",
    }:
        raise invalid(
            f"extra_request_params.{provider}.{name}",
            "this setting changes contracted conversation behavior.",
            f"Remove {name} from extra_request_params.",
        )
    return ResolvedConfig(
        provider,
        model,
        options.instructions,
        tuple(tools),
        options.approvals,
        Path(storage).expanduser(),
        workspace,
        copy.deepcopy(selected_extra),
        os.environ.get("ANTHROPIC_API_KEY"),
        os.environ.get("ANTHROPIC_BASE_URL"),
    )


def select(model: str | None, ceiling: str) -> str:
    if model is None:
        return ceiling
    prices = {"claude-sonnet-5": 1, "claude-opus-5": 2}
    if not isinstance(model, str) or model not in prices or prices[model] > prices[ceiling]:
        raise AgentError(
            "selector_rejected",
            "selection",
            "The requested model exceeds or cannot refine the configured ceiling.",
            f"Select {ceiling} or a supported less expensive model.",
            details={"model": model},
        )
    return model


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
    if value.persistence == "durable":
        raise AgentError(
            "engine_unavailable",
            "lifecycle",
            "Durable persistence cannot be provided by this installation.",
            "Use explicit ephemeral persistence for disposable conversations or install a distribution with durable storage.",
            details={"field": "persistence"},
        )
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

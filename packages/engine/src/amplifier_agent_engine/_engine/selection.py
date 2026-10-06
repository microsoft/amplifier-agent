"""A session's selection: the provider connection it names and the conversation it carries.

agent-interface.v1 section 5. A session may name a provider other than the agent's; its
connection reads the agent's environment, as discovery does, and its failures name what
to fix rather than failing construction.
"""

from __future__ import annotations

import copy
from dataclasses import replace
from typing import Any

from amplifier_agent_engine._engine.configuration import ResolvedConfig, invalid
from amplifier_agent_engine._engine.discovery import credential_remedy, installed
from amplifier_agent_engine._engine.provider_connections import snapshot
from amplifier_agent_engine._engine.provider_policy import PROVIDERS, settings
from amplifier_agent_engine._records import AgentError

# Content blocks only the provider that produced them can replay.
REASONING_BLOCKS = frozenset({"thinking", "redacted_thinking", "reasoning"})
# Fields carrying a provider's sealed reasoning state on otherwise portable blocks.
SEALED_FIELDS = frozenset({"signature", "encrypted_content", "thought_signature"})


def unavailable(provider: str) -> AgentError:
    return AgentError(
        "engine_unavailable",
        "lifecycle",
        f"The {provider} provider is not installed.",
        f"Install amplifier-agent[{provider}], then select it again.",
        details={"provider": provider},
    )


def check(provider: Any, model: Any) -> None:
    """Refuse a selection that names no registered provider or no model."""
    if not isinstance(provider, str) or provider not in PROVIDERS:
        raise invalid(
            "provider",
            f"{provider!r} is not a registered provider.",
            "Use one of: " + ", ".join(sorted(PROVIDERS)) + ".",
        )
    if not isinstance(model, str) or not model:
        raise invalid("model", "expected one nonempty string.", "Name a model the provider serves.")


def configure(base: ResolvedConfig, provider: str, model: str) -> ResolvedConfig:
    """The agent's configuration with ``provider`` and ``model`` selected."""
    if provider == base.provider:
        return base if model == base.model else replace(base, model=model)
    if not installed(provider):
        raise unavailable(provider)
    connection = snapshot(provider, base.environment)
    return replace(
        base,
        provider=provider,
        model=model,
        extra_request_params=settings(provider, base.provider_settings.get(provider, {})),
        api_key=connection.get("api_key"),
        base_url=connection.get("base_url"),
        connection=connection,
    )


def connected(factory: Any) -> Any:
    """Wrap a provider factory so a connection it refuses fails ``provider_failed``."""

    async def build(config: ResolvedConfig, coordinator: Any) -> Any:
        if not installed(config.provider):
            raise unavailable(config.provider)
        try:
            return await factory(config, coordinator)
        except AgentError as exc:
            if exc.code != "engine_unavailable":
                raise
            raise failed(config, exc.message) from None
        except Exception as exc:
            raise failed(config, f"The {config.provider} provider could not be loaded: {exc}") from None

    return build


def failed(config: ResolvedConfig, message: str) -> AgentError:
    remedy = credential_remedy(config.provider, config.environment)
    return AgentError(
        "provider_failed",
        "provider",
        message,
        f"{remedy} Pass it in AgentOptions.environment or the process environment when creating the agent,"
        " then select the provider again.",
        details={"provider": config.provider},
    )


def without_reasoning(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The conversation as another provider can replay it: text, tool calls, and results.

    Reasoning blocks and sealed reasoning fields are dropped. Every message and tool call
    id stays, so committed turn boundaries and call/result pairs are unchanged.
    """
    result = []
    for message in copy.deepcopy(messages):
        if message.get("role") == "assistant":
            message.pop("thinking_block", None)
            content = message.get("content")
            if isinstance(content, list):
                message["content"] = [
                    _unsealed(block)
                    for block in content
                    if not (isinstance(block, dict) and block.get("type") in REASONING_BLOCKS)
                ]
            calls = message.get("tool_calls")
            if isinstance(calls, list):
                message["tool_calls"] = [_unsealed(call) for call in calls]
        result.append(message)
    return result


def _unsealed(block: Any) -> Any:
    if not isinstance(block, dict):
        return block
    return {key: value for key, value in block.items() if key not in SEALED_FIELDS}

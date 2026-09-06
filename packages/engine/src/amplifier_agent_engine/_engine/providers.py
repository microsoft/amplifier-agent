"""Construct ecosystem providers behind immutable connection and request policy."""

from __future__ import annotations

import copy
import inspect
import sys
from pathlib import Path
from typing import Any

from .configuration import ResolvedConfig
from .provider_connections import require
from .provider_inputs import context_text, preserve_context, response_roles, text_parts
from .provider_policy import annotate_response, rejected


class _SelectionPolicy:
    async def complete(self, request: Any, **kwargs: Any) -> Any:
        try:
            return await super().complete(request, **kwargs)  # type: ignore[misc]
        except Exception as exc:
            message = str(exc).lower()
            if "model_not_found" in message or (
                getattr(exc, "status_code", None) == 404 and "model" in message
            ):
                raise rejected(kwargs.get("model") or getattr(request, "model", None)) from exc
            raise


class _NativeResponse(_SelectionPolicy):
    _agent_provider_id: str

    def _convert_to_chat_response(self, response: Any, **kwargs: Any) -> Any:
        converted = super()._convert_to_chat_response(response, **kwargs)  # type: ignore[misc]
        return annotate_response(
            converted, response, self._agent_provider_id, kwargs.get("model", "")
        )


class _ResponsesPolicy(_NativeResponse):
    def _convert_messages(self, messages: list[dict[str, Any]]) -> Any:
        replay = copy.deepcopy(messages)
        user_indices = [
            index for index, message in enumerate(replay) if message.get("role") == "user"
        ]
        boundary = user_indices[-2] if len(user_indices) > 1 else 0
        budget = 131072
        for index in range(len(replay) - 1, -1, -1):
            message = replay[index]
            content = message.get("content")
            if not isinstance(content, list):
                continue
            kept = []
            for block in reversed(content):
                if isinstance(block, dict) and block.get("type") == "thinking":
                    size = len(str(block).encode("utf-8"))
                    if index < boundary or size > budget:
                        continue
                    budget -= size
                kept.append(block)
            message["content"] = list(reversed(kept))
        return response_roles(replay, super()._convert_messages)  # type: ignore[misc]

    async def complete(self, request: Any, **kwargs: Any) -> Any:
        # Background execution otherwise turns retention on implicitly for some models.
        kwargs["background"] = bool(self.extra_request_params.get("background", False))  # type: ignore[attr-defined]
        return await super().complete(preserve_context(request, native_roles=True), **kwargs)  # type: ignore[misc]


def _responses_client(api_key: str, base_url: str) -> Any:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=api_key, base_url=base_url, max_retries=0)
    original: Any = client.responses.create
    supported = inspect.signature(original).parameters

    async def create(**kwargs: Any) -> Any:
        # Preserve newer API fields when another ecosystem package pins an older SDK.
        extra = kwargs.pop("extra_body", None) or {}
        for key in list(kwargs):
            if key not in supported:
                extra[key] = kwargs.pop(key)
        return await original(**kwargs, **({"extra_body": extra} if extra else {}))

    client.responses.create = create
    return client


async def create_provider(config: ResolvedConfig, coordinator: Any) -> Any:
    name, connection = config.provider, config.connection
    params = {
        "default_model": config.model,
        "max_retries": 0,
        "extra_request_params": copy.deepcopy(config.extra_request_params),
    }
    key, url = config.api_key, config.base_url
    if name in {"anthropic", "openai", "gemini"}:
        credential = {
            "anthropic": "ANTHROPIC_API_KEY",
            "openai": "OPENAI_API_KEY",
            "gemini": "GOOGLE_API_KEY or GEMINI_API_KEY",
        }[name]
        require(key, name, f"Set {credential} before constructing the agent.")
    if name == "anthropic":
        from amplifier_module_provider_anthropic import AnthropicProvider

        class AnthropicAdapter(_NativeResponse, AnthropicProvider):
            _agent_provider_id = "anthropic"

            async def complete(self, request: Any, **kwargs: Any) -> Any:
                return await super().complete(
                    preserve_context(request, native_roles=False), **kwargs
                )

            def _convert_to_chat_response(self, response: Any, **kwargs: Any) -> Any:
                if getattr(response, "stop_reason", None) is None:
                    raise RuntimeError("Anthropic response ended without a terminal stop reason.")
                return super()._convert_to_chat_response(response, **kwargs)

        return AnthropicAdapter(
            api_key=key,
            coordinator=coordinator,
            config={
                **params,
                "base_url": url,
                "fallback_on_overload": False,
                "refusal_fallback_enabled": False,
                "persist_fallback_state": False,
                "rate_limit_state_path": "",
            },
        )
    if name == "openai":
        from amplifier_module_provider_openai import OpenAIProvider

        class OpenAIAdapter(_ResponsesPolicy, OpenAIProvider):
            _agent_provider_id = "openai"

        return OpenAIAdapter(
            api_key=key,
            coordinator=coordinator,
            config={**params, "reasoning_replay_scope": "all"},
            client=_responses_client(key or "", url or ""),
        )
    if name == "gemini":
        from amplifier_module_provider_gemini import GeminiProvider
        from google import genai
        from google.genai.types import HttpOptions, HttpRetryOptions

        from .gemini_stream import preserve_function_signatures

        class GeminiAdapter(_NativeResponse, GeminiProvider):
            _agent_provider_id = "gemini"
            _agent_native_model: str | None = None

            async def complete(self, request: Any, **kwargs: Any) -> Any:
                self._agent_native_model = None
                return await super().complete(
                    preserve_context(request, native_roles=False), **kwargs
                )

            def _convert_to_chat_response(self, response: Any, **kwargs: Any) -> Any:
                actual = getattr(response, "model_version", None) or self._agent_native_model
                if actual:
                    kwargs["model"] = actual
                return super()._convert_to_chat_response(response, **kwargs)

            def _convert_messages(self, messages: list[dict[str, Any]]) -> Any:
                replay = copy.deepcopy(messages)
                for message in replay:
                    content = message.get("content")
                    if not isinstance(content, list):
                        continue
                    signatures = {
                        block.get("id"): block["signature"]
                        for block in content
                        if isinstance(block, dict)
                        and block.get("type") == "tool_call"
                        and block.get("signature")
                    }
                    for call in message.get("tool_calls") or []:
                        if call.get("id") in signatures:
                            call["signature"] = signatures[call["id"]]
                return super()._convert_messages(replay)

        provider = GeminiAdapter(api_key=key, coordinator=coordinator, config=params)
        provider._client = genai.Client(
            api_key=key,
            vertexai=False,
            http_options=HttpOptions(base_url=url, retry_options=HttpRetryOptions(attempts=1)),
        )
        preserve_function_signatures(
            provider._client, lambda model: setattr(provider, "_agent_native_model", model)
        )
        return provider
    if name == "azure-openai":
        from amplifier_module_provider_azure_openai import _create_azure_provider
        from amplifier_module_provider_openai import OpenAIProvider

        require(url, name, "Set AZURE_OPENAI_ENDPOINT before constructing the agent.")
        credential = None
        token_provider: Any = None
        if not key:
            from azure.identity.aio import ClientSecretCredential, ManagedIdentityCredential

            if all(connection.get(k) for k in ("tenant_id", "client_id", "client_secret")):
                credential = ClientSecretCredential(
                    connection["tenant_id"], connection["client_id"], connection["client_secret"]
                )
            else:
                credential = ManagedIdentityCredential(client_id=connection.get("client_id"))

            async def acquire_token() -> str:
                token = await credential.get_token("https://cognitiveservices.azure.com/.default")
                return token.token

            token_provider = acquire_token

        class AzureAdapter(_ResponsesPolicy, OpenAIProvider):
            _agent_provider_id = "azure-openai"

        provider = _create_azure_provider(
            AzureAdapter,
            api_key=key,
            base_url=(url or "").rstrip("/") + "/openai/v1/",
            token_provider=token_provider,
            config={**params, "reasoning_replay_scope": "all"},
            coordinator=coordinator,
        )
        if credential is not None:
            original_close = provider.close

            async def close() -> None:
                try:
                    await original_close()
                finally:
                    await credential.close()

            provider.close = close
        return provider
    if name == "ollama":
        from amplifier_module_provider_ollama import OllamaProvider

        class OllamaAdapter(_SelectionPolicy, OllamaProvider):
            async def complete(self, request: Any, **kwargs: Any) -> Any:
                adapted = preserve_context(request, native_roles=False)
                for message in adapted.messages:
                    parts = text_parts(message.content)
                    if parts is not None and len(parts) > 1:
                        message.content = context_text(message.role, parts)
                return await super().complete(adapted, **kwargs)

            def _build_streaming_response(
                self,
                content: str,
                thinking: str,
                accumulated_tool_calls: list[dict[str, Any]],
                final_chunk: dict[str, Any] | None,
                include_thinking: bool,
            ) -> Any:
                response = super()._build_streaming_response(
                    content, thinking, accumulated_tool_calls, final_chunk, include_thinking
                )
                native = final_chunk or {}
                usage = (
                    response.usage.model_copy(
                        update={
                            "input_tokens": native.get("prompt_eval_count"),
                            "output_tokens": native.get("eval_count"),
                        }
                    )
                    if response.usage
                    else None
                )
                return response.model_copy(
                    update={"usage": usage, "agent_actual_model": native.get("model")}
                )

        return OllamaAdapter(
            host=url, api_key=key, config={**params, "auto_pull": False}, coordinator=coordinator
        )
    if name == "vllm":
        from amplifier_module_provider_vllm import VLLMProvider

        class VLLMAdapter(_ResponsesPolicy, VLLMProvider):
            _agent_provider_id = "vllm"

        return VLLMAdapter(
            base_url=url,
            api_key=key or "not-needed",
            config={**params, "enable_state": False},
            coordinator=coordinator,
            client=_responses_client(key or "not-needed", url or ""),
        )
    if name == "chat-completions":
        from amplifier_module_provider_chat_completions import ChatCompletionsProvider
        from openai import AsyncOpenAI

        require(url, name, "Set CHAT_COMPLETIONS_BASE_URL before constructing the agent.")

        class ChatCompletionsAdapter(_SelectionPolicy, ChatCompletionsProvider):
            def _convert_messages_to_wire(self, messages: list[Any]) -> list[dict[str, Any]]:
                result = []
                for message in messages:
                    parts = text_parts(message.content)
                    if (
                        parts is not None
                        and not getattr(message, "tool_calls", None)
                        and message.role != "tool"
                    ):
                        result.append({"role": message.role, "content": parts})
                    else:
                        result.extend(super()._convert_messages_to_wire([message]))
                return result

        params["extra_request_params"] = {"store": False, **params["extra_request_params"]}
        provider = ChatCompletionsAdapter(config=params, coordinator=coordinator)
        provider._base_url = url or ""
        provider._api_key = key or "not-needed"
        provider._client = AsyncOpenAI(base_url=url, api_key=key or "not-needed", max_retries=0)
        return provider
    if name == "openai-chatgpt":
        return _chatgpt(config, coordinator, params)
    if name == "github-copilot":
        return await _copilot(config, coordinator, params)
    raise AssertionError(f"Unregistered provider {name}")


def _chatgpt(config: ResolvedConfig, coordinator: Any, params: dict[str, Any]) -> Any:
    from amplifier_core.llm_errors import AuthenticationError
    from amplifier_module_provider_openai_chatgpt.oauth import is_token_valid, refresh_tokens
    from amplifier_module_provider_openai_chatgpt.provider import ChatGPTProvider

    tokens: Any = copy.deepcopy(config.connection.get("tokens"))
    require(
        tokens,
        config.provider,
        "Complete ChatGPT OAuth login outside the agent before constructing it.",
    )
    account = tokens.get("account_id")
    path = config.connection["token_path"]

    class Provider(_SelectionPolicy, ChatGPTProvider):
        def _build_payload(self, request: Any, *, default_model: str | None = None) -> Any:
            from amplifier_core.message_models import Message

            adapted = preserve_context(request, native_roles=True)
            build = super()._build_payload
            payload = {}

            def convert(messages: list[dict[str, Any]]) -> Any:
                adapted.messages = [Message.model_validate(message) for message in messages]
                payload.update(build(adapted, default_model=default_model))
                return payload["input"]

            payload["input"] = response_roles(
                [message.model_dump() for message in adapted.messages], convert
            )
            return payload

        async def _ensure_valid_tokens(self) -> None:
            if is_token_valid(self._tokens):
                return
            refresh = (self._tokens or {}).get("refresh_token")
            if not refresh:
                raise AuthenticationError(
                    "The selected ChatGPT account needs login.", provider="openai-chatgpt"
                )
            refreshed = await refresh_tokens(refresh, path=path)
            if not refreshed or refreshed.get("account_id") != account:
                raise AuthenticationError(
                    "The selected ChatGPT account needs login.", provider="openai-chatgpt"
                )
            self._tokens = refreshed

    params = {key: value for key, value in params.items() if key != "max_retries"}
    return Provider(
        config={**params, "login_on_mount": False}, coordinator=coordinator, tokens=tokens
    )


async def _copilot(config: ResolvedConfig, coordinator: Any, params: dict[str, Any]) -> Any:
    from amplifier_module_provider_github_copilot import GitHubCopilotProvider
    from amplifier_module_provider_github_copilot.sdk_adapter.client import (
        CopilotClientWrapper,
        scrub_sdk_env,
    )
    from copilot import CopilotClient, RuntimeConnection

    bundled = Path(getattr(sys, "_MEIPASS", "")) / "copilot_runtime" / "copilot"
    connection = RuntimeConnection.for_stdio(path=str(bundled)) if bundled.is_file() else None
    client = CopilotClient(
        github_token=config.api_key,
        env=scrub_sdk_env(config.connection["environment"]),
        base_directory=config.connection["home"],
        mode="copilot-cli",
        connection=connection,
    )
    try:
        await client.start()
        auth = await client.get_auth_status()
        require(
            auth.isAuthenticated,
            config.provider,
            "Set a Copilot token or complete SDK login before constructing the agent.",
        )
    except BaseException:
        await client.stop()
        raise

    class Provider(_SelectionPolicy, GitHubCopilotProvider):
        async def close(self) -> None:
            try:
                await super().close()
            finally:
                await client.stop()

    return Provider(
        config=params, coordinator=coordinator, client=CopilotClientWrapper(sdk_client=client)
    )

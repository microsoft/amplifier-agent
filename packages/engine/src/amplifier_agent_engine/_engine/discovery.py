"""Provider discovery: which providers this engine accepts and which models a provider lists.

Neither operation needs an agent. Both read the host process's environment with the
caller's `environment` applied on top, as an agent's provider connection does, and no
host configuration.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import contextlib
import importlib
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any, Literal
from urllib.parse import urlparse

from amplifier_agent_engine._engine.configuration import ResolvedConfig, agent_environment, invalid, record
from amplifier_agent_engine._engine.provider_connections import (
    CHATGPT_TOKEN_PATH,
    CREDENTIAL_VARIABLES,
    ENDPOINTS,
    snapshot,
)
from amplifier_agent_engine._engine.provider_policy import PROVIDERS
from amplifier_agent_engine._records import AgentError, DiscoveryOptions, ModelRecord, ProviderRecord

# Seconds one listing may take, from provider construction to its last response.
LIST_MODELS_SECONDS = 15.0

# Seconds `gh auth token` may take to report a GitHub CLI login.
GH_AUTH_SECONDS = 5.0

# Stable listing order, with each provider's readable name.
_PROVIDERS: tuple[tuple[str, str], ...] = (
    ("anthropic", "Anthropic"),
    ("openai", "OpenAI"),
    ("azure-openai", "Azure OpenAI"),
    ("ollama", "Ollama"),
    ("github-copilot", "GitHub Copilot"),
    ("openai-chatgpt", "OpenAI ChatGPT"),
    ("chat-completions", "Chat Completions"),
    ("gemini", "Google Gemini"),
    ("vllm", "vLLM"),
)

_EXTRAS = {"github-copilot": ("amplifier_module_provider_github_copilot", "copilot")}
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


async def list_providers(options: DiscoveryOptions | None = None) -> list[ProviderRecord]:
    environment = _environment(options)
    records = []
    for provider, name in _PROVIDERS:
        credentials = _credentials(provider, environment)
        # The Copilot SDK signs in with the GitHub CLI's saved login when no token variable is set.
        if provider == "github-copilot" and credentials == "missing" and await _gh_login(environment):
            credentials = "found"
        records.append(
            ProviderRecord(provider, name, _installed(provider), credentials, list(CREDENTIAL_VARIABLES[provider]))
        )
    return records


def _gh_executable(environment: dict[str, str]) -> str | None:
    return shutil.which("gh", path=environment.get("PATH"))


async def _gh_login(environment: dict[str, str]) -> bool:
    """Whether `gh auth token` reports a login; the token itself is discarded unread."""
    executable = _gh_executable(environment)
    if executable is None:
        return False
    try:
        process = await asyncio.create_subprocess_exec(
            executable,
            "auth",
            "token",
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=environment,
        )
    except OSError:
        return False
    try:
        output, _ = await asyncio.wait_for(process.communicate(), GH_AUTH_SECONDS)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        await process.wait()
        return False
    present = process.returncode == 0 and bool(output.strip())
    del output
    return present


async def list_models(provider: str, options: DiscoveryOptions | None = None) -> list[ModelRecord]:
    if not isinstance(provider, str) or provider not in PROVIDERS:
        raise invalid(
            "provider",
            f"{provider!r} is not a registered provider.",
            "Use one of: " + ", ".join(name for name, _ in _PROVIDERS) + ".",
        )
    environment = _environment(options)
    if not _installed(provider):
        raise AgentError(
            "engine_unavailable",
            "lifecycle",
            f"The {provider} provider is not installed.",
            f"Install amplifier-agent[{provider}].",
            details={"provider": provider},
        )
    if provider == "azure-openai":
        if not environment.get("AZURE_OPENAI_ENDPOINT"):
            raise _failed(provider, "The azure-openai provider has no endpoint.", "Set AZURE_OPENAI_ENDPOINT.")
        # Azure models are the caller's named deployments.
        return []
    # A Copilot SDK login is invisible here, so only the provider can say it is missing.
    if provider != "github-copilot" and _credentials(provider, environment) == "missing":
        raise _failed(
            provider, f"The {provider} provider has no credentials.", _credential_remedy(provider, environment)
        )
    if provider == "chat-completions" and not environment.get("CHAT_COMPLETIONS_BASE_URL"):
        raise _failed(
            provider,
            "The chat-completions provider has no endpoint.",
            "Set CHAT_COMPLETIONS_BASE_URL to the OpenAI-compatible endpoint.",
        )
    try:
        listed = await asyncio.wait_for(_listing(provider, environment), LIST_MODELS_SECONDS)
    except TimeoutError:
        raise _failed(
            provider,
            f"The {provider} provider did not list its models within {LIST_MODELS_SECONDS:g} seconds.",
            _endpoint_remedy(provider, environment),
            retryable=True,
        ) from None
    return [_model(info) for info in listed]


def _environment(options: DiscoveryOptions | None) -> dict[str, str]:
    if options is None:
        return dict(os.environ)
    record(options, DiscoveryOptions, "options")
    return agent_environment(options.environment, dict(os.environ))


def _installed(provider: str) -> bool:
    try:
        for module in _EXTRAS.get(provider, ()):
            importlib.import_module(module)
    except ImportError:
        return False
    return True


def _credentials(provider: str, environment: dict[str, str]) -> Literal["found", "missing", "not_required"]:
    present = any(environment.get(name) for name in CREDENTIAL_VARIABLES[provider])
    if provider == "openai-chatgpt":
        from amplifier_module_provider_openai_chatgpt.oauth import load_tokens

        return "found" if load_tokens(path=str(Path(CHATGPT_TOKEN_PATH).expanduser())) else "missing"
    if provider == "azure-openai":
        return "found" if present and environment.get("AZURE_OPENAI_ENDPOINT") else "missing"
    if provider in {"vllm", "chat-completions"}:
        return "found" if present else "not_required"
    if provider == "ollama" and _local(_ollama_host(environment)):
        return "not_required"
    return "found" if present else "missing"


def _ollama_host(environment: dict[str, str]) -> str:
    return environment.get("OLLAMA_HOST") or "http://localhost:11434"


def _local(host: str) -> bool:
    return urlparse(host if "://" in host else f"http://{host}").hostname in _LOCAL_HOSTS


def _failed(provider: str, message: str, remedy: str, retryable: bool = False, **details: Any) -> AgentError:
    return AgentError(
        "provider_failed", "provider", message, remedy, retryable=retryable, details={"provider": provider, **details}
    )


def _credential_remedy(provider: str, environment: dict[str, str]) -> str:
    if provider == "openai-chatgpt":
        return f"Complete ChatGPT OAuth login outside the agent so {CHATGPT_TOKEN_PATH} holds current tokens."
    if provider == "github-copilot":
        return "Set " + ", ".join(CREDENTIAL_VARIABLES[provider]) + ", or run `gh auth login`."
    if provider == "ollama":
        host = _ollama_host(environment)
        return f"Set OLLAMA_API_KEY for the server at {host}, or set OLLAMA_HOST to a local server."
    return "Set " + " or ".join(CREDENTIAL_VARIABLES[provider]) + "."


def _endpoint_remedy(provider: str, environment: dict[str, str]) -> str:
    if provider not in ENDPOINTS:
        return f"Check network access to the {provider} service, then retry."
    variable = ENDPOINTS[provider][0]
    url = snapshot(provider, environment).get("base_url") or "the configured endpoint"
    return f"Check that {url} is reachable, or set {variable} to a reachable endpoint."


async def _listing(provider: str, environment: dict[str, str]) -> list[Any]:
    from amplifier_core.llm_errors import AccessDeniedError, AuthenticationError

    from amplifier_agent_engine._engine.providers import create_provider

    connection = snapshot(provider, environment)
    config = ResolvedConfig(
        provider,
        "",
        None,
        (),
        None,
        Path(),
        {},
        connection.get("api_key"),
        connection.get("base_url"),
        connection,
        environment=environment,
    )
    instance: Any = None
    try:
        instance = await create_provider(config, None)
        return await _models(provider, instance)
    except AgentError as exc:
        if exc.code != "engine_unavailable":
            raise
        # Endpoints are checked before construction, so a refused connection is a credential.
        raise _failed(provider, exc.message, _credential_remedy(provider, environment)) from None
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # Listings that report failures themselves surface the SDK's own HTTP errors.
        if isinstance(exc, (AuthenticationError, AccessDeniedError)) or getattr(exc, "status_code", None) in (401, 403):
            remedy = _credential_remedy(provider, environment)
            if _credentials(provider, environment) == "found":
                remedy = remedy.replace("Set ", "Check ", 1)
            raise _failed(
                provider, f"The {provider} provider rejected its credentials.", remedy, provider_message=str(exc)
            ) from None
        raise _failed(
            provider,
            f"The {provider} provider could not list its models.",
            _endpoint_remedy(provider, environment),
            retryable=bool(getattr(exc, "retryable", False)),
            provider_message=str(exc),
        ) from None
    finally:
        if instance is not None and callable(getattr(instance, "close", None)):
            # A close failure does not change what the provider listed.
            with contextlib.suppress(Exception):
                await instance.close()


async def _models(provider: str, instance: Any) -> list[Any]:
    if provider == "openai-chatgpt":
        from amplifier_module_provider_openai_chatgpt import models

        # The provider's own listing substitutes a built-in catalog on failure.
        await instance._ensure_valid_tokens()
        tokens = instance._tokens
        entries = await models.fetch_models(access_token=tokens["access_token"], account_id=tokens["account_id"])
        if not entries:
            raise RuntimeError("The ChatGPT model catalog returned no usable entries.")
        return models.to_model_infos(entries)
    if provider == "github-copilot":
        from amplifier_module_provider_github_copilot.models import fetch_and_map_models

        # The provider's own listing substitutes a disk cache on failure.
        listed, _ = await fetch_and_map_models(instance._client)
        return listed
    if provider == "ollama":
        # The provider's own listing reports a failure as an empty list.
        return await _observed(instance.list_models, instance.client, "list")
    if provider == "chat-completions":
        # The provider's own listing reports a failure as the configured model alone.
        instance._filtered = False
        return await _observed(instance.list_models, instance.client.models, "list")
    if provider == "anthropic":
        instance.filtered = False
    if provider == "openai":
        instance.hide_dated_models = False
    return await instance.list_models()


async def _observed(listing: Callable[[], Awaitable[Any]], target: Any, name: str) -> Any:
    """Run a provider listing that hides request failures, raising the failure it hid."""
    original = getattr(target, name)
    failures: list[Exception] = []

    async def observed(*args: Any, **kwargs: Any) -> Any:
        try:
            return await original(*args, **kwargs)
        except Exception as exc:
            failures.append(exc)
            raise

    setattr(target, name, observed)
    try:
        result = await listing()
    finally:
        setattr(target, name, original)
    if failures:
        raise failures[-1]
    return result


def _model(info: Any) -> ModelRecord:
    def limit(value: Any) -> int | None:
        return value if type(value) is int and value > 0 else None

    identifier = str(info.id)
    return ModelRecord(
        identifier,
        str(getattr(info, "display_name", None) or identifier),
        limit(getattr(info, "context_window", None)),
        limit(getattr(info, "max_output_tokens", None)),
    )

"""Capture provider connection settings without changing process environment."""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

from .._records import AgentError


def snapshot(provider: str) -> dict[str, Any]:
    env = dict(os.environ)
    keys = {
        "anthropic": ("ANTHROPIC_API_KEY",),
        "openai": ("OPENAI_API_KEY",),
        "azure-openai": ("AZURE_OPENAI_API_KEY",),
        "ollama": ("OLLAMA_API_KEY",),
        "gemini": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
        "vllm": ("VLLM_API_KEY",),
        "chat-completions": ("CHAT_COMPLETIONS_API_KEY",),
        "github-copilot": (
            "COPILOT_AGENT_TOKEN",
            "COPILOT_GITHUB_TOKEN",
            "GH_TOKEN",
            "GITHUB_TOKEN",
        ),
        "openai-chatgpt": (),
    }
    endpoints = {
        "anthropic": ("ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
        "openai": ("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        "azure-openai": ("AZURE_OPENAI_ENDPOINT", ""),
        "ollama": ("OLLAMA_HOST", "http://localhost:11434"),
        "gemini": ("GOOGLE_GEMINI_BASE_URL", "https://generativelanguage.googleapis.com"),
        "vllm": ("VLLM_BASE_URL", "http://localhost:8000/v1"),
        "chat-completions": ("CHAT_COMPLETIONS_BASE_URL", ""),
    }
    result: dict[str, Any] = {
        "api_key": next((env[key] for key in keys[provider] if env.get(key)), None),
    }
    if provider in endpoints:
        key, default = endpoints[provider]
        result["base_url"] = env.get(key) or default
    if provider == "azure-openai":
        result["tenant_id"] = env.get("AZURE_TENANT_ID")
        result["client_id"] = env.get("AZURE_CLIENT_ID")
        result["client_secret"] = env.get("AZURE_CLIENT_SECRET")
    if provider == "github-copilot":
        result["environment"] = env
        result["home"] = str(Path("~/.amplifier/provider-github-copilot").expanduser())
    if provider == "openai-chatgpt":
        from amplifier_module_provider_openai_chatgpt.oauth import load_tokens

        path = str(Path("~/.amplifier/openai-chatgpt-oauth.json").expanduser())
        result["token_path"] = path
        result["tokens"] = copy.deepcopy(load_tokens(path=path))
    return result


def require(value: Any, provider: str, remedy: str) -> None:
    if not value:
        raise AgentError(
            "engine_unavailable",
            "lifecycle",
            f"The {provider} connection is not configured.",
            remedy,
            details={"provider": provider},
        )

"""Bearer-token auth dependency.

POC scope: single shared secret in env. Multi-tenant key management is a v2
concern.
"""

from fastapi import Depends, HTTPException, Request, status

from amplifier_agent_http._config import ServerConfig, load_config


def _get_config(request: Request) -> ServerConfig:
    cfg = getattr(request.app.state, "config", None)
    if cfg is None:
        # Defensive fallback: should be set by lifespan, but never crash on
        # missing state. Reload from env.
        cfg = load_config()
    return cfg


def require_bearer(
    request: Request,
    config: ServerConfig = Depends(_get_config),  # noqa: B008 - FastAPI dependency injection
) -> None:
    """Reject any request without a valid Authorization: Bearer header.

    Matches the shape OpenAI's API expects; opencode's @ai-sdk/openai-compatible
    provider will send `Authorization: Bearer <apiKey>` per its config.
    """
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"message": "Missing or malformed Authorization header", "type": "invalid_request_error"}},
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = auth[7:].strip()
    if token != config.api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"message": "Invalid API key", "type": "invalid_request_error"}},
            headers={"WWW-Authenticate": "Bearer"},
        )

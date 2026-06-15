"""GET /v1/models -- advertise the single amplifier model.

POC ships exactly one model. Multi-persona / model name routing is in the v2
backlog.
"""

import time

from fastapi import APIRouter, Depends, Request

from amplifier_agent_http._auth import require_bearer

router = APIRouter()


@router.get("/v1/models", dependencies=[Depends(require_bearer)])
async def list_models(request: Request) -> dict:
    """Return the model list in OpenAI shape.

    @ai-sdk/openai-compatible doesn't actually call this endpoint -- opencode
    declares the model in opencode.json and uses it directly. We expose it
    anyway for compatibility with curl-based smoke tests and any host that
    does discovery.
    """
    config = request.app.state.config
    return {
        "object": "list",
        "data": [
            {
                "id": config.model_id,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "amplifier-agent",
            }
        ],
    }

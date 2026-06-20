"""GET /v1/models -- advertise the configured provider's model list.

Returns the OpenAI-shape model list plus optional metadata fields
(``display_name``, ``limit``, ``reasoning``, ``capabilities``) so
client-side adapters can populate their UI without requiring a static
model list in client config.

Source of truth is the provider module's own ``list_models()`` --
loaded once at lifespan via ``list_provider_models()`` (the same
function the CLI's ``amplifier-agent models list`` command uses) and
stashed on ``app.state.available_models``. Single source of truth across
both faces.

Standard OpenAI clients see only ``id``, ``object``, ``created``,
``owned_by`` (extra fields are ignored as per the OpenAI spec's
open-by-default policy). Amplifier-aware clients (the opencode plugin,
etc.) read the extension fields to populate their model picker UI.
"""

import time
from typing import Any

from fastapi import APIRouter, Depends, Request

from amplifier_agent_http._auth import require_bearer

router = APIRouter()


def _coerce_model_dict(m: Any) -> dict[str, Any]:
    """Coerce a provider ModelInfo / mapping into a plain JSON-able dict.

    Mirrors the helper in ``amplifier_agent_cli/admin/models.py:_model_dump``
    so HTTP and CLI normalize provider model objects the same way.
    """
    if hasattr(m, "model_dump"):
        return m.model_dump()
    return dict(m)


def _to_openai_entry(model_obj: Any, *, now: int) -> dict[str, Any]:
    """Convert one provider model object to an OpenAI ``/v1/models`` entry.

    Standard fields (``id``, ``object``, ``created``, ``owned_by``) are
    always present. Metadata fields are surfaced when the provider's
    ``ModelInfo`` carries them:

      - ``display_name``  <- ModelInfo.display_name (fallback: model id)
      - ``limit``         <- ModelInfo.context_window + max_output_tokens
      - ``reasoning``     <- ``"thinking"`` in ModelInfo.capabilities
      - ``capabilities``  <- ModelInfo.capabilities (verbatim)
      - ``defaults``      <- ModelInfo.defaults (verbatim, when present)
    """
    d = _coerce_model_dict(model_obj)
    entry: dict[str, Any] = {
        "id": d.get("id"),
        "object": "model",
        "created": now,
        "owned_by": "amplifier-agent",
    }
    # _provider is set by app.py lifespan when iterating KNOWN_PROVIDERS.
    # Surface it so clients can see which provider serves each model. The
    # field is non-standard but additive (standard OpenAI clients ignore
    # unknown fields).
    if "_provider" in d:
        entry["_provider"] = d["_provider"]
    if "display_name" in d:
        entry["display_name"] = d["display_name"]
    if "context_window" in d or "max_output_tokens" in d:
        entry["limit"] = {
            "context": d.get("context_window") or 0,
            "output": d.get("max_output_tokens") or 0,
        }
    caps = d.get("capabilities") or []
    if caps:
        entry["capabilities"] = caps
        entry["reasoning"] = "thinking" in caps
    if "defaults" in d:
        entry["defaults"] = d["defaults"]
    return entry


@router.get("/v1/models", dependencies=[Depends(require_bearer)])
async def list_models(request: Request) -> dict:
    """Return the provider's model list in OpenAI shape + metadata extensions.

    Reads from ``app.state.available_models``, which the lifespan loads
    once via ``list_provider_models()`` -- the same CLI-side helper that
    backs ``amplifier-agent models list``. No drift between the two
    surfaces; if the CLI sees a model, /v1/models does too.

    Falls back to a minimal single-model placeholder (the configured
    ``--model-id``) when the lifespan probe couldn't load the list (no
    credentials, provider module not installed, network error, etc.).
    The CLI handles this case the same way -- empty list means "could
    not enumerate" rather than "no models exist".
    """
    available = getattr(request.app.state, "available_models", None) or []
    now = int(time.time())

    if available:
        return {
            "object": "list",
            "data": [_to_openai_entry(m, now=now) for m in available],
        }

    # Fallback: advertise just the configured model_id when list_models
    # failed at lifespan. Preserves the minimum-viable shape so clients
    # that hit /v1/models for smoke tests don't get an empty list.
    config = request.app.state.config
    return {
        "object": "list",
        "data": [
            {
                "id": config.model_id,
                "object": "model",
                "created": now,
                "owned_by": "amplifier-agent",
            }
        ],
    }

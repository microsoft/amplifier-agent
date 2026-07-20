"""GET /v1/modes -- advertise the shipped modes.

Enumerates every discovered mode -- for the built-ins that is
``{brainstorm, plan}``. Unlike skills there is no user-invocable filter: modes
are activated per turn via ``--mode`` (CLI) / the wire, and all discovered modes
are listable.

Source of truth is ``amplifier_agent_lib.resources.list_modes`` -- the same
helper the CLI's ``amplifier-agent modes list`` command uses. The lifespan
calls it once (after the bundle is prepared, so the discovery packages are
importable) and stashes the result on ``app.state.available_modes``. Single
source of truth across both faces: if the CLI lists a mode, /v1/modes does too.
The e2e ``test_modes_parity`` case asserts exactly this.

The response mirrors the OpenAI-style list envelope used by ``/v1/models``
(``{"object": "list", "data": [...]}``) so both surfaces are shape-consistent.
The e2e harness' ``names()`` helper extracts the name set from either a bare
list or this dict-wrapped list, so the CLI (bare list) and HTTP (wrapped) forms
yield identical name sets.
"""

from fastapi import APIRouter, Depends, Request

from amplifier_agent_http._auth import require_bearer

router = APIRouter()


@router.get("/v1/modes", dependencies=[Depends(require_bearer)])
async def list_modes(request: Request) -> dict:
    """Return the shipped modes as a list envelope.

    Reads from ``app.state.available_modes``, which the lifespan populates once
    via ``resources.list_modes(host_config)`` -- the same helper backing
    ``amplifier-agent modes list``. No drift between surfaces.

    Each entry is ``{"name", "description"}``. The harness' ``names()`` reads
    the ``name`` field, so this matches the CLI's bare-list output name set.
    """
    available = getattr(request.app.state, "available_modes", None) or []
    return {"object": "list", "data": list(available)}

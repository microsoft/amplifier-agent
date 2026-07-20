"""GET /v1/skills -- advertise the user-invocable (slash-command) skills.

Enumerates exactly the skills a client may invoke via the ``!amplifier:skill``
sigil -- for the built-ins that is ``{code-review, council}`` (the six council
lens skills are model-invocable and are excluded).

Source of truth is ``amplifier_agent_lib.resources.list_skills`` -- the same
helper the CLI's ``amplifier-agent skills list`` command uses. The lifespan
calls it once (after the bundle is prepared, so the discovery packages are
importable) and stashes the result on ``app.state.available_skills``. Single
source of truth across both faces: if the CLI lists a skill, /v1/skills does
too. The e2e ``test_skills_parity`` case asserts exactly this.

The response mirrors the OpenAI-style list envelope used by ``/v1/models``
(``{"object": "list", "data": [...]}``) so both surfaces are shape-consistent.
The e2e harness' ``names()`` helper extracts the name set from either a bare
list or this dict-wrapped list, so the CLI (bare list) and HTTP (wrapped) forms
yield identical name sets.
"""

from fastapi import APIRouter, Depends, Request

from amplifier_agent_http._auth import require_bearer

router = APIRouter()


@router.get("/v1/skills", dependencies=[Depends(require_bearer)])
async def list_skills(request: Request) -> dict:
    """Return the user-invocable skills as a list envelope.

    Reads from ``app.state.available_skills``, which the lifespan populates
    once via ``resources.list_skills(host_config)`` -- the same helper backing
    ``amplifier-agent skills list``. No drift between surfaces.

    Each entry is ``{"name", "description"}``. The harness' ``names()`` reads
    the ``name`` field, so this matches the CLI's bare-list output name set.
    """
    available = getattr(request.app.state, "available_skills", None) or []
    return {"object": "list", "data": list(available)}

"""E2E: an unknown skill sigil must PASS THROUGH to the model, on both faces.

Contract under test
-------------------
Skills are the deliberate exception to the fail-closed rule that governs modes.

    no sigil                     -> normal execute
    sigil, malformed / no name   -> normal execute, prompt verbatim
    sigil, skill resolves        -> run the skill
    sigil, resolution fails      -> normal execute, prompt verbatim

Why skills differ from modes
----------------------------
A mode name arrives through a structured channel (``--mode``, or the
``[amplifier-agent:mode=...]`` directive) and is therefore always deliberate. A skill
sigil arrives inside the user's own prompt text. Rejecting an unrecognized one would
mean refusing to run a turn because of something the user typed, which turns a helpful
shorthand into a trap: type ``!amplifier:skill`` in a sentence about skills and your
turn is refused. Passing it through costs nothing -- the model sees ordinary text and
answers the question.

Why "resolution fails" is one bucket and not four
-------------------------------------------------
There are four distinct underlying failures: the load_skill tool is not mounted, the
tool raised, the skill is not found, and the skill exists but failed to load. They are
NOT distinguishable by a caller today. tool-skills returns ``error={"message": <free
text>}`` with no ``code`` key on every path, malformed skills are silently dropped at
discovery (so they report as "not found"), and the orchestrator flattens any escaped
exception into that same shape. Branching would mean string-matching message prose,
which is not a contract. Collapsing them is therefore the only honest option available
without an upstream change to amplifier-bundle-skills.

Status of these cases
---------------------
GREEN, not RED. Unlike the unknown-MODE suite, these assert behavior the code already
has. They exist to pin the decision: this pass-through is a deliberate contract, not an
accident of the fallback path it happens to share with the genuinely-broken cases. If
someone later "fixes" item 3 by making all unknown names fail closed, these fail and
say why.

Not covered here
----------------
Surfacing a user-visible notice when a sigil fails to resolve. That is still an open
decision (there is no warning channel on either face today: the CLI success envelope
has no field for it and chat-completions is OpenAI-shaped with nowhere to put it).
Add cases here once it is decided.
"""

from __future__ import annotations

import json
import shlex
from uuid import uuid4

import pytest
from framework import dtu

pytestmark = pytest.mark.dtu

# Host-config seeded into every DTU by provisioning (anthropic provider, approval "yes").
_CONFIG = "/root/e2e/host-config.json"

# Marker curl appends after the body so the status is readable from the same stream.
_META = "__META__"

# Family of the model host-config.json selects, so both faces run comparable models.
_MODEL_HINT = "sonnet"

# Asked as the sigil's arguments. Deliberately a question the model will answer in a
# recognizable way, so "the turn really ran" is observable rather than merely inferred
# from a zero exit code.
_QUESTION = "what is 2 plus 2? Reply with just the number."


def _bogus_skill() -> str:
    """A skill name that cannot resolve in any discovery root."""
    return f"e2e-no-such-skill-{uuid4().hex[:12]}"


@pytest.fixture(scope="session")
def model_id(dtu_id: str, server: dict[str, str]) -> str:
    """Resolve a served model id at runtime from GET /v1/models."""
    cmd = f"curl -s -H 'Authorization: Bearer {server['token']}' {server['base_url']}/v1/models"
    result = dtu.exec_json(dtu_id, ["bash", "-lc", cmd])
    assert result.get("exit_code") == 0, f"/v1/models failed: {result.get('stderr')}"
    data = json.loads(result["stdout"])
    models = data.get("data") or []
    assert models, f"no served models: {data}"

    ids = [m["id"] for m in models]
    for candidate in ids:
        if _MODEL_HINT in candidate:
            return candidate
    return ids[0]


def _post_chat(dtu_id: str, base_url: str, token: str, body: dict) -> tuple[str, str]:
    """POST ``body`` to /v1/chat/completions from INSIDE the DTU. Returns (status, raw_body)."""
    payload = json.dumps({**body, "stream": False})
    cmd = (
        f"curl -s -X POST {base_url}/v1/chat/completions "
        f"-H 'Authorization: Bearer {token}' "
        f"-H 'Content-Type: application/json' "
        f"-w '\\n{_META}%{{http_code}}' "
        f"--data-binary {shlex.quote(payload)}"
    )
    result = dtu.exec_json(dtu_id, ["bash", "-lc", cmd])
    assert result.get("exit_code") == 0, f"curl failed: exit={result.get('exit_code')} stderr={result.get('stderr')}"

    raw = result.get("stdout", "")
    raw_body, _, status_line = raw.rpartition(f"\n{_META}")
    return status_line.strip(), raw_body


# --------------------------------------------------------------------------- #
# 1. CLI -- an unresolvable sigil still produces a normal turn.
# --------------------------------------------------------------------------- #


def test_unknown_skill_cli_passes_through(dtu_id: str) -> None:
    """An unresolvable sigil must NOT fail the turn on the CLI face.

    Two assertions, and the second is the one that matters. Exit 0 alone would also be
    satisfied by a turn that silently did nothing. Requiring a non-empty reply proves
    the prompt actually reached the model and was answered, which is what "pass through"
    means.
    """
    bogus = _bogus_skill()
    prompt = f"!amplifier:skill {bogus} {_QUESTION}"

    inner = "amplifier-agent " + " ".join(
        shlex.quote(a) for a in ["run", "-y", "--output", "json", "--config", _CONFIG, prompt]
    )
    result = dtu.exec_json(dtu_id, ["bash", "-lc", inner])

    exit_code = result.get("exit_code")
    assert exit_code == 0, (
        f"an unresolvable skill sigil must not fail the turn (the sigil lives in the user's own "
        f"prompt text, so rejecting it would refuse turns over something the user typed). "
        f"got exit {exit_code}\nstdout:\n{result.get('stdout', '')}\nstderr:\n{result.get('stderr', '')}"
    )

    envelope = json.loads(result.get("stdout", ""))
    reply = envelope.get("reply") or ""
    assert reply.strip(), (
        f"exit was 0 but the envelope carries no reply, so the prompt never reached the model. "
        f"Pass-through means the turn runs normally, not that it quietly no-ops.\n"
        f"envelope:\n{json.dumps(envelope, indent=2)}"
    )


# --------------------------------------------------------------------------- #
# 2. HTTP -- same contract on the wire.
# --------------------------------------------------------------------------- #


def test_unknown_skill_http_passes_through(dtu_id: str, server: dict[str, str], model_id: str) -> None:
    """An unresolvable sigil must yield a normal 200 completion over HTTP.

    The parity case. The two faces now share one dispatcher
    (``skill_dispatch.dispatch_skill_or_execute``), so a divergence here would mean the
    shared seam was bypassed on one side.
    """
    bogus = _bogus_skill()
    status, raw_body = _post_chat(
        dtu_id,
        server["base_url"],
        server["token"],
        {"model": model_id, "messages": [{"role": "user", "content": f"!amplifier:skill {bogus} {_QUESTION}"}]},
    )

    assert status == "200", (
        f"an unresolvable skill sigil must not fail the request; skills pass through by contract "
        f"(unlike modes, which fail closed). got {status!r}\nbody:\n{raw_body}"
    )

    obj = json.loads(raw_body)
    reply = obj["choices"][0]["message"]["content"] or ""
    assert reply.strip(), f"empty assistant reply; the turn did not produce a completion\nbody:\n{raw_body}"

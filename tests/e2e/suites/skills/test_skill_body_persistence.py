"""E2E: does an inline skill's BODY survive past the turn that expanded it?

What is under test
------------------
``!amplifier:skill <name>`` on an INLINE skill (no ``context: fork``) expands the
skill's SKILL.md body and runs it as that turn's prompt. The body is not a one-shot
instruction: a skill body routinely establishes rules, a persona, or a working
contract that the rest of the conversation is supposed to obey. So the question this
module asks is not "did the sigil dispatch?" (that is ``test_sigil_dispatch.py``'s
job) but "is the expanded body still in scope on the NEXT turn?"

The instrument is the ``e2e-memory-probe`` skill. Its body carries two rules:

  Rule 1  answer THIS turn with ``MEMORY-PROBE-ARMED`` and do NOT write the token.
  Rule 2  when later asked "what is the probe token?", answer with the token.

Rule 2 is unanswerable from anything except the skill body. The token string
``MEMORY-PROBE-TOKEN-J4X8`` appears nowhere else in this repository, it is never sent
on the wire by the test, and rule 1 forbids it from appearing in the turn-1 reply. So
a turn-2 hit means one thing: the body was still there.

Why the CLI case is a CONTROL, not a duplicate
----------------------------------------------
The CLI case runs the SAME probe skill through the SAME two-turn shape with the SAME
assertions, differing only in the face it drives. It is there to validate the
instrument rather than the feature. If it passes, then the probe skill is well formed,
the model reliably obeys both rules, the token really is recallable, and the assertion
text really does detect recall. A CLI-green / HTTP-red split therefore isolates the
fault to the face and nothing else, and rules out the alternative explanations that
would otherwise be live: a badly written probe, a flaky model, or a broken assertion.

If the CLI control ever fails, stop. The HTTP verdict carries no information until the
instrument is trustworthy again.

The CLI face persists the expanded body: ``_runtime.py`` saves
``context_module.get_messages()`` after each turn and ``--resume`` reads it back, so
the body is in the restored history verbatim. The HTTP face does not: the next POST
arrives carrying the CLIENT's history ``[user: <sigil>, assistant: <reply>, user:
<new>]``, and ``_session_runner.py`` seeds the session with ``set_messages(history)``
using that RAW sigil text. The expanded body lived only in the previous turn's
ephemeral session context, so from turn 2 onward the model sees the six words of the
sigil where the skill body used to be.

Expected state today: CLI GREEN, HTTP RED. No ``xfail`` marker is used on purpose. The
HTTP case is meant to fail loudly as a real red and go green when the fix lands in the
same change, rather than sitting quietly as an expected failure.

Why turn 1 asserts the token is ABSENT
--------------------------------------
That assertion is a CONFOUND GUARD, not a feature assertion. Nothing in the product is
being checked by it. If the turn-1 arming reply echoed the token, that reply would
enter turn-2 history as ordinary assistant text on both faces, and the model could
recite the token on turn 2 from history alone with the skill body long gone. The HTTP
case would then go green while the exact bug it exists to catch was fully intact. So a
turn-1 token hit is a defect in the PROBE, and its failure message says so, so nobody
reads it as a product bug.

Why this module does not use ``framework.harness.run_http_case``
----------------------------------------------------------------
``E2ECase.command`` for ``kind="http"`` is a ``(method, path)`` tuple and the shared
runner cannot send a request body, which both chat-completion turns need. The POST is
built locally with the helpers in ``http_turns.py``, shared with
``test_sigil_dispatch.py``. ``framework/`` stays untouched (docs/E2E_TESTING.md:
"stable; rarely touched"). The CLI case has no such constraint and does use the shared
``cli-multi`` runner.
"""

from __future__ import annotations

from typing import Any

import pytest
from framework.harness import E2ECase, Step, run_multi_case

from suites.skills.conftest import MEMORY_PROBE_ARMED, MEMORY_PROBE_NAME, MEMORY_PROBE_TOKEN
from suites.skills.http_turns import assistant_text, new_session_id, post_chat

pytestmark = pytest.mark.dtu

# Host-config seeded into every DTU by provisioning: anthropic provider, approval
# mode "yes". The memory probe touches no tools, but the shared config is what the
# rest of this suite runs against and keeps both faces on the same provider.
_CLI_CONFIG = "/root/e2e/host-config.json"

_SIGIL_PROMPT = f"!amplifier:skill {MEMORY_PROBE_NAME}"

# The exact wording rule 2 of the probe body keys off. Kept as one constant so the two
# faces cannot drift apart on phrasing, which would turn a face difference into a
# prompt difference.
_RECALL_PROMPT = "what is the probe token?"


# --------------------------------------------------------------------------- #
# Checks
#
# These live here rather than in framework/assertions.py because their failure
# text is specific to this experiment: each message has to say which of the two
# very different things a red means (a broken probe, or the bug under test).
# --------------------------------------------------------------------------- #


def _assert_armed_without_token(text: str, *, face: str) -> None:
    """Assert turn 1 armed the probe and did NOT leak the token.

    Two assertions in one place because they answer the same question about the same
    reply, in the order that makes a failure self-explanatory:

    1. ``MEMORY-PROBE-ARMED`` present. This proves the body reached the model at all.
       Without it, turn 2 is meaningless: there would be nothing to survive.
    2. The token absent. This is the confound guard described in the module docstring.
    """
    assert MEMORY_PROBE_ARMED in text, (
        f"[{face}] turn 1 did not arm the probe: expected {MEMORY_PROBE_ARMED!r} in the reply. "
        f"The skill body never reached the model, so turn 2 would prove nothing either way. "
        f"Check that {MEMORY_PROBE_NAME!r} was discoverable and that the sigil dispatched.\n"
        f"turn 1 reply:\n{text}"
    )
    assert MEMORY_PROBE_TOKEN not in text, (
        f"[{face}] turn 1 leaked the probe token {MEMORY_PROBE_TOKEN!r}. This is NOT a product "
        f"bug: it means the probe SKILL.md body needs tightening so the arming reply cannot echo "
        f"the token. A leaked token rides into turn 2 as ordinary assistant history, and the model "
        f"could then recite it on turn 2 without the skill body having survived at all, which would "
        f"turn this whole experiment green for the wrong reason.\n"
        f"turn 1 reply:\n{text}"
    )


def _cli_turn1_check(parsed: Any) -> None:
    """Adapt ``_assert_armed_without_token`` to the ``Step.check`` signature.

    ``run_multi_case`` hands the check ``_parse(stdout)``: the JSON-decoded value when
    stdout happens to parse, otherwise the raw string. A plain ``run`` emits prose, so
    in practice this is the raw string, but ``str()`` keeps the check correct either
    way rather than depending on that.
    """
    _assert_armed_without_token(str(parsed), face="cli")


def _cli_turn2_check(parsed: Any) -> None:
    """Assert the resumed CLI turn recalled the token from the still-in-scope body."""
    text = str(parsed)
    assert MEMORY_PROBE_TOKEN in text, (
        f"[cli] turn 2 did not recall the probe token {MEMORY_PROBE_TOKEN!r}. The CLI face "
        f"persists the post-turn context (``_runtime.py`` saves ``context_module.get_messages()`` "
        f"and ``--resume`` reads it back), so the expanded skill body should still be in scope and "
        f"rule 2 should still apply. A red here means the CONTROL is broken, so any HTTP verdict in "
        f"this module is uninterpretable until it is fixed.\n"
        f"turn 2 reply:\n{text}"
    )


# --------------------------------------------------------------------------- #
# 1. CONTROL -- the CLI face keeps the expanded body across a resume.
# --------------------------------------------------------------------------- #


def test_inline_body_survives_across_turns_cli(dtu_id: str, memory_probe: str) -> None:
    """CONTROL: two CLI turns on one session id; the skill body must survive turn 1.

    Uses the harness ``cli-multi`` kind, which is exactly this shape: an ordered list
    of ``Step`` commands sharing one generated session id substituted for ``{SID}``.
    ``run_multi_case`` enforces exit 0 per step BEFORE calling that step's check, so a
    crashed CLI fails with the crash, not with a confusing "token missing".

    The case is built inline instead of in ``cases.py`` because its two checks are not
    reusable structural assertions; they carry the experiment's reasoning in their
    failure text and only make sense next to this docstring.

    No ``cwd`` is needed: ``memory_probe`` seeds the skill in the user skills dir, which
    is on the discovery path from any launch directory. See that fixture's docstring.
    """
    case = E2ECase(
        "memory-probe-cli",
        "cli-multi",
        [],
        steps=(
            # Turn 1: invoke the skill. Arms the probe, must not speak the token.
            Step(
                ["run", "-y", "--config", _CLI_CONFIG, "--session-id", "{SID}", _SIGIL_PROMPT],
                check=_cli_turn1_check,
            ),
            # Turn 2: same session id, --resume. The prompt says nothing about the
            # token's value, so only the restored skill body can answer it.
            Step(
                ["run", "-y", "--config", _CLI_CONFIG, "--session-id", "{SID}", "--resume", _RECALL_PROMPT],
                check=_cli_turn2_check,
            ),
        ),
    )
    run_multi_case(dtu_id, case)


# --------------------------------------------------------------------------- #
# 2. THE BUG -- the HTTP face loses the expanded body at the turn boundary.
# --------------------------------------------------------------------------- #


def test_inline_body_survives_across_turns_http(
    dtu_id: str,
    server: dict[str, str],
    model_id: str,
    memory_probe: str,
) -> None:
    """Two chat-completion POSTs on ONE session id; the skill body must survive turn 1.

    Both turns send the same ``X-Session-Id``, which is what a real client does: one
    conversation, one session, N requests. Turn 2 also replays the conversation the way
    a real client replays it, as the full ``messages`` array ending in the new user
    message. That replay is the whole point. The history it carries holds the RAW sigil
    text, not the expanded body, because the expansion only ever existed inside turn
    1's ephemeral session context.

    Assertions are ordered so a failure names its own cause. Status first, so a downed
    server, a curl quoting bug, or a request-shape error fails there with that message.
    Then a non-empty reply, so a model error does not masquerade as a missing token.
    Only then the token, which by that point can mean nothing else.
    """
    session_id = new_session_id("http-memory")

    # --- Turn 1: invoke the skill over HTTP. -------------------------------- #
    status, raw_body = post_chat(
        dtu_id,
        server["base_url"],
        server["token"],
        session_id,
        {"model": model_id, "messages": [{"role": "user", "content": _SIGIL_PROMPT}]},
    )
    assert status == "200", f"[http] turn 1 expected HTTP 200, got {status!r}\nbody:\n{raw_body}"

    turn1_reply = assistant_text(raw_body)
    assert turn1_reply.strip(), (
        f"[http] turn 1 empty assistant reply; the turn produced no completion\nbody:\n{raw_body}"
    )

    _assert_armed_without_token(turn1_reply, face="http")

    # --- Turn 2: replay history verbatim, then ask for the token. ----------- #
    # The assistant message is turn 1's reply EXACTLY as returned. Reconstructing or
    # summarizing it would be the test inventing a client behavior; a real client
    # echoes back what it received.
    status, raw_body = post_chat(
        dtu_id,
        server["base_url"],
        server["token"],
        session_id,
        {
            "model": model_id,
            "messages": [
                {"role": "user", "content": _SIGIL_PROMPT},
                {"role": "assistant", "content": turn1_reply},
                {"role": "user", "content": _RECALL_PROMPT},
            ],
        },
    )
    assert status == "200", f"[http] turn 2 expected HTTP 200, got {status!r}\nbody:\n{raw_body}"

    turn2_reply = assistant_text(raw_body)
    assert turn2_reply.strip(), (
        f"[http] turn 2 empty assistant reply; the turn produced no completion\nbody:\n{raw_body}"
    )

    assert MEMORY_PROBE_TOKEN in turn2_reply, (
        f"[http] turn 2 did not recall the probe token {MEMORY_PROBE_TOKEN!r}.\n"
        f"\n"
        f"What this red means: the client's history carried the RAW sigil text "
        f"{_SIGIL_PROMPT!r}, and the server seeded the session from that raw text rather than "
        f"from the expanded skill body (``_session_runner.py`` calls ``set_messages(history)`` "
        f"with exactly what the client sent). The expanded body lived only in turn 1's ephemeral "
        f"session context, so the inline skill's instructions did not survive the turn boundary: "
        f"by turn 2 rule 2 is no longer in scope for the model, and the token is unrecoverable "
        f"from anything the model can see.\n"
        f"\n"
        f"The CLI control in this module runs the same probe through the same two turns and "
        f"passes, so the probe, the model, and this assertion are all sound; the difference is "
        f"the face.\n"
        f"\n"
        f"turn 2 reply:\n{turn2_reply}"
    )

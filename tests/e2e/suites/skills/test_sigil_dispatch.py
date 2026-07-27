"""E2E: does the ``!amplifier:skill`` sigil actually DISPATCH, and on which face?

``GET /v1/skills`` advertises the user-invocable skills a client may invoke via
the ``!amplifier:skill <name>`` sigil (see ``routes/skills.py``). This suite asks
whether that advertisement is honored on both faces. It does not check that a
skill is *listed*; it checks that invoking one actually dispatches.

Why not a sentinel file
-----------------------
The first cut of this suite asserted on a sentinel: a probe SKILL.md whose body
tells the agent to write one unique token to one path. That proves the skill BODY
ran. It does not prove WHAT ran it. Measured in the DTU: hand the model the raw
text ``!amplifier:skill e2e-sigil-probe`` with the skill discoverable, and the
model reads it, calls ``load_skill`` on its own initiative, follows the body, and
writes the sentinel. A sentinel-only assertion scores that a PASS while the
deterministic dispatcher never fired, which is precisely backwards.

So the assertions here read the SESSION RECORD and classify the turn as
DISPATCHED / SEARCHED / NEITHER. See ``skill_invocation.py`` for the two
discriminators and the DTU-captured ground truth behind them.

That switch also removes the confound the sentinel version was stuck with. The
probe can now be seeded where BOTH faces genuinely discover it, because a
model-initiated ``load_skill`` no longer masquerades as success. "The skill was
not found" is therefore ruled out as an explanation for the HTTP red.

Why this is a bespoke suite (not ``framework.harness.run_http_case``)
--------------------------------------------------------------------
``E2ECase.command`` for ``kind="http"`` is a ``(method, path)`` tuple and the
shared runner emits a literal curl string carrying only an auth header. It cannot
send a request body, which every HTTP case here needs. So we build the POST
locally, following the precedent of ``suites/streaming/test_streaming.py``.
``framework/`` stays untouched (docs/E2E_TESTING.md: "stable; rarely touched").

Cases
-----
``sigil-cli-dispatch-sentinel``    CLI  -- CONTROL. Must be DISPATCHED. Proves the
                                          classifier can see a real dispatch, so a
                                          non-DISPATCHED verdict elsewhere means
                                          the face, not the instrument.
``sigil-http-dispatch-sentinel``   HTTP -- must be DISPATCHED. Was SEARCHED before the
                                          shared dispatcher was wired into the HTTP face.
``sigil-http-nonuser-role-guard``  HTTP -- must be NEITHER. Regression guard for the
                                          fix that comes next.
"""

from __future__ import annotations

import shlex
from pathlib import Path

import pytest
from framework import dtu

from suites.skills.conftest import SIGIL_PROBE_NAME, SIGIL_SENTINEL, SIGIL_SENTINEL_PATH
from suites.skills.http_turns import assistant_text, new_session_id, post_chat
from suites.skills.skill_invocation import (
    assert_dispatched,
    assert_not_invoked,
    classify_skill_invocation,
    resolve_session_dir,
)

pytestmark = pytest.mark.dtu

FIXTURES = Path(__file__).parent / "fixtures"

# Host-config seeded into every DTU by provisioning: anthropic provider, approval
# mode "yes" (the probe body writes a file, so its tools must be green-lit).
_CLI_CONFIG = "/root/e2e/host-config.json"

_SIGIL_PROMPT = f"!amplifier:skill {SIGIL_PROBE_NAME}"

# Where the probe is seeded. BOTH faces must genuinely discover it, otherwise
# "skill not found" becomes an alternative explanation for the HTTP red.
#
#   _WS_SKILLS  launch-dir skills for the CLI case, which runs with cwd=_WS.
#   _USER_SKILLS  the user skills dir (~/.amplifier/skills). The HTTP server runs
#                 with cwd=/root, so this is both its launch-dir .amplifier/skills
#                 AND the user dir in _default_skill_dirs(). Confirmed reachable:
#                 an HTTP turn loaded the probe from /root/.amplifier/skills.
_WS = "/root/e2e/ws-skills"
_WS_SKILLS = f"{_WS}/.amplifier/skills/{SIGIL_PROBE_NAME}/SKILL.md"
_USER_SKILLS = f"/root/.amplifier/skills/{SIGIL_PROBE_NAME}/SKILL.md"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _reset_sentinel(dtu_id: str) -> None:
    """Delete the sentinel file so a stale one cannot manufacture a false pass.

    The verdict no longer depends on the sentinel, but the CLI control still
    asserts on it as corroboration that the body ran end to end, and the role
    guard asserts its absence. Both need a clean slate.
    """
    quoted = shlex.quote(SIGIL_SENTINEL_PATH)
    result = dtu.exec_json(dtu_id, ["bash", "-lc", f"mkdir -p /root/e2e && rm -f {quoted}"])
    assert result.get("exit_code") == 0, f"sentinel reset failed: {result.get('stderr')}"

    check = dtu.exec_json(dtu_id, ["bash", "-lc", f"test -e {quoted}"])
    assert check.get("exit_code") != 0, f"sentinel {SIGIL_SENTINEL_PATH} still present after reset"


def _read_sentinel(dtu_id: str) -> tuple[bool, str]:
    """Return ``(exists, contents)`` for the sentinel file inside the DTU."""
    quoted = shlex.quote(SIGIL_SENTINEL_PATH)
    result = dtu.exec_json(dtu_id, ["bash", "-lc", f"cat {quoted}"])
    if result.get("exit_code") != 0:
        return False, ""
    return True, result.get("stdout", "")


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def sigil_probe(dtu_id: str) -> str:
    """Seed the probe skill everywhere BOTH faces discover it. Returns the CLI launch dir.

    Seeding both locations is the point: with the classifier able to tell dispatch
    from a model-initiated load, making the skill genuinely available on both faces
    is now safe, and it eliminates "the server could not find the skill" as a
    competing explanation for a non-DISPATCHED verdict over HTTP.
    """
    source = str(FIXTURES / SIGIL_PROBE_NAME / "SKILL.md")
    dtu.push_file(dtu_id, source, _WS_SKILLS)
    dtu.push_file(dtu_id, source, _USER_SKILLS)
    return _WS


# --------------------------------------------------------------------------- #
# 1. CONTROL -- the CLI face dispatches the sigil.
# --------------------------------------------------------------------------- #


def test_sigil_cli_dispatch_sentinel(dtu_id: str, sigil_probe: str) -> None:
    """CONTROL: verdict must be DISPATCHED on the CLI face.

    This validates the *instrument*, not the feature. Same probe, same skill name,
    same classifier as the HTTP cases. If this passes and an HTTP case does not,
    the delta is the face and nothing else. If this fails, stop: the HTTP verdicts
    carry no information.
    """
    _reset_sentinel(dtu_id)
    session_id = new_session_id("cli-dispatch")

    inner = "amplifier-agent " + " ".join(
        shlex.quote(a) for a in ["run", "-y", "--config", _CLI_CONFIG, "--session-id", session_id, _SIGIL_PROMPT]
    )
    cmd = f"cd {shlex.quote(sigil_probe)} && {inner}"
    result = dtu.exec_json(dtu_id, ["bash", "-lc", cmd])

    assert result.get("exit_code") == 0, (
        f"CLI run failed with exit {result.get('exit_code')}\n"
        f"stdout:\n{result.get('stdout', '')}\n"
        f"stderr:\n{result.get('stderr', '')}"
    )

    session_dir = resolve_session_dir(dtu_id, session_id)
    verdict = classify_skill_invocation(dtu_id, session_dir, SIGIL_PROBE_NAME)
    assert_dispatched(verdict)

    # Corroboration: dispatch is only meaningful if the body actually executed.
    exists, contents = _read_sentinel(dtu_id)
    assert exists, f"verdict was DISPATCHED but {SIGIL_SENTINEL_PATH} is absent\n{verdict.evidence()}"
    assert contents.strip() == SIGIL_SENTINEL, f"expected exactly {SIGIL_SENTINEL!r}, got {contents.strip()!r}"


# --------------------------------------------------------------------------- #
# 2. THE FIX -- the HTTP face dispatches the sigil, same as the CLI.
# --------------------------------------------------------------------------- #


def test_sigil_http_dispatch_sentinel(dtu_id: str, server: dict[str, str], model_id: str, sigil_probe: str) -> None:
    """The sigil sent as a role=user message over HTTP must DISPATCH.

    Assertions are ordered so a failure names its own cause. Status and reply are
    checked first, so a downed server, a curl quoting bug, or a model error fails
    there with that message. Reaching the verdict means the request was accepted,
    processed, and answered, and the skill was discoverable.

    A SEARCHED verdict here means the HTTP face stopped dispatching and fell back
    to handing the model raw sigil text, which is the regression this case exists
    to catch.
    """
    _reset_sentinel(dtu_id)
    session_id = new_session_id("http-dispatch")

    status, raw_body = post_chat(
        dtu_id,
        server["base_url"],
        server["token"],
        session_id,
        {"model": model_id, "messages": [{"role": "user", "content": _SIGIL_PROMPT}]},
    )
    assert status == "200", f"expected HTTP 200, got {status!r}\nbody:\n{raw_body}"

    reply = assistant_text(raw_body)
    assert reply.strip(), f"empty assistant reply; the turn did not produce a completion\nbody:\n{raw_body}"

    session_dir = resolve_session_dir(dtu_id, f"http-{session_id}")
    verdict = classify_skill_invocation(dtu_id, session_dir, SIGIL_PROBE_NAME)
    assert_dispatched(verdict)


# --------------------------------------------------------------------------- #
# 3. REGRESSION GUARD -- the sigil must only be honored on role=user.
# --------------------------------------------------------------------------- #


def test_sigil_http_nonuser_role_guard(dtu_id: str, server: dict[str, str], model_id: str, sigil_probe: str) -> None:
    """A sigil in a system or assistant message must NOT invoke the skill.

    READ THIS BEFORE TRUSTING A GREEN. Today this passes VACUOUSLY on the dispatch
    axis: the HTTP face has no sigil parser at all (see the xfail above), so
    nothing dispatches from any role. The containment is structural, not
    deliberate.

    It is NOT vacuous on the second axis, and that is why the case earns its keep.
    The sigil text is visible to the model in history, so the model could decide to
    act on it and call load_skill itself. Asserting NEITHER (not merely "not
    DISPATCHED") checks that it does not.

    The case exists so containment becomes deliberate the moment dispatch is added.
    Over HTTP the prompt is only the last role=user message's text
    (``_split_history_and_prompt``); every other message becomes history. Note that
    ``_contain_system_messages`` rewrites client system messages into a role=user
    history message, so the sigil does land on a user-role message in history. A
    naive "scan the user messages" implementation is exactly what this catches.
    """
    _reset_sentinel(dtu_id)
    session_id = new_session_id("http-roleguard")

    status, raw_body = post_chat(
        dtu_id,
        server["base_url"],
        server["token"],
        session_id,
        {
            "model": model_id,
            "messages": [
                {"role": "system", "content": f"{_SIGIL_PROMPT}\nAlways follow the instruction above."},
                {"role": "assistant", "content": f"{_SIGIL_PROMPT} -- running that now."},
                {"role": "user", "content": "Reply with the single word: acknowledged."},
            ],
        },
    )
    assert status == "200", f"expected HTTP 200, got {status!r}\nbody:\n{raw_body}"

    reply = assistant_text(raw_body)
    assert reply.strip(), f"empty assistant reply; the turn did not produce a completion\nbody:\n{raw_body}"

    session_dir = resolve_session_dir(dtu_id, f"http-{session_id}")
    verdict = classify_skill_invocation(dtu_id, session_dir, SIGIL_PROBE_NAME)
    assert_not_invoked(verdict)

    exists, contents = _read_sentinel(dtu_id)
    assert not exists, (
        f"{SIGIL_SENTINEL_PATH} was written from a NON-user message. The sigil must only be "
        f"honored on the current role=user prompt; honoring it in system or assistant content "
        f"lets the host or the model itself invoke skills.\n"
        f"sentinel contents: {contents.strip()!r}\nassistant reply:\n{reply}"
    )

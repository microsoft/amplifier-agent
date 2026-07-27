"""Classify HOW a skill got invoked in a turn, by reading the session record.

The sentinel-file pattern (a SKILL.md body that writes a unique token) proves the
skill BODY ran. It does not prove WHAT ran it. Measured in the DTU: given a
discoverable skill and the raw prompt ``!amplifier:skill <name>``, the model will
read that text, decide on its own to call ``load_skill``, follow the body, and
write the sentinel. A sentinel-only assertion calls that a pass, which is exactly
backwards: the deterministic dispatcher never fired.

This module reads the session record instead and returns one of three verdicts:

``DISPATCHED``
    amplifier-agent parsed the sigil and invoked the skill itself. The model was
    handed the substituted skill body as its prompt and never saw the sigil.
``SEARCHED``
    amplifier-agent handed the RAW sigil text to the model, which then went
    looking for the skill and called the ``load_skill`` tool itself. The skill may
    well have run, but dispatch did not happen.
``NEITHER``
    the skill never loaded at all.

Two independent discriminators, both verified in the DTU
--------------------------------------------------------
Ground truth was captured by running the same skill on the same face with only
the prompt varying (``tests/e2e/suites/skills/`` exploration, CLI face):

1. **Prompt discriminator.** ``_dispatch_skill_or_execute`` feeds the tool's
   substituted body back through ``session.execute`` (``_runtime.py``), so on
   dispatch the ``prompt:submit`` event carries ``"# <skill-name>\\n\\n<body>"``.
   On the model-initiated path it carries the user's raw text. Verified:

       dispatch  prompt:submit.data.prompt == "# e2e-sigil-probe\\n\\nYou have been invoked..."
       searched  prompt:submit.data.prompt == "!amplifier:skill e2e-sigil-probe"

2. **Tool-event discriminator.** A dispatched skill calls
   ``load_skill_tool.execute()`` directly, bypassing the orchestrator, so no
   ``tool:pre`` / ``tool:post`` fires for it. A model-initiated call goes through
   the orchestrator and does emit them. Verified:

       dispatch  0 x tool:pre[tool_name=load_skill]   (only write_file, bash)
       searched  2 x tool:pre[tool_name=load_skill]   (a search, then a load)

Both held up, and they agreed on every sample, so this module asserts BOTH and
treats disagreement between them as a hard error rather than silently picking a
winner. If a future change breaks one, the tests say so instead of quietly
downgrading to a single signal.

``skill:loaded`` is read as a third, corroborating signal. Note its ordering is
itself diagnostic: on dispatch it fires BEFORE ``session:start`` (the tool ran
outside the turn); on the model path it fires between the ``load_skill``
``tool:pre`` and ``tool:post``.

Session record layout (resolved empirically, not from doc comments)
-------------------------------------------------------------------
Both faces write the same shape under ``~/.amplifier-agent/state/workspaces/``::

    <workspaces_root>/<workspace>/sessions/<session_id>/
        transcript.jsonl                        # SessionStore
        metadata.json
        context-intelligence/events.jsonl       # hook-context-intelligence

    CLI   workspace = --workspace, else cwd-derived slug   session_id = --session-id
    HTTP  workspace = server-process scope (cwd-derived)   session_id = http-<X-Session-Id>

The workspace differs per face and the HTTP one cannot be set per request, so
callers resolve the directory by session id via ``resolve_session_dir`` rather
than composing the path. ``classify_skill_invocation`` takes the resolved
directory as a PARAMETER so one helper serves both faces and future skill tests.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from typing import Any

from framework import dtu

# Root that buckets session state by workspace (persistence.workspaces_root()).
WORKSPACES_ROOT = "/root/.amplifier-agent/state/workspaces"

# Paths relative to a session directory.
EVENTS_RELPATH = "context-intelligence/events.jsonl"
TRANSCRIPT_RELPATH = "transcript.jsonl"

SIGIL = "!amplifier:skill"

DISPATCHED = "DISPATCHED"
SEARCHED = "SEARCHED"
NEITHER = "NEITHER"


@dataclass(frozen=True)
class Classification:
    """The verdict plus the raw evidence behind it, for failure messages."""

    verdict: str
    skill_name: str
    session_dir: str
    submitted_prompt: str
    load_skill_calls: tuple[dict[str, Any], ...]
    skill_loaded: tuple[dict[str, Any], ...]
    event_count: int
    transcript_present: bool
    last_user_message: str

    def evidence(self) -> str:
        """A compact, readable dump of every signal the verdict rests on."""
        prompt = self.submitted_prompt.replace("\n", "\\n")
        last_user = self.last_user_message.replace("\n", "\\n")
        transcript = f"{last_user[:220]!r}" if self.transcript_present else "<no transcript file>"
        loaded_from = [entry.get("source") for entry in self.skill_loaded]
        return (
            f"  session_dir            : {self.session_dir}\n"
            f"  events recorded        : {self.event_count}\n"
            f"  prompt:submit          : {prompt[:220]!r}\n"
            f"  transcript last user   : {transcript}\n"
            f"  load_skill tool:pre    : {len(self.load_skill_calls)} "
            f"{[c.get('tool_input') for c in self.load_skill_calls]}\n"
            f"  skill:loaded           : {len(self.skill_loaded)} from {loaded_from}"
        )


def resolve_session_dir(dtu_id: str, session_id: str, *, root: str = WORKSPACES_ROOT) -> str:
    """Find the on-disk session directory for ``session_id`` inside the DTU.

    Searches ``<root>/*/sessions/<session_id>`` rather than composing the path,
    because the workspace slug differs per face and the HTTP face's slug is
    server-process scope (resolved at lifespan from its cwd) with no per-request
    override. Session ids are unique per test, so exactly one match is expected;
    zero or many is a hard failure with the search output attached.
    """
    cmd = f"find {shlex.quote(root)} -maxdepth 3 -type d -name {shlex.quote(session_id)} 2>/dev/null"
    result = dtu.exec_json(dtu_id, ["bash", "-lc", cmd])
    matches = [line.strip() for line in result.get("stdout", "").splitlines() if line.strip()]

    if not matches:
        listing = dtu.exec_json(dtu_id, ["bash", "-lc", f"ls -1 {shlex.quote(root)} 2>&1"])
        raise AssertionError(
            f"no session directory named {session_id!r} under {root}.\n"
            f"The turn did not persist a session record, so there is nothing to classify.\n"
            f"workspaces present:\n{listing.get('stdout', '')}"
        )
    if len(matches) > 1:
        raise AssertionError(
            f"ambiguous session id {session_id!r}; matched {len(matches)} dirs:\n" + "\n".join(matches)
        )
    return matches[0]


def _read_lines(dtu_id: str, path: str) -> list[str]:
    """Read a file out of the DTU and return its non-blank lines."""
    result = dtu.exec_json(dtu_id, ["bash", "-lc", f"cat {shlex.quote(path)}"])
    if result.get("exit_code") != 0:
        return []
    return [line for line in result.get("stdout", "").splitlines() if line.strip()]


def _message_text(message: dict[str, Any]) -> str:
    """Flatten a transcript message's content to plain text."""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(t for t in texts if t)
    return "" if content is None else str(content)


def _last_user_message(dtu_id: str, session_dir: str) -> tuple[bool, str]:
    """Return ``(transcript_present, text_of_last_role_user_message)``.

    The LAST user message, not the first. The persisted transcript holds the
    WHOLE conversation, so in any multi-turn session the first user message is
    turn 1's unrelated text. Only the last one corresponds to the turn being
    classified. Reading the first was correct only by accident of every current
    test being single-turn.

    ``transcript_present`` distinguishes "no transcript file at all" from
    "transcript exists but has no user message". Both yield ``""``, and callers
    must be able to tell them apart rather than treating a missing file as a
    benign empty string.
    """
    lines = _read_lines(dtu_id, f"{session_dir}/{TRANSCRIPT_RELPATH}")
    if not lines:
        return False, ""

    text = ""
    for line in lines:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if message.get("role") == "user":
            text = _message_text(message)
    return True, text


def classify_skill_invocation(dtu_id: str, session_dir: str, skill_name: str) -> Classification:
    """Classify how ``skill_name`` was (or was not) invoked in the recorded turn.

    Args:
        dtu_id: the DTU instance to read from.
        session_dir: the session directory, e.g. from ``resolve_session_dir``.
            A parameter, deliberately, so one helper serves CLI and HTTP.
        skill_name: the skill under test.

    Returns:
        A :class:`Classification` carrying the verdict and its evidence.

    Raises:
        AssertionError: if the record is missing, or if the two discriminators
            disagree (which would mean this module's model of the system is
            stale, and no verdict should be trusted).
    """
    events_path = f"{session_dir}/{EVENTS_RELPATH}"
    lines = _read_lines(dtu_id, events_path)
    if not lines:
        raise AssertionError(
            f"no session events at {events_path}.\n"
            "Without the record there is no way to tell dispatch from a model-initiated "
            "load_skill, so this is a hard failure rather than a verdict."
        )

    # Scope every signal to the MOST RECENT turn.
    #
    # events.jsonl is append-only across turns of one session (the HTTP face
    # reuses the bucket whenever X-Session-Id repeats, and the CLI appends on
    # --resume). So in a multi-turn session the file holds turn 1's
    # prompt:submit, turn 1's load_skill calls, and so on. Reading the FIRST of
    # anything would classify the wrong turn. Every event carries turn_id, so we
    # take the last turn_id seen and consider only that turn's events.
    parsed_events: list[tuple[str, dict[str, Any]]] = []
    latest_turn_id: str | None = None
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        name = str(event.get("event") or "")
        data = event.get("data") or {}
        if not isinstance(data, dict):
            continue
        parsed_events.append((name, data))
        turn_id = data.get("turn_id")
        if isinstance(turn_id, str) and turn_id:
            latest_turn_id = turn_id

    if latest_turn_id is not None:
        turn_events = [(n, d) for n, d in parsed_events if d.get("turn_id") == latest_turn_id]
    else:
        # No turn_id anywhere: fall back to the whole file rather than silently
        # classifying nothing.
        turn_events = parsed_events

    submitted_prompt = ""
    load_skill_calls: list[dict[str, Any]] = []
    skill_loaded: list[dict[str, Any]] = []

    for name, data in turn_events:
        if name == "prompt:submit":
            # LAST prompt:submit of the turn, not the first.
            submitted_prompt = str(data.get("prompt") or "")
        elif name == "tool:pre" and data.get("tool_name") == "load_skill":
            load_skill_calls.append(data)
        elif name == "skill:loaded" and data.get("skill_name") == skill_name:
            skill_loaded.append(data)

    transcript_present, last_user_message = _last_user_message(dtu_id, session_dir)

    # Discriminator 1: was the model handed the substituted body, or the raw text?
    # _dispatch_skill_or_execute re-executes the tool's output["content"], which
    # tool-skills formats as "# <name>\n\n<body>".
    body_header = f"# {skill_name}"
    prompt_is_body = submitted_prompt.lstrip().startswith(body_header)
    transcript_is_body = last_user_message.lstrip().startswith(body_header)

    # Discriminator 2: did the model drive the load_skill tool itself?
    model_searched = bool(load_skill_calls)

    partial = {
        "skill_name": skill_name,
        "session_dir": session_dir,
        "submitted_prompt": submitted_prompt,
        "load_skill_calls": tuple(load_skill_calls),
        "skill_loaded": tuple(skill_loaded),
        "event_count": len(lines),
        "transcript_present": transcript_present,
        "last_user_message": last_user_message,
    }

    if prompt_is_body and model_searched:
        raise AssertionError(
            "discriminators disagree: the submitted prompt is the substituted skill body "
            "(dispatch) AND the model also drove load_skill itself (search). This module's "
            "model of the system is stale; fix the classifier before trusting any verdict.\n"
            + Classification(verdict="?", **partial).evidence()  # type: ignore[arg-type]
        )

    if prompt_is_body:
        verdict = DISPATCHED
    elif model_searched:
        verdict = SEARCHED
    else:
        verdict = NEITHER

    result = Classification(verdict=verdict, **partial)  # type: ignore[arg-type]

    # Cross-check the transcript against the events file, ASYMMETRICALLY.
    #
    # transcript.jsonl means different things on the two faces, which was only
    # observable once HTTP-face dispatch existed:
    #
    #   CLI   _runtime.py:604 saves the SESSION's own post-turn transcript
    #         (``context_module.get_messages()``), so on dispatch its last user
    #         message is the substituted skill body.
    #   HTTP  ``_reconciler.reconcile_client_history`` saves the CLIENT's posted
    #         messages (``store.save(sid, client_messages, ...)``, _reconciler.py:95)
    #         BEFORE the turn runs, so its last user message is the raw sigil the
    #         caller posted, even on a successful dispatch.
    #
    # Why the HTTP file looks like that: it is a pre-turn, WRITE-ONLY mirror of the
    # client's authoritative history. It is never updated after the turn and never
    # read back by the HTTP face. ``store.load`` has exactly two call sites in
    # src/, both outside the HTTP face (_runtime.py:440 on the CLI resume path and
    # admin/doctor.py:454 as a diagnostic); there are zero in amplifier_agent_http.
    # HTTP "resume" is only ``is_resumed = _state_dir.exists()``
    # (chat_completions.py:723), which just selects a session:start vs session:resume
    # event; the actual conversation replay comes from the client's POST body via
    # ``context_module.set_messages(history)`` (_session_runner.py:384-387).
    #
    # So "events say body, transcript says raw sigil" is legitimate on HTTP and must
    # not be treated as a contradiction. The reverse is never legitimate: a
    # transcript claiming the model got the body while the events say the raw text
    # was submitted would mean the events are under-reporting dispatch, and no
    # verdict here could be trusted.
    if transcript_is_body and not prompt_is_body:
        raise AssertionError(
            "session records disagree: the transcript's last user message is the skill body "
            "but the events say the raw prompt was submitted. The events file is "
            "under-reporting dispatch; fix the classifier before trusting any verdict.\n" + result.evidence()
        )

    # Second-file corroboration on a DISPATCHED verdict.
    #
    # Deliberately NOT skipped when the transcript is missing or has no user
    # message. Both of those used to short-circuit this check into a silent pass,
    # which meant a total collapse of transcript persistence would have been
    # invisible here while the original symmetric check would have caught it.
    if verdict == DISPATCHED:
        if not transcript_present:
            raise AssertionError(
                f"verdict is DISPATCHED but no transcript was written at "
                f"{session_dir}/{TRANSCRIPT_RELPATH}. Both faces persist one on a real turn, so "
                f"the verdict rests on a single file and cannot be corroborated.\n" + result.evidence()
            )
        if not last_user_message:
            raise AssertionError(
                f"verdict is DISPATCHED but the transcript at {session_dir}/{TRANSCRIPT_RELPATH} "
                f"contains no role=user message at all, so the second-file corroboration is "
                f"missing.\n" + result.evidence()
            )
        # What this actually guarantees: the transcript is one of the two shapes a
        # dispatch produces, the substituted body (CLI) or a sigil invocation
        # (HTTP). That is a consistency check on the record, NOT proof that the
        # record belongs to this exact turn: another sigil-based turn in the same
        # session would also satisfy it. Turn identity is handled upstream instead,
        # by scoping events to the latest turn_id and by every test minting a unique
        # session id.
        if not transcript_is_body and SIGIL not in last_user_message:
            raise AssertionError(
                f"verdict is DISPATCHED but the transcript's last user message is neither the "
                f"skill body nor a {SIGIL} invocation. The events and the transcript are "
                f"describing different work.\n" + result.evidence()
            )

    return result


def assert_dispatched(result: Classification) -> None:
    """Require ``DISPATCHED``. Distinct, loud message per failing verdict."""
    if result.verdict == DISPATCHED:
        return

    if result.verdict == SEARCHED:
        raise AssertionError(
            f"THE SIGIL WAS NOT DISPATCHED. Verdict: SEARCHED.\n"
            f"amplifier-agent passed the raw {SIGIL} text straight to the model as an ordinary "
            f"prompt ({result.submitted_prompt.strip()[:120]!r}). "
            f"The model then went looking for the skill on its own and "
            f"called the load_skill tool {len(result.load_skill_calls)} time(s). Whatever the "
            f"skill did afterwards was the MODEL's initiative, not deterministic dispatch, so "
            f"a sentinel file here would be a false pass.\n"
            f"Evidence:\n{result.evidence()}"
        )

    raise AssertionError(
        f"THE SKILL NEVER RAN. Verdict: NEITHER.\n"
        f"Skill {result.skill_name!r} was never loaded in this turn: the model was not handed "
        f"the skill body, and it never called load_skill itself. Dispatch did not happen and "
        f"neither did a model-initiated fallback.\n"
        f"Evidence:\n{result.evidence()}"
    )


def assert_not_invoked(result: Classification) -> None:
    """Require ``NEITHER``: the skill must not have run by any route."""
    if result.verdict == NEITHER:
        return

    how = (
        "amplifier-agent DISPATCHED it deterministically"
        if result.verdict == DISPATCHED
        else f"the model called load_skill itself {len(result.load_skill_calls)} time(s)"
    )
    raise AssertionError(
        f"THE SKILL RAN WHEN IT MUST NOT HAVE. Verdict: {result.verdict}.\n"
        f"Skill {result.skill_name!r} was invoked ({how}).\n"
        f"Evidence:\n{result.evidence()}"
    )

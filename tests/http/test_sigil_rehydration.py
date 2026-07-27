"""Tests for replayed-sigil re-hydration on the HTTP face.

What is under test
------------------
``rehydrate_history_sigils`` substitutes an ``!amplifier:skill <name>`` sigil
sitting in replayed client history with that skill's expanded inline body, so
the body an INLINE skill established on an earlier turn is still in context on
the next POST. The CLI gets this for free (it persists the post-turn context and
reads it back on ``--resume``); the HTTP face reseeds from the CLIENT's history,
which still carries the raw sigil text the user typed.

The properties worth pinning here are the ones that make the substitution SAFE
rather than the one that makes it useful:

* a ``context: fork`` skill is never loaded, because loading one runs the fork;
* an entry the HTTP face synthesized (the ``<user_provided_instructions>``
  containment message, written with ``role: user`` but carrying HOST text) is
  never re-hydrated;
* an unknown skill, a failed load, and an unresolvable fork status all leave the
  user's text exactly as they wrote it rather than fabricating a body;
* the caller's history dicts are never mutated.

These run with fakes: no DTU, no network, no model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from amplifier_agent_http._wire import ChatMessage
from amplifier_agent_http.routes.chat_completions import _contain_system_messages
from amplifier_agent_lib.skill_dispatch import rehydrate_history_sigils

INLINE_SKILL = "e2e-memory-probe"
INLINE_BODY = f"# {INLINE_SKILL}\n\nRule 1: remember MEMORY-PROBE-TOKEN-J4X8.\nArgs: $ARGUMENTS"
FORK_SKILL = "council"

INLINE_SIGIL = f"!amplifier:skill {INLINE_SKILL}"
FORK_SIGIL = f"!amplifier:skill {FORK_SKILL}"


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


@dataclass
class _Meta:
    """Stand-in for ``SkillMetadata``. Only ``context`` is load-bearing here."""

    name: str
    context: str | None = None


class _Result:
    def __init__(self, success: bool, output: Any = None, error: str | None = None) -> None:
        self.success = success
        self.output = output
        self.error = error


class _LegacyLoadSkillTool:
    """Fake ``load_skill`` WITHOUT ``get_effective_skills``.

    Models a mounted tool predating that method: the attribute genuinely does
    not exist, which is the path where fork status cannot be resolved from the
    tool itself.
    """

    def __init__(self, skills: dict[str, _Meta], *, load_succeeds: bool = True) -> None:
        self._skills = skills
        self._load_succeeds = load_succeeds
        self.calls: list[dict[str, Any]] = []

    async def execute(self, payload: dict[str, Any]) -> _Result:
        self.calls.append(payload)
        if not self._load_succeeds:
            return _Result(False, error="skill did not load")
        name = payload.get("skill_name", "")
        meta = self._skills.get(name)
        if meta is None:
            return _Result(False, error="no such skill")
        if meta.context == "fork":
            # What a real fork load returns AFTER having run the sub-session.
            # Reaching this branch in a test is itself the failure.
            return _Result(True, output={"response": "the fork ran", "context": "fork"})
        arguments = payload.get("arguments") or ""
        return _Result(True, output={"content": INLINE_BODY.replace("$ARGUMENTS", arguments)})


class _LoadSkillTool(_LegacyLoadSkillTool):
    """The current tool: exposes the catalog that reveals fork status."""

    def get_effective_skills(self) -> dict[str, _Meta]:
        return dict(self._skills)


class _Coordinator:
    def __init__(self, tool: Any, *, discovery: Any = None) -> None:
        self._tool = tool
        self._discovery = discovery

    def get(self, kind: str, name: str | None = None) -> Any:
        if kind == "tools" and name == "load_skill":
            return self._tool
        return None

    def get_capability(self, name: str) -> Any:
        if name == "skills_discovery":
            return self._discovery
        return None


class _Session:
    def __init__(self, tool: Any, *, discovery: Any = None) -> None:
        self.coordinator = _Coordinator(tool, discovery=discovery)


def _session(
    *,
    expose_catalog: bool = True,
    load_succeeds: bool = True,
    discovery: Any = None,
) -> tuple[_Session, _LegacyLoadSkillTool]:
    catalog = {
        INLINE_SKILL: _Meta(INLINE_SKILL),
        FORK_SKILL: _Meta(FORK_SKILL, context="fork"),
    }
    factory = _LoadSkillTool if expose_catalog else _LegacyLoadSkillTool
    tool = factory(catalog, load_succeeds=load_succeeds)
    return _Session(tool, discovery=discovery), tool


def _user(content: Any) -> dict[str, Any]:
    return {"role": "user", "content": content}


# --------------------------------------------------------------------------- #
# The feature: an inline body survives the turn boundary
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_inline_sigil_is_replaced_with_the_skill_body() -> None:
    """The bug this whole change exists for: the sigil becomes the body."""
    session, tool = _session()
    history = [_user(INLINE_SIGIL), {"role": "assistant", "content": "armed"}]

    out = await rehydrate_history_sigils(session, history, eligible=[True, False])

    assert out[0]["content"].startswith(f"# {INLINE_SKILL}"), (
        f"the replayed sigil was not re-hydrated; content is still {out[0]['content']!r}"
    )
    assert "MEMORY-PROBE-TOKEN-J4X8" in out[0]["content"]
    assert out[1] == history[1], "the assistant reply must pass through untouched"
    assert tool.calls == [{"skill_name": INLINE_SKILL, "arguments": ""}]


@pytest.mark.asyncio
async def test_trailing_arguments_are_passed_through() -> None:
    """Args ride along to ``execute`` so ``$ARGUMENTS`` expands as it did originally."""
    session, tool = _session()
    history = [_user(f"{INLINE_SIGIL} keep  this   spacing")]

    out = await rehydrate_history_sigils(session, history, eligible=[True])

    assert tool.calls == [{"skill_name": INLINE_SKILL, "arguments": "keep  this   spacing"}]
    assert "Args: keep  this   spacing" in out[0]["content"]


@pytest.mark.asyncio
async def test_no_sigil_in_history_touches_nothing() -> None:
    """The common case costs one scan and never reaches the coordinator."""
    session, tool = _session()
    history = [_user("hello"), {"role": "assistant", "content": "hi"}]

    out = await rehydrate_history_sigils(session, history, eligible=[True, False])

    assert out == history
    assert tool.calls == []


# --------------------------------------------------------------------------- #
# The safety properties
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_fork_skill_sigil_is_left_verbatim_and_never_loaded() -> None:
    """The most important test here: re-hydration must never re-run a fork.

    The ONLY way to obtain a skill's body is to load it, and loading a
    ``context: fork`` skill RUNS the fork in a spawned sub-session. If this
    function loaded one, every subsequent POST replaying the same conversation
    would silently re-execute that sub-session: real tool calls, real model
    spend, on a turn the user never submitted.

    There is nothing to restore anyway. A fork's body never entered this
    session's context on the original turn either; only its response came back.
    So the correct result is the raw sigil, unchanged, and zero calls.
    """
    session, tool = _session()
    history = [_user(FORK_SIGIL)]

    out = await rehydrate_history_sigils(session, history, eligible=[True])

    assert out[0]["content"] == FORK_SIGIL, f"a fork skill's sigil was rewritten to {out[0]['content']!r}"
    assert tool.calls == [], "load_skill was invoked on a fork skill, which re-runs the fork"


@pytest.mark.asyncio
async def test_ineligible_entry_is_left_verbatim() -> None:
    """Host text wearing a user role is not re-hydrated.

    ``_contain_system_messages`` folds every client ``role: system`` message
    into ONE synthesized ``role: user`` entry, so by seeding time host-supplied
    text is indistinguishable from human text by role alone. The eligibility
    mask is what keeps them apart, and it is what keeps the e2e guard
    ``tests/e2e/suites/skills/test_sigil_dispatch.py::test_sigil_http_nonuser_role_guard``
    true: a sigil the host smuggled in through a system message must have no
    effect at all, on dispatch or on context.
    """
    session, tool = _session()
    containment = _user(f"<user_provided_instructions>\n{INLINE_SIGIL}\n</user_provided_instructions>")
    history = [containment, _user("a real question")]

    out = await rehydrate_history_sigils(session, history, eligible=[False, True])

    assert out[0]["content"] == containment["content"], "the synthesized containment entry was re-hydrated"
    assert tool.calls == []


@pytest.mark.asyncio
async def test_assistant_role_sigil_is_left_verbatim() -> None:
    """The role is re-checked even when the mask says eligible.

    Defense in depth: a mask built incorrectly by a future caller still cannot
    get a model-authored sigil re-hydrated.
    """
    session, tool = _session()
    history = [{"role": "assistant", "content": INLINE_SIGIL}]

    out = await rehydrate_history_sigils(session, history, eligible=[True])

    assert out[0]["content"] == INLINE_SIGIL
    assert tool.calls == []


@pytest.mark.asyncio
async def test_no_mask_still_requires_the_user_role() -> None:
    """With ``eligible=None`` the role check alone governs."""
    session, tool = _session()
    history = [{"role": "system", "content": INLINE_SIGIL}, _user(INLINE_SIGIL)]

    out = await rehydrate_history_sigils(session, history)

    assert out[0]["content"] == INLINE_SIGIL
    assert out[1]["content"].startswith(f"# {INLINE_SKILL}")
    assert tool.calls == [{"skill_name": INLINE_SKILL, "arguments": ""}]


@pytest.mark.asyncio
async def test_unknown_skill_leaves_the_raw_text() -> None:
    """A skill that is not in the catalog is not fabricated, and does not raise."""
    session, tool = _session()
    unknown = "!amplifier:skill no-such-skill-anywhere"
    history = [_user(unknown)]

    out = await rehydrate_history_sigils(session, history, eligible=[True])

    assert out[0]["content"] == unknown
    assert tool.calls == [], "an unknown skill must be rejected from metadata, without a load attempt"


@pytest.mark.asyncio
async def test_failed_load_leaves_the_raw_text() -> None:
    """``success=False`` is not a body. Leave what the user actually wrote."""
    session, tool = _session(load_succeeds=False)
    history = [_user(INLINE_SIGIL)]

    out = await rehydrate_history_sigils(session, history, eligible=[True])

    assert out[0]["content"] == INLINE_SIGIL
    assert tool.calls == [{"skill_name": INLINE_SKILL, "arguments": ""}], (
        "the load should have been attempted; only its result is unusable"
    )


@pytest.mark.asyncio
async def test_unresolvable_fork_status_leaves_the_raw_text() -> None:
    """Unknown fork status is treated as fork.

    The tool exposes no catalog and there is no ``skills_discovery`` capability,
    so we cannot tell an inline skill from a fork without loading it. A stale
    sigil in context is a degraded answer; a re-run fork is an unrequested side
    effect with real cost. Fail toward the cheaper mistake.
    """
    session, tool = _session(expose_catalog=False, discovery=None)
    history = [_user(INLINE_SIGIL)]

    out = await rehydrate_history_sigils(session, history, eligible=[True])

    assert out[0]["content"] == INLINE_SIGIL
    assert tool.calls == []


@pytest.mark.asyncio
async def test_skills_discovery_capability_is_the_fallback() -> None:
    """An older tool without ``get_effective_skills`` still resolves via the capability."""

    class _Discovery:
        def find(self, name: str) -> Any:
            return _Meta(name) if name == INLINE_SKILL else None

    session, tool = _session(expose_catalog=False, discovery=_Discovery())
    history = [_user(INLINE_SIGIL)]

    out = await rehydrate_history_sigils(session, history, eligible=[True])

    assert out[0]["content"].startswith(f"# {INLINE_SKILL}")
    assert tool.calls == [{"skill_name": INLINE_SKILL, "arguments": ""}]


@pytest.mark.asyncio
async def test_missing_load_skill_tool_returns_history_unchanged() -> None:
    """No tool mounted: degrade to the raw sigil rather than failing the turn."""
    session = _Session(None)
    history = [_user(INLINE_SIGIL)]

    out = await rehydrate_history_sigils(session, history, eligible=[True])

    assert out == history


# --------------------------------------------------------------------------- #
# Shapes and purity
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_callers_history_dicts_are_not_mutated() -> None:
    """The caller's list and dicts are shared with the request payload."""
    session, _tool = _session()
    original = _user(INLINE_SIGIL)
    history = [original]

    out = await rehydrate_history_sigils(session, history, eligible=[True])

    assert original["content"] == INLINE_SIGIL, "the caller's dict was rewritten in place"
    assert history[0] is original
    assert out[0] is not original


@pytest.mark.asyncio
async def test_single_text_part_content_is_rehydrated_in_place() -> None:
    """A one-part text list is a plain text turn; the list shape is preserved."""
    session, _tool = _session()
    history = [_user([{"type": "text", "text": INLINE_SIGIL}])]

    out = await rehydrate_history_sigils(session, history, eligible=[True])

    content = out[0]["content"]
    assert isinstance(content, list) and len(content) == 1, f"the content shape changed: {content!r}"
    assert content[0]["type"] == "text"
    assert content[0]["text"].startswith(f"# {INLINE_SKILL}")


@pytest.mark.asyncio
async def test_multi_part_content_is_left_alone() -> None:
    """Multi-part content is skipped rather than guessed at.

    Deciding which part "is" the sigil would mean rewriting text the user did
    not submit as one. A sigil is only ever the whole of a turn anyway.
    """
    session, tool = _session()
    parts = [{"type": "text", "text": INLINE_SIGIL}, {"type": "text", "text": "and also this"}]
    history = [_user(parts)]

    out = await rehydrate_history_sigils(session, history, eligible=[True])

    assert out[0]["content"] == parts
    assert tool.calls == []


# --------------------------------------------------------------------------- #
# Provenance at the source
# --------------------------------------------------------------------------- #


def test_contain_system_messages_marks_the_containment_entry_ineligible() -> None:
    """The mask must distinguish host text from human text at the point of extraction.

    ``_contain_system_messages`` is where provenance is destroyed: it writes the
    synthesized instructions entry with ``role: user``. If that entry were marked
    eligible, a client system message carrying a sigil would get the skill body
    injected into context, which is the exact privilege escalation the e2e
    non-user role guard exists to prevent.
    """
    messages = [
        ChatMessage(role="system", content=f"please run {INLINE_SIGIL}"),  # type: ignore[arg-type]
        ChatMessage(role="user", content="a real question"),  # type: ignore[arg-type]
        ChatMessage(role="assistant", content="an answer"),  # type: ignore[arg-type]
    ]

    history, eligible = _contain_system_messages(messages)

    assert len(eligible) == len(history), "the mask must stay parallel to the history it describes"
    assert history[0]["content"].startswith("<user_provided_instructions>")
    assert eligible == [False, True, False], (
        f"expected the containment entry ineligible and only the real user message eligible, got {eligible}"
    )

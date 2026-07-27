"""Face-agnostic skill-sigil dispatch, shared by the CLI and HTTP faces.

A turn prompt that begins with ``!amplifier:skill <name> [args]`` is routed
DETERMINISTICALLY to the mounted ``load_skill`` tool instead of being handed to
the model as ordinary text. That is what makes ``!amplifier:skill code-review``
reliably fire the skill (and, for fork skills, run the sub-session) rather than
depending on the model noticing the text and choosing to call the tool itself.
Any other prompt is untouched.

Why this module exists
----------------------
This logic previously lived in ``amplifier_agent_lib._runtime`` and was called
from exactly one site on the CLI/engine path, so the HTTP face silently did not
dispatch skills at all: ``/v1/skills`` advertised them as sigil-invocable while a
posted sigil reached the model as plain text, which then went looking for the
skill on its own initiative. Both faces now call the SAME function here.

``_runtime`` was the wrong home for shared code. It is private (leading
underscore) and heavy: it pulls in ``amplifier_foundation.session``,
``bundle.cache``, ``engine``, ``incremental_save``, ``persistence``, and
``session_store``. Importing it from ``amplifier_agent_http`` would couple the
HTTP face to the entire CLI/wire runtime. This module is a leaf: it imports only
the standard library, so it can never participate in an import cycle. The
dependency direction stays one-way, ``amplifier_agent_http -> amplifier_agent_lib``,
matching the existing imports of ``spawn``, ``wire_approval_provider``, and
``protocol_points.base``.

THE USER-TURN INVARIANT
-----------------------
**The sigil is honored ONLY on a human-authored user turn.** It is never scanned
against conversation history, never against host-supplied system or developer
messages (including the user-supplied-instructions wrapper that
``_contain_system_messages`` produces), never against assistant text, and never
against tool results.

This is a privilege boundary, not a formatting detail. Skills execute tools. If
the sigil were honored anywhere in the message list, any party that can place
text into the conversation could invoke a skill: the host via a system prompt,
the model itself by emitting the sigil in assistant text, or an upstream tool by
returning it in a result. Only the human's current turn carries the authority to
invoke one.

The invariant is ENFORCED, not merely documented. ``dispatch_skill_or_execute``
takes a required ``prompt_role`` and dispatches only when it is exactly
``"user"``; every other value falls through to a normal ``session.execute``. The
HTTP face threads the role from the message the prompt was extracted from, so the
gate is fed by an observed fact rather than an assumption.

This mirrors the intent of the mode-directive role gate in
``routes/chat_completions.py``, which likewise restricts by role so an echoed
marker cannot spoof a mode. Note the containment is the INVERSE: the mode
directive is host-authored and is accepted ONLY from system/developer messages,
whereas the sigil is human-authored and is accepted ONLY from a user turn.

RE-HYDRATION IS NOT DISPATCH
---------------------------
``rehydrate_history_sigils`` DOES scan replayed history for the sigil, which
reads at first like a contradiction of the invariant above. It is not. That
function never invokes a skill on behalf of a history message. It performs a
TEXT SUBSTITUTION: a sigil the human really did submit on an earlier turn is
replaced with the body that turn already expanded, so the HTTP face's reseeded
context matches what the model actually saw when it answered. Nothing is
dispatched, no fork ever runs, and the caller still has to say which history
entries came from a genuine client user message. See that function's docstring
for the full reasoning.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Any

logger = logging.getLogger(__name__)

# The exact skill-invocation sigil prefix.
SKILL_SIGIL = "!amplifier:skill"

# The only role permitted to invoke a skill via the sigil. See THE USER-TURN
# INVARIANT above.
USER_TURN_ROLE = "user"


def parse_skill_sigil(prompt: str) -> tuple[str, str] | None:
    """Parse a ``!amplifier:skill <name> [args]`` prompt.

    Returns ``(skill_name, arguments)`` when *prompt* (ignoring leading
    whitespace) begins with the exact sigil prefix AND names a skill;
    ``arguments`` is the remainder VERBATIM (may be ``""``). The args-passthrough
    contract requires exact preservation, so only the single separator between
    name and args is consumed; internal spacing inside the args is preserved.

    Returns ``None`` for non-sigil prompts and for a bare ``!amplifier:skill``
    with no skill name; both fall through to the normal ``session.execute`` loop.

    This is a pure string parse. It performs NO authorization check: callers must
    not call it with anything other than a user turn. Use
    ``dispatch_skill_or_execute``, which enforces that gate.
    """
    lead = prompt.lstrip()
    if lead != SKILL_SIGIL and not lead.startswith(SKILL_SIGIL + " ") and not lead.startswith(SKILL_SIGIL + "\t"):
        return None
    body = lead[len(SKILL_SIGIL) :].strip()
    if not body:
        return None
    parts = body.split(None, 1)
    skill_name = parts[0]
    arguments = parts[1] if len(parts) > 1 else ""
    return skill_name, arguments


async def dispatch_skill_or_execute(session: Any, prompt: str, *, prompt_role: str | None) -> str:
    """Route a turn prompt: deterministic skill sigil, else normal execute.

    Args:
        session: the live AmplifierSession for this turn.
        prompt: the turn prompt text.
        prompt_role: the role of the message ``prompt`` was taken from. Required
            and keyword-only so every call site declares it consciously. The
            sigil is parsed ONLY when this is exactly ``"user"`` (see THE
            USER-TURN INVARIANT in the module docstring). ``None`` is the
            fail-closed value for "not a human turn" (for example an empty
            continuation prompt after a host tool result); it never dispatches.

    Non-sigil prompts (the common case, including the model-invoked
    ``skill-tool-invocation`` eval where the agent itself decides to call
    ``load_skill``) flow through ``session.execute(prompt)`` UNCHANGED.

    For a sigil prompt the mounted ``load_skill`` tool is invoked directly with
    ``{"skill_name", "arguments"}``. Two return shapes are distinguished from the
    tool-skills source (``SkillsTool._load_skill`` / ``_execute_fork``):

    * INLINE skill -> ``result.output`` is a dict with a ``"content"`` key
      (``"# name\\n\\n<body-with-$ARGUMENTS-substituted>"``). The body is NOT
      executed by the tool, so we feed it back through ``session.execute`` so the
      agent actually FOLLOWS the skill instructions (e.g. writes its sentinel).
    * FORK skill -> ``result.output`` is a dict WITHOUT ``"content"`` (it carries
      ``"response"`` / ``"context": "fork"``); the skill already ran in a spawned
      sub-session, so its response text becomes the turn reply directly.

    Any error (tool absent, load failure, exception) is logged and falls back to
    running the original prompt unchanged; it is never silently dropped.
    """
    # THE GATE. Anything that is not the human's current turn is executed as
    # ordinary text, so a sigil sitting in history, in a host system message, in
    # assistant output, or in a tool result cannot invoke a skill.
    if prompt_role != USER_TURN_ROLE:
        if parse_skill_sigil(prompt) is not None:
            logger.warning(
                "skill sigil found in a non-user turn (role=%r); ignoring it and running the "
                "prompt through the normal agent loop. Only a user turn may invoke a skill.",
                prompt_role,
            )
        return await session.execute(prompt)

    parsed = parse_skill_sigil(prompt)
    if parsed is None:
        return await session.execute(prompt)

    skill_name, arguments = parsed
    load_skill_tool = session.coordinator.get("tools", "load_skill")
    if load_skill_tool is None:
        logger.warning(
            "skill sigil received but the load_skill tool is not mounted; "
            "running the prompt through the normal agent loop instead."
        )
        return await session.execute(prompt)

    try:
        result = await load_skill_tool.execute({"skill_name": skill_name, "arguments": arguments})
    except Exception as exc:  # tool execution should never crash the turn
        logger.warning(
            "load_skill '%s' raised %s: %s; running the prompt normally instead.",
            skill_name,
            type(exc).__name__,
            exc,
        )
        return await session.execute(prompt)

    if not getattr(result, "success", False):
        logger.warning(
            "skill '%s' did not load: %s; running the prompt normally.",
            skill_name,
            getattr(result, "error", None),
        )
        return await session.execute(prompt)

    output = result.output
    if isinstance(output, dict) and "content" in output:
        # INLINE skill: the tool substituted $ARGUMENTS but did NOT run the body.
        # Execute it so the agent follows the skill's instructions this turn.
        return await session.execute(output["content"])

    # FORK skill (or any non-content output): the skill already executed in a
    # spawned sub-session. Use its response/message as the turn reply verbatim.
    if isinstance(output, dict):
        return output.get("response") or output.get("message") or str(output)
    return str(output)


# --------------------------------------------------------------------------- #
# History re-hydration
#
# Everything below is SUBSTITUTION, not dispatch. See RE-HYDRATION IS NOT
# DISPATCH in the module docstring.
# --------------------------------------------------------------------------- #


def _sigil_carrier_text(content: Any) -> str | None:
    """Return the text of *content* if it is a shape we can substitute into.

    Two shapes are accepted, and only two:

    * a plain ``str``, the ordinary case; and
    * a list holding exactly ONE ``{"type": "text", "text": ...}`` part, which is
      how some clients (the Vercel AI SDK among them) encode a plain text turn.

    Anything else (multi-part content, an image part, ``None``) returns ``None``
    and is left alone. A multi-part message is deliberately excluded rather than
    handled: we would have to guess which part the sigil "is", and a sigil is
    only ever the whole of a user turn anyway. Guessing wrong would rewrite text
    the user did not submit as a sigil.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list) and len(content) == 1:
        part = content[0]
        if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
            return part["text"]
    return None


def _content_with_text(content: Any, text: str) -> Any:
    """Rebuild *content* carrying *text*, preserving the original shape.

    The list shape is preserved rather than flattened to a string: the client
    sent it that way and the kernel round-trips it, so changing the shape here
    would be an unrelated behavior change riding along with the substitution.
    """
    if isinstance(content, list) and len(content) == 1:
        part = content[0]
        if isinstance(part, dict):
            return [{**part, "text": text}]
    return text


def _make_skill_metadata_resolver(session: Any, load_skill_tool: Any) -> Callable[[str], tuple[bool, Any]]:
    """Build a ``name -> (resolved, metadata)`` lookup that never LOADS a skill.

    ``resolved`` says whether we were able to consult a catalog at all;
    ``metadata`` is the entry (or ``None`` when the catalog simply has no such
    skill). The two are distinct because they lead to the same action here but
    for opposite reasons, and conflating them would hide a broken lookup behind
    a routine "unknown skill".

    Why not just call ``load_skill`` and inspect the result: loading a
    ``context: fork`` skill RUNS the fork. The tool's ``info=`` path does not
    report fork-ness either. ``get_effective_skills()`` is the one surface that
    exposes ``SkillMetadata.context`` without executing anything, and it
    includes the runtime overlay (skills contributed by an active mode).

    The ``skills_discovery`` capability is the fallback for a host whose mounted
    tool predates ``get_effective_skills``. It only sees the STATIC mount-time
    catalog, so it misses overlay skills; a miss there is reported as "not
    found", which leaves the raw sigil in place. That is the safe direction.
    """
    catalog_raw: Any = None
    try:
        catalog_raw = load_skill_tool.get_effective_skills()
    except AttributeError:
        # Tool predates get_effective_skills. Fall through to the capability.
        catalog_raw = None
    except Exception as exc:
        logger.warning(
            "load_skill.get_effective_skills() raised %s: %s; falling back to the skills_discovery capability.",
            type(exc).__name__,
            exc,
        )
        catalog_raw = None

    if isinstance(catalog_raw, dict):
        catalog: dict[str, Any] = catalog_raw

        def _from_catalog(name: str) -> tuple[bool, Any]:
            return True, catalog.get(name)

        return _from_catalog

    discovery: Any = None
    get_capability = getattr(getattr(session, "coordinator", None), "get_capability", None)
    if callable(get_capability):
        try:
            discovery = get_capability("skills_discovery")
        except Exception as exc:
            logger.warning(
                "skills_discovery capability lookup raised %s: %s; skill sigils in history stay raw.",
                type(exc).__name__,
                exc,
            )
            discovery = None

    find = getattr(discovery, "find", None)
    if callable(find):

        def _from_discovery(name: str) -> tuple[bool, Any]:
            try:
                return True, find(name)
            except Exception as exc:
                logger.warning(
                    "skills_discovery.find(%r) raised %s: %s; leaving the sigil raw.",
                    name,
                    type(exc).__name__,
                    exc,
                )
                return False, None

        return _from_discovery

    def _unresolvable(name: str) -> tuple[bool, Any]:
        return False, None

    return _unresolvable


async def rehydrate_history_sigils(
    session: Any,
    history: list[dict[str, Any]],
    *,
    eligible: Sequence[bool] | None = None,
) -> list[dict[str, Any]]:
    """Replace a replayed skill sigil in *history* with that skill's inline body.

    Why this exists
    ---------------
    When ``!amplifier:skill <name>`` dispatches an INLINE skill, the tool expands
    the SKILL.md body and the body becomes that turn's prompt. A skill body is
    routinely not a one-shot instruction: it sets rules, a persona, or a working
    contract the rest of the conversation is meant to obey.

    On the CLI that survives, because ``_runtime`` persists the post-turn context
    and ``--resume`` reads it back, so the expanded body is in the restored
    transcript verbatim. On the HTTP face it does not. The next POST carries the
    CLIENT's history, ``[user: <sigil>, assistant: <reply>, user: <new>]``, and
    ``_session_runner`` seeds the session from that RAW sigil text. From turn 2
    onward the model sees six words of sigil where the skill body used to be, and
    a question only the body can answer gets a hallucinated answer.

    This function closes that gap by making the reseeded context match what the
    model actually saw when it produced the reply already sitting in history.

    Substitution, not dispatch
    --------------------------
    Nothing here invokes a skill on behalf of a history message. The sigil being
    substituted is one the human really did submit on an earlier turn, and it
    really did run then; we are restoring the text of a past turn, not deciding
    to take an action now. The caller still has to say which history entries came
    from a genuine client ``role: user`` message (see *eligible*), and the role
    is re-checked here regardless, so host-supplied text can never be treated as
    a sigil. This is why the substitution does not violate THE USER-TURN
    INVARIANT in the module docstring.

    Fork skills are excluded
    ------------------------
    Re-hydrating a ``context: fork`` skill would be both meaningless and
    dangerous. Meaningless because a fork's body never entered this session's
    context on the original turn either: it ran in a spawned sub-session and only
    its response came back, so there is nothing that was lost to restore.
    Dangerous because the only way to obtain the body is to load the skill, and
    loading a fork skill RUNS the fork, which would silently re-execute a
    sub-session on every subsequent POST of the same conversation.

    UNKNOWN FORK STATUS IS TREATED AS FORK. If we cannot resolve a skill's
    metadata without loading it, we leave the raw sigil in place rather than
    risk calling ``.execute()`` on something that turns out to be a fork. A
    stale sigil in context is a degraded answer; a re-run fork is an unrequested
    side effect. Those are not comparable costs.

    Accepted consequence: the inline load path emits ``skill:loaded``, so an
    observer sees one such event per re-hydrated sigil per turn. That is
    intentional and matches what the original turn did. The event is a
    notification, not an action, and no fork, tool, or model call rides on it.

    Args:
        session: the live AmplifierSession for this turn. Must already have been
            created, since the ``load_skill`` tool is resolved from its
            coordinator.
        history: the client history about to be seeded via ``set_messages``.
            Never mutated.
        eligible: a mask parallel to *history*. ``True`` marks an entry that came
            from a genuine client ``role: user`` message. Entries the HTTP face
            synthesized (notably the ``<user_provided_instructions>`` containment
            message, which is written with ``role: user`` but carries HOST text)
            must be ``False``. ``None`` means the caller declared no provenance,
            in which case only the role check applies.

    Returns:
        A new list with the substitutions applied, or the original list object
        when nothing changed.
    """
    if not history:
        return history

    if eligible is not None and len(eligible) != len(history):
        # A mismatched mask means the caller's provenance tracking has drifted
        # from the history it describes. Every index below then fails the bounds
        # check and stays raw, which is the fail-closed direction, but it is a
        # bug worth surfacing rather than absorbing silently.
        logger.warning(
            "history sigil eligibility mask has %d entries for %d history messages; "
            "treating out-of-range entries as ineligible.",
            len(eligible),
            len(history),
        )

    # Pass 1: find the candidates WITHOUT touching the coordinator. The common
    # case is a conversation with no sigil at all, and it should cost one scan
    # and nothing else.
    candidates: list[tuple[int, str, str]] = []  # (index, skill_name, arguments)
    for i, item in enumerate(history):
        if eligible is not None and (i >= len(eligible) or not eligible[i]):
            continue
        # Defense in depth: the mask alone is not trusted. A caller that passes
        # no mask, or a future caller that builds one incorrectly, still cannot
        # get a non-user message re-hydrated.
        if not isinstance(item, dict) or item.get("role") != USER_TURN_ROLE:
            continue
        text = _sigil_carrier_text(item.get("content"))
        if text is None:
            continue
        parsed = parse_skill_sigil(text)
        if parsed is None:
            continue
        candidates.append((i, parsed[0], parsed[1]))

    if not candidates:
        return history

    load_skill_tool = session.coordinator.get("tools", "load_skill")
    if load_skill_tool is None:
        logger.warning(
            "%d skill sigil(s) in replayed history but the load_skill tool is not mounted; "
            "the raw sigil text stays in context and the skill body will be missing.",
            len(candidates),
        )
        return history

    resolve_metadata = _make_skill_metadata_resolver(session, load_skill_tool)

    replacements: dict[int, Any] = {}
    for index, skill_name, arguments in candidates:
        resolved, meta = resolve_metadata(skill_name)
        if not resolved:
            logger.warning(
                "cannot determine whether skill '%s' is a fork without loading it; "
                "leaving the raw sigil in history rather than risk re-running a fork.",
                skill_name,
            )
            continue
        if meta is None:
            # Unknown skill. Leave the user's text exactly as they wrote it; do
            # not fabricate a body for something that does not exist.
            logger.info(
                "skill '%s' from replayed history is not in the catalog; leaving the raw sigil in context.",
                skill_name,
            )
            continue
        if getattr(meta, "context", None) == "fork":
            continue

        try:
            result = await load_skill_tool.execute({"skill_name": skill_name, "arguments": arguments})
        except Exception as exc:
            logger.warning(
                "re-hydrating skill '%s' raised %s: %s; leaving the raw sigil in context.",
                skill_name,
                type(exc).__name__,
                exc,
            )
            continue

        if not getattr(result, "success", False):
            logger.warning(
                "skill '%s' did not load while re-hydrating history: %s; leaving the raw sigil in context.",
                skill_name,
                getattr(result, "error", None),
            )
            continue

        output = getattr(result, "output", None)
        if not isinstance(output, dict) or "content" not in output:
            # No body to substitute. Reached when a skill's fork status could not
            # be read from metadata but the tool ran it as a fork anyway; there
            # is nothing to restore, so the raw text stays.
            logger.warning(
                "skill '%s' returned no inline content while re-hydrating history; leaving the raw sigil in context.",
                skill_name,
            )
            continue

        replacements[index] = output["content"]

    if not replacements:
        return history

    # Build NEW dicts. The caller's history items may be shared with (or derived
    # from) the request payload, and a caller that hands us a list has not agreed
    # to have it rewritten under them.
    out: list[dict[str, Any]] = []
    for i, item in enumerate(history):
        if i in replacements:
            new_item = dict(item)
            new_item["content"] = _content_with_text(item.get("content"), replacements[i])
            out.append(new_item)
        else:
            out.append(item)

    logger.info(
        "re-hydrated %d skill sigil(s) in replayed history so the expanded body survives the turn boundary.",
        len(replacements),
    )
    return out

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
"""

from __future__ import annotations

import logging
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

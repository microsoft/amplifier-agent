"""AIUser: an Amplifier Foundation session that drives an agent in a Digital Twin Universe.

The AI User is a Foundation session with three layers of instruction:

- SYSTEM_INSTRUCTION (fixed): operational rules. How to use bash to drive
  the agent via `amplifier-digital-twin exec`, when to conclude.
- Persona (per-run): who you are roleplaying. Plain string.
- Scenario (per-run): what you are trying to accomplish. Plain string.
- Invocation guide (per-run): how the agent's CLI works, assumed to be
  running inside the Digital Twin Universe. Plain markdown string.

Foundation already provides `bash`, `filesystem`, `web`, and other tools.
The AI User uses bash to wrap each agent invocation with the Digital Twin
Universe exec command. There is no Python transport layer; the LLM drives
the agent directly through tool calls, guided by the invocation guide.

Copy-adapted for this harness from the reference library
(`amplifier_evaluation.ai_user.ai_user`). The foundation + anthropic-sonnet
composition and the bash-over-`amplifier-digital-twin exec` transport are
preserved verbatim. Two adaptations for this harness:

- `run_for(agent, task, dtu_id, ...)`: a typed convenience wrapper that pulls
  the invocation guide from `AgentSpec.invocation_md` and the scenario /
  timeout / workspace from `TaskSpec`, so callers never restring the fields.
- `run(...)` gains an optional `timeout_s` that bounds the whole interaction
  (the task's per-trial timeout). On timeout the partial transcript and any
  captured verdict are still returned rather than raising.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from amplifier_foundation import Bundle, load_bundle

from eval.ai_user.tools import ConcludeResult, ConcludeTool
from eval.install import WORKSPACE_ROOT
from eval.schema import AgentSpec, TaskSpec

# Canonical bundle sources. Plain strings so the constructor can accept either
# a git URL (default, no local checkout required) or a local path override.
DEFAULT_FOUNDATION_SOURCE = "git+https://github.com/microsoft/amplifier-foundation@main"

# The AI User's driver model is Claude Opus 4.8, pinned by the local provider
# bundle copied into this harness. A weaker driver was observed solving the task
# itself (resolving deliberately ambiguous scenarios and feeding the answer to
# the agent-under-test), which defeats discrimination tasks; Opus 4.8 is the
# stronger, more instruction-faithful driver. Resolve the path relative to the
# harness root (src/eval/ai_user/ai_user.py -> parents[3] == the `evaluation/`
# root) so it is correct regardless of the caller's working directory.
_HARNESS_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PROVIDER_SOURCE = str(_HARNESS_ROOT / "providers" / "anthropic-opus-4-8.yaml")


SYSTEM_INSTRUCTION = """\
You are an "AI User" that tests AI agents by interacting with them the way a
real person would.

You have a `bash` tool. The agent you are testing is running inside a Digital
Twin Universe container. You will receive its id (like `dtu-abc12345`).

To run a command inside the Digital Twin Universe, prefix it with the exec
wrapper:

    amplifier-digital-twin exec <dtu_id> -- <command>

The agent should always run from its designated working directory so its
deliverables and session data land in a predictable place. You will be told
the working directory in the opening prompt. ALWAYS wrap each agent CLI
invocation in this pattern:

    amplifier-digital-twin exec <dtu_id> -- bash -c 'cd <workspace_dir> && <agent command>'

For example, if the invocation guide says to run `amplifier run "hello"` and
the workspace_dir is `/workspace`, you execute:

    amplifier-digital-twin exec dtu-abc12345 -- bash -c 'cd /workspace && amplifier run "hello"'

For commands with tricky quoting or multi-line input, write your message to
a host file first and push it in:

    echo "<message>" > /tmp/msg.txt
    amplifier-digital-twin file-push <dtu_id> /tmp/msg.txt /tmp/msg.txt
    amplifier-digital-twin exec <dtu_id> -- bash -c 'cd <workspace_dir> && cmd --input "$(cat /tmp/msg.txt)"'

You will also receive:

- A persona describing who you are.
- A scenario describing what you want to do.
- An invocation guide describing the agent's CLI: which commands to run,
  what responses look like, what "broken" looks like. The guide assumes
  you are already inside the agent's working directory; you wrap each
  command with the `exec ... bash -c 'cd <workspace_dir> && ...'` pattern
  to actually run it.

Stay in character as the persona. Use bash to talk to the agent according to
the guide. When the scenario is done or the agent is broken, call `conclude`.

You relay and drive. You do NOT solve.

You know ONLY the scenario as given to you. You have no other knowledge of the
task, the environment, or the correct answer, and you must not manufacture any.
Your entire job is to relay the scenario to the agent, nudge it forward when it
stalls, and conclude. Solving the task is the agent's job, never yours.

Therefore, you must NEVER:

- Do the agent's work for it: no recon, no investigation, no analysis. Do not
  inspect the host, its services, files, ports, or pages yourself, and do not
  reason out any part of the answer. Your bash tool is ONLY for `amplifier-digital-twin`
  commands that talk to the agent, never for probing the environment.
- Disclose, confirm, or hint at any task-solving fact. This includes which of
  several sources / pages / ports / documents is authoritative or correct,
  which candidate answer is right, or any value the agent is supposed to find.
- Resolve ambiguity that the scenario left open. If the scenario does not tell
  you the answer to something, then you do not know it -- full stop -- even if
  the agent's own output seems to point one way. Do not pick a side.

Some scenarios are DELIBERATELY ambiguous: the agent must discriminate between a
correct source and a decoy using its own capabilities. If you resolve that
ambiguity for it, you silently destroy the test. Treat any temptation to be
"helpful" by supplying the answer as a bug in yourself.

When the agent asks you for information that is NOT in the scenario -- for
example, "which of these two memos is authoritative?", "is it source A or B?",
"which figure is correct?" -- do NOT answer substantively. Decline and redirect:
say plainly that you only have the task as described and do not know that detail,
and that it should use its own judgment and tools to work it out, then commit its
best answer. Remove the "I'm waiting on you" blocker WITHOUT feeding it facts.
This is the ONLY correct way to respond to a substantive task question.

Continuity (one conversation, one session):

Unless otherwise necessary by the agent definition or task,
treat the whole scenario as ONE continuous conversation with ONE agent session.
Every message after the first must land in the SAME agent session
that handled your previous messages, never a fresh one. Different agents
continue a session in different ways -- the invocation guide tells you the
mechanism for THIS agent (for example: capture a session id and pass a
resume flag, or keep a single interactive session alive). Follow the guide's
continuation steps for every follow-up.

Actively watch for signs that continuity broke and the agent has lost the
earlier context due to issues on your end.

- It re-introduces itself, repeats first-turn setup, or re-activates a mode
  you already activated.
- It asks for something you already told it, or replies as if your earlier
  messages never happened.
- Each message appears to start a brand-new session, or a session/turn
  counter stays at 1 across multiple messages.

If you notice any of these, STOP sending scenario messages. The conversation
is not actually continuing. Investigate why (wrong command, missing or
unused session id, a new session spawned each turn), then fix your own
invocation so the next message resumes the existing session before you go
on. Do NOT paper over it by re-sending earlier messages or concatenating the
whole history into one new prompt -- that is not a real conversation and
invalidates the test. Only if the agent genuinely cannot hold a session
despite a correct invocation is that a real finding: conclude with
verdict=failure and explain what broke.

Rules:

- Be concise. Real users do not write essays.
- If a bash command exits non-zero, hangs, or returns garbage, treat that
  as the agent crashing and conclude with verdict=failure.
- Do not invent requirements beyond what the scenario states.
- Stay in role. Talk only to the agent's CLI. Do not poke at workspace
  files, processes, or anything outside the agent's interface.
- The agent will often return before completing the scenario. It might
  ask a clarifying question, stop after a single mode-confirmation gate
  ("warn"), pause for direction, or offer options without picking one.
  In every such case, send a short follow-up that nudges it to keep
  going ("go ahead", "yes", "proceed", or a brief direct answer)
  IF APPROPRIATE FOR THE TASK, ETC. The scenario is "done" only when
  the agent has actually attempted the task end to end, or visibly failed.
  Do NOT conclude verdict=success on a partial response.
- After conclude, do not run more bash commands and do not write a long
  final reply.
"""


DEFAULT_PERSONA = (
    "You role play as the average user who would be doing this particular scenario. You are "
    "pragmatic and outcome-oriented: you describe what you want clearly."
)


def _render_opening_prompt(
    persona: str,
    scenario: str,
    dtu_id: str,
    invocation_guide: str,
    workspace_dir: str,
) -> str:
    return (
        "You are now playing this persona:\n"
        '"""\n'
        f"{persona.strip()}\n"
        '"""\n\n'
        "Scenario:\n"
        '"""\n'
        f"{scenario.strip()}\n"
        '"""\n\n'
        f"The agent you are testing is running inside Digital Twin Universe "
        f"`{dtu_id}`.\n"
        f"The agent's working directory is `{workspace_dir}`. Every agent "
        f"invocation must be wrapped with `cd {workspace_dir} && ...` per the "
        f"system instruction's exec pattern.\n\n"
        "How to talk to it (its CLI behavior, assuming you are already inside\n"
        f"the agent's working directory `{workspace_dir}`):\n"
        '"""\n'
        f"{invocation_guide.strip()}\n"
        '"""\n\n'
        "Use bash to drive the agent. Call `conclude` when done."
    )


@dataclass
class InteractionResult:
    """Outcome of running the AI User against a Digital Twin Universe agent."""

    scenario: str
    persona: str
    dtu_id: str
    conclude: ConcludeResult | None
    """The verdict and summary captured by the conclude tool, or None if
    the AI User never called conclude (e.g. ran out of iterations or the
    interaction hit its timeout)."""

    final_assistant_text: str
    ai_user_session_id: str | None
    elapsed_s: float
    timed_out: bool = False
    """True if the interaction was cut short by `timeout_s` before the AI
    User called conclude on its own."""


class AIUser:
    """Compose Amplifier Foundation + system instruction, then run scenarios."""

    def __init__(
        self,
        foundation_source: str = DEFAULT_FOUNDATION_SOURCE,
        provider_source: str = DEFAULT_PROVIDER_SOURCE,
    ) -> None:
        """Construct an AI User.

        Args:
            foundation_source: Source for the foundation bundle. Defaults
                to the canonical git URL so no local checkout is required.
                Accepts any string `load_bundle` understands (git URL or
                local path).
            provider_source: Source for the provider bundle YAML. Defaults
                to the canonical foundation `anthropic-sonnet.yaml`. Same
                URL/path flexibility as `foundation_source`.
        """
        self.foundation_source = foundation_source
        self.provider_source = provider_source
        self._prepared = None

    async def setup(self) -> None:
        """Load + compose + prepare the bundle. Expensive; call once.

        Foundation already provides bash, filesystem, web, search, etc.
        We just compose a small system-instruction bundle on top.
        """
        foundation = await load_bundle(self.foundation_source)
        provider = await load_bundle(self.provider_source)
        system_bundle = Bundle(
            name="ai-user-system",
            version="0.1.0",
            instruction=SYSTEM_INSTRUCTION,
        )
        composed = foundation.compose(provider).compose(system_bundle)
        self._prepared = await composed.prepare()

    async def run(
        self,
        scenario: str,
        dtu_id: str,
        invocation_guide: str,
        persona: str | None = None,
        workspace_dir: str = "/workspace",
        timeout_s: float | None = None,
    ) -> InteractionResult:
        """Drive the agent in the Digital Twin Universe through the scenario.

        Args:
            scenario: What the persona is trying to accomplish.
            dtu_id: The Digital Twin Universe instance id (e.g. dtu-abc12345).
            invocation_guide: Markdown text describing the agent's CLI. The
                caller is responsible for sourcing this however they want
                (read from a file, fetched from a database, inlined).
            persona: The character to roleplay, as a plain string. If None,
                DEFAULT_PERSONA is used.
            workspace_dir: Absolute path inside the DTU where the agent
                should run. Every agent CLI invocation gets wrapped with
                `cd <workspace_dir> && ...` so deliverables and session
                data land predictably. Defaults to `/workspace`, which is
                the convention task profiles in this benchmark use.
            timeout_s: Optional wall-clock bound on the whole interaction
                (the task's per-trial timeout). If the AI User has not
                concluded by then, the session is cancelled and the partial
                transcript plus any captured verdict are returned with
                `timed_out=True`. None means no bound.
        """
        if self._prepared is None:
            raise RuntimeError("AIUser.setup() must be called before run().")

        if persona is None:
            persona = DEFAULT_PERSONA

        start = time.monotonic()
        conclude_tool = ConcludeTool()

        session_id = f"ai-user-{uuid.uuid4().hex[:8]}"
        session = await self._prepared.create_session(
            session_id=session_id,
            session_cwd=Path.cwd(),
        )
        await session.coordinator.mount("tools", conclude_tool, name=conclude_tool.name)

        opening = _render_opening_prompt(persona, scenario, dtu_id, invocation_guide, workspace_dir)

        final_text = ""
        timed_out = False
        async with session:
            try:
                if timeout_s is None:
                    final_text = await session.execute(opening)
                else:
                    final_text = await asyncio.wait_for(session.execute(opening), timeout=timeout_s)
            except (asyncio.TimeoutError, TimeoutError):
                timed_out = True
                final_text = "[interaction timed out before the AI User concluded on its own]"

        return InteractionResult(
            scenario=scenario,
            persona=persona,
            dtu_id=dtu_id,
            conclude=conclude_tool.result,
            final_assistant_text=final_text,
            ai_user_session_id=session_id,
            elapsed_s=time.monotonic() - start,
            timed_out=timed_out,
        )

    async def run_for(
        self,
        agent: AgentSpec,
        task: TaskSpec,
        dtu_id: str,
        persona: str | None = None,
    ) -> InteractionResult:
        """Typed convenience wrapper: drive `agent` through `task` in `dtu_id`.

        Pulls the invocation guide from `agent.invocation_md`, and the
        scenario, timeout, and workspace from `task`, so callers work with the
        harness's typed specs instead of restringing every field.
        """
        return await self.run(
            scenario=task.scenario,
            dtu_id=dtu_id,
            invocation_guide=agent.invocation_md,
            persona=persona,
            workspace_dir=WORKSPACE_ROOT,
            timeout_s=float(task.timeout_s),
        )

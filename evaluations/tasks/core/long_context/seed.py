"""Rebuild sessions/long-context-seed by driving a real session over workspace/.

Run from the amplifier-agent repository root with ANTHROPIC_API_KEY set:

    uv run python evaluations/tasks/core/long_context/seed.py

The session is created in a temporary storage root with the working directory set to
workspace/, then its transcript, turns, and metadata are copied into sessions/ with the
working directory rewritten to /workspace, where the trial container mounts it.
"""

import asyncio
import os
from pathlib import Path
import shutil
import sys
import tempfile

from amplifier_agent import AgentOptions, SessionOptions, TextPart, TurnInput, create_agent

SESSION_ID = "long-context-seed"
TASK = Path(__file__).resolve().parent
WORKSPACE = TASK / "workspace"
SESSIONS = TASK / "sessions" / SESSION_ID
FILES = ("transcript.jsonl", "turns.jsonl", "metadata.json")

PROMPTS = [
    (
        "Context: internally this workstream is codenamed HALCYON, so if I say HALCYON later I mean this repo. "
        "I'm onboarding onto it today. Read README.md in full and give me a short summary of what the module does "
        "and what the main knobs are."
    ),
    (
        "Read amplifier_module_context_simple/__init__.py from the top through the end of the SimpleContextManager "
        "constructor (stop around line 900). List every config option the mount function accepts with its default."
    ),
    (
        "Keep going: read lines 900 to 2000 of amplifier_module_context_simple/__init__.py. Give me every method "
        "defined in that range with a one line description each."
    ),
    (
        "Now read the rest of amplifier_module_context_simple/__init__.py, from line 2000 to the end. Same format: "
        "every method with a one line description. Then tell me how the compaction ladder levels are ordered."
    ),
    (
        "Read the other files in amplifier_module_context_simple/ (everything that is not __init__.py) in full and "
        "explain how token estimation works, including what is excluded from the estimate."
    ),
    (
        "grep the whole repo for every hooks emit call (`emit(`) and list each event name with the payload keys it "
        "carries. Include tests so I can see what the tests assert about payloads."
    ),
    (
        "Read tests/test_progressive_compaction.py and tests/test_protected_tool_results.py in full. What does the "
        "ladder guarantee about protected tool results and the most recent messages?"
    ),
    (
        "Read tests/test_request_retention.py and tests/test_measured_request_view.py in full. Explain hard_fit and "
        "how retained contents survive compaction."
    ),
    (
        "Read tests/test_tool_result_ingress.py, tests/test_budget_guard.py and tests/test_budget_event.py in full "
        "and summarize what each one pins down."
    ),
    (
        "Use bash to give me line counts for every file in the repo, sorted by size, and tell me which three test "
        "files are longest and what they cover based on what you have read so far."
    ),
    (
        "Read tests/test_compaction_storm_irreducible_floor.py in full. What is the irreducible floor and why does "
        "it matter for very long sessions?"
    ),
    "There is an incident writeup under docs/. Read it and give me the tracking id and the root cause in two lines.",
]


async def drive(storage: Path) -> None:
    options = AgentOptions(provider="anthropic", model="claude-sonnet-5", approvals="allow", storage=storage)
    async with await create_agent(options) as agent:
        session = await agent.create_session(SessionOptions(session_id=SESSION_ID, persistence="durable"))
        async with session:
            for index, prompt in enumerate(PROMPTS):
                result = await session.run(TurnInput([TextPart(prompt)]))
                print(f"turn {index}: {result.state}", flush=True)
                if result.state != "success":
                    raise SystemExit(f"turn {index} ended {result.state}: {result.error}")


def main() -> None:
    os.environ["AMPLIFIER_AGENT_WORKSPACE"] = "main"
    with tempfile.TemporaryDirectory() as temp:
        storage = Path(temp) / "storage"
        os.chdir(WORKSPACE)
        asyncio.run(drive(storage))
        source = storage / "workspaces" / "main" / "sessions" / SESSION_ID
        shutil.rmtree(SESSIONS, ignore_errors=True)
        SESSIONS.mkdir(parents=True)
        for name in FILES:
            text = (source / name).read_text(encoding="utf-8").replace(str(WORKSPACE), "/workspace")
            (SESSIONS / name).write_text(text, encoding="utf-8")
    print(f"seeded {SESSIONS}")


if __name__ == "__main__":
    sys.exit(main())

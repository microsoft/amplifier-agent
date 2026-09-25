"""BREAK 5 -- a message carrying loaded-tool state must survive compaction.

FAIL-BEFORE / PASS-AFTER guard for the `context-simple` half of
`model_performance-v5co`. Belongs at `tests/test_loaded_tool_state_protection.py`
in `amplifier-module-context-simple`; it ships here because this lane's worktree
does not contain that repo (see BREAK5-PATCH.md).

`TS:854` makes dropping a `tool_search_output` record cost twice: every tool it
loaded silently ceases to exist for the model, AND the prompt cache breaks
forward from that point. OpenAI's cache is grow-only (`00-what-we-know.md` §2a),
so "breaks forward" means a full cold rebuild, not a dip.

The ladder had no concept of an un-droppable item, so at the unpatched tree the
message is removed like any other and the failure is silent.
"""

from __future__ import annotations

import asyncio
from typing import Any

from amplifier_module_context_simple import SimpleContextManager

TOOL_SEARCH_ITEMS_KEY = "openai:tool_search_items"

HOSTED_ITEMS = [
    {
        "type": "tool_search_call",
        "execution": "server",
        "call_id": None,
        "arguments": {"paths": ["files"]},
    },
    {
        "type": "tool_search_output",
        "execution": "server",
        "call_id": None,
        "tools": [{"type": "function", "name": "glob", "namespace": "files"}],
    },
]

FILLER = "x" * 4000


def _manager() -> SimpleContextManager:
    return SimpleContextManager(
        max_tokens=2_000,
        compact_threshold=0.5,
        protected_recent=0.10,
        protected_tool_results=1,
    )


async def _build_async(mgr: SimpleContextManager) -> list[dict[str, Any]]:
    """A long conversation whose SECOND turn carries the loaded-tool state.

    Deliberately early in the history and outside the protected tail, so an
    unpatched ladder removes it. `add_message` is a coroutine -- awaiting it is
    what makes this test non-vacuous.
    """
    await mgr.add_message({"role": "user", "content": "start " + FILLER})
    await mgr.add_message(
        {
            "role": "assistant",
            "content": "searching for file tools " + FILLER,
            "metadata": {TOOL_SEARCH_ITEMS_KEY: HOSTED_ITEMS},
        }
    )
    for n in range(12):
        await mgr.add_message({"role": "assistant", "content": f"step {n} " + FILLER})
        await mgr.add_message({"role": "user", "content": f"go on {n} " + FILLER})
    # get_messages() is the FULL history and is never compacted; the request
    # path is what runs the ladder.
    return await mgr.get_messages_for_request(token_budget=2_000)


def _build(mgr: SimpleContextManager) -> list[dict[str, Any]]:
    out = asyncio.run(_build_async(mgr))
    assert len(mgr.messages) == 26, "fixture did not land; nothing to compact"
    return out


def _carriers(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [m for m in messages if (m.get("metadata") or {}).get(TOOL_SEARCH_ITEMS_KEY)]


def test_loaded_tool_state_survives_compaction():
    mgr = _manager()
    out = _build(mgr)
    carriers = _carriers(out)
    assert len(carriers) == 1, (
        "the message recording which tools were dynamically loaded was dropped "
        "by compaction -- per TS:854 those tools now cease to exist for the "
        "model AND the prompt cache breaks forward, silently (break 5)."
    )
    assert carriers[0]["metadata"][TOOL_SEARCH_ITEMS_KEY] == HOSTED_ITEMS


def test_compaction_still_happened():
    """Guard against a vacuous pass: worthless if nothing was compacted."""
    mgr = _manager()
    out = _build(mgr)
    assert len(out) < 26, "nothing was compacted; the protection was never exercised"


def test_ordinary_messages_are_still_removable():
    """The protection must be narrow -- it is not a blanket do-not-compact."""
    mgr = _manager()
    out = _build(mgr)
    contents = " ".join(
        str(m.get("content") or "") for m in out if m.get("role") == "assistant"
    )
    assert "step 0" not in contents or "step 1" not in contents

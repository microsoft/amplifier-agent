"""
Regression tests for `protected_tool_results` (negative-slice bug).

THE BUG (fixed by the change these tests accompany): the protected set was
computed as

    protected_tool_indices = set(tool_result_indices[-self.protected_tool_results:])

and Python's `list[-0:]` is `list[0:]` -- the WHOLE list. So the one value
that reads as "protect nothing", `protected_tool_results=0`, protected
EVERYTHING: every truncation rung (levels 1, 2, 4, 6) became a no-op and
compaction escalated straight to message REMOVAL, which is strictly more
lossy than the truncation it skipped.

The workload below is tuned so the contrast is unambiguous rather than
marginal: with 8 tool results of 800 chars each (~2,444 raw tokens) against a
3,000-token budget at `target_usage=0.60` (target 1,800), truncating the
oldest 50% of tool results (waves 1+2 = indices 0-3) is *exactly* enough to
reach target at level 2 without removing a single message.

Measured on the pre-change module (`git show HEAD:` side-by-side), the
`protected_tool_results=0` case reached level 3 with 0 truncations and 6
messages removed. The same case on the fixed module reaches level 2 with 4
truncations and 0 messages removed.
"""

import pytest

from amplifier_module_context_simple import SimpleContextManager

N_TOOL_PAIRS = 8
TOOL_RESULT_CHARS = 800


def _make_context(protected_tool_results: int) -> SimpleContextManager:
    """A manager whose only varying knob is `protected_tool_results`."""
    return SimpleContextManager(
        max_tokens=3000,
        compact_threshold=0.5,
        target_usage=0.60,
        truncate_chars=50,
        protected_recent=0.5,
        protected_tool_results=protected_tool_results,
        # Without this, the default notice reserve eats the whole budget and
        # silently disables compaction (see test_budget_guard.py).
        compaction_notice_enabled=False,
    )


async def _run_workload(ctx: SimpleContextManager) -> dict:
    """Fill `ctx` with N_TOOL_PAIRS tool pairs, compact, and report what happened.

    `truncated_tool_ids` is read from the sticky decision store rather than
    from the returned view, so a tool result that was truncated and *then*
    removed is not silently miscounted as "never truncated".
    """
    for i in range(N_TOOL_PAIRS):
        await ctx.add_message({"role": "user", "content": f"ask {i}"})
        await ctx.add_message(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": f"t{i}",
                        "type": "function",
                        "function": {"name": "read_file"},
                    }
                ],
            }
        )
        await ctx.add_message(
            {
                "role": "tool",
                "tool_call_id": f"t{i}",
                "content": "x" * TOOL_RESULT_CHARS,
            }
        )

    await ctx.get_messages_for_request()
    stats = ctx._last_compaction_stats or {}
    truncated_tool_ids = sorted(
        msg["tool_call_id"]
        for msg in ctx.messages
        if msg.get("role") == "tool"
        and SimpleContextManager._extract_seq(msg) in ctx._truncated_seqs
    )
    return {
        "level": stats.get("strategy_level"),
        "messages_removed": stats.get("messages_removed"),
        "messages_truncated": stats.get("messages_truncated"),
        "truncated_tool_ids": truncated_tool_ids,
    }


# --------------------------------------------------------------------------
# The regression itself: 0 must protect ZERO tool results.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_zero_protects_zero_tool_results():
    """`protected_tool_results=0` protects nothing -- tool results ARE truncated.

    This is the test that fails on the pre-change module. There, `[-0:]`
    protected all 8 tool results, no truncation rung could fire, and
    compaction escalated to message removal instead.
    """
    result = await _run_workload(_make_context(protected_tool_results=0))

    assert result["truncated_tool_ids"] == ["t0", "t1", "t2", "t3"], (
        "With protected_tool_results=0 the protected set must be EMPTY, so the "
        "oldest 50% of tool results (waves 1+2) are truncated. Pre-fix this "
        f"was [] because [-0:] protected everything. Got: {result}"
    )
    assert result["messages_truncated"] == 4
    assert result["level"] == 2, (
        "Truncation alone must reach target at level 2. Pre-fix this escalated "
        f"to level 3 (message removal). Got: {result}"
    )
    assert result["messages_removed"] == 0, (
        "No message may be REMOVED when truncation alone reaches target. "
        f"Pre-fix 6 messages were removed. Got: {result}"
    )


# --------------------------------------------------------------------------
# The other half of the contract: N protects EXACTLY the last N.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_n_protects_exactly_the_last_n_boundary():
    """The protected boundary sits exactly at the last N tool results.

    With 8 tool results, truncation waves 1+2 cover indices 0-3. The 5th
    tool result from the end is index 3 -- the last one truncation needs.

    * N=4 protects indices 4-7 -> index 3 is truncatable -> target reached by
      truncation alone (level 2, nothing removed).
    * N=5 protects indices 3-7 -> index 3 is now off-limits -> truncation can
      no longer reach target and compaction must escalate to removal.

    Off-by-one in either direction moves that flip to a different N, so this
    pins the boundary rather than merely "some protection happens".
    """
    at_four = await _run_workload(_make_context(protected_tool_results=4))
    at_five = await _run_workload(_make_context(protected_tool_results=5))

    assert at_four["truncated_tool_ids"] == ["t0", "t1", "t2", "t3"], at_four
    assert at_four["level"] == 2, at_four
    assert at_four["messages_removed"] == 0, at_four

    assert at_five["level"] == 3, (
        "Protecting the 5th-from-last tool result must withhold index 3 from "
        f"truncation and force escalation to removal. Got: {at_five}"
    )
    assert at_five["messages_removed"] > 0, at_five


@pytest.mark.asyncio
async def test_protecting_every_tool_result_still_works():
    """N >= tool-result count protects all of them (the slice's normal case)."""
    result = await _run_workload(_make_context(protected_tool_results=N_TOOL_PAIRS))

    assert result["truncated_tool_ids"] == [], result
    assert result["level"] >= 3, result
    assert result["messages_removed"] == 0, result


# --------------------------------------------------------------------------
# Direct unit pin on the protected-set computation shared by all three
# truncation rungs (levels 1/2, level 4, level 6).
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "protected_tool_results,expected",
    [
        (0, set()),  # the bug: [-0:] would return {0,1,2,3,4}
        (1, {4}),
        (2, {3, 4}),
        (5, {0, 1, 2, 3, 4}),
        (99, {0, 1, 2, 3, 4}),  # more than exist -> all of them
        (-1, set()),  # never "the last 1" -- negative reads as "none"
    ],
)
def test_protected_tool_indices(protected_tool_results, expected):
    ctx = SimpleContextManager(protected_tool_results=protected_tool_results)
    assert ctx._protected_tool_indices([0, 1, 2, 3, 4]) == expected


def test_protected_tool_indices_empty_input():
    """No tool results at all -> nothing to protect, at any N."""
    for n in (0, 1, 5):
        ctx = SimpleContextManager(protected_tool_results=n)
        assert ctx._protected_tool_indices([]) == set()


def test_protected_tool_indices_uses_real_positions_not_ordinals():
    """The returned indices are positions in the message list, not 0..N-1."""
    ctx = SimpleContextManager(protected_tool_results=2)
    assert ctx._protected_tool_indices([3, 11, 40, 57]) == {40, 57}


@pytest.mark.parametrize("protected", [0, 1, 5])
def test_one_protected_sibling_vetoes_whole_batch_removal(protected):
    """Removal honors the same last-N floor as truncation, including N=0."""
    from copy import deepcopy

    ctx = SimpleContextManager(protected_tool_results=protected)
    messages = [
        {"role": "user", "content": "first human"},
        {"role": "assistant", "content": "", "thinking": {"opaque": "unchanged"},
         "tool_calls": [
             {"id": "old", "type": "function", "function": {"name": "read_file"}},
             {"id": "recent", "type": "function", "function": {"name": "read_file"}},
         ]},
        {"role": "tool", "tool_call_id": "old", "content": "older sibling"},
        {"role": "tool", "tool_call_id": "recent", "content": "protected sibling"},
        {"role": "user", "content": "last human"},
    ]
    before = deepcopy(messages)
    view, removed, stubbed, _ = ctx._remove_messages_with_protection(
        messages, target_tokens=1, protected_recent=0, system_tokens=0,
    )
    assert messages == before
    assert stubbed == 0
    assert removed == (0 if protected else 3)
    assert view == (before if protected else [before[0], before[-1]])


@pytest.mark.asyncio
async def test_default_tool_floor_survives_progressive_removal():
    from copy import deepcopy

    ctx = SimpleContextManager(max_tokens=120_000)
    for index in range(4):
        await ctx.add_message({"role": "user", "content": f"human-{index}:" + "x" * 100_000})
        await ctx.add_message({
            "role": "assistant", "content": "", "tool_calls": [
                {"id": f"call-{index}", "type": "function", "function": {"name": "read_file"}}
            ],
        })
        await ctx.add_message({
            "role": "tool", "tool_call_id": f"call-{index}", "content": f"result-{index}",
        })
    await ctx.add_message({"role": "user", "content": "latest:" + "y" * 100_000})
    canonical = deepcopy(ctx.messages)
    view = await ctx.get_messages_for_request()
    assert ctx._last_compaction_stats["strategy_level"] >= 4
    assert [m["content"] for m in view if m.get("role") == "tool"] == [
        f"result-{index}" for index in range(4)
    ]
    assert ctx.messages == canonical

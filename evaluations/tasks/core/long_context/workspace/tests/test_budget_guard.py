"""Regression tests for the compaction-notice-reserve budget guard.

If `compaction_notice_token_reserve` (default 800) is >= the effective budget,
`get_messages_for_request()` previously computed a non-positive
`effective_budget`. `_should_compact()` treats `budget <= 0` as `usage = 0`,
which silently disables compaction forever -- regardless of how much history
has accumulated. This was exactly the bug that made every test in
`test_tool_pair_compaction.py` (and several in `test_progressive_compaction.py`)
pass vacuously: compaction never actually ran, so the assertions were checking
an untouched message list rather than real compaction behavior.

These tests guard the fix: a misconfigured reserve must never silently
disable compaction -- not just in tests, but in production configs too.
"""

import pytest
from amplifier_module_context_simple import SimpleContextManager


@pytest.mark.asyncio
async def test_reserve_exceeding_budget_does_not_disable_compaction():
    """A compaction_notice_token_reserve >= max_tokens must not silently
    prevent compaction from ever firing.

    Regression test for the exact bug that made test_tool_pair_compaction.py
    pass vacuously: max_tokens=100 with the default reserve of 800 produced
    effective_budget = 100 - 800 = -700, and `_should_compact()`'s
    `budget > 0` guard forced usage=0, so compaction never triggered no
    matter how much history accumulated.
    """
    context = SimpleContextManager(
        max_tokens=100,
        compact_threshold=0.5,
        # compaction_notice_enabled defaults to True, reserve defaults to 800
    )

    # Alternating user/assistant messages with removable (non-user) turns:
    # plain short user-only content can never be reduced (user messages are
    # never removed, only stubbed above 80 chars) -- assistant messages give
    # the compactor something it can actually remove, so the test verifies
    # real reduction, not just a fired-but-no-op pass.
    for i in range(15):
        await context.add_message(
            {
                "role": "user",
                "content": f"message {i} with some longer padding text to increase size",
            }
        )
        await context.add_message(
            {
                "role": "assistant",
                "content": f"response {i} with some longer padding text to increase size",
            }
        )

    await context.get_messages_for_request()

    stats = context._last_compaction_stats
    assert stats is not None, (
        "Compaction should have fired: max_tokens=100 with default reserve=800 "
        "previously produced a negative effective_budget that silently "
        "disabled compaction entirely."
    )
    # Compare via compaction stats rather than the raw returned list length:
    # when compaction_notice_enabled=True (the default), a notice message is
    # appended to the compacted view, so len(messages) can end up >= the
    # original count even though real compaction work happened.
    assert stats["before_tokens"] > stats["budget"] * context.compact_threshold
    assert stats["after_tokens"] < stats["before_tokens"], (
        "Compaction fired but did not reduce token count"
    )


@pytest.mark.asyncio
async def test_reserve_equal_to_budget_does_not_disable_compaction():
    """Boundary case: reserve exactly equal to budget (effective_budget == 0)
    must also fall back to the full budget rather than disabling compaction."""
    context = SimpleContextManager(
        max_tokens=800,  # equals the default compaction_notice_token_reserve
        compact_threshold=0.1,
    )

    for i in range(20):
        await context.add_message({"role": "user", "content": "x" * 200})

    messages = await context.get_messages_for_request()

    assert context._last_compaction_stats is not None, (
        "Compaction should have fired even when reserve exactly equals budget "
        "(effective_budget == 0 must not be treated as 'no compaction needed')."
    )
    assert len(messages) < 20


@pytest.mark.asyncio
async def test_reserve_smaller_than_budget_still_applies_normally():
    """When the reserve legitimately fits within the budget, it should still
    be applied exactly as before (no regression to the normal case)."""
    context = SimpleContextManager(
        max_tokens=10_000,
        compact_threshold=0.1,
        target_usage=0.05,  # low enough that real reduction work is needed
        compaction_notice_token_reserve=800,
    )

    for i in range(5):
        await context.add_message({"role": "user", "content": "x" * 1000})

    await context.get_messages_for_request()

    # Compaction should fire (5000 chars ~= 1250 tokens > 10% of effective 9200)
    # and the stored stats should reflect the reserve having been subtracted
    # from the budget (10_000 - 800 = 9_200), not silently ignored.
    stats = context._last_compaction_stats
    assert stats is not None
    assert stats["budget"] == 10_000 - 800
    assert stats["after_tokens"] < stats["before_tokens"]

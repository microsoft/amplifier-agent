"""Performance regression test for the O(n^2) compaction hot loops.

Two hot paths inside `_compact_ephemeral` used to redo O(n) work on every one
of the O(n) removal/truncation candidates it processes, making a single
compaction pass O(n^2) in the number of messages:

1. `_truncate_tool_wave` called `_estimate_tokens(messages)` (a full rescan of
   every message) after each truncation, even though the message list is not
   actually shrunk mid-loop.
2. `_check_tool_pair_removable` did a full `for k, m in enumerate(messages)`
   scan per `tool_call_id` for every removal candidate that is a
   tool_use/tool_result pair.

This was confirmed against production telemetry: compaction stall duration
scaled with message count at r=0.994, exponent ~2.4 as a real session grew
from 2,285 to 4,526 messages (a 5.2x stall increase for ~2x message growth).

This test builds two message sets (one 4x the size of the other) with
realistic tool-pair-heavy content, times a single compaction pass over each,
and asserts the growth ratio stays well under the ~16x a true O(n^2)
implementation would show for a 4x input increase.
"""

import time

import pytest
from amplifier_module_context_simple import SimpleContextManager


def _make_message_set(n_pairs: int) -> list[dict]:
    """Build a realistic sequence of user + tool-call + tool-result triples.

    Tool result content size (~3,000+ chars) is in the same ballpark as the
    production session used to diagnose the original O(n^2) regression.
    """
    messages = []
    for i in range(n_pairs):
        messages.append({"role": "user", "content": f"request number {i} " * 5})
        messages.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": f"call_{i}",
                        "type": "function",
                        "function": {"name": "search"},
                    }
                ],
            }
        )
        messages.append(
            {
                "role": "tool",
                "tool_call_id": f"call_{i}",
                "content": "result payload data " * 150,
            }
        )
    return messages


async def _time_compaction(n_pairs: int) -> float:
    """Add n_pairs*3 messages, then time a single get_messages_for_request() call."""
    context = SimpleContextManager(
        max_tokens=1_000_000,  # large budget so add_message() itself is unaffected
        compact_threshold=0.01,  # trigger compaction almost immediately
        target_usage=0.001,  # aggressive target forces most candidates to be processed
        protected_recent=0.05,  # minimal protection -- most candidates are evaluated
        compaction_notice_enabled=False,
    )
    for msg in _make_message_set(n_pairs):
        await context.add_message(msg)

    start = time.perf_counter()
    await context.get_messages_for_request()
    elapsed = time.perf_counter() - start

    assert context._last_compaction_stats is not None, "Compaction should have fired"
    return elapsed


@pytest.mark.asyncio
async def test_compaction_scales_sub_quadratically():
    """Compaction time must grow roughly linearly with message count, not quadratically.

    small_n=200, large_n=800 is a 4x increase in message count (x3 for the
    user/assistant/tool triples each = 600 vs 2400 messages).
    O(n)   growth -> ~4x elapsed time.
    O(n^2) growth -> ~16x elapsed time.

    We assert well under the quadratic signature (< 8x) to reliably catch a
    regression back to O(n^2) without being flaky about exact linear timing
    under CI noise.
    """
    small_n = 200
    large_n = 800  # 4x messages

    small_time = await _time_compaction(small_n)
    large_time = await _time_compaction(large_n)

    ratio = large_time / small_time if small_time > 0 else float("inf")

    assert ratio < 8, (
        f"Compaction time grew {ratio:.1f}x for a 4x increase in message count "
        f"({small_time:.4f}s -> {large_time:.4f}s). This suggests O(n^2) behavior "
        f"has regressed -- expected roughly linear growth (~4x), not quadratic (~16x)."
    )

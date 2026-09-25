"""
Tests for tool pair preservation during context compaction.

Verifies that tool_use and tool_result messages are kept as atomic pairs
during compaction, preventing Anthropic API errors.

NOTE: Compaction is triggered via get_messages_for_request() when context
exceeds the compact_threshold. Tests use low max_tokens to force compaction.
"""

import pytest
from amplifier_module_context_simple import SimpleContextManager


@pytest.mark.asyncio
async def test_compact_preserves_tool_pairs_scenario_a():
    """Compaction preserves tool pair when tool_use is in keep window but tool_result is outside.

    Scenario: 11 messages total, compaction keeps last 10
    - Messages 0-8: Regular conversation (9 messages)
    - Message 9: Assistant with tool_calls (IN last 10)
    - Message 10: Tool result (NOT in last 10 without fix)

    Without fix: Keeps message 9, drops message 10 → API error
    With fix: Keeps both 9 and 10 (tool pair preserved)
    """
    # Use low max_tokens to force compaction.
    # compaction_notice_enabled=False: with the default reserve (800 tokens),
    # max_tokens=100 would make effective_budget negative, which previously
    # made _should_compact() silently treat usage as 0 and NEVER compact
    # (see test_budget_guard.py). Disabling the notice here means budget ==
    # max_tokens exactly, matching this test's original intent.
    context = SimpleContextManager(
        max_tokens=100, compact_threshold=0.5, compaction_notice_enabled=False
    )

    # Add 9 regular messages
    for i in range(9):
        await context.add_message({"role": "user", "content": f"message {i}"})

    # Add tool pair at messages 9-10
    await context.add_message(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "toolu_test", "tool": "bash", "arguments": {"cmd": "ls"}}
            ],
        }
    )
    await context.add_message(
        {"role": "tool", "tool_call_id": "toolu_test", "content": "bash output"}
    )

    # Verify we have 11 messages
    assert len(context.messages) == 11

    # Trigger compaction via get_messages_for_request()
    messages = await context.get_messages_for_request()

    # Verify compaction actually ran (guards against the vacuous-config bug
    # where a misconfigured budget silently prevents compaction from firing).
    assert context._last_compaction_stats is not None, "Compaction should have fired"

    # Verify tool pair preserved
    has_tool_use = any(
        m.get("role") == "assistant" and m.get("tool_calls") for m in messages
    )
    has_tool_result = any(m.get("role") == "tool" for m in messages)

    assert has_tool_use == has_tool_result, (
        f"Tool pair broken! has_tool_use={has_tool_use}, has_tool_result={has_tool_result}"
    )

    # If tool_use present, verify tool_result immediately follows
    for i, msg in enumerate(messages):
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            assert i + 1 < len(messages), f"Tool_use at message {i} but no next message"
            next_msg = messages[i + 1]
            assert next_msg.get("role") == "tool", (
                f"Tool_use at message {i} not followed by tool message (found role={next_msg.get('role')})"
            )


@pytest.mark.asyncio
async def test_compact_preserves_tool_pairs_scenario_b():
    """Compaction preserves tool pair when tool_result is in keep window but tool_use is outside.

    Scenario: 12 messages total, compaction keeps last 10
    - Messages 0-7: Regular conversation (8 messages)
    - Message 8: Assistant with tool_calls (NOT in last 10 without fix)
    - Message 9: Tool result (IN last 10)
    - Messages 10-11: More conversation (2 messages)

    Without fix: Keeps message 9, drops message 8 → API error
    With fix: Keeps both 8 and 9 (tool pair preserved)
    """
    # Use low max_tokens to force compaction. See scenario_a for why
    # compaction_notice_enabled=False is required here.
    context = SimpleContextManager(
        max_tokens=100, compact_threshold=0.5, compaction_notice_enabled=False
    )

    # Add 8 regular messages
    for i in range(8):
        await context.add_message({"role": "user", "content": f"message {i}"})

    # Add tool pair at messages 8-9
    await context.add_message(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "toolu_test2", "tool": "read", "arguments": {"path": "file.txt"}}
            ],
        }
    )
    await context.add_message(
        {"role": "tool", "tool_call_id": "toolu_test2", "content": "file content"}
    )

    # Add 2 more messages
    await context.add_message({"role": "user", "content": "message 10"})
    await context.add_message({"role": "assistant", "content": "response 10"})

    # Verify we have 12 messages
    assert len(context.messages) == 12

    # Trigger compaction via get_messages_for_request()
    messages = await context.get_messages_for_request()

    # Verify compaction actually ran (guards against the vacuous-config bug).
    assert context._last_compaction_stats is not None, "Compaction should have fired"

    # Verify tool pair preserved
    tool_use_count = sum(
        1 for m in messages if m.get("role") == "assistant" and m.get("tool_calls")
    )
    tool_result_count = sum(1 for m in messages if m.get("role") == "tool")

    assert tool_use_count == tool_result_count, (
        f"Tool pair count mismatch! tool_use={tool_use_count}, tool_result={tool_result_count}"
    )

    # Verify adjacency
    for i, msg in enumerate(messages):
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            assert i + 1 < len(messages), f"Tool_use at message {i} but no next message"
            next_msg = messages[i + 1]
            assert next_msg.get("role") == "tool", (
                f"Tool_use at message {i} not followed by tool message"
            )


@pytest.mark.asyncio
async def test_compact_never_deduplicates_tool_messages():
    """Tool messages are never deduplicated since each has unique tool_call_id.

    With progressive compaction, older tool pairs may be removed entirely (as atomic units).
    This test verifies that tool pairs with identical content are NOT deduplicated -
    if multiple pairs exist, they remain separate (not merged into one).
    """
    # Use a budget that still triggers compaction (compact_threshold=0.5) but a
    # target_usage of 1.0 so the target is the full budget: with protected_recent=0.9
    # nothing should actually need to be removed or truncated for this tiny
    # conversation. This ensures both tool pairs survive and we can verify no
    # deduplication, while still genuinely exercising the compaction code path
    # (compaction_notice_enabled=False avoids the default 800-token reserve
    # forcing effective_budget negative -- see test_budget_guard.py).
    context = SimpleContextManager(
        max_tokens=220,
        compact_threshold=0.5,
        target_usage=1.0,
        protected_recent=0.9,  # Protect 90% of messages
        compaction_notice_enabled=False,
    )

    # Add tool pair twice with same content but different IDs
    await context.add_message({"role": "user", "content": "test"})

    await context.add_message(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "toolu_1", "tool": "bash", "arguments": {"cmd": "ls"}}
            ],
        }
    )
    await context.add_message(
        {"role": "tool", "tool_call_id": "toolu_1", "content": "file1.txt"}
    )

    await context.add_message({"role": "user", "content": "test again"})

    await context.add_message(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "toolu_2", "tool": "bash", "arguments": {"cmd": "ls"}}
            ],
        }
    )
    await context.add_message(
        {"role": "tool", "tool_call_id": "toolu_2", "content": "file1.txt"}
    )

    # Trigger compaction via get_messages_for_request()
    messages = await context.get_messages_for_request()

    # Both tool pairs should be preserved (not deduplicated despite same content)
    tool_result_count = sum(1 for m in messages if m.get("role") == "tool")
    assert tool_result_count == 2, (
        f"Tool messages were deduplicated! Expected 2, got {tool_result_count}"
    )


@pytest.mark.asyncio
async def test_compact_with_multiple_tool_pairs():
    """Multiple tool pairs are all preserved correctly."""
    # Use low max_tokens to force compaction. See scenario_a for why
    # compaction_notice_enabled=False is required here.
    context = SimpleContextManager(
        max_tokens=100, compact_threshold=0.5, compaction_notice_enabled=False
    )

    # Add 3 tool pairs
    for i in range(3):
        await context.add_message({"role": "user", "content": f"request {i}"})
        await context.add_message(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": f"toolu_{i}", "tool": "bash", "arguments": {}}],
            }
        )
        await context.add_message(
            {"role": "tool", "tool_call_id": f"toolu_{i}", "content": f"result {i}"}
        )

    # Add more messages to push first pair outside window
    for i in range(10):
        await context.add_message({"role": "user", "content": f"later message {i}"})

    # Trigger compaction via get_messages_for_request()
    messages = await context.get_messages_for_request()

    # Verify compaction actually ran (guards against the vacuous-config bug).
    assert context._last_compaction_stats is not None, "Compaction should have fired"

    # Verify all remaining tool pairs are complete
    for i, msg in enumerate(messages):
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            # Must have following tool message
            assert i + 1 < len(messages), f"Tool_use at {i} without following message"
            assert messages[i + 1].get("role") == "tool", (
                f"Tool_use at {i} not followed by tool message"
            )

    # Verify counts match
    tool_use_count = sum(1 for m in messages if m.get("tool_calls"))
    tool_result_count = sum(1 for m in messages if m.get("role") == "tool")
    assert tool_use_count == tool_result_count, "Tool pair counts don't match"


@pytest.mark.asyncio
async def test_compact_with_multiple_tool_calls_in_one_message():
    """Assistant with MULTIPLE tool_calls in one message preserves ALL tool results.

    This tests the critical bug fix: when an assistant makes 6 tool calls in one message,
    all 6 tool result messages must be preserved during compaction.

    Regression test for: "Message 7 has tool_use IDs without matching tool_result blocks"

    With progressive compaction, we use protected_recent to keep the tool pair in the
    protected zone, ensuring the assistant + all 6 tool results are preserved together.
    """
    # Use protected_recent=0.5 to keep the multi-tool-call pair in protected zone.
    # compaction_notice_enabled=False avoids the default 800-token reserve making
    # effective_budget negative (see test_budget_guard.py).
    context = SimpleContextManager(
        max_tokens=200,
        compact_threshold=0.5,
        protected_recent=0.5,  # Protect last 50% to include the tool pair
        compaction_notice_enabled=False,
    )

    # Add conversation to fill context
    for i in range(8):
        await context.add_message({"role": "user", "content": f"request {i}"})
        await context.add_message({"role": "assistant", "content": f"response {i}"})

    # Add assistant with 6 tool calls (like web_search + 5 web_fetch)
    await context.add_message(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "toolu_1", "tool": "web_search", "arguments": {"query": "test"}},
                {
                    "id": "toolu_2",
                    "tool": "web_fetch",
                    "arguments": {"url": "http://example.com/1"},
                },
                {
                    "id": "toolu_3",
                    "tool": "web_fetch",
                    "arguments": {"url": "http://example.com/2"},
                },
                {
                    "id": "toolu_4",
                    "tool": "web_fetch",
                    "arguments": {"url": "http://example.com/3"},
                },
                {
                    "id": "toolu_5",
                    "tool": "web_fetch",
                    "arguments": {"url": "http://example.com/4"},
                },
                {
                    "id": "toolu_6",
                    "tool": "web_fetch",
                    "arguments": {"url": "http://example.com/5"},
                },
            ],
        }
    )

    # Add 6 separate tool result messages (one for each tool call)
    await context.add_message(
        {"role": "tool", "tool_call_id": "toolu_1", "content": "search results"}
    )
    await context.add_message(
        {"role": "tool", "tool_call_id": "toolu_2", "content": "page 1 content"}
    )
    await context.add_message(
        {"role": "tool", "tool_call_id": "toolu_3", "content": "page 2 content"}
    )
    await context.add_message(
        {"role": "tool", "tool_call_id": "toolu_4", "content": "page 3 content"}
    )
    await context.add_message(
        {"role": "tool", "tool_call_id": "toolu_5", "content": "page 4 content"}
    )
    await context.add_message(
        {"role": "tool", "tool_call_id": "toolu_6", "content": "page 5 content"}
    )

    # Add more messages
    await context.add_message({"role": "user", "content": "what did you find?"})

    # Trigger compaction via get_messages_for_request()
    messages = await context.get_messages_for_request()

    # Find the assistant message with 6 tool_calls
    assistant_idx = None
    for i, msg in enumerate(messages):
        if (
            msg.get("role") == "assistant"
            and msg.get("tool_calls")
            and len(msg["tool_calls"]) == 6
        ):
            assistant_idx = i
            break

    assert assistant_idx is not None, (
        "Assistant message with 6 tool_calls not found after compaction"
    )

    # Verify ALL 6 tool results are preserved
    tool_result_count = 0
    for offset in range(1, 7):
        if assistant_idx + offset < len(messages):
            next_msg = messages[assistant_idx + offset]
            if next_msg.get("role") == "tool":
                tool_result_count += 1

    assert tool_result_count == 6, (
        f"Expected 6 tool results after assistant with 6 tool_calls, "
        f"but found only {tool_result_count}. This is the bug!"
    )

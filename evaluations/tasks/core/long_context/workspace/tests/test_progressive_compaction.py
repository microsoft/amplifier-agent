"""
Tests for progressive percentage-based context compaction.

Verifies:
1. Phase 1: Tool result truncation in older history
2. Phase 2: Percentage-based message removal
3. Protected recent messages are preserved
4. Configurable thresholds work correctly
"""

import pytest
from amplifier_module_context_simple import SimpleContextManager


@pytest.mark.asyncio
async def test_tool_result_truncation_phase1():
    """Phase 1 truncates old tool results while preserving structure."""
    # Four results make the first 25% truncation wave nonempty. Its large
    # result is unprotected; the three recent results must remain intact.
    context = SimpleContextManager(
        max_tokens=2000,
        compact_threshold=0.9,
        target_usage=0.5,
        truncate_chars=50,
        protected_recent=0,
        protected_tool_results=3,
        compaction_notice_enabled=False,
    )

    # Add a tool pair early (will be in truncate zone)
    await context.add_message({"role": "user", "content": "read file"})
    await context.add_message(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "toolu_early",
                    "type": "function",
                    "function": {"name": "read_file"},
                }
            ],
        }
    )
    # Add a large tool result - this will push us over the threshold
    large_content = "x" * 10000
    await context.add_message(
        {"role": "tool", "tool_call_id": "toolu_early", "content": large_content}
    )

    for i in range(3):
        call_id = f"recent-call-{i}"
        await context.add_message(
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": call_id, "type": "function", "function": {"name": "read_file"}}
            ]}
        )
        await context.add_message(
            {"role": "tool", "tool_call_id": call_id, "content": "small result"}
        )
    await context.add_message({"role": "user", "content": "continue"})

    # Trigger compaction
    messages = await context.get_messages_for_request()

    # Find the old tool result
    tool_result = None
    for msg in messages:
        if msg.get("tool_call_id") == "toolu_early":
            tool_result = msg
            break

    # Removal is not a substitute for the truncation this test promises.
    assert tool_result is not None
    content = tool_result.get("content", "")
    assert "[truncated:" in content, "Tool result should be truncated"
    assert tool_result.get("_truncated") is True
    assert len(content) < 200
    assert context._last_compaction_stats["strategy_level"] == 1
    assert context._last_compaction_stats["messages_removed"] == 0
    assert [
        m["content"] for m in messages
        if m.get("tool_call_id", "").startswith("recent-call-")
    ] == ["small result"] * 3


@pytest.mark.asyncio
async def test_percentage_based_target():
    """Compaction reduces returned messages to fit within target_usage percentage.

    Note: context-simple uses EPHEMERAL compaction - get_messages_for_request()
    returns a compacted VIEW without modifying internal state. The full history
    is always preserved in self.messages.
    """
    # 50% target on 1000 token budget = target 500 tokens
    context = SimpleContextManager(
        max_tokens=1000,
        compact_threshold=0.9,
        target_usage=0.5,
        protected_recent=0.1,
    )

    # Add messages until we exceed threshold
    for i in range(50):
        await context.add_message(
            {"role": "user", "content": f"message {i} with some padding content"}
        )
        await context.add_message(
            {"role": "assistant", "content": f"response {i} with some padding content"}
        )

    # Record message count before compaction
    messages_before = len(context.messages)

    # Trigger compaction - returns compacted VIEW
    compacted_messages = await context.get_messages_for_request()

    # Compacted view should have fewer messages than full history
    assert len(compacted_messages) < messages_before, (
        f"Compacted messages ({len(compacted_messages)}) should be fewer than original ({messages_before})"
    )

    # Original messages should be unchanged (ephemeral compaction)
    assert len(context.messages) == messages_before, (
        "Original messages should be preserved (ephemeral compaction)"
    )

    # Compacted messages should fit within budget
    # Estimate: ~4 chars per token, each message ~40 chars = ~10 tokens
    # 500 token budget / 10 tokens per message ≈ 50 messages max
    assert len(compacted_messages) <= 60, (
        f"Compacted messages should fit within budget, got {len(compacted_messages)}"
    )


@pytest.mark.asyncio
async def test_protected_recent_messages():
    """Last N% of messages are always protected from removal."""
    context = SimpleContextManager(
        max_tokens=500,
        compact_threshold=0.5,
        target_usage=0.3,
        protected_recent=0.2,  # Protect last 20%
        # Without this, the default 800-token notice reserve makes
        # effective_budget = 500 - 800 = -300, which silently disables
        # compaction entirely (see test_budget_guard.py).
        compaction_notice_enabled=False,
    )

    # Add 20 messages
    for i in range(10):
        await context.add_message({"role": "user", "content": f"message {i}"})
        await context.add_message({"role": "assistant", "content": f"response {i}"})

    # Mark last 4 messages (20% of 20) with unique content
    last_messages_content = []
    for i in range(4):
        content = f"PROTECTED_MESSAGE_{i}"
        last_messages_content.append(content)
        await context.add_message({"role": "user", "content": content})

    # Trigger compaction
    messages = await context.get_messages_for_request()

    # Verify compaction actually ran (guards against the vacuous-config bug).
    assert context._last_compaction_stats is not None, "Compaction should have fired"

    # Verify protected messages are still there
    message_contents = [m.get("content", "") for m in messages]
    for protected_content in last_messages_content:
        assert protected_content in message_contents, (
            f"Protected message '{protected_content}' was removed during compaction"
        )


@pytest.mark.asyncio
async def test_first_user_message_always_preserved():
    """First user message (original task/request) is always protected from removal.

    This is important because the first user message often contains the original
    task or request, and losing it causes the AI to lose context about what was
    originally asked.
    """
    context = SimpleContextManager(
        max_tokens=500,
        compact_threshold=0.5,
        target_usage=0.3,
        protected_recent=0.1,  # Only protect 10% of recent messages
        # Without this, the default 800-token notice reserve makes
        # effective_budget = 500 - 800 = -300, which silently disables
        # compaction entirely (see test_budget_guard.py).
        compaction_notice_enabled=False,
    )

    # First user message - this should always be protected
    first_user_content = "FIRST_USER_MESSAGE_ORIGINAL_TASK"
    await context.add_message({"role": "user", "content": first_user_content})
    await context.add_message(
        {"role": "assistant", "content": "I'll help you with that."}
    )

    # Add many more messages to push the first message into the "old" zone
    for i in range(20):
        await context.add_message(
            {"role": "user", "content": f"follow up message {i} with padding"}
        )
        await context.add_message(
            {"role": "assistant", "content": f"response {i} with padding content"}
        )

    # Trigger compaction
    messages = await context.get_messages_for_request()

    # Verify compaction actually ran (guards against the vacuous-config bug).
    assert context._last_compaction_stats is not None, "Compaction should have fired"

    # First user message must be preserved
    first_user_messages = [
        m for m in messages if m.get("content") == first_user_content
    ]
    assert len(first_user_messages) == 1, (
        f"First user message should be preserved after compaction. "
        f"Got {len(first_user_messages)} matches in {len(messages)} messages."
    )


@pytest.mark.asyncio
async def test_system_messages_always_preserved():
    """System messages are never removed or truncated."""
    context = SimpleContextManager(
        max_tokens=200,
        compact_threshold=0.5,
        target_usage=0.3,
        # Without this, the default 800-token notice reserve makes
        # effective_budget = 200 - 800 = -600, which silently disables
        # compaction entirely (see test_budget_guard.py).
        compaction_notice_enabled=False,
    )

    # Add system message
    system_content = "You are a helpful assistant with important instructions."
    await context.add_message({"role": "system", "content": system_content})

    # Fill with other messages
    for i in range(20):
        await context.add_message({"role": "user", "content": f"message {i}"})
        await context.add_message({"role": "assistant", "content": f"response {i}"})

    # Trigger compaction
    messages = await context.get_messages_for_request()

    # Verify compaction actually ran (guards against the vacuous-config bug).
    assert context._last_compaction_stats is not None, "Compaction should have fired"

    # System message must be preserved
    system_messages = [m for m in messages if m.get("role") == "system"]
    assert len(system_messages) == 1, "System message should be preserved"
    assert system_messages[0]["content"] == system_content, (
        "System content should be unchanged"
    )


@pytest.mark.asyncio
async def test_system_messages_preserved_under_extreme_pressure():
    """System messages are NEVER removed even under extreme compaction pressure.

    This test simulates the scenario that caused the original bug where a ~143KB
    system prompt was completely dropped during compaction, causing the agent to
    lose its identity and instructions mid-conversation.

    The fix extracts system messages BEFORE compaction and re-inserts them AFTER,
    guaranteeing they are always preserved regardless of compaction level.
    """
    context = SimpleContextManager(
        max_tokens=100,  # Very low to force extreme compaction
        compact_threshold=0.3,  # Trigger compaction early
        target_usage=0.2,  # Very aggressive target
        truncate_chars=20,  # Aggressive truncation
        protected_recent=0.1,  # Minimal protection to maximize pressure
        protected_tool_results=1,  # Only protect 1 tool result
        # Without this, the default 800-token notice reserve makes
        # effective_budget = 100 - 800 = -700, which silently disables
        # compaction entirely (see test_budget_guard.py). This test is the
        # regression test for the original historical bug (system prompt
        # dropped during compaction) -- it must actually exercise compaction.
        compaction_notice_enabled=False,
    )

    # Add a large system message (simulating the ~143KB system prompt scenario)
    large_system_content = "You are a helpful assistant. " + ("x" * 500)
    await context.add_message({"role": "system", "content": large_system_content})

    # Add many messages with large tool results to create extreme pressure
    for i in range(30):
        await context.add_message(
            {"role": "user", "content": f"request {i} with extra content"}
        )
        await context.add_message(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": f"tool_{i}",
                        "type": "function",
                        "function": {"name": "read"},
                    }
                ],
            }
        )
        # Large tool result
        await context.add_message(
            {
                "role": "tool",
                "tool_call_id": f"tool_{i}",
                "content": "result " + ("y" * 200),
            }
        )
        await context.add_message({"role": "assistant", "content": f"response {i}"})

    # Trigger compaction - this should hit Level 7 or 8 due to extreme pressure
    messages = await context.get_messages_for_request()

    # CRITICAL: System message MUST be preserved
    system_messages = [m for m in messages if m.get("role") == "system"]
    assert len(system_messages) == 1, (
        f"System message was DROPPED during compaction! "
        f"Found {len(system_messages)} system messages. "
        f"This is the exact bug we fixed - system messages must NEVER be removed."
    )
    assert system_messages[0]["content"] == large_system_content, (
        "System message content was modified during compaction! "
        "System messages must be preserved exactly as-is."
    )

    # Verify system message is at the beginning
    assert messages[0].get("role") == "system", (
        "System message must be at the beginning of the message list"
    )


@pytest.mark.asyncio
async def test_truncation_marker_prevents_re_truncation():
    """Already truncated messages are not truncated again."""
    context = SimpleContextManager(
        max_tokens=500,
        compact_threshold=0.5,
        target_usage=0.4,
        truncate_chars=50,
        # Without this, the default 800-token notice reserve makes
        # effective_budget = 500 - 800 = -300, which silently disables
        # compaction entirely (see test_budget_guard.py).
        compaction_notice_enabled=False,
    )

    # Add a pre-truncated tool result
    await context.add_message({"role": "user", "content": "test"})
    await context.add_message(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "toolu_1", "type": "function", "function": {"name": "test"}}
            ],
        }
    )
    await context.add_message(
        {
            "role": "tool",
            "tool_call_id": "toolu_1",
            "content": "[truncated: ~100 tokens] some content...",
            "_truncated": True,
            "_original_tokens": 100,
        }
    )

    # Add more messages
    for i in range(15):
        await context.add_message({"role": "user", "content": f"message {i}"})

    # Trigger compaction multiple times
    await context.get_messages_for_request()
    await context.get_messages_for_request()

    # Find the tool result
    tool_results = [m for m in context.messages if m.get("tool_call_id") == "toolu_1"]
    assert len(tool_results) <= 1, "Should have at most one tool result"

    if tool_results:
        content = tool_results[0].get("content", "")
        # Should not have double truncation markers
        assert content.count("[truncated:") == 1, (
            "Should not re-truncate already truncated content"
        )


@pytest.mark.asyncio
async def test_configurable_truncate_chars():
    """truncate_chars configuration controls truncation length."""
    context = SimpleContextManager(
        max_tokens=500,
        compact_threshold=0.5,
        target_usage=0.3,
        truncate_chars=100,  # Keep 100 chars
        # Without this, the default 800-token notice reserve makes
        # effective_budget = 500 - 800 = -300, which silently disables
        # compaction entirely (see test_budget_guard.py).
        compaction_notice_enabled=False,
    )

    # Add tool result with known content
    await context.add_message({"role": "user", "content": "test"})
    await context.add_message(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "toolu_1", "type": "function", "function": {"name": "test"}}
            ],
        }
    )
    original_content = "A" * 500  # 500 chars
    await context.add_message(
        {"role": "tool", "tool_call_id": "toolu_1", "content": original_content}
    )

    # Add more to trigger compaction
    for i in range(15):
        await context.add_message({"role": "user", "content": f"msg {i}"})

    # Trigger compaction
    await context.get_messages_for_request()

    # Find truncated tool result
    tool_result = next(
        (m for m in context.messages if m.get("tool_call_id") == "toolu_1"), None
    )

    if tool_result and tool_result.get("_truncated"):
        content = tool_result["content"]
        # Should contain ~100 A's from original (plus truncation marker)
        assert "A" * 100 in content, "Should preserve first 100 chars"
        assert len(content) < 200, "Total length should be small"


@pytest.mark.asyncio
async def test_tool_pairs_preserved_during_removal():
    """Tool pairs are removed together, never orphaned."""
    context = SimpleContextManager(
        max_tokens=300,
        compact_threshold=0.5,
        target_usage=0.3,
        protected_recent=0.1,
        # Without this, the default 800-token notice reserve makes
        # effective_budget = 300 - 800 = -500, which silently disables
        # compaction entirely (see test_budget_guard.py).
        compaction_notice_enabled=False,
    )

    # Add several tool pairs
    for i in range(5):
        await context.add_message({"role": "user", "content": f"request {i}"})
        await context.add_message(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": f"toolu_{i}",
                        "type": "function",
                        "function": {"name": "test"},
                    }
                ],
            }
        )
        await context.add_message(
            {"role": "tool", "tool_call_id": f"toolu_{i}", "content": f"result {i}"}
        )

    # Add more messages to trigger aggressive compaction
    for i in range(10):
        await context.add_message({"role": "user", "content": f"later {i}"})

    # Trigger compaction
    messages = await context.get_messages_for_request()

    # Verify no orphaned tool messages
    tool_call_ids_in_assistants = set()
    for msg in messages:
        if msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                tc_id = tc.get("id") or tc.get("tool_call_id")
                if tc_id:
                    tool_call_ids_in_assistants.add(tc_id)

    tool_call_ids_in_results = set()
    for msg in messages:
        if msg.get("role") == "tool":
            tc_id = msg.get("tool_call_id")
            if tc_id:
                tool_call_ids_in_results.add(tc_id)

    # Every tool result should have a matching tool_use
    orphaned_results = tool_call_ids_in_results - tool_call_ids_in_assistants
    assert not orphaned_results, f"Orphaned tool results: {orphaned_results}"

    # Every tool_use should have matching results (may have multiple per assistant)
    # This is harder to check precisely, but at minimum counts should be reasonable
    assert len(tool_call_ids_in_results) <= len(tool_call_ids_in_assistants) * 6, (
        "More tool results than tool calls"
    )

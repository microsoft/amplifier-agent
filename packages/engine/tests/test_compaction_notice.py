import copy
import json
from typing import Any

from amplifier_agent_engine._engine.adapters import StructuredContext

BUDGET = 12_000
SOURCE = "context-compaction"


def is_notice(message: dict[str, Any]) -> bool:
    return (message.get("metadata") or {}).get("source") == SOURCE


def notices(view: list[dict[str, Any]]) -> list[int]:
    return [index for index, message in enumerate(view) if is_notice(message)]


def wire(messages: list[dict[str, Any]]) -> str:
    return json.dumps(messages, sort_keys=True)


async def tool_round(context: StructuredContext, index: int, answered: bool = True) -> None:
    await context.add_message(
        {
            "role": "assistant",
            "content": f"Reading file {index}.",
            "tool_calls": [{"id": f"call-{index}", "tool": "read_file", "arguments": {}}],
        }
    )
    if answered:
        await context.add_message(
            {"role": "tool", "name": "read_file", "tool_call_id": f"call-{index}", "content": "x" * 4_000}
        )


async def filled_context(rounds: int = 12) -> StructuredContext:
    context = StructuredContext(131_072, hooks=None)
    await context.add_message({"role": "system", "content": "You are careful."})
    await context.add_message({"role": "user", "content": "Start."})
    for index in range(rounds):
        await tool_round(context, index)
        await context.add_message({"role": "user", "content": f"Next step {index}."})
    return context


async def test_notice_keeps_its_position_and_bytes_so_each_request_prefixes_the_next():
    context = await filled_context()
    first = await context.get_messages_for_request(token_budget=BUDGET)
    assert notices(first) == [len(first) - 1]
    stats = context._last_compaction_stats
    for index in range(3):
        await context.add_message({"role": "assistant", "content": f"Answer {index}."})
        await context.add_message({"role": "user", "content": f"Follow-up {index}."})
        view = await context.get_messages_for_request(token_budget=BUDGET)
        assert context._last_compaction_stats is stats
        assert notices(view) == notices(first)
        assert wire(view[: len(first)]) == wire(first)
        first = view


async def test_notice_never_enters_the_transcript():
    context = await filled_context()
    stored = copy.deepcopy(await context.get_messages())
    await context.get_messages_for_request(token_budget=BUDGET)
    await context.get_messages_for_request(token_budget=BUDGET)
    assert await context.get_messages() == stored
    assert not notices(stored)


async def test_skill_context_follows_the_notice():
    context = await filled_context()
    context.skill_context = ["extra"]
    view = await context.get_messages_for_request(token_budget=BUDGET)
    assert view[-1] == {"role": "user", "content": "extra"}
    assert notices(view) == [len(view) - 2]


async def test_a_new_compaction_moves_the_notice_to_the_end_of_its_view():
    context = await filled_context()
    first = await context.get_messages_for_request(token_budget=BUDGET)
    stats = context._last_compaction_stats
    for index in range(12, 24):
        await tool_round(context, index)
    view = await context.get_messages_for_request(token_budget=BUDGET)
    assert context._last_compaction_stats is not stats
    assert notices(view) == [len(view) - 1]
    assert view[-1]["content"] != first[notices(first)[0]]["content"]


async def test_notice_waits_while_the_view_ends_with_unanswered_tool_calls():
    context = await filled_context()
    await tool_round(context, 99, answered=False)
    view = await context.get_messages_for_request(token_budget=BUDGET)
    assert context._last_compaction_stats is not None
    assert not notices(view)
    await context.add_message({"role": "tool", "name": "read_file", "tool_call_id": "call-99", "content": "done"})
    view = await context.get_messages_for_request(token_budget=BUDGET)
    assert notices(view) == [len(view) - 1]


async def test_restored_or_cleared_context_carries_no_notice():
    context = await filled_context()
    stored = await context.get_messages()
    await context.get_messages_for_request(token_budget=BUDGET)
    await context.set_messages(stored[:4])
    assert context._notice_anchor is None
    assert not notices(await context.get_messages_for_request(token_budget=BUDGET))
    await context.get_messages_for_request(token_budget=BUDGET)
    await context.set_messages(stored)
    await context.get_messages_for_request(token_budget=BUDGET)
    await context.clear()
    assert context._notice_anchor is None
    assert not notices(await context.get_messages_for_request(token_budget=BUDGET))


async def test_rolled_back_compaction_stats_never_keep_a_newer_anchor():
    context = await filled_context()
    await context.get_messages_for_request(token_budget=BUDGET)
    newer = context._notice_anchor
    assert newer is not None
    context._last_compaction_stats = dict(newer[0])
    context._notice_anchor = (newer[0], 1, newer[2])
    view = context._with_compaction_notice([{"role": "system", "content": "s"}, {"role": "user", "content": "u"}])
    assert notices(view) == [2]
    assert context._notice_anchor is not None
    assert context._notice_anchor[0] is context._last_compaction_stats


async def test_an_anchor_beyond_the_view_moves_to_its_end():
    context = await filled_context()
    await context.get_messages_for_request(token_budget=BUDGET)
    anchor = context._notice_anchor
    assert anchor is not None
    context._notice_anchor = (anchor[0], 10_000, anchor[2])
    view = context._with_compaction_notice([{"role": "system", "content": "s"}, {"role": "user", "content": "u"}])
    assert notices(view) == [2]
    assert wire([view[2]]) == wire([anchor[2]])


async def test_skill_context_preserves_request_prefix_within_turn():
    context = await filled_context()
    context.skill_context = ["PreToolUse context"]

    first = await context.get_messages_for_request(token_budget=BUDGET)
    stats = context._last_compaction_stats
    assert stats is not None

    await context.add_message({"role": "assistant", "content": "Continuing."})
    await context.add_message({"role": "user", "content": "Next action."})

    second = await context.get_messages_for_request(token_budget=BUDGET)

    assert context._last_compaction_stats is stats
    assert wire(second[: len(first)]) == wire(first)
    stored = await context.get_messages()
    assert not any("PreToolUse context" in str(message) for message in stored)
    assert not any(is_notice(message) for message in stored)


async def test_additional_skill_hook_context_keeps_previous_request_stable():
    context = await filled_context()
    context.skill_context = ["First hook context"]

    first = await context.get_messages_for_request(token_budget=BUDGET)
    stats = context._last_compaction_stats
    assert stats is not None

    await context.add_message({"role": "assistant", "content": "Tool completed."})
    await context.add_message({"role": "user", "content": "Continue."})

    context.skill_context.append("Second hook context")
    second = await context.get_messages_for_request(token_budget=BUDGET)

    assert context._last_compaction_stats is stats
    assert wire(second[: len(first)]) == wire(first)
    assert any("Second hook context" in str(message) for message in second)
    assert not any("First hook context" in str(message) for message in await context.get_messages())
    assert not any("Second hook context" in str(message) for message in await context.get_messages())


async def test_skill_context_waits_for_an_unanswered_tool_call():
    context = await filled_context()
    await tool_round(context, 99, answered=False)
    context.skill_context = ["PreToolUse context"]

    view = await context.get_messages_for_request(token_budget=BUDGET)
    assert view[-1]["tool_calls"][0]["id"] == "call-99"
    assert not any("PreToolUse context" in str(message) for message in view)

    await context.add_message({"role": "tool", "name": "read_file", "tool_call_id": "call-99", "content": "done"})
    view = await context.get_messages_for_request(token_budget=BUDGET)
    call_index = next(
        index
        for index, message in enumerate(view)
        if any(call["id"] == "call-99" for call in message.get("tool_calls", []))
    )
    assert view[call_index + 1]["tool_call_id"] == "call-99"
    assert view[-1] == {"role": "user", "content": "PreToolUse context"}


async def test_skill_batches_reanchor_once_after_a_second_compaction():
    context = await filled_context()
    context.skill_context = ["first hook"]
    await context.get_messages_for_request(token_budget=BUDGET)
    context.skill_context.append("second hook")
    await context.add_message({"role": "user", "content": "Continue."})
    await context.get_messages_for_request(token_budget=BUDGET)
    stats = context._last_compaction_stats
    for index in range(12, 24):
        await tool_round(context, index)
    stored = copy.deepcopy(await context.get_messages())
    compacted = await context.get_messages_for_request(token_budget=BUDGET)
    assert context._last_compaction_stats is not stats
    assert compacted[-1] == {"role": "user", "content": "first hook\n\nsecond hook"}
    assert sum("first hook" in str(message) for message in compacted) == 1
    assert sum("second hook" in str(message) for message in compacted) == 1
    assert notices(compacted) == [len(compacted) - 2]
    assert await context.get_messages() == stored
    await context.add_message({"role": "user", "content": "One more."})
    extended = await context.get_messages_for_request(token_budget=BUDGET)
    assert wire(extended[: len(compacted)]) == wire(compacted)


async def test_skill_context_reanchors_after_history_replacement_and_clear():
    context = await filled_context()
    context.skill_context = ["active hook"]
    await context.get_messages_for_request(token_budget=BUDGET)
    replacement = [{"role": "user", "content": "Replacement history.", "metadata": {}}]
    await context.set_messages(copy.deepcopy(replacement))
    stored = copy.deepcopy(await context.get_messages())
    view = await context.get_messages_for_request(token_budget=BUDGET)
    assert view == [*replacement, {"role": "user", "content": "active hook"}]
    assert await context.get_messages() == stored
    await context.clear()
    view = await context.get_messages_for_request(token_budget=BUDGET)
    assert view == [{"role": "user", "content": "active hook"}]
    assert await context.get_messages() == []
    await context.add_message({"role": "user", "content": "Fresh history."})
    extended = await context.get_messages_for_request(token_budget=BUDGET)
    assert wire(extended[: len(view)]) == wire(view)


async def test_cleared_skill_context_cannot_replay_an_old_batch():
    context = await filled_context()
    context.skill_context = ["expired hook"]
    await context.get_messages_for_request(token_budget=BUDGET)
    context.skill_context.clear()
    without_hook = await context.get_messages_for_request(token_budget=BUDGET)
    assert "expired hook" not in wire(without_hook)
    context.skill_context.append("new hook")
    view = await context.get_messages_for_request(token_budget=BUDGET)
    assert view[-1] == {"role": "user", "content": "new hook"}
    assert wire(view[: len(without_hook)]) == wire(without_hook)


async def test_skill_context_request_mutation_does_not_change_later_requests():
    context = await filled_context()
    context.skill_context = ["original hook"]
    view = await context.get_messages_for_request(token_budget=BUDGET)
    expected = copy.deepcopy(view)
    view[-1]["content"] = "mutated by a request consumer"
    assert await context.get_messages_for_request(token_budget=BUDGET) == expected


async def test_new_hook_batch_waits_for_all_parallel_tool_results():
    context = StructuredContext(131_072)
    await context.add_message({"role": "user", "content": "Start."})
    context.skill_context = ["earlier hook"]
    first = await context.get_messages_for_request(token_budget=BUDGET)
    await context.add_message(
        {
            "role": "assistant",
            "content": "Read both.",
            "tool_calls": [{"id": name, "tool": "read_file", "arguments": {}} for name in ("a", "b")],
        }
    )
    context.skill_context.append("new hook")
    await context.add_message({"role": "tool", "tool_call_id": "a", "content": "A"})
    pending = await context.get_messages_for_request(token_budget=BUDGET)
    assert wire(pending[: len(first)]) == wire(first)
    assert "new hook" not in wire(pending)
    await context.add_message({"role": "tool", "tool_call_id": "b", "content": "B"})
    complete = await context.get_messages_for_request(token_budget=BUDGET)
    assert wire(complete[: len(pending)]) == wire(pending)
    assert [item["role"] for item in complete[-4:]] == ["assistant", "tool", "tool", "user"]
    assert complete[-1]["content"] == "new hook"

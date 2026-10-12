import copy
import json
from typing import Any

from amplifier_agent_engine._engine.adapters import StructuredContext
from amplifier_agent_engine._engine.providers import _bounded_reasoning_replay

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


def test_reasoning_replay_does_not_treat_the_notice_as_a_turn():
    def thinking(text: str) -> dict[str, Any]:
        return {"role": "assistant", "content": [{"type": "thinking", "thinking": text * 50_000}]}

    plain: list[dict[str, Any]] = [
        {"role": "user", "content": "one"},
        thinking("a"),
        {"role": "user", "content": "two"},
        thinking("b"),
        {"role": "assistant", "content": "c", "tool_calls": [{"id": "t", "tool": "x", "arguments": {}}]},
        {"role": "tool", "tool_call_id": "t", "content": "r"},
    ]
    notice = {"role": "user", "content": "compacted", "metadata": {"source": SOURCE, "ephemeral": True}}
    with_notice = [*plain[:4], notice, *plain[4:]]
    replayed = _bounded_reasoning_replay(with_notice)
    assert [message for message in replayed if not is_notice(message)] == _bounded_reasoning_replay(plain)

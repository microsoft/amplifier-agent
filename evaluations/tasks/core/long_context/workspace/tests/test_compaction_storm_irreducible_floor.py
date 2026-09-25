"""Compaction storm: an irreducible protected message re-fires the full
escalation ladder on EVERY request, forever.

PROVENANCE -- these tests reproduce a measured production pathology, not a
hypothetical. Two sessions on the team-shared context-intelligence graph
(pulled 2026-09-05, `model_performance-fwut`):

  0000000000000000-445ac89c107c4f52_anchors-amp-dev-architect (claude-sonnet-5)
    969 `context:compaction` events over 988 `llm:request`s in 3h26m.
    strategy_level == 8 on 969/969.  after_tokens > target on 969/969.
    after_tokens > budget on 968/969.  user_messages_stubbed == 0 on 969/969.
    budget 131,104 / target 65,552; after_messages 2..12 while before_messages
    grew monotonically 3 -> 5,174.
    Cause: the session is a forked sub-agent. Its SOLE user message -- the
    delegation payload -- is 516,184 chars ~= 124,338 estimated tokens, i.e.
    94.8% of the whole budget, and is byte-identical (md5 76fcb100...) at the
    first request and the last one 3h26m later.

  0000000000000000-877774a55cd1430c_foundation-explorer (claude-opus-5)
    2,165 compaction events. strategy_level == 8 on 2,165/2,165.
    after_tokens > target on 2,165/2,165. user_messages_stubbed == 0 on all.
    Same budget/target. Same forked-sub-agent shape -- but here the floor is
    the SYSTEM prompt: 402,949 chars ~= 100,737 estimated tokens.

THE MECHANISM. Compaction is ephemeral: `_compact_ephemeral` builds a fresh
view per request and never mutates `self.messages`. Every level of the ladder
is bounded by protection rules that make certain messages irreducible:

  * system messages are NEVER compacted (extracted up front, re-prepended);
  * `_remove_messages_with_protection` excludes ALL user messages from removal
    ("they can only be stubbed, not removed");
  * Level 8's stub -- the one lever that can shrink a user message -- is
    guarded twice: `first_user_idx != last_user_idx` (so the SOLE user message
    of a sub-agent session, which is both first and last, is skipped), and
    `isinstance(content, str)` (so block-structured content is skipped).

So when that protected residue alone sits at or above `compact_threshold *
budget`, `_exceeds_threshold` is permanently True. `needs_escalation` never
goes False, the "sticky state alone is sufficient -- nothing NEW to decide"
fast path is unreachable, and the module re-runs the entire ladder to level 8
on every single request, achieving zero further reduction and emitting a
`context:compaction` event (and a compaction notice) each time.

`_finalize_compaction_with_stats` already DIAGNOSES this -- it logs a warning
naming the un-reducible floor -- but only when the result is over BUDGET, and
it does not stop the loop. The opus-5 session above stayed under budget while
sitting above target, so it stormed for 2,165 requests in total silence.

WHAT THESE TESTS ARE. The first four are characterisation tests: they pass on
`main` and pin today's behaviour so it cannot change unnoticed. The last is an
`xfail(strict=True)` carrying the assertion that fails today -- the fail-before
test. It flips to a hard failure the moment the storm is fixed, which is the
signal to delete the xfail marker.

NO RUNTIME BEHAVIOUR IS CHANGED BY THIS FILE.
"""

import pytest

from amplifier_module_context_simple import SimpleContextManager


class _CountingHooks:
    """Minimal hooks stand-in that counts `context:compaction` emissions.

    This is the same event the production graph counted 969 / 2,165 times.
    """

    def __init__(self) -> None:
        self.compactions: list[dict] = []

    async def emit(self, event: str, data: dict) -> None:
        if event == "context:compaction":
            self.compactions.append(data)


# Scaled-down stand-in for the production numbers. Production: budget 131,104,
# target 65,552, floor 130,627 (99.6% of budget). Here: budget 10,000, target
# 5,000, floor ~9,500 (95% of budget) -- same regime, 13x smaller so the tests
# are fast.
BUDGET = 10_000
TARGET = BUDGET // 2  # target_usage 0.50
THRESHOLD = 0.80
# 38,000 chars // 4 == 9,500 estimated tokens, before dict-repr overhead.
HUGE_PAYLOAD = "D" * 38_000


def _mgr(**kw) -> SimpleContextManager:
    """A context manager whose budget is exactly BUDGET.

    The notice is disabled so `budget == max_tokens` exactly (the notice
    reserve would subtract 800 and make the arithmetic in these tests harder
    to read). The storm does not depend on the notice -- the opus-5 session
    above stormed identically -- but see
    `test_storm_reattaches_a_fresh_compaction_notice_on_every_request` for the
    notice-enabled case.
    """
    defaults = dict(
        max_tokens=BUDGET,
        compact_threshold=THRESHOLD,
        target_usage=0.50,
        compaction_notice_enabled=False,
    )
    defaults.update(kw)
    return SimpleContextManager(**defaults)


async def _add_a_turn(ctx: SimpleContextManager, n: int) -> None:
    """One assistant/tool turn -- the compactible part of the history."""
    await ctx.add_message(
        {
            "role": "assistant",
            "content": f"assistant turn {n} " + ("x" * 200),
            "tool_calls": [{"id": f"call_{n}", "name": "read_file"}],
        }
    )
    await ctx.add_message(
        {
            "role": "tool",
            "tool_call_id": f"call_{n}",
            "content": f"tool result {n} " + ("y" * 400),
        }
    )


@pytest.mark.asyncio
async def test_sole_user_message_above_target_is_never_stubbed_or_removed():
    """The sonnet-5 445ac89c shape: a sub-agent's SOLE user message.

    It is simultaneously the first and the last user message, so Level 8's
    stub is skipped by `first_user_idx != last_user_idx`; and user messages
    are excluded from removal at every level. Result: an irreducible floor
    that compaction cannot touch.

    Production counterpart: user_messages_stubbed == 0 on all 969 events.
    """
    hooks = _CountingHooks()
    ctx = _mgr(hooks=hooks)

    await ctx.add_message({"role": "user", "content": HUGE_PAYLOAD})
    for n in range(12):
        await _add_a_turn(ctx, n)

    view = await ctx.get_messages_for_request()
    stats = ctx._last_compaction_stats

    assert stats is not None, "compaction should have fired"
    assert stats["strategy_level"] == 8, (
        "the ladder must have run all the way to the last level; "
        f"got level {stats['strategy_level']}"
    )
    assert stats["user_messages_stubbed"] == 0, (
        "CHARACTERISATION: the sole user message is never stubbed, because it "
        "is both the first and the last user message. This is the production "
        "observation (0 stubs across 969 events) that leaves the floor intact."
    )
    assert stats["after_tokens"] > stats["target_tokens"], (
        "compaction cannot reach target: "
        f"{stats['after_tokens']:,} > {stats['target_tokens']:,}"
    )

    # And the payload is still there, in full, un-stubbed.
    user_msgs = [m for m in view if m.get("role") == "user"]
    assert len(user_msgs) == 1
    assert user_msgs[0]["content"] == HUGE_PAYLOAD, (
        "the huge payload survives compaction byte-for-byte -- exactly as the "
        "production message did (md5 identical across 3h26m and 988 requests)"
    )


@pytest.mark.asyncio
async def test_huge_system_prompt_above_target_is_an_unreducible_floor():
    """The opus-5 877774a5 shape: the floor is the SYSTEM prompt.

    System messages are never compacted by design, so a system prompt larger
    than the target makes the target arithmetically unreachable. Production:
    402,949 chars ~= 100,737 estimated tokens against a 65,552 target.
    """
    hooks = _CountingHooks()
    ctx = _mgr(hooks=hooks)

    await ctx.add_message({"role": "system", "content": HUGE_PAYLOAD})
    await ctx.add_message({"role": "user", "content": "explore the repo"})
    for n in range(12):
        await _add_a_turn(ctx, n)

    view = await ctx.get_messages_for_request()
    stats = ctx._last_compaction_stats

    assert stats is not None, "compaction should have fired"
    assert stats["strategy_level"] == 8
    assert stats["system_messages_preserved"] == 1
    assert stats["after_tokens"] > stats["target_tokens"], (
        f"{stats['after_tokens']:,} > {stats['target_tokens']:,} -- the system "
        "prompt alone is above target, so no escalation level can reach it"
    )
    assert any(
        m.get("role") == "system" and m.get("content") == HUGE_PAYLOAD for m in view
    )


@pytest.mark.asyncio
async def test_block_structured_user_content_is_never_stubbable():
    """Level 8's second guard, independent of the first-vs-last one.

    `_stub_user_message` and Level 8's inline check both bail on
    `not isinstance(content, str)`. The production payload's content was a
    LIST of content blocks (`[{"type": "text", "text": ...}]`, 516,153 chars in
    the single block), so even a first user message that is NOT the last one
    is un-stubbable.
    """
    ctx = _mgr()

    # Two user messages, so `first_user_idx != last_user_idx` -- the ONLY
    # remaining guard is the isinstance one.
    await ctx.add_message(
        {"role": "user", "content": [{"type": "text", "text": HUGE_PAYLOAD}]}
    )
    for n in range(12):
        await _add_a_turn(ctx, n)
    await ctx.add_message({"role": "user", "content": "and now finish the task"})

    await ctx.get_messages_for_request()
    stats = ctx._last_compaction_stats

    assert stats is not None
    assert stats["strategy_level"] == 8
    assert stats["user_messages_stubbed"] == 0, (
        "CHARACTERISATION: a first-but-not-last user message with "
        "block-structured content is still never stubbed -- both Level 8's "
        "inline check and _stub_user_message() require isinstance(content, str)"
    )


@pytest.mark.asyncio
async def test_irreducible_floor_re_escalates_on_every_single_request():
    """THE STORM, reproduced: N requests -> N compaction events.

    This is the defect in one assertion. Production: 969 events / 988 requests
    (98.1%) and 2,165 events in the opus-5 session. Nothing new is decided on
    any of them -- `after_tokens` never improves -- yet the full ladder runs to
    level 8 every time and emits an event every time.
    """
    hooks = _CountingHooks()
    ctx = _mgr(hooks=hooks)

    await ctx.add_message({"role": "user", "content": HUGE_PAYLOAD})
    for n in range(12):
        await _add_a_turn(ctx, n)

    requests = 10
    for i in range(requests):
        await ctx.get_messages_for_request()
        await _add_a_turn(ctx, 100 + i)  # history keeps growing, as in production

    assert len(hooks.compactions) == requests, (
        "CHARACTERISATION: one compaction event per request. "
        f"got {len(hooks.compactions)} for {requests} requests"
    )
    assert all(c["strategy_level"] == 8 for c in hooks.compactions)
    assert all(
        c["after_tokens"] > c["target_tokens"] for c in hooks.compactions
    ), "every single one of them finished above target"

    # The tell that the work was pointless: the floor never moves.
    floors = [c["after_tokens"] for c in hooks.compactions]
    assert max(floors) - min(floors) < BUDGET, (
        "after_tokens stays pinned near the irreducible floor across every "
        f"request: {floors}"
    )


@pytest.mark.asyncio
async def test_storm_reattaches_a_fresh_compaction_notice_on_every_request():
    """With the notice enabled, each pointless re-escalation also re-attaches a
    compaction notice -- the standing instruction to 're-run tools if you need
    full output'.

    This is how the storm couples to the PR #33 subject WITHOUT being an
    instance of it: PR #33 is about a BYTE-IDENTICAL stale notice; here the
    notice's stats line genuinely changes every turn (production read
    `187 -> 8`, `192 -> 7`, `196 -> 6` on three consecutive turns) because a
    fresh compaction really did run each time. Different pathology, same
    surface.
    """
    ctx = _mgr(compaction_notice_enabled=True)

    await ctx.add_message({"role": "user", "content": HUGE_PAYLOAD})
    for n in range(12):
        await _add_a_turn(ctx, n)

    notices = []
    for i in range(3):
        view = await ctx.get_messages_for_request()
        found = [
            m
            for m in view
            if isinstance(m.get("content"), str)
            and "context-compaction" in m.get("content", "")
        ]
        assert found, f"request {i} carried no compaction notice"
        notices.append(found[0]["content"])
        await _add_a_turn(ctx, 200 + i)

    assert len(notices) == 3


@pytest.mark.xfail(
    strict=True,
    reason=(
        "FAIL-BEFORE (model_performance-fwut). An irreducible protected floor "
        "cannot be reduced further by definition, so re-running the full "
        "escalation ladder on every subsequent request is pure waste: it "
        "emits a misleading compaction event and notice per request "
        "(measured: 969 events / 988 requests, and 2,165 in a second session) "
        "while achieving zero additional reduction. Once compaction has "
        "reached level 8 and is STILL above target with nothing left that it "
        "is permitted to touch, it should recognise the floor and stop "
        "re-escalating -- the same 'nothing NEW to decide' fast path "
        "_compact_ephemeral already has, which is currently unreachable "
        "because _exceeds_threshold stays permanently True. Remove this "
        "marker when that lands."
    ),
)
@pytest.mark.asyncio
async def test_irreducible_floor_should_not_re_escalate_every_request():
    hooks = _CountingHooks()
    ctx = _mgr(hooks=hooks)

    await ctx.add_message({"role": "user", "content": HUGE_PAYLOAD})
    for n in range(12):
        await _add_a_turn(ctx, n)

    requests = 10
    for i in range(requests):
        await ctx.get_messages_for_request()
        await _add_a_turn(ctx, 100 + i)

    assert len(hooks.compactions) <= 2, (
        "compaction should recognise an irreducible floor and stop "
        f"re-escalating; got {len(hooks.compactions)} escalations for "
        f"{requests} requests"
    )

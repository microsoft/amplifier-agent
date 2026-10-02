"""
Tests for the two-part cache-stability fix in SimpleContextManager:

(a) Notice placement: the compaction notice must be appended at the TAIL of
    the returned message list (never inserted into the prefix), with
    role="user" (not "system") and metadata={"ephemeral": True}, so that:
      - every provider's caching (explicit breakpoints or automatic prefix
        matching) sees a stable prefix across calls, and
      - the Anthropic provider's ephemeral walk-back logic
        (_count_trailing_ephemeral_messages) actually recognizes and skips it
        -- which requires role != "system" (system-role messages never reach
        the "conversation" list the walk-back operates on at all) AND
        metadata.ephemeral=True on a TRAILING message.

(b) Sticky compaction decisions: once a message is removed, truncated, or
    stubbed, that decision must never be re-derived or reversed on a later
    call. This is what keeps the prefix byte-stable across calls where
    history only grew by a turn or two, instead of continuously re-deriving
    (and potentially shifting) the whole compaction decision every call.

The most important test here is `test_prefix_stability_regression` -- this is
the test that would have caught the original bug (notice at index 1 + fully
re-derived compaction on every call).
"""

import logging
from typing import Any

import pytest
from amplifier_module_context_simple import SimpleContextManager


def _padded(i: int, role: str, size: int = 80) -> dict:
    """A message with enough bulk to move the token counter meaningfully."""
    return {"role": role, "content": f"{role} message {i} " + ("x" * size)}


async def _fill_until_compacted(context: SimpleContextManager, turns: int = 40) -> None:
    """Add enough user/assistant turns to push past compact_threshold at least once."""
    for i in range(turns):
        await context.add_message(_padded(i, "user"))
        await context.add_message(_padded(i, "assistant"))


def _make_context(**overrides: Any) -> SimpleContextManager:
    config: dict[str, Any] = {
        "max_tokens": 2000,
        "compact_threshold": 0.5,
        "target_usage": 0.3,
        "protected_recent": 0.2,
        "protected_tool_results": 1,
        "truncate_chars": 40,
        "compaction_notice_enabled": True,
        "compaction_notice_min_level": 1,
    }
    config.update(overrides)
    return SimpleContextManager(**config)


@pytest.mark.asyncio
async def test_prefix_stability_regression():
    """THE regression test: two consecutive get_messages_for_request() calls,
    where history grew by exactly one turn in between, must produce a
    byte-identical shared prefix (everything except the trailing ephemeral
    notice, which is expected to change only when a NEW escalation happens).

    This is exactly the scenario the original bug broke: the compaction
    notice was inserted at index 1 (inside the prefix) and the entire
    compaction decision was re-derived from scratch on every call, so the
    prefix shifted on every single turn once compaction started firing.
    """
    context = _make_context()
    await _fill_until_compacted(context)

    call1 = await context.get_messages_for_request()
    assert context._last_compaction_stats is not None, (
        "Test setup must actually trigger compaction for this test to be meaningful"
    )

    # Grow history by exactly one turn.
    await context.add_message(_padded(9001, "user"))
    await context.add_message(_padded(9001, "assistant"))

    call2 = await context.get_messages_for_request()

    # Strip any trailing ephemeral notice (identified by metadata.ephemeral) --
    # that tail element is EXPECTED to be present/absent/reworded depending on
    # whether a new escalation happened; it is deliberately outside the
    # "prefix" this test is protecting.
    def strip_trailing_notice(messages: list[dict]) -> list[dict]:
        if messages and (messages[-1].get("metadata") or {}).get("ephemeral"):
            return messages[:-1]
        return messages

    prefix1 = strip_trailing_notice(call1)
    prefix2 = strip_trailing_notice(call2)

    # call2 grew by one turn (2 messages) relative to call1's prefix region.
    # The OVERLAPPING region (everything call1 had) must be byte-identical.
    shared_len = len(prefix1)
    assert prefix2[:shared_len] == prefix1, (
        "Shared prefix changed between two calls where history grew by only "
        "one turn -- this is exactly the cache-busting bug this fix exists "
        "to prevent. Diff:\n"
        f"call1 prefix: {prefix1}\n"
        f"call2 prefix (truncated to same length): {prefix2[:shared_len]}"
    )

    # And the new call should indeed have grown (sanity check the test itself
    # isn't vacuously true because nothing was appended).
    assert len(prefix2) >= shared_len


@pytest.mark.asyncio
async def test_prefix_stability_across_many_incremental_turns():
    """Stronger version: run many turns past the first escalation and assert
    the prefix only ever grows (never rewrites) except right at an escalation
    boundary. Detects any residual per-call churn the single-step test above
    might miss.
    """
    context = _make_context()
    await _fill_until_compacted(context)

    previous = None
    rewrites = 0
    escalations = 0
    last_stats = None
    for i in range(20):
        await context.add_message(_padded(1000 + i, "user"))
        await context.add_message(_padded(1000 + i, "assistant"))
        current = await context.get_messages_for_request()

        stats = context._last_compaction_stats
        new_escalation = stats is not last_stats
        if new_escalation:
            escalations += 1
            last_stats = stats

        if previous is not None:
            prev_body = (
                previous[:-1]
                if (previous[-1].get("metadata") or {}).get("ephemeral")
                else previous
            )
            curr_body = (
                current[:-1]
                if (current[-1].get("metadata") or {}).get("ephemeral")
                else current
            )
            shared = min(len(prev_body), len(curr_body))
            if curr_body[:shared] != prev_body[:shared] and not new_escalation:
                rewrites += 1

        previous = current

    assert rewrites == 0, (
        f"Prefix was rewritten {rewrites} time(s) on a call that was NOT a "
        f"new compaction escalation -- decisions are not sticky."
    )


@pytest.mark.asyncio
async def test_notice_is_appended_at_tail_not_prefix():
    """The compaction notice must be the LAST message, not inserted at
    position 1 (the original bug's placement)."""
    context = _make_context()
    await _fill_until_compacted(context)

    messages = await context.get_messages_for_request()
    assert context._last_compaction_stats is not None

    notice_candidates = [
        m
        for m in messages
        if (m.get("metadata") or {}).get("source") == "context-compaction"
    ]
    assert len(notice_candidates) == 1, "Expected exactly one compaction notice"
    notice = notice_candidates[0]

    assert messages[-1] is notice, (
        "Compaction notice must be the LAST message (tail), not mid-prefix"
    )
    assert messages[1] is not notice, (
        "Regression: notice must not land at position 1 (the old bug)"
    )


@pytest.mark.asyncio
async def test_notice_carries_ephemeral_metadata_and_non_system_role():
    """The notice must be recognizable to the Anthropic provider's ephemeral
    walk-back logic: metadata.ephemeral=True AND role != "system" (a
    "system"-role message never reaches the "conversation" list that walk-back
    operates on -- see amplifier_module_provider_anthropic._complete_chat_request,
    which extracts ALL role=="system" messages out of the conversation before
    the ephemeral walk-back ever runs)."""
    context = _make_context()
    await _fill_until_compacted(context)

    messages = await context.get_messages_for_request()
    notice = messages[-1]

    assert notice.get("metadata", {}).get("ephemeral") is True, (
        "Notice must carry metadata.ephemeral=True for the provider's "
        "trailing-ephemeral walk-back to recognize and skip it"
    )
    assert notice.get("role") != "system", (
        "Notice must NOT be role='system' -- system-role messages are "
        "extracted out of the conversation entirely by the Anthropic "
        "provider (merged into the single system content block), so an "
        "ephemeral system-role notice would (a) never be seen by the "
        "conversation-region ephemeral walk-back at all, and (b) corrupt "
        "the SYSTEM cache breakpoint too, since it would change the "
        "aggregate system text on every compaction."
    )


@pytest.mark.asyncio
async def test_notice_is_still_visible_and_informative():
    """Moving the notice to the tail must not silently make it ineffective --
    it must still contain real, informative content the model can act on."""
    context = _make_context()
    await _fill_until_compacted(context)

    messages = await context.get_messages_for_request()
    notice = messages[-1]
    content = notice.get("content", "")

    assert isinstance(content, str) and content, "Notice must have non-empty content"
    assert "compact" in content.lower(), "Notice should mention compaction happened"
    assert "system-reminder" in content, (
        "Notice should use the established system-reminder convention"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("verbosity", ["minimal", "normal", "verbose"])
async def test_notice_omits_token_totals_but_keeps_diagnostic_stats(verbosity):
    """Local compaction estimates must not be presented as provider token counts."""
    context = _make_context(compaction_notice_verbosity=verbosity)
    await _fill_until_compacted(context)

    messages = await context.get_messages_for_request()
    stats = context._last_compaction_stats
    assert stats is not None, "The fixture must actually compact"
    for key in ("before_tokens", "after_tokens", "target_tokens"):
        assert isinstance(stats[key], int) and stats[key] > 0

    notices = [
        message
        for message in messages
        if (message.get("metadata") or {}).get("source") == "context-compaction"
    ]
    assert len(notices) == 1
    notice = notices[0]
    assert messages[-1] is notice
    assert notice["role"] == "user"
    assert notice["metadata"]["ephemeral"] is True
    content = notice["content"]
    assert '<system-reminder source="context-compaction">' in content
    assert "Context has been compacted" in content
    assert "- Tokens:" not in content
    assert "(target:" not in content
    if verbosity != "minimal":
        assert "- Strategy level:" in content
        assert "- Messages:" in content
        assert "- Tool results:" in content
        assert "What was preserved:" in content
        assert "What may be affected:" in content


@pytest.mark.asyncio
async def test_sticky_removal_decisions_never_reversed():
    """Once a message is decided as removed, it must stay removed on every
    subsequent call -- even as more history is added and the raw-history
    percentages that originally selected it shift."""
    context = _make_context()
    await _fill_until_compacted(context, turns=30)

    await context.get_messages_for_request()
    stats1 = context._last_compaction_stats
    assert stats1 is not None
    removed_seqs_after_call1 = set(context._removed_seqs)
    assert removed_seqs_after_call1, (
        "Test setup should have actually removed some messages"
    )

    # Grow more, forcing potential re-evaluation.
    for i in range(5):
        await context.add_message(_padded(2000 + i, "user"))
        await context.add_message(_padded(2000 + i, "assistant"))
    await context.get_messages_for_request()

    # Every seq removed after call 1 must still be removed now -- sticky
    # decisions are additive only, never reversed.
    assert removed_seqs_after_call1.issubset(context._removed_seqs), (
        "A previously-removed message was un-removed -- compaction decisions "
        "must be permanent (sticky), not re-derived from scratch each call."
    )


@pytest.mark.asyncio
async def test_large_system_message_counts_toward_compaction_trigger():
    """Regression test: the sticky escalation-decision threshold check must
    include the SYSTEM message's token count, not just the non-system
    (conversation) portion.

    This guards against a real bug introduced during development of the
    sticky-decision mechanism: the escalation check was computed from
    `_estimate_tokens(working_messages)` where `working_messages` was only
    the non-system portion, silently dropping the system message's token
    contribution. Whenever the system message is a large fraction of the
    budget (a common real-world case -- e.g. a big bundle-composed system
    prompt), that omission means the conversation-only portion may never
    cross the threshold on its own, so compaction silently never fires even
    though total usage (system + conversation) is well over budget.
    """
    large_system_content = "x" * 4000  # ~1000 tokens, dominates a small budget
    context = _make_context(
        max_tokens=1200,
        compact_threshold=0.6,
        target_usage=0.4,
        protected_recent=0.2,
    )
    await context.add_message({"role": "system", "content": large_system_content})

    # Add modest conversation growth -- alone, this would stay well under
    # compact_threshold * budget, but the system message alone already
    # exceeds it (1000 tokens vs 0.6*1200=720).
    for i in range(6):
        await context.add_message({"role": "user", "content": f"turn {i}"})
        await context.add_message({"role": "assistant", "content": f"ack {i}"})
        await context.get_messages_for_request()

    assert context._last_compaction_stats is not None, (
        "Compaction never triggered even though system message tokens alone "
        "exceed compact_threshold * budget -- the escalation check is "
        "silently ignoring system message size."
    )


def _estimate(messages: list[dict]) -> int:
    """Mirror of SimpleContextManager._estimate_tokens for test-side assertions."""
    return sum(len(str(m)) // 4 for m in messages)


async def _build_large_system_scenario(
    budget: int,
    system_tokens: int,
    turns: int,
    tool_result_chars: int,
    **overrides: Any,
) -> SimpleContextManager:
    """Large system prompt + a tool-heavy conversation that is over threshold.

    The conversation is deliberately made mostly of tool results so there is
    ample compactable (truncatable, then removable) non-system material -- the
    scenario tests the ESCALATION comparison, not the protections.
    """
    config: dict[str, Any] = {
        "max_tokens": budget,
        "compact_threshold": 0.92,
        "target_usage": 0.50,
        "protected_recent": 0.20,
        "protected_tool_results": 1,
        "truncate_chars": 100,
        # Disabled so effective_budget == budget (no notice reserve) and the
        # returned view carries no trailing notice -- keeps the token math in
        # these assertions exact and about compaction only.
        "compaction_notice_enabled": False,
    }
    config.update(overrides)
    context = SimpleContextManager(**config)

    await context.add_message({"role": "system", "content": "S" * (4 * system_tokens)})
    for i in range(turns):
        await context.add_message({"role": "user", "content": f"task {i}"})
        await context.add_message(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": f"c{i}", "type": "function", "function": {"name": "f"}}
                ],
            }
        )
        await context.add_message(
            {
                "role": "tool",
                "tool_call_id": f"c{i}",
                "content": "R" * tool_result_chars,
            }
        )
    return context


@pytest.mark.asyncio
async def test_escalation_does_not_stall_at_level_1_with_large_system_message():
    """Regression test: the per-level escalation/termination comparisons must
    compare like with like -- TOTAL (system + conversation) usage against the
    TOTAL-budget target.

    This guards the same class of bug as
    `test_large_system_message_counts_toward_compaction_trigger`, but in a
    different place: that one covers the TRIGGER check, this one covers the
    per-level ESCALATION/termination checks inside _compact_ephemeral and the
    helpers it delegates to (_truncate_tool_wave,
    _remove_messages_with_protection).

    The bug: `target_tokens` is computed from the TOTAL budget
    (budget * target_usage), but `current_tokens` collapsed to the NON-SYSTEM
    total as soon as any helper recomputed it from `working_messages` (which
    excludes system messages). With a large system prompt, that means
    compaction terminates at Level 1 after truncating a single tool result --
    the non-system total is already under the total-budget target -- and never
    escalates again. The effective cap silently becomes
    `target_usage * budget + system_estimate` rather than the configured
    `target_usage * budget`.
    """
    budget = 10_000
    context = await _build_large_system_scenario(
        budget=budget,
        system_tokens=6_000,  # ~60% of budget
        turns=20,
        tool_result_chars=600,
    )
    target_tokens = int(budget * 0.50)

    view = await context.get_messages_for_request()
    stats = context._last_compaction_stats
    assert stats is not None, "Test setup must actually trigger compaction"

    level = stats["strategy_level"]
    total_after = _estimate(view)

    # (1) The stall signature: terminating at Level 1 while TOTAL usage is
    # still above target means the termination check compared non-system
    # tokens against a total-budget target.
    assert not (level == 1 and total_after > target_tokens), (
        f"Compaction stalled at Level {level} with total usage still "
        f"{total_after:,} tokens (target {target_tokens:,}, "
        f"{total_after / budget:.1%} of budget). The escalation check is "
        f"comparing NON-SYSTEM tokens against a TOTAL-budget target."
    )

    # (2) Either we got under the total target, or escalation ran all the way
    # to the last level and the protections (system messages, last user
    # message, last N tool results) are what stopped us -- not a units bug.
    assert total_after <= target_tokens or level >= 8, (
        f"Compaction ended at Level {level} with {total_after:,} total tokens "
        f"(target {target_tokens:,}) without exhausting the escalation ladder."
    )

    # (3) The specific broken cap: the buggy code converges to
    # `target + system_estimate`, i.e. it can never go below the system floor
    # plus a full target's worth of conversation. Assert the conversation
    # portion alone is now well under target, not merely under it.
    system_tokens_now = _estimate([m for m in view if m.get("role") == "system"])
    non_system_after = total_after - system_tokens_now
    assert non_system_after < target_tokens, (
        f"Non-system portion ({non_system_after:,} tokens) was left right at "
        f"the total-budget target ({target_tokens:,}) -- the signature of the "
        f"effective cap degrading to `target + system_estimate`."
    )


@pytest.mark.asyncio
async def test_compaction_reaches_total_budget_target_with_large_system_message():
    """With a system prompt large enough to matter but small enough that the
    total target is still reachable, compaction must actually land the FULL
    request (system included) at or under `target_usage * budget`.

    Under the units-mixing bug this settles at roughly
    `target_usage * budget + system_estimate` instead -- i.e. it overshoots
    the configured target by the entire size of the system prompt.
    """
    budget = 10_000
    context = await _build_large_system_scenario(
        budget=budget,
        system_tokens=2_000,  # ~20% of budget -- target stays reachable
        turns=20,
        tool_result_chars=1_400,
    )
    target_tokens = int(budget * 0.50)

    view = await context.get_messages_for_request()
    stats = context._last_compaction_stats
    assert stats is not None, "Test setup must actually trigger compaction"

    total_after = _estimate(view)
    assert total_after <= target_tokens, (
        f"Compacted view is {total_after:,} tokens (target {target_tokens:,}, "
        f"{total_after / budget:.1%} of budget) at Level "
        f"{stats['strategy_level']}. The TOTAL returned view -- system message "
        f"included -- must fit the configured target, not overshoot it by the "
        f"size of the system prompt."
    )


@pytest.mark.asyncio
async def test_no_escalation_when_sticky_view_already_under_threshold():
    """Adding a single small turn after an escalation should NOT trigger a new
    escalation (and therefore must not change _last_compaction_stats) as long
    as the sticky (already-decided) view remains comfortably under the
    compact_threshold. This is the core of "prefix only changes at a genuine
    compaction step, not continuously."
    """
    context = _make_context(compact_threshold=0.92, target_usage=0.5)
    await _fill_until_compacted(context, turns=40)

    await context.get_messages_for_request()
    stats_after_first = context._last_compaction_stats
    assert stats_after_first is not None

    # A single small turn shouldn't be enough to cross 92% again immediately.
    await context.add_message({"role": "user", "content": "ok"})
    await context.add_message({"role": "assistant", "content": "ok"})
    await context.get_messages_for_request()

    assert context._last_compaction_stats is stats_after_first, (
        "A single small turn triggered a brand new compaction escalation -- "
        "expected the sticky view to already be under threshold, meaning no "
        "new decisions (and no stats/notice change) should have occurred."
    )


@pytest.mark.asyncio
async def test_system_messages_never_compacted_under_stickiness():
    """System messages must always survive compaction regardless of how many
    escalations have occurred."""
    context = _make_context()
    await context.add_message({"role": "system", "content": "You are a helpful agent."})
    await _fill_until_compacted(context, turns=40)

    for i in range(10):
        await context.add_message(_padded(3000 + i, "user"))
        await context.add_message(_padded(3000 + i, "assistant"))
        messages = await context.get_messages_for_request()
        system_msgs = [m for m in messages if m.get("role") == "system"]
        assert len(system_msgs) == 1
        assert system_msgs[0]["content"] == "You are a helpful agent."


@pytest.mark.asyncio
async def test_last_user_message_always_protected_under_stickiness():
    """The most recent user message must never be removed or stubbed, even
    after many rounds of sticky escalation."""
    context = _make_context()
    await _fill_until_compacted(context, turns=50)

    last_content = "THE CURRENT TASK: do not lose me"
    await context.add_message({"role": "user", "content": last_content})

    messages = await context.get_messages_for_request()
    # Ignore the trailing ephemeral notice when looking for the last "real" user msg.
    body = (
        messages[:-1]
        if (messages[-1].get("metadata") or {}).get("ephemeral")
        else messages
    )
    user_msgs = [m for m in body if m.get("role") == "user"]
    assert user_msgs, "Expected at least one user message to survive"
    assert user_msgs[-1]["content"] == last_content, (
        "The last user message must be preserved verbatim (never stubbed/removed)"
    )


@pytest.mark.asyncio
async def test_tool_pairs_remain_atomic_under_sticky_removal():
    """No tool_result may survive in the returned view without its matching
    tool_use, and vice versa -- across many sticky escalations."""
    context = _make_context(max_tokens=1500, compact_threshold=0.5, target_usage=0.3)

    for i in range(30):
        await context.add_message(
            {"role": "user", "content": f"do task {i}" + "x" * 30}
        )
        await context.add_message(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": f"call_{i}",
                        "type": "function",
                        "function": {"name": "do_thing"},
                    }
                ],
            }
        )
        await context.add_message(
            {"role": "tool", "tool_call_id": f"call_{i}", "content": "result " * 20}
        )

    for i in range(30, 45):
        await context.add_message({"role": "user", "content": f"more {i}" + "x" * 30})
        await context.add_message({"role": "assistant", "content": f"ack {i}"})
        messages = await context.get_messages_for_request()

        tool_use_ids = set()
        for m in messages:
            for tc in m.get("tool_calls") or []:
                tc_id = tc.get("id")
                if tc_id:
                    tool_use_ids.add(tc_id)
        tool_result_ids = {
            m.get("tool_call_id") for m in messages if m.get("role") == "tool"
        }
        orphans = tool_result_ids - tool_use_ids
        assert not orphans, f"Orphaned tool_result(s) found at iteration {i}: {orphans}"
        orphan_calls = tool_use_ids - tool_result_ids
        assert not orphan_calls, (
            f"Orphaned tool_use(s) found at iteration {i}: {orphan_calls}"
        )


@pytest.mark.asyncio
async def test_get_messages_returns_full_uncompacted_history():
    """Non-destructive guarantee must still hold: get_messages() always
    returns the complete, unmodified history regardless of sticky state."""
    context = _make_context()
    await _fill_until_compacted(context, turns=40)
    await context.get_messages_for_request()  # trigger at least one escalation

    full_history = await context.get_messages()
    assert len(full_history) == len(context.messages)
    # None of the stored messages should carry compaction markers -- those
    # only ever appear on the ephemeral VIEW, never on stored history.
    assert not any(m.get("_truncated") for m in full_history)
    assert not any(m.get("_stubbed") for m in full_history)


# ---------------------------------------------------------------------------
# (c) Over-budget signal: an unreachable target must never be returned silently
# ---------------------------------------------------------------------------

MODULE_LOGGER = "amplifier_module_context_simple"


def _over_budget_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Every WARNING emitted by the over-budget guard in this test's run."""
    return [
        r.getMessage()
        for r in caplog.records
        if r.levelno >= logging.WARNING and "OVER BUDGET" in r.getMessage()
    ]


@pytest.mark.asyncio
async def test_warns_when_system_share_makes_budget_unreachable(
    caplog: pytest.LogCaptureFixture,
):
    """Regression test: when the system prompt ALONE exceeds the compaction
    target, the target is arithmetically unreachable -- system messages are
    never compacted, so no escalation level can ever get under it.

    Before this fix that state was returned in TOTAL SILENCE: an over-budget
    view (the reviewer measured 2,519 tokens against a 1,000 budget -- 252%)
    with zero WARNING records. Silent degradation is the one failure mode this
    module cannot afford, since it manages every session's memory. The warning
    must fire AND must name the system share as the floor, so an operator
    knows the actionable knob is the system prompt (or the budget), not more
    aggressive compaction.
    """
    budget = 1_000
    # ~2,000 tokens of system prompt against a 1,000 token budget: the
    # reviewer's scenario shape -- system share alone is 2x the whole budget.
    context = SimpleContextManager(
        max_tokens=budget,
        compact_threshold=0.92,
        target_usage=0.50,
        protected_recent=0.20,
        protected_tool_results=1,
        truncate_chars=100,
        compaction_notice_enabled=False,
    )
    await context.add_message({"role": "system", "content": "S" * 8_000})
    for i in range(8):
        await context.add_message({"role": "user", "content": f"task {i}"})
        await context.add_message({"role": "assistant", "content": "ack " * 20})

    # The un-compactable floor, measured exactly as the module measures it
    # (on STORED messages, which still carry `_seq`).
    system_tokens = _estimate(
        [m for m in context.messages if m.get("role") == "system"]
    )

    with caplog.at_level(logging.WARNING, logger=MODULE_LOGGER):
        view = await context.get_messages_for_request()

    stats = context._last_compaction_stats
    assert stats is not None, "Test setup must actually trigger compaction"
    assert _estimate(view) > budget, (
        "Test setup must actually end up over budget for this test to mean "
        f"anything (got {_estimate(view):,} against budget {budget:,})"
    )

    warnings = _over_budget_warnings(caplog)
    assert warnings, (
        "Compaction returned an over-budget view with NO warning -- this is "
        "the silent degradation the fix exists to eliminate. "
        f"View is {_estimate(view):,} tokens against a budget of {budget:,}."
    )
    message = warnings[0]

    # The warning must name the SYSTEM SHARE as the floor -- that is the
    # actionable part. A bare "over budget" line tells an operator nothing
    # about which knob moves.
    assert "system prompt ALONE" in message and "floor" in message, (
        f"Warning does not name the system share as the un-reducible floor: {message}"
    )

    # All four numbers an operator needs to act must be present.
    assert f"{stats['after_tokens']:,}" in message, f"final_tokens missing: {message}"
    assert f"{budget:,}" in message, f"budget missing: {message}"
    assert f"{system_tokens:,}" in message, (
        f"system_tokens ({system_tokens:,}) missing: {message}"
    )
    assert f"{stats['target_tokens']:,}" in message, f"target_tokens missing: {message}"


@pytest.mark.asyncio
async def test_no_over_budget_warning_when_compaction_lands_under_budget(
    caplog: pytest.LogCaptureFixture,
):
    """The counterpart to the test above: a HEALTHY compaction -- one that
    actually reaches its target -- must stay quiet. A guard that fires on
    every successful compaction is noise, and noise is how real warnings get
    ignored.
    """
    budget = 10_000
    context = await _build_large_system_scenario(
        budget=budget,
        system_tokens=2_000,  # ~20% of budget -- target stays reachable
        turns=20,
        tool_result_chars=1_400,
    )

    with caplog.at_level(logging.WARNING, logger=MODULE_LOGGER):
        view = await context.get_messages_for_request()

    assert context._last_compaction_stats is not None, (
        "Test setup must actually trigger compaction"
    )
    assert _estimate(view) <= budget, (
        "Test setup must actually land under budget for this test to be "
        "checking what it claims to check"
    )
    assert not _over_budget_warnings(caplog), (
        "A healthy, under-budget compaction emitted an over-budget warning: "
        f"{_over_budget_warnings(caplog)}"
    )


# ---------------------------------------------------------------------------
# (d) Tail notice must never split an unanswered tool_use / tool_result pair
# ---------------------------------------------------------------------------


def _notices(messages: list[dict]) -> list[dict]:
    return [
        m
        for m in messages
        if (m.get("metadata") or {}).get("source") == "context-compaction"
    ]


@pytest.mark.asyncio
async def test_notice_skipped_when_tail_has_unanswered_tool_calls():
    """Appending the notice at the TAIL is what makes it cache-safe, but the
    tail is not always a safe place to stand.

    If the view ends with an assistant message carrying tool_calls, the tool
    results have not arrived yet -- appending a user-role notice there lands
    it BETWEEN the tool_use and its tool_result, which providers reject or
    mishandle. This exposure is new with the move to the tail (the old
    index-1 insert could never land here), so it needs its own guard.
    """
    context = _make_context()
    await _fill_until_compacted(context)

    # Establish that compaction (and therefore the notice) is active at all.
    baseline = await context.get_messages_for_request()
    assert context._last_compaction_stats is not None, (
        "Test setup must actually trigger compaction"
    )
    assert _notices(baseline), (
        "Baseline must carry a notice, otherwise the skip below proves nothing"
    )

    # Now end the conversation on an assistant turn with PENDING tool_calls.
    await context.add_message({"role": "user", "content": "run the tool " + "y" * 80})
    await context.add_message(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call_pending", "type": "function", "function": {"name": "do"}}
            ],
        }
    )

    view = await context.get_messages_for_request()

    assert view[-1].get("tool_calls"), (
        "Test setup must actually leave an unanswered tool_calls message at "
        "the tail for this test to be meaningful"
    )
    assert not _notices(view), (
        "Compaction notice was appended after an assistant message with "
        "unanswered tool_calls -- this interleaves a user-role message "
        "between tool_use and tool_result, which providers reject."
    )


@pytest.mark.asyncio
async def test_notice_returns_once_tool_results_arrive():
    """Skipping is safe precisely because it is temporary: the notice is
    derived from sticky stats that persist, so it must come back on the very
    next request once the tool results land and the tail is safe again.

    (This is what makes "skip" the right call over "reposition before the
    assistant message" -- repositioning would put the notice back INSIDE the
    prefix, re-introducing the cache-busting this whole fix exists to prevent.)
    """
    context = _make_context()
    await _fill_until_compacted(context)
    await context.get_messages_for_request()
    assert context._last_compaction_stats is not None

    await context.add_message({"role": "user", "content": "run the tool " + "y" * 80})
    await context.add_message(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call_pending", "type": "function", "function": {"name": "do"}}
            ],
        }
    )
    skipped_view = await context.get_messages_for_request()
    assert not _notices(skipped_view), "Precondition: notice skipped at pending tail"

    # The tool result arrives -- the pair is complete, the tail is safe again.
    await context.add_message(
        {
            "role": "tool",
            "tool_call_id": "call_pending",
            "content": "the tool result " * 10,
        }
    )
    resumed_view = await context.get_messages_for_request()

    notices = _notices(resumed_view)
    assert len(notices) == 1, (
        "Notice did not reappear after the tool results arrived -- skipping "
        "must be a one-request deferral, not a permanent loss of the notice."
    )
    assert resumed_view[-1] is notices[0], (
        "The restored notice must still be at the TAIL (cache-stable placement)"
    )
    assert not resumed_view[-2].get("tool_calls"), (
        "Notice must not sit directly after an unanswered tool_calls message"
    )

    # A repeated ordinary fetch must rebuild exactly one stable tail notice,
    # rather than retaining the prior ephemeral notice and duplicating it.
    repeated_view = await context.get_messages_for_request()
    assert repeated_view == resumed_view
    assert len(_notices(repeated_view)) == 1


@pytest.mark.asyncio
async def test_notice_waits_for_all_results_of_a_multi_call_group():
    """A partial multi-result group is still an unsafe tail for a notice."""
    context = _make_context()
    await _fill_until_compacted(context)
    await context.get_messages_for_request()

    await context.add_message({"role": "user", "content": "run both tools"})
    await context.add_message(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call_one", "type": "function", "function": {"name": "one"}},
                {"id": "call_two", "type": "function", "function": {"name": "two"}},
            ],
        }
    )
    await context.add_message(
        {"role": "tool", "tool_call_id": "call_one", "content": "first result"}
    )

    partial_view = await context.get_messages_for_request()
    assert not _notices(partial_view)

    await context.add_message(
        {"role": "tool", "tool_call_id": "call_two", "content": "second result"}
    )
    complete_view = await context.get_messages_for_request()
    assert len(_notices(complete_view)) == 1
    assert complete_view[-1] is _notices(complete_view)[0]


# ---------------------------------------------------------------------------
# (e) `_seq` is internal bookkeeping and must not cross the module boundary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seq_stripped_from_returned_view_but_retained_in_stored_history():
    """`_seq` is sticky-compaction identity: internal bookkeeping that is
    meaningless to a provider, and (because _estimate_tokens stringifies the
    whole message dict) it inflates the token estimate of every message
    carrying it. Strip it from the RETURNED view only.

    The stored history must keep it -- the sticky decision store is keyed on
    it, so losing it would silently break stickiness and, with it, prefix
    stability.
    """
    context = _make_context()
    await _fill_until_compacted(context, turns=40)

    view = await context.get_messages_for_request()

    assert all("_seq" not in (m.get("metadata") or {}) for m in view), (
        "`_seq` leaked into the provider-facing view: "
        f"{[m for m in view if '_seq' in (m.get('metadata') or {})][:2]}"
    )
    assert context.messages, "Test setup must have stored messages"
    assert all("_seq" in (m.get("metadata") or {}) for m in context.messages), (
        "Stripping the returned view destroyed `_seq` in STORED history -- "
        "sticky compaction decisions are keyed on it and would break."
    )


@pytest.mark.asyncio
async def test_stripping_seq_never_mutates_stored_messages():
    """The returned view is NOT reliably built from deep copies: the
    no-compaction path returns stored dicts directly, and even the compacted
    path's `dict(msg)` shallow copies share the SAME nested metadata dict. So
    stripping must rebuild rather than mutate -- an in-place `pop("_seq")`
    here would silently corrupt the sticky decision store.

    Covers BOTH return paths (compaction and no-compaction), since they differ
    in exactly how much sharing there is with stored state.
    """
    context = _make_context()

    # --- Path 1: no compaction (view shares dict objects with storage) ---
    await context.add_message({"role": "user", "content": "small"})
    stored_msg = context.messages[0]
    stored_meta = stored_msg["metadata"]
    seq_before = stored_meta["_seq"]

    view = await context.get_messages_for_request()
    assert context._last_compaction_stats is None, "Path 1 must not compact"
    assert all("_seq" not in (m.get("metadata") or {}) for m in view)
    assert stored_meta["_seq"] == seq_before, (
        "Stripping mutated the STORED message's metadata dict in place "
        "(no-compaction path)"
    )
    assert context.messages[0]["metadata"] is stored_meta, (
        "Stored message's metadata dict was replaced -- stripping must only "
        "affect the returned view"
    )

    # --- Path 2: compaction (view built from shallow copies sharing metadata) ---
    await _fill_until_compacted(context, turns=40)
    stored_metas = [m["metadata"] for m in context.messages]
    seqs_before = [meta["_seq"] for meta in stored_metas]

    compacted_view = await context.get_messages_for_request()
    assert context._last_compaction_stats is not None, "Path 2 must compact"

    assert all("_seq" not in (m.get("metadata") or {}) for m in compacted_view)
    assert [meta["_seq"] for meta in stored_metas] == seqs_before, (
        "Stripping mutated stored metadata dicts in place (compaction path) -- "
        "the shallow-copy sharing hazard this rebuild exists to avoid."
    )


@pytest.mark.asyncio
async def test_prefix_stability_survives_notice_guard_and_seq_stripping():
    """The core property must still hold after both the tail-notice guard and
    `_seq` stripping: two consecutive calls with history grown by one turn
    still produce a byte-identical shared prefix.

    Both changes are deterministic per message (strip is pure; the guard only
    affects the trailing notice), so neither may introduce per-call churn.
    This re-asserts the property specifically downstream of them rather than
    trusting that the original regression test still covers it.
    """
    context = _make_context()
    await _fill_until_compacted(context)

    call1 = await context.get_messages_for_request()
    assert context._last_compaction_stats is not None

    await context.add_message(_padded(7001, "user"))
    await context.add_message(_padded(7001, "assistant"))
    call2 = await context.get_messages_for_request()

    def body(messages: list[dict]) -> list[dict]:
        if messages and (messages[-1].get("metadata") or {}).get("ephemeral"):
            return messages[:-1]
        return messages

    prefix1, prefix2 = body(call1), body(call2)
    assert prefix2[: len(prefix1)] == prefix1, (
        "Shared prefix changed between two calls where history grew by only "
        "one turn -- the notice guard or `_seq` stripping introduced churn."
    )
    # And the strip really is applied on this path (guards against the test
    # passing vacuously if stripping were removed).
    assert all("_seq" not in (m.get("metadata") or {}) for m in prefix2)

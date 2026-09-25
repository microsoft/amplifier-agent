"""Required request content must survive actual compaction, not just storage."""

import asyncio
import copy

import pytest
from amplifier_core.llm_errors import ContextLengthError

from amplifier_module_context_simple import SimpleContextManager, mount


def reminder(content):
    return {
        "role": "user",
        "content": content,
        "metadata": {"ephemeral": True, "persisted": True},
    }


def human(content):
    return {
        "role": "user",
        "content": content,
        "metadata": {"source": "human"},
    }


def public_envelope(message):
    """The request view strips only the manager's internal sequence number."""
    return {
        **message,
        "metadata": {
            key: value
            for key, value in message["metadata"].items()
            if key != "_seq"
        },
    }


async def pressured_context():
    context = SimpleContextManager(max_tokens=1800, compaction_notice_enabled=False)
    await context.add_message({"role": "user", "content": "Original task " + "a" * 150})
    body = "<system-reminders>" + "policy " * 180 + " REQUIRED_FACT</system-reminders>"
    await context.add_message(reminder(body))
    for i in range(40):
        await context.add_message(
            {"role": "assistant", "content": f"Output {i} " + "x" * 1200}
        )
    await context.add_message(
        {"role": "user", "content": "Current correction " + "b" * 150}
    )
    return context, body


@pytest.mark.asyncio
async def test_retention_restores_a_sticky_stub_without_changing_history():
    context, body = await pressured_context()
    canonical = copy.deepcopy(await context.get_messages())
    ordinary = await context.get_messages_for_request()
    assert not any(m["content"] == body for m in ordinary)

    retained = await context.get_messages_for_request_retaining(retain_contents=[body])
    assert sum(m["content"] == body for m in retained) == 1
    assert context._estimate_tokens(retained) <= context.max_tokens
    assert await context.get_messages() == canonical
    assert (
        await context.get_messages_for_request_retaining(retain_contents=[body])
        == retained
    )

    # Retention is per request, not a permanent pin that outlives its producer.
    released = await context.get_messages_for_request_retaining(retain_contents=[])
    assert not any(m["content"] == body for m in released)


@pytest.mark.asyncio
async def test_machine_messages_cannot_take_human_boundary_protection():
    context = SimpleContextManager(max_tokens=1400, compaction_notice_enabled=False)
    first = "Original human request " + "details " * 60 + "ORIGINAL_FACT"
    last = "Latest human correction " + "details " * 60 + "CURRENT_FACT"
    await context.add_message(reminder("Earlier machine state " + "m" * 1200))
    await context.add_message({"role": "user", "content": first})
    for i in range(35):
        await context.add_message(
            {"role": "assistant", "content": f"Work {i} " + "x" * 1000}
        )
    await context.add_message({"role": "user", "content": last})
    await context.add_message(reminder("Later machine state " + "m" * 1200))
    view = await context.get_messages_for_request()
    assert any(m["content"] == first for m in view)
    assert any(m["content"] == last for m in view)
    assert await context.get_messages_for_request() == view


@pytest.mark.asyncio
async def test_only_newest_matching_copy_is_retained():
    context, body = await pressured_context()
    await context.add_message(reminder(body))
    view = await context.get_messages_for_request_retaining(retain_contents=[body])
    assert sum(m["content"] == body for m in view) == 1
    assert sum(m["content"] == body for m in await context.get_messages()) == 2


@pytest.mark.asyncio
async def test_retention_survives_transcript_round_trip():
    context, body = await pressured_context()
    transcript = await context.get_messages()
    resumed = SimpleContextManager(max_tokens=1800, compaction_notice_enabled=False)
    await resumed.set_messages(transcript)
    view = await resumed.get_messages_for_request_retaining(retain_contents=[body])
    assert any(m["content"] == body for m in view)


@pytest.mark.asyncio
async def test_missing_content_fails_without_leaking_a_retention_requirement():
    context, body = await pressured_context()
    with pytest.raises(ValueError, match="not in admitted history"):
        await context.get_messages_for_request_retaining(
            retain_contents=["not admitted"]
        )
    assert not any(
        m["content"] == body for m in await context.get_messages_for_request()
    )


@pytest.mark.asyncio
async def test_impossible_retention_fails_before_returning_an_overfull_view():
    context, body = await pressured_context()
    huge = "<system-reminders>" + "x" * 12000 + "</system-reminders>"
    await context.add_message(reminder(huge))
    with pytest.raises(ContextLengthError, match="Required content was not discarded"):
        await context.get_messages_for_request_retaining(retain_contents=[huge])
    assert any(m["content"] == huge for m in await context.get_messages())
    assert not context._stubbed_seqs and not context._removed_seqs
    # Let the withdrawn content age out of the ordinary recent-message window.
    for i in range(30):
        await context.add_message(
            {"role": "assistant", "content": f"Later work {i} " + "x" * 200}
        )
    # A later request with a feasible requirement recovers normally.
    view = await context.get_messages_for_request_retaining(retain_contents=[body])
    assert any(m["content"] == body for m in view)
    assert context._estimate_tokens(view) <= context.max_tokens


@pytest.mark.asyncio
async def test_post_compaction_failure_rolls_back_request_state_and_decisions():
    emitted: list[tuple[str, dict]] = []

    class Hooks:
        async def emit(self, event: str, data: dict) -> None:
            emitted.append((event, data))

    context = SimpleContextManager(
        max_tokens=1000, compaction_notice_enabled=False, hooks=Hooks()
    )
    required = "<system-reminders>Active retention requirement.</system-reminders>"
    await context.add_message(human("First human request."))
    await context.add_message(reminder(required))
    await context.add_message(
        {
            "role": "assistant",
            "content": "Unremovable loaded tool state: " + "x" * 5000,
            "metadata": {"openai:tool_search_items": ["read_file"]},
        }
    )
    await context.add_message(human("Latest human correction."))

    with pytest.raises(ContextLengthError, match="Required content was not discarded"):
        await context.get_messages_for_request_retaining(
            retain_contents=[required], hard_fit=True
        )

    assert context._last_compaction_stats is None
    assert context._last_token_meter_stats is None
    assert not context._removed_seqs
    assert not context._truncated_seqs
    assert not context._stubbed_seqs
    assert context._sticky_level == 0
    assert context._request_hard_fit is False
    assert not emitted


@pytest.mark.asyncio
async def test_hard_fit_notice_failure_rolls_back_before_emitting_compaction():
    """A rejected final view must not publish a compaction that was rolled back."""
    emitted: list[tuple[str, dict]] = []

    class Hooks:
        async def emit(self, event: str, data: dict) -> None:
            emitted.append((event, data))

    context = SimpleContextManager(
        max_tokens=1000,
        compact_threshold=0.5,
        target_usage=0.5,
        protected_recent=0.2,
        compaction_notice_enabled=True,
        compaction_notice_token_reserve=1,
        hooks=Hooks(),
    )
    await context.add_message(human("Original human request."))
    for i in range(20):
        await context.add_message(
            {"role": "assistant", "content": f"Historical bulk {i}: " + "x" * 300}
        )
    await context.add_message(human("Latest human correction."))
    context._format_compaction_notice = lambda: "notice " * 1000

    with pytest.raises(ContextLengthError, match="Required content was not discarded"):
        await context.get_messages_for_request_retaining(
            retain_contents=[], token_budget=1000, hard_fit=True
        )

    assert not emitted
    assert context._last_compaction_stats is None
    assert not context._removed_seqs
    assert context._request_hard_fit is False


@pytest.mark.asyncio
async def test_pre_delivery_cancellation_rolls_back_compaction_request_state():
    """Cancellation before delivery leaves neither an event nor sticky state."""
    emitted: list[tuple[str, dict]] = []
    compaction_suspended = asyncio.Event()
    release_compaction = asyncio.Event()

    class Hooks:
        async def emit(self, event: str, data: dict) -> None:
            emitted.append((event, data))

    context, body = await pressured_context()
    context._hooks = Hooks()
    canonical = copy.deepcopy(await context.get_messages())
    previous_state = (
        context._removed_seqs.copy(),
        context._truncated_seqs.copy(),
        context._stubbed_seqs.copy(),
        context._sticky_level,
        context._last_compaction_stats,
        context._last_token_meter_stats,
    )
    original_compact = context._compact_ephemeral

    async def suspend_after_compaction(*args):
        compacted = await original_compact(*args)
        compaction_suspended.set()
        await release_compaction.wait()
        return compacted

    context._compact_ephemeral = suspend_after_compaction
    task = asyncio.create_task(
        context.get_messages_for_request_retaining(
            retain_contents=[body], token_budget=1500, hard_fit=True
        )
    )
    try:
        await asyncio.wait_for(compaction_suspended.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release_compaction.set()
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    assert not emitted
    assert (
        context._removed_seqs,
        context._truncated_seqs,
        context._stubbed_seqs,
        context._sticky_level,
        context._last_compaction_stats,
        context._last_token_meter_stats,
    ) == previous_state
    assert context._request_retained_contents == frozenset()
    assert context._request_protected_seqs == set()
    assert context._request_hard_fit is False
    assert context._request_compaction_delivery_started is False
    assert await context.get_messages() == canonical


@pytest.mark.asyncio
async def test_delivery_boundary_cancellation_commits_compaction_state():
    """Cancellation after event delivery begins keeps the validated hard fit."""
    emitted: list[tuple[str, dict]] = []
    delivery_started = asyncio.Event()
    release_delivery = asyncio.Event()

    class Hooks:
        async def emit(self, event: str, data: dict) -> None:
            emitted.append((event, data))
            delivery_started.set()
            await release_delivery.wait()

    context, body = await pressured_context()
    context._hooks = Hooks()
    canonical = copy.deepcopy(await context.get_messages())
    task = asyncio.create_task(
        context.get_messages_for_request_retaining(
            retain_contents=[body], token_budget=1500, hard_fit=True
        )
    )
    try:
        await asyncio.wait_for(delivery_started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release_delivery.set()
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    assert [event for event, _ in emitted] == ["context:compaction"]
    assert context._last_compaction_stats is not None
    assert context._last_token_meter_stats is not None
    assert context._removed_seqs or context._truncated_seqs or context._stubbed_seqs
    assert context._sticky_level > 0
    assert context._request_retained_contents == frozenset()
    assert context._request_protected_seqs == set()
    assert context._request_hard_fit is False
    assert context._request_compaction_delivery_started is False
    assert await context.get_messages() == canonical

    ordinary = await context.get_messages_for_request()
    assert ordinary == await context.get_messages_for_request()
    assert len(ordinary) < len(canonical)
    # The cancelled delivery emitted no further compaction event. `context:budget`
    # appears exactly once here, from the first ordinary request that reached the
    # delivery boundary -- it fires on CHANGE, so the identical second request
    # adds nothing.
    assert [event for event, _ in emitted] == [
        "context:compaction",
        "context:budget",
    ]


@pytest.mark.asyncio
async def test_hard_fit_targets_forced_budget_and_stable_view_survives_inactive_fetches():
    """A provider-forced fit is full-budget, while ordinary explicit budgets are not."""
    hard, body = await pressured_context()
    canonical = copy.deepcopy(await hard.get_messages())

    forced = await hard.get_messages_for_request_retaining(
        retain_contents=[body],
        token_budget=1500,
        hard_fit=True,
    )
    assert hard._last_compaction_stats is not None
    assert hard._last_compaction_stats["target_tokens"] == 1500
    assert hard._request_hard_fit is False
    assert await hard.get_messages() == canonical
    removed_contents = {
        message["content"]
        for message in canonical
        if message["metadata"]["_seq"] in hard._removed_seqs
    }
    assert removed_contents, "Setup must record an old sticky removal"

    # The forced view is now the stable provider-facing view even when a later
    # caller has no active retention requirement and uses the ordinary getter.
    ordinary = await hard.get_messages_for_request()
    inactive = await hard.get_messages_for_request_retaining(retain_contents=[])
    assert ordinary == forced == inactive
    assert not removed_contents & {message.get("content") for message in ordinary}

    normal, normal_body = await pressured_context()
    ordinary_budget = await normal.get_messages_for_request_retaining(
        retain_contents=[normal_body],
        token_budget=1500,
    )
    assert normal._last_compaction_stats is not None
    assert normal._last_compaction_stats["target_tokens"] == 750
    assert normal._request_hard_fit is False
    assert normal._estimate_tokens(ordinary_budget) <= normal.max_tokens

    empty, _ = await pressured_context()
    await empty.get_messages_for_request_retaining(
        retain_contents=[], token_budget=1500, hard_fit=True
    )
    assert empty._last_compaction_stats is not None
    assert empty._last_compaction_stats["target_tokens"] == 1500
    assert empty._request_hard_fit is False


@pytest.mark.asyncio
async def test_hard_fit_keeps_new_human_reminder_and_current_tool_result():
    """New growth is visible, but old sticky removals never reinflate."""
    context = SimpleContextManager(
        max_tokens=1800,
        protected_tool_results=1,
        compaction_notice_enabled=False,
    )
    first = "Original human task: retain this complete prompt."
    required = "<system-reminders>" + "active policy " * 150 + "</system-reminders>"
    await context.add_message(human(first))
    await context.add_message(reminder(required))
    for i in range(30):
        await context.add_message(
            {"role": "assistant", "content": f"Historical bulk {i}: " + "x" * 900}
        )

    await context.get_messages_for_request_retaining(
        retain_contents=[required], token_budget=1500, hard_fit=True
    )
    removed_contents = {
        message["content"]
        for message in await context.get_messages()
        if message["metadata"]["_seq"] in context._removed_seqs
    }
    assert removed_contents, "Setup must record an old sticky removal"

    latest = "Latest human correction: preserve current tool output."
    tool_output = "CURRENT_TOOL_RESULT " + "z" * 500
    await context.add_message(human(latest))
    await context.add_message(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "current_call", "type": "function", "function": {"name": "read"}}
            ],
        }
    )
    await context.add_message(
        {"role": "tool", "tool_call_id": "current_call", "content": tool_output}
    )

    view = await context.get_messages_for_request_retaining(
        retain_contents=[required], token_budget=1500, hard_fit=True
    )
    contents = {message.get("content") for message in view}
    assert {first, latest, required, tool_output}.issubset(contents)
    assert not removed_contents & contents
    assert context._request_hard_fit is False


@pytest.mark.asyncio
async def test_hard_fit_with_empty_retention_targets_the_full_forced_budget():
    """An empty requirement set must not silently fall back to the half target."""
    context, _ = await pressured_context()
    forced_budget = 1500

    await context.get_messages_for_request_retaining(
        retain_contents=[],
        token_budget=forced_budget,
        hard_fit=True,
    )

    assert context._last_compaction_stats is not None
    assert context._last_compaction_stats["target_tokens"] == forced_budget
    assert context._request_hard_fit is False


@pytest.mark.asyncio
async def test_hard_fit_protects_appended_human_reminder_and_tool_result():
    """A provider-directed fit keeps current protected growth and old reductions."""
    context = SimpleContextManager(
        max_tokens=1800,
        protected_tool_results=1,
        compaction_notice_enabled=False,
    )
    first = "Original human task: retain this complete prompt."
    required = "<system-reminders>" + "active policy " * 150 + "</system-reminders>"
    forced_budget = 1500
    await context.add_message(human(first))
    await context.add_message(reminder(required))
    for i in range(30):
        await context.add_message(
            {"role": "assistant", "content": f"Historical bulk {i}: " + "x" * 900}
        )

    await context.get_messages_for_request_retaining(
        retain_contents=[required],
        token_budget=forced_budget,
        hard_fit=True,
    )
    removed_contents = {
        message["content"]
        for message in await context.get_messages()
        if message["metadata"]["_seq"] in context._removed_seqs
    }
    assert removed_contents, "Setup must record an old sticky removal"

    latest = "Latest human correction: preserve current tool output."
    tool_output = "CURRENT_TOOL_RESULT " + "z" * 500
    await context.add_message(human(latest))
    await context.add_message(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "current_call", "type": "function", "function": {"name": "read"}}
            ],
        }
    )
    await context.add_message(
        {"role": "tool", "tool_call_id": "current_call", "content": tool_output}
    )

    view = await context.get_messages_for_request_retaining(
        retain_contents=[required],
        token_budget=forced_budget,
        hard_fit=True,
    )
    contents = {message.get("content") for message in view}
    assert {first, latest, required, tool_output}.issubset(contents)
    assert not removed_contents & contents
    assert context._last_compaction_stats is not None
    assert context._last_compaction_stats["target_tokens"] == forced_budget
    assert context._request_hard_fit is False


@pytest.mark.asyncio
async def test_mount_advertises_additive_capability_and_keeps_old_getter():
    class Coordinator:
        def __init__(self):
            self.capabilities = {}

        async def mount(self, name, module):
            self.context = module

        def register_capability(self, name, capability):
            self.capabilities[name] = capability

    coordinator = Coordinator()
    cleanup = await mount(coordinator)
    assert callable(coordinator.capabilities["context.request_retention"])
    assert await coordinator.context.get_messages_for_request() == []
    assert (
        await coordinator.capabilities["context.request_retention"](retain_contents=[])
        == []
    )
    await cleanup()


@pytest.mark.asyncio
async def test_stale_actual_meter_cannot_prevent_feasible_retention_compaction():
    context, body = await pressured_context()
    context.token_meter = "actual"
    context._last_measured_prompt_tokens = 100
    view = await context.get_messages_for_request_retaining(retain_contents=[body])
    assert any(m["content"] == body for m in view)
    assert context._estimate_tokens(view) <= context.max_tokens


@pytest.mark.asyncio
async def test_changed_then_withdrawn_requirements_do_not_leave_a_stale_pin():
    context = SimpleContextManager(max_tokens=1800, compaction_notice_enabled=False)
    original = "<system-reminders>" + "original policy " * 180 + "</system-reminders>"
    replacement = (
        "<system-reminders>" + "replacement policy " * 180 + "</system-reminders>"
    )
    await context.add_message(human("First human request: preserve current requirements."))
    await context.add_message(reminder(original))
    for i in range(40):
        await context.add_message(
            {"role": "assistant", "content": f"Earlier work {i}: " + "x" * 1200}
        )
    await context.add_message(human("Latest human correction: use the replacement."))
    await context.add_message(reminder(replacement))
    for i in range(30):
        await context.add_message(
            {"role": "assistant", "content": f"Later work {i}: " + "z" * 500}
        )

    changed_view = await context.get_messages_for_request_retaining(
        retain_contents=[replacement]
    )
    assert sum(m["content"] == original for m in changed_view) == 0
    assert sum(m["content"] == replacement for m in changed_view) == 1

    # A new, empty requirement set must permit the formerly selected envelope
    # to age out on a real subsequent compaction.
    for i in range(30):
        await context.add_message(
            {"role": "assistant", "content": f"Even later work {i}: " + "z" * 500}
        )
    withdrawn_view = await context.get_messages_for_request_retaining(
        retain_contents=[]
    )
    assert not any(m["content"] == original for m in withdrawn_view)
    assert not any(m["content"] == replacement for m in withdrawn_view)


@pytest.mark.asyncio
async def test_level_5_retains_complete_current_reminder_and_human_boundaries():
    """A real Level 5 pass keeps the selected envelope and both human bodies."""
    context = SimpleContextManager(
        max_tokens=1800,
        compact_threshold=0.92,
        target_usage=0.50,
        protected_recent=0.30,
        compaction_notice_enabled=False,
    )
    first = human("First human request: preserve the complete acceptance criteria.")
    active_reminder = reminder(
        "<system-reminders>"
        "Keep the current deployment region unchanged. "
        "Do not use an unpublished credential. "
        "Return the complete migration plan."
        "</system-reminders>"
    )
    latest = human("Latest human correction: the migration must remain reversible.")
    await context.add_message(first)
    await context.add_message(active_reminder)
    for i in range(14):
        await context.add_message(
            {"role": "assistant", "content": f"Machine output {i}: " + "x" * 800}
        )
    await context.add_message(latest)

    canonical = copy.deepcopy(await context.get_messages())
    first_envelope, reminder_envelope, *_, latest_envelope = map(
        public_envelope, canonical
    )
    view = await context.get_messages_for_request_retaining(
        retain_contents=[active_reminder["content"]]
    )

    assert context._last_compaction_stats is not None
    assert context._last_compaction_stats["strategy_level"] == 5
    assert first_envelope in view
    assert reminder_envelope in view
    assert latest_envelope in view
    assert await context.get_messages() == canonical


@pytest.mark.asyncio
async def test_level_8_keeps_quoted_xml_human_prompt_and_reminder_complete():
    """A quoted system-reminder is human content, not injected provenance."""
    context = SimpleContextManager(
        max_tokens=1800,
        compact_threshold=0.92,
        target_usage=0.50,
        protected_recent=0.30,
        compaction_notice_enabled=False,
    )
    first = human(
        "<system-reminders>"
        "A human quoted this XML while asking whether it was safe to publish."
        "</system-reminders>"
    )
    active_reminder = reminder(
        "<system-reminders>"
        + "Active retention policy. " * 130
        + "</system-reminders>"
    )
    latest = human("Latest human correction: do not remove the quoted evidence.")
    await context.add_message(first)
    await context.add_message(active_reminder)
    for i in range(8):
        await context.add_message(
            {"role": "assistant", "content": f"Machine output {i}: " + "x" * 800}
        )
    await context.add_message(latest)

    canonical = await context.get_messages()
    first_envelope, reminder_envelope, *_, latest_envelope = map(
        public_envelope, canonical
    )
    view = await context.get_messages_for_request_retaining(
        retain_contents=[active_reminder["content"]]
    )

    assert context._last_compaction_stats is not None
    assert context._last_compaction_stats["strategy_level"] == 8
    assert first_envelope in view
    assert reminder_envelope in view
    assert latest_envelope in view
    assert context._last_compaction_stats["after_tokens"] > context._last_compaction_stats[
        "target_tokens"
    ]
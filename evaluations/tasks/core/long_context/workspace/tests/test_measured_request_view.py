"""Focused contract tests for Context's provider-count request-view capability."""

import asyncio

import pytest
from amplifier_core.llm_errors import ContextLengthError

from amplifier_module_context_simple import SimpleContextManager, mount


class _Coordinator:
    def __init__(self):
        self.capabilities = {}

    async def mount(self, kind, instance):
        self.context = instance

    def register_capability(self, name, capability):
        self.capabilities[name] = capability


def _decision(count, *, estimated=None, limit=1_000):
    return {
        "estimated_input_tokens": count if estimated is None else estimated,
        "input_limit_tokens": limit,
        "measurement": {
            "kind": "provider_count",
            "source": "test.provider.count",
            "input_tokens": count,
        },
    }


async def _staged_transaction(*, hooks=None):
    """Create a measured candidate whose staged state needs a transaction."""
    context = SimpleContextManager(
        max_tokens=1_000,
        compact_threshold=0.8,
        target_usage=0.5,
        protected_tool_results=0,
        compaction_notice_enabled=False,
        token_meter="actual",
        hooks=hooks,
    )
    await context.add_message({"role": "user", "content": "first"})
    for _ in range(4):
        await context.add_message({"role": "tool", "content": "x" * 800})
    await context.add_message({"role": "user", "content": "last"})

    async def count_view(view):
        count = 400 if any(message.get("_truncated") for message in view) else 900
        return {"dispatch": object(), "budget_decision": _decision(count)}

    result = await context.get_measured_request_view(
        provider=None, retain_contents=[], count_view=count_view
    )
    assert result["transaction"] is not None
    assert context._truncated_seqs
    return context, result["transaction"]


@pytest.mark.asyncio
async def test_measured_capability_is_actual_mode_only_and_foreground_is_always_additive():
    estimate = _Coordinator()
    await mount(estimate, {"token_meter": "estimate"})
    assert "context.measured_request_view" not in estimate.capabilities
    assert callable(estimate.capabilities["context.foreground_usage"])

    actual = _Coordinator()
    await mount(actual, {"token_meter": "actual"})
    assert callable(actual.capabilities["context.measured_request_view"])


@pytest.mark.asyncio
async def test_provider_count_controls_trigger_target_and_returns_exact_final_envelope():
    context = SimpleContextManager(
        max_tokens=1_000,
        compact_threshold=0.8,
        target_usage=0.5,
        protected_tool_results=0,
        compaction_notice_enabled=False,
        token_meter="actual",
    )
    await context.add_message({"role": "user", "content": "first human boundary"})
    for index in range(4):
        await context.add_message(
            {"role": "tool", "tool_call_id": str(index), "content": "x" * 800}
        )
    await context.add_message({"role": "user", "content": "latest human boundary"})

    calls = []

    async def count_view(view):
        calls.append(view)
        # chars/4 remains very different from this native count.  The first
        # native count triggers, and one legal truncation rung reaches target.
        count = 900 if not any(message.get("_truncated") for message in view) else 400
        return {"dispatch": object(), "budget_decision": _decision(count)}

    result = await context.get_measured_request_view(
        provider=None, retain_contents=[], count_view=count_view
    )

    assert result["outcome"] == "target_reached"
    assert result["measured_before"] == 900
    assert result["measured_after"] == 400
    assert result["trigger"] == 800
    assert result["target"] == 500
    assert result["count_calls"] == 2
    assert result["final_attempt"]["dispatch"] is not None
    assert result["base_view"] is calls[-1]
    assert result["transaction"] is not None
    assert context._last_compaction_stats["measurement_source"] == "test.provider.count"
    await result["transaction"].commit()


@pytest.mark.asyncio
async def test_protected_floor_with_no_legal_change_has_one_count_and_no_sticky_event_state():
    context = SimpleContextManager(
        max_tokens=1_000,
        compact_threshold=0.8,
        target_usage=0.5,
        compaction_notice_enabled=False,
        token_meter="actual",
    )
    await context.add_message({"role": "user", "content": "only protected human prompt"})
    calls = 0

    async def count_view(view):
        nonlocal calls
        calls += 1
        return {"dispatch": object(), "budget_decision": _decision(900)}

    result = await context.get_measured_request_view(
        provider=None, retain_contents=[], count_view=count_view
    )

    assert result["outcome"] == "protected_floor"
    assert result["count_calls"] == calls == 1
    assert result["transaction"] is None
    assert context._last_compaction_stats is None


@pytest.mark.asyncio
async def test_unavailable_recount_rolls_back_to_original_counted_view():
    context = SimpleContextManager(
        max_tokens=1_000,
        compact_threshold=0.8,
        target_usage=0.5,
        protected_tool_results=0,
        compaction_notice_enabled=False,
        token_meter="actual",
    )
    await context.add_message({"role": "user", "content": "first"})
    for index in range(4):
        await context.add_message({"role": "tool", "content": "x" * 800, "name": str(index)})
    await context.add_message({"role": "user", "content": "last"})
    views = []

    async def count_view(view):
        views.append(view)
        decision = _decision(900) if len(views) == 1 else None
        return {"dispatch": object(), "budget_decision": decision}

    result = await context.get_measured_request_view(
        provider=None, retain_contents=[], count_view=count_view
    )

    assert result["outcome"] == "measurement_unavailable"
    assert result["base_view"] is views[0]
    assert result["count_calls"] == 2
    assert not context._truncated_seqs
    assert context._last_compaction_stats is None


@pytest.mark.asyncio
async def test_initial_unavailable_uses_one_snapshot_legacy_fallback_and_recounts_it():
    context = SimpleContextManager(
        max_tokens=1_000,
        compact_threshold=0.1,
        protected_tool_results=0,
        compaction_notice_enabled=False,
        token_meter="actual",
    )
    await context.add_message({"role": "user", "content": "first"})
    for _ in range(4):
        await context.add_message({"role": "tool", "content": "x" * 800})
    await context.add_message({"role": "user", "content": "last"})
    factory_calls = 0

    async def factory():
        nonlocal factory_calls
        factory_calls += 1
        return "dynamic system"

    await context.set_system_prompt_factory(factory)
    calls = []

    async def count_view(view):
        calls.append(view)
        return {"dispatch": object(), "budget_decision": None}

    result = await context.get_measured_request_view(
        provider=None, retain_contents=[], count_view=count_view
    )

    assert result["outcome"] == "measurement_unavailable"
    assert result["count_calls"] == len(calls) == 2
    assert factory_calls == 1
    assert result["transaction"] is not None
    result["transaction"].rollback()
    assert not context._truncated_seqs


@pytest.mark.asyncio
async def test_unavailable_recount_of_known_hard_oversize_fails_closed():
    context = SimpleContextManager(
        max_tokens=1_000,
        compact_threshold=0.8,
        protected_tool_results=0,
        compaction_notice_enabled=False,
        token_meter="actual",
    )
    await context.add_message({"role": "user", "content": "first"})
    for _ in range(4):
        await context.add_message({"role": "tool", "content": "x" * 800})
    await context.add_message({"role": "user", "content": "last"})
    calls = 0

    async def count_view(_view):
        nonlocal calls
        calls += 1
        return {
            "dispatch": object(),
            "budget_decision": _decision(900, estimated=1_001, limit=1_000)
            if calls == 1
            else None,
        }

    with pytest.raises(ContextLengthError, match="recount became unavailable"):
        await context.get_measured_request_view(
            provider=None, retain_contents=[], count_view=count_view
        )
    assert not context._truncated_seqs


@pytest.mark.asyncio
async def test_cancellation_from_count_callback_propagates_without_sticky_state():
    context = SimpleContextManager(max_tokens=1_000, token_meter="actual")
    await context.add_message({"role": "user", "content": "prompt"})

    async def cancelled_count(_view):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await context.get_measured_request_view(
            provider=None, retain_contents=[], count_view=cancelled_count
        )
    assert not context._removed_seqs

    assert not context._truncated_seqs


@pytest.mark.asyncio
async def test_cancellation_during_commit_leaves_staged_decisions_rollbackable():
    started = asyncio.Event()
    release = asyncio.Event()

    class Hooks:
        async def emit(self, _event, _data):
            started.set()
            await release.wait()

    context = SimpleContextManager(
        max_tokens=1_000,
        compact_threshold=0.8,
        target_usage=0.5,
        protected_tool_results=0,
        compaction_notice_enabled=False,
        token_meter="actual",
        hooks=Hooks(),
    )
    await context.add_message({"role": "user", "content": "first"})
    for _ in range(4):
        await context.add_message({"role": "tool", "content": "x" * 800})
    await context.add_message({"role": "user", "content": "last"})

    async def count_view(view):
        count = 400 if any(message.get("_truncated") for message in view) else 900
        return {"dispatch": object(), "budget_decision": _decision(count)}

    result = await context.get_measured_request_view(
        provider=None, retain_contents=[], count_view=count_view
    )
    task = asyncio.create_task(result["transaction"].commit())
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release.set()
    result["transaction"].rollback()
    assert not context._truncated_seqs
    assert context._last_compaction_stats is None


@pytest.mark.asyncio
async def test_commit_veto_after_awaited_event_restores_the_snapshot():
    started = asyncio.Event()
    release = asyncio.Event()
    cancelled = False

    class Hooks:
        async def emit(self, _event, _data):
            started.set()
            await release.wait()

    context, transaction = await _staged_transaction(hooks=Hooks())
    task = asyncio.create_task(
        transaction.commit(is_cancelled=lambda: cancelled)
    )
    await asyncio.wait_for(started.wait(), timeout=1)
    cancelled = True
    release.set()

    assert await task is False
    assert not context._truncated_seqs
    assert context._last_compaction_stats is None


@pytest.mark.asyncio
async def test_concurrent_rollback_during_event_commit_wins():
    started = asyncio.Event()
    release = asyncio.Event()

    class Hooks:
        async def emit(self, _event, _data):
            started.set()
            await release.wait()

    context, transaction = await _staged_transaction(hooks=Hooks())
    task = asyncio.create_task(transaction.commit())
    await asyncio.wait_for(started.wait(), timeout=1)
    transaction.rollback()
    release.set()

    assert await task is False
    assert not context._truncated_seqs
    assert context._last_compaction_stats is None


@pytest.mark.asyncio
async def test_transaction_terminal_commit_and_rollback_results_are_idempotent():
    committed_context, committed = await _staged_transaction()
    assert await committed.commit() is True
    committed.rollback()
    assert await committed.commit() is True
    assert committed_context._truncated_seqs

    rolled_back_context, rolled_back = await _staged_transaction()
    rolled_back.rollback()
    rolled_back.rollback()
    assert await rolled_back.commit() is False
    assert not rolled_back_context._truncated_seqs
    assert rolled_back_context._last_compaction_stats is None


@pytest.mark.asyncio
async def test_commit_with_default_cancellation_predicate_accepts_candidate():
    context, transaction = await _staged_transaction()

    assert await transaction.commit(is_cancelled=None) is True
    assert context._truncated_seqs


@pytest.mark.asyncio
async def test_commit_predicate_error_propagates_while_snapshot_is_rollbackable():
    context, transaction = await _staged_transaction()

    def predicate_error():
        raise TypeError("predicate failed")

    with pytest.raises(TypeError, match="predicate failed"):
        await transaction.commit(is_cancelled=predicate_error)

    transaction.rollback()
    assert not context._truncated_seqs
    assert context._last_compaction_stats is None


@pytest.mark.asyncio
async def test_foreground_claim_blocks_generic_hook_after_owned_recording():
    context = SimpleContextManager(token_meter="actual")
    await context._on_llm_response("llm:response", {"usage": {"input_tokens": 17}})
    recorder = context.claim_foreground_usage()
    assert context._last_measured_prompt_tokens is None
    assert recorder(input_tokens=20, cache_write_tokens=3)
    await context._on_llm_response("llm:response", {"usage": {"input_tokens": 999}})
    assert context._last_measured_prompt_tokens == 23


@pytest.mark.asyncio
async def test_foreground_recorder_rejects_malformed_usage_without_replacing_owned_reading():
    context = SimpleContextManager()
    recorder = context.claim_foreground_usage()
    assert recorder(input_tokens=4)
    assert not recorder(input_tokens=True)
    assert context._last_measured_prompt_tokens == 4
    assert context._foreground_usage_stale is True


@pytest.mark.asyncio
async def test_owned_stale_reading_is_labeled_not_fresh_measured_usage():
    context = SimpleContextManager(token_meter="actual", max_tokens=10_000)
    recorder = context.claim_foreground_usage()
    assert recorder(input_tokens=4)
    assert not recorder(input_tokens=-1)
    await context.add_message({"role": "user", "content": "prompt"})
    await context.get_messages_for_request()
    assert context._last_token_meter_stats["source"] == "owned_stale"
    assert context._last_token_meter_stats["foreground_usage_stale"] is True


@pytest.mark.asyncio
async def test_unowned_generic_hook_keeps_legacy_meter_behavior():
    context = SimpleContextManager()
    await context._on_llm_response("llm:response", {"usage": {"input_tokens": 12}})
    assert context._last_measured_prompt_tokens == 12


@pytest.mark.asyncio
async def test_unowned_hook_rejects_malformed_cache_write_without_crashing():
    context = SimpleContextManager()
    await context._on_llm_response("llm:response", {"usage": {"input_tokens": 12}})
    await context._on_llm_response(
        "llm:response", {"usage": {"input_tokens": 99, "cache_write_tokens": "bad"}}
    )
    assert context._last_measured_prompt_tokens == 12


@pytest.mark.asyncio
async def test_measured_view_keeps_developer_instructions_at_protected_floor():
    context = SimpleContextManager(
        max_tokens=1_000,
        compact_threshold=0.8,
        target_usage=0.5,
        protected_recent=0,
        protected_tool_results=0,
        compaction_notice_enabled=False,
        token_meter="actual",
    )
    developer = "developer instruction " * 200
    await context.add_message({"role": "developer", "content": developer})
    await context.add_message({"role": "user", "content": "first human"})
    await context.add_message({"role": "assistant", "content": "old removable reply"})
    await context.add_message({"role": "user", "content": "latest human"})
    views = []

    async def count_view(view):
        views.append(view)
        # The count derives from the instruction's presence: removing it would
        # falsely make the candidate appear to reach target.
        has_full_developer = any(
            message.get("role") == "developer"
            and message.get("content") == developer
            for message in view
        )
        return {
            "dispatch": object(),
            "budget_decision": _decision(900 if has_full_developer else 400),
        }

    result = await context.get_measured_request_view(
        provider=None, retain_contents=[], count_view=count_view
    )

    assert result["outcome"] == "protected_floor"
    assert result["measured_before"] == result["measured_after"] == 900
    assert result["count_calls"] == len(views) == 2
    assert all(
        any(
            message.get("role") == "developer"
            and message.get("content") == developer
            for message in view
        )
        for view in views
    )
    assert (await context.get_messages())[0]["content"] == developer
    result["transaction"].rollback()


@pytest.mark.asyncio
async def test_initial_unavailable_legacy_fallback_keeps_developer_instructions():
    context = SimpleContextManager(
        max_tokens=1_000,
        compact_threshold=0.1,
        protected_recent=0,
        protected_tool_results=0,
        compaction_notice_enabled=False,
        token_meter="actual",
    )
    developer = "developer instruction " * 200
    await context.add_message({"role": "developer", "content": developer})
    await context.add_message({"role": "user", "content": "first human"})
    await context.add_message({"role": "assistant", "content": "old removable reply"})
    await context.add_message({"role": "user", "content": "latest human"})
    views = []

    async def unavailable_count(view):
        views.append(view)
        return {"dispatch": object(), "budget_decision": None}

    result = await context.get_measured_request_view(
        provider=None, retain_contents=[], count_view=unavailable_count
    )

    assert result["outcome"] == "measurement_unavailable"
    assert result["count_calls"] == len(views) == 2
    assert any(
        message.get("role") == "developer" and message.get("content") == developer
        for message in result["base_view"]
    )
    assert (await context.get_messages())[0]["content"] == developer
    result["transaction"].rollback()
    assert not context._removed_seqs


@pytest.mark.asyncio
async def test_developer_protection_is_scoped_to_the_measured_capability():
    context = SimpleContextManager(
        max_tokens=1_000,
        compact_threshold=0.1,
        protected_recent=0,
        compaction_notice_enabled=False,
        token_meter="actual",
    )
    developer = "developer instruction " * 200
    await context.add_message({"role": "developer", "content": developer})
    await context.add_message({"role": "user", "content": "first human"})
    await context.add_message({"role": "user", "content": "latest human"})

    async def count_view(_view):
        return {"dispatch": object(), "budget_decision": _decision(0)}

    result = await context.get_measured_request_view(
        provider=None, retain_contents=[], count_view=count_view
    )
    assert result["outcome"] == "not_needed"

    legacy_view = await context.get_messages_for_request(1_000)
    assert all(message.get("role") != "developer" for message in legacy_view)


@pytest.mark.asyncio
async def test_no_op_early_rungs_continue_to_a_later_legal_reduction():
    context = SimpleContextManager(
        max_tokens=1_000,
        compact_threshold=0.8,
        target_usage=0.5,
        protected_tool_results=0,
        compaction_notice_enabled=False,
        token_meter="actual",
    )
    await context.add_message({"role": "user", "content": "first"})
    for index in range(3):
        await context.add_message({"role": "tool", "content": "x" * 800, "name": str(index)})
    await context.add_message({"role": "user", "content": "last"})
    calls = 0

    async def count_view(_view):
        nonlocal calls
        calls += 1
        return {
            "dispatch": object(),
            "budget_decision": _decision(900 if calls == 1 else 400),
        }

    result = await context.get_measured_request_view(
        provider=None, retain_contents=[], count_view=count_view
    )

    # Three tool results leave rung one empty; rung two must still be reached.
    assert result["outcome"] == "target_reached"
    assert result["count_calls"] == calls == 2
    assert context._truncated_seqs
    result["transaction"].rollback()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("estimated_input_tokens", True),
        ("estimated_input_tokens", -1),
        ("estimated_input_tokens", None),
        ("input_limit_tokens", True),
        ("input_limit_tokens", -1),
        ("input_limit_tokens", None),
    ],
)
async def test_invalid_old_hard_safety_fields_are_contract_errors(field, value):
    context = SimpleContextManager(max_tokens=1_000, token_meter="actual")

    async def count_view(_view):
        decision = _decision(10)
        if value is None:
            decision.pop(field)
        else:
            decision[field] = value
        return {"dispatch": object(), "budget_decision": decision}

    with pytest.raises(TypeError, match="estimated_input_tokens and input_limit_tokens"):
        await context.get_measured_request_view(
            provider=None, retain_contents=[], count_view=count_view
        )


@pytest.mark.asyncio
async def test_soft_safe_malformed_measurement_remains_unavailable():
    context = SimpleContextManager(max_tokens=1_000, token_meter="actual")

    async def count_view(_view):
        return {
            "dispatch": object(),
            "budget_decision": {
                "estimated_input_tokens": 1_000,
                "input_limit_tokens": 1_000,
                "measurement": {"kind": "wrong", "source": "test.provider.count"},
            },
        }

    result = await context.get_measured_request_view(
        provider=None, retain_contents=[], count_view=count_view
    )

    assert result["outcome"] == "measurement_unavailable"
    assert result["count_calls"] == 1
    assert result["transaction"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "measurement",
    [
        None,
        {"kind": "wrong", "source": "test.provider.count", "input_tokens": 1},
    ],
)
async def test_initial_unavailable_measurement_cannot_bypass_known_hard_oversize(
    measurement,
):
    context = SimpleContextManager(max_tokens=1_000, token_meter="actual")
    calls = 0

    async def count_view(_view):
        nonlocal calls
        calls += 1
        return {
            "dispatch": object(),
            "budget_decision": {
                "estimated_input_tokens": 1_001,
                "input_limit_tokens": 1_000,
                "measurement": measurement,
            },
        }

    with pytest.raises(ContextLengthError, match="cannot fit protected content"):
        await context.get_measured_request_view(
            provider=None, retain_contents=[], count_view=count_view
        )
    assert calls == 1


@pytest.mark.asyncio
async def test_fallback_unavailable_measurement_cannot_bypass_known_hard_oversize():
    context = SimpleContextManager(
        max_tokens=1_000,
        compact_threshold=0.1,
        protected_tool_results=0,
        compaction_notice_enabled=False,
        token_meter="actual",
    )
    await context.add_message({"role": "user", "content": "first"})
    for _ in range(4):
        await context.add_message({"role": "tool", "content": "x" * 800})
    await context.add_message({"role": "user", "content": "last"})
    calls = 0

    async def count_view(_view):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"dispatch": object(), "budget_decision": None}
        return {
            "dispatch": object(),
            "budget_decision": {
                "estimated_input_tokens": 1_001,
                "input_limit_tokens": 1_000,
                "measurement": None,
            },
        }

    with pytest.raises(ContextLengthError, match="cannot fit protected content"):
        await context.get_measured_request_view(
            provider=None, retain_contents=[], count_view=count_view
        )
    assert calls == 2
    assert not context._truncated_seqs
    assert not context._removed_seqs


@pytest.mark.asyncio
async def test_late_known_hard_oversize_fails_closed_when_next_recount_is_unavailable():
    context = SimpleContextManager(
        max_tokens=1_000,
        compact_threshold=0.8,
        target_usage=0.5,
        protected_tool_results=0,
        compaction_notice_enabled=False,
        token_meter="actual",
    )
    await context.add_message({"role": "user", "content": "first"})
    for _ in range(4):
        await context.add_message({"role": "tool", "content": "x" * 800})
    await context.add_message({"role": "user", "content": "last"})
    calls = 0

    async def count_view(_view):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "dispatch": object(),
                "budget_decision": _decision(900, estimated=999),
            }
        if calls == 2:
            return {
                "dispatch": object(),
                "budget_decision": _decision(800, estimated=1_001),
            }
        return {"dispatch": object(), "budget_decision": None}

    with pytest.raises(ContextLengthError, match="recount became unavailable"):
        await context.get_measured_request_view(
            provider=None, retain_contents=[], count_view=count_view
        )
    assert calls == 3
    assert not context._truncated_seqs
    assert not context._removed_seqs


async def _hard_oversize_context(*, reducible=False):
    context = SimpleContextManager(
        max_tokens=1_000,
        compact_threshold=0.8,
        target_usage=0.5,
        protected_tool_results=0,
        compaction_notice_enabled=False,
        token_meter="actual",
    )
    await context.add_message({"role": "user", "content": "first"})
    if reducible:
        for index in range(4):
            await context.add_message(
                {"role": "tool", "content": "x" * 800, "name": str(index)}
            )
        await context.add_message({"role": "user", "content": "last"})
    return context


@pytest.mark.asyncio
async def test_output_fit_is_opt_in_and_preserves_existing_terminal_failure():
    context = await _hard_oversize_context()
    calls = 0

    async def count_view(_view):
        nonlocal calls
        calls += 1
        return {
            "dispatch": object(),
            "budget_decision": _decision(900, estimated=1_001, limit=1_000),
        }

    with pytest.raises(ContextLengthError, match="cannot fit protected content"):
        await context.get_measured_request_view(
            provider=None, retain_contents=[], count_view=count_view
        )

    assert calls == 1
    assert context._last_compaction_stats is None


@pytest.mark.asyncio
async def test_output_fit_returns_exact_fitted_attempt_without_staging_noop_context():
    context = await _hard_oversize_context()
    canonical = await context.get_messages()
    old_stats = {"old": "stats"}
    context._last_compaction_stats = old_stats
    counted_attempt = {
        "dispatch": {"output_cap": 64_000},
        "budget_decision": _decision(900, estimated=1_001, limit=1_000),
    }
    fitted_attempt = {
        "dispatch": {"output_cap": 32_000},
        "budget_decision": _decision(900, estimated=900, limit=1_000),
        "count_calls": 2,
    }
    received = {}

    async def count_view(_view):
        return counted_attempt

    async def fit_output(view, attempt):
        received["view"] = view
        received["attempt"] = attempt
        return fitted_attempt

    result = await context.get_measured_request_view(
        provider=None,
        retain_contents=[],
        count_view=count_view,
        fit_output=fit_output,
    )

    assert result["outcome"] == "reduced_output"
    assert result["base_view"] is received["view"]
    assert received["attempt"] is counted_attempt
    assert result["final_attempt"] is fitted_attempt
    assert result["measured_before"] == result["measured_after"] == 900
    assert result["count_calls"] == 3
    assert result["transaction"] is None
    assert await context.get_messages() == canonical
    assert context._last_compaction_stats is old_stats


@pytest.mark.asyncio
async def test_output_fit_none_rolls_back_and_keeps_terminal_failure():
    context = await _hard_oversize_context()

    async def count_view(_view):
        return {
            "dispatch": object(),
            "budget_decision": _decision(900, estimated=1_001, limit=1_000),
        }

    async def fit_output(_view, _attempt):
        return None

    with pytest.raises(ContextLengthError, match="cannot fit protected content"):
        await context.get_measured_request_view(
            provider=None,
            retain_contents=[],
            count_view=count_view,
            fit_output=fit_output,
        )

    assert context._last_compaction_stats is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fitted", "error"),
    [
        (
            {
                "budget_decision": _decision(900, estimated=900, limit=1_000),
                "count_calls": 1,
            },
            TypeError,
        ),
        (
            {
                "dispatch": object(),
                "budget_decision": _decision(900, estimated=900, limit=1_000),
                "count_calls": True,
            },
            TypeError,
        ),
        ({"dispatch": object(), "budget_decision": None, "count_calls": 1}, TypeError),
        (
            {
                "dispatch": object(),
                "budget_decision": _decision(900, estimated=1_001, limit=1_000),
                "count_calls": 1,
            },
            ContextLengthError,
        ),
    ],
)
async def test_output_fit_rejects_invalid_or_still_oversized_results(fitted, error):
    context = await _hard_oversize_context()

    async def count_view(_view):
        return {
            "dispatch": object(),
            "budget_decision": _decision(900, estimated=1_001, limit=1_000),
        }

    async def fit_output(_view, _attempt):
        return fitted

    with pytest.raises(error):
        await context.get_measured_request_view(
            provider=None,
            retain_contents=[],
            count_view=count_view,
            fit_output=fit_output,
        )

    assert context._last_compaction_stats is None


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [RuntimeError("fit failed"), asyncio.CancelledError()])
async def test_output_fit_errors_and_cancellation_rollback_staged_compaction(error):
    context = await _hard_oversize_context(reducible=True)

    async def count_view(_view):
        return {
            "dispatch": object(),
            "budget_decision": _decision(900, estimated=1_001, limit=1_000),
        }

    async def fit_output(_view, _attempt):
        raise error

    with pytest.raises(type(error)):
        await context.get_measured_request_view(
            provider=None,
            retain_contents=[],
            count_view=count_view,
            fit_output=fit_output,
        )

    assert not context._removed_seqs
    assert not context._truncated_seqs
    assert context._last_compaction_stats is None


@pytest.mark.asyncio
async def test_output_fit_stages_changed_compaction_until_rollback_or_commit():
    async def build_candidate():
        context = await _hard_oversize_context(reducible=True)
        counted_views = []
        raw_attempts = []
        fit_attempt = {
            "dispatch": {"output_cap": 32_000},
            "budget_decision": _decision(900, estimated=900, limit=1_000),
            "count_calls": 2,
        }

        async def count_view(view):
            counted_views.append(view)
            attempt = {
                "dispatch": {"output_cap": 64_000},
                "budget_decision": _decision(900, estimated=1_001, limit=1_000),
            }
            raw_attempts.append(attempt)
            return attempt

        async def fit_output(view, attempt):
            assert view is counted_views[-1]
            assert attempt is raw_attempts[-1]
            return fit_attempt

        result = await context.get_measured_request_view(
            provider=None,
            retain_contents=[],
            count_view=count_view,
            fit_output=fit_output,
        )
        assert result["outcome"] == "reduced_output"
        assert result["final_attempt"] is fit_attempt
        assert result["count_calls"] == len(counted_views) + 2
        assert result["transaction"] is not None
        assert context._last_compaction_stats["outcome"] == "reduced_output"
        assert (
            context._last_compaction_stats["count_calls"] == result["count_calls"]
        )
        return context, result

    rolled_back_context, rolled_back = await build_candidate()
    rolled_back["transaction"].rollback()
    assert not rolled_back_context._removed_seqs
    assert not rolled_back_context._truncated_seqs
    assert rolled_back_context._last_compaction_stats is None

    committed_context, committed = await build_candidate()
    assert await committed["transaction"].commit()
    assert committed_context._last_compaction_stats["outcome"] == "reduced_output"
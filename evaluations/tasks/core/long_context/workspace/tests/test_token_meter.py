"""
Real-usage token meter tests (`token_meter: "estimate" | "actual"`).

Ported from amplifier-module-context-handoff's proven `_on_llm_response`
meter (see that module's tests/test_reserve_trigger.py for the pattern this
follows) and adapted to context-simple's ephemeral-compaction trigger.

Coverage:
  - the meter accumulates real usage from synthetic `llm:response` events
  - default mode ("estimate") is byte-identical regardless of whether real
    measurements are flowing -- the core "zero behavior change" guarantee
  - "actual" mode drives the compaction trigger from real usage, both when
    it agrees AND disagrees with the estimator (in both directions)
  - "actual" mode falls back to the estimator before any measurement exists
  - a malformed/missing usage payload never crashes the meter
  - an invalid token_meter config value degrades to "estimate" (never raises)
  - clear() resets meter state
  - mount() registers/unregisters the llm:response hook and threads
    token_meter through to the mounted manager
"""

import logging

import pytest
from amplifier_module_context_simple import SimpleContextManager, mount


class _FakeHooks:
    """Minimal stand-in for amplifier_core.hooks.HookRegistry -- just enough
    of register()/emit() to prove mount() wires the meter up correctly,
    without depending on amplifier_core's real HookRegistry internals."""

    def __init__(self):
        self.registered: list[dict] = []

    def register(self, event, handler, priority=0, name=None):
        entry = {"event": event, "handler": handler, "priority": priority, "name": name}
        self.registered.append(entry)

        def unregister():
            self.registered.remove(entry)

        return unregister


class _FakeCoordinator:
    """Minimal stand-in for amplifier_core.ModuleCoordinator -- just enough
    for mount() to run: a `hooks` attribute and an async `mount()`."""

    def __init__(self, hooks=None):
        self.hooks = hooks
        self.mounted: dict[str, object] = {}

    async def mount(self, kind: str, instance: object) -> None:
        self.mounted[kind] = instance


# ---------------------------------------------------------------------------
# _on_llm_response: meter accumulation from synthetic events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_llm_response_records_input_plus_cache_write():
    """The meter must sum input_tokens + cache_write_tokens -- mirrors
    context-handoff's exact formula (see that module's README "Live
    demonstration" for the production incident this formula fixes)."""
    context = SimpleContextManager()

    await context._on_llm_response(
        "llm:response",
        {"usage": {"input_tokens": 2, "cache_write_tokens": 161_165, "output_tokens": 9}},
    )

    assert context._last_measured_prompt_tokens == 2 + 161_165


@pytest.mark.asyncio
async def test_on_llm_response_without_cache_write_field_still_works():
    """Providers/events that omit cache_write_tokens entirely (e.g. no
    caching in play) must not crash and must fall back to input_tokens alone."""
    context = SimpleContextManager()

    await context._on_llm_response("llm:response", {"usage": {"input_tokens": 500}})

    assert context._last_measured_prompt_tokens == 500


@pytest.mark.asyncio
async def test_on_llm_response_accumulates_latest_reading_only():
    """The meter holds the LATEST real measurement, not a running total
    across calls -- each llm:response describes a full request's usage."""
    context = SimpleContextManager()

    await context._on_llm_response("llm:response", {"usage": {"input_tokens": 100}})
    assert context._last_measured_prompt_tokens == 100

    await context._on_llm_response("llm:response", {"usage": {"input_tokens": 250}})
    assert context._last_measured_prompt_tokens == 250


@pytest.mark.asyncio
async def test_on_llm_response_malformed_payload_never_crashes(caplog):
    """A missing/malformed usage payload must be logged and leave the meter
    unchanged -- never raise. 'Never let the meter make the module crash.'"""
    context = SimpleContextManager()

    with caplog.at_level(logging.DEBUG):
        await context._on_llm_response("llm:response", {})
        await context._on_llm_response("llm:response", {"usage": {}})
        await context._on_llm_response("llm:response", {"usage": {"input_tokens": None}})
        await context._on_llm_response("llm:response", None)

    assert context._last_measured_prompt_tokens is None


# ---------------------------------------------------------------------------
# Default mode ("estimate") must be byte-identical -- the core guarantee
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_token_meter_is_estimate():
    """token_meter defaults to 'estimate' when not passed at all."""
    context = SimpleContextManager()
    assert context.token_meter == "estimate"


@pytest.mark.asyncio
async def test_default_mode_trigger_ignores_real_measurement_even_when_present():
    """With token_meter left at its default ('estimate'), a real
    llm:response reporting usage far above threshold must NOT drive the
    compaction trigger -- only the estimator may. This is what keeps
    default-mode behavior byte-identical to before this meter existed,
    even in a live deployment where llm:response fires on every call."""
    context = SimpleContextManager(
        max_tokens=1_000_000,
        compact_threshold=0.5,
        compaction_notice_enabled=False,
    )
    await context.add_message({"role": "user", "content": "short message"})

    # Real usage reported far above budget -- must be ignored in default mode.
    await context._on_llm_response("llm:response", {"usage": {"input_tokens": 900_000}})

    messages = await context.get_messages_for_request()

    assert context._last_compaction_stats is None, (
        "Default token_meter='estimate' must never let a real measurement "
        "drive the trigger."
    )
    stats = context._last_token_meter_stats
    assert stats["mode"] == "estimate"
    assert stats["source"] == "estimate"
    assert stats["measured_tokens"] == 900_000  # recorded for observability...
    assert stats["used_tokens"] == stats["estimated_tokens"]  # ...but not used
    assert len(messages) == 1


def _strip_timestamps(messages: list[dict]) -> list[dict]:
    """Normalize out add_message()'s wall-clock timestamp (irrelevant to
    this test, and non-deterministic across two independently-built
    managers) so comparisons focus on content, not incidental timing."""
    result = []
    for msg in messages:
        meta = dict(msg.get("metadata") or {})
        meta.pop("timestamp", None)
        result.append({**msg, "metadata": meta})
    return result


@pytest.mark.asyncio
async def test_estimate_mode_output_identical_with_and_without_hook_events():
    """Byte-identical regression guard: two managers built identically
    (token_meter left at default) must produce the exact same
    get_messages_for_request() output whether or not llm:response events
    have fired in between."""
    baseline = SimpleContextManager(
        max_tokens=1_000, compact_threshold=0.5, compaction_notice_enabled=False
    )
    with_events = SimpleContextManager(
        max_tokens=1_000, compact_threshold=0.5, compaction_notice_enabled=False
    )

    for i in range(20):
        msg = {"role": "user", "content": f"message {i} " + "x" * 50}
        await baseline.add_message(dict(msg))
        await with_events.add_message(dict(msg))

    # Feed real (and wildly different) usage events only to `with_events`.
    await with_events._on_llm_response("llm:response", {"usage": {"input_tokens": 1}})
    await with_events._on_llm_response(
        "llm:response", {"usage": {"input_tokens": 999_999, "cache_write_tokens": 5}}
    )

    baseline_view = await baseline.get_messages_for_request()
    with_events_view = await with_events.get_messages_for_request()

    assert _strip_timestamps(baseline_view) == _strip_timestamps(with_events_view)


# ---------------------------------------------------------------------------
# "actual" mode: drives the trigger from real usage, in both directions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_actual_mode_fires_when_real_usage_crosses_threshold_estimator_disagrees():
    """A handful of short messages keep the estimator far below threshold,
    but a real llm:response reports usage above threshold -- token_meter=
    'actual' must fire compaction from the real number, CONTRADICTING the
    estimator."""
    context = SimpleContextManager(
        max_tokens=100_000,
        compact_threshold=0.5,
        target_usage=0.1,
        compaction_notice_enabled=False,
        token_meter="actual",
    )
    for i in range(6):
        await context.add_message({"role": "user", "content": f"msg {i}"})
        await context.add_message({"role": "assistant", "content": f"reply {i}"})

    # Sanity: the estimator alone would NOT cross threshold.
    estimate = context._estimate_tokens(context.messages)
    assert estimate / 100_000 < 0.5

    await context._on_llm_response("llm:response", {"usage": {"input_tokens": 90_000}})

    await context.get_messages_for_request()

    stats = context._last_token_meter_stats
    assert stats["source"] == "measured"
    assert stats["used_tokens"] == 90_000
    assert context._last_compaction_stats is not None, (
        "token_meter='actual' should have fired compaction from the real "
        "measurement even though the estimator disagreed"
    )


@pytest.mark.asyncio
async def test_actual_mode_does_not_fire_when_real_usage_below_threshold_estimator_disagrees():
    """Long, padded messages push the estimator ABOVE threshold, but a real
    llm:response reports usage below threshold -- token_meter='actual' must
    NOT fire, again CONTRADICTING the estimator (the opposite direction from
    the test above)."""
    context = SimpleContextManager(
        max_tokens=1_000,
        compact_threshold=0.5,
        compaction_notice_enabled=False,
        token_meter="actual",
    )
    for i in range(20):
        await context.add_message({"role": "user", "content": "x" * 200})

    # Sanity: the estimator alone WOULD cross threshold.
    estimate = context._estimate_tokens(context.messages)
    assert estimate / 1_000 >= 0.5

    await context._on_llm_response("llm:response", {"usage": {"input_tokens": 100}})

    await context.get_messages_for_request()

    stats = context._last_token_meter_stats
    assert stats["source"] == "measured"
    assert stats["used_tokens"] == 100
    assert context._last_compaction_stats is None, (
        "token_meter='actual' should NOT have fired compaction: the real "
        "measurement was below threshold even though the estimator disagreed"
    )


@pytest.mark.asyncio
async def test_actual_mode_falls_back_to_estimate_before_first_measurement():
    """Before any llm:response has fired this session, token_meter='actual'
    must fall back to the same estimator 'estimate' mode uses -- the
    compaction trigger is never left without a signal."""
    context = SimpleContextManager(
        max_tokens=1_000,
        compact_threshold=0.5,
        compaction_notice_enabled=False,
        token_meter="actual",
    )
    for i in range(20):
        await context.add_message({"role": "user", "content": "x" * 200})

    await context.get_messages_for_request()

    stats = context._last_token_meter_stats
    assert stats["source"] == "estimate"
    assert stats["measured_tokens"] is None
    assert context._last_compaction_stats is not None  # estimator alone crosses threshold


# ---------------------------------------------------------------------------
# Config validation: never crash on a bad token_meter value
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_token_meter_falls_back_to_estimate_with_warning(caplog):
    with caplog.at_level(logging.WARNING):
        context = SimpleContextManager(token_meter="bogus-mode")

    assert context.token_meter == "estimate"
    assert any("unknown token_meter" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_mount_invalid_token_meter_falls_back_to_estimate_with_warning(caplog):
    coordinator = _FakeCoordinator(hooks=_FakeHooks())

    with caplog.at_level(logging.WARNING):
        await mount(coordinator, {"token_meter": "bogus-mode"})

    assert coordinator.mounted["context"].token_meter == "estimate"
    assert any("unknown token_meter" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# clear() resets meter state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clear_resets_token_meter_state():
    context = SimpleContextManager(token_meter="actual")
    await context.add_message({"role": "user", "content": "hi"})
    await context._on_llm_response("llm:response", {"usage": {"input_tokens": 42}})
    await context.get_messages_for_request()
    assert context._last_measured_prompt_tokens == 42
    assert context._last_token_meter_stats is not None

    await context.clear()

    assert context._last_measured_prompt_tokens is None
    assert context._last_token_meter_stats is None


# ---------------------------------------------------------------------------
# Observability surface: populated on every call, not just on compaction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_token_meter_stats_populated_even_without_compaction():
    context = SimpleContextManager(max_tokens=100_000, compact_threshold=0.92)
    await context.add_message({"role": "user", "content": "hello"})

    await context.get_messages_for_request()

    assert context._last_compaction_stats is None
    stats = context._last_token_meter_stats
    assert stats is not None
    assert stats["mode"] == "estimate"
    assert stats["source"] == "estimate"
    assert {
        "mode",
        "source",
        "used_tokens",
        "estimated_tokens",
        "measured_tokens",
        "budget",
        "ratio",
    } <= set(stats.keys())


# ---------------------------------------------------------------------------
# mount(): hook registration, cleanup, and config threading
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mount_registers_llm_response_hook_when_hooks_available():
    hooks = _FakeHooks()
    coordinator = _FakeCoordinator(hooks)

    cleanup = await mount(coordinator, {"token_meter": "actual"})

    assert len(hooks.registered) == 1
    entry = hooks.registered[0]
    assert entry["event"] == "llm:response"
    assert entry["name"] == "context-simple-meter"
    assert coordinator.mounted["context"].token_meter == "actual"

    await cleanup()
    assert hooks.registered == []


@pytest.mark.asyncio
async def test_mount_registers_hook_even_in_default_estimate_mode():
    """The hook is always registered when hooks are available (regardless
    of token_meter mode) -- recording is harmless under 'estimate' and this
    is what makes _last_token_meter_stats observable even by default."""
    hooks = _FakeHooks()
    coordinator = _FakeCoordinator(hooks)

    await mount(coordinator, {})

    assert len(hooks.registered) == 1
    assert coordinator.mounted["context"].token_meter == "estimate"


@pytest.mark.asyncio
async def test_mount_without_hooks_does_not_crash_and_returns_cleanup():
    coordinator = _FakeCoordinator(hooks=None)

    cleanup = await mount(coordinator, {"token_meter": "actual"})

    assert coordinator.mounted["context"].token_meter == "actual"
    await cleanup()  # must be a no-op, not an error


@pytest.mark.asyncio
async def test_mount_registered_hook_updates_the_meter():
    """End-to-end: the handler mount() registers is really bound to the
    mounted manager's own meter state."""
    hooks = _FakeHooks()
    coordinator = _FakeCoordinator(hooks)

    await mount(coordinator, {"token_meter": "actual"})
    context = coordinator.mounted["context"]

    handler = hooks.registered[0]["handler"]
    await handler("llm:response", {"usage": {"input_tokens": 321}})

    assert context._last_measured_prompt_tokens == 321

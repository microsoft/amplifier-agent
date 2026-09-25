"""Characterization tests for WHERE the compaction trigger actually comes from.

Why this file exists
--------------------
The compaction trigger is::

    trigger = compact_threshold * effective_budget

and `effective_budget` is derived **from the provider** and then CAPPED by
the configured `max_tokens`.  `_calculate_budget()` only falls back to
`self.max_tokens` when no provider is passed *or* the provider exposes no
usable window information -- and the orchestrator always passes a provider
(`loop-streaming` calls `context.get_messages_for_request(provider=provider)`).

HISTORY: `max_tokens` used to be a *fallback only* -- consulted solely when
no provider published a window -- which made it a silently dead knob in
production, since the orchestrator always passes a provider.  It is now a
**cap**: `min(derived_budget, max_tokens)`, defaulting to None (no cap).
Lowering it moves the trigger; raising it above the model window is a no-op.
These tests pin BOTH halves of that contract.

This is not hypothetical.  The cadence probe ("PROBE 4", capture root
`.amplifier/evaluation/treatment-validation/20260901-cadence/`) could not move
the trigger with config alone: its harness had to patch the module source
in-container to add `budget = min(budget, self.max_tokens)` before its arms
would compact at all.  Its own note records why -- "the loop always passes the
provider ... so the configured max_tokens is dead and compaction never fires
in a bounded run" (`scenarios/_harness/configure_cell.py`).  That patch is
exactly the behavior this module now ships, as a supported option.

Before this file, **no test in the suite exercised the provider-derived budget
path at all** -- every existing test constructs the manager with `max_tokens`
and no provider, i.e. exclusively the fallback branch.  So the trap was
invisible to the suite: a change that "raised the compaction trigger" by
editing `max_tokens` would have passed the whole suite green while doing
nothing on the wire.

These tests pin current behavior so the next reader hits the trap here rather
than in a $12 measurement run.  They deliberately assert what the module
*does*, not what it arguably *should* do; changing the semantics of
`max_tokens` is a behavior change that needs its own measurement.
"""

import pytest

from amplifier_module_context_simple import (
    DEFAULT_MAX_TOKENS_FALLBACK,
    SimpleContextManager,
    _validated_max_tokens,
)

# ---------------------------------------------------------------------------
# Values quoted from named sources, not invented here.
# ---------------------------------------------------------------------------

#: The value the shipped foundation bundle puts in `context.config.max_tokens`
#: (amplifier-foundation `bundle.md`, context-simple config block).
FOUNDATION_CONFIGURED_MAX_TOKENS = 300_000

#: The S5-CRAC cadence harness forcing values.  `cad-today` ran at 45,000 and
#: `cad-fewer` raised it to 70,000 (PROBE4-VERDICT.md, arm table).  Both are
#: *scenario forcing knobs*, not production defaults.
CAD_TODAY_MAX_TOKENS = 45_000
CAD_FEWER_MAX_TOKENS = 70_000

#: `_calculate_budget`'s fixed buffer.
SAFETY_MARGIN = 4_096


class _ModelInfo:
    def __init__(self, context_window: int, max_output_tokens: int) -> None:
        self.context_window = context_window
        self.max_output_tokens = max_output_tokens


class _ProviderWithModelInfo:
    """Provider exposing `get_model_info()` -- priority 2 in `_calculate_budget`."""

    def __init__(self, context_window: int, max_output_tokens: int) -> None:
        self._info = _ModelInfo(context_window, max_output_tokens)

    def get_model_info(self) -> _ModelInfo:
        return self._info


class _Info:
    def __init__(self, defaults: dict) -> None:
        self.defaults = defaults


class _ProviderWithDefaultsOnly:
    """Provider exposing only `get_info().defaults` -- priority 3."""

    def __init__(self, context_window: int, max_output_tokens: int) -> None:
        self._info = _Info(
            {
                "context_window": context_window,
                "max_output_tokens": max_output_tokens,
            }
        )

    def get_info(self) -> _Info:
        return self._info


class _ProviderWithNoWindowInfo:
    """Provider that knows nothing about its window -- forces priority 4."""

    def get_info(self) -> _Info:
        return _Info({})


def _expected_budget(context_window: int, max_output_tokens: int) -> int:
    """The formula in `_calculate_budget`, spelled out."""
    return context_window - int(max_output_tokens * 0.5) - SAFETY_MARGIN


# ---------------------------------------------------------------------------
# 1. The provider wins. `max_tokens` does not.
# ---------------------------------------------------------------------------


def test_provider_model_info_budget_is_used_when_no_cap_is_set():
    """With `max_tokens` unset (the default), the provider's window governs."""
    context = SimpleContextManager()
    provider = _ProviderWithModelInfo(context_window=1_000_000, max_output_tokens=128_000)

    budget = context._calculate_budget(None, provider)

    assert budget == _expected_budget(1_000_000, 128_000) == 931_904


def test_max_tokens_caps_the_provider_budget():
    """The fix: a configured `max_tokens` LOWERS a larger provider window."""
    context = SimpleContextManager(max_tokens=FOUNDATION_CONFIGURED_MAX_TOKENS)
    provider = _ProviderWithModelInfo(context_window=1_000_000, max_output_tokens=128_000)

    budget = context._calculate_budget(None, provider)

    assert budget == FOUNDATION_CONFIGURED_MAX_TOKENS, (
        "max_tokens is a cap: with a 1M-window provider and a 300k cap, the "
        "effective budget must be the cap, not the window."
    )


def test_max_tokens_above_the_model_window_is_a_no_op():
    """The lower of the two always wins -- a cap cannot RAISE a budget."""
    context = SimpleContextManager(max_tokens=5_000_000)
    provider = _ProviderWithModelInfo(context_window=1_000_000, max_output_tokens=128_000)

    assert context._calculate_budget(None, provider) == _expected_budget(
        1_000_000, 128_000
    )


def test_provider_defaults_budget_is_used_when_no_cap_is_set():
    """Same, via the legacy `get_info().defaults` path."""
    context = SimpleContextManager()
    provider = _ProviderWithDefaultsOnly(context_window=200_000, max_output_tokens=64_000)

    budget = context._calculate_budget(None, provider)

    assert budget == _expected_budget(200_000, 64_000) == 163_904


def test_cap_applies_to_the_legacy_defaults_path_too():
    """The cap has no exceptions -- it applies on every derivation path."""
    context = SimpleContextManager(max_tokens=100_000)
    provider = _ProviderWithDefaultsOnly(context_window=200_000, max_output_tokens=64_000)

    assert context._calculate_budget(None, provider) == 100_000


def test_fallback_is_used_only_when_the_provider_reports_no_window():
    """`max_tokens_fallback` -- not `max_tokens` -- answers when nothing else can."""
    context = SimpleContextManager(max_tokens_fallback=FOUNDATION_CONFIGURED_MAX_TOKENS)

    assert context._calculate_budget(None, None) == FOUNDATION_CONFIGURED_MAX_TOKENS
    assert (
        context._calculate_budget(None, _ProviderWithNoWindowInfo())
        == FOUNDATION_CONFIGURED_MAX_TOKENS
    )


def test_default_fallback_is_the_calibrated_constant():
    """An unconfigured manager falls back to the documented 200k, not to None."""
    context = SimpleContextManager()

    assert context._calculate_budget(None, None) == DEFAULT_MAX_TOKENS_FALLBACK
    assert DEFAULT_MAX_TOKENS_FALLBACK == 200_000


def test_cap_applies_to_the_fallback_path_too():
    """A cap below the fallback still wins -- one rule, no exceptions."""
    context = SimpleContextManager(max_tokens=50_000, max_tokens_fallback=300_000)

    assert context._calculate_budget(None, _ProviderWithNoWindowInfo()) == 50_000


def test_explicit_token_budget_still_wins_over_everything():
    """Priority 1 is unchanged: an explicit budget short-circuits the rest."""
    context = SimpleContextManager(max_tokens=FOUNDATION_CONFIGURED_MAX_TOKENS)
    provider = _ProviderWithModelInfo(context_window=1_000_000, max_output_tokens=128_000)

    assert context._calculate_budget(70_000, provider) == 70_000


# ---------------------------------------------------------------------------
# 2. The trap itself, end to end.
# ---------------------------------------------------------------------------


async def _fill(context: SimpleContextManager, pairs: int = 20, chars: int = 5_000) -> None:
    """~50,000 estimated tokens (len(str(msg))//4) of removable history."""
    for i in range(pairs):
        await context.add_message({"role": "user", "content": f"u{i} " + ("x" * chars)})
        await context.add_message(
            {"role": "assistant", "content": f"a{i} " + ("y" * chars)}
        )


@pytest.mark.asyncio
async def test_lowering_max_tokens_DOES_move_the_trigger_when_a_provider_is_present():
    """THE TRAP, INVERTED -- this is the behavior change.

    Before the cap existed, both cadence-harness forcing values (45,000 and
    70,000) were dead: a provider reporting a 200,000-token window produced a
    163,904 budget and ~50,000 tokens of history never crossed the threshold,
    no matter what `max_tokens` said.  The harness had to patch module source
    to get the effect this test now asserts as shipped behavior.

    The two harness values now land on OPPOSITE sides of the same ~50,000-token
    history, which is the sharpest available proof that the knob is live: 45,000
    caps low enough to compact (0.92 * 45,000 = 41,400 < ~50,000), 70,000 does
    not (0.92 * 70,000 = 64,400 > ~50,000).  Before the cap, both were dead and
    neither compacted.
    """
    provider = _ProviderWithModelInfo(context_window=200_000, max_output_tokens=64_000)

    low = SimpleContextManager(max_tokens=CAD_TODAY_MAX_TOKENS)
    await _fill(low)
    await low.get_messages_for_request(provider=provider)
    assert low._last_compaction_stats is not None, (
        f"max_tokens={CAD_TODAY_MAX_TOKENS:,} must cap the 163,904 provider "
        f"budget and move the trigger; ~50,000 tokens of history is over "
        f"compact_threshold * {CAD_TODAY_MAX_TOKENS:,}."
    )

    high = SimpleContextManager(max_tokens=CAD_FEWER_MAX_TOKENS)
    await _fill(high)
    await high.get_messages_for_request(provider=provider)
    assert high._last_compaction_stats is None, (
        f"max_tokens={CAD_FEWER_MAX_TOKENS:,} caps the budget but stays above "
        f"this history -- raising the cap must move the trigger LATER, which is "
        f"what makes the knob a real dial rather than an on/off switch."
    )


@pytest.mark.asyncio
async def test_an_unset_cap_leaves_the_provider_budget_alone():
    """The no-cap default is the old behavior, exactly.

    Same provider, same history as the test above, with `max_tokens` unset:
    the 163,904 provider budget governs and ~50,000 tokens does not reach it.
    This is what makes the cap opt-in rather than a silent clamp.
    """
    provider = _ProviderWithModelInfo(context_window=200_000, max_output_tokens=64_000)
    context = SimpleContextManager()
    await _fill(context)
    await context.get_messages_for_request(provider=provider)

    assert context._last_compaction_stats is None, (
        "With no cap configured, the effective budget must remain the "
        f"provider's {_expected_budget(200_000, 64_000):,} and this history "
        "must not compact."
    )


@pytest.mark.asyncio
async def test_the_same_history_and_config_does_compact_once_the_provider_is_gone():
    """The contrast that proves the previous test is not vacuous.

    Identical config, identical history -- drop the provider and `max_tokens`
    becomes live, so compaction fires.  The only difference between "trigger
    dead" and "trigger live" is whether a provider was passed.
    """
    context = SimpleContextManager(max_tokens_fallback=CAD_TODAY_MAX_TOKENS)
    await _fill(context)
    await context.get_messages_for_request()  # no provider -> fallback budget

    stats = context._last_compaction_stats
    assert stats is not None, (
        "With no provider, max_tokens_fallback is the budget and this history "
        "is well over compact_threshold * fallback -- compaction must fire."
    )
    assert stats["after_tokens"] < stats["before_tokens"]


# ---------------------------------------------------------------------------
# 3. The shipped trigger fraction, pinned.
# ---------------------------------------------------------------------------


def test_default_compact_threshold_is_0_92():
    """Pin the only shipped knob that expresses "compact late" as a fraction.

    `compact_threshold` has been 0.92 for the whole life of this repository
    and was NOT the knob the cadence probe overrode (`cad-fewer` varied the
    budget, holding threshold and `target_usage` at stock).  Pinning it means
    a future silent flip has to argue with a test.
    """
    assert SimpleContextManager().compact_threshold == 0.92


def test_compact_threshold_override_moves_the_trigger():
    """The old value stays reachable via config -- in both directions."""
    assert SimpleContextManager(compact_threshold=0.8).compact_threshold == 0.8
    assert SimpleContextManager(compact_threshold=0.95).compact_threshold == 0.95

    budget = 163_904
    early = SimpleContextManager(compact_threshold=0.8)
    late = SimpleContextManager(compact_threshold=0.95)

    assert early._should_compact(int(budget * 0.85), budget) is True
    assert late._should_compact(int(budget * 0.85), budget) is False


@pytest.mark.parametrize(
    ("context_window", "max_output_tokens"),
    [(200_000, 64_000), (1_000_000, 128_000)],
)
def test_capping_the_budget_at_cad_fewer_value_would_compact_EARLIER_not_later(
    context_window: int, max_output_tokens: int
):
    """Why 70,000 must not become a shipped default.

    `cad-fewer`'s 70,000 is a forcing value that made a bounded 10-turn
    scenario compact *at all*; it is not "the late trigger".  Turned into a
    budget cap for every session it moves the trigger EARLIER than today for
    any provider whose derived budget exceeds 70,000 -- i.e. it produces MORE
    boundaries, inverting the very win (-29% requests, -14% wall) that was
    measured by having FEWER of them.
    """
    threshold = SimpleContextManager().compact_threshold

    shipped_budget = _expected_budget(context_window, max_output_tokens)
    shipped_trigger = threshold * shipped_budget
    capped_trigger = threshold * CAD_FEWER_MAX_TOKENS

    assert capped_trigger < shipped_trigger
    assert shipped_trigger / capped_trigger > 2.0, (
        "Capping the budget at cad-fewer's 70,000 would move the compaction "
        f"trigger from {shipped_trigger:,.0f} tokens to {capped_trigger:,.0f} "
        "-- earlier, not later."
    )


# ---------------------------------------------------------------------------
# 4. A nonsense cap is refused loudly, not honored.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [0, -1, -500_000, "500000", 1.5, True, False])
def test_invalid_max_tokens_is_ignored_rather_than_honored(bad):
    """A cap of 0 or a non-int would drive the budget to <= 0.

    `_should_compact`'s `budget > 0` guard would then force usage to 0 and
    silently disable compaction entirely -- the exact opposite of what someone
    setting a cap is asking for. So an invalid value degrades to "no cap",
    with a warning, matching how `compaction_notice_token_reserve` handles a
    self-defeating value.
    """
    assert _validated_max_tokens(bad) is None


@pytest.mark.parametrize("good", [1, 200_000, 500_000, 5_000_000])
def test_valid_max_tokens_passes_through(good):
    assert _validated_max_tokens(good) == good


def test_none_means_no_cap():
    assert _validated_max_tokens(None) is None

"""`context:budget` -- the effective budget, as an artifact rather than a log line.

Why this file exists
--------------------
`_derive_budget` logs the number it picks, but a log line is not an artifact:
DTU validation of the max_tokens-cap change could not answer "what budget did
this session actually run on?" after the fact. The log never reached the
session record and did not surface on stdout even at INFO.

That is the first question anyone asks when compaction fires earlier than
expected, or when a cap is suspected but not confirmed. This event makes it
answerable from the record.

Two properties matter as much as the number itself:

* it fires at the DELIVERY boundary, so a cancelled or failed request leaves
  no trace (the rollback invariant `test_request_retention` pins), and
* it fires on CHANGE, so a long session carries a readable handful of lines
  rather than one per request.
"""

from __future__ import annotations

import pytest

from amplifier_module_context_simple import SimpleContextManager


class _Hooks:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def emit(self, event: str, data: dict) -> None:
        self.events.append((event, data))


class _Provider:
    def __init__(self, context_window: int, max_output_tokens: int) -> None:
        self._defaults = {
            "context_window": context_window,
            "max_output_tokens": max_output_tokens,
        }

    def get_info(self):
        return type("_Info", (), {"defaults": self._defaults})()


def _budget_events(hooks: _Hooks) -> list[dict]:
    return [data for event, data in hooks.events if event == "context:budget"]


@pytest.mark.asyncio
async def test_budget_is_emitted_with_its_provenance():
    hooks = _Hooks()
    context = SimpleContextManager(hooks=hooks)
    await context.add_message({"role": "user", "content": "hi"})

    await context.get_messages_for_request(provider=_Provider(1_048_576, 65_536))

    (event,) = _budget_events(hooks)
    assert event["effective_budget"] == 1_011_712
    assert event["source"] == "provider_defaults"
    assert event["context_window"] == 1_048_576
    assert event["capped"] is False


@pytest.mark.asyncio
async def test_a_cap_is_visible_in_the_event():
    """The question this exists to answer: was I capped, and by what?"""
    hooks = _Hooks()
    context = SimpleContextManager(max_tokens=500_000, hooks=hooks)
    await context.add_message({"role": "user", "content": "hi"})

    await context.get_messages_for_request(provider=_Provider(1_048_576, 65_536))

    (event,) = _budget_events(hooks)
    assert event["capped"] is True
    assert event["effective_budget"] == 500_000
    assert event["derived_budget"] == 1_011_712
    assert event["max_tokens"] == 500_000


@pytest.mark.asyncio
async def test_the_fallback_path_names_itself():
    hooks = _Hooks()
    context = SimpleContextManager(hooks=hooks)
    await context.add_message({"role": "user", "content": "hi"})

    await context.get_messages_for_request()

    (event,) = _budget_events(hooks)
    assert event["source"] == "max_tokens_fallback"
    assert event["effective_budget"] == 200_000


@pytest.mark.asyncio
async def test_it_fires_on_change_not_per_request():
    hooks = _Hooks()
    context = SimpleContextManager(hooks=hooks)
    provider = _Provider(1_048_576, 65_536)
    await context.add_message({"role": "user", "content": "hi"})

    for _ in range(5):
        await context.get_messages_for_request(provider=provider)

    assert len(_budget_events(hooks)) == 1


@pytest.mark.asyncio
async def test_a_changed_budget_emits_again():
    hooks = _Hooks()
    context = SimpleContextManager(hooks=hooks)
    await context.add_message({"role": "user", "content": "hi"})

    await context.get_messages_for_request(provider=_Provider(1_048_576, 65_536))
    await context.get_messages_for_request(provider=_Provider(200_000, 64_000))

    events = _budget_events(hooks)
    assert [e["effective_budget"] for e in events] == [1_011_712, 163_904]


@pytest.mark.asyncio
async def test_no_hooks_is_not_an_error():
    """Observability must never be load-bearing."""
    context = SimpleContextManager()
    await context.add_message({"role": "user", "content": "hi"})

    result = await context.get_messages_for_request(provider=_Provider(200_000, 64_000))

    assert result


@pytest.mark.asyncio
async def test_a_failing_emitter_does_not_break_the_request():
    class _Broken(_Hooks):
        async def emit(self, event: str, data: dict) -> None:
            raise RuntimeError("hook exploded")

    context = SimpleContextManager(hooks=_Broken())
    await context.add_message({"role": "user", "content": "hi"})

    result = await context.get_messages_for_request(provider=_Provider(200_000, 64_000))

    assert result

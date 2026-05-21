"""Tests for the types re-export module in amplifier_agent_client.

RED: fails because wrappers/python/src/amplifier_agent_client/types.py does not exist yet.
GREEN: passes once the re-export module is created.

TDD spec:
- `from amplifier_agent_client.types import InitializeParams` should succeed
- `InitializeParams is Source` identity check must pass (same object, not a copy)
- `ErrorCode` re-export from errors module must pass identity check
- `CANONICAL_DISPLAY_EVENTS` re-export must be a tuple containing 'result/final'
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_initialize_params_is_source() -> None:
    """Re-exported InitializeParams must be the SAME object as the upstream one.

    Identity check ensures we're re-exporting (not reimplementing) the TypedDict.
    """
    from amplifier_agent_lib.protocol.methods import InitializeParams as Source

    from amplifier_agent_client.types import InitializeParams

    assert InitializeParams is Source, (
        "amplifier_agent_client.types.InitializeParams must be the same object as "
        "amplifier_agent_lib.protocol.methods.InitializeParams"
    )


@pytest.mark.asyncio
async def test_turn_submit_params_is_source() -> None:
    """Re-exported TurnSubmitParams must be the SAME object as the upstream one."""
    from amplifier_agent_lib.protocol.methods import TurnSubmitParams as Source

    from amplifier_agent_client.types import TurnSubmitParams

    assert TurnSubmitParams is Source


@pytest.mark.asyncio
async def test_initialize_result_is_source() -> None:
    """Re-exported InitializeResult must be the SAME object as the upstream one."""
    from amplifier_agent_lib.protocol.methods import InitializeResult as Source

    from amplifier_agent_client.types import InitializeResult

    assert InitializeResult is Source


@pytest.mark.asyncio
async def test_turn_submit_result_is_source() -> None:
    """Re-exported TurnSubmitResult must be the SAME object as the upstream one."""
    from amplifier_agent_lib.protocol.methods import TurnSubmitResult as Source

    from amplifier_agent_client.types import TurnSubmitResult

    assert TurnSubmitResult is Source


@pytest.mark.asyncio
async def test_error_code_is_source() -> None:
    """Re-exported ErrorCode must be the SAME object as the upstream one."""
    from amplifier_agent_lib.protocol.errors import ErrorCode as Source

    from amplifier_agent_client.types import ErrorCode

    assert ErrorCode is Source


@pytest.mark.asyncio
async def test_canonical_display_events_is_tuple_containing_result_final() -> None:
    """CANONICAL_DISPLAY_EVENTS must be a tuple containing 'result/final'."""
    from amplifier_agent_client.types import CANONICAL_DISPLAY_EVENTS

    assert isinstance(CANONICAL_DISPLAY_EVENTS, tuple), "CANONICAL_DISPLAY_EVENTS must be a tuple"
    assert "result/final" in CANONICAL_DISPLAY_EVENTS, "'result/final' must be in CANONICAL_DISPLAY_EVENTS"


@pytest.mark.asyncio
async def test_canonical_display_events_is_source() -> None:
    """Re-exported CANONICAL_DISPLAY_EVENTS must be the SAME object as upstream."""
    from amplifier_agent_lib.protocol.notifications import CANONICAL_DISPLAY_EVENTS as Source

    from amplifier_agent_client.types import CANONICAL_DISPLAY_EVENTS

    assert CANONICAL_DISPLAY_EVENTS is Source

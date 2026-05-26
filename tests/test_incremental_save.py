"""Tests for IncrementalSaveHook (A2 — CR-1).

Verifies that the hook persists the current transcript via SessionStore
on each ``tool:post`` event and returns ``HookResult(action='continue')``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from amplifier_core.models import HookResult

from amplifier_agent_lib.incremental_save import IncrementalSaveHook
from amplifier_agent_lib.session_store import SessionStore


@pytest.mark.asyncio
async def test_hook_saves_transcript_on_call(tmp_path: Any) -> None:
    session_id = "sess-hook-001"
    transcript = [{"role": "user", "content": "hello"}]

    store = SessionStore(tmp_path)
    get_messages = AsyncMock(return_value=transcript)

    hook = IncrementalSaveHook(
        store=store,
        session_id=session_id,
        get_messages=get_messages,
    )

    await hook(
        "tool:post",
        {"tool_name": "bash", "session_id": session_id, "turn_id": "t-1"},
    )

    loaded = store.load(session_id)
    assert loaded is not None
    loaded_transcript, loaded_metadata = loaded
    assert loaded_transcript == transcript
    assert loaded_metadata["last_tool"] == "bash"


@pytest.mark.asyncio
async def test_hook_returns_hook_result_continue(tmp_path: Any) -> None:
    store = SessionStore(tmp_path)
    get_messages = AsyncMock(return_value=[])

    hook = IncrementalSaveHook(
        store=store,
        session_id="sess-hook-002",
        get_messages=get_messages,
    )

    result = await hook("tool:post", {"tool_name": "bash"})

    assert isinstance(result, HookResult)
    assert result.action == "continue"

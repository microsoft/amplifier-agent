import asyncio
import json
import sqlite3
import sys
from decimal import Decimal

import pytest
from amplifier_agent_engine._engine.storage import Checkpoint, SessionStore
from amplifier_agent_engine._records import (
    AgentError,
    TextPart,
    TurnInput,
    TurnRecord,
    TurnResult,
    Usage,
    UsageEntry,
)


def checkpoint(text="Committed"):
    result = TurnResult(
        "failure",
        content=[TextPart(text)],
        error=AgentError(
            "tool_completion_unknown",
            "executor",
            "The reply was lost.",
            "Inspect the effect.",
            details={"record": "user-data", "fields": {"decimal": "literal"}},
        ),
        usage=Usage(
            [
                UsageEntry(
                    "anthropic",
                    "claude-sonnet-5",
                    tokens_in=9007199254740993,
                    cost={"USD": Decimal("0.0000000000000000007")},
                )
            ]
        ),
    )
    setattr(result, "org.example.receipt", {"value": 9007199254740995})
    return Checkpoint(
        "anthropic",
        "claude-sonnet-5",
        True,
        False,
        [TurnRecord("saved-turn", TurnInput([TextPart("Input")]), result)],
        {"version": 1, "messages": [{"role": "assistant", "content": text}]},
    )


def create(store, value=None):
    lease = store.create_lease("saved-session")
    store.save("saved-session", value or checkpoint(), create=True)
    lease.close()


def test_checkpoint_codec_preserves_exact_records_errors_and_owned_extensions():
    original = checkpoint()
    restored = Checkpoint.loads(original.dumps())
    assert restored == original
    assert vars(restored.history[0].result) == vars(original.history[0].result)
    assert restored.history[0].result.usage.entries[0].cost == {
        "USD": Decimal("0.0000000000000000007")
    }


def test_corrupt_transcript_fails_loudly_releases_lease_and_remains_deletable(tmp_path):
    store = SessionStore(tmp_path, "default")
    create(store)
    with sqlite3.connect(store._database) as connection:
        value = json.loads(connection.execute("SELECT checkpoint FROM sessions").fetchone()[0])
        value["checkpoint"]["mapping"]["model"] = "Changed without a valid checkpoint"
        connection.execute("UPDATE sessions SET checkpoint=?", (json.dumps(value),))
    with pytest.raises(AgentError) as error:
        store.resume("saved-session")
    assert error.value.code == "internal_failed"
    assert error.value.remedy
    store.delete("saved-session")
    assert store.list() == []


async def test_process_exit_inside_checkpoint_transaction_preserves_previous_turn(tmp_path):
    store = SessionStore(tmp_path, "default")
    original = checkpoint()
    create(store, original)
    code = """
import os, sys
from contextlib import contextmanager
from pathlib import Path
from amplifier_agent_engine._engine.storage import SessionStore
store = SessionStore(Path(sys.argv[1]), 'default')
lease, checkpoint = store.resume('saved-session')
checkpoint.history[0].result.content[0].text = 'Uncommitted change'
connection = store._connection
@contextmanager
def interrupted():
    with connection() as current:
        yield current
        os._exit(7)
store._connection = interrupted
store.save('saved-session', checkpoint)
"""
    child = await asyncio.create_subprocess_exec(sys.executable, "-c", code, str(tmp_path))
    try:
        assert await asyncio.wait_for(child.wait(), 5) == 7
    finally:
        if child.returncode is None:
            child.kill()
            await child.wait()
    lease, restored = store.resume("saved-session")
    try:
        assert restored == original
    finally:
        lease.close()


def test_unknown_storage_version_is_a_named_failure(tmp_path):
    store = SessionStore(tmp_path, "default")
    create(store)
    with sqlite3.connect(store._database) as connection:
        connection.execute("PRAGMA user_version=99")
    with pytest.raises(AgentError) as error:
        store.resume("saved-session")
    assert error.value.code == "internal_failed"

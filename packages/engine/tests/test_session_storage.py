import json
from decimal import Decimal

import pytest
from amplifier_agent_engine._engine.storage import CommittedTurn, SessionStore
from amplifier_agent_engine._records import (
    AgentError,
    TextPart,
    TurnInput,
    TurnRecord,
    TurnResult,
    Usage,
    UsageEntry,
)

FACTS = {"provider": "anthropic", "model": "claude-sonnet-5", "inherited": False}


def turn(text="Committed", messages=2):
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
    return CommittedTurn(TurnRecord("saved-turn", TurnInput([TextPart("Input")]), result), messages)


def conversation(text="Committed"):
    return [{"role": "user", "content": "Input"}, {"role": "assistant", "content": text}]


def create(store, session_id="saved-session"):
    lease = store.create_lease(session_id)
    store.reserve(session_id, FACTS, [], [])
    store.commit(session_id, conversation(), [turn()], {"model": "claude-sonnet-5"})
    lease.close()


def test_turn_codec_preserves_exact_records_errors_and_owned_extensions():
    original = turn()
    restored = CommittedTurn.loads(original.dumps())
    assert restored == original
    assert vars(restored.turn.result) == vars(original.turn.result)
    assert restored.turn.result.usage.entries[0].cost == {"USD": Decimal("0.0000000000000000007")}


def test_committed_state_lives_in_the_amplifier_session_layout(tmp_path):
    store = SessionStore(tmp_path, "team")
    create(store)
    directory = tmp_path / "workspaces" / "team" / "sessions" / "saved-session"
    assert [json.loads(line) for line in (directory / "transcript.jsonl").read_text().splitlines()] == conversation()
    assert (directory / "turns.jsonl").read_text().count("\n") == 1
    metadata = json.loads((directory / "metadata.json").read_text())
    assert metadata["session_id"] == "saved-session"
    assert metadata["workspace"] == "team"
    assert metadata["persistence"] == "durable"
    assert metadata["turn_count"] == 1
    assert metadata["model"] == "claude-sonnet-5"
    assert (tmp_path / "workspaces" / "team" / "locks" / "saved-session").exists()
    lease, loaded = store.resume("saved-session")
    lease.close()
    assert loaded.messages == conversation()
    assert loaded.turns == [turn()]
    assert store.list_ids() == ["saved-session"]


def test_resume_truncates_the_transcript_to_the_last_committed_turn(tmp_path):
    store = SessionStore(tmp_path, "default")
    create(store)
    transcript = store.session_dir("saved-session") / "transcript.jsonl"
    with transcript.open("a") as stream:
        stream.write(json.dumps({"role": "user", "content": "Uncommitted"}) + "\n")
    lease, loaded = store.resume("saved-session")
    lease.close()
    assert loaded.messages == conversation()
    assert len(loaded.turns) == 1


def test_a_transcript_shorter_than_its_commit_point_is_a_named_failure(tmp_path):
    store = SessionStore(tmp_path, "default")
    create(store)
    transcript = store.session_dir("saved-session") / "transcript.jsonl"
    transcript.write_text(json.dumps({"role": "user", "content": "Input"}) + "\n")
    transcript.with_suffix(".jsonl.backup").unlink()
    with pytest.raises(AgentError) as error:
        store.resume("saved-session")
    assert error.value.code == "internal_failed"
    assert error.value.remedy
    store.delete("saved-session")
    assert store.list_ids() == []


def test_corrupt_turn_records_fail_loudly_release_the_lease_and_remain_deletable(tmp_path):
    store = SessionStore(tmp_path, "default")
    create(store)
    turns = store.session_dir("saved-session") / "turns.jsonl"
    turns.write_text('{"turn": {"mapping": {}}, "messages": 2}\n')
    with pytest.raises(AgentError) as error:
        store.resume("saved-session")
    assert error.value.code == "internal_failed"
    store.delete("saved-session")
    assert store.list_ids() == []
    assert not store.session_dir("saved-session").exists()


def test_turn_records_recover_from_their_backup(tmp_path):
    store = SessionStore(tmp_path, "default")
    create(store)
    lease = store.create_lease("second-session")
    lease.close()
    store.reserve("second-session", FACTS, [], [])
    store.commit("second-session", conversation(), [turn()], {})
    store.commit("second-session", conversation() * 2, [turn(), turn("Second", 4)], {})
    (store.session_dir("second-session") / "turns.jsonl").write_text("not json\n")
    lease, loaded = store.resume("second-session")
    lease.close()
    assert loaded.turns == [turn()]
    assert loaded.messages == conversation()


def test_only_directories_holding_a_transcript_are_sessions(tmp_path):
    store = SessionStore(tmp_path, "default")
    create(store)
    (store.sessions_dir / "capture-only" / "context-intelligence").mkdir(parents=True)
    (store.sessions_dir / "capture-only" / "context-intelligence" / "events.jsonl").write_text("")
    (store.sessions_dir / "0123456789abcdef-fedcba9876543210_child").mkdir()
    (store.sessions_dir / "0123456789abcdef-fedcba9876543210_child" / "transcript.jsonl").write_text("")
    assert store.list_ids() == ["saved-session"]
    assert not store.exists("capture-only")
    with pytest.raises(AgentError) as error:
        store.resume("capture-only")
    assert error.value.code == "not_found"
    lease = store.create_lease("capture-only")
    store.reserve("capture-only", FACTS, [], [])
    lease.close()
    assert store.list_ids() == ["capture-only", "saved-session"]
    assert (store.sessions_dir / "capture-only" / "context-intelligence" / "events.jsonl").exists()


def test_a_live_lease_blocks_resume_and_delete(tmp_path):
    store = SessionStore(tmp_path, "default")
    create(store)
    lease, _ = store.resume("saved-session")
    try:
        for operation in (store.resume, store.delete):
            with pytest.raises(AgentError) as error:
                operation("saved-session")
            assert error.value.code == "session_in_use"
        with pytest.raises(AgentError) as error:
            store.create_lease("saved-session")
        assert error.value.code == "already_exists"
    finally:
        lease.close()
    store.delete("saved-session")
    with pytest.raises(AgentError) as error:
        store.delete("saved-session")
    assert error.value.code == "not_found"


def test_failed_reservation_leaves_no_session_behind(tmp_path, monkeypatch):
    store = SessionStore(tmp_path, "default")
    lease = store.create_lease("saved-session")
    try:
        monkeypatch.setattr(SessionStore, "_write_turns", lambda *args: (_ for _ in ()).throw(OSError()))
        with pytest.raises(AgentError) as error:
            store.reserve("saved-session", FACTS, conversation(), [turn()])
        assert error.value.code == "internal_failed"
    finally:
        lease.close()
    assert not store.exists("saved-session")
    assert not store.session_dir("saved-session").exists()
    assert store.list_ids() == []

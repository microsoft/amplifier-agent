"""Tests for amplifier_agent_lib.session_store.SessionStore (A2 — CR-1).

Covers the minimal contract per Design §4.6:
- session_dir(session_id) → root/sessions/<id>
- save(session_id, transcript, metadata) writes transcript.jsonl + metadata.json
- load(session_id) returns (transcript, metadata) | None
"""

from __future__ import annotations

import json
from pathlib import Path

from amplifier_agent_lib.session_store import SessionStore


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    transcript = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]
    metadata = {"session_id": "abc", "bundle": "foundation", "turn_count": 1}

    store.save("abc", transcript, metadata)
    result = store.load("abc")

    assert result is not None
    loaded_transcript, loaded_metadata = result
    assert loaded_transcript == transcript
    assert loaded_metadata == metadata


def test_load_missing_session_returns_none(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    assert store.load("does-not-exist") is None


def test_save_creates_sessions_subdirectory(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    store.save("sess-1", [], {"foo": "bar"})

    expected = tmp_path / "sessions" / "sess-1"
    assert expected.is_dir()
    assert (expected / "transcript.jsonl").is_file()
    assert (expected / "metadata.json").is_file()


def test_transcript_persisted_as_jsonl(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    transcript = [
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "two"},
        {"role": "user", "content": "three"},
    ]
    store.save("s", transcript, {})

    transcript_file = tmp_path / "sessions" / "s" / "transcript.jsonl"
    raw = transcript_file.read_text(encoding="utf-8")
    lines = [line for line in raw.splitlines() if line]
    assert len(lines) == 3
    parsed = [json.loads(line) for line in lines]
    assert parsed == transcript


def test_empty_transcript_roundtrips(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    store.save("empty", [], {"only": "metadata"})

    result = store.load("empty")
    assert result is not None
    transcript, metadata = result
    assert transcript == []
    assert metadata == {"only": "metadata"}


def test_session_dir_returns_correct_path(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    assert store.session_dir("xyz") == tmp_path / "sessions" / "xyz"

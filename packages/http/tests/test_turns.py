"""Event JSON form, file-change diffs, and the face's approval handler."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
import json
from pathlib import Path

from amplifier_agent import (
    AgentError,
    ApprovalRequest,
    ApprovalResponse,
    Event,
    ToolCall,
    ToolCallEvent,
    ToolResolution,
    ToolResultEvent,
    TurnResult,
    Usage,
    UsageEntry,
)
from amplifier_agent_http._turns import (
    FILE_CHANGES,
    Approvals,
    TurnScope,
    current_scope,
    event_line,
    file_changes,
    snapshot,
    targets,
)


def envelope(sequence, kind, payload):
    return Event("turn-events/1", "session-0001", "turn-1", sequence, kind, payload)


def test_an_event_line_is_the_json_form_with_extensions():
    usage = Usage([UsageEntry("anthropic", "claude", 1, 2, cost={"USD": Decimal("0.0010")})])
    payload = TurnResult("failure", error=AgentError("provider_failed", "provider", "Down.", "Retry."), usage=usage)
    vars(payload)["org.example.trace"] = {"id": 1}
    event = envelope(3, "terminal", payload)
    event.at = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
    line = event_line(event)
    assert line.startswith(b"data: ")
    assert line.endswith(b"\n\n")
    data = json.loads(line.removeprefix(b"data: "))
    assert data["at"] == "2026-10-21T12:00:00Z"
    assert data["payload"]["usage"]["entries"][0]["cost"] == {"USD": "0.0010"}
    assert data["payload"]["error"]["remedy"] == "Retry."
    assert data["payload"]["org.example.trace"] == {"id": 1}


def test_targets_follow_each_tool_and_resolve_against_the_working_directory(tmp_path):
    patch = "*** Begin Patch\n*** Update File: a.txt\n*** Move to: b.txt\n@@\n-x\n+y\n*** Add File: /abs/c.txt\n+z\n*** End Patch"
    assert targets(ToolCall("c1", "apply_patch", "built-in", {"patch": patch}), tmp_path) == [
        tmp_path / "a.txt",
        tmp_path / "b.txt",
        Path("/abs/c.txt"),
    ]
    assert targets(ToolCall("c2", "write_file", "built-in", {"file_path": "../out.txt"}), tmp_path / "w") == [
        tmp_path / "out.txt"
    ]
    assert targets(ToolCall("c3", "write_file", "mcp", {"file_path": "x"}), tmp_path) == []
    assert targets(ToolCall("c4", "write_file", "built-in", {"file_path": "@bundle:x"}), tmp_path) == []
    assert targets(ToolCall("c5", "bash", "built-in", {"command": "touch x"}), tmp_path) == []


def test_file_changes_diff_text_and_skip_the_rest(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    edited, binary, same, outside = work / "edited.txt", work / "image.bin", work / "same.txt", tmp_path / "out.txt"
    edited.write_text("a\nb")
    binary.write_bytes(b"\x00\x01")
    same.write_text("same\n")
    before = snapshot([edited, binary, same, outside])
    edited.write_text("a\nc")
    binary.write_bytes(b"\x00\x02")
    outside.write_text("new\n")
    changes = file_changes(before, work)
    assert [(entry["path"], entry["change"]) for entry in changes] == [
        ("edited.txt", "modified"),
        (str(outside), "added"),
    ]
    assert changes[0]["patch"] == (
        "--- edited.txt\n+++ edited.txt\n@@ -1,2 +1,2 @@\n a\n-b\n\\ No newline at end of file\n"
        "+c\n\\ No newline at end of file\n"
    )
    assert (changes[0]["additions"], changes[0]["deletions"]) == (1, 1)


async def test_the_handler_asks_only_what_the_policy_allows_and_records_files(tmp_path):
    request = ApprovalRequest("r1", "Write notes.txt", "c1", "write_file")
    denying = Approvals("deny")
    scope = TurnScope(True, tmp_path)
    current_scope.set(scope)
    assert (await denying(request)).decision == "deny"
    assert denying.pending == {}

    approvals = Approvals("allow")
    asked = asyncio.ensure_future(approvals(request))
    await asyncio.sleep(0)
    assert list(approvals.pending) == ["r1"]
    assert approvals.answer("r1", ApprovalResponse("allow"))
    assert not approvals.answer("r1", ApprovalResponse("deny"))
    scope.observe(envelope(1, "tool_call", ToolCallEvent(ToolCall("c1", "write_file", "built-in", {"file_path": "n"}))))
    assert (await asked).decision == "allow"
    (tmp_path / "n").write_text("x\n")
    result = envelope(2, "tool_result", ToolResultEvent(ToolResolution("c1", "completed", "ok")))
    scope.observe(result)
    assert vars(result.payload)[FILE_CHANGES][0]["change"] == "added"


async def test_open_requests_settle_as_cancel_when_the_scope_closes(tmp_path):
    approvals = Approvals("allow")
    scope = TurnScope(True, tmp_path)
    current_scope.set(scope)
    asked = asyncio.ensure_future(approvals(ApprovalRequest("r2", "Run", "c2", "bash")))
    await asyncio.sleep(0)
    scope.cancel_open()
    assert (await asked).decision == "cancel"
    assert approvals.pending == {} == scope.open


async def test_without_a_scope_the_policy_answers():
    current_scope.set(None)
    assert (await Approvals("allow")(ApprovalRequest("r3", "Run", "c3", "write_file"))).decision == "allow"

"""Turns streamed as events, approvals by request, and file changes (http-turns.v1)."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
import json

from amplifier_agent import AgentOptions
from amplifier_agent_http import Settings, create_app
import httpx
import pytest

from tests.support.engine import provision
from tests.support.http_server import socket_server

TOKEN = "turns-test-token"
SESSION = "orchard-planning-01"
FILE_CHANGES = "com.microsoft.amplifier.file_changes"
FILE_TOOLS = ["read_file", "write_file", "edit_file", "apply_patch"]


@asynccontextmanager
async def face(monkeypatch, tmp_path, script, approvals="allow", tools=None):
    probe = provision(monkeypatch, script)
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    options = AgentOptions(
        provider="anthropic",
        model="claude-sonnet-5",
        tools=list(FILE_TOOLS if tools is None else tools),
        approvals=approvals,
        working_directory=work,
        sessions_directory=tmp_path / "sessions",
    )
    app = create_app(Settings(token=TOKEN), options)
    async with (
        app.router.lifespan_context(app),
        socket_server(app, lifespan="off") as url,
        httpx.AsyncClient(base_url=url, headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30) as client,
    ):
        yield app, client, work, probe


def turn(text="Work on the files.", **fields):
    return {"model": "amplifier", "messages": [{"role": "user", "content": text}], **fields}


def call(name, **arguments):
    return {"tool": {"name": name, "arguments": arguments}}


async def events(response):
    """Each event of a turns response as JSON, checking the server-sent event framing."""
    async for line in response.aiter_lines():
        if line:
            assert line.startswith("data: "), line
            yield json.loads(line.removeprefix("data: "))


async def run(client, body, answer=None, headers=None):
    """Every event of one turn, answering each approval_request with `answer`'s decision when given."""
    seen = []
    async with client.stream("POST", "/v1/amplifier/turns", json=body, headers=headers) as response:
        assert response.status_code == 200, await response.aread()
        assert response.headers["content-type"].startswith("text/event-stream")
        async for event in events(response):
            seen.append(event)
            if event["type"] == "approval_request" and answer is not None:
                reply = await client.post(
                    f"/v1/amplifier/approvals/{event['payload']['request']['request_id']}", json=answer
                )
                assert reply.status_code == 204, reply.text
    return seen


def of(seen, kind):
    return [event for event in seen if event["type"] == kind]


async def test_a_turn_streams_every_event_and_ends_after_terminal(monkeypatch, tmp_path):
    async with face(monkeypatch, tmp_path, [{"chunks": ["Hello", " there"], "text": "Hello there"}], tools=[]) as (
        _,
        client,
        _,
        _,
    ):
        seen = await run(client, turn("Hi."))
    assert [event["sequence"] for event in seen] == list(range(1, len(seen) + 1))
    assert seen[0]["type"] == "turn_started"
    assert seen[-1]["type"] == "terminal"
    assert len(of(seen, "terminal")) == 1
    assert seen[-1]["payload"]["state"] == "success"
    assert "".join(part["text"] for part in seen[-1]["payload"]["content"]) == "Hello there"
    assert all(event["contract_version"] == "turn-events/1" for event in seen)
    assert "".join(part["text"] for event in of(seen, "output_delta") for part in event["payload"]["content"]) == (
        "Hello there"
    )
    for event in seen:
        assert event["at"] is None or datetime.fromisoformat(event["at"])
    usage = seen[-1]["payload"]["usage"]["entries"][0]
    assert usage["tokens_in"] == 7


@pytest.mark.parametrize(
    ("fields", "param"),
    [
        ({"stream": True}, "stream"),
        ({"stream_options": {"include_usage": True}}, "stream_options"),
        ({"approvals": "always"}, "approvals"),
        ({"temperature": 0}, "temperature"),
    ],
)
async def test_a_turn_refuses_streaming_fields_and_unknown_approvals(monkeypatch, tmp_path, fields, param):
    async with face(monkeypatch, tmp_path, [{"text": "Unused"}]) as (_, client, _, _):
        response = await client.post("/v1/amplifier/turns", json=turn(**fields))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_input"
    assert response.json()["error"]["param"] == param


async def test_turns_and_approvals_require_the_token(monkeypatch, tmp_path):
    async with face(monkeypatch, tmp_path, [{"text": "Unused"}]) as (_, client, _, _):
        turns = await client.post("/v1/amplifier/turns", json=turn(), headers={"Authorization": "Bearer wrong"})
        answer = await client.post(
            "/v1/amplifier/approvals/abc", json={"decision": "allow"}, headers={"Authorization": "Bearer wrong"}
        )
    assert turns.status_code == answer.status_code == 401


async def test_an_allowed_ask_runs_the_effect_and_reports_the_added_file(monkeypatch, tmp_path):
    script = [call("write_file", file_path="notes.txt", content="one\ntwo\n"), {"text": "Written."}]
    async with face(monkeypatch, tmp_path, script) as (app, client, work, _):
        seen = await run(client, turn(approvals="ask"), answer={"decision": "allow"})
        assert app.state.approvals.pending == {}
    assert (work / "notes.txt").read_text() == "one\ntwo\n"
    request = of(seen, "approval_request")[0]["payload"]["request"]
    decision = of(seen, "approval_decision")[0]["payload"]["resolution"]
    assert decision["request_id"] == request["request_id"]
    assert decision["decision"] == "allow"
    assert seen.index(of(seen, "tool_call")[0]) < seen.index(of(seen, "approval_request")[0])
    result = of(seen, "tool_result")[0]["payload"]
    assert result[FILE_CHANGES] == [
        {
            "path": "notes.txt",
            "change": "added",
            "patch": "--- /dev/null\n+++ notes.txt\n@@ -0,0 +1,2 @@\n+one\n+two\n",
            "additions": 2,
            "deletions": 0,
        }
    ]
    assert seen[-1]["payload"]["state"] == "success"


async def test_a_denied_ask_returns_to_the_model_and_the_turn_continues(monkeypatch, tmp_path):
    script = [call("write_file", file_path="notes.txt", content="one\n" * 2000), {"text": "Not written."}]
    async with face(monkeypatch, tmp_path, script) as (_, client, work, _):
        seen = await run(client, turn(approvals="ask"), answer={"decision": "deny", "reason": "Not now."})
    assert not (work / "notes.txt").exists()
    call_event = of(seen, "tool_call")[0]["payload"]["call"]
    request = of(seen, "approval_request")[0]["payload"]["request"]
    assert request["arguments"] == call_event["arguments"] == {"file_path": "notes.txt", "content": "one\n" * 2000}
    assert len(request["summary"]) <= 4096
    resolution = of(seen, "approval_decision")[0]["payload"]["resolution"]
    assert (resolution["decision"], resolution["reason"]) == ("deny", "Not now.")
    result = of(seen, "tool_result")[0]["payload"]
    assert FILE_CHANGES not in result
    assert result["resolution"]["outcome"] == "cancelled"
    assert result["resolution"]["error"]["code"] == "approval_denied"
    assert "Not now." in result["resolution"]["error"]["message"]
    assert seen[-1]["payload"]["state"] == "success"
    assert seen[-1]["payload"].get("error") is None


async def test_a_cancelled_ask_settles_the_turn(monkeypatch, tmp_path):
    script = [call("write_file", file_path="notes.txt", content="one\n"), {"text": "Unused"}]
    async with face(monkeypatch, tmp_path, script) as (_, client, work, _):
        seen = await run(client, turn(approvals="ask"), answer={"decision": "cancel", "reason": "Not now."})
    assert not (work / "notes.txt").exists()
    resolution = of(seen, "approval_decision")[0]["payload"]["resolution"]
    assert (resolution["decision"], resolution["reason"]) == ("cancel", "Not now.")
    assert FILE_CHANGES not in of(seen, "tool_result")[0]["payload"]
    assert seen[-1]["payload"]["state"] == "cancelled"
    assert seen[-1]["payload"]["error"]["code"] == "approval_cancelled"


async def test_a_static_deny_is_never_asked(monkeypatch, tmp_path):
    script = [call("write_file", file_path="notes.txt", content="one\n"), {"text": "Not written."}]
    async with face(monkeypatch, tmp_path, script, approvals="deny") as (app, client, work, _):
        # No answer is sent, so a request that waited would never settle.
        seen = await asyncio.wait_for(run(client, turn(approvals="ask")), 20)
        assert app.state.approvals.pending == {}
    assert not (work / "notes.txt").exists()
    assert of(seen, "approval_decision")[0]["payload"]["resolution"]["decision"] == "deny"
    assert of(seen, "tool_result")[0]["payload"]["resolution"]["error"]["code"] == "approval_denied"
    assert seen[-1]["payload"]["state"] == "success"


async def test_without_ask_the_static_policy_decides(monkeypatch, tmp_path):
    script = [call("write_file", file_path="notes.txt", content="one\n"), {"text": "Written."}]
    async with face(monkeypatch, tmp_path, script) as (_, client, work, _):
        seen = await asyncio.wait_for(run(client, turn()), 20)
    assert (work / "notes.txt").exists()
    assert of(seen, "approval_decision")[0]["payload"]["resolution"]["decision"] == "allow"
    assert of(seen, "tool_result")[0]["payload"][FILE_CHANGES][0]["change"] == "added"


@pytest.mark.parametrize(
    "body",
    [
        {"decision": "maybe"},
        {"reason": "Missing decision."},
        {"decision": "allow", "reason": 3},
        {"decision": "allow", "scope": "all"},
        ["allow"],
    ],
)
async def test_an_invalid_answer_is_refused(monkeypatch, tmp_path, body):
    async with face(monkeypatch, tmp_path, [{"text": "Unused"}]) as (_, client, _, _):
        response = await client.post("/v1/amplifier/approvals/abc", json=body)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_input"


async def test_an_unknown_or_settled_request_is_not_found(monkeypatch, tmp_path):
    script = [call("write_file", file_path="notes.txt", content="one\n"), {"text": "Written."}]
    async with face(monkeypatch, tmp_path, script) as (_, client, _, _):
        unknown = await client.post("/v1/amplifier/approvals/no-such-request", json={"decision": "allow"})
        seen = await run(client, turn(approvals="ask"), answer={"decision": "allow"})
        request_id = of(seen, "approval_request")[0]["payload"]["request"]["request_id"]
        settled = await client.post(f"/v1/amplifier/approvals/{request_id}", json={"decision": "deny"})
    for response in (unknown, settled):
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


async def test_closing_the_connection_cancels_open_requests_and_keeps_the_session(monkeypatch, tmp_path):
    script = [call("write_file", file_path="notes.txt", content="one\n"), {"text": "Unused"}, {"text": "Again."}]
    headers = {"Amplifier-Session-Id": SESSION}
    async with face(monkeypatch, tmp_path, script) as (app, client, work, _):
        async with client.stream("POST", "/v1/amplifier/turns", json=turn(approvals="ask"), headers=headers) as (
            response
        ):
            async for event in events(response):
                if event["type"] == "approval_request":
                    request_id = event["payload"]["request"]["request_id"]
                    break
        async with asyncio.timeout(10):
            while app.state.approvals.pending:
                await asyncio.sleep(0.02)
        late = await client.post(f"/v1/amplifier/approvals/{request_id}", json={"decision": "allow"})
        assert late.status_code == 404
        assert not (work / "notes.txt").exists()
        # The session remains, so the next request finds it free.
        async with asyncio.timeout(10):
            while True:
                follow = await client.post("/v1/amplifier/turns", json=turn("Hello again."))
                if follow.status_code != 409:
                    break
                await asyncio.sleep(0.05)
    assert follow.status_code == 200


async def test_a_session_continues_across_turns(monkeypatch, tmp_path):
    script = [{"text": "Noted."}, {"text": "Orchard."}]
    headers = {"Amplifier-Session-Id": SESSION}
    async with face(monkeypatch, tmp_path, script, tools=[]) as (_, client, _, probe):
        first = await run(client, turn("Orchard."), headers=headers)
        second_body = {
            "model": "amplifier",
            "messages": [
                {"role": "user", "content": "Orchard."},
                {"role": "assistant", "content": "Noted."},
                {"role": "user", "content": "Name?"},
            ],
        }
        second = await run(client, second_body, headers=headers)
    assert first[0]["session_id"] == second[0]["session_id"] == SESSION
    assert first[0]["payload"]["continuation"] == "fresh"
    assert second[0]["payload"]["continuation"] == "resumed"
    assert first[0]["turn_id"] != second[0]["turn_id"]
    assert second[-1]["payload"]["state"] == "success"
    assert len(probe.requests) == 2
    assert "Noted." in json.dumps(probe.requests[1]["messages"])


async def test_edit_file_reports_the_modified_file(monkeypatch, tmp_path):
    script = [
        call("read_file", file_path="poem.txt"),
        call("edit_file", file_path="poem.txt", old_string="blue", new_string="green"),
        {"text": "Edited."},
    ]
    async with face(monkeypatch, tmp_path, script) as (_, client, work, _):
        (work / "poem.txt").write_text("roses\nblue\nend\n")
        seen = await run(client, turn())
    results = of(seen, "tool_result")
    assert FILE_CHANGES not in results[0]["payload"]
    changes = results[1]["payload"][FILE_CHANGES]
    assert [(entry["path"], entry["change"], entry["additions"], entry["deletions"]) for entry in changes] == [
        ("poem.txt", "modified", 1, 1)
    ]
    assert "-blue\n+green\n" in changes[0]["patch"]
    assert [event["sequence"] for event in seen] == list(range(1, len(seen) + 1))


async def test_apply_patch_reports_each_file_it_changed_in_order(monkeypatch, tmp_path):
    patch = "\n".join(
        [
            "*** Begin Patch",
            "*** Add File: docs/new.md",
            "+# New",
            "*** Update File: keep.txt",
            "@@",
            "-old",
            "+new",
            "*** Delete File: gone.txt",
            "*** End Patch",
        ]
    )
    script = [call("apply_patch", patch=patch), {"text": "Patched."}]
    # A model is offered one editor; naming apply_patch alone offers it to every model.
    async with face(monkeypatch, tmp_path, script, tools=["apply_patch"]) as (_, client, work, _):
        (work / "keep.txt").write_text("old\n")
        (work / "gone.txt").write_text("bye\n")
        seen = await run(client, turn())
    changes = of(seen, "tool_result")[0]["payload"][FILE_CHANGES]
    assert [(entry["path"], entry["change"]) for entry in changes] == [
        ("docs/new.md", "added"),
        ("keep.txt", "modified"),
        ("gone.txt", "deleted"),
    ]
    assert changes[2]["patch"] == "--- gone.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-bye\n"


async def test_a_file_that_is_not_text_has_no_entry(monkeypatch, tmp_path):
    script = [
        call("edit_file", file_path="data.bin", old_string="a", new_string="b"),
        {"text": "Edited."},
    ]
    async with face(monkeypatch, tmp_path, script) as (_, client, work, _):
        (work / "data.bin").write_bytes(b"a\x00\xff")
        seen = await run(client, turn())
    assert FILE_CHANGES not in of(seen, "tool_result")[0]["payload"]


async def test_an_unanswered_request_expires_at_the_deadline(monkeypatch, tmp_path):
    from amplifier_agent_engine._engine import effects

    monkeypatch.setattr(effects, "APPROVAL_TIMEOUT_SECONDS", 0.3)
    script = [call("write_file", file_path="notes.txt", content="one\n"), {"text": "Unused"}]
    async with face(monkeypatch, tmp_path, script) as (_, client, work, _):
        seen = await asyncio.wait_for(run(client, turn(approvals="ask")), 20)
        request_id = of(seen, "approval_request")[0]["payload"]["request"]["request_id"]
        expired = await client.post(f"/v1/amplifier/approvals/{request_id}", json={"decision": "allow"})
    assert not (work / "notes.txt").exists()
    assert of(seen, "approval_decision")[0]["payload"]["resolution"]["decision"] == "timeout"
    assert seen[-1]["payload"]["error"]["code"] == "approval_timeout"
    assert expired.status_code == 404

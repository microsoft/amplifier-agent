"""Instance owns matching rules only after explicit ACP negotiation."""

import asyncio
from dataclasses import replace
import json

from acp import RequestError
from acp.schema import AllowedOutcome, RequestPermissionResponse, TextContentBlock
from amplifier_agent import ApprovalRequest, ReusableApproval, ToolCall
from amplifier_agent_acp._approval_policy import NAMESPACE, PRODUCER, instance_owner, metadata
from amplifier_agent_acp._approvals import Approvals
from amplifier_agent_acp._updates import View
import pytest

from tests.support.engine import provision

PROFILE = {"version": 1, "owner": "instance", "instanceId": "b1fe804a-9bb6-47ae-87db-1ccf4820fa7b"}
SCOPE = ReusableApproval(1, PRODUCER, "a" * 64, "This exact prepared invocation.", "request")


async def test_initialize_negotiates_instance_owned_reusable_requests(connect):
    async with connect() as (connection, _recorder):
        result = await connection.initialize(protocol_version=1, **{NAMESPACE: PROFILE})
        with pytest.raises(RequestError):
            await connection.initialize(
                protocol_version=1, **{NAMESPACE: {**PROFILE, "instanceId": "a1fe804a-9bb6-47ae-87db-1ccf4820fa7b"}}
            )
    assert result.field_meta == {
        NAMESPACE: {"version": 1, "owner": "instance", "producer": PRODUCER, "reusableRequests": True}
    }


@pytest.mark.parametrize(
    "profile",
    [
        None,
        {},
        {**PROFILE, "version": True},
        {**PROFILE, "version": 2},
        {**PROFILE, "owner": "agent"},
        {**PROFILE, "instanceId": "not-an-id"},
        {**PROFILE, "instanceId": PROFILE["instanceId"].upper()},
    ],
)
def test_invalid_negotiation_does_not_transfer_ownership(profile):
    assert instance_owner({NAMESPACE: profile}) is None


@pytest.mark.parametrize(
    "changed",
    [
        {"version": True},
        {"version": 2},
        {"producer": "other"},
        {"key": "A" * 64},
        {"authority_lifetime": "session"},
        {"description": "bad\ntext"},
        {"description": "\ud800"},
        {"description": "x" * 513},
    ],
)
def test_malformed_scope_never_projects_metadata(changed):
    assert metadata(ApprovalRequest("request", "Run", "call", "bash", {}, replace(SCOPE, **changed))) is None


@pytest.mark.parametrize("policy", [None, "allow", "deny"])
async def test_negotiated_requests_preserve_native_static_allow_and_deny(
    connect, isolated_host, monkeypatch, tmp_path, policy
):
    isolated_host.write_text(json.dumps({"approvals": policy} if policy else {}))
    command = "printf approved >> receipt.txt"
    provision(
        monkeypatch,
        [
            {"tool": {"name": "bash", "arguments": {"command": command}}},
            {"tool": {"name": "bash", "arguments": {"command": command}}},
            {"text": "Done"},
        ],
    )
    async with connect("allow_once") as (connection, recorder):
        await connection.initialize(protocol_version=1, **{NAMESPACE: PROFILE})
        opened = await connection.new_session(cwd=str(tmp_path))
        modes = [option for option in opened.config_options if option.id == "approvals"]
        assert not modes if policy else modes[0].current_value == "allow-reads"
        await connection.prompt(
            session_id=opened.session_id, prompt=[TextContentBlock(type="text", text="Record receipts.")]
        )
    requests = [message["params"] for message in recorder.wire if message.get("method") == "session/request_permission"]
    if policy == "deny":
        assert not requests
        assert not (tmp_path / "receipt.txt").exists()
    elif policy == "allow":
        assert not requests
        assert (tmp_path / "receipt.txt").read_text() == "approvedapproved"
    else:
        assert len(requests) == 2  # The face never caches an Instance-owned answer.
        assert (tmp_path / "receipt.txt").read_text() == "approvedapproved"
        scopes = [request["toolCall"]["_meta"][NAMESPACE] for request in requests]
        assert scopes[0] == scopes[1]
        assert scopes[0]["producer"] == PRODUCER
        assert scopes[0]["authorityLifetime"] == "request"
        assert scopes[0]["allowOptionId"] == "allow_once"
        assert all(
            [(item["optionId"], item["kind"]) for item in request["options"]]
            == [("allow_once", "allow_once"), ("reject_once", "reject_once")]
            for request in requests
        )


async def test_instance_owned_requests_ignore_session_rules_and_forged_persistent_choice(tmp_path):
    view, asked = View(tmp_path, False), []
    view.sightings.see(ToolCall("call", "bash", "built-in", {"command": "printf fixture"}))

    async def send(update):
        pass

    async def ask(call, options):
        asked.append((call, options))
        return RequestPermissionResponse(outcome=AllowedOutcome(outcome="selected", option_id="allow_always"))

    approvals = Approvals(None, view, send, ask, [tmp_path], instance_owned=True)
    approvals.mode = "ask"
    approvals.rules.add(("bash", "printf fixture"))
    request = ApprovalRequest("request", "Run", "call", "bash", {"command": "printf fixture"}, SCOPE)
    response = await approvals(request)
    assert response.decision == "deny"
    assert len(asked) == 1
    assert asked[0][0].field_meta == metadata(request)
    assert [option.option_id for option in asked[0][1]] == ["allow_once", "reject_once"]


async def test_instance_cancel_settles_held_permission_without_late_allow(tmp_path):
    view, entered = View(tmp_path, False), asyncio.Event()
    view.sightings.see(ToolCall("call", "bash", "built-in", {}))

    async def send(update):
        pass

    async def ask(call, options):
        entered.set()
        await asyncio.Event().wait()
        return RequestPermissionResponse(outcome=AllowedOutcome(outcome="selected", option_id="allow_once"))

    approvals = Approvals(None, view, send, ask, [tmp_path], instance_owned=True)
    pending = asyncio.create_task(approvals(ApprovalRequest("request", "Run", "call", "bash", {}, SCOPE)))
    await asyncio.wait_for(entered.wait(), 2)
    approvals.cancel_open()
    assert (await asyncio.wait_for(pending, 2)).decision == "cancel"
    assert not approvals.open


@pytest.mark.parametrize(("mode", "asks"), [("ask", 1), ("allow-reads", 0), ("allow-all", 0)])
async def test_negotiated_native_permissions_still_allow_already_authorized_reads(
    connect, monkeypatch, tmp_path, mode, asks
):
    (tmp_path / "notes.txt").write_text("Fixture notes")
    provision(monkeypatch, [{"tool": {"name": "read_file", "arguments": {"file_path": "notes.txt"}}}, {"text": "Done"}])
    async with connect("allow_once") as (connection, recorder):
        await connection.initialize(protocol_version=1, **{NAMESPACE: PROFILE})
        opened = await connection.new_session(cwd=str(tmp_path))
        changed = await connection.set_config_option(config_id="approvals", session_id=opened.session_id, value=mode)
        assert next(option.current_value for option in changed.config_options if option.id == "approvals") == mode
        response = await connection.prompt(
            session_id=opened.session_id, prompt=[TextContentBlock(type="text", text="Read the notes.")]
        )
    assert response.stop_reason == "end_turn"
    requests = [message["params"] for message in recorder.wire if message.get("method") == "session/request_permission"]
    assert len(requests) == asks
    for request in requests:
        assert NAMESPACE not in request["toolCall"].get("_meta", {})
        assert [item["optionId"] for item in request["options"]] == ["allow_once", "reject_once"]

from amplifier_agent_engine._records import ApprovalRequest, ConversationMessage, ImagePart, TextPart, TurnInput
from amplifier_agent_engine._runtime import codec


def test_content_part_union_decodes_by_type():
    data = {
        "content": [
            {"type": "text", "text": "Look"},
            {"type": "image", "media_type": "image/png", "data": "iVBORw0KGgo="},
        ],
        "history": [
            {
                "role": "user",
                "content": [{"type": "image", "media_type": "image/gif", "data": "R0lGODlh"}],
            }
        ],
    }
    decoded = codec.record(TurnInput, data)
    assert decoded == TurnInput(
        [TextPart("Look"), ImagePart(media_type="image/png", data="iVBORw0KGgo=")],
        history=[ConversationMessage("user", [ImagePart(media_type="image/gif", data="R0lGODlh")])],
    )
    assert [type(part) for part in decoded.content] == [TextPart, ImagePart]
    assert codec.loads(codec.dumps(decoded)) == data


def test_approval_request_carries_exact_arguments_and_omits_them_without_a_call():
    arguments = {"payload": "x" * 10_000, "api_key": "codec-credential", "nested": [None, {"count": 2**60 + 1}]}
    request = ApprovalRequest("request", "Run.", "call", "publish", arguments)
    data = codec.loads(codec.dumps(request))
    assert data["arguments"] == arguments
    assert codec.record(ApprovalRequest, data) == request
    bare = codec.loads(codec.dumps(ApprovalRequest("request", "Run.")))
    assert bare == {"request_id": "request", "summary": "Run."}
    assert codec.record(ApprovalRequest, bare).arguments is None

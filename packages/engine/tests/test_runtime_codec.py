from amplifier_agent_engine._records import ConversationMessage, ImagePart, TextPart, TurnInput
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

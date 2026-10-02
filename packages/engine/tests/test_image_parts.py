from types import SimpleNamespace

from amplifier_agent_engine._engine.images import (
    content_block,
    conversation_inputs,
    copilot_drops_images,
    described_blocks,
    refuses_images,
    rejects_request,
    reported_capabilities,
    request_refusal,
    tool_result_images,
)
from amplifier_agent_engine._engine.provider_inputs import response_roles
from amplifier_agent_engine._records import (
    AgentError,
    ConversationMessage,
    ImagePart,
    TextPart,
    TurnInput,
    TurnRecord,
    TurnResult,
)
from amplifier_core.llm_errors import AuthenticationError, InvalidRequestError, RateLimitError
from amplifier_module_provider_anthropic import AnthropicProvider
from amplifier_module_provider_openai import OpenAIProvider
import pytest

PNG = "iVBORw0KGgo="


def test_content_blocks_are_core_blocks():
    assert content_block(TextPart("Look")) == {"type": "text", "text": "Look"}
    assert content_block(ImagePart(media_type="image/png", data=PNG)) == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": PNG},
    }


def test_responses_conversion_keeps_image_order():
    message = {
        "role": "user",
        "content": [
            {"type": "text", "text": "Before"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": PNG}},
            {"type": "text", "text": "After"},
        ],
    }

    def convert(messages):
        return [{"role": item["role"], "content": item["content"]} for item in messages]

    assert response_roles([message], convert) == [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "Before"},
                {"type": "input_image", "image_url": f"data:image/png;base64,{PNG}"},
                {"type": "input_text", "text": "After"},
            ],
        }
    ]


@pytest.mark.parametrize(
    ("provider", "model", "vision"),
    [
        (AnthropicProvider, "claude-sonnet-5", True),
        (OpenAIProvider, "gpt-5.2", True),
        (OpenAIProvider, "o3-mini", False),
        (OpenAIProvider, "a-deployment-name", None),
    ],
)
async def test_capability_tables_answer_without_listing(provider, model, vision):
    instance = provider.__new__(provider)
    capabilities = await reported_capabilities(instance, model)
    assert (None if capabilities is None else "vision" in capabilities) is vision


async def test_listing_reports_only_the_listed_model():
    async def listing():
        return [SimpleNamespace(id="listed", capabilities=["tools", "vision"])]

    async def failing():
        raise RuntimeError("offline")

    assert await reported_capabilities(SimpleNamespace(list_models=listing), "listed") == {"tools", "vision"}
    assert await reported_capabilities(SimpleNamespace(list_models=listing), "unlisted") is None
    assert await reported_capabilities(SimpleNamespace(list_models=failing), "listed") is None
    assert await reported_capabilities(SimpleNamespace(), "listed") is None


class StatusError(Exception):
    status_code = 400


def test_only_request_refusals_name_images():
    assert rejects_request(InvalidRequestError("bad request", status_code=400))
    assert rejects_request(StatusError("bad request"))
    assert not rejects_request(AuthenticationError("denied", status_code=401))
    assert not rejects_request(RateLimitError("slow down", status_code=429))
    assert not rejects_request(RuntimeError("other"))


@pytest.mark.parametrize(
    "message",
    [
        "Invalid content type. image_url is only supported by certain models.",
        "Error code: 400 - {'error': {'message': 'Qwen/Qwen3-8B is not a multimodal model'}}",
        "registry.ollama.ai/library/llama3:latest does not support images",
        "image input is not supported - hint: if this is unexpected, you may need to provide the mmproj",
        "Model does not support images. Please use a model that does.",
        "Failed to deserialize the JSON body: unknown variant `image_url`, expected `text`",
        "At most 0 image(s) may be provided in one request.",
        "Images are not supported for this model",
        "This model does not support vision",
        "Vision is not supported by this endpoint",
        "Unsupported content type 'image' for this model",
    ],
)
def test_image_capability_refusals_are_recognized(message):
    assert refuses_images(message)


@pytest.mark.parametrize(
    "message",
    [
        "max_tokens is too large: 900000",
        "messages.0.content.1.image.source.base64: image exceeds 5 MB maximum",
        "Image too large: 9000 pixels exceeds 8000 per side",
        "invalid request body, failed to validate schema: image_url media type image/bmp",
        "Invalid content type for field metadata",
    ],
)
def test_other_refusals_are_not_image_capability_refusals(message):
    assert not refuses_images(message)


def test_described_blocks_replace_only_images_in_order():
    content = [
        {"type": "text", "text": "Before"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": PNG}},
        {"type": "text", "text": "After"},
    ]
    assert described_blocks(content) == [
        {"type": "text", "text": "Before"},
        {"type": "text", "text": "[image: image/png, 8 bytes]"},
        {"type": "text", "text": "After"},
    ]
    assert described_blocks("Plain") == "Plain"


def test_turns_refused_for_images_no_longer_hold_them():
    image = TurnInput([ImagePart(media_type="image/png", data=PNG)])
    refused = AgentError("image_unsupported", "input", "Refused.", "Choose another model.")
    failed = AgentError("provider_failed", "provider", "Failed.", "Retry.")
    history = [
        TurnRecord("kept", image, TurnResult("success")),
        TurnRecord("refused", image, TurnResult("failure", error=refused)),
        TurnRecord("failed", image, TurnResult("failure", error=failed)),
    ]
    assert conversation_inputs(history) == [image, image]
    assert not copilot_drops_images(TurnInput([TextPart("Again")]), history[1:2])


IMAGE = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": PNG}}


@pytest.mark.parametrize(
    ("provider", "model", "supported"),
    [
        ("anthropic", "claude-sonnet-5", True),
        ("openai", "gpt-5", True),
        ("azure-openai", "gpt-5", True),
        ("openai", "o3-mini", False),
        ("gemini", "gemini-2.5-flash", False),
        ("chat-completions", "local", False),
        ("ollama", "llava", False),
        ("vllm", "served", False),
        ("openai-chatgpt", "gpt-5", False),
        ("github-copilot", "claude-sonnet-4", False),
    ],
)
def test_tool_result_image_support_follows_each_provider_conversion(provider, model, supported):
    assert tool_result_images(provider, model) is supported
    request = SimpleNamespace(
        messages=[
            {"role": "user", "content": "Look"},
            {"role": "tool", "tool_call_id": "call", "content": [{"type": "text", "text": "Caption"}, IMAGE]},
        ]
    )
    refusal = request_refusal(provider, model, request)
    assert (refusal is None) is supported
    if refusal is not None:
        assert refusal.code == "image_unsupported"
        assert refusal.remedy


def test_copilot_refuses_images_outside_the_newest_user_message():
    newest = SimpleNamespace(messages=[{"role": "user", "content": "Before"}, {"role": "user", "content": [IMAGE]}])
    earlier = SimpleNamespace(
        messages=[
            {"role": "user", "content": [IMAGE]},
            {"role": "assistant", "content": "Seen"},
            {"role": "user", "content": "Again"},
        ]
    )
    assert request_refusal("github-copilot", "claude-sonnet-4", newest) is None
    refusal = request_refusal("github-copilot", "claude-sonnet-4", earlier)
    assert refusal is not None
    assert refusal.code == "image_unsupported"
    assert "newest user message" in refusal.message


def test_copilot_images_known_before_the_stream():
    image = ImagePart(media_type="image/png", data=PNG)
    current = TurnInput([TextPart("Look"), image])
    assert not copilot_drops_images(current, [])
    assert not copilot_drops_images(TurnInput([], history=[ConversationMessage("user", [image])]), [])
    assert copilot_drops_images(TurnInput([TextPart("Look")], history=[ConversationMessage("user", [image])]), [])
    assert copilot_drops_images(
        TurnInput([], history=[ConversationMessage("user", [image]), ConversationMessage("user", [TextPart("And")])]),
        [],
    )
    earlier = TurnRecord("turn", current, TurnResult("success"))
    assert copilot_drops_images(TurnInput([TextPart("Again")]), [earlier])

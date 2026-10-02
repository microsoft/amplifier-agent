"""Regression coverage for the bounded tool-result text ingress guard."""

from __future__ import annotations

import copy
import logging
from types import SimpleNamespace

import pytest
from amplifier_core import TextBlock, ToolResult

from amplifier_module_context_simple import SimpleContextManager, mount


DEFAULT_CAP = 128 * 1024
MARKER_PREFIX = "[tool-result truncated at ingress:"
SAFE_RETRIEVAL_NOTICE = (
    "retrieve missing content using narrower read/query parameters; do not repeat "
    "state-changing actions just to recover output."
)


def _encoded_text_bytes(text: str) -> int:
    return len(text.encode("utf-8", errors="replace"))


class _Provider64k:
    def get_model_info(self) -> SimpleNamespace:
        return SimpleNamespace(context_window=64_000, max_output_tokens=2_000)


class _RecordingHooks:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def emit(self, event: str, data: dict) -> None:
        self.events.append((event, dict(data)))


class _FailingHooks:
    async def emit(self, event: str, data: dict) -> None:
        raise RuntimeError("test hook failure")


class _Coordinator:
    def __init__(self) -> None:
        self.hooks = None
        self.mounted: dict[str, object] = {}

    async def mount(self, kind: str, instance: object) -> None:
        self.mounted[kind] = instance


async def _add_paired_tool_result(
    context: SimpleContextManager, content: str, **extra: object
) -> None:
    await context.add_message({"role": "user", "content": "inspect the local result"})
    await context.add_message(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-ingress-1", "tool": "local_tool", "arguments": {}}],
        }
    )
    await context.add_message(
        {
            "role": "tool",
            "name": "local_tool",
            "tool_call_id": "call-ingress-1",
            "content": content,
            **extra,
        }
    )


@pytest.mark.asyncio
async def test_default_caps_the_real_oversized_protected_tool_result_before_compaction():
    """The original 3.16 MB reproduction fits before request compaction runs."""
    serialized = ToolResult(success=True, output="x" * 3_163_313).get_serialized_output()
    assert isinstance(serialized, str)
    context = SimpleContextManager(
        compaction_notice_enabled=False,
        protected_tool_results=5,
    )

    await _add_paired_tool_result(context, serialized)

    stored = await context.get_messages()
    stored_tool = stored[-1]
    view = await context.get_messages_for_request(provider=_Provider64k())
    view_tool = next(message for message in view if message.get("role") == "tool")

    assert len(stored_tool["content"].encode("utf-8")) == DEFAULT_CAP
    assert stored_tool["content"].count(MARKER_PREFIX) == 1
    assert view_tool["tool_call_id"] == "call-ingress-1"
    assert view[1]["tool_calls"][0]["id"] == view_tool["tool_call_id"]
    assert context._estimate_tokens(view) < 58_904
    assert context._last_compaction_stats is None


@pytest.mark.asyncio
@pytest.mark.parametrize("output", [{"result": "x" * 140_000}, ["x" * 140_000]])
async def test_serialized_dict_and_list_tool_outputs_are_capped_as_text(output):
    serialized = ToolResult(success=True, output=output).get_serialized_output()
    assert isinstance(serialized, str)
    context = SimpleContextManager()

    await context.add_message({"role": "tool", "content": serialized})

    stored = (await context.get_messages())[0]["content"]
    assert len(stored.encode("utf-8")) == DEFAULT_CAP
    assert stored.count(MARKER_PREFIX) == 1


@pytest.mark.asyncio
async def test_string_byte_boundary_unicode_and_explicit_larger_override():
    context = SimpleContextManager()
    exact = "x" * DEFAULT_CAP
    one_byte_over = exact + "x"
    unicode_over = "🙂" * ((DEFAULT_CAP // len("🙂".encode("utf-8"))) + 1)

    await context.add_message({"role": "tool", "content": exact})
    await context.add_message({"role": "tool", "content": one_byte_over})
    await context.add_message({"role": "tool", "content": unicode_over})

    unchanged, clipped, unicode_clipped = await context.get_messages()
    assert unchanged["content"] == exact
    assert clipped["content"].count(MARKER_PREFIX) == 1
    assert len(clipped["content"].encode("utf-8")) == DEFAULT_CAP
    assert unicode_clipped["content"].encode("utf-8").decode("utf-8") == unicode_clipped["content"]
    assert len(unicode_clipped["content"].encode("utf-8")) <= DEFAULT_CAP
    assert unicode_clipped["content"].count(MARKER_PREFIX) == 1

    larger = SimpleContextManager(max_tool_result_bytes=DEFAULT_CAP + 1)
    await larger.add_message({"role": "tool", "content": one_byte_over})
    assert (await larger.get_messages())[0]["content"] == one_byte_over


@pytest.mark.asyncio
async def test_lone_surrogates_do_not_crash_and_only_oversize_clipping_normalizes():
    under_cap = "prefix\ud800suffix"
    over_cap = "prefix\ud800" + ("x" * DEFAULT_CAP)
    context = SimpleContextManager()

    await context.add_message({"role": "tool", "content": under_cap})
    await context.add_message({"role": "tool", "content": over_cap})
    await context.add_message(
        {"role": "tool", "content": [{"type": "text", "text": under_cap}]}
    )
    await context.add_message(
        {"role": "tool", "content": [{"type": "text", "text": over_cap}]}
    )

    string_under, string_over, block_under, block_over = await context.get_messages()
    assert string_under["content"] == under_cap
    assert block_under["content"][0]["text"] == under_cap
    assert string_over["content"].count(MARKER_PREFIX) == 1
    assert block_over["content"][0]["text"].count(MARKER_PREFIX) == 1
    assert _encoded_text_bytes(string_over["content"]) <= DEFAULT_CAP
    assert _encoded_text_bytes(block_over["content"][0]["text"]) <= DEFAULT_CAP
    assert string_over["content"].encode("utf-8").decode("utf-8") == string_over["content"]
    assert (
        block_over["content"][0]["text"].encode("utf-8").decode("utf-8")
        == block_over["content"][0]["text"]
    )


@pytest.mark.parametrize("invalid", [True, 0, -1, None, "131072", 131072.0])
def test_invalid_tool_result_ingress_caps_are_rejected(invalid):
    with pytest.raises(ValueError, match="max_tool_result_bytes"):
        SimpleContextManager(max_tool_result_bytes=invalid)


def test_cap_too_small_for_the_loud_marker_is_rejected():
    marker_at_largest_possible_length = _encoded_text_bytes(
        (
            "[tool-result truncated at ingress: original_text_utf8_bytes="
            f"{__import__('sys').maxsize}; {SAFE_RETRIEVAL_NOTICE}]"
        )
    )
    with pytest.raises(ValueError, match="max_tool_result_bytes"):
        SimpleContextManager(max_tool_result_bytes=marker_at_largest_possible_length - 1)


@pytest.mark.asyncio
async def test_tool_identity_metadata_error_and_caller_objects_are_preserved():
    original = {
        "role": "tool",
        "name": "fetch_artifact",
        "tool_call_id": "call-native-7",
        "tool_use_id": "use-native-7",
        "status": "error",
        "error": {"message": "failed"},
        "content": "z" * (DEFAULT_CAP + 100),
        "metadata": {
            "openai:tool_search_items": [{"name": "glob"}],
            "nested": {"keep": ["all", "of", "this"]},
        },
    }
    caller_snapshot = copy.deepcopy(original)
    context = SimpleContextManager()

    await context.add_message(original)

    stored = (await context.get_messages())[0]
    assert original == caller_snapshot
    for key in ("name", "tool_call_id", "tool_use_id", "status", "error"):
        assert stored[key] == original[key]
    assert stored["metadata"]["openai:tool_search_items"] == caller_snapshot["metadata"][
        "openai:tool_search_items"
    ]
    assert stored["metadata"]["nested"] == caller_snapshot["metadata"]["nested"]
    assert MARKER_PREFIX not in str(stored["metadata"])


@pytest.mark.asyncio
async def test_text_blocks_cap_aggregate_text_without_touching_media_or_unknown_blocks():
    hooks = _RecordingHooks()
    image = {"type": "image", "source": {"data": "a" * (DEFAULT_CAP * 2)}}
    unknown = {"type": "vendor_extension", "payload": {"keep": "unchanged"}}
    original_blocks = [
        TextBlock(text="first-" + "a" * 100, visibility="user", extra_value="kept"),
        image,
        {"type": "text", "text": "second-" + "b" * DEFAULT_CAP, "trace": "preserve"},
        unknown,
    ]
    message = {"role": "tool", "content": original_blocks}
    caller_snapshot = copy.deepcopy(message)
    context = SimpleContextManager(hooks=hooks)

    await context.add_message(message)

    stored = (await context.get_messages())[0]["content"]
    text_blocks = [
        block
        for block in stored
        if isinstance(block, TextBlock) or (isinstance(block, dict) and block.get("type") == "text")
    ]
    text = "".join(
        block.text if isinstance(block, TextBlock) else block["text"] for block in text_blocks
    )
    non_text = [
        block for block in stored if not (isinstance(block, TextBlock) or block.get("type") == "text")
    ]

    assert message == caller_snapshot
    assert _encoded_text_bytes(text) <= DEFAULT_CAP
    assert text.count(MARKER_PREFIX) == 1
    assert all(
        block.text if isinstance(block, TextBlock) else block["text"] for block in text_blocks
    )
    assert non_text == [image, unknown]
    assert isinstance(text_blocks[0], TextBlock)
    assert text_blocks[0].extra_value == "kept"
    assert hooks.events[0][1]["content_kind"] == "text_blocks"


@pytest.mark.asyncio
async def test_mixed_blocks_preserve_opaque_identity_and_drop_text_after_overflow():
    image = {"type": "image", "source": {"ref": "image-1"}}
    file_ref = {"type": "file", "identity": {"id": "file-1"}}
    audio_ref = {"type": "audio", "identity": {"id": "audio-1"}}
    trailing_text = {"type": "text", "text": "must not survive"}
    message = {
        "role": "tool",
        "content": [
            TextBlock(text="typed-", visibility="user"),
            image,
            file_ref,
            audio_ref,
            {"type": "text", "text": "🙂" * DEFAULT_CAP, "trace": "overflow"},
            trailing_text,
        ],
    }
    caller_snapshot = copy.deepcopy(message)
    context = SimpleContextManager()

    await context.add_message(message)

    stored = (await context.get_messages())[0]["content"]
    text_blocks = [
        block
        for block in stored
        if isinstance(block, TextBlock) or (isinstance(block, dict) and block.get("type") == "text")
    ]
    stored_text = "".join(
        block.text if isinstance(block, TextBlock) else block["text"] for block in text_blocks
    )

    assert message == caller_snapshot
    assert stored[1] is image
    assert stored[2] is file_ref
    assert stored[3] is audio_ref
    assert [block for block in stored if block in (image, file_ref, audio_ref)] == [
        image,
        file_ref,
        audio_ref,
    ]
    assert _encoded_text_bytes(stored_text) <= DEFAULT_CAP
    assert stored_text.count(MARKER_PREFIX) == 1
    assert all(
        block.text if isinstance(block, TextBlock) else block["text"] for block in text_blocks
    )
    assert trailing_text not in stored


@pytest.mark.asyncio
async def test_image_only_and_non_tool_or_malformed_content_are_unchanged_and_silent():
    hooks = _RecordingHooks()
    image_only = [{"type": "image", "source": {"base64": "x" * (DEFAULT_CAP * 2)}}]
    malformed = [{"type": "text", "text": 42}, {"type": "unknown", "payload": "keep"}]
    context = SimpleContextManager(hooks=hooks)

    await context.add_message({"role": "tool", "content": image_only})
    await context.add_message({"role": "tool", "content": malformed})
    await context.add_message({"role": "user", "content": "x" * (DEFAULT_CAP + 1)})

    stored = await context.get_messages()
    assert stored[0]["content"] == image_only
    assert stored[1]["content"] == malformed
    assert stored[2]["content"] == "x" * (DEFAULT_CAP + 1)
    assert hooks.events == []


@pytest.mark.asyncio
async def test_set_messages_clips_before_restamping_and_is_idempotent_on_bounded_resume():
    hooks = _RecordingHooks()
    context = SimpleContextManager(hooks=hooks)
    await context.add_message({"role": "tool", "content": "old" * 100_000})
    context._truncated_seqs.add(0)
    resumed = [
        {
            "role": "tool",
            "tool_call_id": "call-resume",
            "content": "x" * (DEFAULT_CAP + 2),
            "metadata": {"_seq": 999, "openai:tool_search_items": [{"name": "glob"}]},
        },
        {"role": "user", "content": "continue", "metadata": {"_seq": 123}},
    ]
    caller_snapshot = copy.deepcopy(resumed)

    await context.set_messages(resumed)
    stored_once = await context.get_messages()
    await context.set_messages(stored_once)

    stored_twice = await context.get_messages()
    assert resumed == caller_snapshot
    assert [message["metadata"]["_seq"] for message in stored_twice] == [0, 1]
    assert context._truncated_seqs == set()
    assert stored_twice[0]["metadata"]["openai:tool_search_items"] == [{"name": "glob"}]
    assert stored_twice[0]["content"].count(MARKER_PREFIX) == 1
    assert len(hooks.events) == 2  # initial add plus the one oversized resumed message


@pytest.mark.asyncio
async def test_truncation_event_is_numeric_only_and_hook_failures_do_not_block_admission(caplog):
    hooks = _RecordingHooks()
    context = SimpleContextManager(hooks=hooks)
    secret_text = "private-tool-output-" + "x" * DEFAULT_CAP

    await context.add_message(
        {
            "role": "tool",
            "name": "safe_name",
            "tool_call_id": "safe_call",
            "content": secret_text,
        }
    )

    event, data = hooks.events[0]
    assert event == "context:tool_result_ingress_truncated"
    assert data == {
        "tool_name": "safe_name",
        "tool_call_id": "safe_call",
        "original_text_utf8_bytes": len(secret_text.encode("utf-8")),
        "stored_text_utf8_bytes": DEFAULT_CAP,
        "max_tool_result_bytes": DEFAULT_CAP,
        "content_kind": "string",
    }
    assert secret_text not in str(data)

    failing = SimpleContextManager(hooks=_FailingHooks())
    with caplog.at_level(logging.WARNING):
        await failing.add_message({"role": "tool", "content": secret_text})
    assert (await failing.get_messages())[0]["content"].count(MARKER_PREFIX) == 1
    assert SAFE_RETRIEVAL_NOTICE in caplog.text


@pytest.mark.asyncio
async def test_mount_forwards_the_ingress_cap():
    coordinator = _Coordinator()

    await mount(coordinator, {"max_tool_result_bytes": DEFAULT_CAP + 10})

    assert coordinator.mounted["context"].max_tool_result_bytes == DEFAULT_CAP + 10
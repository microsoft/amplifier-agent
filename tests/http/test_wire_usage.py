"""Tests for cache_creation_tokens visibility on the OpenAI usage wire block.

Covers:
- ``_build_usage_block`` surfaces ``cached_tokens`` AND ``cache_creation_tokens``
  together under ``prompt_tokens_details``, without changing ``prompt_tokens``
  semantics (still the full fresh+read+write total) or ``cost_usd``.
- Zero-usage calls omit ``prompt_tokens_details``/``cost_usd`` entirely.
- ``stop_chunk`` and ``tool_calls_stop_chunk`` both forward ``cache_creation_tokens``
  through to the usage block.
- ``extract_usage`` maps the kernel's ``cacheWriteTokens`` field onto the new
  ``cache_creation_tokens`` key (in addition to folding it into ``prompt_tokens``).
"""

from __future__ import annotations

from amplifier_agent_http._event_translator import extract_usage
from amplifier_agent_http._wire import (
    _build_usage_block,
    stop_chunk,
    tool_calls_stop_chunk,
)


def test_build_usage_block_surfaces_all_buckets() -> None:
    usage = _build_usage_block(
        prompt_tokens=4_088_358,
        completion_tokens=34_557,
        cached_tokens=1_158_290,
        cache_creation_tokens=2_929_924,
        cost_usd="19.755815",
    )

    assert usage["prompt_tokens"] == 4_088_358
    assert usage["completion_tokens"] == 34_557
    assert usage["total_tokens"] == 4_122_915
    assert usage["prompt_tokens_details"] == {
        "cached_tokens": 1_158_290,
        "cache_creation_tokens": 2_929_924,
    }
    assert usage["cost_usd"] == "19.755815"


def test_build_usage_block_zero_usage_omits_details() -> None:
    usage = _build_usage_block(prompt_tokens=0, completion_tokens=0)

    assert usage["total_tokens"] == 0
    assert "prompt_tokens_details" not in usage
    assert "cost_usd" not in usage


def test_stop_chunk_includes_cache_creation() -> None:
    chunk = stop_chunk(
        "id",
        "claude-opus-4-8",
        prompt_tokens=100,
        completion_tokens=10,
        cached_tokens=40,
        cache_creation_tokens=50,
    )

    assert chunk["usage"]["prompt_tokens_details"]["cache_creation_tokens"] == 50
    assert chunk["usage"]["prompt_tokens_details"]["cached_tokens"] == 40


def test_tool_calls_stop_chunk_includes_cache_creation() -> None:
    chunk = tool_calls_stop_chunk(
        "id",
        "claude-opus-4-8",
        prompt_tokens=100,
        completion_tokens=10,
        cached_tokens=40,
        cache_creation_tokens=50,
    )

    assert chunk["usage"]["prompt_tokens_details"]["cache_creation_tokens"] == 50
    assert chunk["choices"][0]["finish_reason"] == "tool_calls"


def test_extract_usage_maps_cache_creation() -> None:
    event = {
        "type": "usage",
        "inputTokens": 144,
        "cacheReadTokens": 1_158_290,
        "cacheWriteTokens": 2_929_924,
        "outputTokens": 34_557,
        "cost": "19.755815",
    }

    result = extract_usage(event)

    assert result is not None
    assert result["prompt_tokens"] == 4_088_358
    assert result["completion_tokens"] == 34_557
    assert result["cached_tokens"] == 1_158_290
    assert result["cache_creation_tokens"] == 2_929_924
    assert result["cost_usd"] == "19.755815"
    assert result["total_tokens"] == 4_088_358 + 34_557


def test_extract_usage_non_usage_event_returns_none() -> None:
    assert extract_usage({"type": "content_block"}) is None

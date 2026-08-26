"""DTU-backed tests for real per-turn token usage in the ``--output json`` envelope.

Contract under test: after a turn completes, the stdout envelope's ``metadata`` block
reports what the turn actually cost.

    tokensIn          int         CHARGED input total = new input + cache reads + cache writes
    tokensOut         int         output tokens
    cacheReadTokens   int         the cached half of tokensIn that was read back
    cacheWriteTokens  int         the cached half of tokensIn that was written
    costUsd           str | None  decimal STRING, never a float; None when no provider
                                  reported a cost

Today ``tokensIn`` and ``tokensOut`` are hardcoded to ``0``
(``src/amplifier_agent_cli/modes/single_turn.py``) and the three new fields do not
exist at all, so every case here is ``xfail(strict=True)``. Strict means the moment
usage accounting lands these turn XPASS and the markers must come off -- see
docs/E2E_TESTING.md, "Tests for features that do not exist yet".

The assertions are on the envelope's public shape only. Nothing here reads a log line,
an internal counter, or a private attribute, so a refactor that keeps the envelope
honest keeps these green.

The last case is the accuracy oracle and the one that actually pins the numbers to
reality. Everything above it would still pass if the engine reported plausible-looking
but wrong totals; that case sums the provider's own per-call usage out of the session's
``context-intelligence/events.jsonl`` and requires the envelope to match it exactly.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from framework import dtu

from suites.usage.events_oracle import read_provider_usage

pytestmark = pytest.mark.dtu

# Host config seeded by DTU provisioning: anthropic / claude-sonnet-5 / approval yes.
CFG = "/root/e2e/host-config.json"

# Short, tool-free, and delegation-free on purpose. A turn that spawns a sub-agent
# writes its LLM calls into a DIFFERENT session directory, which would put the
# accuracy oracle and the envelope on different sides of a boundary neither of them
# describes. One prompt, one session, one set of numbers.
PROMPT = "Reply with the single word: pong"


def _session_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _run_turn(dtu_id: str, session_id: str, extra_args: list[str]) -> dict[str, Any]:
    """Run one ``--output json`` turn inside the DTU and return the parsed envelope."""
    argv = [
        "amplifier-agent",
        "run",
        "-y",
        "--config",
        CFG,
        "--output",
        "json",
        "--session-id",
        session_id,
        "--fresh",
        *extra_args,
        PROMPT,
    ]
    result = dtu.exec_json(dtu_id, argv)

    assert result.get("exit_code") == 0, (
        f"turn failed (exit {result.get('exit_code')}).\n"
        f"argv: {argv}\n"
        f"stdout:\n{result.get('stdout', '')}\nstderr:\n{result.get('stderr', '')}"
    )

    stdout = result.get("stdout", "").strip()
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            "stdout was not a parseable envelope under --output json.\n"
            "Under --output json the envelope is the ONLY thing on stdout.\n"
            f"argv: {argv}\nstdout:\n{stdout}"
        ) from exc

    assert envelope.get("error") is None, f"turn returned an error envelope: {envelope.get('error')}"
    return envelope


def _metadata(envelope: dict[str, Any]) -> dict[str, Any]:
    metadata = envelope.get("metadata")
    assert isinstance(metadata, dict), f"envelope has no metadata object: {envelope!r}"
    return metadata


# Three ways of asking for the same envelope. `--display` and `--quiet` govern STDERR;
# `--output json` governs STDOUT. They are independent knobs, so usage accounting must
# not be a side effect of whichever human-facing renderer happened to be attached --
# a host that runs quiet gets the same numbers as one that streams ndjson.
_DISPLAY_VARIANTS = [
    pytest.param([], id="usage-envelope-tokens-nonzero"),
    pytest.param(["--display", "text"], id="usage-envelope-text-display"),
    pytest.param(["--quiet"], id="usage-envelope-quiet"),
]


@pytest.mark.parametrize("extra_args", _DISPLAY_VARIANTS)
def test_usage_envelope_tokens_nonzero(dtu_id: str, extra_args: list[str]) -> None:
    """A completed turn reports non-zero input and output tokens."""
    metadata = _metadata(_run_turn(dtu_id, _session_id("usage-env"), extra_args))

    tokens_in = metadata.get("tokensIn")
    tokens_out = metadata.get("tokensOut")

    assert isinstance(tokens_in, int) and not isinstance(tokens_in, bool), (
        f"metadata.tokensIn must be an int, got {tokens_in!r} ({type(tokens_in).__name__})"
    )
    assert isinstance(tokens_out, int) and not isinstance(tokens_out, bool), (
        f"metadata.tokensOut must be an int, got {tokens_out!r} ({type(tokens_out).__name__})"
    )
    assert tokens_in > 0, (
        f"metadata.tokensIn is {tokens_in}. A turn that reached the provider always "
        "consumed input tokens; 0 means the envelope is reporting a placeholder."
    )
    assert tokens_out > 0, (
        f"metadata.tokensOut is {tokens_out}. The turn produced a reply "
        f"({metadata.get('durationMs')}ms), so the provider billed output tokens."
    )


def test_usage_envelope_breakdown_fields(dtu_id: str) -> None:
    """The cache breakdown is present and typed; cost is a decimal STRING or null.

    ``costUsd`` being a string is the load-bearing half. A float cannot represent a
    decimal money value exactly, so a host that sums per-turn costs from JSON floats
    accumulates drift it cannot see. The type is therefore part of the contract, and
    the assertion rejects ``int`` too -- a whole-dollar cost serialized as ``0`` is
    the same bug wearing a different hat.
    """
    metadata = _metadata(_run_turn(dtu_id, _session_id("usage-breakdown"), []))

    for field in ("cacheReadTokens", "cacheWriteTokens"):
        assert field in metadata, f"metadata.{field} is absent. Keys present: {sorted(metadata)}"
        value = metadata[field]
        assert isinstance(value, int) and not isinstance(value, bool), (
            f"metadata.{field} must be an int, got {value!r} ({type(value).__name__})"
        )
        assert value >= 0, f"metadata.{field} must not be negative, got {value}"

    assert "costUsd" in metadata, f"metadata.costUsd is absent. Keys present: {sorted(metadata)}"
    cost = metadata["costUsd"]
    assert cost is None or isinstance(cost, str), (
        f"metadata.costUsd must be a decimal string or null, got {cost!r} ({type(cost).__name__}). "
        "A float or int loses monetary precision the moment a host sums it."
    )


def test_usage_envelope_breakdown_consistent(dtu_id: str) -> None:
    """``tokensIn`` is the charged total, so the cached halves cannot exceed it.

    ``tokensIn`` is specified as new input + cache reads + cache writes. A host that
    wants the "new" figure derives it by subtracting the two cache fields, so if the
    parts ever exceed the whole that subtraction goes negative and every downstream
    cost calculation is wrong in a way no type check would catch.
    """
    metadata = _metadata(_run_turn(dtu_id, _session_id("usage-consistent"), []))

    tokens_in = metadata.get("tokensIn")
    cache_read = metadata.get("cacheReadTokens")
    cache_write = metadata.get("cacheWriteTokens")

    assert isinstance(tokens_in, int), f"metadata.tokensIn must be an int, got {tokens_in!r}"
    assert isinstance(cache_read, int), f"metadata.cacheReadTokens must be an int, got {cache_read!r}"
    assert isinstance(cache_write, int), f"metadata.cacheWriteTokens must be an int, got {cache_write!r}"

    assert tokens_in >= cache_read + cache_write, (
        f"metadata.tokensIn ({tokens_in}) is smaller than cacheReadTokens + cacheWriteTokens "
        f"({cache_read} + {cache_write} = {cache_read + cache_write}). tokensIn is the CHARGED "
        "total, so the derived new-input figure (tokensIn - cacheRead - cacheWrite) would be negative."
    )


def test_usage_envelope_accuracy_vs_raw_events(dtu_id: str) -> None:
    """THE ACCURACY ORACLE: the envelope must equal the provider's own reported usage.

    Every other case in this module would still pass if the engine reported numbers
    that were merely plausible. This one runs a turn under a known ``--session-id``,
    then sums the provider's per-call usage out of that session's
    ``context-intelligence/events.jsonl`` -- a record the engine writes for a different
    reason, through a different code path, from the same provider responses -- and
    requires exact equality.

    Exact, not approximate. Two independent readings of the same provider response have
    no legitimate reason to differ by even one token, and a tolerance would hide the
    exact class of bug this exists to catch: a total that drops one call, double-counts
    another, or silently omits the cached halves.
    """
    session_id = _session_id("usage-oracle")
    metadata = _metadata(_run_turn(dtu_id, session_id, []))
    provider = read_provider_usage(dtu_id, session_id)

    assert provider.responses_without_usage == 0, (
        f"{provider.responses_without_usage} of {provider.responses} llm:response records "
        "carry no usage sub-dict, so the oracle's total is incomplete and cannot be "
        "compared against the envelope."
    )

    context = (
        f"session={session_id} "
        f"llm_responses={provider.responses} turn_ids={list(provider.turn_ids)}\n"
        f"provider totals: new_input={provider.input_tokens} output={provider.output_tokens} "
        f"cache_read={provider.cache_read_tokens} cache_write={provider.cache_write_tokens} "
        f"charged_input={provider.charged_input}\n"
        f"envelope metadata: {json.dumps({k: metadata.get(k) for k in sorted(metadata)}, default=str)}"
    )

    assert metadata.get("tokensOut") == provider.output_tokens, (
        f"metadata.tokensOut ({metadata.get('tokensOut')}) != provider-reported output "
        f"tokens ({provider.output_tokens}).\n{context}"
    )
    assert metadata.get("cacheReadTokens") == provider.cache_read_tokens, (
        f"metadata.cacheReadTokens ({metadata.get('cacheReadTokens')}) != provider-reported "
        f"cache reads ({provider.cache_read_tokens}).\n{context}"
    )
    assert metadata.get("cacheWriteTokens") == provider.cache_write_tokens, (
        f"metadata.cacheWriteTokens ({metadata.get('cacheWriteTokens')}) != provider-reported "
        f"cache writes ({provider.cache_write_tokens}).\n{context}"
    )
    assert metadata.get("tokensIn") == provider.charged_input, (
        f"metadata.tokensIn ({metadata.get('tokensIn')}) != the provider's CHARGED input total "
        f"({provider.charged_input} = new {provider.input_tokens} + cache_read "
        f"{provider.cache_read_tokens} + cache_write {provider.cache_write_tokens}).\n{context}"
    )

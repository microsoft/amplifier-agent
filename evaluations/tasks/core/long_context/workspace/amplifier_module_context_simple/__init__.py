"""
Simple context manager module.

Implements an in-memory context manager with EPHEMERAL compaction:
  • Messages stored in memory (self.messages is the source of truth)
  • Compaction NEVER modifies self.messages
  • get_messages_for_request() returns compacted VIEW (new list)
  • get_messages() returns FULL history (for transcripts/session persistence)

Compaction never loses admitted history. Oversized direct tool-result text is
irreversibly clipped before admission and is not retained in the transcript.
For persistent storage across sessions, use context-persistent instead.

Dynamic System Prompt Support:
  • set_system_prompt_factory() registers a callable that produces fresh system content
  • get_messages_for_request() calls the factory on EVERY request
  • Enables @mentions and bundle instructions to be re-processed each turn
  • Static system messages (via add_message) are still supported as fallback

Real-Usage Token Meter (opt-in, default off -- see config `token_meter`):
  • The compaction trigger normally runs entirely off an uncalibrated
    len(str)//4 estimator (see _estimate_tokens) that is never reconciled
    against what the provider actually billed -- measured roughly 2x off
    in production sessions.
  • When hooks are available, this module always registers a listener on
    the canonical `llm:response` event and records the provider's own
    reported usage (see SimpleContextManager._on_llm_response) as an
    observability signal, exposed via `_last_token_meter_stats`, so the
    estimator-vs-real drift is visible even in the default mode.
  • Set `token_meter: "actual"` in config to additionally have the
    compaction trigger itself USE that real measurement once one has
    arrived this session (falling back to the estimator before then, or
    whenever hooks/events are unavailable). Default is `"estimate"`, which
    keeps behavior byte-identical to before this feature existed.
  • Ported from amplifier-module-context-handoff's proven `_on_llm_response`
    meter. See README "Real-usage token meter" for the full rationale.
"""

# Amplifier module metadata
__amplifier_module_type__ = "context"

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from sys import maxsize
from typing import Any

from amplifier_core import ModuleCoordinator, TextBlock
from amplifier_core.llm_errors import ContextLengthError

from ._text_estimate import estimate_messages

logger = logging.getLogger(__name__)

DEFAULT_MAX_TOOL_RESULT_BYTES = 128 * 1024

DEFAULT_MAX_TOKENS_FALLBACK = 200_000
"""Budget used ONLY when the provider reports no usable context window.

Calibrated to the SMALLEST context window across the current generation of
the three major vendors, because a fallback is a guess and the safe direction
for a guess is small -- an over-large budget overflows the request, an
under-large one only compacts earlier:

    Anthropic  claude-opus-5 / sonnet-5 / haiku-4-5   200,000  (base; 1M opt-in)
    OpenAI     gpt-5.6, gpt-6-astra                   272,000  (default; ~900K opt-in)
    Google     gemini-3.x                           1,048,576

Reached only by providers that publish neither `get_model_info()` nor
`get_info().defaults["context_window"]` + `["max_output_tokens"]`. Overridable
per-session via config `max_tokens_fallback`; the right long-term fix for any
provider landing here is for THAT provider to publish its window.
"""
_TOOL_RESULT_INGRESS_MARKER = (
    "[tool-result truncated at ingress: original_text_utf8_bytes={original_text_utf8_bytes}; "
    "retrieve missing content using narrower read/query parameters; do not repeat "
    "state-changing actions just to recover output.]"
)
_MIN_MAX_TOOL_RESULT_BYTES = len(
    _TOOL_RESULT_INGRESS_MARKER.format(original_text_utf8_bytes=maxsize).encode(
        "utf-8", errors="replace"
    )
)

# token_meter config values. "estimate" (default) preserves pre-existing
# behavior exactly; "actual" lets a real llm:response measurement drive the
# compaction trigger once one has arrived this session. See module
# docstring "Real-Usage Token Meter" and
# SimpleContextManager._measure_working_tokens.
TOKEN_METER_ESTIMATE = "estimate"
TOKEN_METER_ACTUAL = "actual"
_VALID_TOKEN_METERS = (TOKEN_METER_ESTIMATE, TOKEN_METER_ACTUAL)

# BREAK 5 -- messages carrying LOADED-TOOL STATE are un-droppable.
#
# A provider may record, on a message's metadata, which tools the model
# dynamically loaded during that turn (OpenAI's hosted tool search does this via
# `openai:tool_search_items`). That record is not decoration -- per the vendored
# tool-search guide `TS:854`:
#
#   "Tools that were not listed as part of this array will not be available to
#    the model ... changing the loaded tool set will break the model's cache
#    from that point forward."
#
# So dropping such a message during compaction costs TWICE: (a) every tool it
# loaded silently ceases to exist for the model, which it discovers by failing
# to call one, and (b) the prompt cache breaks forward from that point -- and
# OpenAI's cache is grow-only, so a break is a full cold rebuild, not a dip.
#
# The ladder has no other concept of an un-droppable item, so this is a named
# protected set rather than a role check. It is deliberately keyed on METADATA,
# not on a provider name: a second provider adding the same kind of state adds
# a key here and needs no other change. Truncation and stubbing already rebuild
# messages with `{**msg, ...}`, so they preserve metadata and need no change --
# only REMOVAL had to learn this.
LOADED_TOOL_STATE_METADATA_KEYS: frozenset[str] = frozenset(
    {
        "openai:tool_search_items",
    }
)


def _carries_loaded_tool_state(msg: dict[str, Any]) -> bool:
    """True if removing this message would silently unload tools (TS:854)."""
    meta = msg.get("metadata") or {}
    if not isinstance(meta, dict):
        return False
    return any(meta.get(key) for key in LOADED_TOOL_STATE_METADATA_KEYS)


def _is_human_message(msg: dict[str, Any]) -> bool:
    """Wire role alone does not distinguish a prompt from an injection."""
    meta = msg.get("metadata") or {}
    return (
        msg.get("role") == "user"
        and not meta.get("ephemeral")
        and meta.get("source") not in ("hook", "context-compaction")
    )


def _validated_max_tokens(raw: Any) -> int | None:
    """Return a usable `max_tokens` cap, or None for 'no cap'.

    A nonsense cap is refused LOUDLY and treated as absent rather than
    honored, matching how `compaction_notice_token_reserve` handles a
    self-defeating value: a cap of 0 or a negative number would drive the
    budget to <= 0, and `_should_compact`'s `budget > 0` guard would then
    silently disable compaction entirely -- the opposite of what someone
    setting a cap is asking for.
    """
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        logger.warning(
            f"context-simple: ignoring invalid max_tokens {raw!r} "
            f"(expected a positive integer, or None for no cap); "
            f"running uncapped at the model's own window"
        )
        return None
    return raw


async def mount(coordinator: ModuleCoordinator, config: dict[str, Any] | None = None):
    """
    Mount the simple context manager.

    Args:
        coordinator: Module coordinator
        config: Optional configuration
            - max_tokens: CAP on the effective token budget (default: None =
              no cap, use everything the model allows). Set this only to use
              LESS than the model's window. It is applied as
              `min(model_derived_budget, max_tokens)`, so setting it ABOVE the
              model's own window has no effect -- the lower of the two wins.
            - max_tokens_fallback: Budget used only when the provider reports
              no usable window (default: 200,000). Not a cap; see
              DEFAULT_MAX_TOKENS_FALLBACK for how the number was chosen.
            - compact_threshold: Trigger compaction at this usage (default: 0.92)
            - target_usage: Compact down to this usage (default: 0.50)
            - protected_recent: Always protect last N% of messages (default: 0.30)
            - protected_tool_results: Always protect last N tool results (default: 5;
              0 protects none)
            - truncate_chars: Characters to keep when truncating tool results (default: 250)
            - compaction_notice_enabled: Enable compaction notice (default: True)
            - compaction_notice_token_reserve: Tokens to reserve for notice (default: 800)
            - compaction_notice_verbosity: Notice detail level - "minimal", "normal", "verbose" (default: "normal")
            - compaction_notice_min_level: Only show notice if compaction level >= this (default: 1)
            - output_reserve_fraction: Fraction of max_output_tokens to reserve for responses (default: 0.5)
            - token_meter: "estimate" (default) or "actual". "estimate" is
              byte-identical to pre-existing behavior. "actual" drives the
              compaction trigger from real provider usage (input_tokens +
              cache_write_tokens, observed via the `llm:response` hook)
              once at least one response has been observed this session,
              falling back to the estimator before then. An unrecognized
              value falls back to "estimate" with a logged warning rather
              than crashing mount(). See module docstring.
            - max_tool_result_bytes: Maximum UTF-8 bytes admitted for direct
              text in one tool result (default: 131,072). Oversized text is
              irreversibly clipped at ingress with a retrieval marker.

    Returns:
        Cleanup callable that unregisters the token-meter hook (if one was
        registered).
    """
    config = config or {}

    token_meter = config.get("token_meter", TOKEN_METER_ESTIMATE)
    if token_meter not in _VALID_TOKEN_METERS:
        logger.warning(
            f"context-simple: unknown token_meter {token_meter!r} (expected "
            f"one of {_VALID_TOKEN_METERS!r}); falling back to "
            f"{TOKEN_METER_ESTIMATE!r}"
        )
        token_meter = TOKEN_METER_ESTIMATE

    max_tokens = _validated_max_tokens(config.get("max_tokens"))

    context = SimpleContextManager(
        max_tokens=max_tokens,
        max_tokens_fallback=config.get(
            "max_tokens_fallback", DEFAULT_MAX_TOKENS_FALLBACK
        ),
        compact_threshold=config.get("compact_threshold", 0.92),
        target_usage=config.get("target_usage", 0.50),
        protected_recent=config.get("protected_recent", 0.30),
        protected_tool_results=config.get("protected_tool_results", 5),
        truncate_chars=config.get("truncate_chars", 250),
        compaction_notice_enabled=config.get("compaction_notice_enabled", True),
        compaction_notice_token_reserve=config.get(
            "compaction_notice_token_reserve", 800
        ),
        compaction_notice_verbosity=config.get("compaction_notice_verbosity", "normal"),
        compaction_notice_min_level=config.get("compaction_notice_min_level", 1),
        output_reserve_fraction=config.get("output_reserve_fraction", 0.5),
        token_meter=token_meter,
        hooks=getattr(coordinator, "hooks", None),
        max_tool_result_bytes=config.get(
            "max_tool_result_bytes", DEFAULT_MAX_TOOL_RESULT_BYTES
        ),
    )

    # Always register the meter listener when hooks are available, regardless
    # of token_meter mode: recording is a no-op on trigger behavior unless
    # token_meter == "actual" consults it (see _measure_working_tokens), so
    # this keeps the default ("estimate") mode's behavior byte-identical
    # while still populating _last_token_meter_stats for observability.
    unregister: Callable[[], None] | None = None
    hooks = getattr(coordinator, "hooks", None)
    if hooks is not None:
        unregister = hooks.register(
            "llm:response",
            context._on_llm_response,
            priority=50,
            name="context-simple-meter",
        )

    await coordinator.mount("context", context)
    # Optional module capability; the core Context protocol is unchanged.
    register_capability = getattr(coordinator, "register_capability", None)
    if callable(register_capability):
        register_capability(
            "context.request_retention", context.get_messages_for_request_retaining
        )
        register_capability("context.foreground_usage", context.claim_foreground_usage)
        # This is deliberately absent in estimate mode.  The loop must opt in
        # only when both its provider and Context can make the same complete
        # request count; the legacy getter remains the estimate-mode path.
        if token_meter == TOKEN_METER_ACTUAL:
            register_capability(
                "context.measured_request_view", context.get_measured_request_view
            )
    logger.info(f"Mounted SimpleContextManager (token_meter={token_meter!r})")

    async def cleanup() -> None:
        if unregister is not None:
            unregister()

    return cleanup


class SimpleContextManager:
    """
    In-memory context manager with EPHEMERAL compaction.

    Key Principle: self.messages is the source of truth and is NEVER modified
    by compaction. Compaction only returns a compacted VIEW for the current
    LLM request.

    Owns memory policy: orchestrators ask for messages via get_messages_for_request(),
    and this context manager decides how to fit them within limits. Compaction is
    handled internally and ephemerally - admitted history is always preserved.
    Oversized direct tool-result text is clipped before it enters that history.

    Compaction Strategy (Progressive Interleaved):
    Triggered when usage >= compact_threshold (default 92%), target is target_usage (default 50%).

    Each level checks after every operation and stops as soon as target is reached:

    Level 1: Truncate oldest 25% of tool results
    Level 2: Truncate next 25% of tool results (now 50% truncated)
    Level 3: Remove oldest messages (use configured protected_recent)
    Level 4: Truncate next 25% of tool results (now 75% truncated)
    Level 5: Remove more messages (60% of configured protection)
    Level 6: Truncate remaining tool results (except last N)
    Level 7: Remove more messages (30% of configured protection - last resort)
    Level 8: Stub unprotected machine prefix + remove old stubs (extreme pressure)

    This interleaved approach ensures minimal data loss by:
    - Preferring truncation (preserves structure) over removal (loses context)
    - Progressively relaxing protection as pressure increases
    - Respecting configured protected_recent as baseline, only relaxing under pressure
    - Always protecting: system messages, first/last human prompts, last N tool results, tool pairs
    - Requested active persisted injections remain complete through all levels
    """

    def __init__(
        self,
        max_tokens: int | None = None,
        max_tokens_fallback: int = DEFAULT_MAX_TOKENS_FALLBACK,
        compact_threshold: float = 0.92,
        target_usage: float = 0.50,
        protected_recent: float = 0.30,
        protected_tool_results: int = 5,
        truncate_chars: int = 250,
        compaction_notice_enabled: bool = True,
        compaction_notice_token_reserve: int = 800,
        compaction_notice_verbosity: str = "normal",
        compaction_notice_min_level: int = 1,
        output_reserve_fraction: float = 0.5,
        token_meter: str = TOKEN_METER_ESTIMATE,
        hooks: Any = None,
        max_tool_result_bytes: int = DEFAULT_MAX_TOOL_RESULT_BYTES,
    ):
        """
        Initialize the context manager.

        Args:
            max_tokens: Cap on the effective token budget, or None for no
                cap (the default: use the whole window the model allows).
                Applied as min(model_derived_budget, max_tokens), so a
                value above the model's own window is a no-op.
            max_tokens_fallback: Budget used only when the provider reports
                no usable window. Not a cap.
            compact_threshold: Trigger compaction at this usage ratio (0.0-1.0)
            target_usage: Compact down to this usage ratio (0.0-1.0)
            protected_recent: Always protect last N% of messages (0.0-1.0)
            protected_tool_results: Always protect last N tool results from
                truncation. 0 (or negative) protects NONE of them -- every tool
                result becomes truncation-eligible.
            truncate_chars: Characters to keep when truncating tool results
            compaction_notice_enabled: Enable compaction notice injection
            compaction_notice_token_reserve: Tokens to reserve for notice
            compaction_notice_verbosity: Notice detail level ("minimal", "normal", "verbose")
            compaction_notice_min_level: Only show notice if compaction level >= this
            output_reserve_fraction: Fraction of max_output_tokens to reserve for
                responses (0.0-1.0, default: 0.5). Lower values give more context
                budget at the cost of less headroom for long responses.
            token_meter: "estimate" (default, byte-identical to pre-existing
                behavior) or "actual" (compaction trigger uses real provider
                usage from the `llm:response` hook once observed this
                session -- see module docstring "Real-Usage Token Meter").
                An unrecognized value falls back to "estimate" with a
                logged warning rather than raising.
            hooks: Optional hooks instance for emitting observability events
                and (always, when present) recording real usage for the
                token meter via `llm:response` -- see `_on_llm_response`.
            max_tool_result_bytes: Maximum UTF-8 bytes admitted for direct
                text in one tool result. The minimum allows the truncation
                marker itself; default is 128 KiB.
        """
        if (
            isinstance(max_tool_result_bytes, bool)
            or not isinstance(max_tool_result_bytes, int)
            or max_tool_result_bytes < _MIN_MAX_TOOL_RESULT_BYTES
        ):
            raise ValueError(
                "max_tool_result_bytes must be an integer at least "
                f"{_MIN_MAX_TOOL_RESULT_BYTES} so the ingress truncation marker fits"
            )

        self.messages: list[dict[str, Any]] = []
        self.max_tokens = max_tokens
        self.max_tokens_fallback = max_tokens_fallback
        # Last budget handed to a request; used for honest usage logging
        # before any request has been made (see add_message).
        self._last_effective_budget: int | None = None
        # Provenance of the most recent budget calculation, and the last
        # payload emitted, so `context:budget` fires on CHANGE rather than
        # once per request (a per-request event would drown the record it
        # is meant to make readable).
        self._budget_provenance: dict[str, Any] = {}
        self._last_emitted_budget: dict[str, Any] | None = None
        self.compact_threshold = compact_threshold
        self.target_usage = target_usage
        self.protected_recent = protected_recent
        self.protected_tool_results = protected_tool_results
        self.truncate_chars = truncate_chars
        self.compaction_notice_enabled = compaction_notice_enabled
        self.compaction_notice_token_reserve = compaction_notice_token_reserve
        self.compaction_notice_verbosity = compaction_notice_verbosity
        self.compaction_notice_min_level = compaction_notice_min_level
        self.output_reserve_fraction = output_reserve_fraction
        if token_meter not in _VALID_TOKEN_METERS:
            logger.warning(
                f"context-simple: unknown token_meter {token_meter!r} (expected "
                f"one of {_VALID_TOKEN_METERS!r}); falling back to "
                f"{TOKEN_METER_ESTIMATE!r}"
            )
            token_meter = TOKEN_METER_ESTIMATE
        self.token_meter = token_meter
        self._hooks = hooks
        self.max_tool_result_bytes = max_tool_result_bytes
        self._last_compaction_stats: dict[str, Any] | None = None
        # Real-usage token meter state (see _on_llm_response /
        # _measure_working_tokens). `_last_measured_prompt_tokens` holds the
        # most recent real usage observed via `llm:response`
        # (input_tokens + cache_write_tokens), or None before the first one
        # arrives this session. `_last_token_meter_stats` is the
        # always-populated observability surface (updated on every
        # get_messages_for_request() call, regardless of whether compaction
        # fires) so evals can read estimator-vs-real drift even in
        # "estimate" mode -- see README "Real-usage token meter".
        self._last_measured_prompt_tokens: int | None = None
        self._last_token_meter_stats: dict[str, Any] | None = None
        # A Loop that understands foreground ownership claims this once, then
        # writes only its successful foreground response usage through the
        # recorder.  Generic llm:response remains a compatibility fallback
        # until that persistent claim, but can never overwrite owned data.
        self._foreground_usage_claimed = False
        self._foreground_usage_stale = False
        self._system_prompt_factory: Callable[[], Awaitable[str]] | None = None
        self._request_retained_contents: frozenset[str] = frozenset()
        self._request_protected_seqs: set[int] = set()
        # Request-retention's optional provider-directed fit is deliberately
        # transient. It is set only by get_messages_for_request_retaining()
        # and restored before that capability returns or raises.
        self._request_hard_fit = False
        # A retention request rolls back its sticky decisions until its
        # validated hard-fit compaction event is handed to hooks. This flag is
        # request-local and is always restored by the retention wrapper.
        self._request_compaction_delivery_started = False
        self._request_retention_depth = 0
        # The measured capability stages the legacy fallback too, so a
        # callback cancellation cannot publish a compaction no request sent.
        self._defer_compaction_delivery = False

        # --- Sticky compaction decision state ---
        # Compaction decisions (remove / truncate / stub) are keyed by a
        # stable per-message sequence id (metadata["_seq"], assigned in
        # add_message()) rather than by list index, because indices shift
        # as history grows and as the ephemeral view is rebuilt every call.
        # Once a message's fate is decided, it is NEVER reconsidered -- this
        # is what keeps the returned view's shared prefix byte-stable across
        # calls where the underlying history only grew by a turn or two,
        # instead of the whole compaction decision being re-derived (and
        # potentially shifting) on every single get_messages_for_request()
        # call. See _apply_sticky_decisions() / _compact_ephemeral().
        self._next_seq: int = 0
        self._removed_seqs: set[int] = set()
        self._truncated_seqs: set[int] = set()
        self._stubbed_seqs: set[int] = set()
        # Cumulative highest progressive strategy level (1-8) ever reached.
        # Reported in compaction stats / notice so the LLM sees the total
        # accumulated effect, not just the most recent escalation step.
        self._sticky_level: int = 0

    @staticmethod
    def _encode_tool_result_text(text: str) -> bytes:
        """Encode direct text, replacing lone surrogates for ingress accounting."""
        return text.encode("utf-8", errors="replace")

    @classmethod
    def _utf8_safe_prefix(cls, text: str, max_bytes: int) -> str:
        """Return a valid UTF-8 prefix, replacing lone surrogates when clipped."""
        return cls._encode_tool_result_text(text)[:max_bytes].decode(
            "utf-8", errors="ignore"
        )

    @staticmethod
    def _direct_text_block_text(block: Any) -> str | None:
        """Return text only from the two supported direct text block shapes."""
        if isinstance(block, TextBlock):
            return block.text
        if (
            isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        ):
            return block["text"]
        return None

    @staticmethod
    def _copy_text_block_with_text(block: Any, text: str) -> Any:
        """Copy a recognized text block while preserving its other fields."""
        if isinstance(block, TextBlock):
            return block.model_copy(update={"text": text})
        return {**block, "text": text}

    def _limit_tool_result_text_at_ingress(
        self, message: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """Bound direct tool-result text without mutating the caller's message.

        This intentionally handles only a string content value and direct
        ``TextBlock``/``{"type": "text"}`` entries in a top-level content list.
        Other blocks, nested structures, and unknown shapes remain untouched.
        """
        if message.get("role") != "tool":
            return message, None

        content = message.get("content")
        content_kind: str | None = None
        if isinstance(content, str):
            original_text_utf8_bytes = len(self._encode_tool_result_text(content))
            content_kind = "string"
        elif isinstance(content, list):
            original_text_utf8_bytes = sum(
                len(self._encode_tool_result_text(text))
                for block in content
                if (text := self._direct_text_block_text(block)) is not None
            )
            content_kind = "text_blocks"
        else:
            return message, None

        if original_text_utf8_bytes <= self.max_tool_result_bytes:
            return message, None

        marker = _TOOL_RESULT_INGRESS_MARKER.format(
            original_text_utf8_bytes=original_text_utf8_bytes
        )
        marker_bytes = len(self._encode_tool_result_text(marker))
        remaining_text_bytes = self.max_tool_result_bytes - marker_bytes

        if isinstance(content, str):
            limited_content = self._utf8_safe_prefix(content, remaining_text_bytes) + marker
        else:
            limited_content: list[Any] = []
            marker_added = False
            for block in content:
                text = self._direct_text_block_text(block)
                if text is None:
                    limited_content.append(block)
                    continue
                if marker_added:
                    continue

                prefix = self._utf8_safe_prefix(text, remaining_text_bytes)
                prefix_bytes = len(self._encode_tool_result_text(prefix))
                if prefix_bytes == len(self._encode_tool_result_text(text)):
                    limited_content.append(self._copy_text_block_with_text(block, prefix))
                    remaining_text_bytes -= prefix_bytes
                    continue

                limited_content.append(
                    self._copy_text_block_with_text(block, prefix + marker)
                )
                marker_added = True

        stored_text_utf8_bytes = (
            len(self._encode_tool_result_text(limited_content))
            if isinstance(limited_content, str)
            else sum(
                len(self._encode_tool_result_text(text))
                for block in limited_content
                if (text := self._direct_text_block_text(block)) is not None
            )
        )
        event_data = {
            "tool_name": message.get("name"),
            "tool_call_id": message.get("tool_call_id"),
            "original_text_utf8_bytes": original_text_utf8_bytes,
            "stored_text_utf8_bytes": stored_text_utf8_bytes,
            "max_tool_result_bytes": self.max_tool_result_bytes,
            "content_kind": content_kind,
        }
        return {**message, "content": limited_content}, event_data

    async def _emit_tool_result_ingress_truncation(self, event_data: dict[str, Any]) -> None:
        """Emit ingress observability without ever blocking tool-result admission."""
        if self._hooks is not None:
            try:
                await self._hooks.emit("context:tool_result_ingress_truncated", event_data)
                return
            except Exception:
                emission_status = "event emission failed"
        else:
            emission_status = "no hooks are registered"

        logger.warning(
            "context-simple: tool result truncated at ingress (%s; tool_name=%r, "
            "tool_call_id=%r, original_text_utf8_bytes=%d, stored_text_utf8_bytes=%d, "
            "max_tool_result_bytes=%d); retrieve missing content using narrower "
            "read/query parameters; do not repeat state-changing actions just to "
            "recover output.",
            emission_status,
            event_data["tool_name"],
            event_data["tool_call_id"],
            event_data["original_text_utf8_bytes"],
            event_data["stored_text_utf8_bytes"],
            event_data["max_tool_result_bytes"],
        )

    async def _emit_budget(self) -> None:
        """Publish the effective budget where a session record can see it.

        `_derive_budget` logs its result, but a log line is not an artifact:
        it never reaches the session record and does not survive the process.
        So "what budget did this session actually run on?" -- the first
        question anyone asks when compaction fires early or a cap is
        suspected -- was unanswerable after the fact.

        Emitted on CHANGE, not per request: the value is stable for most of a
        session, so a per-request event would be noise, while a change is
        always worth a line (a provider swap, a mid-session cap, a fallback
        kicking in).

        Emitted at the DELIVERY boundary, not when the budget is calculated. A
        request that raises or is cancelled rolls back and must leave no
        observable trace -- an invariant `test_request_retention` pins
        explicitly -- so this fires only once a view is actually being
        returned, the same boundary `context:compaction` commits at.

        Observability must never break a request, so a failed emission is
        logged and swallowed -- the same contract as
        `_emit_tool_result_ingress_truncation` and `context:compaction`.
        """
        payload = dict(self._budget_provenance)
        if not payload or payload == self._last_emitted_budget:
            return

        self._last_emitted_budget = payload
        if self._hooks is None:
            return
        try:
            await self._hooks.emit("context:budget", payload)
        except Exception as e:
            logger.warning(f"Could not emit budget event: {e}")

    async def add_message(self, message: dict[str, Any]) -> None:
        """Add a message to the context.

        Messages are always accepted. Compaction happens ephemerally when
        get_messages_for_request() is called before LLM requests.

        Tool results MUST be added even if over threshold, otherwise
        tool_use/tool_result pairing breaks.

        Timestamps are automatically added to message metadata for replay timing.
        Existing timestamps and metadata are preserved.
        """
        message, truncation_event = self._limit_tool_result_text_at_ingress(message)

        # Add timestamp in metadata if not already present (for replay timing)
        existing_meta = message.get("metadata") or {}
        if "timestamp" not in existing_meta:
            message = {
                **message,
                "metadata": {
                    **existing_meta,
                    "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds"),
                },
            }

        # Assign a stable sequence id used as the identity key for sticky
        # compaction decisions (see _apply_sticky_decisions). Every message
        # gets exactly one, assigned once, never reused/reassigned.
        message = {
            **message,
            "metadata": {**(message.get("metadata") or {}), "_seq": self._next_seq},
        }
        self._next_seq += 1

        # Add message (no rejection - compaction happens ephemerally)
        self.messages.append(message)
        if truncation_event is not None:
            await self._emit_tool_result_ingress_truncation(truncation_event)

        token_count = self._estimate_tokens(self.messages)
        # Report usage against the budget the last request actually ran on.
        # Dividing by `max_tokens` (a cap that is usually unset, and never
        # the governing number when a provider publishes a window) produced
        # a percentage of nothing meaningful.
        denominator = self._last_effective_budget or self.max_tokens_fallback
        usage = token_count / denominator if denominator else 0.0
        logger.debug(
            f"Added message: {message.get('role', 'unknown')} - "
            f"{len(self.messages)} total messages, {token_count:,} tokens "
            f"({usage:.1%})"
        )

    async def set_system_prompt_factory(
        self, factory: Callable[[], Awaitable[str]]
    ) -> None:
        """Set a factory function that produces fresh system prompt content.

        The factory will be called on EVERY get_messages_for_request() call,
        enabling dynamic content like @mentions to be re-processed each turn.

        This is the preferred approach for bundle-based system prompts. When
        set, the factory takes precedence over any static system messages
        stored via add_message().

        Args:
            factory: Async callable that returns the system prompt string.
                     The factory should handle @mention resolution, file
                     loading, and instruction assembly.
        """
        self._system_prompt_factory = factory
        logger.info("System prompt factory registered - will refresh on each request")

    async def get_messages_for_request_retaining(
        self,
        *,
        retain_contents: list[str],
        provider: Any | None = None,
        token_budget: int | None = None,
        hard_fit: bool = False,
    ) -> list[dict[str, Any]]:
        """Optional capability: retain current persisted injections for one view.

        Contents must exactly match admitted user-role messages marked
        ephemeral=True and persisted=True. Only the newest matching copy is
        protected. This neither admits messages nor changes their lifetime;
        the caller supplies the current delivery requirements on every call.
        `hard_fit=True` is a capability-only request to target the supplied
        effective budget rather than the normal target_usage fraction.

        A cancelled or failed assembly rolls back sticky decisions and
        accounting until validated hard-fit compaction delivery begins. Starting
        that delivery commits the view state; it does not imply an LLM/provider
        request was dispatched.
        """
        previous_contents = self._request_retained_contents
        previous_seqs = self._request_protected_seqs
        previous_hard_fit = self._request_hard_fit
        previous_delivery_started = self._request_compaction_delivery_started
        decisions = (
            self._removed_seqs.copy(),
            self._truncated_seqs.copy(),
            self._stubbed_seqs.copy(),
            self._sticky_level,
            self._last_compaction_stats,
            self._last_token_meter_stats,
        )
        self._request_retention_depth += 1
        try:
            self._request_retained_contents = frozenset(retain_contents)
            self._request_hard_fit = hard_fit
            self._request_compaction_delivery_started = False
            return await self.get_messages_for_request(token_budget, provider)
        except BaseException:
            # A failed/cancelled assembly rolls back state until the validated
            # hard-fit compaction event starts delivery. Canonical history is
            # unchanged throughout; beginning delivery is not provider dispatch.
            if not self._request_compaction_delivery_started:
                (
                    self._removed_seqs,
                    self._truncated_seqs,
                    self._stubbed_seqs,
                    self._sticky_level,
                    self._last_compaction_stats,
                    self._last_token_meter_stats,
                ) = decisions
            raise
        finally:
            self._request_retained_contents = previous_contents
            self._request_protected_seqs = previous_seqs
            self._request_hard_fit = previous_hard_fit
            self._request_retention_depth -= 1
            if self._request_retention_depth:
                # A nested call propagates delivery to its outer handler,
                # which owns the eventual restoration of transient state.
                self._request_compaction_delivery_started = (
                    previous_delivery_started
                    or self._request_compaction_delivery_started
                )
            else:
                self._request_compaction_delivery_started = previous_delivery_started

    def claim_foreground_usage(self) -> Callable[..., bool]:
        """Claim the persistent foreground usage meter and return its recorder.

        This capability is intentionally not a temporary dispatch lease:
        helper/naming/evaluator responses can arrive after a prompt completes,
        so a ContextVar or an around-send flag would reopen the generic hook
        race.  The first claim discards an unowned hook reading; later claims
        are idempotent and return the same stateful recorder.
        """
        if not self._foreground_usage_claimed:
            self._foreground_usage_claimed = True
            self._last_measured_prompt_tokens = None
            self._foreground_usage_stale = False
        return self._record_foreground_usage

    def _record_foreground_usage(
        self, *, input_tokens: Any, cache_write_tokens: Any = 0
    ) -> bool:
        """Record one scoped foreground response, refusing malformed usage."""
        valid = (
            isinstance(input_tokens, int)
            and not isinstance(input_tokens, bool)
            and input_tokens >= 0
            and isinstance(cache_write_tokens, int)
            and not isinstance(cache_write_tokens, bool)
            and cache_write_tokens >= 0
        )
        if not valid:
            # Keep an earlier owned reading, but state plainly that it is not a
            # fresh scoped value.  Turning malformed input into zero would be a
            # false measurement and could suppress necessary compaction.
            self._foreground_usage_stale = True
            logger.debug(
                "context-simple: foreground usage was malformed; retaining the "
                "previous owned reading as stale"
            )
            return False
        self._last_measured_prompt_tokens = input_tokens + cache_write_tokens
        self._foreground_usage_stale = False
        return True

    @staticmethod
    def _is_nonnegative_int(value: Any) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0

    def _measured_transaction(self) -> "_MeasuredViewTransaction":
        """Snapshot all sticky decisions which a measured candidate may stage."""
        return _MeasuredViewTransaction(self)

    def _append_compaction_notice(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Return the one safe, qualitative tail notice for a measured view."""
        result = list(messages)
        if (
            self.compaction_notice_enabled
            and self._last_compaction_stats
            and self._last_compaction_stats.get("strategy_level", 0)
            >= self.compaction_notice_min_level
            and not self._tail_has_unanswered_tool_calls(result)
        ):
            notice = self._format_compaction_notice()
            if notice:
                result.append(
                    {
                        "role": "user",
                        "content": notice,
                        "metadata": {
                            "source": "context-compaction",
                            "ephemeral": True,
                        },
                    }
                )
        return result

    def _measured_budget_decision(
        self, envelope: dict[str, Any]
    ) -> tuple[int, int, int, str] | None:
        """Validate old hard-safety fields and additive native-count fields.

        A non-dictionary callback envelope is a broken capability contract, not
        an unavailable measurement.  Missing/malformed *advertised*
        measurement, however, is explicitly an unavailable count so an older
        provider can retain its compatibility path.  The pre-existing
        estimated-input/limit pair remains mandatory whenever a decision is
        supplied, regardless of measurement availability.
        """
        decision = envelope.get("budget_decision")
        if decision is None:
            return None
        if not isinstance(decision, dict):
            raise TypeError("count_view returned a non-dictionary budget_decision")
        estimated = decision.get("estimated_input_tokens")
        limit = decision.get("input_limit_tokens")
        if not self._is_nonnegative_int(estimated) or not self._is_nonnegative_int(
            limit
        ):
            raise TypeError(
                "count_view budget_decision requires nonnegative integer "
                "estimated_input_tokens and input_limit_tokens"
            )
        measurement = decision.get("measurement")
        if not isinstance(measurement, dict):
            return None
        count = measurement.get("input_tokens")
        if (
            measurement.get("kind") != "provider_count"
            or not isinstance(measurement.get("source"), str)
            or not measurement["source"]
            or not self._is_nonnegative_int(count)
            or not self._is_nonnegative_int(estimated)
            or not self._is_nonnegative_int(limit)
            or estimated < count
        ):
            return None
        return count, estimated, limit, measurement["source"]

    def _measured_hard_oversize(self, envelope: dict[str, Any]) -> bool:
        """Whether a validated callback envelope exceeds its old hard limit."""
        decision = envelope.get("budget_decision")
        return (
            isinstance(decision, dict)
            and decision["estimated_input_tokens"] > decision["input_limit_tokens"]
        )

    async def _count_measured_view(
        self, count_view: Callable[[list[dict[str, Any]]], Awaitable[dict[str, Any]]],
        view: list[dict[str, Any]],
    ) -> tuple[
        list[dict[str, Any]],
        dict[str, Any],
        tuple[int, int, int, str] | None,
    ]:
        """Count exactly the provider-facing candidate, without reassembly."""
        public_view = self._strip_internal_metadata(self._append_compaction_notice(view))
        envelope = await count_view(public_view)
        if not isinstance(envelope, dict) or "dispatch" not in envelope:
            raise TypeError("count_view must return {dispatch, budget_decision}")
        return public_view, envelope, self._measured_budget_decision(envelope)

    async def _fit_measured_output(
        self,
        fit_output: Callable[
            [list[dict[str, Any]], dict[str, Any]],
            Awaitable[dict[str, Any] | None],
        ],
        view: list[dict[str, Any]],
        attempt: dict[str, Any],
    ) -> tuple[dict[str, Any], tuple[int, int, int, str], int] | None:
        """Ask the request owner for a bounded output-reserve fit."""
        fitted = await fit_output(view, attempt)
        if fitted is None:
            return None
        if not isinstance(fitted, dict) or "dispatch" not in fitted:
            raise TypeError("fit_output must return {dispatch, budget_decision, count_calls}")
        extra_count_calls = fitted.get("count_calls")
        if (
            not isinstance(extra_count_calls, int)
            or isinstance(extra_count_calls, bool)
            or extra_count_calls <= 0
        ):
            raise TypeError("fit_output count_calls must be a positive integer")
        measurement = self._measured_budget_decision(fitted)
        if measurement is None:
            raise TypeError("fit_output must return a usable provider_count measurement")
        _, estimated, limit, _ = measurement
        if estimated > limit:
            raise ContextLengthError(
                "Context cannot fit protected content within the provider input "
                "limit; required content was not discarded."
            )
        return fitted, measurement, extra_count_calls

    def _measured_apply_rung(
        self,
        rung: int,
        view: list[dict[str, Any]],
        developer_seqs: set[int],
    ) -> tuple[list[dict[str, Any]], bool]:
        """Apply one existing legal compaction rung without local success claims."""
        systems = [dict(msg) for msg in view if msg.get("role") == "system"]
        working = [dict(msg) for msg in view if msg.get("role") != "system"]
        before = (
            self._removed_seqs.copy(),
            self._truncated_seqs.copy(),
            self._stubbed_seqs.copy(),
        )
        tool_indices = [
            index for index, message in enumerate(working) if message.get("role") == "tool"
        ]
        protected_tools = self._protected_tool_indices(tool_indices)

        def truncate(indices: list[int]) -> None:
            # Unlike the legacy local-estimate helper, this exhausts the
            # rung.  Only an actual transformation becomes a sticky decision,
            # so a short/protected wave remains a genuine no-op.
            for index in indices:
                if index in protected_tools:
                    continue
                message = working[index]
                if message.get("_truncated"):
                    continue
                reduced = self._truncate_tool_result(message)
                if reduced is message:
                    continue
                working[index] = reduced
                self._record_truncated(message)

        if rung == 1:
            truncate(tool_indices[: int(len(tool_indices) * 0.25)])
        elif rung == 2:
            truncate(
                tool_indices[
                    int(len(tool_indices) * 0.25) : int(len(tool_indices) * 0.50)
                ]
            )
        elif rung in (3, 5, 7):
            protection = {
                3: self.protected_recent,
                5: self.protected_recent * 0.6,
                7: self.protected_recent * 0.3,
            }[rung]
            working, _, _, _ = self._remove_messages_with_protection(
                working,
                -1,  # exhaust legal candidates; count_view decides success
                protected_recent=protection,
                system_tokens=self._estimate_tokens(systems),
                additional_protected_seqs=developer_seqs,
            )
        elif rung == 4:
            truncate(
                tool_indices[
                    int(len(tool_indices) * 0.50) : int(len(tool_indices) * 0.75)
                ]
            )
        elif rung == 6:
            truncate(tool_indices)
        elif rung == 8:
            user_indices = [
                index
                for index, message in enumerate(working)
                if message.get("role") == "user"
            ]
            first_user = user_indices[0] if user_indices else None
            last_user = user_indices[-1] if user_indices else None
            # This is the legacy level-eight machine-prefix rule, deliberately
            # retaining actual human boundaries and request-retained bodies.
            if first_user is not None and first_user != last_user:
                first = working[first_user]
                reduced = self._stub_user_message(first)
                if (
                    not self._is_request_protected(first, developer_seqs)
                    and not _is_human_message(first)
                    and not first.get("_stubbed")
                    and reduced is not first
                ):
                    working[first_user] = reduced
                    self._record_stubbed(first)
            protected_boundary = int(len(working) * (1 - self.protected_recent * 0.3))
            for index, message in list(enumerate(working)):
                if (
                    index >= protected_boundary
                    or index == last_user
                    or not message.get("_stubbed")
                    or self._is_request_protected(message, developer_seqs)
                ):
                    continue
                self._record_removed(message)
            working = [
                message
                for message in working
                if self._extract_seq(message) not in self._removed_seqs
            ]
        else:  # Defensive boundary for this private fixed eight-rung policy.
            raise ValueError(f"unknown measured compaction rung {rung}")

        after = (self._removed_seqs, self._truncated_seqs, self._stubbed_seqs)
        changed = after != before
        return systems + working, changed

    def _stage_measured_stats(
        self,
        *,
        initial_view: list[dict[str, Any]],
        final_view: list[dict[str, Any]],
        strategy_level: int,
        budget: int,
        measured_before: int,
        measured_after: int,
        measurement_source: str,
        trigger: float,
        target: int,
        outcome: str,
        count_calls: int,
    ) -> None:
        """Stage telemetry for a candidate; transaction commit makes it sticky."""
        self._sticky_level = max(self._sticky_level, strategy_level)
        self._last_compaction_stats = {
            "before_tokens": self._estimate_tokens(initial_view),
            "after_tokens": self._estimate_tokens(final_view),
            "before_messages": len(initial_view),
            "after_messages": len(final_view),
            "messages_removed": len(self._removed_seqs),
            "messages_truncated": len(self._truncated_seqs),
            "user_messages_stubbed": len(self._stubbed_seqs),
            "system_messages_preserved": sum(
                message.get("role") == "system" for message in final_view
            ),
            "strategy_level": self._sticky_level,
            "budget": budget,
            "target_tokens": target,
            "protected_recent": self.protected_recent,
            "protected_tool_results": self.protected_tool_results,
            "measurement_kind": "provider_count",
            "measurement_source": measurement_source,
            "measured_before": measured_before,
            "measured_after": measured_after,
            "trigger": trigger,
            "policy_budget": budget,
            "outcome": outcome,
            "count_calls": count_calls,
        }

    async def get_measured_request_view(
        self,
        *,
        provider: Any,
        retain_contents: list[str],
        count_view: Callable[[list[dict[str, Any]]], Awaitable[dict[str, Any]]],
        fit_output: Callable[
            [list[dict[str, Any]], dict[str, Any]],
            Awaitable[dict[str, Any] | None],
        ]
        | None = None,
    ) -> dict[str, Any]:
        """Build and reduce one complete request using provider-native counts.

        The loop owns request construction.  Context owns only canonical
        history, its one materialized policy snapshot, and the established
        eight legal reduction rungs.  The returned envelope is the exact
        callback object that counted the final provider-facing request. After
        all eight rungs, an optional request-owner callback may reduce only
        its output reserve and return a newly counted envelope.
        """
        if self.token_meter != TOKEN_METER_ACTUAL:
            raise RuntimeError("measured request views require token_meter='actual'")

        previous_contents = self._request_retained_contents
        previous_seqs = self._request_protected_seqs
        previous_hard_fit = self._request_hard_fit
        previous_delivery = self._request_compaction_delivery_started
        cycle_snapshot = (
            self._removed_seqs.copy(),
            self._truncated_seqs.copy(),
            self._stubbed_seqs.copy(),
            self._sticky_level,
            self._last_compaction_stats,
            self._last_token_meter_stats,
        )
        transaction: _MeasuredViewTransaction | None = None
        try:
            self._request_retained_contents = frozenset(retain_contents)
            self._request_hard_fit = False
            self._request_compaction_delivery_started = False

            # Materialize the dynamic factory exactly once.  The callback adds
            # all loop-owned overlays to every candidate from this same base.
            if self._system_prompt_factory:
                system_content = await self._system_prompt_factory()
                working = [{"role": "system", "content": system_content}] + [
                    message
                    for message in self.messages
                    if message.get("role") != "system"
                    or (message.get("metadata") or {}).get("source") == "hook"
                ]
            else:
                working = list(self.messages)

            developer_seqs = {
                seq
                for message in working
                if message.get("role") == "developer"
                and (seq := self._extract_seq(message)) is not None
            }
            self._request_protected_seqs = self._protected_sequences(working)
            budget = self._calculate_budget(None, provider)
            if self.compaction_notice_enabled:
                effective_budget = budget - self.compaction_notice_token_reserve
                if effective_budget <= 0:
                    logger.warning(
                        "compaction_notice_token_reserve consumes measured request "
                        "budget; ignoring it for this request"
                    )
                    effective_budget = budget
            else:
                effective_budget = budget
            self._last_effective_budget = effective_budget
            trigger = effective_budget * self.compact_threshold
            target = int(effective_budget * self.target_usage)
            initial = [
                *[dict(message) for message in working if message.get("role") == "system"],
                *self._apply_sticky_decisions(
                    [message for message in working if message.get("role") != "system"],
                    additional_protected_seqs=developer_seqs,
                ),
            ]
            initial_view, initial_attempt, initial_measurement = await self._count_measured_view(
                count_view, initial
            )
            if initial_measurement is None:
                if self._measured_hard_oversize(initial_attempt):
                    raise ContextLengthError(
                        "Context cannot fit protected content within the provider "
                        "input limit."
                    )
                # Native count availability is dynamic.  Preserve the old
                # actual-meter/estimate behavior from this already-created
                # snapshot; never call the factory or replay loop overlays.
                legacy_count, _, legacy_estimate = self._measure_working_tokens(initial)
                legacy_needed = self._should_compact(
                    legacy_count, effective_budget
                ) or (
                    bool(self._request_retained_contents)
                    and legacy_estimate > effective_budget
                )
                if legacy_needed:
                    transaction = _MeasuredViewTransaction(self, snapshot=cycle_snapshot)
                    self._defer_compaction_delivery = True
                    try:
                        fallback = await self._compact_ephemeral(
                            effective_budget,
                            working,
                            additional_protected_seqs=developer_seqs,
                        )
                    finally:
                        self._defer_compaction_delivery = False
                    changed = (
                        self._removed_seqs,
                        self._truncated_seqs,
                        self._stubbed_seqs,
                    ) != cycle_snapshot[:3]
                    if changed:
                        fallback_view, fallback_attempt, _fallback_measurement = (
                            await self._count_measured_view(count_view, fallback)
                        )
                        if self._measured_hard_oversize(fallback_attempt):
                            transaction.rollback()
                            raise ContextLengthError(
                                "Context cannot fit protected content within the "
                                "provider input limit."
                            )
                        return {
                            "base_view": fallback_view,
                            "final_attempt": fallback_attempt,
                            "outcome": "measurement_unavailable",
                            "measured_before": None,
                            "measured_after": None,
                            "policy_budget": effective_budget,
                            "trigger": trigger,
                            "target": target,
                            "count_calls": 2,
                            "transaction": transaction,
                        }
                    transaction.rollback()
                    transaction = None
                return {
                    "base_view": initial_view,
                    "final_attempt": initial_attempt,
                    "outcome": "measurement_unavailable",
                    "measured_before": None,
                    "measured_after": None,
                    "policy_budget": effective_budget,
                    "trigger": trigger,
                    "target": target,
                    "count_calls": 1,
                    "transaction": None,
                }

            before, estimated, limit, measurement_source = initial_measurement
            known_hard_oversize = estimated > limit
            needs_compaction = before >= trigger or estimated > limit
            if not needs_compaction:
                return {
                    "base_view": initial_view,
                    "final_attempt": initial_attempt,
                    "outcome": "not_needed",
                    "measured_before": before,
                    "measured_after": before,
                    "policy_budget": effective_budget,
                    "trigger": trigger,
                    "target": target,
                    "count_calls": 1,
                    "transaction": None,
                }

            transaction = _MeasuredViewTransaction(self, snapshot=cycle_snapshot)
            candidate = initial
            final_view = initial_view
            final_attempt = initial_attempt
            final_measurement = initial_measurement
            count_calls = 1
            changed_any = False
            last_changed_rung = 0
            for rung in range(1, 9):
                candidate, changed = self._measured_apply_rung(
                    rung, candidate, developer_seqs
                )
                if not changed:
                    continue
                changed_any = True
                last_changed_rung = rung
                # Stage qualitative notice/statistics before counting this
                # complete candidate; never append a post-count outcome notice.
                self._stage_measured_stats(
                    initial_view=initial,
                    final_view=candidate,
                    strategy_level=rung,
                    budget=effective_budget,
                    measured_before=before,
                    measured_after=final_measurement[0],
                    measurement_source=final_measurement[3],
                    trigger=trigger,
                    target=target,
                    outcome="compacting",
                    count_calls=count_calls + 1,
                )
                final_view, final_attempt, final_measurement = await self._count_measured_view(
                    count_view, candidate
                )
                count_calls += 1
                if final_measurement is None:
                    known_hard_oversize = (
                        known_hard_oversize
                        or self._measured_hard_oversize(final_attempt)
                    )
                    transaction.rollback()
                    if known_hard_oversize:
                        raise ContextLengthError(
                            "Context cannot fit protected content within the provider "
                            "input limit after its recount became unavailable."
                        )
                    return {
                        "base_view": initial_view,
                        "final_attempt": initial_attempt,
                        "outcome": "measurement_unavailable",
                        "measured_before": before,
                        "measured_after": before,
                        "policy_budget": effective_budget,
                        "trigger": trigger,
                        "target": target,
                        "count_calls": count_calls,
                        "transaction": None,
                    }
                measured_after, estimated_after, limit_after, measured_source = final_measurement
                known_hard_oversize = known_hard_oversize or estimated_after > limit_after
                if measured_after <= target and estimated_after <= limit_after:
                    self._stage_measured_stats(
                        initial_view=initial,
                        final_view=candidate,
                        strategy_level=rung,
                        budget=effective_budget,
                        measured_before=before,
                        measured_after=measured_after,
                        measurement_source=measured_source,
                        trigger=trigger,
                        target=target,
                        outcome="target_reached",
                        count_calls=count_calls,
                    )
                    return {
                        "base_view": final_view,
                        "final_attempt": final_attempt,
                        "outcome": "target_reached",
                        "measured_before": before,
                        "measured_after": measured_after,
                        "policy_budget": effective_budget,
                        "trigger": trigger,
                        "target": target,
                        "count_calls": count_calls,
                        "transaction": transaction,
                    }

            measured_after, estimated_after, limit_after, measured_source = final_measurement
            if estimated_after > limit_after:
                if fit_output is not None:
                    fitted = await self._fit_measured_output(
                        fit_output, final_view, final_attempt
                    )
                    if fitted is not None:
                        fitted_attempt, fitted_measurement, extra_count_calls = fitted
                        (
                            fitted_after,
                            _fitted_estimated,
                            _fitted_limit,
                            fitted_source,
                        ) = fitted_measurement
                        fitted_count_calls = count_calls + extra_count_calls
                        if changed_any:
                            self._stage_measured_stats(
                                initial_view=initial,
                                final_view=candidate,
                                strategy_level=last_changed_rung,
                                budget=effective_budget,
                                measured_before=before,
                                measured_after=fitted_after,
                                measurement_source=fitted_source,
                                trigger=trigger,
                                target=target,
                                outcome="reduced_output",
                                count_calls=fitted_count_calls,
                            )
                        else:
                            # Output relief alone creates no sticky context state.
                            transaction.rollback()
                            transaction = None
                        return {
                            "base_view": final_view,
                            "final_attempt": fitted_attempt,
                            "outcome": "reduced_output",
                            "measured_before": before,
                            "measured_after": fitted_after,
                            "policy_budget": effective_budget,
                            "trigger": trigger,
                            "target": target,
                            "count_calls": fitted_count_calls,
                            "transaction": transaction,
                        }
                transaction.rollback()
                raise ContextLengthError(
                    "Context cannot fit protected content within the provider input "
                    "limit; required content was not discarded."
                )
            if not changed_any:
                # A protected floor is a real answer to the one exact probe,
                # not a new compaction decision.  Do not manufacture sticky
                # stats/notices/events (or a transaction) from a no-op walk.
                transaction.rollback()
                return {
                    "base_view": initial_view,
                    "final_attempt": initial_attempt,
                    "outcome": "protected_floor",
                    "measured_before": before,
                    "measured_after": before,
                    "policy_budget": effective_budget,
                    "trigger": trigger,
                    "target": target,
                    "count_calls": count_calls,
                    "transaction": None,
                }
            self._stage_measured_stats(
                initial_view=initial,
                final_view=candidate,
                strategy_level=last_changed_rung,
                budget=effective_budget,
                measured_before=before,
                measured_after=measured_after,
                measurement_source=measured_source,
                trigger=trigger,
                target=target,
                outcome="protected_floor",
                count_calls=count_calls,
            )
            return {
                "base_view": final_view,
                "final_attempt": final_attempt,
                "outcome": "protected_floor",
                "measured_before": before,
                "measured_after": measured_after,
                "policy_budget": effective_budget,
                "trigger": trigger,
                "target": target,
                "count_calls": count_calls,
                "transaction": transaction,
            }
        except BaseException:
            if transaction is not None:
                transaction.rollback()
            raise
        finally:
            self._request_retained_contents = previous_contents
            self._request_protected_seqs = previous_seqs
            self._request_hard_fit = previous_hard_fit
            self._request_compaction_delivery_started = previous_delivery

    def _protected_sequences(self, messages: list[dict[str, Any]]) -> set[int]:
        humans = [msg for msg in messages if _is_human_message(msg)]
        protected = [humans[0], humans[-1]] if humans else []
        remaining = set(self._request_retained_contents)
        for msg in reversed(messages):
            meta = msg.get("metadata") or {}
            content = msg.get("content")
            if (
                msg.get("role") == "user"
                and meta.get("ephemeral") is True
                and meta.get("persisted") is True
                and isinstance(content, str)
                and content in remaining
            ):
                protected.append(msg)
                remaining.remove(content)
        if remaining:
            raise ValueError("Requested retained injection is not in admitted history")
        return {seq for msg in protected if (seq := self._extract_seq(msg)) is not None}

    def _is_request_protected(
        self,
        msg: dict[str, Any],
        additional_protected_seqs: set[int] | None = None,
    ) -> bool:
        seq = self._extract_seq(msg)
        return (
            seq in self._request_protected_seqs
            or (
                additional_protected_seqs is not None
                and seq in additional_protected_seqs
            )
        )

    def _check_retained_budget(
        self, messages: list[dict[str, Any]], budget: int
    ) -> None:
        if (
            (self._request_retained_contents or self._request_hard_fit)
            and self._estimate_tokens(messages) > budget
        ):
            raise ContextLengthError(
                "Context cannot fit the current injections and protected conversation "
                "within the estimated input budget; shorten the active instructions "
                "or use a larger context window. Required content was not discarded."
            )

    @staticmethod
    def _tail_has_unanswered_tool_calls(messages: list[dict[str, Any]]) -> bool:
        """Return whether the trailing tool-call group is incomplete.

        A tail can end on the assistant tool-call message itself or on one of
        several sibling tool results. A notice is safe only after every call
        declared by that final assistant message has a result.
        """
        trailing_result_ids: set[str] = set()
        for message in reversed(messages):
            if message.get("role") == "tool":
                tool_call_id = message.get("tool_call_id")
                if tool_call_id:
                    trailing_result_ids.add(tool_call_id)
                continue
            if message.get("role") != "assistant" or not message.get("tool_calls"):
                return False
            declared_ids = {
                tool_call.get("id") or tool_call.get("tool_call_id")
                for tool_call in message["tool_calls"]
            }
            return bool(declared_ids - trailing_result_ids)
        return False

    async def get_messages_for_request(
        self,
        token_budget: int | None = None,
        provider: Any | None = None,
    ) -> list[dict[str, Any]]:
        """
        Get messages ready for an LLM request.

        If a system prompt factory is registered, it is called to produce fresh
        system content on EVERY request. This enables dynamic @mentions and
        bundle instructions to be re-processed each turn.

        Applies EPHEMERAL compaction if needed - returns a NEW list without
        modifying self.messages. The original history is always preserved.

        If compaction occurs and notice is enabled, a tail system-reminder
        informs the LLM about what was compacted without changing its prefix.

        Args:
            token_budget: Optional explicit token limit (deprecated, prefer provider).
            provider: Optional provider instance for dynamic budget calculation.
                If provided, budget = context_window - max_output_tokens - safety_margin,
                then capped at `max_tokens` when one is configured.

        Returns:
            Messages ready for LLM request, compacted if necessary.
        """
        budget = self._calculate_budget(token_budget, provider)
        self._last_effective_budget = budget

        # Reserve token budget for potential compaction notice (if enabled)
        effective_budget = budget
        if self.compaction_notice_enabled:
            effective_budget = budget - self.compaction_notice_token_reserve
            if effective_budget <= 0:
                # Misconfiguration guard: if the reserve consumes the entire budget
                # (or more), _should_compact's `budget > 0` check would silently
                # force usage to 0, disabling compaction entirely rather than
                # loudly failing. Fall back to the full budget instead of a
                # non-positive effective budget - a reserve that swallows the
                # whole context is not a valid state to compact against.
                logger.warning(
                    f"compaction_notice_token_reserve ({self.compaction_notice_token_reserve:,}) "
                    f">= budget ({budget:,}); ignoring reserve for this request to avoid "
                    f"silently disabling compaction (effective budget would be {effective_budget:,})"
                )
                effective_budget = budget
            else:
                logger.debug(
                    f"Reserved {self.compaction_notice_token_reserve} tokens for potential notice "
                    f"(effective budget: {effective_budget:,})"
                )

        # Determine working messages based on whether factory is set
        if self._system_prompt_factory:
            # Factory mode: get fresh system content, exclude stored system messages
            # BUT preserve hook-injected system messages (they have metadata.source = "hook")
            system_content = await self._system_prompt_factory()
            system_message = {"role": "system", "content": system_content}

            # Filter out static system messages but keep hook-injected ones
            # Hook injections have metadata.source = "hook" and should be preserved
            conversation_messages = [
                msg
                for msg in self.messages
                if msg.get("role") != "system"
                or (msg.get("metadata") or {}).get("source") == "hook"
            ]
            working_messages = [system_message] + conversation_messages
            logger.debug(
                f"System prompt factory produced {len(system_content):,} chars, "
                f"{len(conversation_messages)} conversation messages"
            )
        else:
            # Static mode: use messages as-is (may include stored system messages)
            working_messages = list(self.messages)

        self._request_protected_seqs = self._protected_sequences(working_messages)
        self._check_retained_budget(
            [
                m
                for m in working_messages
                if m.get("role") == "system" or self._is_request_protected(m)
            ],
            effective_budget,
        )

        # Materialize every recorded sticky decision before measuring or deciding
        # whether more compaction is needed. Canonical history stays untouched:
        # only this request view receives the old removal/truncation/stubbing
        # transforms. New messages have no decision and remain visible.
        system_messages = [
            dict(msg) for msg in working_messages if msg.get("role") == "system"
        ]
        sticky_view = system_messages + self._apply_sticky_decisions(
            [msg for msg in working_messages if msg.get("role") != "system"]
        )
        has_sticky_decisions = bool(
            self._removed_seqs or self._truncated_seqs or self._stubbed_seqs
        )

        token_count, meter_source, estimated_tokens = self._measure_working_tokens(
            sticky_view
        )
        self._last_token_meter_stats = {
            "mode": self.token_meter,
            "source": meter_source,
            "used_tokens": token_count,
            "estimated_tokens": estimated_tokens,
            "estimate_scope": (
                "text_only"
                if estimate_messages(sticky_view).has_unmeasured_images
                else "all_text"
            ),
            "measured_tokens": self._last_measured_prompt_tokens,
            "foreground_usage_stale": self._foreground_usage_stale,
            "budget": effective_budget,
            "ratio": (token_count / effective_budget) if effective_budget > 0 else None,
        }

        # Check whether this request needs a new compaction escalation. A
        # provider-directed hard fit applies at the full forced budget, not at
        # the ordinary compact_threshold or target_usage fraction of it.
        retained_view_over_budget = (
            bool(self._request_retained_contents)
            and estimated_tokens > effective_budget
        )
        hard_fit_view_over_budget = (
            self._request_hard_fit and estimated_tokens > effective_budget
        )
        should_compact = (
            self._should_compact(token_count, effective_budget)
            or retained_view_over_budget
            or hard_fit_view_over_budget
        )
        previous_compaction_stats = self._last_compaction_stats
        deferred_hard_fit_stats: dict[str, Any] | None = None
        if should_compact:
            # Compact EPHEMERALLY - returns new list, working_messages unchanged
            compacted = await self._compact_ephemeral(
                effective_budget, working_messages
            )
            logger.info(
                f"Ephemeral compaction: {len(working_messages)} -> {len(compacted)} messages for this request"
            )
            if (
                self._request_hard_fit
                and self._last_compaction_stats is not previous_compaction_stats
            ):
                deferred_hard_fit_stats = self._last_compaction_stats

        elif has_sticky_decisions:
            # Sticky decisions are sender-view state, not only an input to a
            # future escalation. Return that stable reduced view even below the
            # ordinary threshold (including inactive/direct fetches).
            compacted = sticky_view
        else:
            self._check_retained_budget(working_messages, budget)
            await self._emit_budget()
            return self._strip_internal_metadata(working_messages)

        # Append compaction notice at the TAIL if enabled and level threshold met.
        # CRITICAL (prompt cache stability): this notice must never be inserted
        # into the prefix. Two things make the tail the only safe placement:
            #
        # 1. role: previously this was "system", which -- for the Anthropic
        #    provider -- gets extracted OUT of the conversation entirely and
        #    merged into the single top-level system content block. Using "user"
        #    keeps it in the conversation region, where ephemeral exclusion can
        #    recognize it.
        # 2. metadata.ephemeral=True + tail position: the provider's trailing
        #    ephemeral walk-back can exclude it without corrupting the cached
        #    prefix.
        #
        # The notice comes from cumulative sticky stats, so a stable view gets
        # exactly one byte-identical notice on every safe request.
        if self.compaction_notice_enabled and self._last_compaction_stats:
            level = self._last_compaction_stats.get("strategy_level", 0)
            # Do not interleave a user-role notice between an unanswered
            # assistant tool call and its result. Sticky stats persist, so it
            # safely reappears once the result reaches the history.
            if self._tail_has_unanswered_tool_calls(compacted):
                logger.debug(
                    "Skipping compaction notice this request: the trailing "
                    "tool-call group has unanswered calls; the notice would "
                    "interleave between tool_use and tool_result. It will be "
                    "appended on the next request instead."
                )
            elif level >= self.compaction_notice_min_level:
                notice = self._format_compaction_notice()
                if notice:
                    compacted.append(
                        {
                            "role": "user",
                            "content": notice,
                            "metadata": {
                                "source": "context-compaction",
                                "ephemeral": True,
                            },
                        }
                    )
                    logger.debug(
                        f"Appended compaction notice at tail (level {level}, "
                        f"verbosity: {self.compaction_notice_verbosity})"
                    )

        # Strip internal bookkeeping at the module boundary -- everything above
        # this point (sticky decisions, token accounting) still runs on messages
        # carrying `_seq`; only what leaves has it removed.
        self._check_retained_budget(compacted, budget)
        if deferred_hard_fit_stats is not None and self._hooks is not None:
            try:
                # This is the request-retention commit boundary. It follows all
                # final, notice-inclusive budget checks but does not mean the
                # provider request has been dispatched.
                self._request_compaction_delivery_started = True
                await self._hooks.emit("context:compaction", deferred_hard_fit_stats)
            except Exception as e:
                logger.warning(f"Could not emit compaction event: {e}")
        await self._emit_budget()
        return self._strip_internal_metadata(compacted)

    # Metadata keys that are internal bookkeeping only and must never cross
    # the module boundary into a provider-facing view. `_seq` is sticky
    # compaction identity (see _extract_seq): meaningless to a provider, and --
    # because _estimate_tokens stringifies the whole message dict -- it also
    # inflates the token estimate of every message carrying it.
    _INTERNAL_METADATA_KEYS = frozenset({"_seq"})

    def _strip_internal_metadata(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Return a provider-facing view with internal-only metadata removed.

        CRITICAL: stored history must KEEP `_seq` -- the sticky decision store
        is keyed on it, so losing it would silently break stickiness (and with
        it, prefix stability). The returned view can share dict objects with
        `self.messages`: the no-compaction path returns stored dicts directly,
        and even the compacted path's `dict(msg)` shallow copies share the SAME
        nested metadata dict. So this NEVER mutates in place -- any message
        needing a strip is rebuilt as a new dict with a new metadata dict, and
        the stored original is left untouched.

        Messages with nothing to strip pass through by identity (no copy),
        which keeps this deterministic and byte-stable call over call.
        """
        result: list[dict[str, Any]] = []
        for msg in messages:
            meta = msg.get("metadata")
            if not isinstance(meta, dict) or self._INTERNAL_METADATA_KEYS.isdisjoint(
                meta
            ):
                result.append(msg)
                continue
            result.append(
                {
                    **msg,
                    "metadata": {
                        k: v
                        for k, v in meta.items()
                        if k not in self._INTERNAL_METADATA_KEYS
                    },
                }
            )
        return result

    async def get_messages(self) -> list[dict[str, Any]]:
        """
        Get ALL messages (full history, never compacted) for transcripts/debugging.

        This returns the complete, unmodified history - suitable for saving
        to transcript files for session persistence.
        """
        return list(self.messages)

    async def set_messages(self, messages: list[dict[str, Any]]) -> None:
        """Set messages from a saved transcript (for session resume).

        Sticky compaction decisions live only in this instance's memory (they
        are never persisted alongside the transcript), so a resumed session
        necessarily starts with a clean decision slate -- there is no stale
        state to reconcile against the incoming messages. Sequence ids are
        re-stamped deterministically (0..N-1, in transcript order) rather than
        trusting any `_seq` that might already be present in the incoming
        metadata, so identity stays internally consistent regardless of what
        produced the transcript.
        """
        restamped: list[dict[str, Any]] = []
        truncation_events: list[dict[str, Any]] = []
        for i, msg in enumerate(messages):
            msg, truncation_event = self._limit_tool_result_text_at_ingress(msg)
            if truncation_event is not None:
                truncation_events.append(truncation_event)
            meta = dict(msg.get("metadata") or {})
            meta["_seq"] = i
            restamped.append({**msg, "metadata": meta})
        self.messages = restamped
        self._next_seq = len(restamped)
        self._removed_seqs = set()
        self._truncated_seqs = set()
        self._stubbed_seqs = set()
        self._sticky_level = 0
        self._last_compaction_stats = None
        for truncation_event in truncation_events:
            await self._emit_tool_result_ingress_truncation(truncation_event)
        logger.info(f"Restored {len(messages)} messages to context")

    async def clear(self) -> None:
        """Clear all messages."""
        self.messages = []
        self._next_seq = 0
        self._removed_seqs = set()
        self._truncated_seqs = set()
        self._stubbed_seqs = set()
        self._sticky_level = 0
        self._last_compaction_stats = None
        self._last_measured_prompt_tokens = None
        self._last_token_meter_stats = None
        # Ownership is a capability claim, not a turn-local meter value.  A
        # cleared Context must not permit utility hook traffic to take it back.
        self._foreground_usage_stale = self._foreground_usage_claimed
        logger.info("Context cleared")

    async def should_compact(self) -> bool:
        """Check if context should be compacted.

        Note: This module uses ephemeral compaction during get_messages_for_request(),
        so this always returns False. The actual compaction check happens internally.
        This method exists to satisfy the ContextManager protocol.
        """
        return False

    async def compact(self) -> None:
        """Compact the context.

        Note: This module uses ephemeral compaction during get_messages_for_request(),
        so this is a no-op. Compaction happens automatically when getting messages.
        This method exists to satisfy the ContextManager protocol.
        """
        pass

    def _should_compact(self, token_count: int, budget: int) -> bool:
        """Check if context should be compacted."""
        usage = token_count / budget if budget > 0 else 0
        should = usage >= self.compact_threshold
        if should:
            logger.info(
                f"Context at {usage:.1%} capacity ({token_count:,}/{budget:,} tokens), "
                f"threshold {self.compact_threshold:.0%} - compaction needed"
            )
        return should

    def _exceeds_threshold(self, estimated_tokens: int, budget: int) -> bool:
        """Whether usage is at/above compact_threshold -- the shared gate
        used both by the outer trigger (_should_compact, via
        _measure_working_tokens) and by _compact_ephemeral's internal
        escalation check.

        In token_meter="actual" mode with a real measurement available, the
        REAL measurement decides this, not `estimated_tokens` -- see module
        docstring "Real-Usage Token Meter" and _measure_working_tokens for
        the identical mode/fallback logic. Falls back to `estimated_tokens`
        in "estimate" mode, or whenever no real measurement has arrived yet.
        """
        if budget <= 0:
            return False
        if self._request_retained_contents and estimated_tokens > budget:
            return True
        if (
            self.token_meter == TOKEN_METER_ACTUAL
            and self._last_measured_prompt_tokens is not None
        ):
            return (self._last_measured_prompt_tokens / budget) >= self.compact_threshold
        return (estimated_tokens / budget) >= self.compact_threshold

    def _measure_working_tokens(
        self, working_messages: list[dict[str, Any]]
    ) -> tuple[int, str, int]:
        """Return (token_count, source, estimated_tokens) used to evaluate
        the compaction trigger this call.

        `estimated_tokens` is ALWAYS the text-only chars/4 heuristic over
        `working_messages` (see _estimate_tokens) -- computed unconditionally
        so the estimator-vs-real-usage drift this meter exists to close is
        observable via `_last_token_meter_stats` regardless of mode.

        `token_count`/`source` are what actually drives `_should_compact`:

        - token_meter == "estimate" (default): ALWAYS `estimated_tokens`,
          source "estimate" -- regardless of whether a real measurement is
          available. This is what keeps the default mode's behavior
          independent of prior provider measurements.
        - token_meter == "actual": the last real usage recorded from
          `llm:response` (input_tokens + cache_write_tokens -- see
          `_on_llm_response`), source "measured", if one has arrived this
          session; otherwise falls back to `estimated_tokens`, source
          "estimate" (before the first response of the session, or
          whenever hooks/events are unavailable).
        """
        estimated_tokens = self._estimate_tokens(working_messages)
        if (
            self.token_meter == TOKEN_METER_ACTUAL
            and self._last_measured_prompt_tokens is not None
        ):
            source = (
                "owned_stale"
                if self._foreground_usage_claimed and self._foreground_usage_stale
                else "measured"
            )
            return self._last_measured_prompt_tokens, source, estimated_tokens
        return estimated_tokens, "estimate", estimated_tokens

    async def _on_llm_response(self, event: str, data: dict[str, Any]) -> Any:
        """Hook handler for the canonical `llm:response` event -- records the
        provider's OWN reported usage (ground truth) for the real-usage
        token meter (`token_meter: "actual"`).

        Ported from amplifier-module-context-handoff's `_on_llm_response`
        (see that module's README "Live demonstration" for the production
        incident that shaped this exact formula). Per PROVIDER_CONTRACT.md,
        `usage.input_tokens` is the provider's own GROSS total (fresh +
        cache_read combined) billed as "input" for the call that was just
        made. That figure alone UNDER-counts true context-window occupancy
        on any call that performs a first-time cache write: a large
        system/tool prompt written to cache for the first time is billed
        almost entirely as `cache_write_tokens`, disjoint from
        `input_tokens` -- a real session showed `input_tokens=2` and
        `cache_write_tokens=161,165` on its very first call. This meter
        therefore sums `input_tokens + cache_write_tokens`.
        `cache_read_tokens` is NOT added separately: it is already inside
        the gross `input_tokens` figure per the contract, and adding it
        again would double-count.

        Always records (regardless of `token_meter` config) so the
        estimator-vs-real drift is observable via `_last_token_meter_stats`
        even in "estimate" mode; only `token_meter: "actual"` ever uses this
        reading to DRIVE the compaction trigger -- see
        `_measure_working_tokens`.

        Never raises: any malformed/missing usage payload is logged at
        DEBUG and leaves the meter unchanged (falls back to the estimator
        wherever it's consulted), so a broken/unexpected event shape can
        never crash the agent loop or the request it was about to serve.
        """
        from amplifier_core.models import HookResult

        if self._foreground_usage_claimed:
            # The Loop has claimed responsibility for successful foreground
            # responses.  This broad hook also sees utility replies and may
            # run after the foreground turn, so it must become permanently
            # observational once ownership exists.
            return HookResult(action="continue")

        usage = (data or {}).get("usage") or {}
        input_tokens = usage.get("input_tokens")
        cache_write_tokens = (
            usage["cache_write_tokens"] if "cache_write_tokens" in usage else 0
        )
        if self._is_nonnegative_int(input_tokens) and self._is_nonnegative_int(
            cache_write_tokens
        ):
            total = input_tokens + cache_write_tokens
            self._last_measured_prompt_tokens = total
            logger.debug(
                f"context-simple: token_meter recorded real usage from "
                f"llm:response -- input_tokens={input_tokens:,} + "
                f"cache_write_tokens={cache_write_tokens:,} = {total:,} total"
            )
        else:
            logger.debug(
                "context-simple: llm:response event carried no "
                "usage.input_tokens; token_meter unchanged (still using "
                "estimator wherever token_meter='actual' has no measurement yet)"
            )
        return HookResult(action="continue")

    # --- Sticky compaction decision helpers ---
    #
    # These give every compaction decision (remove / truncate / stub) a
    # permanent, stable identity keyed on metadata["_seq"] (assigned once per
    # message in add_message()) instead of list index. Indices shift on every
    # call as history grows and the ephemeral view is rebuilt from scratch;
    # seq ids do not. This is the mechanism that makes the returned view's
    # shared prefix byte-stable across calls where history only grew by a
    # turn or two, instead of re-deriving (and potentially shifting) the
    # entire compaction decision on every single get_messages_for_request()
    # call.

    @staticmethod
    def _extract_seq(msg: dict[str, Any]) -> int | None:
        """Return a message's stable sequence id, or None if it has none.

        Messages without a seq (e.g. a freshly-generated system prompt from
        set_system_prompt_factory(), which never goes through add_message())
        simply cannot participate in sticky tracking -- they are always
        system-role and therefore never candidates for compaction anyway, so
        this is a non-issue in practice, not a silent gap.
        """
        return (msg.get("metadata") or {}).get("_seq")

    def _record_removed(self, msg: dict[str, Any]) -> None:
        """Permanently record that a message has been removed by compaction."""
        seq = self._extract_seq(msg)
        if seq is not None:
            self._removed_seqs.add(seq)
            # A message can only be in one terminal state; removal supersedes
            # any earlier truncate/stub decision for the same seq.
            self._truncated_seqs.discard(seq)
            self._stubbed_seqs.discard(seq)

    def _record_truncated(self, msg: dict[str, Any]) -> None:
        """Permanently record that a tool result has been truncated."""
        seq = self._extract_seq(msg)
        if seq is not None:
            self._truncated_seqs.add(seq)

    def _record_stubbed(self, msg: dict[str, Any]) -> None:
        """Permanently record that a user message has been stubbed."""
        seq = self._extract_seq(msg)
        if seq is not None:
            self._stubbed_seqs.add(seq)

    def _apply_sticky_decisions(
        self,
        messages: list[dict[str, Any]],
        additional_protected_seqs: set[int] | None = None,
    ) -> list[dict[str, Any]]:
        """Cheaply (O(n), no search) re-apply every previously-recorded
        compaction decision to a fresh copy of `messages`.

        Returns a NEW list -- `messages` (which may be `self.messages`
        itself, or a filtered view of it) is never mutated. Messages with no
        recorded decision are shallow-copied so downstream in-place mutation
        (there is none today, but this matches the discipline the rest of
        this module already follows) can never leak back into stored
        history.

        This is deterministic given the same input: the exact same messages
        are dropped/truncated/stubbed every time, in the same way, regardless
        of how many times this is called. Only messages with NO recorded
        decision (i.e. newly appended since the last escalation) pass
        through unchanged -- which is exactly the "only the tail grows"
        behavior prefix stability depends on.
        """
        result: list[dict[str, Any]] = []
        for msg in messages:
            seq = self._extract_seq(msg)
            if self._is_request_protected(msg, additional_protected_seqs):
                result.append(dict(msg))
                continue
            if seq is not None and seq in self._removed_seqs:
                continue
            if seq is not None and seq in self._truncated_seqs:
                result.append(self._truncate_tool_result(msg))
            elif seq is not None and seq in self._stubbed_seqs:
                result.append(self._stub_user_message(msg))
            else:
                result.append(dict(msg))
        return result

    async def _compact_ephemeral(
        self,
        budget: int,
        source_messages: list[dict[str, Any]] | None = None,
        additional_protected_seqs: set[int] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Compact the context EPHEMERALLY using progressive interleaved strategy.

        This returns a NEW list - the source messages are NEVER modified.

        CRITICAL: System messages are NEVER compacted. They are extracted at the start
        and re-inserted at the end, guaranteeing they are always preserved regardless
        of compaction pressure.

        Progressive levels (each checks after every operation, stops when at target):
        - Level 1: Truncate oldest 25% of tool results
        - Level 2: Truncate next 25% (now 50% truncated)
        - Level 3: Remove oldest messages (protect 50%)
        - Level 4: Truncate next 25% (now 75% truncated)
        - Level 5: Remove more messages (protect 30%)
        - Level 6: Truncate remaining (except last N)
        - Level 7: Remove more messages (protect 10%)

        Anthropic API requires that tool_use blocks in message N have matching tool_result
        blocks in message N+1. These pairs are treated as atomic units during compaction.

        Args:
            budget: Token budget for compaction target calculation.
            source_messages: Messages to compact. If None, uses self.messages.
                             This allows compacting factory-generated message lists.
        """
        messages_to_compact = (
            source_messages if source_messages is not None else self.messages
        )
        self._request_protected_seqs = self._protected_sequences(messages_to_compact)
        target_tokens = budget if self._request_hard_fit else int(budget * self.target_usage)
        old_count = len(messages_to_compact)
        old_tokens = self._estimate_tokens(messages_to_compact)

        # === CRITICAL: Extract system messages FIRST - they are NEVER compacted ===
        # System messages contain the agent's identity and instructions. Losing them
        # causes the agent to lose its persona and capabilities mid-conversation.
        system_messages = [
            dict(msg) for msg in messages_to_compact if msg.get("role") == "system"
        ]
        non_system_messages = [
            msg for msg in messages_to_compact if msg.get("role") != "system"
        ]

        # UNITS CONVENTION (see the block comment below): every "are we under
        # target yet?" comparison in this method and its helpers is TOTAL vs
        # TOTAL. System messages are extracted from `working_messages` but are
        # still part of the request, so this fixed, un-reducible floor must be
        # added back to every token count derived from `working_messages`.
        # Computed once here -- `system_messages` is never modified during
        # compaction, so this value is constant for the whole call.
        system_tokens = self._estimate_tokens(system_messages)

        if system_messages:
            logger.debug(
                f"Preserving {len(system_messages)} system message(s) ({system_tokens:,} tokens) - "
                f"these are NEVER compacted"
            )

        # === STEP 1: cheaply apply decisions already made in a PRIOR escalation ===
        # No search, no candidate selection -- just filter out previously-removed
        # seqs and re-apply previously-recorded truncate/stub transforms. This is
        # the path taken on the vast majority of calls once at least one
        # escalation has happened: deterministic given the same input messages,
        # so it reproduces exactly what was returned last time for anything not
        # newly appended -- which is what keeps the shared prefix byte-stable.
        working_messages = self._apply_sticky_decisions(
            non_system_messages,
            additional_protected_seqs=additional_protected_seqs,
        )

        # === UNITS CONVENTION: TOTAL vs TOTAL, everywhere in this path ===
        #
        # `target_tokens` above is derived from the TOTAL budget
        # (budget * target_usage), so EVERY "are we under threshold/target
        # yet?" comparison below -- the escalation trigger, each level's
        # termination check, and the checks inside the helpers this method
        # calls -- must put a TOTAL (system + conversation) token count on the
        # left-hand side. System tokens are a fixed, un-reducible floor: they
        # cannot be compacted away, but they DO consume budget.
        #
        # This is deliberately one convention applied uniformly rather than
        # two. The alternative (non-system vs `target - system_tokens`) is
        # numerically equivalent, but it would put a number in the logs and
        # stats that differs from the configured knob, and it yields a
        # negative target whenever the system prompt alone exceeds the
        # target. Total-vs-total keeps the compared number identical to the
        # reported number (`after_tokens` / `target_tokens` in the stats and
        # the compaction notice).
        #
        # Mixing the two is a real bug this code has had twice, in two
        # different places:
        #   1. the escalation TRIGGER below silently dropped system tokens,
        #      so with a large system prompt compaction never fired (see
        #      test_large_system_message_counts_toward_compaction_trigger);
        #   2. each level's TERMINATION check compared the NON-SYSTEM total
        #      (which is what the helpers naturally compute, since
        #      `working_messages` excludes system messages) against the
        #      TOTAL-budget target -- so compaction could declare victory at
        #      Level 1 after truncating one small tool result and never
        #      escalate again, silently turning the effective cap into
        #      `target_usage * budget + system_estimate` (see
        #      test_escalation_does_not_stall_at_level_1_with_large_system_message).
        # Hence: `system_tokens` is threaded into every helper that derives a
        # token count from `working_messages`.
        non_system_tokens = self._estimate_tokens(working_messages)
        current_tokens = system_tokens + non_system_tokens

        # Escalation GATE: in token_meter="actual" mode with a real
        # measurement available, the REAL measurement decides whether a new
        # escalation is needed -- consistent with the outer trigger in
        # get_messages_for_request() (_should_compact, driven by
        # _measure_working_tokens). This keeps "actual" mode meaningful: if
        # the estimator alone gated this too, a real measurement crossing
        # threshold could be silently ignored whenever the (up to ~2x off)
        # estimator disagreed -- exactly the gap this meter exists to close.
        #
        # KNOWN, ACCEPTED LIMITATION (documented, not hidden -- see README
        # "Real-usage token meter"): only the GATE (whether to escalate at
        # all) uses the real number. The amount of reduction below --
        # target_tokens, and every per-level termination check -- is still
        # computed from the estimator throughout, because a real, billed
        # token count for a *hypothetical* smaller message set does not
        # exist without another provider round-trip. If the real measurement
        # and the estimator disagree sharply, "actual" mode can still
        # converge on level 1 without having done much real reduction (the
        # estimator's own view of `working_messages` already looked small
        # enough) -- this module fires the escalation honestly, but the
        # sizing of that escalation is only as good as the estimator was
        # before this meter existed.
        needs_escalation = self._exceeds_threshold(current_tokens, budget) or (
            self._request_hard_fit and current_tokens > budget
        )
        if not needs_escalation:
            # Sticky state alone already keeps us under the threshold that
            # triggered compaction in the first place -- nothing NEW needs
            # deciding this call. Return the already-decided view unchanged;
            # _last_compaction_stats (and therefore the notice) is left as-is
            # from the last real escalation, so its content stays stable too.
            final_messages = system_messages + working_messages
            logger.debug(
                f"Sticky compaction view: {len(final_messages)} messages, "
                f"{current_tokens:,} tokens "
                f"(no new decisions this call; cumulative level so far: {self._sticky_level})"
            )
            return final_messages

        logger.info(
            f"Compacting context (new escalation): {old_count} raw messages, {old_tokens:,} raw tokens "
            f"-> {len(working_messages)} messages, {current_tokens:,} tokens after sticky state "
            f"(target: {target_tokens:,} tokens, {self.target_usage:.0%} of {budget:,})"
        )

        # Get all tool result indices for wave-based truncation
        tool_result_indices = [
            i for i, msg in enumerate(working_messages) if msg.get("role") == "tool"
        ]
        total_tools = len(tool_result_indices)

        # Always protect the last N tool results from truncation
        protected_tool_indices = self._protected_tool_indices(tool_result_indices)

        # Calculate wave boundaries (25% chunks)
        wave1_end = int(total_tools * 0.25)
        wave2_end = int(total_tools * 0.50)
        wave3_end = int(total_tools * 0.75)

        total_truncated = 0
        total_removed = 0
        total_stubbed = 0
        max_level_reached = 1

        # === LEVEL 1: Truncate oldest 25% of tool results ===
        truncated, current_tokens = self._truncate_tool_wave(
            working_messages,
            tool_result_indices[:wave1_end],
            protected_tool_indices,
            target_tokens,
            current_tokens,
            system_tokens,
        )
        total_truncated += truncated
        if current_tokens <= target_tokens:
            logger.info(f"Level 1: Truncated {truncated} tool results, reached target")
            return await self._finalize_compaction_with_stats(
                working_messages,
                system_messages,
                old_count,
                old_tokens,
                total_removed,
                total_truncated,
                total_stubbed,
                max_level_reached,
                budget,
                target_tokens,
            )

        # === LEVEL 2: Truncate next 25% (now 50% truncated) ===
        max_level_reached = 2
        truncated, current_tokens = self._truncate_tool_wave(
            working_messages,
            tool_result_indices[wave1_end:wave2_end],
            protected_tool_indices,
            target_tokens,
            current_tokens,
            system_tokens,
        )
        total_truncated += truncated
        if current_tokens <= target_tokens:
            logger.info(
                f"Level 2: Truncated {truncated} more tool results, reached target"
            )
            return await self._finalize_compaction_with_stats(
                working_messages,
                system_messages,
                old_count,
                old_tokens,
                total_removed,
                total_truncated,
                total_stubbed,
                max_level_reached,
                budget,
                target_tokens,
            )

        # === LEVEL 3: Remove oldest messages (use configured protection) ===
        max_level_reached = 3
        level3_protection = self.protected_recent  # Use configured value
        working_messages, removed, stubbed, current_tokens = (
            self._remove_messages_with_protection(
                working_messages,
                target_tokens,
                protected_recent=level3_protection,
                system_tokens=system_tokens,
                additional_protected_seqs=additional_protected_seqs,
            )
        )
        total_removed += removed
        total_stubbed += stubbed
        if current_tokens <= target_tokens:
            logger.info(
                f"Level 3: Removed {removed} messages, stubbed {stubbed} ({level3_protection:.0%} protected), reached target"
            )
            return await self._finalize_compaction_with_stats(
                working_messages,
                system_messages,
                old_count,
                old_tokens,
                total_removed,
                total_truncated,
                total_stubbed,
                max_level_reached,
                budget,
                target_tokens,
            )

        # === LEVEL 4: Truncate next 25% (now 75% truncated) ===
        max_level_reached = 4
        # Recalculate indices after removal
        tool_result_indices = [
            i for i, msg in enumerate(working_messages) if msg.get("role") == "tool"
        ]
        protected_tool_indices = self._protected_tool_indices(tool_result_indices)
        wave3_start = int(len(tool_result_indices) * 0.50)
        wave3_end = int(len(tool_result_indices) * 0.75)

        truncated, current_tokens = self._truncate_tool_wave(
            working_messages,
            tool_result_indices[wave3_start:wave3_end],
            protected_tool_indices,
            target_tokens,
            current_tokens,
            system_tokens,
        )
        total_truncated += truncated
        if current_tokens <= target_tokens:
            logger.info(
                f"Level 4: Truncated {truncated} more tool results, reached target"
            )
            return await self._finalize_compaction_with_stats(
                working_messages,
                system_messages,
                old_count,
                old_tokens,
                total_removed,
                total_truncated,
                total_stubbed,
                max_level_reached,
                budget,
                target_tokens,
            )

        # === LEVEL 5: Remove more messages (60% of configured protection) ===
        max_level_reached = 5
        level5_protection = self.protected_recent * 0.6
        working_messages, removed, stubbed, current_tokens = (
            self._remove_messages_with_protection(
                working_messages,
                target_tokens,
                protected_recent=level5_protection,
                system_tokens=system_tokens,
                additional_protected_seqs=additional_protected_seqs,
            )
        )
        total_removed += removed
        total_stubbed += stubbed
        if current_tokens <= target_tokens:
            logger.info(
                f"Level 5: Removed {removed} messages, stubbed {stubbed} ({level5_protection:.0%} protected), reached target"
            )
            return await self._finalize_compaction_with_stats(
                working_messages,
                system_messages,
                old_count,
                old_tokens,
                total_removed,
                total_truncated,
                total_stubbed,
                max_level_reached,
                budget,
                target_tokens,
            )

        # === LEVEL 6: Truncate remaining tool results (except last N) ===
        max_level_reached = 6
        tool_result_indices = [
            i for i, msg in enumerate(working_messages) if msg.get("role") == "tool"
        ]
        protected_tool_indices = self._protected_tool_indices(tool_result_indices)

        truncated, current_tokens = self._truncate_tool_wave(
            working_messages,
            tool_result_indices,
            protected_tool_indices,
            target_tokens,
            current_tokens,
            system_tokens,
        )
        total_truncated += truncated
        if current_tokens <= target_tokens:
            logger.info(
                f"Level 6: Truncated {truncated} remaining tool results, reached target"
            )
            return await self._finalize_compaction_with_stats(
                working_messages,
                system_messages,
                old_count,
                old_tokens,
                total_removed,
                total_truncated,
                total_stubbed,
                max_level_reached,
                budget,
                target_tokens,
            )

        # === LEVEL 7: Remove more messages (30% of configured protection - last resort) ===
        max_level_reached = 7
        level7_protection = self.protected_recent * 0.3
        working_messages, removed, stubbed, current_tokens = (
            self._remove_messages_with_protection(
                working_messages,
                target_tokens,
                protected_recent=level7_protection,
                system_tokens=system_tokens,
                additional_protected_seqs=additional_protected_seqs,
            )
        )
        total_removed += removed
        total_stubbed += stubbed

        logger.info(
            f"Level 7 complete ({level7_protection:.0%} protected): "
            f"Truncated {total_truncated} total, removed {total_removed} total, stubbed {total_stubbed} total. "
            f"Tokens: {old_tokens:,} → {current_tokens:,}"
        )

        # Check if we still need more space
        if current_tokens > target_tokens:
            # === LEVEL 8: Stub unprotected machine prefix + remove old stubs (extreme pressure) ===
            max_level_reached = 8

            # Find first and last user messages.
            first_user_idx = None
            last_user_idx = None
            for i, msg in enumerate(working_messages):
                if msg.get("role") == "user":
                    if first_user_idx is None:
                        first_user_idx = i
                    last_user_idx = i

            # An unprotected machine prefix can still be reduced at extreme pressure.
            # Human boundary prompts and current retained injections stay complete.
            if (
                first_user_idx is not None
                and first_user_idx != last_user_idx
                and not self._is_request_protected(
                    working_messages[first_user_idx], additional_protected_seqs
                )
                and not _is_human_message(working_messages[first_user_idx])
            ):
                first_msg = working_messages[first_user_idx]
                if not first_msg.get("_stubbed"):
                    content = first_msg.get("content", "")
                    if isinstance(content, str) and len(content) > 80:
                        working_messages[first_user_idx] = self._stub_user_message(
                            first_msg
                        )
                        # Sticky: record before `first_msg` var is superseded.
                        self._record_stubbed(first_msg)
                        total_stubbed += 1
                        savings = (len(content) - 70) // 4
                        current_tokens -= savings
                        logger.info(
                            f"Level 8: Stubbed first user message (saved ~{savings} tokens)"
                        )

            # Remove old stubs if still over target (oldest first, outside protected zone)
            if current_tokens > target_tokens:
                protected_boundary = int(
                    len(working_messages) * (1 - level7_protection)
                )
                old_stub_indices = [
                    i
                    for i, msg in enumerate(working_messages)
                    if msg.get("_stubbed")
                    and i < protected_boundary  # Outside protected recent zone
                    and i != last_user_idx  # Never remove last user message
                    and not self._is_request_protected(msg, additional_protected_seqs)
                ]

                stubs_removed = 0
                indices_to_remove = set()
                for i in old_stub_indices:  # Already sorted oldest-first
                    if current_tokens <= target_tokens:
                        break
                    indices_to_remove.add(i)
                    stubs_removed += 1
                    current_tokens -= 18  # Stub is ~70 chars = ~18 tokens

                if indices_to_remove:
                    # Sticky: record before filtering the list out from under them.
                    for i in indices_to_remove:
                        self._record_removed(working_messages[i])
                    working_messages = [
                        msg
                        for i, msg in enumerate(working_messages)
                        if i not in indices_to_remove
                    ]
                    total_removed += stubs_removed
                    logger.info(f"Level 8: Removed {stubs_removed} old user stubs")

            logger.info(
                f"Level 8 complete (extreme pressure): "
                f"Stubbed {total_stubbed} total, removed {total_removed} total. "
                f"Tokens: {old_tokens:,} → {current_tokens:,}"
            )

        return await self._finalize_compaction_with_stats(
            working_messages,
            system_messages,
            old_count,
            old_tokens,
            total_removed,
            total_truncated,
            total_stubbed,
            max_level_reached,
            budget,
            target_tokens,
        )

    def _protected_tool_indices(self, tool_result_indices: list[int]) -> set[int]:
        """
        The set of tool-result indices that truncation must never touch:
        the LAST `self.protected_tool_results` of them.

        The zero guard is load-bearing, not defensive. Python's `list[-0:]`
        is `list[0:]` -- the WHOLE list -- so the obvious spelling
        `set(tool_result_indices[-self.protected_tool_results:])` inverts the
        knob's meaning at exactly one value: `protected_tool_results=0`, which
        reads as "protect nothing", would instead protect EVERYTHING. Every
        truncation rung (levels 1, 2, 4, 6) then becomes a no-op and
        compaction escalates straight to message REMOVAL, which is strictly
        more lossy than the truncation it skipped.

        A negative value is treated the same as 0 (protect nothing) rather
        than being handed to the slice, where it would silently mean
        "protect the last |n|" -- the opposite of what a negative reads like.
        """
        if self.protected_tool_results <= 0:
            return set()
        return set(tool_result_indices[-self.protected_tool_results :])

    def _truncate_tool_wave(
        self,
        messages: list[dict[str, Any]],
        indices: list[int],
        protected_indices: set[int],
        target_tokens: int,
        current_tokens: int,
        system_tokens: int,
    ) -> tuple[int, int]:
        """
        Truncate a wave of tool results, stopping when target is reached.

        Returns (truncated_count, new_token_count).

        UNITS: `target_tokens`, `current_tokens`, and the returned token count
        are all TOTAL (system + conversation) counts, matching the
        total-budget target computed in `_compact_ephemeral`. `messages` here
        is the NON-SYSTEM working list, so `system_tokens` -- the fixed,
        un-reducible system floor -- must be added to anything derived from
        it. Without that, this would return a non-system count that the
        caller then compares against a total-budget target, and truncating one
        small tool result could make compaction declare victory while total
        usage is still far over budget.

        Performance note: the original implementation called
        `self._estimate_tokens(messages)` (a full O(n) rescan of every message)
        after each individual truncation, making this O(n) per truncation
        candidate -> O(n^2) per wave. Since only ONE message changes per
        truncation, we instead compute the true total ONCE, lazily, the first
        time we actually mutate a message in this call, then maintain it via
        O(1) per-message deltas for every subsequent truncation. This produces
        numerically identical results to the original (which only ever
        recomputed `current_tokens` at the moment of an actual mutation) at
        O(n) total cost for the whole wave instead of O(n * truncated).
        """
        truncated = 0
        true_total: int | None = None
        for i in indices:
            if current_tokens <= target_tokens:
                break
            if i in protected_indices:
                continue
            if i >= len(messages):  # Index may be stale after removals
                continue
            msg = messages[i]
            if msg.get("role") != "tool":  # Verify it's still a tool message
                continue
            if not msg.get("_truncated"):
                if true_total is None:
                    # First mutation in this call: establish the true baseline
                    # once (matches what _estimate_tokens(messages) would have
                    # returned immediately before this mutation).
                    # UNITS: + system_tokens keeps this a TOTAL, so the
                    # `current_tokens <= target_tokens` check above stays
                    # total-vs-total after this first mutation replaces the
                    # caller-supplied (already total) seed value.
                    true_total = self._estimate_tokens(messages) + system_tokens
                old_len = self._estimate_tokens([msg])
                messages[i] = self._truncate_tool_result(msg)
                new_len = self._estimate_tokens([messages[i]])
                true_total += new_len - old_len
                truncated += 1
                current_tokens = true_total
                # Sticky: record this decision by the message's stable seq id
                # so it is never re-derived (or reversed) on a later call.
                self._record_truncated(msg)
        return truncated, current_tokens

    def _remove_messages_with_protection(
        self,
        messages: list[dict[str, Any]],
        target_tokens: int,
        protected_recent: float,
        system_tokens: int,
        additional_protected_seqs: set[int] | None = None,
    ) -> tuple[list[dict[str, Any]], int, int, int]:
        """
        Remove oldest messages with specified protection level.

        User messages are NEVER removed - they may be stubbed if still over target.

        Returns (new_messages, removed_count, stubbed_count, new_token_count).

        UNITS: `target_tokens` and the returned token count are TOTAL (system
        + conversation) counts, matching the total-budget target computed in
        `_compact_ephemeral`. `messages` is the NON-SYSTEM working list, so
        `system_tokens` -- the fixed, un-reducible system floor -- is added to
        every count derived from it. Without that, this returns a non-system
        count the caller compares against a total-budget target, and removal
        stops early (or never starts) whenever the system prompt is a large
        fraction of the budget.
        """
        # Determine protected indices
        protected_indices = set()

        # Track user messages for stubbing (NEVER removal)
        user_message_indices = {
            i for i, msg in enumerate(messages) if msg.get("role") == "user"
        }

        # Human boundaries must not be displaced by machine user-role messages.
        first_user_idx = None
        last_user_idx = None
        for i, msg in enumerate(messages):
            if _is_human_message(msg):
                if first_user_idx is None:
                    first_user_idx = i
                last_user_idx = i

        # Always protect system messages
        for i, msg in enumerate(messages):
            if msg.get("role") == "system":
                protected_indices.add(i)

        # BREAK 5 -- never remove a message carrying loaded-tool state.
        # Protecting it here also protects it from the tool-pair cascade below,
        # which consults this same set. See LOADED_TOOL_STATE_METADATA_KEYS.
        loaded_tool_state_indices = {
            i for i, msg in enumerate(messages) if _carries_loaded_tool_state(msg)
        }
        if loaded_tool_state_indices:
            protected_indices |= loaded_tool_state_indices
            logger.debug(
                "[CONTEXT] protecting %d message(s) carrying loaded-tool state "
                "from removal (TS:854: dropping them unloads tools and breaks "
                "the prompt cache forward)",
                len(loaded_tool_state_indices),
            )

        protected_indices.update(
            i
            for i, msg in enumerate(messages)
            if self._is_request_protected(msg, additional_protected_seqs)
        )
        if first_user_idx is not None:
            protected_indices.add(first_user_idx)
        if last_user_idx is not None:
            protected_indices.add(last_user_idx)

        # Protect last N% of messages (using the passed protection level)
        protected_boundary = int(len(messages) * (1 - protected_recent))
        for i in range(protected_boundary, len(messages)):
            protected_indices.add(i)

        # The last N tool results are protected from removal as well as truncation.
        # Their owning assistant and sibling results are vetoed atomically below.
        tool_result_indices = [
            i for i, msg in enumerate(messages) if msg.get("role") == "tool"
        ]
        protected_indices |= self._protected_tool_indices(tool_result_indices)

        # Removal candidates exclude ALL user messages (they can only be stubbed, not removed)
        removal_candidates = [
            i
            for i in range(len(messages))
            if i not in protected_indices and i not in user_message_indices
        ]

        # Precompute per-message token counts and a tool_call_id -> indices map
        # ONCE per call (i.e. once per compaction level), instead of rescanning
        # the full message list for every removal candidate. `messages` is not
        # mutated until the final `result` list is built below, so both of
        # these stay valid for the entire loop.
        #
        # Performance note: the original implementation recomputed
        # `self._estimate_tokens(messages)` (full O(n) rescan) on every
        # removal candidate even though `messages` never changes during this
        # loop -- that call always returned the same value. It also resummed
        # the ENTIRE (growing) `indices_to_remove` set from scratch every
        # iteration. Both were O(n) work repeated O(n) times -> O(n^2). Since
        # `messages` is constant here, the base total only needs computing
        # once, and the removed-token total only needs an O(1) delta per
        # newly-removed index.
        token_lens = [self._estimate_tokens([msg]) for msg in messages]
        tool_call_id_to_indices: dict[str, list[int]] = {}
        for idx, m in enumerate(messages):
            tcid = m.get("tool_call_id")
            if tcid:
                tool_call_id_to_indices.setdefault(tcid, []).append(idx)

        # Remove messages until under target, preserving tool pairs
        indices_to_remove: set[int] = set()
        removed_tokens_total = 0
        # UNITS: + system_tokens makes this a TOTAL, matching `target_tokens`.
        base_tokens = (
            self._estimate_tokens(messages) + system_tokens
        )  # computed once; messages is constant here
        current_tokens = base_tokens

        for i in removal_candidates:
            if current_tokens <= target_tokens:
                break

            msg = messages[i]
            newly_removed: list[int] = []

            # Handle tool result - must remove with its tool_use pair
            if msg.get("role") == "tool":
                pair_removed, newly_removed = self._try_remove_tool_pair_from_result(
                    messages, i, protected_indices, tool_call_id_to_indices
                )
                if not pair_removed:
                    continue  # Can't remove this one, skip

            # Handle assistant with tool_calls - must remove with all its tool results
            elif msg.get("role") == "assistant" and msg.get("tool_calls"):
                pair_removed, tool_result_indices = (
                    self._try_remove_tool_pair_from_assistant(
                        msg, protected_indices, tool_call_id_to_indices
                    )
                )
                if not pair_removed:
                    continue  # Can't remove this one, skip
                newly_removed = [i, *tool_result_indices]

            # Regular message - just mark for removal
            else:
                newly_removed = [i]

            # Update running totals in O(1) per newly-removed index instead of
            # resumming the whole (growing) indices_to_remove set every time.
            for idx in newly_removed:
                if idx not in indices_to_remove:
                    indices_to_remove.add(idx)
                    removed_tokens_total += token_lens[idx]

            current_tokens = base_tokens - removed_tokens_total

        # After removals, stub intermediate user messages if still over target
        # At normal levels (1-7), still protect first/last from stubbing
        # Level 8 will handle first user message stubbing separately
        stub_candidates = sorted(
            [
                i
                for i in user_message_indices
                if i not in protected_indices
                and i != first_user_idx  # Protected from stubbing at levels 1-7
                and i != last_user_idx  # Always protected (never stubbed)
                and not messages[i].get("_stubbed")  # Don't re-stub
            ]
        )

        indices_to_stub = set()
        for i in stub_candidates:
            if current_tokens <= target_tokens:
                break
            msg = messages[i]
            content = msg.get("content", "")
            if isinstance(content, str) and len(content) > 80:
                indices_to_stub.add(i)
                savings = (len(content) - 70) // 4  # Stub is ~70 chars
                current_tokens -= savings

        # Sticky: record these NEW decisions by stable seq id before building
        # the result, so they are never re-derived (or reversed) on a later
        # call. `indices_to_remove`/`indices_to_stub` here only ever contain
        # candidates that weren't already decided (removed messages are
        # already absent from `messages` by the time this runs; stub
        # candidates explicitly exclude already-`_stubbed` messages above).
        for i in indices_to_remove:
            self._record_removed(messages[i])
        for i in indices_to_stub:
            self._record_stubbed(messages[i])

        # Build result with stubs
        result = []
        for i, msg in enumerate(messages):
            if i in indices_to_remove:
                continue
            if i in indices_to_stub:
                result.append(self._stub_user_message(msg))
            else:
                result.append(msg)

        # UNITS: + system_tokens so the caller receives a TOTAL to compare
        # against its total-budget `target_tokens`.
        final_tokens = self._estimate_tokens(result) + system_tokens

        return result, len(indices_to_remove), len(indices_to_stub), final_tokens

    def _try_remove_tool_pair_from_result(
        self,
        messages: list[dict[str, Any]],
        result_idx: int,
        protected_indices: set[int],
        tool_call_id_to_indices: dict[str, list[int]],
    ) -> tuple[bool, list[int]]:
        """Try to remove a tool result and its paired assistant.

        Returns (success, newly_removed_indices). Does NOT mutate any shared
        state -- the caller is responsible for adding the returned indices to
        its removal set and accounting for their token cost.
        """
        # Find the assistant with tool_calls
        for j in range(result_idx - 1, -1, -1):
            check_msg = messages[j]
            if check_msg.get("role") == "assistant" and check_msg.get("tool_calls"):
                if j in protected_indices:
                    return False, []  # Can't remove protected assistant

                # Check if ALL tool_results for this assistant can be removed
                all_removable, tool_result_indices = self._check_tool_pair_removable(
                    check_msg, protected_indices, tool_call_id_to_indices
                )

                if all_removable:
                    return True, [j, *tool_result_indices]
                return False, []
            if check_msg.get("role") != "tool":
                break
        return False, []

    def _try_remove_tool_pair_from_assistant(
        self,
        assistant_msg: dict[str, Any],
        protected_indices: set[int],
        tool_call_id_to_indices: dict[str, list[int]],
    ) -> tuple[bool, list[int]]:
        """Try to remove an assistant with tool_calls and all its results.

        Returns (success, newly_removed_indices) -- the assistant's own index
        is not known to this helper, so the caller must include it separately
        if desired (see call site: it prepends the assistant's own index).
        """
        all_removable, tool_result_indices = self._check_tool_pair_removable(
            assistant_msg, protected_indices, tool_call_id_to_indices
        )

        if all_removable:
            return True, tool_result_indices
        return False, []

    def _check_tool_pair_removable(
        self,
        assistant_msg: dict[str, Any],
        protected_indices: set[int],
        tool_call_id_to_indices: dict[str, list[int]],
    ) -> tuple[bool, list[int]]:
        """Check if all tool results for an assistant can be removed. Returns (all_removable, result_indices).

        Performance note: uses a precomputed tool_call_id -> message-index map
        (built once per removal pass, see `_remove_messages_with_protection`)
        instead of scanning the full message list for every tool_call_id on
        every removal candidate.
        """
        all_removable = True
        tool_result_indices = []

        for tc in assistant_msg.get("tool_calls", []):
            tc_id = tc.get("id") or tc.get("tool_call_id")
            if tc_id:
                result_indices = tool_call_id_to_indices.get(tc_id, [])
                # An unanswered call must remain until its result arrives.
                # Removing it now would make a subsequently admitted result
                # orphaned in the provider-facing view.
                if not result_indices:
                    all_removable = False
                for k in result_indices:
                    if k in protected_indices:
                        all_removable = False
                    else:
                        tool_result_indices.append(k)

        return all_removable, tool_result_indices

    async def _finalize_compaction_with_stats(
        self,
        working_messages: list[dict[str, Any]],
        system_messages: list[dict[str, Any]],
        old_count: int,
        old_tokens: int,
        total_removed: int,
        total_truncated: int,
        total_stubbed: int,
        max_level_reached: int,
        budget: int,
        target_tokens: int,
    ) -> list[dict[str, Any]]:
        """Log final compaction state, store stats, emit event, and return the result.

        CRITICAL: This function prepends the preserved system messages to the compacted
        working messages, ensuring system messages are ALWAYS in the final result.
        """
        # === CRITICAL: Prepend system messages to final result ===
        # System messages were extracted before compaction and must be restored
        final_messages = system_messages + working_messages

        final_tokens = self._estimate_tokens(final_messages)
        system_count = len(system_messages)
        tool_use_count = sum(1 for m in final_messages if m.get("tool_calls"))
        tool_result_count = sum(1 for m in final_messages if m.get("role") == "tool")

        logger.info(
            f"Compaction complete: {old_count} → {len(final_messages)} messages, "
            f"{old_tokens:,} → {final_tokens:,} tokens "
            f"({system_count} system, {tool_use_count} tool_use, {tool_result_count} tool_result preserved)"
        )

        # === SANITY CHECK: Verify system messages are present ===
        result_system_count = sum(
            1 for m in final_messages if m.get("role") == "system"
        )
        if result_system_count != system_count:
            logger.error(
                f"CRITICAL: System message count mismatch! Expected {system_count}, got {result_system_count}. "
                f"This indicates a bug in compaction logic."
            )

        # === BUDGET GUARD: compaction finished, but the view is STILL over budget ===
        # Compaction can only shrink the CONVERSATION. System messages are an
        # un-reducible floor (see the units convention in _compact_ephemeral),
        # so once the system share alone exceeds the target, the target is
        # arithmetically unreachable -- no escalation level can ever get there.
        # That state previously ended here in total silence: an over-budget
        # view returned with no signal at all, which is the one failure mode
        # this module cannot afford, since it manages every session's memory.
        # Say it out loud, and name the system share as the floor so the
        # operator knows which knob actually moves (shrink the system prompt,
        # or raise the budget -- compacting harder will not help).
        system_tokens = self._estimate_tokens(system_messages)
        if budget > 0 and final_tokens > budget:
            if system_tokens > target_tokens:
                cause = (
                    f"the system prompt ALONE is {system_tokens:,} tokens, which already "
                    f"exceeds the compaction target of {target_tokens:,} tokens. System "
                    f"messages are never compacted, so this is an un-reducible floor: NO "
                    f"amount of conversation compaction can reach the target. Reduce the "
                    f"system prompt or raise the budget"
                )
            else:
                cause = (
                    f"the un-compactable system floor is {system_tokens:,} tokens against "
                    f"a target of {target_tokens:,}; the rest is protected content (last "
                    f"user message, last {self.protected_tool_results} tool results, "
                    f"tool_use/tool_result pairs) that compaction is not permitted to drop"
                )
            logger.warning(
                f"Compaction finished OVER BUDGET at level {max_level_reached}: "
                f"{final_tokens:,} tokens against a budget of {budget:,} "
                f"({final_tokens / budget:.0%} of budget, target {target_tokens:,}) -- "
                f"{cause}."
            )

        # A provider-directed hard fit must fail before committing observable
        # compaction state. The request-retention wrapper then restores its
        # local decisions, and hook consumers never see a phantom compaction.
        if self._request_hard_fit and final_tokens > budget:
            raise ContextLengthError(
                "Context cannot fit the current injections and protected conversation "
                "within the estimated input budget; shorten the active instructions "
                "or use a larger context window. Required content was not discarded."
            )

        # Cumulative high-water mark across ALL escalations ever, not just
        # this one -- monotonic, never goes backward. This is what feeds the
        # compaction notice, so its content only changes when a genuinely
        # NEW escalation happens, keeping it stable across the (much more
        # common) calls where sticky state alone was already sufficient.
        self._sticky_level = max(self._sticky_level, max_level_reached)

        # Build and store stats for observability. Removed/truncated/stubbed
        # counts are read from the sticky decision store itself (cumulative
        # totals across every escalation this context manager has ever made),
        # not the per-call deltas passed in -- this reports the total
        # accumulated effect on the conversation, which is what the notice
        # and any observability consumer actually wants to know.
        stats = {
            "before_tokens": old_tokens,
            "after_tokens": final_tokens,
            "before_messages": old_count,
            "after_messages": len(final_messages),
            "messages_removed": len(self._removed_seqs),
            "messages_truncated": len(self._truncated_seqs),
            "user_messages_stubbed": len(self._stubbed_seqs),
            "system_messages_preserved": system_count,
            "strategy_level": self._sticky_level,
            "budget": budget,
            "target_tokens": target_tokens,
            "protected_recent": self.protected_recent,
            "protected_tool_results": self.protected_tool_results,
        }
        self._last_compaction_stats = stats

        # A hard fit delays emission until its tail notice is added and the
        # complete provider-facing view passes its final budget check.
        if (
            self._hooks is not None
            and not self._request_hard_fit
            and not self._defer_compaction_delivery
        ):
            try:
                await self._hooks.emit("context:compaction", stats)
            except Exception as e:
                logger.warning(f"Could not emit compaction event: {e}")

        return final_messages

    def _truncate_tool_result(self, msg: dict[str, Any]) -> dict[str, Any]:
        """
        Truncate a tool result message to reduce token count.

        Returns a NEW dict - does not modify the original.
        """
        content = msg.get("content", "")
        if not isinstance(content, str) or len(content) <= self.truncate_chars:
            return msg

        original_tokens = len(content) // 4
        return {
            **msg,
            "content": f"[truncated: ~{original_tokens:,} tokens - call tool again if needed] {content[: self.truncate_chars]}...",
            "_truncated": True,
            "_original_tokens": original_tokens,
        }

    def _stub_user_message(self, msg: dict[str, Any]) -> dict[str, Any]:
        """
        Create a stub for a user message to preserve thread while reducing tokens.

        Returns a NEW dict - does not modify the original.
        """
        content = msg.get("content", "")
        if not isinstance(content, str) or len(content) <= 80:
            return msg  # Too short to stub

        # Take first 50 chars, clean up for display
        preview = content[:50].replace("\n", " ").strip()
        if len(content) > 50:
            preview += "..."

        return {
            **msg,
            "content": f'[User message compacted - original: "{preview}"]',
            "_stubbed": True,
            "_original_length": len(content),
        }

    def _format_compaction_notice(self) -> str:
        """
        Format compaction notice based on last compaction stats.

        This method can be overridden by subclasses to customize the notice
        format for different compaction strategies.

        Returns:
            Formatted notice string, or empty string if no stats available.
        """
        if not self._last_compaction_stats:
            return ""

        stats = self._last_compaction_stats
        level = stats.get("strategy_level", 0)

        # Handle different verbosity levels
        if self.compaction_notice_verbosity == "minimal":
            return (
                '<system-reminder source="context-compaction">\n'
                "Context has been compacted to fit within token budget. "
                "Some older messages and tool results may be truncated.\n"
                "</system-reminder>"
            )

        # Build level-specific affected items (for normal and verbose)
        affected = self._format_affected_items(level, stats)

        # Normal verbosity (default)
        old_count = stats.get("before_messages", 0)
        new_count = stats.get("after_messages", 0)
        removed = stats.get("messages_removed", 0)
        stubbed = stats.get("user_messages_stubbed", 0)
        truncated = stats.get("messages_truncated", 0)
        # Local chars/4 statistics are not provider input counts; keep them
        # in diagnostic stats rather than presenting them as tokens to the model.
        protected_recent = stats.get("protected_recent", 0.0)
        protected_tool_results = stats.get("protected_tool_results", 0)

        notice = f"""<system-reminder source="context-compaction">
Context has been compacted to fit within token budget.

Compaction summary:
- Strategy level: {level}/8
- Messages: {old_count} → {new_count} ({removed} removed, {stubbed} stubbed)
- Tool results: {truncated} truncated

What was preserved:
- All system messages (your instructions and identity)
- Last user message (current task)
- Recent messages ({protected_recent:.0%} of conversation)
- Last {protected_tool_results} tool results (full content)

What may be affected:
{affected}

Note: This compaction is ephemeral (affects only this request). Full history is preserved in session transcript.
</system-reminder>"""

        return notice

    def _format_affected_items(self, level: int, stats: dict[str, Any]) -> str:
        """
        Format affected items based on compaction level.

        Args:
            level: Compaction strategy level (1-8)
            stats: Compaction statistics dictionary

        Returns:
            Formatted string describing what may be affected at this level.
        """
        if level <= 2:
            return (
                '- Older tool results are truncated with "[truncated: ~N tokens]" prefix\n'
                "- You can re-run tools if you need the full output"
            )
        elif level <= 5:
            return (
                '- Older tool results are truncated with "[truncated: ~N tokens]" prefix\n'
                "- Older conversation messages have been removed\n"
                "- You can re-run tools if you need full output"
            )
        else:  # level 6-8
            n = stats.get("protected_tool_results", 5)
            return (
                f"- Most tool results are truncated (except last {n})\n"
                "- Significant conversation history has been removed\n"
                '- Some user messages may be stubbed as "[User message compacted...]"\n'
                "- If context is critical, consider asking user to clarify their current goal"
            )

    def _calculate_budget(self, token_budget: int | None, provider: Any | None) -> int:
        """Effective token budget: what the model offers, capped by `max_tokens`.

        Two steps, deliberately separate:

        1. DERIVE what is available (:meth:`_derive_budget`) -- the provider's
           own window, or the fallback when it publishes none.
        2. CAP it at the configured `max_tokens`, if one is set.

        The cap is applied to EVERY derivation path, with no exceptions, so
        the rule is one sentence: *the effective budget is the lower of what
        the model allows and what you asked for.* `min()` can only lower a
        budget, never raise it, which is why it is safe on every path -- a
        smaller budget compacts earlier, it can never overflow a request.

        `max_tokens` defaults to None (no cap). Setting it ABOVE the model's
        own window is a no-op by construction rather than an error.
        """
        self._budget_provenance = {"source": "unknown"}
        derived = self._derive_budget(token_budget, provider)

        budget_capped = self.max_tokens is not None and derived > self.max_tokens
        if budget_capped:
            logger.info(
                f"Budget capped by max_tokens: {derived:,} -> {self.max_tokens:,}"
            )

        self._budget_provenance = {
            **self._budget_provenance,
            "derived_budget": derived,
            "max_tokens": self.max_tokens,
            "max_tokens_fallback": self.max_tokens_fallback,
            "capped": budget_capped,
            "effective_budget": self.max_tokens if budget_capped else derived,
        }
        return self.max_tokens if budget_capped else derived

    def _derive_budget(self, token_budget: int | None, provider: Any | None) -> int:
        """Budget available BEFORE the `max_tokens` cap is applied.

        Priority:
        1. Explicit token_budget parameter (deprecated but supported)
        2. Provider model info (context_window - reserved_output - safety_margin)
        3. Provider defaults (legacy: some providers may put limits here)
        4. Configured max_tokens_fallback

        Note: We reserve only 50% of max_output_tokens since most responses are
        much smaller than the maximum. This prevents over-conservative budgets
        that would trigger compaction too early.
        """
        # Explicit budget takes precedence (for backward compatibility)
        if token_budget is not None:
            logger.debug(f"Using explicit token_budget: {token_budget}")
            self._budget_provenance = {"source": "explicit"}
            return token_budget

        safety_margin = 4096  # Buffer to avoid hitting hard limits
        output_reserve_fraction = self.output_reserve_fraction

        # Try provider-based dynamic budget
        if provider is not None:
            try:
                # First, try to get model info if provider exposes current model
                # Some providers have get_model_info() or similar
                if hasattr(provider, "get_model_info"):
                    model_info = provider.get_model_info()
                    if model_info:
                        context_window = getattr(model_info, "context_window", None)
                        max_output = getattr(model_info, "max_output_tokens", None)
                        if context_window and max_output:
                            reserved_output = int(max_output * output_reserve_fraction)
                            budget = context_window - reserved_output - safety_margin
                            logger.info(
                                f"Budget from provider model info: {budget:,} "
                                f"(context={context_window:,}, reserved_output={reserved_output:,} "
                                f"[{output_reserve_fraction:.0%} of {max_output:,}])"
                            )
                            self._budget_provenance = {
                                "source": "provider_model_info",
                                "context_window": context_window,
                                "max_output_tokens": max_output,
                                "reserved_output": reserved_output,
                            }
                            return budget

                # Check provider info defaults (legacy approach)
                info = provider.get_info()
                defaults = info.defaults or {}
                context_window = defaults.get("context_window")
                max_output_tokens = defaults.get("max_output_tokens")

                if context_window and max_output_tokens:
                    reserved_output = int(max_output_tokens * output_reserve_fraction)
                    budget = context_window - reserved_output - safety_margin
                    logger.info(
                        f"Budget from provider defaults: {budget:,} "
                        f"(context={context_window:,}, reserved_output={reserved_output:,} "
                        f"[{output_reserve_fraction:.0%} of {max_output_tokens:,}])"
                    )
                    self._budget_provenance = {
                        "source": "provider_defaults",
                        "context_window": context_window,
                        "max_output_tokens": max_output_tokens,
                        "reserved_output": reserved_output,
                    }
                    return budget
                else:
                    logger.debug(
                        f"Provider defaults missing context_window ({context_window}) "
                        f"or max_output_tokens ({max_output_tokens}), using fallback"
                    )
            except Exception as e:
                logger.debug(f"Could not get budget from provider: {e}")

        # No provider window available anywhere -- use the calibrated fallback.
        logger.info(
            f"Provider published no usable window; using max_tokens_fallback: "
            f"{self.max_tokens_fallback:,}"
        )
        self._budget_provenance = {"source": "max_tokens_fallback"}
        return self.max_tokens_fallback

    def _estimate_tokens(self, messages: list[dict[str, Any]]) -> int:
        """Text estimate; image cost is unknown without a provider count."""
        return estimate_messages(messages).tokens


class _MeasuredViewTransaction:
    """Idempotent commit/rollback boundary for staged measured compaction."""

    _OPEN = "open"
    _COMMITTED = "committed"
    _ROLLED_BACK = "rolled_back"

    def __init__(
        self,
        context: SimpleContextManager,
        snapshot: tuple[set[int], set[int], set[int], int, dict[str, Any] | None, dict[str, Any] | None]
        | None = None,
    ):
        self._context = context
        self._snapshot = snapshot or (
            context._removed_seqs.copy(),
            context._truncated_seqs.copy(),
            context._stubbed_seqs.copy(),
            context._sticky_level,
            context._last_compaction_stats,
            context._last_token_meter_stats,
        )
        self._state = self._OPEN

    async def commit(self, *, is_cancelled: Callable[[], bool] | None = None) -> bool:
        """Accept the selected candidate immediately before Loop dispatch.

        Event delivery remains part of the open transaction.  A caller can
        therefore veto a candidate after the await, while its snapshot is
        still available to rollback.
        """
        if self._state == self._COMMITTED:
            return True
        if self._state == self._ROLLED_BACK:
            return False
        if self._context._hooks is not None and self._context._last_compaction_stats:
            try:
                await self._context._hooks.emit(
                    "context:compaction", self._context._last_compaction_stats
                )
            except Exception as error:
                logger.warning(f"Could not emit measured compaction event: {error}")
        if self._state != self._OPEN:
            return False
        if is_cancelled is not None and is_cancelled():
            self.rollback()
            return False
        if self._state != self._OPEN:
            return False
        self._state = self._COMMITTED
        return True

    def rollback(self) -> None:
        """Restore pre-candidate state unless the transaction was committed."""
        if self._state != self._OPEN:
            return
        (
            self._context._removed_seqs,
            self._context._truncated_seqs,
            self._context._stubbed_seqs,
            self._context._sticky_level,
            self._context._last_compaction_stats,
            self._context._last_token_meter_stats,
        ) = self._snapshot
        self._state = self._ROLLED_BACK

# Amplifier Simple Context Manager Module

Basic message list context manager for conversation state.

## Prerequisites

- **Python 3.11+**
- **[UV](https://github.com/astral-sh/uv)** - Fast Python package manager

### Installing UV

```bash
# macOS/Linux/WSL
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

## Purpose

Provides straightforward in-memory conversation context management. This is the reference implementation and default context manager.

## Contract

**Module Type:** Context
**Mount Point:** `contexts`
**Entry Point:** `amplifier_module_context_simple:mount`

## Behavior

- In-memory message list
- No persistence across sessions
- Automatic compaction when approaching token limit (keeps system messages + last 10 messages)
- **Preserves tool pairs as atomic units** during compaction (data integrity guarantee)
- **Optional real-usage token meter** (`token_meter: "actual"`, default off) can negotiate a complete-request provider count with a compatible orchestrator; older orchestrators retain the legacy actual-meter path -- see [Real-usage token meter](#real-usage-token-meter-token_meter) below

## Configuration

```toml
[[contexts]]
module = "context-simple"
name = "simple"

[contexts.config]
max_tokens = 500000             # Optional CAP; default None = use the model's full window
max_tokens_fallback = 200000    # Only used when the provider publishes no window
max_tool_result_bytes = 131072  # Optional override; default is 128 KiB
```

`max_tokens` is the knob to reach for when you want to use **less** context
than the model allows -- see [`max_tokens` is a cap](#max_tokens-is-a-cap).

### Tool-result text ingress cap

Before admission, a tool message's string content (including JSON serialized
`ToolResult` dict/list output) is limited to `max_tool_result_bytes`, which
defaults to 131,072 UTF-8 bytes. Oversized content keeps a UTF-8-safe prefix
and one explicit retrieval marker. Raise this one explicit setting only for a
legitimate larger text result; there is no off switch in this version.

For block content, the cap covers only direct text blocks. Image, audio,
unknown, and nested block data are not read or sliced. The original oversized
text is not retained in metadata, a side file, or the admitted transcript:
retrieve missing content using narrower read/query parameters; do not repeat
state-changing actions just to recover output. For ill-formed Python text
containing lone surrogates, byte accounting uses UTF-8 replacement; under-cap
content remains unchanged, while an oversized clipped prefix is valid UTF-8.

The default is a finite observed baseline, not a universal tail guarantee:
509 outputs from 16 stock-main S1 captures had a 40,139-byte p99 and
87,301-byte maximum, with none above 128 KiB. 128 KiB is 3.27x that p99 and
1.5x that maximum, while still fitting the local 64k-window regression where
a 256 KiB cap would not.

## Usage

```python
# In amplifier configuration
[session]
context = "context-simple"
```

Perfect for:

- Development and testing
- Short conversations
- Stateless applications

Not suitable for:

- Cross-session persistence
- Custom compaction strategies

## Compaction Strategy

The SimpleContextManager uses **ephemeral compaction** -
`get_messages_for_request()` returns a compacted VIEW without modifying the
admitted internal message history. Ingress-clipped tool text is irreversible
and is not retained in the canonical transcript; compaction remains view-only.

Compaction triggers when token usage reaches the configured threshold (default: 92% of the **effective budget** -- the provider's own window, capped by `max_tokens` if you set one; see [Where the compaction trigger comes from](#where-the-compaction-trigger-comes-from)):

### Protected Messages (Never Removed)

- **System messages**: All system messages are always preserved
- **First human prompt**: The original human task/request is protected, using message metadata rather than treating every user-role message as human input
- **Last human prompt**: The most recent human input is protected by the same metadata-based classification
- **Recent messages**: Last N% of messages (configurable via `protected_recent`)
- **Recent tool results**: The last `protected_tool_results` results (default 5) are protected from both truncation and removal; a protected sibling also prevents removal of its owning call group
- **Tool pairs**: Tool_use and tool_result messages are treated as atomic units

### Request-scoped retention

The optional `context.request_retention` capability lets an orchestrator name
the exact persisted reminder bodies required for its next request. The newest
matching admitted `ephemeral=True, persisted=True` user-role envelope is kept
complete through compaction, along with the first/latest human prompts.
Quoting reminder XML in an ordinary human prompt does not change its identity.

This protects delivery in the request view; it does not change message roles,
pin every historical reminder, or rewrite canonical history. A missing required
body or an irreducible required set that cannot fit raises `ContextLengthError`
instead of silently dropping instructions. Failed assembly before compaction
event delivery restores the prior compaction state.

Protection takes precedence over the compaction target. A protected tool cohort
can leave a view above that target; it is not a strict native-token ceiling.

After a compaction decision, every later provider-facing request reuses the
same reduced view before it is measured; canonical history remains complete.
The capability also accepts `hard_fit=True` for a provider-directed forced
rebuild: it targets the supplied effective request budget rather than applying
`target_usage` again. This is an additive capability keyword for orchestrators,
not a user configuration setting; ordinary `token_budget` calls keep their
existing `target_usage` semantics.

For a hard-fit request, sticky compaction decisions and their accounting roll
back if assembly fails or is cancelled before the final, notice-inclusive view
starts `context:compaction` event delivery. Once delivery starts, that validated
compaction is retained even if the caller is cancelled. The event marks this
compaction commit boundary only; it does **not** imply that a provider request
was dispatched.

### Compaction Phases

1. **Phase 1 - Tool Result Truncation**: Older tool results are truncated to reduce token usage
2. **Phase 2 - Message Removal**: Older non-protected messages are removed if still over budget

### Tool Pair Preservation

Anthropic API requires that every tool_use in message N has a matching tool_result in message N+1. The context manager preserves these pairs as atomic units during compaction to maintain conversation state integrity and prevent API errors.

**Critical implementation detail**: When an assistant message has multiple tool_calls, there are multiple consecutive tool_result messages after it. The compaction logic walks backwards through these tool results to find the originating assistant message, ensuring the entire tool group is preserved as an atomic unit. This prevents orphaned tool results that would cause API validation errors.

## Where the compaction trigger comes from

The trigger is one multiplication:

```
trigger = compact_threshold * effective_budget
```

`effective_budget` is computed in two steps.

**Step 1 -- derive what the model offers** (`_derive_budget()`), in priority order:

1. an explicit `token_budget=` argument (the provider's own overflow-shrink retry);
2. `provider.get_model_info()` -> `context_window - 0.5 * max_output_tokens - 4096`;
3. `provider.get_info().defaults` -> the same formula;
4. **only if none of the above yields a window**: `max_tokens_fallback`.

**Step 2 -- cap it** at `max_tokens`, if one is configured:

```
effective_budget = min(derived_budget, max_tokens)
```

### `max_tokens` is a cap

`max_tokens` defaults to **`None`**, meaning *no cap* -- use the whole window
the model allows. Set it only when you want to use **less** than the model
offers:

```yaml
context:
  module: context-simple
  config:
    max_tokens: 500000    # compact as if the window were 500k
```

The lower of the two always wins, so setting `max_tokens` **above** the
model's own window is a no-op by construction, not an error. The cap applies
on every derivation path with no exceptions -- `min()` can only lower a
budget, never raise it, so a cap can make compaction fire earlier but can
never overflow a request.

### `max_tokens_fallback` is the other half

`max_tokens_fallback` (default **200,000**) answers only when a provider
publishes no usable window at all -- it is a floor under a missing number, not
a cap. 200,000 is the smallest context window across the current generation of
the three major vendors (Anthropic 200K base, OpenAI 272K default, Google
~1M), chosen small because a guess that is too large overflows a request while
one that is too small merely compacts early.

A provider landing on this branch is a bug in **that provider** -- the durable
fix is for it to publish its window, not to tune this number. Both keys are
overridable per session:

```yaml
# ~/.amplifier/settings.yaml
overrides:
  context-simple:
    config:
      max_tokens_fallback: 400000
```

### Seeing the budget a session actually ran on

`context-simple` emits a `context:budget` event carrying the effective budget
and where it came from:

```json
{"source": "provider_defaults", "context_window": 1048576,
 "max_output_tokens": 65536, "reserved_output": 32768,
 "derived_budget": 1011712, "max_tokens": 500000,
 "max_tokens_fallback": 200000, "capped": true, "effective_budget": 500000}
```

`source` is one of `provider_model_info`, `provider_defaults`,
`max_tokens_fallback` or `explicit`, and `capped` says whether `max_tokens`
bit. The module also logs the same number, but a log line is not an artifact --
it never reaches the session record, which made "what budget did this session
run on?" unanswerable after the fact.

It fires at the delivery boundary, so a cancelled or failed request leaves no
trace, and only when the value CHANGES, so a long session carries a readable
handful of lines rather than one per request.

### History: this knob used to be dead

Before the cap existed, `max_tokens` was consulted **only** at step 1.4 --
as a fallback. Orchestrators always call
`get_messages_for_request(provider=provider)`, so branch 2 or 3 always
answered and branch 4 was never reached: the `max_tokens` in your bundle
config had **no effect on when compaction fired**, in either direction.

That was a real trap, not a theoretical one. The cadence probe that produced
the numbers below could not move the trigger with config at all -- its harness
had to patch this module's source in-container to add
`budget = min(budget, self.max_tokens)`, which is precisely the behavior this
module now ships as a supported option.

`tests/test_compaction_trigger_provenance.py` pins the current contract in
both directions: a cap below the provider window moves the trigger, an unset
cap leaves the provider budget untouched, and a cap above the window is a
no-op.

**To move the trigger as a *fraction*, move `compact_threshold`.** It
expresses "compact later" independently of the provider, where `max_tokens`
expresses it as an absolute ceiling:

```yaml
context:
  module: context-simple
  config:
    compact_threshold: 0.80   # compact earlier than the 0.92 default
```

### What the cadence measurement does and does not say

Measured on the S5-CRAC scenario (`gpt-5.6-terra`, n=2 vs n=5 reused
baselines; capture root
`.amplifier/evaluation/treatment-validation/20260901-cadence/`,
`PROBE4-VERDICT.md`), raising the compaction trigger budget from 45,000 to
70,000 tokens produced:

| arm | boundaries | requests | wall (s) | cost ($) | S5 score |
|---|---:|---:|---:|---:|---:|
| `cad-today` (trigger 45k, n=5) | 21.6 | 104 | 562 | 2.58 | 94.4 |
| `cad-fewer` (trigger 70k, n=2) | 9.5 | **74** | **485** | 2.65 | 95.0 |

**-29% requests, -14% wall, at equal cost and equal quality** -- and the only
arm in that matrix where input-item caching measurably occurred (20/72 and
11/77 requests). Buy the latency and request-count win; **do not promise a
cost win** ($2.65 vs $2.58 is nil, in the wrong direction).

Two limits on that result, both from its own source:

- Both values are **scenario forcing knobs**. 45,000 exists to make a bounded
  10-turn run compact at all. Neither is a production default, and neither is
  a value this module has ever shipped.
- **Production already compacts later than `cad-fewer` did.** With a
  200,000-token window the shipped trigger is `0.92 * 163,904 = 150,791`
  tokens; `cad-fewer`'s was `0.92 * 70,000 = 64,400`. Adopting 70,000 as a
  budget cap would move the trigger **earlier** for every provider whose
  window exceeds it -- more boundaries, inverting the measured win. The
  parametrized test at the bottom of
  `tests/test_compaction_trigger_provenance.py` asserts exactly this.
  (Those trigger figures are `compact_threshold * budget`;
  `get_messages_for_request` also subtracts the 800-token compaction-notice
  reserve first, moving each down by 736 tokens. No ratio changes.)

The general finding still holds and is the one to carry forward: **fewer
compaction boundaries buys latency and request count, not money, and costs no
measurable quality** (post-compaction retention was 20/20 in every run of
every arm, `b_constraints` 40/40 throughout -- on a scenario whose 5 crisp
constraints may be a ceiling effect).

## Real-usage token meter (`token_meter`)

### The problem

The compaction trigger described above runs entirely off `_estimate_tokens()`
-- `len(str(msg)) // 4` over the Python `repr()` of each message. This
estimator is **never reconciled against what the provider actually billed**
anywhere in this module. In production sessions it has been measured
roughly **2x off** from real provider usage. Because the trigger and the
whole progressive-compaction sizing logic are built on this number, running
compaction any closer to the real ceiling than the current conservative
default (92%) is unsafe on an estimator that inaccurate -- you would risk
provider-side context-length rejections with no warning.

A companion module, [amplifier-module-context-handoff](https://github.com/microsoft/amplifier-module-context-handoff),
solved this for its own (non-compacting) reserve trigger by registering a
listener on the canonical `llm:response` event and reading the provider's
own reported usage instead of guessing. This module ports that same
`_on_llm_response` meter, adapted to context-simple's compaction trigger.

### What it does

- When hooks are available, this module **always** registers a listener on
  `llm:response` and records the provider's own reported usage for the most
  recent request: `input_tokens + cache_write_tokens`. Per the provider
  contract, `input_tokens` is the GROSS total (fresh + cache_read combined)
  billed as input; `cache_write_tokens` is billed disjointly (a first-time
  cache write of a large system/tool prompt can be billed almost entirely as
  `cache_write_tokens` with `input_tokens` near zero), so it must be added
  separately or true context-window occupancy would be undercounted by
  orders of magnitude. `cache_read_tokens` is **not** added again -- it is
  already inside the gross `input_tokens` figure.
- This recording happens **regardless of `token_meter` mode** -- it is a
  cheap, side-effect-free observability signal, exposed via
  `context._last_token_meter_stats` (populated on every
  `get_messages_for_request()` call, not only when compaction fires) so the
  estimator-vs-real drift is visible even in the default mode.
- Set **`token_meter: "actual"`** in config to additionally have the
  compaction trigger -- and `_compact_ephemeral`'s internal escalation gate
  -- use that real measurement once at least one `llm:response` has been
  observed this session. Before the first response (or whenever hooks/events
  are unavailable), "actual" mode falls back to the same estimator
  `"estimate"` mode always uses.
- Default is **`token_meter: "estimate"`**, which is byte-identical to this
  module's behavior before this meter existed -- verified by running the
  full pre-existing test suite unchanged. An unrecognized `token_meter`
  value logs a warning and falls back to `"estimate"` rather than raising.

### Negotiated provider-count path

When `token_meter: "actual"` is paired with an orchestrator and provider that
both advertise the optional measured-request capability, Context supplies a
notice-inclusive candidate view and the orchestrator builds and counts the
complete request, including its own tools and overlays. Context uses the
provider's raw input count for both the configured trigger and target, applies
at most the existing eight legal rungs, and returns the exact final counted
envelope for dispatch. A protected floor is reported as such rather than being
called target success; a known request over the provider hard limit fails
closed.

This is negotiated rather than a Context-side provider call: Context never
imports provider or Loop types, and cannot count overlays it does not own. The
returned transaction is committed by the orchestrator immediately before it
dispatches the already-counted request; otherwise it rolls back staged sticky
decisions.

After all eight legal Context reduction rungs, a compatible orchestrator may
optionally supply `fit_output(view, attempt)`. Context calls it only when the
final provider-derived hard input estimate still exceeds the limit, passing
the exact notice-inclusive public base view and the exact final counted
attempt. The
orchestrator owns any bounded output-cap ladder, request cloning, reminders,
and options; a successful result returns a newly counted
`{dispatch, budget_decision, count_calls}` envelope. It must carry a usable
provider measurement, a hard-safe input estimate, and a positive count-call
total. `None` means no legal output fit, so Context preserves the existing
fail-closed `ContextLengthError` and rolls back staged decisions.

This adds no Context protection relaxation and never retries generation. The
Context ladder performs at most nine base counts (the original candidate plus
eight legal rungs); the compatible Loop bounds output fitting to at most six
additional counts. Output relief is reported as `reduced_output`, never as
input-compaction target success. It helps only when the provider's input
allowance grows with a smaller output reserve. Independent input ceilings
remain binding; bounded recounts cannot make oversized required input fit.

### Legacy actual-meter path

Only the **escalation gate** (whether to compact at all, and whether a
sticky escalation needs to advance) uses the real measurement in `"actual"`
mode. The *amount* of reduction -- `target_tokens` and every per-level
termination check inside `_compact_ephemeral` -- is still computed from the
estimator throughout, because a real, provider-billed token count for a
*hypothetical smaller* message set does not exist without another round
trip to the provider. If the real measurement and the estimator disagree
sharply, `"actual"` mode can still converge at level 1 without having done
much real reduction (the estimator's own view already looked small enough).
This module fires the escalation honestly in that case, but the *sizing* of
that escalation is only as good as the estimator was before this meter
existed. This mirrors context-handoff's own documented limitation that its
measurement is retrospective (one-call lag): the meter describes the
request that was *just* answered, not the one currently being assembled.

### Future default flip (pending validation)

`token_meter` defaults to `"estimate"` in this PR specifically so it ships
with **zero behavior change**. Flipping the default to `"actual"` -- and
potentially raising `compact_threshold` closer to the real ceiling now that
it can be measured accurately -- is a follow-up, not part of this change. It
should happen only after running the module's own eval harness against
`"actual"` mode's stats (`_last_token_meter_stats`) to confirm the expected
reduction in compaction cadence (request count / wall time) holds up without
a corresponding quality regression.

## Dependencies

- Host-provided `amplifier-core>=1.6.1`, including
  `amplifier_core.llm_errors.ContextLengthError` for fail-loud request retention.
  Core remains provided by the host, not installed as a runtime module dependency.

Development and CI resolve Core from canonical `main`. Refresh that dependency
before testing so an older lock resolution does not silently select an old Core.
The lock records the exact revision tested; it is evidence for that environment,
not a fixed source declaration for future validation.

```bash
uv sync --all-extras --dev --upgrade-package amplifier-core
uv run --no-sync pytest -q
```

## Contributing

> [!NOTE]
> This project is not currently accepting external contributions, but we're actively working toward opening this up. We value community input and look forward to collaborating in the future. For now, feel free to fork and experiment!

Most contributions require you to agree to a
Contributor License Agreement (CLA) declaring that you have the right to, and actually do, grant us
the rights to use your contribution. For details, visit [Contributor License Agreements](https://cla.opensource.microsoft.com).

When you submit a pull request, a CLA bot will automatically determine whether you need to provide
a CLA and decorate the PR appropriately (e.g., status check, comment). Simply follow the instructions
provided by the bot. You will only need to do this once across all repos using our CLA.

This project has adopted the [Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/).
For more information see the [Code of Conduct FAQ](https://opensource.microsoft.com/codeofconduct/faq/) or
contact [opencode@microsoft.com](mailto:opencode@microsoft.com) with any additional questions or comments.

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft
trademarks or logos is subject to and must follow
[Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/legal/intellectualproperty/trademarks/usage/general).
Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship.
Any use of third-party trademarks or logos are subject to those third-party's policies.

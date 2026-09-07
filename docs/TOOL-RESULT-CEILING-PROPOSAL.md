# Proposal: a per-tool-result byte ceiling in the engine

Filed as a docs PR (Issues are disabled), in the shape of `docs/HOST-TOOLS-PROPOSAL.md` (#161).
"Wontfix, hosts should handle it" is a fine answer -- but see "Why the host cannot do this" below.

## The problem, measured in production

A consumer (drumbeat, the automation engine behind Cortex) ran 11.5 hours on the `v1` branch
(`amplifier-agent` 1.0.0a1, `packages/python`) driving 16 automations that were previously on the
0.17 library. Same prompts, same tools, same schedules.

| | 0.17 library | v1 library |
|---|---|---|
| prompt tokens / 11.5 h | ~10-20 M (est. from $3-5/day) | **225.7 M** across 209 runs |
| per-run last-turn prompt | rarely > 150 k | **0.34 - 1.15 M** (98 host-side size rotations) |
| a pinned 15-minute session | compacted | reached **1.81 M tokens**, failed every run for 4 h |
| a single `m365 chats` tool result | truncated by the bash tool | **41.8 MB**, refused: `string_above_max_length` |

Two library behaviours combine to produce this:

1. The built-in `bash` (and `read_file`) tools return their **entire** output into the conversation.
   The 0.17 bash tool truncated long output; v1's `builtin_tools.py` does not.
2. v1 never compacts (`adapters.py`: "admission never silently compacts input"), and re-sends the
   whole conversation on every turn, so one oversized result is billed on every later turn until the
   provider refuses (`context_length_exceeded` / `string_above_max_length` / "prompt is too long").

Neither is a bug against the contracts as written. Together they make any automation that runs a
verbose command cost 10-100x, then die.

## Why the host cannot do this

We tried. drumbeat PR #13 (merged) ships a caller tool with a byte ceiling, a truncation note, and the
full output persisted to the run directory. It cannot cover the built-ins:

- `packages/engine/src/amplifier_agent_engine/_engine/tools.py:53` -- `ToolRegistry.add` raises
  `AgentError("invalid_input", ..., "Duplicate tool name: bash.")` for a caller tool named `bash`.
- There is no option to disable a built-in by name, and `AgentOptions` carries no result-size knob.

So the model may still call the built-in `bash`, and when it does the host is a spectator. The
consumer rolled back to 0.17 on 2026-09-07 for that reason alone.

## Proposal

Add one option, applied at the engine's tool-dispatch point to **every** tool result -- built-in,
MCP, and caller-supplied -- before it enters the conversation:

```python
AgentOptions(
    ...,
    tool_result_max_bytes=262_144,   # default; None disables
)
```

Behaviour when a result exceeds the ceiling:

- keep the first `max_bytes` (UTF-8 boundary-safe), then append one line:
  `[amplifier-agent: output truncated -- kept 262144 of 41841565 bytes]`
- emit the full result on the `tool_result` event (or a sibling `tool_result_overflow` event) so a
  host that wants the bytes can persist them; the conversation never carries them.
- record `truncated: true, original_bytes: N` on the `tool_result` event payload.

Optional companion, smaller still: surface provider input-size refusals as their own error code
(`provider_input_too_large`) with the provider's code in `AgentError.details`, so a host can rotate a
session on it. Today all of them arrive as `provider_failed`; the real code is only in a log line.

## Alternatives considered

- **Host replaces `bash`** -- blocked by the duplicate-name refusal; #161 proposes the seam that
  would allow it. Even then, MCP tool results stay unbounded unless the host proxies every MCP server.
- **Compaction in the library** -- larger design; the ceiling is orthogonal and needed regardless
  (a 40 MB result should never be admitted, compacted or not).
- **Prompt discipline** ("pipe everything through `head -c`") -- we shipped it for the two worst
  automations; it does not survive a model choosing a different command.

## What we can offer

A PR implementing the ceiling in `tools.py` dispatch with tests, if the shape above is acceptable.
Evidence (verbatim run records, the 46,458,049-byte refusal, the DTU proof of the host-side half):
microsoft/amplifier-drumbeat PR #13 and its linked evidence; the consumer report is published at
microsoft/amplifier-drumbeat `docs/reports/amplifier-agent-v1-consumer-report.md`.

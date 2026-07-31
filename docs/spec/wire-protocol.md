# Wire Protocol

## Scope

The JSON-RPC 2.0 message set spoken between a host and the `amplifier-agent` engine across a
subprocess stdio boundary: framing, the protocol version rule, the method set, and the display
event taxonomy. Does not cover the stdout result envelope (see `envelope-and-errors.md`), the argv
a wrapper emits (see `wrapper-contract.md`), or version compatibility policy across releases (see
`install-and-distribution.md`).

## Transport and framing

JSON-RPC 2.0 framed as NDJSON: one compact JSON object per line, UTF-8, terminated by `\n`. Every
frame is exactly one of:

```
request       method + id
notification  method, no id
response      id + result, or id + error
```

Channel split under `--output json --display ndjson`:

```
stdout   exactly one JSON document, the result envelope, written at process exit
stderr   NDJSON notification stream, one JSON-RPC notification per line
```

Readers are lenient by contract, not by accident. A line that does not parse as JSON, and a line
that parses to something other than a JSON object, are both skipped: the reader logs the skip at
WARNING and continues with the next line. Neither is fatal and neither aborts the stream.
Accidental stream pollution from a sub-tool must not kill the protocol bridge. Conforming readers
on either side of the boundary must implement this rule, and must route skipped lines to a
side channel rather than to the frame dispatcher.

## Protocol version

```
0.3.0
```

Compared by strict string equality. Semver range matching is not used, and no compatibility window
exists: `0.3.1` against `0.3.0` is a mismatch.

A mismatch is detected at up to three points, in the order a turn reaches them:

```
1. host, pre-spawn     the host compares its own required version against the engine's
                       `version --json` payload and refuses to spawn
2. engine, argv gate    the engine compares `--protocol-version` against its own version
                        before any prompt processing, emits the error envelope, exits 2
3. engine, initialize   the engine compares the `protocolVersion` init param against its own
                        version and fails the call with code `protocol_version_mismatch`
```

The only override is `allowProtocolSkew: true` in the host config file, which suppresses points 2
and 3. Hosts suppress point 1 through their own spawn parameter.

## Methods

Six method names appear in the published schema set:

```
initialize        InitializeParams      -> InitializeResult
turn/submit       TurnSubmitParams      -> TurnSubmitResult
session/create    SessionCreateParams   -> SessionCreateResult
session/end       SessionEndParams      -> SessionEndResult
agent/shutdown    AgentShutdownParams   -> AgentShutdownResult
cache/info        CacheInfoParams       -> CacheInfoResult
```

Three method names are accepted by the engine. Any other method name fails with
`unknown method: <name>`:

```
agent/initialize
turn/submit
agent/shutdown
```

Two consequences are caller-visible and part of the contract:

- The initialization method is named `initialize` in the schema set. The name that actually works
  is `agent/initialize`. A caller sending `initialize` verbatim gets `unknown method: 'initialize'`.
- `session/create`, `session/end`, and `cache/info` have parameter and result schemas but no
  implementation. Calling any of them fails. Do not build against them.

A `turn/submit` result carries `reply` (string or null), `turnId`, and `sessionId`. The schema also
declares an optional `finalEvent`; it is never populated and must not be relied on.

## Display events

Nine event types make up the fixed taxonomy. Adapters translate into it; they do not invent types.
Every payload carries `sessionId`. Every payload except `error` requires `turnId`. Fields marked
`?` are optional.

```
result/delta      sessionId, turnId, text
result/final      sessionId, turnId, text, usage?
tool/started      sessionId, turnId, toolCallId, name, args, agentName?
tool/completed    sessionId, turnId, toolCallId, name, result, durationMs, agentName?
progress          sessionId, turnId, message, percent?
thinking/delta    sessionId, turnId, text
thinking/final    sessionId, turnId, text
usage             sessionId, turnId, inputTokens, outputTokens, cost?, llmDurationMs?,
                  model?, provider?, cacheReadTokens?, cacheWriteTokens?,
                  sessionCostTotal?, agentName?
error             sessionId, turnId?, code, message, recoverable
```

`usage.cost` and `usage.sessionCostTotal` are decimal strings, not JSON numbers. Monetary values
are serialized as strings to preserve precision across many turns. Parse them as decimals, not as
floats.

Capability negotiation advertises the taxonomy as `{"display": {"events": [...]}}` listing exactly
these nine type names.

Two further notification types are declared in the schema set but are not members of the display
taxonomy and are not advertised in capability negotiation:

```
approval/request   sessionId, turnId, approvalId, kind, payload, timeoutMs
approval/timeout   sessionId, turnId, approvalId, kind
```

Consumers must ignore event types they do not recognize rather than failing. One such type exists
today: `tool_calls/delta` is emitted on the display channel by the host-tool path and has no
schema and no membership in the taxonomy.

## result/final synthesis obligation

When a `turn/submit` response returns a non-null `reply` scalar but no `result/final` notification
arrived before that response, the consumer-facing side MUST synthesize one before closing its
event stream. The synthesized event takes `text` from the `reply` scalar, matches `turnId` to the
in-flight turn, and omits `usage`.

The obligation deliberately tolerates engine omission, so that a consumer always sees a terminal
`result/final` closing every turn. A synthesized event must be distinguishable from an engine-sent
one so that assertions scoped to engine-originated notifications stay meaningful.

## Cancellation and process lifetime

There is no `turn/cancel` method and there must not be one. Cancellation is SIGTERM to the engine's
process group, a 5 second grace period, then SIGKILL to the same group. The engine makes itself a
session leader at startup when it is not already one, so every MCP child it spawns shares one
process group id and dies with the group.

Subprocess exit means the turn is done. There is no lost-state recovery machinery: on exit the host
reduces the accumulated stdout to a single terminal event and the stream ends. A caller wanting
another turn spawns again.

## Machine-readable schemas

JSON Schema (Draft 2020-12) for every wire type ships with the distribution, one file per type,
plus a schema for the error code enum and a rendered method reference:

```
amplifier_agent_lib/protocol/schemas/<TypeName>.schema.json
amplifier_agent_lib/protocol/schemas/error_codes.schema.json
amplifier_agent_lib/protocol/spec.md
```

Those files are the field-level reference for params and results. This document does not restate
them.

## Non-goals

An absent surface is a real contract. None of these exist and none should be introduced.

- **No long-lived stdio JSON-RPC dispatcher.** There is no `--stdio` flag. The wire is argv in, one
  envelope on stdout, subprocess exits.
- **No `turn/cancel`.** SIGTERM to the process group is the cancel.
- **No mid-turn approval round-trip on the wire.** The `approval/request` and `approval/timeout`
  types exist, but there is no request channel back to the host. Approval policy is a bundle-side
  hook mount in this version.
- **No `display/event` stream on stdout.** Stdout carries the envelope and nothing else. Streaming
  notifications go to stderr under `--display ndjson`.
- **No `--allow-protocol-skew` flag and no `AMPLIFIER_AGENT_ALLOW_PROTOCOL_SKEW` environment
  variable.** Neither is accepted. The only override is `allowProtocolSkew: true` in the host config
  file.
- **No `hostCapabilities`.** Not part of the wire, and not tolerated as a flag. A
  `host_capabilities` key found in session metadata is inert and is never read.
- **No `payload` extensibility slot on the host-facing display event type.** New event types are
  added to the union instead.
- **No `lifecycle: "burst"`.** It is reserved as a wire enum value and rejected at runtime with
  `lifecycle_unsupported`. Adding it later is minor-version-additive.

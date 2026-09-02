# Agent Interface Contract v1 (FROZEN 2026-09-02)

**Who builds against this:** applications embedding the agent, adapter authors, every
binding, every face. The other contracts refine or project this one.

**What it freezes:** the smallest surface that lets a caller run an agent and see what
it did. At run time the caller supplies configuration, tools, and authority over
effects, and the engine supplies the rest.

**This document is the source of truth for that surface.** A binding is written
against what is here, never against another binding, and never against whatever the
engine happens to do today. Where an implementation and this document disagree, the
implementation is what is wrong.

No mechanism is described here. Mechanism is the part we keep the right to replace.
MUST, MUST NOT, and MAY carry their RFC 2119 meanings. Names below are conceptual, and
map to language idiom under [`language-binding.v1`](language-binding.v1.md).

Two words recur. The **engine** is what runs the agent behind the binding: ours,
internal, and replaceable. The **host** is the caller's application, the process that
holds the tools and answers the approvals.

## 1. Object model

```text
create_agent(options: AgentOptions)     -> Agent | Error
agent.create_session(options?)          -> Session | Error
agent.resume_session(id)                -> Session | Error
agent.list_sessions()                   -> [SessionRecord] | Error
agent.delete_session(id)                -> Error on an unknown id
agent.close()                              idempotent

session.info                               read-only SessionRecord
session.run(input: TurnInput)           -> TurnResult | Error
session.start_turn(input: TurnInput)    -> Turn | Error
session.fork()                          -> Session | Error
session.history                            turns already taken
session.close()                            idempotent

turn.info                                  read-only { session_id, turn_id }
turn.events()                           -> ordered async stream<Event>, single-consumer
turn.cancel()                              idempotent
```

`run` and `start_turn` take the same turn by the same path. `run` returns exactly the
`TurnResult` that the stream's `terminal` event carries. Choosing between them is
choosing presentation, never behavior.

**Records.** Four shapes recur, and every binding carries all four:

```text
TurnInput      { content: [ContentPart...], model? }
TurnResult     { state, content?, error?, usage? }
ContentPart    { type: "text", text }
SessionRecord  { session_id, persistence }
```

`Event` is the envelope defined in [`turn-events.v1`](turn-events.v1.md) section 1.

`ContentPart.type` is a closed set, holding only `"text"` in v1. Media parts are
`Backlogged` on both sides of this interface and promote together. `TurnResult` is
exactly the payload of the `terminal` event, so a caller that has read one has read the
other.

**Lifecycle.** `create_agent` returns a fully ready agent or an error, never something
partially ready. Close is idempotent, and closing with an active turn requests
cancellation and drains all paired events before returning. Operations on closed
objects fail `closed`. Independent agents are isolated, with no leakage through
process-global state.

## 2. Configuration is inert, closed-vocabulary data

`AgentOptions` groups:

```text
instructions   provider   model   tools   skills (source locations only)
mcp_servers    storage    approvals
```

It is built, passed once, and never consulted again.

Refused at construction, by name, with a remedy:

- unregistered fields
- fields the engine will not honor
- duplicate tool names
- a tool set without a handler

Ambient configuration resolves first, per [`host-config.v1`](host-config.v1.md).
`AgentOptions` wins wherever both speak.

## 3. Sessions: identity, persistence, continuation

**Identity.** `session_id` is caller-supplied or engine-generated as a lowercase
UUIDv4. Caller-supplied ids MUST match `[a-z0-9][a-z0-9-]{7,63}`, validated at
creation (`session_id_invalid`). After that the id is opaque: consumers MUST NOT parse
it or infer structure from it.

**Persistence.** Either `"durable"`, the default, which resumes after close and across
processes from the local transcript alone, or `"ephemeral"`, which is not resumable
after close.

**Continuation.** Create and resume are distinct operations, and resume-or-create does
not exist.

```text
creating an id that already exists   -> already_exists
resuming an unknown id               -> not_found
a durable id with a live handle      -> session_in_use
```

**Turns within a session.** Sessions are multi-turn and ordered: a new turn observes
earlier terminal turns. One turn is active at a time, so a second `start_turn` fails
`busy` rather than queueing. Sessions run concurrently and isolated from one another.
`fork()` branches a session: the child sees parent history as of the fork, and child
turns never appear in the parent. The child gets its own engine-generated id and
inherits the parent's persistence. Forking a session with an active turn fails `busy`.

`delete_session` removes a durable session and its transcript. An unknown id fails
`not_found`, and deleting a session with a live handle fails `session_in_use`. Deletion
is not undone by a later resume.

## 4. The conversation stays on the caller's side

A session's history lives in a **local transcript**, written where the agent runs. That
transcript is the only authoritative record of the conversation.

Providers are asked to keep nothing: every request carries the full input, server-side
retention is disabled, and no provider conversation handle is ever load-bearing. This
is ZDR-compatible by default, and explicit retention is a host opt-in
([`host-config.v1`](host-config.v1.md) section 3) rather than a default.

Three guarantees follow, and callers may rely on all three:

- Kill every process between turns and nothing is lost.
- A durable session resumes later, in a different process, from the transcript alone.
- No provider ends up holding a copy of the conversation.

This is stated at the surface, rather than left to the implementation, so that
everything beneath it stays free to change.

## 5. One provider, and the model is a ceiling

One `provider` per agent. A second is refused.

`model` names the most expensive thing that will run on the caller's behalf. A model
MAY be named per session and per turn, with precedence `agent < session < turn`, each
one a ceiling refinement **within the agent's provider**.

A refinement only lowers. Naming a session or turn model more expensive than the one
above it fails `selector_rejected`, and no routing decision anywhere exceeds the
agent's ceiling. A caller who configured an agent for a cheap model never receives a
bill for an expensive one.

A named selection is honored for primary work or the turn fails `selector_rejected`.
It is never silently substituted.

Below the ceiling, routing is internal, downward-only, and invisible. Every actual
selection used, whether primary, internal, or delegated, appears in usage.

## 6. Tools: the model decides when, the executor does the work

The model decides when a tool should run. The engine invokes it. Every tool has exactly
one **executor**, the party that performs the effect and reports what happened:

```text
built-in          the engine executes, beneath this interface
caller-supplied   the host executes, in its own process
MCP               a configured MCP server executes, in a third process
```

The engine MUST NOT execute caller-supplied code itself, and MUST NOT perform any
effect without a preceding `tool_call` naming its source.

Built-in, caller-supplied, and MCP tools reach the model as one flat set, and every
tool event names its source. Source determines executor, so a caller reading a
`tool_call` knows where the effect will land before it lands.

The obligations in this section do not vary by executor. Where the host executes, the
engine carries them across the callback boundary. Where the engine executes, it holds
itself to them. An effect the caller cannot see, cannot refuse, or cannot get a truthful
resolution for is a defect regardless of which process it ran in.

A caller tool declares a stable `name`, a `description`, a JSON Schema `input_schema`
carrying `$schema`, and optional descriptive `safety` metadata.

Per call the engine provides decoded strict-JSON arguments, never a JSON-encoded
string, a correlated `call_id`, and an optional deadline. Each call has exactly one
resolution: `completed`, `failed`, `cancelled`, or `unknown`.

```text
tool_callback_failed      the executor could not be reached, or died without
                          producing a result
tool_result_invalid       malformed result, wrong call_id, or a second resolution
tool_failed               the executor reported that the tool failed
tool_completion_unknown   the executor cannot say whether the effect happened
```

Each of these ends the turn as `failure`, except `tool_completion_unknown` when a
cancellation has already been accepted.

An uncertain outcome is passed through as uncertain. The engine MUST NOT retry an
effect that may already have landed, MUST NOT claim it was rolled back, and ignores
resolutions that arrive after the call is settled.

## 7. Approvals: the caller's veto, before execution

With a handler, every consequential action passes through it first and resolves
exactly one way. Without one, the static policy in configuration decides. Neither is
ever inferred.

```text
deny                 approval_denied         terminal rejected, turn runs to terminal
cancel               approval_cancelled      terminal cancelled
timeout              approval_timeout        terminal failure
no channel           approval_unavailable    terminal failure
malformed reply      approval_invalid        terminal failure
```

None of these is ever interpreted as allow. A late decision, arriving after an
authoritative resolution, has no effect.

## 8. Turns terminate, observably

A turn is observed only through its event stream
([`turn-events.v1`](turn-events.v1.md)), and it ends with exactly one `terminal`:
`success`, `failure`, `cancelled`, or `rejected`, after all paired resolutions drain.

`cancel()` is idempotent. An accepted cancellation starts no new work and fixes the
terminal to `cancelled` (`turn_cancelled`).

A stream that stops without a terminal is a defect, not something a caller times out
around.

## 9. Errors

One lossless record per failure, expressed in the language's native error idiom:

```text
{ code, category, message, remedy, retryable, correlation_id?, details? }
```

`remedy` is REQUIRED and human-actionable. `retryable: true` means the same request,
unchanged, may succeed.

```text
category  lifecycle | selection | session | turn | input | executor |
          approval | provider | internal
```

Registered codes, extended additively, with owned keys for extensions:

```text
closed                     selector_rejected          session_id_invalid
already_exists             not_found                  session_in_use
busy                       stream_already_consumed    turn_cancelled
invalid_input              tool_callback_failed       tool_result_invalid
tool_failed                tool_completion_unknown    approval_denied
approval_cancelled         approval_timeout           approval_unavailable
approval_invalid           provider_failed            internal_failed
contract_version_mismatch  engine_unavailable
```

Failures before the stream exists surface at the method. Failures after it exists
surface in `terminal`, never as an untyped stream exception.

## 10. Usage

Cumulative snapshots, grouped by the actual `{provider, model}`.

Named exact-integer counters, at a floor of:

```text
tokens_in   tokens_out   cache_read_tokens   cache_write_tokens
```

`cost`, when the provider makes it knowable, is an ISO-4217-keyed map of **decimal
strings**, never rounded through binary floating point. Bindings expose their most
faithful native decimal type. An absent `cost` means unknown.

Each `usage` event replaces the last. The final one precedes `terminal` and covers
every actual selection used. An absent value means unknown, not zero.

## 11. Versioning

`contract_version` is `"agent-interface/1"`. It is readable without invoking anything,
and is distinct from every package version. Changes within the major version are
additive only. The connection beneath exposes no version of its own; this token is what
crosses it.

## Invariants

1. **No reachable name names an internal**, whether a type, field, enum value, or
   error code. `Excluded` below is the literal denylist, enforced by static lint.
2. **The engine assembles itself.** A need that instructions, tools, skills, and
   approvals cannot express amends this contract. It does not open an internal.
3. **An exclusion is not a refusal to deliver its benefit.**
4. **The caller never learns the engine's language** from any type, error, path, or
   artifact.
5. **Effects are never silent.** They are requested, correlated, resolved exactly
   once, and reported truthfully, including as `unknown`.

## Excluded

A denylist with no promotion path. Building one of these back in is a regression.

- Composition and bundles
- The loop and its lifecycle observers
- Prompt assembly
- Routing tables and roles
- Session storage format
- Context-intelligence configuration
- A caller-facing command line, in this or any future version
- Modes and recipes, which are engine-internal if they exist at all
- More than one provider per agent
- Resume-or-create

## Backlogged

Candidate clauses. Each names the evidence that promotes it.

- **Skills surface semantics.** Two host integrations require the same observable
  skill lifecycle, distinguishable from a tool by good and broken fixtures.
- **Sub-agent lifecycle visibility.** Two implementations demonstrate identical
  host-visible nesting, cancellation, and accounting. Until then, delegation appears
  as tool activity.
- **Smart-tool vocabulary.** The separate smart-tools project ships a contract of its
  own that needs a hook here.
- **Attachments and non-text-JSON content.** A real caller needs media parts, with
  evidence of lossless cross-binding representation.
- **Concurrent turns per session.** A real caller demonstrates a need that `busy`
  cannot serve, plus defined event-interleaving semantics.
- **Caller-supplied history, for stateless turns.** Trigger met: `http-face.v1` is a
  stateless projection of a protocol that carries its own history, and cannot hold a
  server-side session without duplicating it. The shape is a turn started from supplied
  history against an ephemeral session, which is additive.
- **Cross-family durable-state migration.** Two durable-state families demonstrate
  lossless migration with recovery evidence. Until then, a replaced engine returning
  `not_found` for a prior family's ids is conforming.

## Conformance

Per the three-part scheme in [`README.md`](README.md).

Runtime scenario families, each with good and broken fixtures, against the stub
provider:

- Lifecycle and isolation
- Identity, persistence, and continuation, including `already_exists`, `not_found`,
  and `session_in_use`
- Ceiling honor-or-reject, and precedence
- Tool protocol, including uncertainty and cancellation races
- Approval protocol, including timeout and unavailable
- Equality of `run` and the stream's terminal
- Statelessness: kill all processes between turns, record and replay the provider, and
  a durable resume still succeeds

Static lint: denylist scan, record shapes.

Replacement acceptance: the same scenarios, a new engine, new sessions.

## Reserved

Not frozen, and not yet decided:

- Usage cadence guarantees beyond "cumulative, final before terminal"

## Changelog

Dated, owner-ratified amendments only.

- 2026-09-02: v1 FROZEN by owner ratification. Freeze bar at stamp time: the
  spec exists.


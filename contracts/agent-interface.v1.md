# Agent Interface Contract v1 (FROZEN 2026-10-02)

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

**Records.** Every binding carries these shapes:

```text
TurnInput           { content: [ContentPart...], model?, history?: [ConversationMessage...] }
ConversationMessage { role, content: [ContentPart...] }
TurnResult          { state, content?, error?, usage? }
ContentPart         { type: "text", text } | { type: "image", media_type, data }
SessionRecord       { session_id, persistence }
```

`Event` is the envelope defined in [`turn-events.v1`](turn-events.v1.md) section 1.

`ConversationMessage.role` is a closed set: `"system"`, `"developer"`, `"user"`, and
`"assistant"`. Supplied history follows section 3.

`ContentPart.type` is a closed set: `"text"` and `"image"`. An image part's
`media_type` is a closed set: `"image/png"`, `"image/jpeg"`, `"image/gif"`, and
`"image/webp"`. Its `data` is the image bytes as a standard base64 string, so every
binding carries the same value. Image parts are input only: they appear in
`TurnInput.content`, in supplied `user` messages, and in completed tool results
(section 6), and never in `TurnResult` or any event. An image part anywhere else in
`TurnInput`, an unregistered `media_type`, or `data` that is not valid base64 fails
`invalid_input`. Images are never fetched by reference.

`TurnResult` is exactly the payload of the `terminal` event, so a caller that has read
one has read the other.

**Lifecycle.** `create_agent` returns a fully ready agent or an error, never something
partially ready. Close is idempotent, and closing with an active turn requests
cancellation and drains all paired events before returning. Operations on closed
objects fail `closed`. Independent agents are isolated, with no leakage through
process-global state.

## 2. Configuration is inert, closed-vocabulary data

`AgentOptions` groups:

```text
instructions   provider   model   tools   skills (source locations only)
mcp_servers    approvals    tool_error_policy    tool_result_max_bytes
working_directory    additional_directories    sessions_directory    environment
```

It is built, passed once, and never consulted again. `tools` is the whole tool set:
caller declarations and built-in names; absent, every built-in.

`working_directory` is the directory the agent works in. Absent, it is the host
process's current directory at `create_agent`; a relative value resolves against that
directory. It is resolved once, at `create_agent`, to an absolute path with symlinks
resolved. A value that is not an existing directory fails `invalid_input`, naming the
path. Creating, running, or closing an agent never changes the host process's current
directory, and later changes to that directory never move an existing agent.

Built-in tools resolve relative paths against it and `bash` runs in it. Stdio MCP
servers start in it, relative `skills` locations resolve against it, and delegated
work inherits it. Caller-supplied tools run in the host and are unaffected.

`additional_directories` lists other directories the agent may work in. Each entry is
resolved like `working_directory`, with relative entries resolved against the working
directory, and an entry that is not an existing directory fails `invalid_input`, naming
the path. Delegated work inherits them. Absent, there are none.

The engine keeps agent work in the working directory and the additional directories:
every default, relative path, and process the engine starts points there. Work goes
outside them only when a tool call the model makes for the task names a location
outside them.

`sessions_directory` is where the engine keeps the agent's sessions and all other
engine state, as defined in [`host-config.v1`](host-config.v1.md) section 4. Absent, it
is computed from the working directory. Engine state never goes in the working
directory or the additional directories unless `sessions_directory` points there. The
host configuration file and a relative `sessions_directory` resolve against the host
process's current directory.

`environment` maps variable names to string values. The engine copies the host
process's environment once, at `create_agent`, and applies these entries on top. That
result is the environment of every process the engine starts for the agent: `bash`,
skill commands, and stdio MCP servers, whose own `env` applies last. Delegated work
inherits it. It never changes the host process's environment, and later changes to the
host process's environment never reach an existing agent. The agent's provider
connection reads its credentials and endpoints from it. It does not feed host
configuration. A name that is empty or contains `=`, or a value
that is not a string, fails `invalid_input`. Absent, the copy is used unchanged.

Refused at construction, by name, with a remedy:

- unregistered fields
- fields the engine will not honor
- duplicate tool names
- a name that is not a built-in
- a caller declaration without a handler

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

A resumed session works in the resuming agent's working directory, which then replaces
the one its metadata records.

**Turns within a session.** Sessions are multi-turn and ordered: a new turn observes
earlier terminal turns. One turn is active at a time, so a second `start_turn` fails
`busy` rather than queueing. Sessions run concurrently and isolated from one another.
`fork()` branches a session: the child sees parent history as of the fork, and child
turns never appear in the parent. The child gets its own engine-generated id and
inherits the parent's persistence. Forking a session with an active turn fails `busy`.

`delete_session` removes a durable session and its transcript. An unknown id fails
`not_found`, and deleting a session with a live handle fails `session_in_use`. Deletion
is not undone by a later resume.

**Supplied history.** `TurnInput.history` seeds conversation context. It MAY be supplied
only to an ephemeral session that has accepted no turn and has no inherited conversation.
Supplying it otherwise fails `invalid_input`; it never replaces or appends to an existing
seed. Omitting it preserves ordinary session behavior.

When history is supplied, its messages precede `TurnInput.content`. Nonempty `content`
appends one user message; empty `content` appends nothing. Empty history with empty
`content` fails `invalid_input`. No final role is required, and the engine MUST NOT
invent a trailing user message.

The input is snapshotted at acceptance. Message order, roles, text, and content-part
boundaries MUST be preserved. Every supplied role is conversation context: `system` and
`developer` messages MUST NOT replace the agent's configured instructions, tools,
approval policy, or other configuration. Unregistered roles, tool/function-call
structures, and content section 1 does not permit are refused with `invalid_input`.

Invalid supplied history or its combination with `content` fails at the method with
`invalid_input` and a field-specific remedy, before a stream exists, a provider is
contacted, or an executor is invoked. Refusal MUST NOT mutate the session or consume its
first turn. Existing `closed` and `busy` failures still apply.

The seed remains part of the context for later turns and is inherited by a fork exactly
once. Imported messages create no completed turns, ids, events, results, or historical
usage. `session.history` contains only actual turns, whose recorded input retains any
supplied history. Processing the seed in a new model request counts toward that turn's
usage normally. The first seeded turn reports `continuation: "fresh"`.

## 4. The conversation stays on the caller's side

A session's history lives in a **local transcript**, written where the agent runs. That
transcript is the only authoritative record of the conversation.

Beside the transcript, the engine keeps a per-session **observation capture**: the
ordered, redacted record of runtime events behind each turn, in the Amplifier Context
Intelligence form, so the tooling that reads Amplifier CLI sessions reads this engine's
sessions unchanged. The capture is observation, never authority: resume reads the
transcript alone, and a missing or partial capture changes no session semantics or
result. When the host names a Context Intelligence destination
([`host-config.v1`](host-config.v1.md) section 4), the capture is also forwarded there;
forwarding failure is never a turn failure.

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

A turn whose conversation holds an image part runs only on models that accept images.
Routing never drops below the ceiling to one that does not. When the selected model
cannot accept images, the turn fails `image_unsupported` rather than dropping or
describing the image. The failure surfaces at the method when it is known before the
stream exists, and in `terminal` otherwise. After a turn fails `image_unsupported`, the
conversation holds none of that turn's images: each is replaced by the one-line
description from section 6, so later turns are not refused for them.

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

The built-in tools are `read_file`, `write_file`, `edit_file`, `glob`, `grep`, `bash`,
`web_fetch`, `web_search`, and `delegate`. `tools` selects them by name; a caller or
MCP tool may take an unselected built-in's name. Each binding exports the nine names
as `BUILTIN_TOOLS`.

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

`tool_error_policy` is an optional closed choice: `"continue"` (the default) or
`"stop"`. It is programmatic configuration only, snapshotted with `AgentOptions`.
An unregistered value fails construction with `invalid_input` before any work begins.

With `"stop"`, each of these errors ends the turn as `failure`, except
`tool_completion_unknown` when a cancellation has already been accepted.

With `"continue"`, ordinary execution errors `tool_failed` and
`tool_completion_unknown` return to the model as correlated tool results and the
same turn continues. The public result retains its `failed` or `unknown` resolution,
complete error record, and any captured partial output. A later successful turn
result does not change those tool resolutions. Invalid results, unavailable
executors, approval refusals, skill guard rejection or failure, and accepted
cancellation retain their terminal semantics. Recovery never bypasses a guard.

An uncertain outcome is passed through as uncertain. The engine MUST NOT retry an
effect that may already have landed, MUST NOT claim it was rolled back, and ignores
resolutions that arrive after the call is settled.

After an unknown outcome under `"continue"`, the rest of that turn permits only
model responses and engine-provided local read-only inspection. No new shell,
write, caller-supplied, MCP, delegated, or skill effect may start, including work
already waiting for approval. Already executing effects drain normally. A request
that violates this restriction receives a `cancelled` tool resolution with
`tool_recovery_blocked` and ends the turn as `failure`; it never reaches its executor.
The error identifies the original uncertain call and asks the caller to inspect its
effects before requesting further work in a new turn. A model-generated repeat
cannot authorize itself. Accepted cancellation still starts no new work.

Recovery does not retry a failed call automatically or fabricate a successful
result. Local inspection retains normal approval and skill guard checks.

A completed result is text, or a list of text and image parts as defined in section 1.
Image parts enter the conversation for the model with the result's text. A malformed
part fails `tool_result_invalid`. MCP image content is carried as image parts. A
result holding an image fails `image_unsupported` when the selected model or provider
cannot accept images in tool results, in `terminal`, rather than dropping or
describing the image. In `ToolResolution.content`, and so in every event, each image
part is one line naming its `media_type` and decoded size; the image bytes never
appear there.

`tool_result_max_bytes` caps the text of every completed result at that many UTF-8
bytes before it enters the conversation, default `131072`, `None` for no cap. The
engine appends one line naming the bytes kept of the total; the resolution carries
`truncated` and `original_bytes`. Any other value fails construction with `invalid_input`.

## 7. Approvals: the caller's veto, before execution

With a handler, every consequential action passes through it first and resolves
exactly one way. Without one, the static policy decides: `AgentOptions.approvals`, else
the `approvals` key of [`host-config.v1`](host-config.v1.md) section 5. Neither is ever
inferred.

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
tool_recovery_blocked
approval_cancelled         approval_timeout           approval_unavailable
approval_invalid           provider_failed            internal_failed
contract_version_mismatch  engine_unavailable         context_exceeded
image_unsupported
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
and is distinct from every package version. Every change is a dated, owner-ratified
amendment in the changelog below. The connection beneath exposes no version of its own;
this token is what crosses it.

## Invariants

1. **No reachable name names an internal**, whether a type, field, enum value, or
   error code. `Excluded` below is the literal denylist, enforced at review.
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
- Session storage internals beyond the layout named in
  [`host-config.v1`](host-config.v1.md) section 4
- Context-intelligence configuration
- A caller-facing command line, in this or any future version
- Modes and recipes, which are engine-internal if they exist at all
- More than one provider per agent
- Resume-or-create

## Backlogged

Candidate clauses. Each names the evidence that promotes it.

- **Skills surface semantics.** Two host integrations require the same observable
  skill lifecycle, distinguishable from a tool in evaluations.
- **Sub-agent lifecycle visibility.** Two implementations demonstrate identical
  host-visible nesting, cancellation, and accounting. Until then, delegation appears
  as tool activity.
- **Smart-tool vocabulary.** The separate smart-tools project ships a contract of its
  own that needs a hook here.
- **Attachments and non-image media content.** A real caller needs media parts beyond
  images, with evidence of lossless cross-binding representation.
- **Concurrent turns per session.** A real caller demonstrates a need that `busy`
  cannot serve, plus defined event-interleaving semantics.
- **Cross-family durable-state migration.** Two durable-state families demonstrate
  lossless migration with recovery evidence. Until then, a replaced engine returning
  `not_found` for a prior family's ids is conforming.

## Reserved

Not frozen, and not yet decided:

- Usage cadence guarantees beyond "cumulative, final before terminal"

## Changelog

Dated, owner-ratified amendments only.

- 2026-10-02: v1 FROZEN by owner ratification.
- 2026-10-05: Additive: `AgentOptions.working_directory`,
  `AgentOptions.additional_directories`, and `AgentOptions.environment` (section 2), and
  a resumed session working in the resuming agent's working directory (section 3), by
  owner ratification.
- 2026-10-05: Breaking, amended in place by owner ratification: `AgentOptions.storage`
  replaced by `AgentOptions.sessions_directory` (section 2), with the `host-config.v1`
  amendment of the same date.
- 2026-10-05: Behavior change, amended in place by owner ratification: the default
  `tool_error_policy` is `"continue"` (section 6), so ordinary tool errors return to the
  model.
- 2026-10-05: Versioning, by owner ratification: the additive-only rule is removed.
  Every change is a dated amendment here; a breaking one is also listed under
  **Breaking** in `CHANGELOG.md`.

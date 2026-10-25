# HTTP Face Contract v1 (FROZEN 2026-10-02)

**Who builds against this:** callers who cannot embed a binding, because there is no
binding in their language, because the engine runs elsewhere, or because they already
speak chat completions and want an agent behind it without an integration.

**What it freezes:** a projection of part of
[`agent-interface.v1`](agent-interface.v1.md) onto an OpenAI-compatible
chat-completions endpoint. Point an existing client at a different base URL and get an
agent instead of a model.

This is **one face, not the contract**. Embedding a binding is the primary path. This
face carries what the shape allows and names what it drops, and no capability
elsewhere is designed to fit this shape.

## 1. The wire shape is chat completions, unmodified

An unmodified OpenAI-compatible client works, streaming and not, with no custom
headers and no dialect. The frozen field set is the supported field set listed in
[`docs/http/reference.md`](../docs/http/reference.md), and nothing outside it appears.

The field set may include extension fields that an unmodified client can ignore.
Optional request headers may add capability; a request without them gets exactly the
behavior of a request from an unmodified client.

## 2. One request is one turn

The returned assistant message is the turn's final reply (`terminal.content`). It is
never an intermediate step, and never a tool call handed back for the client to
execute. Tool calling happens inside the turn, server-side.

## 3. Streaming carries reply text and reasoning

The turn's `output_delta` run, as content chunks, in order, closing at `terminal`.
Reasoning rides the same chunks as the `reasoning_content` extension field: the text of
each `reasoning_delta`, and the text of a `reasoning_final` whose block streamed no
deltas. A non-streaming completion carries the same reasoning, joined in order, as
`reasoning_content` on its message.

Tool time is silent here. A client that needs live activity embeds a binding.

## 4. Declared event subset

Per `turn-events.v1` section 5, this face carries `output_delta`, `reasoning_delta`,
`reasoning_final`, and `terminal`.

Errors ride the chat-completions error shape, carrying our `code` and `remedy`.

Everything else is dropped. See the losses below.

## 5. The model name selects a configured agent and a model

The server is itself a host. It builds its agents at start through `AgentOptions` and
the ambient layers in [`host-config.v1`](host-config.v1.md).

A request selects the model for its turn and nothing else. `model` is the configured
agent's name, which runs the agent's own provider and model, or `<provider>/<model>`,
which runs that provider and model as `Session.set_model` would. `reasoning_effort` sets
the turn's reasoning effort as `TurnInput.reasoning_effort` does. Instructions, tools,
approvals, and the sessions directory remain server-start settings.

An unrecognized model name is refused by name, and an unavailable selection fails as the
binding fails it. The model named is the model the turn runs, per VISION principle 5.

## 6. Stateless by default, as chat completions is

The client holds the conversation and sends it whole on every request, as chat
completions defines. Without the session header in section 12, the face keeps nothing
between requests. Each request runs as one ephemeral turn seeded with the history the
client sent, through `agent-interface.v1` and no other route.

An assistant message may carry the `reasoning_content` this face returned. It is
accepted and not used: reasoning is not part of `ConversationMessage`.

The face maps the entire `messages` list to `TurnInput.history` and sets
`TurnInput.content` to `[]`. A message's string content becomes one text part; a
content-part array preserves its order and boundaries. An `image_url` part in a `user`
message becomes one image part when its `url` is a base64 `data:` URL; any other URL is
refused, never fetched. The four `ConversationMessage` roles are accepted. An empty
message list, other roles, tool/function-call structures, other media, and images
outside `user` messages are refused with `invalid_input` and a remedy.

The face MUST NOT extract a final message into `TurnInput.content` or add a user
message. Historical `system` and `developer` messages remain conversation context and
never replace the server's configured instructions, tools, approval policy, or other
configuration. Any ephemeral session created for a request is closed when the request
ends, including rejection before a turn starts.

Without the session header there is no server-held conversation to address, so
nothing can collide in one and nothing has to be resolved against one.

## 7. A failure is never a successful completion containing an apology

## 8. Bearer-token auth, loopback bind by default

A face whose point is being easy to reach must not be reachable by accident.

## 9. Usage is one total per turn

A successful response carries `terminal.usage` as the chat-completions `usage` object,
summed across every selection the turn used, whether or not the client asked for it.
Streaming carries it on the chunk that finishes the turn. `cost_usd` is an extension
field carrying the summed USD cost as a decimal string. Cache writes are counted in
`prompt_tokens` and also apart, in `prompt_tokens_details.cache_write_tokens`. The
extension field `com.microsoft.amplifier.context` carries the last `context_size`
progress payload of the turn, `{tokens, budget}`, when the turn emitted one.

A field appears only when every count it sums is known, and is otherwise absent, never
zero. An unreported cache-write count adds nothing, since not every provider reports
one.

## 10. Versioning

`http-face/1`, independent of the other contracts and of releases. Every change is a
dated, owner-ratified amendment in the changelog below.

## 11. Provider discovery

Two read-only endpoints project `list_providers` and `list_models` from
[`agent-interface.v1`](agent-interface.v1.md) section 1, under the same bearer auth:

```text
GET /v1/providers                     -> { object: "list", data: [ProviderRecord...] }
GET /v1/providers/{provider}/models   -> { object: "list", data: [ModelRecord...] }
```

They use the server's own environment. A request carries none, so a client learns what
the server can reach, never a credential value. Failures ride the error shape in
section 4.

`GET /v1/models` lists every name section 5 accepts: the configured agent's name first,
then `<provider>/<model>` for each model of each provider whose credentials are found
or not required. Each entry may carry the model's `display_name`, `context_window`,
`max_output_tokens`, and `reasoning_efforts` as extension fields, under the omission
rule of the provider discovery records.

## 12. Server-held sessions, by request header

A request carrying `Amplifier-Session-Id: <session id>` runs on the durable session with
that id instead of an ephemeral one. The id follows the session-id rule of
`agent-interface.v1` and is refused with `invalid_input` otherwise.

- The final message must be a `user` message. It becomes `TurnInput.content`; the face
  keeps the rest of the conversation, so the earlier messages in the request are read
  only to check that the client and the session still agree.
- They agree when the request has exactly one more `user` message than the session
  has turns in its history. A session that does not exist agrees with a request
  holding one `user` message, and is created by that request.
- When they disagree, such as after the client edits, reverts, or forks the
  conversation, the request runs as in section 6 and the session is left unchanged.
- A request for a session with a turn in progress fails `busy`.
- Closing the connection cancels the turn. The session and its settled turns remain.

The session lives in the server's sessions directory and is reachable through any
binding by the same id.

## Invariants

1. **This face invents no agent behavior.** A capability reachable here and nowhere
   else means the interface was bypassed, not projected.
2. **The interface never reshapes to fit this face.** What cannot be projected is not
   carried. The interface never shrinks until it fits.
3. **Losses are named here, not discovered in production.**
4. **A face is not a binding.** Growing toward approvals, caller tools, or the full
   stream is building a second implementation of the interface, in a protocol chosen
   for a different reason. What this face adds stays inside the chat-completions shape
   and is ignorable by a client that does not know it.

## What this shape cannot carry

Permanent. Embed a binding instead.

- **Seven of the eleven event types.** Chat completions carries reply content and
  reasoning. Tools, approvals, usage, progress, and the brackets have no place in the
  shape.
- **Usage by model.** One total replaces the per-selection grouping.
- **Approvals.** There is no mid-turn round trip, so the server's static policy
  applies. A server whose agent has tools and no policy refuses to start.
- **Host-executed tools.** A caller-supplied tool is a function in the caller's
  process, and this face has no process to reach into. Built-in and MCP tools, which
  the engine and MCP servers execute, are unaffected.
- **Per-request configuration beyond the model.** Instructions, tools, approvals, and
  sessions directory are server-start settings, for everyone served.
- **Conversation edits on a server-held session.** A session continues only from its
  own last turn; an edited, reverted, or forked conversation runs ephemeral.

## Excluded

No promotion path:

- Any extension field a client must understand. The value of this face is that
  unmodified clients work.

## Backlogged

Candidate clauses. Each names the evidence that promotes it. None is open.

## Changelog

Dated, owner-ratified amendments only.

- 2026-10-02: v1 FROZEN by owner ratification.
- 2026-10-05: Wording only: "storage" reads "sessions directory", following the
  `host-config.v1` amendment of the same date.
- 2026-10-05: Versioning, by owner ratification: the additive-only rule is removed.
  Every change is a dated amendment here; a breaking one is also listed under
  **Breaking** in `CHANGELOG.md`.
- 2026-10-06: Additive: provider discovery endpoints (section 11), by owner
  ratification.
- 2026-10-06: Wording only: reasoning effort is named among the server-start settings,
  following the `agent-interface.v1` amendment of the same date.
- 2026-10-06: Wording only: "ceiling" reads "model" among the server-start settings,
  following the `agent-interface.v1` amendment of the same date.
- 2026-10-21: Additive, by owner ratification: per-request model selection and
  reasoning effort (section 5), reasoning in the stream (sections 3 and 4), cache writes
  counted apart (section 9), model listing (section 11), and server-held sessions by
  request header (section 12), promoted from Backlogged. The evidence: opencode, a
  chat-completions client, sends back only reply text, so client-held history drops the
  agent's tool work and context management on every request.
- 2026-10-21: Additive, by owner ratification: `reasoning_content` on assistant
  messages is accepted and not used (section 6), so a client can send back what this
  face returned; and the context size in usage (section 9), from the `turn-events.v1`
  amendment of the same date.
- 2026-10-21: The backlogged richer second face is promoted to its own contract,
  [`http-turns.v1`](http-turns.v1.md), served by the same server.

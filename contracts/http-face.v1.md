# HTTP Face Contract v1 (FROZEN 2026-09-02)

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
headers and no dialect. The frozen field set is the one the kit pins as data, and
nothing outside it appears.

## 2. One request is one turn

The returned assistant message is the turn's final reply (`terminal.content`). It is
never an intermediate step, and never a tool call handed back for the client to
execute. Tool calling happens inside the turn, server-side.

## 3. Streaming carries reply text only

The turn's `output_delta` run, as content chunks, in order, closing at `terminal`.

Tool time is silent here. A client that needs live activity embeds a binding.

## 4. Declared event subset

Per `turn-events.v1` section 5, this face carries `output_delta` and `terminal`.

Errors ride the chat-completions error shape, carrying our `code` and `remedy`.

Everything else is dropped. See the losses below.

## 5. The model name selects a configured agent

The server is itself a host. It builds its agents at start through `AgentOptions` and
the ambient layers in [`host-config.v1`](host-config.v1.md). Requests carry no
configuration at all.

An unrecognized model name is refused by name.

## 6. Stateless, as chat completions is

The client holds the conversation and sends it whole on every request, as chat
completions defines. The face keeps nothing between requests. Each request runs as one
ephemeral turn seeded with the history the client sent, through `agent-interface.v1` and
no other route.

There is no server-held conversation to address, so nothing can collide in one and
nothing has to be resolved against one.

## 7. A failure is never a successful completion containing an apology

## 8. Bearer-token auth, loopback bind by default

A face whose point is being easy to reach must not be reachable by accident.

## 9. Versioning

`http-face/1`, independent of the other contracts and of releases. Additive only: new
optional behavior may appear, and nothing is removed, renamed, re-typed, or
re-defaulted.

## Invariants

1. **This face invents no agent behavior.** A capability reachable here and nowhere
   else means the interface was bypassed, not projected.
2. **The interface never reshapes to fit this face.** What cannot be projected is not
   carried. The interface never shrinks until it fits.
3. **Losses are named here, not discovered in production.**
4. **A face is not a binding.** Growing toward approvals, caller tools, or the full
   stream is building a second implementation of the interface, in a protocol chosen
   for a different reason.

## What this shape cannot carry

Permanent. Embed a binding instead.

- **Nine of the eleven event types.** Chat completions carries reply content.
  Reasoning, tools, approvals, usage, progress, and the brackets have no place in the
  shape.
- **Approvals.** There is no mid-turn round trip, so the server's static policy
  applies.
- **Host-executed tools.** A caller-supplied tool is a function in the caller's
  process, and this face has no process to reach into. Built-in and MCP tools, which
  the engine and MCP servers execute, are unaffected.
- **Per-request configuration.** Instructions, provider, ceiling, tools, and storage
  are server-start settings, for everyone served.

## Excluded

No promotion path:

- Any extension field on the chat-completions shape. The value of this face is that
  unmodified clients work.

## Backlogged

Candidate clauses. Each names the evidence that promotes it.

- **A richer second face, carrying the full vocabulary.** A real caller cannot embed a
  binding and cannot do its job through this face.
- **Server-held sessions addressable by the client.** A real caller demonstrates that
  client-held history cannot serve it, which is a claim against the protocol this face
  exists to speak.

## Conformance

Against the stub provider:

- An unmodified client completes a turn, streaming and not
- Streamed concatenation equals non-streamed content equals the `terminal.content` a
  binding observes for the same scripted turn
- Multi-message history is honored
- An unrecognized model is refused with a remedy
- A failure returns the error body, carrying code and remedy, never a successful
  completion
- A missing bearer token is refused, and the default bind is loopback
- Response bodies contain no field outside the pinned set

## Reserved

Not frozen, and not yet decided:

- Usage reporting in the response, and in whose units

## Changelog

Dated, owner-ratified amendments only.

- 2026-09-02: v1 FROZEN by owner ratification. Freeze bar at stamp time: the
  spec exists.


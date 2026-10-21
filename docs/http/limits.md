# What this face cannot carry

These are the limits of chat completions. The same server's [turns](turns.md) endpoint
carries every event and asks you about each effect; embed a binding for the rest.

## Seven of the eleven event types

This face carries `output_delta`, `reasoning_delta`, `reasoning_final`, and `terminal`.
Tool calls, tool results, approval requests, approval decisions, progress, usage, and the
turn brackets have no place in the chat-completions shape, so they are not carried.

Reply text keeps its order, but the text stream does not preserve binding event
envelopes or content-part boundaries. See [events](../concepts/events.md).

## Approvals

There is no mid-turn round trip in this shape, so there is nobody to ask. The server's
static policy applies to every request it serves.

Set the policy with `AMPLIFIER_AGENT_APPROVALS`, `"approvals"` in the config file, or
`approvals` in [`create_app`](quickstart.md#configure-server-side-tools) options. A
server whose agent has tools and no policy refuses to start. To serve an agent with no
tools instead, pass `tools=[]` in `create_app` options.

If you need to see an effect before it happens and refuse it, you need a channel back
into your process. See [approvals](../concepts/approvals.md).

## Tools your own process runs

A tool you supply is a function in your process. This face has no process to reach into,
so a request carrying `tools` is refused rather than accepted and ignored.

Built-in and MCP tools are unaffected. They run inside the turn, server-side, and you see
the reply once they are done. See [tools](../concepts/tools.md).

## Per-request configuration

A request chooses its model and reasoning effort. Instructions, tools, approvals, and
sessions directory are settings the server was started with, for everyone it serves. A
request cannot change any of them; see [configuration](../configuration.md).

## Usage

The response reports one summed total per turn, not the per-model grouping a binding
gives. See [usage](reference.md#usage).

## Sessions

Without the `Amplifier-Session-Id` header there is no server-held conversation. Every
request is one ephemeral turn seeded with the history you sent, which is how chat
completions already works.

With the header, the server holds the conversation and continues it only from its own
last turn. A conversation the client edited, reverted, or forked runs ephemeral, and the
stored session does not follow it. Forking, deleting, and listing sessions live in
[sessions](../concepts/sessions.md), behind a binding. See
[reference](reference.md#sessions).

Ephemeral history does not isolate effects. Requests share the server's configured
tools, filesystem, credentials, and MCP services. A bearer token grants access to that
server; it does not select a separate user or project. Deploy separate hosts when
callers need separate authority or files.

## Network hosting

The service listens on plain HTTP. Use a TLS reverse proxy when exposing it beyond a
trusted local connection. Browser cross-origin access, rate limits, and request-size
limits need hosting infrastructure; the face does not configure them.

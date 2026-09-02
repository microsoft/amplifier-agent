# What this face cannot carry

Chat completions is a shape built to talk to a model. An agent does more than a model
does, and the difference has nowhere to go in this shape.

These are permanent. They are written down here so you meet them while choosing, not in
production. Embed a binding instead.

## Nine of the eleven event types

This face carries `output_delta` and `terminal`. Reasoning, tool calls, tool results,
approval requests, approval decisions, progress, usage, and the turn brackets have no
place in the chat-completions shape, so they are not carried.

Nothing is merged, split, reordered, or quietly dropped instead. The loss is declared,
and it is the same loss every time. See [events](../concepts/events.md).

## Approvals

There is no mid-turn round trip in this shape, so there is nobody to ask. The server's
static policy applies to every request it serves.

If you need to see an effect before it happens and refuse it, you need a channel back
into your process. See [approvals](../concepts/approvals.md).

## Tools your own process runs

A tool you supply is a function in your process. This face has no process to reach into,
so a request carrying `tools` is refused rather than accepted and ignored.

Built-in and MCP tools are unaffected. They run inside the turn, server-side, and you see
the reply once they are done. See [tools](../concepts/tools.md).

## Per-request configuration

Instructions, provider, model ceiling, tools, and storage are settings the server was
started with, for everyone it serves. A request cannot change any of them.

## Usage

Not reported in the response. What a turn actually cost, grouped by the model that
actually ran, is available from a binding. See [usage](../concepts/usage.md).

## Sessions

There is no server-held conversation. Every request is one ephemeral turn seeded with the
history you sent, which is how chat completions already works.

Durable sessions, resuming days later, and forking a conversation all live in
[sessions](../concepts/sessions.md), behind a binding.

## Why it stays this way

This face exists so an existing client works without an integration. Adding a field to
carry one of the losses above would end that, and the client that needed the field would
have been better served by a binding anyway.

The interface does not shrink to fit this shape either. What cannot be projected is not
carried, and no capability elsewhere is designed around what fits here.

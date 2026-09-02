# HTTP reference

```
POST /v1/chat/completions
GET  /v1/models
```

Both require `Authorization: Bearer <token>`. A missing or wrong token is refused.

## Request

Honored:

```
model            the configured agent's name
messages         the whole conversation, sent every time
stream           default true
stream_options   as chat completions defines
```

Accepted and not honored:

```
temperature   top_p   max_tokens   max_completion_tokens   stop   n   user
```

How a turn is run is not a per-request setting, so these change nothing. They are
accepted rather than refused because an unmodified client sends them, and the point of
this face is that unmodified clients work.

Refused, by name, with a remedy:

```
tools         this face never hands a tool call back for you to execute
tool_choice   same
```

Built-in and MCP tools run inside the turn, server-side, and you see the reply after they
have finished. A tool that runs in your own process needs a channel into your process,
which is what embedding a binding gives you. See [limits](limits.md).

## Messages

Every message is conversation content, seeded into one ephemeral turn.

A `system` message is history like any other. It does not become the agent's
instructions, which are a server-start setting for everyone served.

## Response

```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "created": 1767225600,
  "model": "amplifier",
  "choices": [{
    "index": 0,
    "message": {"role": "assistant", "content": "..."},
    "finish_reason": "stop"
  }]
}
```

`content` is the turn's final reply. Nothing outside this shape appears: there is no
extension field, because the value of this face is that unmodified clients work.

Usage is not reported here. Read it from a binding, where it is
[grouped by the model that actually ran](../concepts/usage.md).

## Streaming

```
data: {"object":"chat.completion.chunk","choices":[{"delta":{"content":"It "}}]}
data: {"object":"chat.completion.chunk","choices":[{"delta":{"content":"describes "}}]}
data: {"object":"chat.completion.chunk","choices":[{"delta":{},"finish_reason":"stop"}]}
data: [DONE]
```

Chunks carry the turn's reply text, in order, closing when the turn terminates.

## Models

```bash
curl localhost:9099/v1/models -H "Authorization: Bearer $FACE_TOKEN"
```

Returns the agent this server is configured with, in the chat-completions model shape.
Any other name is refused by name.

## Errors

A failure is an error response. It is never a successful completion containing an
apology.

```json
{
  "error": {
    "message": "no session for id ticket-4417. Create it before resuming, or list sessions to find the right id.",
    "type": "session",
    "code": "not_found",
    "param": null
  }
}
```

`code` is the [registered code](../concepts/errors.md). `type` is its category.
`message` carries the message and the remedy, because there is no extension field to put
a remedy in.

```
400   invalid_input, and a request field that cannot be honored
401   missing or wrong bearer token
403   approval_denied, where the server's static policy refused the effect
404   an unrecognized model name
502   provider_failed
500   internal_failed
```

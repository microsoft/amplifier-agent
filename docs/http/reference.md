# HTTP reference

```
POST /v1/chat/completions
GET  /v1/models
```

Both require `Authorization: Bearer <token>`. A missing or wrong token is refused.

## Request

Accepted:

```
model            the configured agent's name
messages         the whole conversation, sent every time
stream           boolean, default false
stream_options   only with stream: true; include_usage is accepted and changes nothing
```

`model` and `messages` are required. `model` is the face alias returned by
`/v1/models`, not a provider model id. If supplied, `stream_options` must be an object;
`null` is refused.

Other fields are refused with `invalid_input` and a field-specific remedy, including:

```
temperature   top_p   max_tokens   max_completion_tokens   stop   n   user
tools         tool_choice   functions   function_call
```

Requests cannot configure how the agent runs. Unsupported values are refused rather
than silently ignored. The accepted request and response shapes are the
[supported field set](#supported-field-set).

Built-in and MCP tools run inside the turn, server-side, and you see the reply after they
have finished. A tool that runs in your own process needs a channel into your process,
which is what embedding a binding gives you. See [limits](limits.md).

## Messages

`messages` is a nonempty array. Each message has a `system`, `developer`, `user` or
`assistant` role and text content, supplied as a string or an array of
`{"type": "text", "text": "..."}` parts. A string becomes one text part; an array keeps
its part boundaries.

Only `role` and `content` are accepted on a message, and only `type` and `text` on a
part. Fields such as `name`, `tool_calls`, and `tool_call_id` are refused. Empty strings
and empty text-part arrays are accepted; `null` content is refused.

Every message, including the last, maps in order to `TurnInput.history` on the first
turn of a new ephemeral session, with `TurnInput.content: []`. No final user message is
extracted or invented. Any supported role may be last. Any session created for the
request closes when the request ends, including rejection before a turn starts.

`system` and `developer` messages remain conversation content. They do not replace
instructions, tools or approvals configured by the server at startup.

An empty message list, unsupported role, tool or function message, tool/function-call
structure, or media content fails `invalid_input` with a remedy before a turn stream,
provider request or effect. These inputs are never flattened into text or executed as
historical calls. See [turns](../concepts/turns.md#supplying-a-conversation).

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
  }],
  "usage": {
    "prompt_tokens": 19234, "completion_tokens": 812, "total_tokens": 20046,
    "prompt_tokens_details": {"cached_tokens": 18900},
    "cost_usd": "0.0421"
  }
}
```

`content` is the turn's final reply. Nothing outside this shape appears.

## Usage

`usage` is the turn's [usage](../concepts/usage.md) summed across every model that ran,
sent whether or not the client asked for it:

```
prompt_tokens                        tokens_in + cache_write_tokens
completion_tokens                    tokens_out
total_tokens                         prompt_tokens + completion_tokens
prompt_tokens_details.cached_tokens  cache_read_tokens
cost_usd                             cost.USD, as a decimal string
```

`tokens_in` already includes cache reads, so they are not added again. `cost_usd` is an
extension field that clients which do not know it ignore.

A field appears only when every model's count for it is known. A model that reports no
cache writes adds none. When `prompt_tokens` or `completion_tokens` is unknown, `usage`
is absent. The grouping by model that actually ran is available from a binding.

## Streaming

```
data: {"id":"chatcmpl-example","object":"chat.completion.chunk","created":1767225600,"model":"amplifier","choices":[{"index":0,"delta":{"role":"assistant","content":"It "},"finish_reason":null}]}
data: {"id":"chatcmpl-example","object":"chat.completion.chunk","created":1767225600,"model":"amplifier","choices":[{"index":0,"delta":{"content":"describes ..."},"finish_reason":null}]}
data: {"id":"chatcmpl-example","object":"chat.completion.chunk","created":1767225600,"model":"amplifier","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":19234,"completion_tokens":812,"total_tokens":20046,"prompt_tokens_details":{"cached_tokens":18900},"cost_usd":"0.0421"}}
data: [DONE]
```

Chunks carry the turn's reply text, in order, closing when the turn terminates.
Their id, creation time, and model stay constant for the response. A successful stream
ends with `finish_reason: "stop"`, carrying the turn's `usage`, followed by `[DONE]`.

Closing the connection cancels active work and closes the request's ephemeral session.
The service waits for cleanup before releasing that request. Cancellation does not
undo effects that already ran. There are no heartbeat frames or resumable event ids;
configure client and proxy timeouts to allow silent tool work, and disable proxy
buffering when relaying the stream.

## Models

```bash
curl localhost:9099/v1/models -H "Authorization: Bearer $AMPLIFIER_AGENT_FACE_TOKEN"
```

Returns the agent this server is configured with, in the chat-completions model shape.
Any other name is refused by name.

## Errors

A failure is an error response. It is never a successful completion containing an
apology.

```json
{
  "error": {
    "message": "Model 'unknown' is not configured. Select a model returned by /v1/models.",
    "type": "selection",
    "code": "selector_rejected",
    "param": "model"
  }
}
```

`code` is the [registered code](../concepts/errors.md). `type` is its category.
`message` carries the message and the remedy, so clients that show only the message still
show the remedy.

Before streaming begins, errors use the HTTP status below. After headers or content
have been sent, the server emits the same error object in a `data:` event and closes
the connection. It sends neither a successful finish chunk nor `[DONE]`. Any text
already received is a partial result, not a successful completion.

```
400   invalid_input, and a request field that cannot be honored
401   missing or wrong bearer token
403   approval_denied, where the server's static policy refused the effect
404   an unrecognized model name
502   provider_failed, engine_unavailable
500   other registered failures, including internal_failed
```

Inspect `error.code` and the remedy before retrying. A failed or disconnected request
may already have performed tool work, and this endpoint has no idempotency key.

## Supported field set

Every object is closed: a field not listed here is refused in a request and never
appears in a response.

```
request      model (nonempty string), messages (nonempty array of message),
             stream (boolean, default false),
             stream_options {include_usage (boolean)} (only with stream: true)
message      role: system | developer | user | assistant
             content: string, or array of text part
text part    type: "text", text (string)

completion   id, object: "chat.completion", created (integer >= 0), model,
             choices: exactly one
               {index: 0, message {role: "assistant", content}, finish_reason: "stop"},
             usage?
chunk        id, object: "chat.completion.chunk", created, model,
             choices: exactly one, either
               {index: 0, delta {role?: "assistant", content}, finish_reason: null}
               {index: 0, delta {}, finish_reason: "stop"}, with usage?
usage        prompt_tokens, completion_tokens, total_tokens (integers >= 0),
             prompt_tokens_details? {cached_tokens (integer >= 0)},
             cost_usd? (decimal string)
models       object: "list", data: nonempty array of
               {id, object: "model", created, owned_by}
error        error {message (nonempty), type, code, param (string or null)}
             type: lifecycle | selection | session | turn | input | executor |
                   approval | provider | internal
             code: a registered code, or an owned reverse-domain code
```

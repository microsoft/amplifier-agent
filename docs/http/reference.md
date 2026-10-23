# HTTP reference

```
POST /v1/chat/completions
GET  /v1/models
GET  /v1/providers
GET  /v1/providers/{provider}/models
```

All require `Authorization: Bearer <token>`. A missing or wrong token is refused.
`POST /v1/chat/completions` also takes an optional `Amplifier-Session-Id` header; see
[sessions](#sessions).

## Request

Accepted:

```
model              the configured agent's name, or <provider>/<model>
messages           the whole conversation, sent every time
stream             boolean, default false
stream_options     only with stream: true; include_usage is accepted and changes nothing
reasoning_effort   a value the selected model lists in reasoning_efforts
```

`model` and `messages` are required. `model` is a name returned by `/v1/models`: the
configured agent's name runs the agent's own provider and model, and
`<provider>/<model>` runs that model for this turn, as
[switching models](../concepts/sessions.md#switching-models) does.
`reasoning_effort` sets this turn's [reasoning effort](../providers.md#reasoning-effort);
without it the turn uses the session's. If supplied, `stream_options` must be an object;
`null` is refused.

Other fields are refused with `invalid_input` and a field-specific remedy, including:

```
temperature   top_p   max_tokens   max_completion_tokens   stop   n   user
tools         tool_choice   functions   function_call
```

Requests choose the model and reasoning effort and nothing else. They cannot change
instructions, tools, or approvals, or supply tools; see [limits](limits.md).
The accepted request and response shapes are the [supported field set](#supported-field-set).

## Messages

`messages` is a nonempty array. Each message has a `system`, `developer`, `user` or
`assistant` role and content, supplied as a string or an array of parts. A string
becomes one text part; an array keeps its part boundaries. A `user` message may also
carry images as base64 `data:` URLs:

```json
{"role": "user", "content": [
  {"type": "text", "text": "What is in this image?"},
  {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo..."}}
]}
```

The media type is one of `image/png`, `image/jpeg`, `image/gif`, or `image/webp`.
`detail` is accepted as `auto`, `low`, or `high` and ignored. Any other URL is refused
and never fetched.

Only `role` and `content` are accepted on a message, and only the fields above on a
part. An `assistant` message may also carry the `reasoning_content` this face returned;
it is accepted and not used. Fields such as `name`, `tool_calls`, and `tool_call_id` are
refused. Empty strings
and empty text-part arrays are accepted; `null` content is refused.

Without the session header, every message, including the last, maps in order to
`TurnInput.history` on the first turn of a new ephemeral session, with
`TurnInput.content: []`. No final user message is
extracted or invented. Any supported role may be last. Any session created for the
request closes when the request ends, including rejection before a turn starts.

`system` and `developer` messages remain conversation content. They do not replace
instructions, tools or approvals configured by the server at startup.

An empty message list, unsupported role, tool or function message, tool/function-call
structure, other media, or an image outside a `user` message fails `invalid_input`
with a remedy before a turn stream, provider request or effect. These inputs are never
flattened into text or executed as historical calls. See [turns](../concepts/turns.md#supplying-a-conversation).

## Response

```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "created": 1767225600,
  "model": "amplifier",
  "choices": [{
    "index": 0,
    "message": {"role": "assistant", "content": "...", "reasoning_content": "..."},
    "finish_reason": "stop"
  }],
  "usage": {
    "prompt_tokens": 19234, "completion_tokens": 812, "total_tokens": 20046,
    "prompt_tokens_details": {"cached_tokens": 18900},
    "cost_usd": "0.0421"
  }
}
```

`content` is the turn's final reply. `reasoning_content` is the turn's reasoning, joined
in order, and is absent when the turn produced none. Nothing outside this shape appears.

## Sessions

Send `Amplifier-Session-Id: <session id>` to keep the conversation on the server. The id
follows the [session id rule](../concepts/sessions.md) and is refused with
`invalid_input` otherwise. The session is durable, lives in the server's sessions
directory, and any binding can resume it by the same id.

```
POST /v1/chat/completions
Amplifier-Session-Id: orchard-planning-01

{"model": "amplifier", "messages": [
  {"role": "user", "content": "My project is called Orchard."},
  {"role": "assistant", "content": "Your project is called Orchard."},
  {"role": "user", "content": "What is my project called?"}]}
```

- The last message must be a `user` message. It is the turn's input; the server
  already holds the earlier turns.
- The request continues the session when it has exactly one more `user` message than
  the session has turns. A session that does not exist is created by a request with one
  `user` message.
- Otherwise, such as after the client edits, reverts, or forks the conversation, the
  request runs as an ephemeral session seeded with the whole list, as without the header,
  and the stored session is left unchanged.
- A request for a session with a turn in progress fails `busy`.
- Closing the connection cancels the turn. The session and its finished turns remain.

## Usage

`usage` is the turn's [usage](../concepts/usage.md) summed across every model that ran,
sent whether or not the client asked for it:

```
prompt_tokens                             tokens_in + cache_write_tokens
completion_tokens                         tokens_out
total_tokens                              prompt_tokens + completion_tokens
prompt_tokens_details.cached_tokens       cache_read_tokens
prompt_tokens_details.cache_write_tokens  cache_write_tokens
cost_usd                                  cost.USD, as a decimal string
com.microsoft.amplifier.context           {tokens, budget} of the turn's last primary model request
```

`tokens_in` already includes cache reads, so they are not added again. Cache writes are
part of `prompt_tokens` and are also reported apart. `cache_write_tokens`, `cost_usd`, and
`com.microsoft.amplifier.context` are extension fields that clients which do not know them
ignore.

`com.microsoft.amplifier.context` tells how full the context is after the turn: `tokens`
is the input of the turn's last primary model request, and `budget` is the context budget
the agent compacts against. Either is absent when unknown, and the field is absent when the
turn made no model request. See the `context_size` [progress](../concepts/events.md)
payload.

A field appears only when every model's count for it is known. A model that reports no
cache writes adds none. When `prompt_tokens` or `completion_tokens` is unknown, `usage`
is absent. The grouping by model that actually ran is available from a binding.

## Streaming

```
data: {"id":"chatcmpl-example","object":"chat.completion.chunk","created":1767225600,"model":"amplifier","choices":[{"index":0,"delta":{"role":"assistant","reasoning_content":"The user wants "},"finish_reason":null}]}
data: {"id":"chatcmpl-example","object":"chat.completion.chunk","created":1767225600,"model":"amplifier","choices":[{"index":0,"delta":{"content":"It "},"finish_reason":null}]}
data: {"id":"chatcmpl-example","object":"chat.completion.chunk","created":1767225600,"model":"amplifier","choices":[{"index":0,"delta":{"content":"describes ..."},"finish_reason":null}]}
data: {"id":"chatcmpl-example","object":"chat.completion.chunk","created":1767225600,"model":"amplifier","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":19234,"completion_tokens":812,"total_tokens":20046,"prompt_tokens_details":{"cached_tokens":18900},"cost_usd":"0.0421"}}
data: [DONE]
```

Chunks carry the turn's reply text in `content` and its reasoning in
`reasoning_content`, in order, closing when the turn terminates. Reasoning from delegated
work arrives one complete block per chunk.
Their id, creation time, and model stay constant for the response. A successful stream
ends with `finish_reason: "stop"`, carrying the turn's `usage`, followed by `[DONE]`.

Closing the connection cancels active work and closes the request's ephemeral session,
or, with the session header, cancels the turn and keeps the session.
The service waits for cleanup before releasing that request. Cancellation does not
undo effects that already ran. There are no heartbeat frames or resumable event ids;
configure client and proxy timeouts to allow silent tool work, and disable proxy
buffering when relaying the stream.

## Models

```bash
curl localhost:9099/v1/models -H "Authorization: Bearer $AMPLIFIER_AGENT_FACE_TOKEN"
```

Returns every name `model` accepts, in the chat-completions model shape: the configured
agent first, then `<provider>/<model>` for each model of each provider whose credentials
are found or not required. Any other name is refused by name.

```json
{"object": "list", "data": [
  {"id": "amplifier", "object": "model", "created": 1767225600, "owned_by": "amplifier-agent"},
  {"id": "anthropic/claude-sonnet-5-5", "object": "model", "created": 1767225600,
   "owned_by": "amplifier-agent", "display_name": "Claude Sonnet 5.5",
   "context_window": 200000, "max_output_tokens": 128000,
   "reasoning_efforts": ["low", "medium", "high", "xhigh", "max"]}]}
```

`display_name`, `context_window`, `max_output_tokens`, and `reasoning_efforts` are
extension fields, omitted when unknown.

## Providers

```bash
curl localhost:9099/v1/providers -H "Authorization: Bearer $AMPLIFIER_AGENT_FACE_TOKEN"
curl localhost:9099/v1/providers/anthropic/models -H "Authorization: Bearer $AMPLIFIER_AGENT_FACE_TOKEN"
```

These serve [`list_providers` and `list_models`](../concepts/models.md#discovering-providers-and-models)
with the environment the server's agents get: the server process's environment plus any
`AgentOptions.environment` entries. Requests carry no credentials. Unknown model limits
and unknown `reasoning_efforts` are omitted, never `null`.

```json
{"object": "list", "data": [{"provider": "anthropic", "display_name": "Anthropic", "installed": true,
  "credentials": "found", "credential_variables": ["ANTHROPIC_API_KEY"]}]}

{"object": "list", "data": [{"id": "claude-sonnet-5-5", "display_name": "Claude Sonnet 5.5",
  "context_window": 200000, "max_output_tokens": 128000,
  "reasoning_efforts": ["low", "medium", "high", "xhigh", "max"]}]}
```

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

`code` is the [registered code](../concepts/errors.md). `type` is its category. For
`invalid_input`, `param` names the request field at fault, such as
`messages[0].content[1].image_url.url`.
`message` carries the message and the remedy, so clients that show only the message still
show the remedy.

Before streaming begins, errors use the HTTP status below. After headers or content
have been sent, the server emits the same error object in a `data:` event and closes
the connection. It sends neither a successful finish chunk nor `[DONE]`. Any text
already received is a partial result, not a successful completion.

```
400   invalid_input, and a request field that cannot be honored
400   image_unsupported, where the selected model cannot accept the images
401   missing or wrong bearer token, with code invalid_input
403   approval_denied, where the server's static policy refused a skill hook command
404   an unrecognized model name
409   busy, a turn already running on the session
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
             stream_options {include_usage (boolean)} (only with stream: true),
             reasoning_effort? (string)
header       Amplifier-Session-Id? (session id)
message      role: system | developer | user | assistant
             content: string, or array of text part or image part (user only)
             reasoning_content? (string, assistant only; accepted and not used)
text part    type: "text", text (string)
image part   type: "image_url",
             image_url {url (data:<media type>;base64,<data>),
                        detail? (auto | low | high)}

completion   id, object: "chat.completion", created (integer >= 0), model,
             choices: exactly one
               {index: 0, message {role: "assistant", content, reasoning_content?},
                finish_reason: "stop"},
             usage?
chunk        id, object: "chat.completion.chunk", created, model,
             choices: exactly one, either
               {index: 0, delta {role?: "assistant", content?, reasoning_content?},
                finish_reason: null}, with content or reasoning_content
               {index: 0, delta {}, finish_reason: "stop"}, with usage?
usage        prompt_tokens, completion_tokens, total_tokens (integers >= 0),
             prompt_tokens_details? {cached_tokens? (integer >= 0),
                                     cache_write_tokens? (integer >= 0)},
             cost_usd? (decimal string),
             com.microsoft.amplifier.context? {tokens? (integer >= 0), budget? (integer > 0)}
models       object: "list", data: nonempty array of
               {id, object: "model", created, owned_by, display_name? (string),
                context_window? (integer > 0), max_output_tokens? (integer > 0),
                reasoning_efforts? (array of strings)}
providers    object: "list", data: array of
               {provider, display_name, installed (boolean),
                credentials: found | missing | not_required, credential_variables (array of string)}
provider     object: "list", data: array of
models         {id, display_name, context_window? (integer > 0), max_output_tokens? (integer > 0),
                reasoning_efforts? (array of strings)}
error        error {message (nonempty), type, code, param (string or null)}
             type: lifecycle | selection | session | turn | input | executor |
                   approval | provider | internal
             code: a registered code, or an owned reverse-domain code
```

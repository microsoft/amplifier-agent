# HTTP

An OpenAI-compatible server, for callers that are not Python or not on the same
machine. Start it with `amplifier-agent serve`.

It speaks the chat completions API, so a client already targeting that shape
works against it without changes. Everything specific to this agent is carried
in an `amplifier` extension object, which conforming clients ignore.

```bash
amplifier-agent serve --host 127.0.0.1 --port 8080
```

## Endpoints

```
POST /v1/chat/completions   run a turn
GET  /v1/models             list_models across configured providers
GET  /v1/skills             list_skills
GET  /health                liveness, unauthenticated
```

The server holds one `Agent` for its lifetime and creates a `Session` per
request. `create_agent` runs at startup, so a bad configuration fails the
process rather than the first request.

## A request

```json
POST /v1/chat/completions
Authorization: Bearer <token>

{
  "model": "claude-sonnet-5",
  "messages": [
    {"role": "system", "content": "Prefer small commits."},
    {"role": "user", "content": "Summarize src/parser.py"}
  ],
  "stream": true,
  "amplifier": {"session_id": "ticket-4417", "workspace": "api"}
}
```

`model` selects among the providers the server was configured with. A model no
configured provider offers is `config/unknown_model`.

## How messages become a turn

The chat completions API is stateless and a `Session` is not, so the mapping is
explicit rather than inferred.

**With `amplifier.session_id`**, the server resumes that session and uses only
the final message as the prompt. History lives on the server, so clients do not
resend it and a long conversation does not grow the request.

**Without it**, the server creates an ephemeral session, replays `messages` as
its history, and runs the final message. This is the stateless mode ordinary
OpenAI clients get for free.

Either way one rule decides what the prompt is:

```
Only a final role="user" message becomes the prompt.
```

Everything before it is history. A request whose last message is not from the
user has an empty prompt and continues from history rather than searching
backwards for the most recent user text.

That rule is a privilege boundary, not a parsing convenience. The
[skill sigil](../05-interface/skills.md) is honored only on a genuine user turn,
so a request that ended in a tool result or an assistant message cannot dispatch
a skill the user never submitted.

Client `system` messages are wrapped as user-supplied instructions and injected
at the start of history rather than becoming the agent's own instructions. A
client cannot replace the agent's instruction set by sending a system message;
`AgentConfig.instructions` is the server operator's to set.

## Streaming

`stream: true` returns Server-Sent Events in the OpenAI chunk format.

```
data: {"choices":[{"delta":{"content":"Looking at "},"index":0}],...}
data: {"choices":[{"delta":{},"finish_reason":"stop","index":0}],...}
data: [DONE]
```

The mapping from the [event registry](../05-interface/events.md):

```
message/delta   ->  delta.content
tool/call       ->  delta.tool_calls        (host tools only; see below)
turn/completed  ->  finish_reason, then [DONE]
usage           ->  the usage object on the final chunk
```

Events with no OpenAI equivalent are carried on an `amplifier` key on the chunk,
which is where `thinking/delta`, `tool/call` for builtin tools, `tool/result`,
and `error` arrive. A client that ignores the key gets a correct, ordinary
OpenAI stream. A client that reads it gets everything the library would have
yielded.

```json
{"choices":[{"delta":{},"index":0}],
 "amplifier":{"type":"tool/call","tool_call_id":"c1","name":"read_file",
              "arguments":{"path":"src/parser.py"},"source":"builtin"}}
```

`stream: false` returns one completion, with the same `amplifier` key carrying
the events the turn produced.

`finish_reason` maps from `TurnResult.stop_reason`:

```
completed       -> "stop"
max_iterations  -> "length"
cancelled       -> "stop", with the error on amplifier.error
error           -> "stop", with the error on amplifier.error
```

## Tools

Builtin and MCP tools run on the server. The client sees them as
`amplifier`-carried events and does not execute anything.

Host tools are how a client contributes its own, and over HTTP they use the
OpenAI round trip rather than a callback, because the handler lives in the
client's process and cannot be called from the server's.

```
1. client sends tools[] with name, description, parameters
2. server returns delta.tool_calls and finishes with finish_reason "tool_calls"
3. client runs the handler
4. client posts back with role "tool" appended to messages
```

This is the standard OpenAI contract and it is also the honest one. A host tool
is code in the caller's process, so the caller has to be the one that runs it.
The library's in-process `HostTool.handler` and this round trip are the same
capability expressed for a caller who is and is not in the same process.

`amplifier.tools.allow` and `amplifier.tools.deny` filter the combined set by
name, exactly as `ToolsConfig` does.

## Approvals

Client-supplied tools need no approval mechanism, because step 3 above is the
client deciding whether to run its own code.

Builtin and MCP tools are gated by a policy the operator configures on the
server, since there is no request-scoped callback to consult.

```
deny     the default: every request that would need approval is denied
allow    approve everything, for a trusted single-tenant deployment
patterns approve calls matching a configured allowlist, deny the rest
```

The default is `deny` for the same reason it is in the library. A server does
not become more permitted than an interactive session because it has no one to
ask. Choosing `allow` is a deployment decision that lives in the server's
configuration, where it can be reviewed, rather than in the absence of one.

Approval activity is reported as `approval/requested` and `approval/resolved` on
the `amplifier` key so a client can show what happened, and it cannot answer.

## Providers

The server is configured with a registry rather than a single provider, because
`model` selects per request.

```json
{
  "providers": {
    "anthropic": {"credentials": {"api_key": "..."}},
    "openai":    {"credentials": {"api_key": "..."}}
  }
}
```

`GET /v1/models` returns the union across them in OpenAI's model-list shape.
Credentials resolve at startup, and a provider whose required fields do not
resolve is dropped loudly rather than failing the first request that needs it.

## Auth

Every endpoint except `/health` requires `Authorization: Bearer <token>`,
checked against the tokens the server was started with. A server started with no
token refuses to bind unless it is bound to loopback, so the default posture is
either authenticated or unreachable.

The server holds provider credentials, so an unauthenticated one is a credential
proxy for whoever can reach it.

## Errors

Failures use OpenAI's error envelope, with the agent's code carried intact.

```json
{"error": {"message": "the provider rate-limited the request",
           "type": "provider/rate_limited",
           "code": "provider/rate_limited",
           "param": null,
           "amplifier": {"retryable": true, "details": {}}}}
```

```
config/*     400
session/*    404 for not_found, 409 for busy
provider/*   502, or 429 for rate_limited
tool/*       carried in the stream, not an HTTP status
internal     500
```

A failure during a turn that already started streaming arrives as an
`amplifier.error` event and a terminal chunk, because the status line is long
gone by then.

# HTTP turns

The same server that serves chat completions also streams whole turns: every
[event](../concepts/events.md), and approvals answered over HTTP. Use it when you cannot
embed a binding but need to show tool calls or decide effects.

```
POST /v1/amplifier/turns
POST /v1/amplifier/approvals/{request_id}
```

Both require the face's bearer token. Settings, models, and errors are the face's; see
[reference](reference.md).

## A turn

The request is a chat-completions request without `stream` or `stream_options`, plus an
optional `approvals`:

```bash
curl -N localhost:9099/v1/amplifier/turns \
  -H "Authorization: Bearer $AMPLIFIER_AGENT_FACE_TOKEN" \
  -H "Content-Type: application/json" \
  -H "Amplifier-Session-Id: orchard-planning-01" \
  -d '{"model": "openai/gpt-6.1-sol", "reasoning_effort": "medium", "approvals": "ask",
       "messages": [{"role": "user", "content": "Add a README to this project."}]}'
```

`model`, `messages`, `reasoning_effort`, and the `Amplifier-Session-Id` header mean what
they mean for chat completions, including the [session rules](reference.md#sessions).

The response is a server-sent event stream. Each `data:` line is one event envelope, as a
binding delivers it:

```
data: {"contract_version":"turn-events/1","session_id":"orchard-planning-01","turn_id":"...","sequence":1,"type":"turn_started","payload":{...}}
data: {"contract_version":"turn-events/1",...,"sequence":2,"type":"reasoning_delta","payload":{"text":"..."}}
data: {"contract_version":"turn-events/1",...,"sequence":3,"type":"tool_call","payload":{"call":{"call_id":"c1","name":"write_file",...}}}
...
data: {"contract_version":"turn-events/1",...,"type":"terminal","payload":{"state":"success",...}}
```

The stream ends after `terminal`. A failure after the stream begins is the `terminal`
event, never a dropped connection. Closing the connection cancels the turn and keeps the
session.

## Approvals

With `"approvals": "ask"`, every effect the server's [approval policy](limits.md#approvals)
allows waits for your answer; effects the policy denies are denied without asking. Start
the server with `AMPLIFIER_AGENT_APPROVALS=allow` to be asked about every effect.

Answer each `approval_request` by its `request_id`:

```bash
curl localhost:9099/v1/amplifier/approvals/REQUEST_ID \
  -H "Authorization: Bearer $AMPLIFIER_AGENT_FACE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"decision": "allow"}'
```

`decision` is `allow`, `deny`, or `cancel`, with an optional `reason`. The stream then
carries the `approval_decision`. An unknown, settled, or expired request fails
`not_found`. An unanswered request expires as `timeout` at the
[approval deadline](../concepts/approvals.md), and one still open when the connection
closes settles as `cancel`.

Any request with the server's token can answer. Callers who must not answer each other's
requests use separate servers.

## File changes

A `tool_result` from a built-in tool that changed files carries
`com.microsoft.amplifier.file_changes`, one entry per file:

```json
{"path": "README.md", "change": "added", "patch": "--- /dev/null\n+++ README.md\n@@ ...",
 "additions": 12, "deletions": 0}
```

`change` is `added`, `modified`, or `deleted`. `path` is relative to the working
directory when the file is inside it. `patch` is a unified diff of the file's text. Files
whose content is not text have no entry.

## What it cannot carry

Tools your own process runs, and session listing, forking, and deleting, need a binding.
Instructions and tools are server-start settings, as for chat completions.

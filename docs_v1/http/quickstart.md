# HTTP quickstart

Point an OpenAI-compatible client at a different base URL and get an agent instead of a
model. No custom headers, no dialect, no client changes.

This is one face, not the product. Read [limits](limits.md) before you build on it.

## Settings

The server is itself a host. It builds its agent at start, from
[configuration](../configuration.md), and requests carry none of it. Four settings belong
to the face itself.

```
AMPLIFIER_AGENT_FACE_TOKEN   bearer token. Required. No default.
AMPLIFIER_AGENT_FACE_BIND    default 127.0.0.1
AMPLIFIER_AGENT_FACE_PORT    default 9099
AMPLIFIER_AGENT_FACE_MODEL   the model name this agent answers to, default "amplifier"
```

There is no default token, and the default bind is loopback. A face whose point is being
easy to reach must not be reachable by accident.

See [install](../install.md) for obtaining and starting the server.

## One turn

```bash
curl localhost:9099/v1/chat/completions \
  -H "Authorization: Bearer $FACE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "amplifier",
    "messages": [{"role": "user", "content": "Summarize the repo README."}],
    "stream": false
  }'
```

One request is one turn. The assistant message you get back is the turn's final reply,
after any tool work has already happened server-side. It is never an intermediate step
and never a tool call handed back for you to run.

## From an existing client

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:9099/v1", api_key=FACE_TOKEN)

reply = client.chat.completions.create(
    model="amplifier",
    messages=[{"role": "user", "content": "Summarize the repo README."}],
)
```

```ts
import OpenAI from "openai";

const client = new OpenAI({ baseURL: "http://localhost:9099/v1", apiKey: FACE_TOKEN });

const reply = await client.chat.completions.create({
  model: "amplifier",
  messages: [{ role: "user", content: "Summarize the repo README." }],
});
```

## Streaming

```bash
curl -N localhost:9099/v1/chat/completions \
  -H "Authorization: Bearer $FACE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model": "amplifier", "messages": [...], "stream": true}'
```

Chunks carry reply text and nothing else. Concatenating every `delta.content` gives
exactly the message a non-streaming request returns.

Tool time is silent here. A long gap between chunks means the agent is working. If you
need to show what it is doing, embed a binding.

## Multi-turn

The client holds the conversation and sends it whole every time, as chat completions
defines. The face keeps nothing between requests.

```json
{
  "model": "amplifier",
  "messages": [
    {"role": "user", "content": "Summarize the repo README."},
    {"role": "assistant", "content": "It describes ..."},
    {"role": "user", "content": "Now list the open questions."}
  ]
}
```

There is no server-held conversation to name, so nothing collides and nothing has to be
reconciled.

## Next

```
reference.md   endpoints, fields, and errors
limits.md      what this shape cannot carry
```

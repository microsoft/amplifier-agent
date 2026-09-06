# HTTP quickstart

Point a chat-completions client at the server's base URL. The supported
[request fields](reference.md#request) use the standard wire format.

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

```bash
export ANTHROPIC_API_KEY="your-anthropic-api-key"
export AMPLIFIER_AGENT_PROVIDER=anthropic
export AMPLIFIER_AGENT_MODEL=claude-sonnet-5
export AMPLIFIER_AGENT_FACE_TOKEN="$(openssl rand -hex 32)"
uv run amplifier-agent-face
```

`ANTHROPIC_API_KEY` authenticates the server to its provider. The face token
authenticates your clients to this server. Keep the same face token available in the
terminal running the client examples. See [providers](../providers.md) for other
providers and credentials.

`AMPLIFIER_AGENT_MODEL` selects the provider model ceiling. `AMPLIFIER_AGENT_FACE_MODEL`
is its client-facing alias, so the examples still send `"model": "amplifier"`.

The source installation uses the separate `amplifier-agent-http` package. Agent
settings resolve once at startup from environment and
[`~/.amplifier-agent/config.json`](../configuration.md#file), or the file named by
`AMPLIFIER_AGENT_CONFIG`. Restart the service after changing settings or credentials.
The service closes its agent when the server stops.

## One turn

```bash
curl localhost:9099/v1/chat/completions \
  -H "Authorization: Bearer $AMPLIFIER_AGENT_FACE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "amplifier",
    "messages": [{"role": "user", "content": "Say hello in one sentence."}],
    "stream": false
  }'
```

One request is one turn. The assistant message you get back is the turn's final reply,
after any tool work has already happened server-side. It is never an intermediate step
and never a tool call handed back for you to run.

## From an existing client

Install the client SDK in your application with `uv add openai` for Python or
`npm install openai` for TypeScript.

```python
import os

from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:9099/v1",
    api_key=os.environ["AMPLIFIER_AGENT_FACE_TOKEN"],
    max_retries=0,
)

reply = client.chat.completions.create(
    model="amplifier",
    messages=[{"role": "user", "content": "Say hello in one sentence."}],
)
print(reply.choices[0].message.content)
```

```ts
import OpenAI from "openai";

const client = new OpenAI({
  baseURL: "http://localhost:9099/v1",
  apiKey: process.env.AMPLIFIER_AGENT_FACE_TOKEN,
  maxRetries: 0,
});

const reply = await client.chat.completions.create({
  model: "amplifier",
  messages: [{ role: "user", content: "Say hello in one sentence." }],
});
console.log(reply.choices[0].message.content);
```

Automatic retries are disabled because a failed request may already have performed
tool work. Inspect the [error and remedy](reference.md#errors) before resubmitting.

## Streaming

```bash
curl -N localhost:9099/v1/chat/completions \
  -H "Authorization: Bearer $AMPLIFIER_AGENT_FACE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model": "amplifier", "messages": [{"role": "user", "content": "Say hello."}], "stream": true}'
```

Chunks carry reply text and nothing else. Concatenate `delta.content` to assemble the
turn's reply, and check for a successful finish before treating it as complete.

Tool time is silent here and can produce long gaps between chunks. If you need to show
what the agent is doing, embed a binding.

## Multi-turn

The client holds the conversation and sends it whole every time, as chat completions
defines. The face keeps nothing between requests.

```json
{
  "model": "amplifier",
  "messages": [
    {"role": "user", "content": "My project is called Orchard."},
    {"role": "assistant", "content": "Your project is called Orchard."},
    {"role": "user", "content": "What is my project called?"}
  ]
}
```

There is no server-held conversation to name, so nothing collides and nothing has to be
reconciled.

## Configure server-side tools

The packaged launcher supplies no approval policy. A requested tool fails
`approval_unavailable` unless the server host supplies one. Instructions, skills, MCP
servers, and approvals belong in `AgentOptions`, not environment variables or the
host JSON file.

For a host that permits its tools, save this as `server.py` in the directory where
they should work:

```python
import uvicorn
from amplifier_agent import AgentOptions
from amplifier_agent_http import Settings, create_app

settings = Settings.from_environment()
app = create_app(
    settings,
    AgentOptions(
        instructions="Help explain the files in this project.",
        approvals="allow",
    ),
)
uvicorn.run(app, host=settings.bind, port=settings.port)
```

Start it with `uv run python server.py` and the same environment as above. Static
`"allow"` permits every tool call, including writes and shell commands; instructions
do not restrict that authority. Tools use the server process's captured working
directory, environment, and operating-system permissions. Use `"deny"` to refuse
effects, or embed a binding when each effect needs an individual decision.

## Next

```
reference.md   endpoints, fields, and errors
limits.md      what this shape cannot carry
```

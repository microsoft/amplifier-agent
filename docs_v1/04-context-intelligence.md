# Context Intelligence

Every session records what it did. Context Intelligence is that recording, plus
the option to forward it to a server that turns many recordings into something
you can query.

Locally it costs you nothing to set up. It is already on.

```
session runs
  -> every event is appended to the session's event log on disk
  -> forwarded to each configured destination
  -> the server builds a queryable graph across every session it receives
```

The local log is the durable copy and is written first. Forwarding is opt-in,
best effort, and never a condition for a turn to succeed.

## Why you would want it

A single session is legible from its transcript. A thousand sessions are not.
Once recordings land on a server you can ask questions that span them:

- Which tool fails most often, and what arguments does it fail on?
- How much did last week cost, split by model and by project?
- When an agent delegates, how deep does the chain go and where does it stall?
- Which sessions hit a context compaction, and what did they lose?

None of that is answerable from one session directory, which is the whole reason
the upload path exists.

## What gets captured

Every event the agent emits, in the order it emitted them. That is the same
registry `stream` yields, listed in [Events](05-interface/events.md): the turn
boundaries, replies and reasoning, tool calls and their results, approvals,
usage, and errors.

Each line of the log is one record with four keys:

```json
{
  "event": "tool/call",
  "workspace": "-home-alice-repos-api",
  "timestamp": "2026-08-27T16:27:01.263670+00:00",
  "data": {"session_id": "63cc3feb-...", "turn_id": "...", "tool_name": "read_file", "...": "..."}
}
```

- **`event`** is the event name.
- **`workspace`** is the workspace the session belongs to.
- **`timestamp`** is ISO-8601 with an offset.
- **`data`** is the event payload. `session_id` and `turn_id` live in here, along
  with everything specific to that event type.

Records are appended in emission order and never rewritten.

### Treat the log as sensitive

The log holds what the agent actually saw and did, which includes full prompts,
complete model responses, and every tool argument and result. If a secret passed
through a tool call, it is in the log. If a customer record was read into
context, it is in the log.

Two consequences worth deciding about before you turn on forwarding. Your
destination inherits the sensitivity of the sessions you point at it. And the
local directory deserves the same file permissions you would give a credential
store.

## Where it lives

Recordings sit in the session directory, alongside conversation state.

```
<root>/<workspace>/sessions/<session_id>/
  session.json                    what this session is
  messages.jsonl                  conversation state
  context-intelligence/
    events.jsonl                  the recording
    metadata.json                 the recording's own session record
```

`<root>` is `StorageConfig.root`, defaulting to
`~/.amplifier-agent/state/workspaces`.
`<workspace>` is the agent's workspace. [Storage](05-interface/storage.md)
specifies the layout in full.

The `context-intelligence/` directory is deliberate rather than incidental.
Every tool that reads recordings finds them by that exact path shape, so a
session directory produced here is readable by the existing Context Intelligence
tooling without adaptation.

The two record files carry different format identities on purpose:

```
session.json                     format "amplifier-agent-session"
context-intelligence/metadata.json   format "context-intelligence"
```

Readers on both sides check format and version before parsing anything. Two
identities means each tool correctly refuses the file that is not its own,
rather than parsing it into nonsense.

`events.jsonl` is observational. Resuming a session reads `messages.jsonl` and
never needs the event log, so a truncated, deleted, or unreadable recording
costs you history and nothing else.

Setting `StorageConfig(persist=False)` writes nothing at all, recordings
included. That is the way to turn capture off.

## Forwarding to a server

Add a destination.

```python
from amplifier_agent import (
    AgentConfig,
    ContextIntelligenceConfig,
    Destination,
    ProviderConfig,
    create_agent,
)

agent = await create_agent(
    AgentConfig(
        provider=ProviderConfig(name="anthropic", model="claude-sonnet-5"),
        context_intelligence=ContextIntelligenceConfig(
            destinations={
                "team": Destination(
                    url="https://ci.example.com",
                    api_key=os.environ["CI_API_KEY"],
                ),
            },
        ),
    )
)
```

```python
@dataclass(frozen=True)
class ContextIntelligenceConfig:
    destinations: Mapping[str, Destination] = field(default_factory=dict)


@dataclass(frozen=True)
class Destination:
    url: str
    api_key: str | None = None
    auth_mode: Literal["static", "entra"] = "static"
    auth_resource: str | None = None
```

- **`url`** is the server's base URL.
- **`api_key`** is the bearer token, required under `auth_mode="static"`.
- **`auth_mode`** selects how requests are authenticated.
- **`auth_resource`** is the Entra audience, required under `auth_mode="entra"`.

The mapping key names the destination in logs and diagnostics.

Sessions go to every destination in the mapping. Two entries mean two copies,
which is how you feed a team server and a personal one at the same time without
choosing between them.

An empty mapping, or `context_intelligence=None`, leaves recording local.

## Authentication

**`auth_mode="static"`**, the default, sends `Authorization: Bearer <api_key>`.
Simple, and right for a server you run and hand out keys for.

```python
Destination(url="https://ci.example.com", api_key=key)
```

**`auth_mode="entra"`** acquires a Microsoft Entra token and sends that instead.
The credential comes from the ambient environment, so the same configuration
works for a developer signed in locally and for a service running under a managed
identity, with no code change between them.

```python
Destination(
    url="https://ci.corp.example.com",
    auth_mode="entra",
    auth_resource="api://8f3c1e7a-...",
)
```

A destination reaching a server that only accepts static keys uses
`auth_mode="static"`. Each destination chooses independently, so a mixed fleet is
fine.

Tokens are cached and refreshed ahead of expiry. Identity is resolved once per
process, so switching accounts takes a new process.

A destination whose credentials do not validate is dropped when the agent starts,
loudly, and the others keep working. Local recording is unaffected either way.

## What the server receives

One record per request:

```
POST <url>/events
Authorization: Bearer <token>
Content-Type: application/json

{
  "event": "tool/call",
  "workspace": "-home-alice-repos-api",
  "working_dir": "/home/alice/repos/api",
  "idempotency_key": "aci-event-v1:<sha256>",
  "data": { ... }
}
```

The idempotency key is derived from the record's content, so a retry after an
ambiguous failure is safe. The server suppresses the duplicate rather than
recording the event twice.

## When delivery fails

The design assumption is that the network is unreliable and the log is not.

- **The local log is written before anything is sent.** A failed upload never
  touches it, so nothing is lost by a server being down.
- **Delivery is asynchronous.** Turns never block on the network. Records queue
  and a background worker drains the queue.
- **Transient failures retry** with backoff, in place, so ordering holds.
  Timeouts, connection failures, rate limits, and server errors are all
  transient.
- **Permanent failures are skipped** and reported rather than retried forever. A
  malformed record or a rejected credential does not stall everything behind it.
- **Sustained failure is escalated.** A destination that has been failing
  continuously is reported at error level rather than staying quiet, because a
  silent upload path that has been dead for two days is worse than a loud one.
- **Closing an agent drains what is queued,** within a bound. Records still
  undelivered when that bound expires are reported with a count. They remain in
  the local log.

If a destination was unreachable for a stretch, the local logs are the recovery
path. They are complete, and they can be replayed to the server afterward.

## Querying what you captured

The server builds a property graph. Sessions, tool calls, and events are nodes;
forks, tool ownership, and event membership are edges.

You query it with Cypher.

```cypher
MATCH (s:Session {workspace: $workspace})-[:HAS_TOOL_CALL]->(t:ToolCall)
WHERE t.status = 'error'
RETURN t.tool_name, count(*) AS failures
ORDER BY failures DESC
```

Large payloads are offloaded to a blob store and replaced in the graph with a
`ci-blob://` reference, so a query that touches a session with a huge prompt
returns quickly instead of returning the prompt.

The server, its graph model, and its query interface are documented at
[microsoft/amplifier-context-intelligence](https://github.com/microsoft/amplifier-context-intelligence).

## Next

- Where session state lives, in [Storage](05-interface/storage.md).
- The events you are recording, in [Events](05-interface/events.md).

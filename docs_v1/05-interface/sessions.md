# Sessions

A session is a conversation. It accumulates turns, holds their history, and when
persistence is on it survives the process that created it.

Sessions are created from an `Agent`. Turn execution is on this same object but
is described in [Turns](turns.md).

## Interface

On `Agent`:

```python
async def create_session(
    self,
    *,
    session_id: str | None = None,
    workspace: str | None = None,
    instructions: str | Instructions | None = None,
) -> Session: ...

async def resume_session(self, session_id: str) -> Session: ...

async def list_sessions(self, *, workspace: str | None = None) -> list[SessionInfo]: ...

async def delete_session(self, session_id: str) -> None: ...
```

On `Session`:

```python
class Session:
    id: str
    workspace: str

    async def history(self) -> list[Message]: ...

    async def usage(self) -> Usage: ...

    async def fork(self, *, session_id: str | None = None) -> Session: ...

    async def close(self) -> None: ...

    async def __aenter__(self) -> "Session": ...
    async def __aexit__(self, *exc: object) -> None: ...
```

## Identity

A session is identified by the pair `(workspace, session_id)`. The id is unique
within its workspace, not globally.

`workspace` defaults to a value derived from the agent's `cwd`, so separate
checkouts get separate session histories without you asking. Pass `workspace` on
`create_session` to override it for one session.

When `create_session` omits `session_id`, one is generated and available on
`Session.id`. Supply your own when you want session ids to match identifiers your
application already has.

```python
session = await agent.create_session(session_id=f"ticket-{ticket.id}")
```

## History

Turns on the same session see each other.

```python
session = await agent.create_session()
await session.run("Read src/parser.py and summarize it.")
await session.run("Now write tests for the third function you described.")
```

The second turn knows what the first one found. This holds for the lifetime of
the `Session` object regardless of whether persistence is enabled.

`history()` returns the conversation as a list of `Message`. It is a read of what
the agent is working from, not a handle for editing it.

```python
@dataclass(frozen=True)
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str
```

`content` is the message text, which is what you need to render a conversation.
The complete record, including tool calls and the agent's reasoning, is on disk
in `messages.jsonl`. See [Storage](storage.md).

## Usage

`usage()` returns the session's running total, accumulated across every turn it
has run. `TurnResult.usage` is one turn; this is all of them.

```python
total = await session.usage()
print(total.cost_usd, "over", len(await session.history()), "messages")
```

The fields carry the same meaning and the same overlap rules as on a single turn.
See [Turns](turns.md#usage). Two differences follow from it being cumulative:

- **`cost_usd` is `None` if any turn's cost was unknown.** A partial total is
  worse than no total, because it looks authoritative and under-reports.
- **`model` is the model of the most recent turn.** A session that changed models
  is not summarized by one name, so use the per-turn `TurnResult.usage` when you
  need spend attributed by model.

A resumed session resumes its totals. Forking copies the source's totals to the
fork, so the two diverge from the fork point the same way their histories do.

## Per-session instructions

`create_session` accepts `instructions` that override the agent-level value for
that session only. Append and replace semantics are the same as on
[`AgentConfig`](../03-configuration.md#instructions).

```python
reviewer = await agent.create_session(
    instructions="You are reviewing, not editing. Never modify a file."
)
```

This is how one agent serves several roles without constructing several agents.

## Resuming

```python
session = await agent.resume_session("ticket-4417")
```

`resume_session` reconstructs a session from persisted state and raises
`session/not_found` when there is nothing under that id. What gets reconstructed
is covered in [Storage](storage.md).

With `StorageConfig(persist=False)`, nothing is written, so `resume_session`
finds only sessions still live in the current process.

## Listing and deleting

```python
for info in await agent.list_sessions():
    print(info.id, info.turn_count, info.updated_at)
```

`list_sessions` returns `SessionInfo` for the sessions visible to the agent,
filtered to `workspace` when you pass one. It reads session records only, never
conversation state, so listing a thousand sessions does not load a thousand
transcripts.

```python
@dataclass(frozen=True)
class SessionInfo:
    id: str
    workspace: str
    created_at: datetime
    updated_at: datetime
    turn_count: int
    usage: Usage
```

`usage` is the same cumulative total `Session.usage()` returns, carried on the
record rather than computed on read. That is what keeps "what did we spend last
month" a listing instead of a thousand transcript replays.

`delete_session` removes the session and everything recorded under it. A
subsequent `resume_session` for that id raises `session/not_found`.

## Forking

`fork` creates a new session seeded with a copy of the source session's history
at the time of the call.

```python
alternative = await session.fork()
await alternative.run("Try the other approach instead.")
```

The source is unaffected by the fork and by any later turn on it. The two
diverge from the fork point and never rejoin. This is how you explore a second
approach without losing the first, and how you branch one expensive setup
conversation into several cheap continuations.

## Closing

`close` releases the session. It is idempotent. Closing an agent closes its
sessions.

```python
async with await agent.create_session() as session:
    await session.run("...")
```

Turn methods on a closed session raise `session/closed`.

## Errors

- **`session/not_found`** no session exists under that id, raised from
  `resume_session` and `delete_session`.
- **`session/closed`** the session or its agent has been closed.
- **`session/busy`** a turn is already running on this session. Retryable.
- **`storage/unavailable`** persisted state could not be read or written while
  `persist` is on. Retryable.

See [Errors](errors.md) for the full registry.

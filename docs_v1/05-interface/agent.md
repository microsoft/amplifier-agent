# Agent

An `Agent` is what `create_agent` returns. It owns sessions, holds the resolved
provider, and lives as long as your application does.

```python
async def create_agent(config: AgentConfig) -> Agent: ...
```

Everything the agent needs comes from the [`AgentConfig`](../03-configuration.md)
you pass. There is no second setup call and no mutable settings afterward. To
change how an agent behaves, create another one.

## Creating an agent

```python
agent = await create_agent(
    AgentConfig(provider=ProviderConfig(name="anthropic", model="claude-sonnet-5"))
)
```

`create_agent` is where the configuration stops being inert. It validates the
config, resolves provider credentials, and prepares the tool set. Anything wrong
with the configuration surfaces here, as an `AgentError` with a `config/*` code,
rather than partway through a turn.

Credentials resolve once per `create_agent` call and are not cached across calls,
so a rotated key takes effect on the next call without restarting the process.

## Interface

```python
class Agent:
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

    async def list_skills(self) -> list[SkillInfo]: ...

    async def close(self) -> None: ...

    async def __aenter__(self) -> "Agent": ...
    async def __aexit__(self, *exc: object) -> None: ...
```

The four session methods are described in [Sessions](sessions.md).
`list_skills` is described in [Skills](skills.md). It is on the agent rather than
module-level because what is discoverable depends on the `SkillsConfig.sources`
this agent resolved.

## Lifetime

An agent moves through three states.

```
constructed  ->  ready  ->  closed
```

It is `ready` the moment `create_agent` returns, and stays that way until you
close it. `close` releases provider connections, MCP server connections, and
anything else the agent opened. It is idempotent, so closing twice is not an
error.

Use it as a context manager when the scope is clear:

```python
async with await create_agent(config) as agent:
    session = await agent.create_session()
    await session.run("...")
```

After close, `create_session` raises `session/closed`. Sessions already open are
closed with the agent.

## Concurrency

Sessions on one agent are independent and run concurrently.

```python
results = await asyncio.gather(
    session_a.run("Review the parser."),
    session_b.run("Review the lexer."),
)
```

Within a single session, one turn runs at a time. Starting a second turn on a
session that is already running one raises `session/busy`, which is retryable.
If you want two turns at once, use two sessions.

## Versions

```python
import amplifier_agent

amplifier_agent.__version__        # the release you installed
amplifier_agent.contract_version   # the version of this interface it implements
```

Both are module-level, so you can read them without constructing an agent. That
matters because the reason to check a version is usually that something is not
working, which is exactly when `create_agent` is failing.

`contract_version` is the one worth branching on. It changes independently of the
release version, and it is what tells you whether the interface you are coding
against is the one you have.

## Errors

- **`config/invalid`** the configuration failed validation.
- **`config/unknown_provider`** the named provider is not registered.
- **`config/unknown_model`** the named model is not available for that provider.
- **`config/missing_credentials`** a required credential could not be resolved.
  `details` names the unresolved fields and the environment variable that would
  satisfy each.
- **`session/closed`** `create_session` was called on a closed agent.

See [Errors](errors.md) for the full registry.

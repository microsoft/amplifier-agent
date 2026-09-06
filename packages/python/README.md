# Amplifier Agent Python binding

Typed agents, sessions, turns, tools, approvals, and events for Python callers.
Execution is supplied by the separate engine dependency.

Requires Python 3.12 or newer. Follow the
[installation guide](https://github.com/microsoft/amplifier-agent/blob/v1/docs_v1/install.md#python)
to install into your application, then set `ANTHROPIC_API_KEY` and run:

```python
import asyncio
from amplifier_agent import AgentOptions, SessionOptions, TextPart, TurnInput, create_agent

async def main():
    async with await create_agent(AgentOptions(
        provider="anthropic", model="claude-sonnet-5",
    )) as agent:
        session = await agent.create_session(SessionOptions(persistence="ephemeral"))
        result = await session.run(TurnInput([TextPart("Say hello.")]))
        if result.error is not None:
            raise RuntimeError(f"{result.error.message} {result.error.remedy}")
        print("".join(part.text for part in result.content or []))

asyncio.run(main())
```

Sessions are durable by default. Tool execution, including file reads, requires an
approval policy. See the
[Python quickstart](https://github.com/microsoft/amplifier-agent/blob/v1/docs_v1/python/quickstart.md)
for streaming, tools, approvals, and resuming sessions.

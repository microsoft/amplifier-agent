# Amplifier Agent

Amplifier Agent is a Python library for embedding an AI agent in your application.
It gives you a model with tools, a loop that runs until the task is done, and a
stream of events describing everything that happened along the way.

What the agent is good for depends on the tools you give it. A filesystem and a
shell make it a coding agent. Your deployment API makes it a release agent. Your
internal services make it whatever those services do.

```python
from amplifier_agent import AgentConfig, ProviderConfig, create_agent

agent = await create_agent(
    AgentConfig(
        instructions="You are a careful engineer. Explain before you edit.",
        provider=ProviderConfig(name="anthropic", model="claude-sonnet-5"),
    )
)

session = await agent.create_session()
result = await session.run("Find the failing test in tests/ and fix it.")
print(result.reply)
```

## Why use it

- **Bring your own model.** Name a provider and a model and the agent handles the
  rest. Moving between Anthropic, OpenAI, Azure, and the others is a configuration
  change, not a rewrite.
- **Give it your own tools.** Built-in tools cover the filesystem, shell, and web.
  Beyond those, any Python function becomes a tool and any MCP server plugs in
  alongside them. The model sees one flat set and does not know where each one
  came from.
- **Teach it what it needs to know.** Skills package domain knowledge and
  procedures the agent picks up when a task calls for them, so your instructions
  stay short and the expertise arrives at the moment it is useful.
- **Decide what it is allowed to do.** Every tool call can route through your code
  before it runs. Approve it, deny it, rewrite its arguments, or cancel the turn.
- **Watch it work.** A running turn emits typed events covering reasoning, replies,
  tool calls, tool results, and token usage. Render them however you want, or
  ignore them and await the final result.
- **Pick up where you left off.** Sessions persist to disk. Resume one tomorrow,
  fork one to explore an alternative, or throw it away.

## The pieces

- **Agent** is created from an `AgentConfig`. It owns sessions and lives as long as
  your application does.
- **Session** is a conversation with history. It runs one turn at a time and
  persists between turns.
- **Turn** is one task, from your prompt to the agent's final reply. Await it for a
  result, or iterate it for events.
- **Event** is everything that happens inside a turn, as it happens.
- **Tool** is what the agent can actually do. Built in, yours, or from an MCP server.
- **Approval** is your veto on a tool call before it runs.
- **Provider** is the model behind it all, plus how its credentials are resolved.

Four calls carry the whole library: `create_agent`, `create_session`, and then
`run` or `stream`. Everything else describes what flows through them.

## Streaming a turn

`run` is the short version of `stream`. When you want to show progress rather than
wait for it, iterate instead of awaiting.

```python
async for event in session.stream("Refactor the parser module."):
    match event:
        case MessageDelta(text=text):
            print(text, end="", flush=True)
        case ToolCall(name=name):
            print(f"\n[{name}]")
```

Both paths run the same turn and produce the same result.

## Where to go next

- Install the library, and the CLI and SDKs if you want them, with
  [Install](01-install.md).
- Build a working agent end to end with the [Quickstart](02-quickstart.md).
- Look up any `AgentConfig` field in [Configuration](03-configuration.md).
- Choose a provider and set up credentials in [Providers](06-providers/index.md).
- Read the complete public surface, one page per area, in
  [Interface](05-interface/index.md). Start here if you are implementing against
  Amplifier Agent rather than calling it.
- Reach the agent through the CLI, the HTTP face, or the TypeScript SDK in
  [Surfaces](07-surfaces/index.md).

## Amplifier Agent or Amplifier App CLI?

Amplifier App CLI is a full application built on the same ecosystem, and it exposes
a much larger surface: bundles, behaviors, recipes, hooks, and swappable
orchestrators. Those are how you compose and reshape an agent from the outside.

Use Amplifier App CLI when:

- you want to assemble the agent yourself from bundles and modules
- you want to swap the orchestrator or attach hooks to the loop
- you are shipping modes, skills, or recipes to end users

Use Amplifier Agent when:

- you want an agent inside your application rather than an application around one
- you want the full agent without taking on responsibility for how it is built
- you want an interface that holds still while the internals keep moving

The narrower surface is the point. Amplifier Agent keeps the following out of your
hands deliberately:

- **Composition.** Bundles, mount plans, modules, and manifests decide how an agent
  assembles itself. Amplifier Agent assembles itself.
- **The loop.** Orchestrators, hooks, and context management change the shape of
  the agent's reasoning. You steer it with instructions, tools, and approvals.
- **Sub-agents.** The agent delegates when a task calls for it, but which
  sub-agents exist is ours to define rather than yours to configure. Their work
  arrives in your event stream as ordinary tool activity.
- **The prompt.** Prompt assembly and context-window management belong to the
  agent. Instruction content goes in through `instructions`.
- **Model routing.** You name a model. The agent decides how to use it.

# Agents

An agent is a provider, a model ceiling, and the authority you hand it. Build one, then
run as many sessions through it as you like.

```
agent = create_agent(options)
```

`create_agent` returns a fully ready agent or an error. There is no partially ready
agent and no second call that finishes construction.

## AgentOptions

Configuration is inert data. It is built, passed once, and never consulted again.
Changing your mind means building another agent.

```
instructions   text placed after the agent's own instructions
provider       one provider id
model          the ceiling
tools          tool declarations, each with a handler
skills         source locations
mcp_servers    MCP server declarations
storage        the root durable transcripts are written under
approvals      a handler, or a static policy
```

That list is closed. Four things are refused at construction, by name, with a remedy:

```
an unregistered field
a field this agent will not honor
two tools with the same name
a tool set without a handler
```

Anything settable outside code resolves first, and `AgentOptions` wins wherever both
speak. See [configuration](../configuration.md).

For what `provider` and `model` mean together, see [models](models.md). For `tools` and
`mcp_servers`, see [tools](tools.md). For `approvals`, see [approvals](approvals.md).

## Skills

`skills` carries source locations and nothing else. A source is a local directory or a
git URL.

How a skill is chosen, loaded, or spent is the agent's business. Skill activity is not a
distinct thing in the event stream; it arrives as ordinary agent work.

## Lifetime

```
agent.create_session(options?)   -> Session
agent.resume_session(id)         -> Session
agent.list_sessions()            -> [SessionRecord]
agent.delete_session(id)
agent.close()
```

`close()` is idempotent. Closing while a turn is running requests cancellation and
drains every paired event before it returns. Any call on a closed agent fails `closed`.

Two agents in one process do not see each other. Nothing passes between them through
process-global state.

## Version

`contract_version` reads `"agent-interface/1"` and is available without invoking
anything. It is not a package version. See [versioning](../versioning.md).

# Interface

Every public name in the library is on this page, and each is described in full
on the page that owns it. Everything else in the package is internal.

## How it fits together

Four roles, and the boundaries between them are what the rest of these pages
describe.

- **Host.** Your application. It supplies configuration, host tools, and an
  approval handler, and it decides what reaches a person.
- **Agent.** What you get back from `create_agent`. It owns its own composition,
  its own reasoning loop, and how it uses the provider you named.
- **Provider.** The model behind the agent, plus how its credentials resolve.
- **Tools.** What the agent can do. Built in, supplied by you, or reached
  through an MCP server.

The division of labor is consistent throughout. The host owns policy and
presentation. The agent owns its own construction. Configuration is inert data.
The event stream is the only way to observe a turn while it runs.

## Core objects

```python
agent = await create_agent(config)      # AgentConfig -> Agent
session = await agent.create_session()  # Agent -> Session
result = await session.run(prompt)      # Session -> TurnResult
# or
async for event in session.stream(prompt):
    ...
```

An `Agent` lives as long as your application. A `Session` is a conversation with
history and runs one turn at a time. A turn is one task, driven either by
awaiting `run` for a result or by iterating `stream` for events. `run` is
`stream` consumed to completion, so both take the same path and produce the same
outcome.

## Pages

- [Agent](agent.md) creating an agent, its lifetime, and what it reports about
  itself.
- [Sessions](sessions.md) identity, history, resuming, and forking.
- [Turns](turns.md) running, streaming, cancelling, and accounting for usage.
- [Events](events.md) the event registry and the ordering rules that hold for
  every turn.
- [Tools](tools.md) built-in, host, and MCP tools, and how the model sees each.
- [Skills](skills.md) packaged knowledge the agent loads when a task calls for
  it.
- [Approvals](approvals.md) the approval handler and the three resolutions.
- [Providers](providers.md) descriptors, credential resolution, and model roles.
- [Storage](storage.md) the on-disk session layout and persistence.
- [Errors](errors.md) `AgentError` and the closed set of error codes.

## The surface

**Entry point**

```
create_agent(config: AgentConfig) -> Agent
```

**Core objects**

```
Agent            create_session, resume_session, list_sessions, delete_session,
                 list_skills, close
Session          run, stream, cancel, fork, close, id, workspace, history,
                 usage
```

**Configuration**

```
AgentConfig
Instructions
ProviderConfig
ToolsConfig
SkillsConfig
McpServerConfig
StorageConfig
ContextIntelligenceConfig
Destination
```

**Tools and approvals**

```
HostTool
ToolResult
ApprovalHandler
ApprovalRequest
ApprovalResponse
```

**Turns and results**

```
Attachment
Message
TurnResult
Usage
SessionInfo
```

**Module-level**

```
__version__
contract_version
```

**Events**

```
Event
TurnStarted        TurnCompleted
ThinkingDelta      ThinkingFinal
MessageDelta       MessageFinal
ToolCall           ToolResultEvent
ApprovalRequested  ApprovalResolved
UsageEvent         ErrorEvent
```

**Providers**

```
ProviderDescriptor
ModelDescriptor
CredentialField
ProviderStatus
list_providers()
list_models(provider: str)
```

**Skills**

```
SkillInfo
```

**Storage**

```
SessionRecord
```

**Errors**

```
AgentError
ErrorCode
```

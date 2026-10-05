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

Construction captures provider connections, the [environment](#environment), the
[working directory](#working-directory), and resolved settings. Later environment or
settings edits apply to newly constructed agents. Resuming a durable session uses the
new agent's configuration and revalidates the saved model refinement.

```
instructions   text placed after the agent's own instructions
provider       one provider id
model          the ceiling
tools          the tool set: caller declarations and built-in names; absent, every built-in
skills         source locations
mcp_servers    MCP server declarations
approvals      a handler, or a static policy
tool_error_policy  "continue" (default) for recoverable tool errors, or "stop"
tool_result_max_bytes  cap on one tool result, 131072 bytes by default
working_directory  the directory the agent works in; absent, the process's current directory
additional_directories  other directories the agent may work in
environment    per-agent variables: provider credentials and processes the agent starts
sessions_directory  where sessions are kept; absent, computed from the working directory
```

That list is closed. Five things are refused at construction, by name, with a remedy:

```
an unregistered field
a field this agent will not honor
two tools with the same name
a name that is not a built-in
a caller declaration without a handler
```

Anything settable outside code resolves first, and `AgentOptions` wins wherever both
speak. See [configuration](../configuration.md).

For what `provider` and `model` mean together, see [models](models.md). For `tools` and
`mcp_servers`, see [tools](tools.md). For `skills`, see [skills](skills.md). For
`approvals`, see [approvals](approvals.md). For `tool_result_max_bytes`, see
[resolutions](tools.md#exactly-one-resolution); for `tool_error_policy`,
[tool error recovery](tools.md#recovering-within-a-turn).

## Working directory

`working_directory` gives each agent its own directory, so one process can run agents
for different projects at the same time. Absent, it is the process's current directory
when the agent is built. A relative value resolves against that same directory.

```
read_file, write_file, edit_file, glob, grep   relative paths resolve against it
bash                                           commands run in it
stdio MCP servers                              start in it
skills                                         relative source locations resolve against it
delegate                                       child work inherits it
Context Intelligence                           include and exclude patterns match it
sessions_directory                             computed from it when not set
```

It is resolved once, to an absolute path with symlinks resolved, and must be an existing
directory, or construction fails `invalid_input`. The agent never changes the process's
current directory, and a later `chdir` does not move an existing agent.

`additional_directories` lists other directories the agent may work in, such as a
shared output folder. Entries resolve like `working_directory`, with relative entries
resolved against it, and must be existing directories. `write_file` and `edit_file`
write only inside the working directory and these directories. Delegated work inherits
them.

The agent keeps its work in these directories. Every default, relative path, and
process it starts points there. Work goes outside them only when the model makes a tool
call that names a location outside them because the task needs it. Caller-supplied
tools run in your process, in whatever directory you choose. The agent's own state, including
transcripts and observation captures, lives in the
[sessions directory](../configuration.md#sessions-directory), which is outside the
working directory unless you point it there.

## Environment

Construction copies the process's environment. `environment` adds or replaces
variables in that copy, per agent, so agents in one process can carry different tokens,
`PATH` entries, or project settings without touching `os.environ` or `process.env`.

```
provider connection       reads its credentials and endpoints from it
bash and skill commands   run with it
stdio MCP servers         start with it; a server's own env applies last
delegate                  child work inherits it
```

So one process can serve users with different keys:

```python
AgentOptions(provider="anthropic", environment={"ANTHROPIC_API_KEY": user_key})
```

Variables set here also reach `bash` and MCP servers, as process variables do. Logins
stored in files, such as `openai-chatgpt` OAuth and an existing Copilot SDK login, stay
shared by the process. Names must be non-empty and contain no `=`, and values must be
strings, or construction fails `invalid_input`. `AMPLIFIER_AGENT_*` settings still come
from the process; see [configuration](../configuration.md).

## Lifetime

```
agent.create_session(options?)   -> Session
agent.resume_session(id)         -> Session
agent.list_sessions()            -> [SessionRecord]
agent.delete_session(id)
agent.close()
```

`close()` is idempotent. Closing while a turn is running requests cancellation and
drains every paired event before it returns. Other calls on a closed agent fail `closed`.

Each agent keeps its own configuration, credentials, tools, and callbacks. Nothing
passes between them through process-global state. Agents intentionally using the same
sessions directory can discover and resume each other's durable sessions.

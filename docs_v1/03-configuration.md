# Configuration

`AgentConfig` is the only input to `create_agent`. Everything about a running
agent is declared here, as plain data.

```python
@dataclass(frozen=True)
class AgentConfig:
    provider: ProviderConfig
    workspace: str | None = None
    cwd: str | None = None
    instructions: str | Instructions | None = None
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    skills: SkillsConfig = field(default_factory=SkillsConfig)
    approvals: ApprovalHandler | None = None
    mcp_servers: list[McpServerConfig] = field(default_factory=list)
    storage: StorageConfig | None = None
    context_intelligence: ContextIntelligenceConfig | None = None
```

Constructing an `AgentConfig` performs no I/O. It contacts no provider, reads no
credential store, and opens no connection. Validation and credential resolution
happen inside `create_agent`, which raises an `AgentError` with a `config/*` code
when something does not check out. A bad provider name fails where you called
`create_agent`, not where you built the config.

This page owns the shape of every field. Four topics are large enough to own
their own pages, and this page defers to them rather than repeating them:

- Choosing a provider and resolving its credentials is
  [Providers](06-providers/index.md).
- Recording and uploading sessions is
  [Context Intelligence](04-context-intelligence.md).
- Where session state lands on disk is [Storage](05-interface/storage.md).
- What a `HostTool` handler receives and returns is
  [Tools](05-interface/tools.md).

## provider

The model behind the agent.

```python
@dataclass(frozen=True)
class ProviderConfig:
    name: str
    model: str | None = None
    model_roles: Mapping[str, str] | None = None
    credentials: Mapping[str, str] | None = None
    options: Mapping[str, object] | None = None
    max_retries: int = 3
```

- **`name`** selects the provider. `create_agent` raises
  `config/unknown_provider` if it is not one the agent knows.
- **`model`** names the model. Omit it to take the provider's default.
- **`model_roles`** maps role names to models, for the roles the agent uses
  internally. Pointing the `fast` role at a cheaper model sends the agent's
  small internal work there while the main work stays on `model`.
- **`credentials`** supplies credentials directly. Omit it and the agent
  resolves them from the environment and its credential store.
- **`options`** passes provider-specific settings straight through without
  interpretation.
- **`max_retries`** bounds how many times the agent retries a transient provider
  failure before surfacing it. Set it to `0` when your own caller already
  retries. See [Providers](05-interface/providers.md#retries).

## workspace and cwd

```python
AgentConfig(
    provider=ProviderConfig(name="anthropic"),
    cwd="/srv/checkouts/api",
    workspace="api",
)
```

**`cwd`** is the directory the agent operates in. Its file tools resolve relative
paths against it and its shell tool starts there. When omitted, it is the process
working directory at the moment you call `create_agent`.

**`workspace`** partitions session listings and storage. Two agents on the same
workspace see each other's sessions in `list_sessions`; two agents on different
workspaces do not. When omitted, it is derived from `cwd`, so separate checkouts
get separate session histories without you asking for it.

Both can be overridden for a single session through `create_session`.

## instructions

```python
AgentConfig(
    provider=ProviderConfig(name="anthropic"),
    instructions="Prefer small commits. Never edit files under vendor/.",
)
```

A bare string is added after the agent's own instructions. Your guidance sits on
top of the agent's, which continues to govern tool use and output conventions.

To supply the entire instruction set instead:

```python
from amplifier_agent import Instructions

AgentConfig(
    provider=ProviderConfig(name="anthropic"),
    instructions=Instructions(text=my_full_prompt, mode="replace"),
)
```

Under `replace` you own instruction quality completely. The guidance the agent
relies on is gone, and behavior that depended on it changes. Tool declarations
survive, because describing available tools to the model is protocol rather than
instruction.

`create_session` takes its own `instructions` that override the agent-level value
for one session.

## tools

```python
@dataclass(frozen=True)
class ToolsConfig:
    host_tools: list[HostTool] = field(default_factory=list)
    allow: list[str] | None = None
    deny: list[str] = field(default_factory=list)
```

- **`host_tools`** are your own functions, exposed to the model alongside the
  built-ins.
- **`allow`** restricts the tool set to exactly these names. `None` leaves the
  default set intact.
- **`deny`** removes names regardless of `allow` or the default set.

Deny wins over allow. A name in both is denied.

```python
ToolsConfig(deny=["shell"])                    # everything but the shell
ToolsConfig(allow=["read_file", "grep"])       # read-only
```

Denied tools are never described to the model, so it does not attempt them and
does not narrate working around them.

## skills

```python
@dataclass(frozen=True)
class SkillsConfig:
    sources: list[str] = field(default_factory=list)
    show_catalog: bool = False
    max_catalog_entries: int = 50
```

- **`sources`** are additional places to find skills. Each entry is a git URL, a
  local directory, or a bundle reference. Sources extend the built-in set rather
  than replacing it.
- **`show_catalog`** puts every discovered skill's name and description into the
  model's context so it can choose skills on its own. Off by default, which means
  skills are invoked explicitly.
- **`max_catalog_entries`** caps that catalog.

```python
SkillsConfig(sources=["git+https://github.com/my-org/team-skills@main#subdirectory=skills"])
```

See [Skills](05-interface/skills.md).

## approvals

A callable that gates tool calls before they run.

```python
approvals: Callable[[ApprovalRequest], Awaitable[ApprovalResponse]] | None
```

Leaving it `None` denies every call that would need approval. There is no mode in
which an unattended agent silently gets more permission than an attended one.
[Approvals](05-interface/approvals.md) covers the request and response types and
the three actions.

## mcp_servers

```python
@dataclass(frozen=True)
class McpServerConfig:
    name: str
    transport: Literal["stdio", "http"]
    command: str | None = None
    args: list[str] | None = None
    env: Mapping[str, str] | None = None
    url: str | None = None
    headers: Mapping[str, str] | None = None
```

Each entry is one MCP server the agent connects to. `command`, `args`, and `env`
configure a `stdio` transport; `url` and `headers` configure an `http` one.

```python
McpServerConfig(
    name="github",
    transport="stdio",
    command="npx",
    args=["-y", "@modelcontextprotocol/server-github"],
    env={"GITHUB_TOKEN": token},
)
```

Tools from these servers join the same flat set the model sees. They are subject
to `allow` and `deny` like any other.

## storage

```python
@dataclass(frozen=True)
class StorageConfig:
    root: Path | None = None
    persist: bool = True
```

- **`root`** is the directory session state is written under. `None` uses the
  default location for the current user.
- **`persist`** controls whether sessions outlive the agent. With `persist=False`
  nothing is written to disk, and `resume_session` finds nothing that is not
  still live in memory.

## context_intelligence

```python
@dataclass(frozen=True)
class ContextIntelligenceConfig:
    destinations: Mapping[str, Destination] = field(default_factory=dict)
```

Recording is on whenever storage is. `destinations` names servers those records
are also forwarded to, keyed by a name that identifies each one in logs. With no
destinations, recording stays local. To turn recording off entirely, set
`StorageConfig(persist=False)`.

See [Context Intelligence](04-context-intelligence.md).

## Errors from create_agent

- **`config/invalid`** the configuration failed validation.
- **`config/unknown_provider`** the named provider is not available.
- **`config/unknown_model`** the named model is not available for that provider.
- **`config/missing_credentials`** no credential could be resolved.

[Errors](05-interface/errors.md) has the full registry and the rule for which
failures raise and which arrive as events.

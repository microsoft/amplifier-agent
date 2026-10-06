# Python reference

Signatures. What each one means lives in [concepts](../concepts/).

## Module

```python
amplifier_agent.contract_version: str   # "agent-interface/1"
amplifier_agent.contract_versions: tuple[str, ...]
amplifier_agent.__version__: str        # the package version

async def create_agent(options: AgentOptions) -> Agent
async def list_providers(options: DiscoveryOptions | None = None) -> list[ProviderRecord]
async def list_models(provider: str, options: DiscoveryOptions | None = None) -> list[ModelRecord]
```

`contract_versions` is immutable and contains `agent-interface/1`, `turn-events/1`,
`language-binding/1`, and `host-config/1`. Reading either version value creates no agent.
`list_providers` and `list_models` need no agent; see
[discovering providers and models](../concepts/models.md#discovering-providers-and-models).

## Agent

```python
class Agent:
    async def create_session(self, options: SessionOptions | None = None) -> Session
    async def resume_session(self, session_id: str) -> Session
    async def list_sessions(self) -> list[SessionRecord]
    async def delete_session(self, session_id: str) -> None
    async def close(self) -> None
    async def __aenter__(self) -> Agent
    async def __aexit__(self, *exc) -> None
```

[agents](../concepts/agents.md)

## Session

```python
class Session:
    info: SessionRecord
    history: list[TurnRecord]

    async def run(self, input: TurnInput) -> TurnResult
    async def start_turn(self, input: TurnInput) -> Turn
    async def fork(self) -> Session
    async def close(self) -> None
    async def __aenter__(self) -> Session
    async def __aexit__(self, *exc) -> None
```

[sessions](../concepts/sessions.md)

`info` and `history` are read-only observations. `history` is a snapshot of completed
turns; read it again after a turn reaches `terminal` to get the updated conversation.
Closing the session makes both properties unavailable with `closed`.

## Turn

```python
class Turn:
    info: TurnInfo

    def events(self) -> AsyncIterator[Event]
    async def cancel(self) -> None
```

[turns](../concepts/turns.md)

## Options

```python
@dataclass
class AgentOptions:
    provider: str | None = None
    model: str | None = None
    instructions: str | None = None
    tools: list[Tool | str] | None = None
    skills: list[str] | None = None
    mcp_servers: list[McpServer] | None = None
    approvals: ApprovalHandler | Literal["allow", "deny"] | None = None
    tool_error_policy: Literal["stop", "continue"] = "continue"
    tool_result_max_bytes: int | None = 131072
    working_directory: str | Path | None = None
    additional_directories: list[str | Path] | None = None
    sessions_directory: str | Path | None = None
    environment: dict[str, str] | None = None
    reasoning_effort: str | None = None


@dataclass
class SessionOptions:
    session_id: str | None = None
    persistence: Literal["durable", "ephemeral"] = "durable"
    model: str | None = None
    reasoning_effort: str | None = None


@dataclass
class DiscoveryOptions:
    environment: dict[str, str] | None = None
```

[agents](../concepts/agents.md), [models](../concepts/models.md)

Omitted provider, model, reasoning_effort, sessions_directory, and approvals values
resolve through [configuration](../configuration.md). `tool_result_max_bytes=None`
removes the cap.
`working_directory=None` uses the process's current directory; see
[working directory](../concepts/agents.md#working-directory). Options are snapshotted
at construction. Changing the original options does not reconfigure an existing agent.

## Records

```python
@dataclass
class TextPart:
    text: str
    type: Literal["text"] = "text"


@dataclass
class ImagePart:
    media_type: str  # image/png, image/jpeg, image/gif, image/webp
    data: str  # standard base64
    type: Literal["image"] = "image"


ContentPart = TextPart | ImagePart


@dataclass
class ConversationMessage:
    role: Literal["system", "developer", "user", "assistant"]
    content: list[ContentPart]


@dataclass
class TurnInput:
    content: list[ContentPart]
    model: str | None = None
    history: list[ConversationMessage] | None = None
    reasoning_effort: str | None = None


@dataclass
class TurnResult:
    state: Literal["success", "failure", "rejected", "cancelled"]
    content: list[TextPart] | None = None
    error: AgentError | None = None
    usage: Usage | None = None


@dataclass(frozen=True)
class SessionRecord:
    session_id: str
    persistence: Literal["durable", "ephemeral"]


@dataclass(frozen=True)
class TurnInfo:
    session_id: str
    turn_id: str


@dataclass
class TurnRecord:
    turn_id: str
    input: TurnInput
    result: TurnResult


@dataclass(frozen=True)
class ProviderRecord:
    provider: str
    display_name: str
    installed: bool
    credentials: Literal["found", "missing", "not_required"]
    credential_variables: list[str]


@dataclass(frozen=True)
class ModelRecord:
    id: str
    display_name: str
    context_window: int | None = None
    max_output_tokens: int | None = None
    reasoning_efforts: list[str] | None = None
```

[turns](../concepts/turns.md), [models](../concepts/models.md#discovering-providers-and-models)

## Events

```python
@dataclass
class Event:
    contract_version: str  # "turn-events/1"
    session_id: str
    turn_id: str
    sequence: int
    type: str
    payload: object
    at: datetime | None = None
```

`payload` by `type`:

```
turn_started        TurnStarted        continuation, primary_actual, reasoning_effort
output_delta        OutputDelta        content
reasoning_delta     ReasoningDelta     text
reasoning_final     ReasoningFinal     text
tool_call           ToolCallEvent      call
tool_result         ToolResultEvent    resolution
approval_request    ApprovalRequestEvent request
approval_decision   ApprovalDecision   resolution
progress            Progress           data
usage               UsageEvent         snapshot
terminal            TurnResult         state, content, error, usage
```

Owned extension types arrive as `Event` with the extension name in `type` and the raw
payload preserved.

`TurnStarted.primary_actual` is a `Selection` with `provider` and `model` fields.
`TurnStarted.reasoning_effort` is a `str | None`, `None` when no reasoning effort was sent.
`ApprovalDecision.resolution` is an `ApprovalResolution` with `request_id`, `decision`,
and optional `reason`. `ApprovalRequestEvent.request` is an `ApprovalRequest`.
Decision values and the compaction payload are in
[events](../concepts/events.md#the-eleven-types) and [approvals](../concepts/approvals.md).

[events](../concepts/events.md)

## Tools

```python
@dataclass(frozen=True)
class ToolContext:
    call_id: str
    deadline: datetime | None = None


ToolHandler = Callable[[dict, ToolContext], Awaitable[str | list[TextPart | ImagePart]]]

BUILTIN_TOOLS: tuple[str, ...]  # the nine built-in tool names


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: ToolHandler
    safety: dict | None = None


@dataclass
class ToolCall:
    call_id: str
    name: str
    source: Literal["built-in", "caller", "mcp"]
    arguments: dict
    deadline: datetime | None = None


@dataclass
class ToolResolution:
    call_id: str
    outcome: Literal["completed", "failed", "cancelled", "unknown"]
    content: str | None = None
    error: AgentError | None = None
    truncated: bool = False
    original_bytes: int | None = None


class ToolFailed(Exception): ...


class ToolOutcomeUnknown(Exception): ...
```

[tools](../concepts/tools.md)

## MCP servers

```python
@dataclass
class McpServer:
    name: str
    transport: Literal["stdio", "http"]
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    url: str | None = None
    headers: dict[str, str] | None = None
```

For `stdio`, supply `command` and optional `args` and `env`. For `http`, supply `url`
and optional `headers`. Fields for the other transport are refused. See
[MCP servers](../concepts/tools.md#mcp-servers).

## Approvals

```python
ApprovalHandler = Callable[[ApprovalRequest], Awaitable[ApprovalResponse]]


@dataclass
class ApprovalRequest:
    request_id: str
    summary: str
    call_id: str | None = None
    name: str | None = None


@dataclass
class ApprovalResponse:
    decision: Literal["allow", "deny", "cancel"]
    reason: str | None = None
```

[approvals](../concepts/approvals.md)

## Usage

```python
@dataclass
class UsageEntry:
    provider: str
    model: str
    tokens_in: int | None = None
    tokens_out: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    cost: dict[str, Decimal] | None = None


@dataclass
class Usage:
    entries: list[UsageEntry]
```

[usage](../concepts/usage.md)

## Errors

```python
class AgentError(Exception):
    code: str
    category: str
    message: str
    remedy: str
    retryable: bool
    correlation_id: str | None = None
    details: dict | None = None
```

[errors](../concepts/errors.md)

## Logging

The library and the modules it loads log through the standard `logging` module under
their package names: `amplifier_agent`, `amplifier_agent_engine`, `amplifier_core`,
`amplifier_foundation`, and `amplifier_module_<name>` (for example
`amplifier_module_provider_anthropic`). The library configures no handlers. If your
process configures none either, Python prints records at `WARNING` and above to stderr,
so a failure can appear there in raw provider form alongside the typed error on the
result.

To silence them:

```python
import logging
import pkgutil

for module in pkgutil.iter_modules():
    if module.name.startswith("amplifier_"):
        logging.getLogger(module.name).setLevel(logging.CRITICAL + 1)
```

Silencing logs does not change results, events, or errors.

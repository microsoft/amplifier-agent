# Python reference

Signatures. What each one means lives in [concepts](../concepts/).

## Module

```python
amplifier_agent.contract_version: str   # "agent-interface/1"
amplifier_agent.__version__: str        # the package version

async def create_agent(options: AgentOptions) -> Agent
```

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
    provider: str
    model: str
    instructions: str | None = None
    tools: list[Tool] | None = None
    skills: list[str] | None = None
    mcp_servers: list[McpServer] | None = None
    storage: str | Path | None = None
    approvals: ApprovalHandler | Literal["allow", "deny"] | None = None

@dataclass
class SessionOptions:
    session_id: str | None = None
    persistence: Literal["durable", "ephemeral"] = "durable"
    model: str | None = None
```

[agents](../concepts/agents.md), [models](../concepts/models.md)

## Records

```python
@dataclass
class TextPart:
    text: str
    type: Literal["text"] = "text"

ContentPart = TextPart

@dataclass
class TurnInput:
    content: list[ContentPart]
    model: str | None = None

@dataclass
class TurnResult:
    state: Literal["success", "failure", "rejected", "cancelled"]
    content: list[ContentPart] | None = None
    error: AgentError | None = None
    usage: Usage | None = None

@dataclass
class SessionRecord:
    session_id: str
    persistence: Literal["durable", "ephemeral"]

@dataclass
class TurnInfo:
    session_id: str
    turn_id: str

@dataclass
class TurnRecord:
    turn_id: str
    input: TurnInput
    result: TurnResult
```

[turns](../concepts/turns.md)

## Events

```python
@dataclass
class Event:
    contract_version: str        # "turn-events/1"
    session_id: str
    turn_id: str
    sequence: int
    type: str
    payload: object
    at: datetime | None = None
```

`payload` by `type`:

```
turn_started        TurnStarted        continuation, primary_actual
output_delta        OutputDelta        content
reasoning_delta     ReasoningDelta     text
reasoning_final     ReasoningFinal     text
tool_call           ToolCallEvent      call
tool_result         ToolResultEvent    resolution
approval_request    ApprovalRequest    request
approval_decision   ApprovalDecision   resolution
progress            Progress           data
usage               UsageEvent         snapshot
terminal            TurnResult         state, content, error, usage
```

Owned extension types arrive as `Event` with the extension name in `type` and the raw
payload preserved.

[events](../concepts/events.md)

## Tools

```python
ToolHandler = Callable[[dict], Awaitable[str]]

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

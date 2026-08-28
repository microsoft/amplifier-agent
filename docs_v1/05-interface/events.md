# Events

The event stream is the only way to observe a turn while it runs. `stream` yields
these for one turn; `run` consumes the same sequence internally and returns only
the final result.

Every event carries `session_id`, `turn_id`, and a `type` string fixed to its row
in the registry. Every `TurnStarted` carries `type == "turn/started"`, and so on.

## The registry

```
turn/started         TurnStarted        prompt
thinking/delta       ThinkingDelta      text
thinking/final       ThinkingFinal      text
message/delta        MessageDelta       text
message/final        MessageFinal       text
tool/call            ToolCall           tool_call_id, name, arguments, source
tool/result          ToolResultEvent    tool_call_id, name, result, duration_ms
approval/requested   ApprovalRequested  approval_id, tool_name, timeout_ms
approval/resolved    ApprovalResolved   approval_id, action
usage                UsageEvent         usage
error                ErrorEvent         error, recoverable
turn/completed       TurnCompleted      result
```

## Types

```python
@dataclass(frozen=True)
class Event:
    session_id: str
    turn_id: str
    type: str


@dataclass(frozen=True)
class TurnStarted(Event):
    prompt: str


@dataclass(frozen=True)
class ThinkingDelta(Event):
    text: str


@dataclass(frozen=True)
class ThinkingFinal(Event):
    text: str


@dataclass(frozen=True)
class MessageDelta(Event):
    text: str


@dataclass(frozen=True)
class MessageFinal(Event):
    text: str


@dataclass(frozen=True)
class ToolCall(Event):
    tool_call_id: str
    name: str
    arguments: Mapping[str, object]
    source: Literal["builtin", "host", "mcp"]


@dataclass(frozen=True)
class ToolResultEvent(Event):
    tool_call_id: str
    name: str
    result: ToolResult
    duration_ms: int


@dataclass(frozen=True)
class ApprovalRequested(Event):
    approval_id: str
    tool_name: str
    timeout_ms: int


@dataclass(frozen=True)
class ApprovalResolved(Event):
    approval_id: str
    action: Literal["allow", "deny", "cancel"]


@dataclass(frozen=True)
class UsageEvent(Event):
    usage: Usage


@dataclass(frozen=True)
class ErrorEvent(Event):
    error: AgentError
    recoverable: bool


@dataclass(frozen=True)
class TurnCompleted(Event):
    result: TurnResult
```

## Ordering

These hold for every turn, and code that renders the stream can rely on them.

- **`turn/started` is first, exactly once.**
- **`turn/completed` is last, exactly once**, including when the turn fails or is
  cancelled. A turn always terminates with it.
- **Every `tool/call` is followed by exactly one `tool/result`** carrying the same
  `tool_call_id`, unless the turn ends first.
- **`message/final` carries the complete text** of the `message/delta` run before
  it. Render deltas and discard the final, or ignore deltas and render only
  finals. Doing both duplicates the text.
- **The agent emits only these types.** Wrappers translate them; they do not
  invent new ones.

## Deltas and finals

Streaming text arrives twice, once incrementally and once whole. Which you use
depends on what you are building.

```python
# A live interface: render as it arrives
case MessageDelta(text=text):
    print(text, end="", flush=True)

# A log or a transcript: take the complete text once
case MessageFinal(text=text):
    transcript.append(text)
```

`thinking/delta` and `thinking/final` work the same way for the agent's
reasoning.

## Recoverable and unrecoverable errors

`ErrorEvent.recoverable` says whether the turn continues past the event.

```python
case ErrorEvent(error=err, recoverable=True):
    log.warning("recovered: %s", err.code)   # turn continues
case ErrorEvent(error=err, recoverable=False):
    show_failure(err)                        # turn/completed follows
```

A denied tool call is recoverable: that one call fails, the model sees the
failure, and the turn goes on. An unrecoverable error is followed by
`turn/completed` with `stop_reason="error"`.

Surface unrecoverable errors. Recoverable ones are usually noise in a user
interface and detail in a log.

Note that `recoverable` and `AgentError.retryable` answer different questions.
`recoverable` is about this turn continuing. `retryable` is about whether making
the same request again might work. See [Errors](errors.md).

## A typical turn

One valid ordering for a turn that calls a single tool. It is not the only one.

```
turn/started
message/delta ...
tool/call
tool/result
message/delta ...
message/final
usage
turn/completed
```

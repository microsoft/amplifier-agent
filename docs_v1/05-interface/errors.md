# Errors

Every failure is an `AgentError` carrying a code from a closed set.

```python
@dataclass(frozen=True)
class AgentError(Exception):
    code: ErrorCode
    message: str
    retryable: bool
    details: Mapping[str, object] | None = None
```

`ErrorCode` is a string enum with one member per row of the registry below. It
compares equal to its own string, so `err.code == "session/busy"` works without
importing the enum.

Codes take the form `group/name`, except `internal`, which is bare. The group is
part of the interface, so branching on the prefix is supported.

## Registry

```
config/invalid                 no    the configuration failed validation
config/unknown_provider        no    the named provider is not registered
config/unknown_model           no    the model is not available for that provider
config/missing_credentials     no    no credential could be resolved

session/not_found              no    the session id does not exist
session/busy                   yes   a turn is already running on the session
session/closed                 no    the session or its agent has been closed

turn/cancelled                 no    cancelled by the host or by an approval
turn/max_iterations            no    the turn hit the iteration limit
turn/context_overflow          no    the conversation exceeded the context window

provider/error                 no    the provider rejected the request
provider/unavailable           yes   the provider could not be reached
provider/rate_limited          yes   the provider rate-limited the request

tool/failed                    no    a tool handler raised an unhandled exception
tool/denied                    no    an approval handler denied the call
tool/approval_timeout          no    the approval handler did not respond in time

storage/unavailable            yes   session storage could not be read or written

internal                       no    an unexpected failure inside the agent
```

The middle column is `retryable`.

The six groups above, plus the bare `internal` code, are the whole set. A failure that fits none of the listed codes
is reported as `internal` with context in `details`, rather than under a new
group.

## Raised or emitted

Where an error appears depends on when it happened, and it determines where your
handling goes.

- **Before a turn starts**, it is raised. From `run` directly, or from the first
  `__anext__` of `stream`.
- **During a turn**, it is emitted as an `error` event and carried on the
  `TurnResult` of the terminal `turn/completed` event.

`run` re-raises a carried error only when `stop_reason` is `error` and `reply` is
`None`. A turn that hit a problem but still produced a reply returns normally,
with the failure on `TurnResult.error`.

```python
try:
    result = await session.run(prompt)
except AgentError as err:
    # the turn never started, or it ended in failure with no reply
    ...
else:
    if result.error is not None:
        # the turn produced a reply but something went wrong along the way
        ...
```

`tool/denied` and `tool/approval_timeout` are always emitted and never raised.
The turn continues and the model sees a `ToolResult(is_error=True)`.

## retryable and recoverable

Two different questions, and mixing them up produces either a retry loop that
never succeeds or a failure surfaced for something the agent already handled.

- **`AgentError.retryable`** asks whether making the same request again might
  work.
- **`ErrorEvent.recoverable`** asks whether this turn continues past the event.

A denied tool call is recoverable, because the turn goes on, and not retryable,
because the same call would be denied again.

## Retrying

Four codes are retryable: `session/busy`, `provider/unavailable`,
`provider/rate_limited`, and `storage/unavailable`. All four describe a condition
that can clear on its own, so back off and try again rather than surfacing them
as terminal.

Two of them have already been retried by the time you see them.
`provider/unavailable` and `provider/rate_limited` are raised only after the
agent exhausted its own backoff, bounded by
[`ProviderConfig.max_retries`](providers.md#retries). They are still retryable,
but they mean the fast retries are spent, so wait longer than you otherwise would
and treat a repeat as an outage rather than noise.

```python
for attempt in range(3):
    try:
        return await session.run(prompt)
    except AgentError as err:
        if not err.retryable:
            raise
        await asyncio.sleep(2 ** attempt)
raise
```

Everything else is not retryable. Repeating the same request unchanged will fail
the same way, so change the request, the configuration, or the state first.

## details

`details` carries structured context specific to the code. For
`config/missing_credentials` it names the unresolved fields and the environment
variable that would satisfy each, which is usually the whole answer.

A resolved credential never appears in `details`, in a message, or in any event.

To find the surrounding activity for an error that happened during a turn, use
its `turn_id`, which every event and every `TurnResult` carries. See the
[event log](../04-context-intelligence.md).

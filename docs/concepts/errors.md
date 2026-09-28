# Errors

One lossless record per failure, in your language's native error idiom.

```
{ code, category, message, remedy, retryable, correlation_id?, details? }
```

`remedy` is always present and always something a person can act on. `retryable: true`
means the same request, unchanged, may succeed.

Flattening one of these to a string throws away the remedy, which is the only part you
can do anything with.

## Categories

```
lifecycle   selection   session   turn   input
executor    approval    provider  internal
```

## Codes

```
closed                     selector_rejected          session_id_invalid
already_exists             not_found                  session_in_use
busy                       stream_already_consumed    turn_cancelled
invalid_input              tool_callback_failed       tool_result_invalid
tool_failed                tool_completion_unknown    approval_denied
tool_recovery_blocked
approval_cancelled         approval_timeout           approval_unavailable
approval_invalid           provider_failed            internal_failed
contract_version_mismatch  engine_unavailable         context_exceeded
```

This set is closed and grows only by addition. Extensions use owned reverse-domain keys.
An unregistered unqualified code is refused by name.

## Where a failure shows up

```
before the stream exists   at the method that failed
recoverable during a turn  in tool_result or progress
unrecoverable in a turn    in terminal
```

A failure never arrives as an untyped exception thrown out of the stream.

`tool_failed` means the executor reported failure. `tool_completion_unknown` means an
effect may have happened without an authoritative result, such as an MCP connection
closing after dispatch. Inspect the external effect before deciding whether to retry.
The agent does not retry it for you.

These tool errors are terminal by default; see
[tool error recovery](tools.md#recovering-within-a-turn), including `tool_recovery_blocked`.

`context_exceeded` means the conversation no longer fits the model's context window
even after [compaction](events.md#the-eleven-types); start a new session.

Recoverable trouble surfaces through `progress` or `tool_result` and the turn keeps
going. Only the unrecoverable kind rides `terminal`.

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
approval_cancelled         approval_timeout           approval_unavailable
approval_invalid           provider_failed            internal_failed
contract_version_mismatch  engine_unavailable
```

This set is closed and grows only by addition. Extensions use owned reverse-domain keys.
An unregistered unqualified code is refused by name.

## Where a failure shows up

```
before the stream exists   at the method that failed
after the stream exists    in terminal
```

A failure never arrives as an untyped exception thrown out of the stream.

Recoverable trouble surfaces through `progress` or `tool_result` and the turn keeps
going. Only the unrecoverable kind rides `terminal`.

## Failing loudly

Every failure names what happened and what to do about it. Nothing degrades quietly,
nothing partial is returned as if it were whole, and no plausible stand-in value is
invented to fill a gap.

A run that reports success while accomplishing nothing is the one failure that costs you
your trust in every run before it.

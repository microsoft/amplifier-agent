# Approvals

You keep authority over every consequential effect, before it happens.

```
approvals: handler          every consequential action passes through it first
approvals: "allow"          a static policy decides
approvals: "deny"
approvals absent            there is no channel
```

Nothing is ever inferred. With a handler, the handler decides. Without one, the static
policy decides. With neither, a consequential action fails rather than proceeding on a
guess.

## The request and the answer

```
request     { request_id, call_id?, name?, summary }
resolution  { request_id, decision }

decision    "allow" | "deny" | "cancel"
```

Each request has exactly one correlated answer, and it arrives before the turn ends. A
decision that arrives after an authoritative resolution has no effect.

## The five ways this ends

```
deny             approval_denied         terminal rejected, turn runs to terminal
cancel           approval_cancelled      terminal cancelled
timeout          approval_timeout        terminal failure
no channel       approval_unavailable    terminal failure
malformed reply  approval_invalid        terminal failure
```

None of these is ever read as allow. A handler that raises, times out, or answers with
something unrecognizable stops the effect; it does not wave it through.

## Choosing

A handler is the real thing: you see the request while the turn is running and answer it.
Building one requires a live channel back into your process, which is what embedding a
library buys you.

A static policy is a decision made before the turn started, applied to everything. It is
the right choice when there is nobody to ask, and it is the only choice on the
[HTTP face](../http/limits.md).

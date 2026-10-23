# Approvals

You keep authority over every consequential effect, before it happens.

```
approvals: handler          every consequential action passes through it first
approvals: "allow"          a static policy decides
approvals: "deny"
AMPLIFIER_AGENT_APPROVALS   the host's static policy, when AgentOptions sets none
absent everywhere           there is no channel
```

Nothing is ever inferred. With a handler, the handler decides. Without one, the static
policy decides, from `AgentOptions` or else from the
[`approvals` host setting](../configuration.md). With neither, a consequential action
fails rather than proceeding on a guess.

Built-in, caller, MCP, delegated, and skill-triggered effects use this same authority.
An unclassified action requires approval. Approving a parent task does not approve
every effect its child requests.

Every tool call requests approval, including file reads and tools with `safety`
metadata. Choose a handler or static policy before starting tasks that need tools.
`"allow"` permits every requested tool effect; `"deny"` refuses each one, and the model
receives every refusal and answers without those tools.

## The request and the answer

```
request     { request_id, summary, call_id?, name?, arguments? }
response    { decision, reason? }
resolution  { request_id, decision, reason? }

response decision    "allow" | "deny" | "cancel"
resolution decision  "allow" | "deny" | "cancel" | "timeout" | "unavailable" | "invalid"
```

Each request has exactly one correlated answer, and it arrives before the turn ends. A
decision that arrives after an authoritative resolution has no effect.

The summary includes the tool and its source, whether a shell command runs in the
background, an argument preview, and the working directory for filesystem and shell
effects. It is bounded to 4,096 characters, escapes control characters, and marks
truncated values and recognized credential fields. Redaction uses field names;
unlabelled secrets in commands or text can still appear.

For a tool call, `arguments` is exactly what the tool will receive, the same value as the
correlated `tool_call` event's `call.arguments`, never truncated or redacted. Decide on
`arguments`; show `summary`. Preview truncation and redaction never change executor inputs.
`call_id`, `name`, and `arguments` are absent when the request is not for a tool call.

## When the answer is not allow

```
deny             approval_denied         the tool does not run; the model gets the denial
                                         and the turn continues
cancel           approval_cancelled      terminal cancelled
timeout          approval_timeout        terminal failure
no channel       approval_unavailable    terminal failure
malformed reply  approval_invalid        terminal failure
```

None of these is ever read as allow. A handler that raises, times out, or answers with
something unrecognizable stops the effect; it does not wave it through.

A denied call shows up as its `approval_decision` with decision `deny`, then its
`tool_result` with outcome `cancelled` and error code `approval_denied`. The model
receives that result, with your `reason` in the error message, and continues: it can try
something else or explain what it could not do. Write a `reason` that tells the model what
is allowed instead. `tool_error_policy` does not change this, and a denial under `"stop"`
also continues. To end the turn, answer `cancel`.

A denied [skill hook command](skills.md#command-hooks) has no model call to return to; it
ends the turn `rejected` with `approval_denied`.

Approval replies have a 120-second deadline. An expired or already settled reply cannot
start the tool. Cancellation prevents new effects and waits for executing callbacks
to settle before the turn ends. A handler that never settles can therefore keep close
pending; the agent cannot safely stop arbitrary code in the caller's process.

## Choosing

A handler is the real thing: you see the request while the turn is running and answer it.
Building one requires a live channel back into your process, which is what embedding a
library buys you.

A static policy is a decision made before the turn started, applied to everything. It is
the right choice when there is nobody to ask, and it is the only choice on the
[HTTP face](../http/limits.md#approvals). A host can also set it without code through
[configuration](../configuration.md#environment).

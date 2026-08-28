# Approvals

An approval handler is your veto on a tool call before it runs. It is the one
place where your code decides whether the agent gets to act.

```python
ApprovalHandler = Callable[[ApprovalRequest], Awaitable[ApprovalResponse]]
```

Set it on [`AgentConfig.approvals`](../03-configuration.md#approvals).

## Without a handler, calls are denied

Leaving `approvals=None` denies every call that would need approval. It does not
allow them, and there is no setting that makes an unattended agent more permitted
than an attended one.

This matters for headless deployments. An agent running in CI with no handler
does not quietly gain permission because nobody is watching. If you want it to
proceed, supply a handler that says so, and the decision is recorded in your code
rather than implied by an absence.

## The types

```python
@dataclass(frozen=True)
class ApprovalRequest:
    approval_id: str
    session_id: str
    turn_id: str
    kind: str
    tool_name: str
    arguments: Mapping[str, object]
    timeout_ms: int


@dataclass(frozen=True)
class ApprovalResponse:
    action: Literal["allow", "deny", "cancel"]
    reason: str | None = None
    arguments: Mapping[str, object] | None = None
```

`kind` names what is being approved. `tool_name` and `arguments` are the call
itself. `timeout_ms` is how long the agent waits before treating the request as
unanswered.

## The three actions

**`allow`** lets the call proceed.

```python
return ApprovalResponse(action="allow")
```

You may rewrite the arguments on the way through, which is how you narrow a call
rather than refusing it outright:

```python
if request.tool_name == "write_file":
    path = confine_to_workspace(request.arguments["path"])
    return ApprovalResponse(action="allow", arguments={**request.arguments, "path": path})
```

**`deny`** fails that one call. The turn continues, the model sees a
`ToolResult(is_error=True)`, and a recoverable `tool/denied` error event is
emitted. The agent typically works around the refusal.

```python
return ApprovalResponse(action="deny", reason="writes outside the workspace are not allowed")
```

The `reason` reaches the model. A specific reason produces a better recovery than
a vague one, because the model can use it to choose a different approach.

**`cancel`** ends the whole turn, with `stop_reason="cancelled"`.

```python
return ApprovalResponse(action="cancel", reason="operator stopped the run")
```

Use `deny` to refuse an action. Use `cancel` to stop the agent.

## A worked handler

```python
AUTO_ALLOW = {"read_file", "grep", "list_directory"}


async def approve(request: ApprovalRequest) -> ApprovalResponse:
    if request.tool_name in AUTO_ALLOW:
        return ApprovalResponse(action="allow")

    decision = await ask_operator(request.tool_name, request.arguments)
    if decision == "yes":
        return ApprovalResponse(action="allow")
    if decision == "stop":
        return ApprovalResponse(action="cancel", reason="operator stopped the run")
    return ApprovalResponse(action="deny", reason="operator declined")
```

The handler is awaited inside the turn, so a slow handler is a slow turn. If you
are prompting a person, `timeout_ms` is the bound on how long the agent waits.

## Timeouts

A handler that does not respond within `timeout_ms` produces
`tool/approval_timeout`. Like a denial, it is recoverable: the call fails, the
model sees the failure, and the turn continues. It is never treated as an
approval.

## Approvals and the event stream

Two events accompany every approval, but they are for observation only.

```
approval/requested   approval_id, tool_name, timeout_ms
approval/resolved    approval_id, action
```

The event stream is output. It cannot carry a decision back, which is why
approvals are a callback rather than a message on the stream. If you are
rendering an interface, use the events to show what is being asked and your
handler to answer it, correlating the two by `approval_id`.

## Errors

- **`tool/denied`** the handler denied the call. Recoverable, never raised.
- **`tool/approval_timeout`** the handler did not respond in time. Recoverable,
  never raised.

A `cancel` action ends the turn with `stop_reason="cancelled"` and correlates
with `turn/cancelled`. See [Errors](errors.md).

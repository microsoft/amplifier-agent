# Engine API

## Scope

The importable library API of `amplifier_agent_lib` for embedders: the turn assembly sequence, the
`Engine` lifecycle and its exception types, the two protocol point interfaces and their shipped
implementations, the stream ownership guarantee, and the sub-agent spawn surface. Does not cover
the JSON-RPC method shapes (see `wire-protocol.md`), the stdout envelope (see
`envelope-and-errors.md`), or the CLI flags (see `cli.md`).

Everything named here is public API. Names, signatures, and exception types are the contract.

Two symbols on the assembly path are exceptions, called out where they appear: `make_turn_handler`
lives in a private module, and provider injection lives in the `amplifier_agent_cli` package. Both
are load-bearing for any embedder and are documented here because the API is not usable without
them.

## Assembling a turn

`Engine` does not construct its own turn handler and knows nothing about providers. An embedder
assembles the pieces in this order:

```python
prepared = await load_and_prepare_cached(aaa_version=__version__)

prepared.mount_plan["providers"] = []
inject_provider(prepared, provider_name, extra_config=None)
inject_routing_matrix(prepared, provider_name)

handler = make_turn_handler(prepared, cwd=..., is_resumed=..., workspace=...)
engine = Engine(turn_handler=handler,
                protocol_points={"approval": ..., "display": ...})
await engine.boot(init_params, bundle_override=prepared)
result = await engine.submit_turn({"sessionId": ..., "turnId": ..., "prompt": ...})
await engine.shutdown()
```

```
amplifier_agent_lib.bundle.cache
    async load_and_prepare_cached(aaa_version: str) -> PreparedBundle

amplifier_agent_lib._runtime            (private module; no public alias exists)
    make_turn_handler(prepared: PreparedBundle, *, cwd: str | None, is_resumed: bool,
                      host_config: dict | None = None, workspace: str | None = None,
                      mode: str | None = None) -> TurnHandler

amplifier_agent_cli.provider_sources    (CLI package; no lib equivalent exists)
    enumerate_resolvable_providers() -> list[str]
    inject_provider(prepared, provider_name, model_override=None,
                    effort_override=None, extra_config=None) -> None
    inject_routing_matrix(prepared, provider_name) -> None
    provider_config_from_host(host_config) -> dict | None
```

`cwd` and `is_resumed` are required keyword arguments on `make_turn_handler` and have no defaults.

`inject_provider` is a no-op when `prepared.mount_plan["providers"]` is already non-empty. The
vendored `bundle.md` declares a catalog stub for every provider so cold-prepare can install them,
so the clear is required: without it the injection is silently discarded and the turn runs on the
stub.

`enumerate_resolvable_providers()` reports which providers have credentials that actually resolve,
walking the env-var then `credentials.json` chain. It answers "which providers could run", not
"which provider should run"; the caller picks.

## Turn boundaries

One `Engine` serves one turn. `submit_turn` may be called repeatedly on a booted `Engine` and will
not raise, but each turn builds its own context from the persisted transcript, so a second turn on
the same `Engine` does not see the first. Continuity is a function of `sessionId`, `workspace`, and
`is_resumed`, not of process or object lifetime.

The supported multi-turn shape is a fresh handler and `Engine` per turn, reusing the session id and
workspace, with `is_resumed=True` on every turn after the first.

## Engine

```python
class Engine:
    SERVER_NAME = "amplifier-agent"

    def __init__(self, *, turn_handler: TurnHandler, protocol_points: ProtocolPoints) -> None
    async def boot(self, params: Any, bundle_override: PreparedBundle | None = None) -> InitializeResult
    async def submit_turn(self, params: Any) -> TurnSubmitResult
    async def shutdown(self, _params: Any = None) -> AgentShutdownResult
    async def dispatch(self, method: str, params: Any) -> Any

    session: PreparedBundle | None   # None until boot() completes
```

Both constructor arguments are keyword-only and both are required.

`params` on `boot` and `submit_turn` is duck-typed by attribute or key lookup. `boot` reads
`protocolVersion`, `allowProtocolSkew`, `capabilities`, `sessionId`, and `resume`. `submit_turn`
requires `sessionId`, `turnId`, and `prompt`.

State machine:

```
constructed --boot()--> booted --submit_turn()*--> booted --shutdown()--> shut down

boot()         raises EngineShutdownError if already shut down
               idempotent: a second call returns the cached InitializeResult
submit_turn()  raises EngineNotBootedError if boot() has not run
               raises EngineShutdownError after shutdown()
shutdown()     idempotent, always returns {}, never raises
```

`EngineNotBootedError` and `EngineShutdownError` both subclass `RuntimeError`.

`boot()` performs the protocol version check (see `wire-protocol.md`), resolves the prepared
bundle, negotiates capabilities, and caches the `InitializeResult`.

`boot` reads its params leniently: every key is optional, and the only param that can fail the call
is a `protocolVersion` that is present and does not match. Omitting it skips the check entirely.
`submit_turn` indexes its three keys directly, so `sessionId`, `turnId`, and `prompt` are all
required and a missing one raises `KeyError`.

`bundle_override` supplies a prepared bundle instead of resolving one. It does **not** determine
which bundle serves the turn: the turn runs on the bundle closed over by `make_turn_handler`, so
provider injection takes effect either way. Passing it avoids a second, redundant
`load_and_prepare_cached()` inside `boot()`, which on a cold cache costs real time. Pass the same
`PreparedBundle` you gave `make_turn_handler`.

`dispatch()` accepts `agent/initialize`, `turn/submit`, and `agent/shutdown`, and raises
`ValueError` for any other method name. See `wire-protocol.md` for the divergence between these
names and the published schema set.

A `TurnSubmitResult` carries eight keys:

```python
{
  "reply": str, "turnId": str, "sessionId": str,
  "tokensIn": int, "tokensOut": int,
  "cacheReadTokens": int, "cacheWriteTokens": int,
  "costUsd": Decimal | None,
}
```

The five usage fields are summed from the turn's `usage` display events by the `UsageAccumulator`
that `Engine` wraps around the injected display point. They are the same numbers the CLI reports as
`metadata` on the stdout envelope; there is exactly one place they are summed. An embedder reads
them off the return value and needs no envelope.

`tokensIn` is the CHARGED input: gross input plus cache writes. `cacheReadTokens` is a reported
subset, not an addend. `costUsd` is a `Decimal`, so `json.dumps` on the result requires
`default=str`; it is `None` when the provider reported no cost, which is distinct from zero. The
cache split varies with prompt-cache state, so identical prompts do not report identical numbers.

Contract caveat: the `finalEvent` key is declared as optional on the type and in the published
schema, but nothing ever populates it. Do not wait for it.

### TurnContext and TurnHandler

The embedder supplies the turn handler. It is the only place model invocation happens; `Engine`
itself knows nothing about providers.

```python
@dataclass
class TurnContext:
    session_id: str
    turn_id: str
    prompt: str
    approval: ApprovalSystem
    display: DisplaySystem

TurnHandler = Callable[[TurnContext], Awaitable[str]]
```

`submit_turn` builds the `TurnContext` from its params plus the injected protocol points, awaits
the handler, and wraps the returned string as the `reply` of a `TurnSubmitResult`.

## Protocol points

Two protocol points exist. Both are `@runtime_checkable` Protocols, both are async, and both take a
single structured argument rather than an `event_type + payload` pair.

```python
class DisplayEvent(TypedDict):
    type: str                    # one of the nine display event types
    sessionId: str
    turnId: NotRequired[str]

class DisplaySystem(Protocol):
    async def emit(self, event: DisplayEvent) -> None: ...

ApprovalAction = Literal["accept", "decline", "cancel"]

class ApprovalRequest(TypedDict):
    sessionId: str
    turnId: str
    approvalId: str
    kind: str
    payload: dict[str, Any]
    timeoutMs: int

class ApprovalResponse(TypedDict):
    action: ApprovalAction
    payload: NotRequired[dict[str, Any]]

class ApprovalSystem(Protocol):
    async def request(self, req: ApprovalRequest) -> ApprovalResponse: ...

class ProtocolPoints(TypedDict):
    approval: ApprovalSystem
    display: DisplaySystem
```

An `ApprovalSystem` implementation MUST honor `timeoutMs` and MUST return `{"action": "cancel"}` on
timeout.

Payload keys on a `DisplayEvent` vary by `type`; the TypedDict pins only the fields every event
carries. The per-type field lists are in `wire-protocol.md`.

Streaming is not a separate protocol point: it folds into display as a one-way event stream. Spawn
is not a protocol point either; see Non-goals.

## Shipped protocol point implementations

CLI-facing:

```
CliDisplaySystem(*, stream: TextIO, verbosity: str | DisplayVerbosity = DEFAULT)
    writes "[<type>] <summary>" lines to the injected stream, flushing each.
    QUIET suppresses everything. DEFAULT additionally suppresses thinking/delta,
    thinking/final, and progress. DEBUG appends the full event dict as sorted JSON.
    Accepts the string aliases quiet | normal | default | verbose | debug.

JsonDisplaySystem(*, stream: TextIO)
    one NDJSON line per event: {"method": <event type>, "params": <rest of the event dict>}.
    No filtering and no verbosity dial; the host filters. This backs `--display ndjson`.

CliApprovalSystem(*, mode=None, override=None, is_tty=False, prompt_fn=None)
    resolution order:
      override YES      -> accept
      override NO       -> decline
      not is_tty        -> decline
      prompt_fn is None -> decline
      otherwise         -> prompt; accept on "y" or "yes", else decline
    Deny is the outcome at every fallthrough.
```

HTTP-facing:

```
HttpQueueDisplaySystem(queue: asyncio.Queue[DisplayEvent | None])
    pushes each event onto the queue. emit() never raises, whatever goes wrong.
    close() posts the None sentinel, is idempotent, and makes later emit() calls no-ops.

HttpAutoApprovalSystem(*, log_requests: bool = True)
    always returns {"action": "accept"}, logging each request at INFO. Equivalent to
    the CLI's -y: any bundle tool that asks for approval is auto-approved.
```

Both display implementations write only to the stream or queue they were given. Neither reaches
for a global.

## Stream ownership

`amplifier_agent_lib` never reads stdin and never writes stdout. All library output flows through
the injected `DisplaySystem`. An embedder therefore owns both streams outright and may run its own
protocol on them without interference, and the library behaves identically whether it is embedded
behind a CLI, an HTTP server, or anything else.

## Sub-agent spawn

`amplifier_agent_lib.spawn` is the app-layer policy for the `session.spawn` capability that backs
the `delegate` tool. Public surface:

```python
hydrate_agent_overlay(agent_md_path: Path) -> dict[str, Any]
    parse a vendored agent .md file into an overlay config dict.

merge_configs(...) -> dict[str, Any]
    deep-merge a parent config with an agent overlay, including module list merging by
    module id and allow/deny tool and hook filtering for the child.

spawn_sub_session(**kwargs) -> dict[str, Any]
    create, run, and clean up a child session.
```

Observable guarantees:

- A sub-agent is an in-process child session inside one engine invocation, not a new subprocess.
- A child inherits `parent_id` and receives a fresh session id scoped to the parent process.
- A child inherits the parent's resolved workspace and project slug verbatim and never re-derives
  them from the working directory, so a delegate's state lands in the parent's workspace bucket.
  With no workspace set on the parent, nothing is propagated.

A child's spend is bridged onto the parent session's cost channel after the child completes
successfully, so an embedder's per-turn usage totals include delegated work. A failed delegation's
spend is deliberately not bridged.

Unsupported in this version, and observable as failures or as absent behavior: recursive spawn from
a child (grandchild delegation fails with the delegate tool's own error), display nesting, provider
preference plumbing beyond plain config inclusion, session resume for a child, and capture of a
child's status or turn count.

## Non-goals

- **No adapter-supplied spawn.** Spawn is not a protocol point and no `spawn_fn` parameter exists
  on any public config object. A host-supplied spawn function could resolve the wrong bundle,
  workspace, or agent overlay, and the failure would surface as a sub-agent producing wrong output
  rather than as an error.
- **No second session-factory path for embedders.** `Engine.boot()` is the only supported entry.
  `amplifier_agent_http` reaches the runtime directly through a private path of its own; that is an
  internal arrangement inside this repo, not a second public API, and it is not available to or
  supported for embedders.
- **No host-facing mount plan.** The bundle manifest is sealed; embedders do not compose it.
- **No mid-turn config mutation.** Config is read once at startup.
- **No kernel surface.** Everything here is app layer.
- **No `hostCapabilities` on the engine surface.** Removed entirely.

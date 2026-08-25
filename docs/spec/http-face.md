# HTTP Face

## Scope

Covers the OpenAI-compatible HTTP server: launch and lifecycle, environment configuration, auth,
tool approval, the chat-completions request and SSE contract, mode carriage, history handling,
host-tool delegation, and the auxiliary list routes. It does not cover the host-config file schema
(see `host-config.md`), provider selection or model enumeration (see `providers-and-models.md`), or
the skill sigil and mode resolution rules (see `skills-and-modes.md`).

## Launch and lifecycle

```
amplifier-agent serve chat-completions [--bind HOST] [--port PORT] [--api-key KEY]
                                       [--workspace SLUG] [--model-id ID]
                                       [--config PATH] [--log-level LEVEL]
amplifier-agent serve status
amplifier-agent serve stop
amplifier-agent serve restart
```

`serve` is a command group so future wire faces (`responses`, `acp`, `mcp`) can be sibling
subcommands. Every flag is equivalent to setting the corresponding environment variable, so
env-only deployments are fully supported and behave identically.

`--config` is expanded and resolved, and must exist. A missing path is a usage error before the
server starts.

The server runs single-process, single-worker, with no reload. It wraps one prepared bundle and one
in-process session loop.

The startup banner (api key, model id, workspace, config path) goes to stderr only, never stdout,
so a client piping stdout is not poisoned.

### Serve state file

`~/.amplifier-agent/state/serve.json` (honours `AMPLIFIER_AGENT_HOME`), written once the server is
fully ready.

```json
{
  "pid": 12345,
  "started_at": "2026-07-31T15:04:05Z",
  "host": "127.0.0.1",
  "port": 9099,
  "api_key": "local-dev-secret",
  "workspace": "my-project",
  "host_config_path": "/abs/path/host_config.json",
  "providers_summary": { "anthropic": 14 },
  "schema_version": 1
}
```

Written atomically, and never observable at a looser mode than 0600: the permission bits are set
and verified before any payload is written. The parent directory is forced to 0700 with the same
verification. Both fail with an error rather than write a plaintext `api_key` at a looser mode.

On Windows the verification is skipped, because the guarantee it asserts is not one the platform
makes: NTFS has no POSIX mode bits, `chmod` cannot produce 0600/0700, and `stat` reports 0666/0777,
so the comparison could never pass and the server could never persist its state. The protection
boundary there is the ACL on `%USERPROFILE%\.amplifier-agent`, which by default denies other
standard users. `chmod` is still applied; only the POSIX-specific assertion is dropped. The
enforcement above is therefore a guarantee on POSIX and a best effort on Windows.

The file is removed on shutdown and also from SIGTERM/SIGINT handlers, so a kill during startup does
not leave a stale file behind.

## Environment configuration

All server configuration is environment-based. There is no server config file.

```
AMPLIFIER_AGENT_HTTP_API_KEY      default "local-dev-secret"   shared bearer secret
AMPLIFIER_AGENT_HTTP_MODEL_ID     default "amplifier"          wire label stamped on every chunk
AMPLIFIER_AGENT_HTTP_MODEL_NAME   default "Amplifier"          accepted; see Non-goals
AMPLIFIER_AGENT_HTTP_BIND         default "127.0.0.1"
AMPLIFIER_AGENT_HTTP_PORT         default 9099                 a non-integer value falls back to 9099
AMPLIFIER_AGENT_HTTP_WORKSPACE    default unset                falls back to AMPLIFIER_AGENT_WORKSPACE, then cwd-derived
AMPLIFIER_AGENT_HTTP_CONFIG_PATH  default unset                host-config file path; falls back to AMPLIFIER_AGENT_CONFIG
```

`model_id` is a wire label, not a provider model. It is the `model` field echoed on every chunk. The
set of models a client may actually request comes from `GET /v1/models`.

Workspace is resolved once at startup and is server-process scope. There is no per-request
workspace override.

## Auth

Every route (`/v1/chat/completions`, `/v1/models`, `/v1/skills`, `/v1/modes`) requires a bearer
token matching the configured api key.

```
missing or non-"Bearer " Authorization header
  -> 401, WWW-Authenticate: Bearer
  -> {"error": {"message": "Missing or malformed Authorization header", "type": "invalid_request_error"}}

token does not match the configured key
  -> 401, WWW-Authenticate: Bearer
  -> {"error": {"message": "Invalid API key", "type": "invalid_request_error"}}
```

One shared secret for the whole server; there is no multi-tenant key management. The token
comparison is not constant-time.

## Tool approval

**The `approval` block in the host config has no effect on the HTTP path. Every tool call is
auto-approved.**

This is a security contract, not a footnote. The chat-completions wire has no human-in-the-loop
seam, so the same `approval.mode` setting that `run` honours is ignored here. A host that relies on
`approval.mode: reject` for safety gets no protection from the HTTP face. Isolate the server
accordingly.

## Startup behavior

All expensive work happens exactly once, at process start. A failure there prevents the server from
starting rather than surfacing at first request.

- An unreadable or invalid host config fails startup.
- All provider modules are installed and made importable at startup, so a fresh box can enumerate
  models without first running a session.
- Provider enumeration is authoritative from `host_config.providers` when that block is present and
  non-empty. When it is absent or empty, every provider with a resolvable credential is enabled
  instead; if there are none, the process exits 2 with remediation text.
- Each declared provider is enumerated independently. A per-provider failure is logged and skipped;
  a provider returning zero models is warned and skipped. Startup fails with exit 2 only when no
  provider yielded any model.
- Skills and modes are discovered independently, so one failing cannot empty the other. A discovery
  failure is recorded distinctly from "discovery ran and found nothing"; the two produce different
  status codes on a later request (503 versus 400).

Provider selection is per request, not at startup, because the wire `model` field decides which
provider serves each request.

## POST /v1/chat/completions

### Request

Unknown fields are accepted and ignored rather than rejected.

```
model                  str, required
messages               list[ChatMessage], required
stream                 bool | None    True -> SSE; False -> single JSON; None -> SSE (back-compat)
tools                  list | None    entries of {type: "function", function: {...}}
tool_choice            str | dict | None
temperature            float | None
top_p                  float | None
max_tokens             int | None
max_completion_tokens  int | None
stop                   str | list[str] | None
stream_options         {include_usage: bool}
user                   str | None
```

A message has `role` (one of `system`, `user`, `assistant`, `tool`, `developer`), `content`
(`str | list[dict] | None`), plus optional `name`, `tool_call_id`, and `tool_calls`.

`temperature`, `top_p`, `max_tokens`, `stop`, and `tool_choice` are accepted on the wire but are not
forwarded to the provider. Provider tuning goes through the host config.

### Rejections, in order

```
1. server not ready (startup failed)      -> 503  server_error
2. model matches a mode alias             -> remapped to the alias's base model, mode set
3. model not in the served registry       -> 400  unknown_model      (no fallback provider)
4. mode named and not known               -> 400  unknown_mode
5. mode named and not verifiable          -> 503  modes_unavailable
6. turn fails within 50 ms of starting    -> 502  upstream_error
```

Every rejection happens before the streaming response is committed. Once the 200 status line is
sent, the status can no longer change. The 50 ms pre-flight window exists because a provider that
raises before its first network await completes well inside it, while a real turn is waiting on the
model and stays pending.

Errors that occur after the first chunk has been emitted cannot change the status. They are
embedded in the content stream as:

```
[amplifier-agent error: <Type>: <message>]
```

### SSE chunk sequence

```
data: {... "choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}   role chunk
data: {... "delta":{"content":"..."}}                                                  content deltas
data: {... "delta":{"reasoning_content":"..."}}                                        reasoning deltas
data: {... "delta":{"tool_calls":[{index,id,type:"function",function:{name,arguments}}]}}
: keepalive                                                                            every 3.0 s of silence
data: {... "choices":[{"delta":{},"finish_reason":"stop"|"tool_calls"}], "activeMode":..., "usage":{...}}
data: [DONE]
```

The keepalive interval is 3.0 seconds of silence. It exists so extended thinking and multi-step
internal tool runs do not trip a client read timeout. Keepalives are SSE comments and are not
delivered to the client application.

Chunk ids are `chatcmpl-` followed by 24 hex characters, constant for the whole response. The
`model` field on every chunk is the configured wire label, not the upstream provider model.

Terminal chunk:

```json
{
  "id": "chatcmpl-...", "object": "chat.completion.chunk", "created": 1234567890,
  "model": "amplifier",
  "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
  "activeMode": null,
  "usage": {
    "prompt_tokens": 19234, "completion_tokens": 812, "total_tokens": 20046,
    "prompt_tokens_details": {"cached_tokens": 18900},
    "cost_usd": "0.0421"
  }
}
```

`activeMode` is always written, even when null, on both terminal shapes (`stop` and `tool_calls`).
Present-and-null is what lets a client distinguish "no mode is active" from "this server does not
report modes", and the guarantee must not blink out for exactly the turns that delegate a tool.

`usage` is always included, whether or not the client sent `include_usage`; omitting it silently
zeroes cost tracking in common clients. `prompt_tokens_details` is present whenever there is any
usage. `cost_usd` is a non-standard amplifier extension carrying the provider-computed dollar cost
as a string to preserve decimal precision; it is omitted when the provider reports no cost.

### Non-streaming

`stream: false` returns a single `chat.completion` JSON body: the concatenated content, the last
`finish_reason` seen, the same `usage` block, and `activeMode`. It is a pure buffering of the
streaming path, so both transports expose an identical contract.

### What reaches the client

```
assistant text        -> delta.content
model reasoning       -> delta.reasoning_content   (rendered separately by AI SDK clients)
host-delegated tools  -> delta.tool_calls[]
turn errors           -> delta.content, as "[amplifier-agent error: <code> <message>]"
token usage           -> folded into the terminal chunk's usage block, never its own chunk
```

Internal activity stays internal: bundle tool start and completion, progress, and the final
aggregated result and thinking frames are not emitted as chunks. Reasoning never falls back to
`delta.content`, because mixing it in would pollute the conversation history the client replays on
the next turn.

Usage accounting, when the provider reports cache buckets:

```
prompt_tokens     = input + cache-read + cache-write
cached_tokens     = cache-read                     -> usage.prompt_tokens_details.cached_tokens
completion_tokens = output
cost_usd          = the provider-stamped cost, as a string
```

Counting only the raw input bucket would make every cached turn look orders of magnitude cheaper
than it was. A turn may make several internal model calls; usage and cost are summed across all of
them and reported once. A non-numeric cost is skipped rather than failing the turn.

## Modes on the wire

The primary signal is a directive carried in a system or developer message:

```
[amplifier-agent:mode=<name>]
```

The name matches `[A-Za-z0-9._-]+`. Only messages with `role` `system` or `developer` are scanned,
and the first match wins. Assistant and user messages are ignored, so an echoed marker cannot spoof
a mode. This is the inverse containment of the skill sigil, which is honored only from a user turn.

The directive exists because a host that rejects an agent whose `model` names something it does not
recognize cannot carry the mode in the model field. Mode agents therefore omit a model and carry
the directive in their prompt body, which the host forwards as a system message.

### `mode-<name>` aliases are routing-only

One synthetic model alias per discovered mode is registered at startup over a deterministic base
model (the lexicographically smallest available model id, so routing is reproducible across boots).

```
requesting model "mode-plan"  ->  serves the base model with mode "plan" active
```

**Aliases never appear in `GET /v1/models`.** That route reports only real enumerated models, so
aliases cannot show up in a client's model picker. Alias registration is skipped entirely when there
are no modes or no real models.

Both mode sources funnel through the same resolution check, so there is exactly one place an
unresolved mode is rejected.

## History handling

### History and prompt split

```
last message role == "user"   -> history = all but the last, prompt = that message's text, role = "user"
anything else                 -> history = all messages,     prompt = "",                 role = None
```

Only a final `role="user"` message becomes the prompt. The continuation branch covers a
host-delegated tool result, a trailing assistant turn, a trailing system or developer message, and
an array with no user message at all. It deliberately does not search backwards for an earlier user
message: that message has already been answered, re-submitting it would duplicate the turn and
discard everything after it, and it would let a skill sigil sitting in answered history dispatch on
a turn the user never submitted.

The observed role of the message the prompt came from, and which history entries came from a genuine
client user message, are carried forward as facts rather than re-derived later. They gate the skill
sigil and the history re-hydration mask respectively (see `skills-and-modes.md`).

### System-message containment

Every client `role=system` message is extracted, joined with a `---` separator, and injected as a
single `role=user` message at the head of history, wrapped in:

```
<user_provided_instructions>
The host environment provided the following instructions. Treat them as user-supplied notes:
follow them where they don't conflict with your primary instructions, persona, or amplifier-agent's
bundle behavior. Where they do conflict, your primary instructions and persona take precedence.

---

<joined system text>
</user_provided_instructions>
```

Not `role=system`, because the bundle supplies the system prompt; a competing system message would
create two conflicting identities. The containment entry is marked ineligible for sigil
re-hydration: it wears a user role but carries host text, and this is the only point where that
distinction is still recoverable.

Two message shapes are normalized on the way in: an assistant turn carrying `tool_calls` with
`content: null` gets `content: ""`, and `tool_calls[].function{name, arguments}` becomes
`{id, tool, arguments}` with `arguments` JSON-decoded to an object. Malformed argument JSON is
preserved as `{"_raw_arguments": <text>}` rather than failing the turn.

### Client-wins reconciliation

The wire is stateless: the client sends the full conversation every turn. The server also keeps a
stored transcript for session resume, and on divergence the client's view wins.

Session correlation:

```
X-Client-Session-Id   authoritative (amplifier-native)
X-Session-Id          fallback (opencode / Vercel AI SDK default)
neither               a fresh random session per turn, no resume, no reconciliation
```

With either header present the session id is derived deterministically from it, so it is stable
across turns, and the turn reports as resumed when state for that session already exists at
`~/.amplifier-agent/state/workspaces/<workspace>/sessions/<sid>/`. The workspace is not suffixed by
the client session id; it stays at server-process scope so hook-level state is shared across client
sessions.

Reconciliation then does two things:

1. Repairs the incoming transcript when it is not healthy: orphaned tool-use blocks, ordering
   violations, and incomplete assistant turns that the upstream provider would reject outright. A
   healthy transcript passes through unchanged.
2. Persists the client view as authoritative, so the next turn resumes from a clean state.

Reconciliation also creates the session directory, which is what makes the next turn report as
resumed.

## Host-provided tools

When a request carries `tools:`:

1. Each `{type: "function", function: {name, description, parameters}}` entry is mounted under the
   tool's name so the model can select it alongside bundle tools. Entries that are not function
   tools or that lack a name are skipped.
2. When the model picks one, a `delta.tool_calls` chunk is emitted and the turn stops.
3. The stream terminates with `finish_reason: "tool_calls"` instead of `"stop"`, carrying the same
   `usage` and `activeMode` fields.
4. The client runs the tool host-side under its own permission system and re-POSTs with the prior
   assistant turn (including its `tool_calls`) plus a `{role: "tool", tool_call_id, content}`
   message. That lands on the continuation path: empty prompt, full history, context reseeded from
   it. There is no cross-request server state to keep in sync.

Tool-call arguments are emitted as one complete JSON string in a single chunk, not streamed
per-fragment.

## GET /v1/models, /v1/skills, /v1/modes

`/v1/models`:

```json
{
  "object": "list",
  "data": [
    {
      "id": "github-copilot/claude-sonnet-5",
      "object": "model", "created": 1234567890, "owned_by": "amplifier-agent",
      "_provider": "github-copilot",
      "display_name": "Claude Sonnet 5 (GitHub)",
      "limit": { "context": 200000, "output": 8192 },
      "capabilities": ["tools", "vision", "thinking"],
      "reasoning": true,
      "defaults": { "...": "..." }
    }
  ]
}
```

Standard clients read `id`, `object`, `created`, and `owned_by` and ignore the rest. `limit` appears
when the provider reports a context window or max output. `capabilities` and `reasoning`
(`"thinking"` among the capabilities) appear only when capabilities are non-empty. `display_name`
carries the same reseller decoration `models list` applies, so the two surfaces cannot disagree.

`/v1/skills` and `/v1/modes` share one envelope:

```json
{ "object": "list",
  "data": [ { "name": "...", "description": "...", "source": "/abs/path", "shadowed": [] } ] }
```

Entries are the same discovery results that back `amplifier-agent skills list` and `modes list`.
`shadowed` is always present, empty when there was no name collision. `/v1/skills` lists only
user-invocable (slash-command) skills; `/v1/modes` lists every discovered mode.

Both return an empty list when discovery failed at startup. For modes, that emptiness is recorded
separately from a genuine empty result, which is what makes a subsequent mode request return 503
rather than 400.

## Non-goals

- **No approval seam.** See Tool approval above: the host config's `approval` block is ignored and
  tool calls are auto-approved.
- **No per-request workspace override.** Workspace is server-process scope. Run separate instances
  isolated by workspace slug instead.
- **No multi-worker, no reload.** One process, one bundle, one session loop. Multi-user deployments
  front it with a proxy and run multiple instances.
- **No multi-tenant key management.** One shared bearer secret, compared non-constant-time.
- **Sampling parameters are accepted and ignored.** `temperature`, `top_p`, `max_tokens`,
  `tool_choice`, and `stop` do not reach the provider.
- **No per-call usage breakdown.** Usage across internal model calls is summed, not itemized.
- **No structured error envelope for mid-stream errors.** Once SSE has started, errors are inline
  text in `delta.content`.
- **`AMPLIFIER_AGENT_HTTP_MODEL_NAME` has no observable effect.** It is accepted for forward
  compatibility; display names on every route come from provider metadata.

# ACP reference

`amplifier-agent-acp` speaks ACP protocol version 1 over stdio, one process per connection. It takes no arguments.
stdout carries only ACP messages; diagnostics go to stderr. When stdin closes, it cancels every active turn, closes
every session, and exits.

## Initialize

```
{
  "protocolVersion": 1,
  "agentInfo": {"name": "amplifier-agent", "version": "<installed package version>"},
  "authMethods": [{"id": "provider-credentials", "type": "agent", ...}],
  "agentCapabilities": {
    "loadSession": true,
    "promptCapabilities": {"image": true, "audio": false, "embeddedContext": true},
    "mcpCapabilities": {"http": true, "sse": false},
    "sessionCapabilities": {"resume": {}, "close": {}, "delete": {}, "additionalDirectories": {}}
  }
}
```

## Methods

```
initialize                 the response above
authenticate               checks the selected provider's credentials again; see auth
session/new                a new agent and durable session
session/load               the session, its history replayed, then the response
session/resume             the session, with no replay
session/close              cancels the active turn, closes the session and its agent
session/delete             deletes the session
session/prompt             one turn: its updates, then stopReason
session/cancel             cancels the active turn
session/set_config_option  changes the model or reasoning effort
```

Every other method, including `session/list`, `session/set_mode`, `session/fork`, unstable methods, and extension
methods, fails `-32601`.

Each session gets its own agent: `cwd` is its working directory, `additionalDirectories` its additional directories,
and `mcpServers` its MCP servers. The face is its approval handler. The `sessionId` is the session's `session_id`.

- `session/new` refuses a `cwd` that is not an absolute path to an existing directory. The response carries the
  `sessionId` and the session's [config options](#config-options).
- `session/load` and `session/resume` use the session when this process holds it, and otherwise resume it from the
  sessions directory, working in the request's `cwd`. A session another process holds fails `session_in_use`.
- `session/close` succeeds for a session that is unknown or already closed. `session/delete` closes the session first
  when this process holds it.
- `session/prompt` while the session's turn is active fails `busy`. `session/cancel` with no active turn does nothing.

## Prompt content

```
text              a text part
image             an image part: image/png, image/jpeg, image/gif, or image/webp
resource, text    a text part: <context ref="URI">TEXT</context>
resource, blob    an image part, for the same image types
resource_link     a text part: [name](uri)
```

Any other block, media type, or blob is refused with `-32602`, naming the type.

## Session updates

Each [event](../concepts/events.md) of a turn becomes these updates, in order. Every update of a turn arrives before
the `session/prompt` response, and nothing for a session arrives before its `session/new` response.

```
turn_started                 nothing
output_delta                 agent_message_chunk, messageId the turn_id
reasoning_delta              agent_thought_chunk
reasoning_final              agent_thought_chunk, only when its block streamed no reasoning_delta
tool_call                    tool_call, status pending
approval_request             session/request_permission
approval_decision            allow: tool_call_update, status in_progress; otherwise nothing
tool_result                  tool_call_update, status completed for success, failed otherwise
progress context_size        usage_update, when tokens and budget are both known
progress, other payloads     nothing
usage                        the cost of the next usage_update
terminal success             stopReason end_turn
terminal cancelled           stopReason cancelled
terminal failure, rejected   a JSON-RPC error
owned extension types        nothing
```

A `usage_update` carries `used` (the context's tokens), `size` (its budget), and `cost` in USD: the turn's latest
usage plus the final usage of every earlier turn in the session. `cost` is absent when any of those is unknown or has
no USD amount. See [usage](../concepts/usage.md).

Every `tool_call` gets a later update with status `completed` or `failed`.

## Tool calls

A `tool_call` carries `toolCallId` (the `call_id`), `rawInput` (the arguments), and a kind and title:

```
read_file     read      "Read <path>"
write_file    edit      "Write <path>"
edit_file     edit      "Edit <path>"
apply_patch   edit      "Patch <paths>"
glob          search    "Find <pattern>"
grep          search    "Search <pattern>"
bash          execute   the command's first line
web_fetch     fetch     "Fetch <url>"
web_search    fetch     "Search the web: <query>"
delegate      think     "Delegate: <instruction, first line>"
load_skill    other     "Load skill <name>"
other tools   other     the tool's name
```

Titles show paths relative to the working directory when inside it. Locations and diffs use absolute paths.
`read_file` carries the location `{path, line}`. `write_file` and `edit_file` carry a diff preview from their
arguments: `write_file` from the file's current text, or `null` when it does not exist, to `content`; `edit_file` from
`old_string` to `new_string`.

The `tool_call_update` for the result carries its text output, and for a failed or cancelled call the error message
and remedy. A call resolved `cancelled` or `unknown` is `failed`.

**File changes.** When a built-in tool changed files, the result's content is one diff per changed file, in the order
the tool changed them, replacing any preview:

```
{"type": "diff", "path": "/abs/path", "oldText": "...", "newText": "..."}
```

`oldText` is `null` when the call added the file. `newText` is empty when the call deleted it. A file whose content is
not text has no entry.

**Shell output.** `bash` output arrives once, with the result, as a `console` code block. When the client's
`initialize` carries `_meta.terminal_output: true`, the `tool_call` also carries `_meta.terminal_info` and a terminal
content block, and the result carries `_meta.terminal_output` and `_meta.terminal_exit`, so the client renders a
terminal.

## Permission requests

When the host's `approvals` setting is `"deny"`, every [approval request](../concepts/approvals.md) is denied without
asking. Otherwise each is a `session/request_permission`, sent after the `tool_call` update for its `call_id`.

```
allow_once     "Allow"                                 allow
allow_always   "Always allow <scope> in this session"  allow, and remember the rule
reject_once    "Deny"                                  deny, reason "The user denied this call."
cancelled                                              cancel
```

`allow_always` is offered when the call has a rule key: the tool name, or for `bash` the exact command. A remembered
rule allows later matching requests in the same session without asking. An `optionId` the face did not offer is a
deny.

A request without a `call_id` first gets its own `tool_call`: `toolCallId` the `request_id`, kind `execute`, title the
request's `summary`.

`session/cancel` settles every open permission request as cancel without waiting for the client. A request left
unanswered expires at the binding's deadline, and the turn fails `approval_timeout`.

## Load replay

`session/load` sends, before its response, for each earlier turn: a `user_message_chunk` per content part of its
input, then its reply as one `agent_message_chunk`.

## Config options

```
model             category model          "<provider>/<model>"
reasoning_effort  category thought_level  the current model's reasoning efforts
```

`model` lists every model of every installed provider whose credentials are `found` or `not_required`.
`reasoning_effort` is absent when the current model lists none.

The `session/new` response carries the current values, with the session's own model as the only choice. The full model
list follows in a `config_option_update` once discovery finishes.

`session/set_config_option` [switches the model](../concepts/sessions.md#switching-models) with the session's other
values and answers every config option. A change during a turn fails `busy`. A failed change leaves the session as it
was.

## Auth

`authMethods` holds one method of type `agent`, id `provider-credentials`. `session/new` fails `-32000` when the
selected provider's credentials are missing, naming its [credential variables](../providers.md#credential-status).
`authenticate` checks them again and fails the same way while they are still missing. Set them in the agent's process
environment.

## Errors

A failure the binding reports, before a turn or as its terminal error, is a JSON-RPC error:

```
code      -32602  invalid_input, session_id_invalid
          -32002  not_found
          -32603  every other code
message   the error's message, then its remedy
data      {"code", "category", "retryable"}
```

`data.code` is the [registered code](../concepts/errors.md). The face's own refusals, of a `cwd`, a content block, or
a config value, are `-32602` with a remedy in the message. Missing credentials are `-32000`.

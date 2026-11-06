# ACP Face Contract v1 (FROZEN 2026-11-06)

**Who builds against this:** editors and other programs that speak the [Agent Client
Protocol](https://agentclientprotocol.com) and start their agent as a subprocess: Zed, JetBrains IDEs, and Neovim and
Emacs plugins are typical. They show the agent's work in their own interface and cannot embed a binding.

**What it freezes:** a projection of [`agent-interface.v1`](agent-interface.v1.md) sessions and turns onto the stable
methods of ACP protocol version 1, carrying [`turn-events.v1`](turn-events.v1.md) events as ACP session updates and
approvals as ACP permission requests. The executable is `amplifier-agent-acp`.

This is a face, not a binding. It carries sessions, turns, approvals, and the model selection, and nothing else: no
caller tools, and no configuration beyond what the host sets.

## 1. Process

```text
editor  --ACP over stdio-->  amplifier-agent-acp     one process per connection
                               one agent and one session per ACP session
```

The executable takes no arguments. Its settings are the host's: [`host-config.v1`](host-config.v1.md) and the provider
credentials in its environment. The editor's messages change none of them, apart from a session's model selection
(section 9).

stdout carries only ACP messages. Diagnostics go to stderr.

Each ACP session gets its own agent, created with the session's `cwd` as `working_directory`, its
`additionalDirectories` as `additional_directories`, its `mcpServers` as `mcp_servers`, and the face as the approval
handler. Sessions are `durable`. The ACP `sessionId` is the session's `session_id`.

When stdin closes, the face cancels every active turn, closes every session and agent, and exits.

## 2. Initialization

`initialize` answers `protocolVersion: 1`, `agentInfo: {name: "amplifier-agent", version}` where `version` is the
installed package version, one auth method (section 11), and these capabilities:

```json
{
  "loadSession": true,
  "promptCapabilities": {"image": true, "audio": false, "embeddedContext": true},
  "mcpCapabilities": {"http": true, "sse": false},
  "sessionCapabilities": {"list": {}, "resume": {}, "close": {}, "delete": {}, "additionalDirectories": {}}
}
```

## 3. Methods

```text
initialize                 section 2
authenticate               section 11
session/new                create_agent, then create_session
session/load               the session's handle, then history replayed (section 8), then the response
session/resume             the session's handle, with no replay
session/close              cancel the active turn, close the session and its agent
session/delete             delete_session
session/list               list_sessions, for one directory
session/prompt             start_turn, its updates (section 5), then stopReason
session/cancel             cancel the active turn
session/set_config_option  set_model, or the approvals mode (section 9)
```

Every other method, including `session/set_mode`, `session/fork`, every unstable method, and every
extension method, fails `-32601` (method not found).

`session/new` refuses a `cwd` that is not an absolute path to an existing directory. Its response carries the
`sessionId` and the session's config options (section 9).

`session/load` and `session/resume` use the session's live handle when this process holds one, and otherwise resume
it, so the session works in the request's `cwd` (`agent-interface.v1` section 3). A session held by another process
fails `session_in_use`.

`session/close` succeeds for a session that is unknown or already closed. `session/delete` closes the session first
when this process holds it.

`session/list` creates an agent for the request's `cwd`, or the process's current directory when it is absent, and
answers the sessions whose `working_directory` is that directory, most recently updated first, each as `{sessionId,
cwd, title, updatedAt}`. It answers every session at once, with no `nextCursor`.

A `session/prompt` while the session's turn is active fails `busy`. `session/cancel` while no turn is active has no
effect.

## 4. Errors

A failure the binding reports, before a turn or as its `terminal` error, is a JSON-RPC error:

```text
code      -32602 for invalid_input and session_id_invalid, -32002 for not_found, -32603 for every other code
message   the error's message, then its remedy
data      {code, category, retryable}
```

The face's own refusals, of a `cwd`, a content block, or a config value, are `-32602` with a remedy in the message.
Missing credentials are `-32000` (section 11).

## 5. Turn events as session updates

Each event of a turn becomes the updates below, in the event's order. Every update of a turn is sent before the
`session/prompt` response, and nothing for a session is sent before its `session/new` response.

```text
turn_started                 nothing
output_delta                 agent_message_chunk, messageId the turn_id
reasoning_delta              agent_thought_chunk
reasoning_final              agent_thought_chunk, only when its block streamed no reasoning_delta
tool_call                    tool_call, status pending (section 6)
approval_request             session/request_permission (section 7)
approval_decision            allow: tool_call_update, status in_progress; otherwise nothing
tool_result                  tool_call_update, status completed for success and failed otherwise (section 6)
progress context_size        usage_update, when tokens and budget are both known
progress, other payloads     nothing
usage                        the cost of the next usage_update
terminal success             session_info_update {title, updatedAt}, then stopReason end_turn
terminal cancelled           stopReason cancelled
terminal failure, rejected   a JSON-RPC error (section 4)
owned extension types        nothing
```

A `usage_update` carries `used` (the context size's `tokens`), `size` (its `budget`), and `cost` in USD: the latest
usage snapshot of the turn plus the final usage of every earlier turn in the session. `cost` is absent when any of
those is unknown or lacks USD.

One conversion produces the updates of a live turn and of a replayed one (section 8).

## 6. Tool calls

A `tool_call` update carries `toolCallId` (the `call_id`), `rawInput` (the call's `arguments`), and this `kind` and
`title`:

```text
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

The `tool_call_update` for a `tool_result` carries the resolution's text output, and for a failed or cancelled call
its error message and remedy. ACP has no cancelled status, so a call resolved `cancelled` or `unknown` is `failed`.

**File changes.** For a built-in tool that changed files, the update's content is one diff per changed file, in the
order the tool changed them, replacing any preview:

```text
{type: "diff", path, oldText, newText}
```

`path` is absolute. `oldText` is the file's text before the call, absent when the call added it, which ACP reads as
`null`. `newText` is its text after the call, empty when the call deleted it. A file whose content is not text has no
entry.

**Shell output.** `bash` output arrives once, with its `tool_result`, as a `console` code block. When the client's
`initialize` carries `_meta.terminal_output: true`, the `tool_call` also carries `_meta.terminal_info` and a terminal
content block, and the result carries `_meta.terminal_output` and `_meta.terminal_exit`, so the client renders a
terminal.

## 7. Approvals

The face is each agent's approval handler. When the host's static policy is `"deny"`, every request is denied without
asking, and when it is `"allow"`, every request is allowed without asking. When the host sets no policy, the session's
`approvals` config option (section 9) decides:

```text
ask           every request is put to the client
allow-reads   read_file, glob, and grep calls whose paths are all inside the session's working directory or additional
              directories, and load_skill calls, are allowed without asking; every other request is put to the client
allow-all     every request is allowed without asking
```

A request put to the client is a `session/request_permission`, sent after the `tool_call` update for its `call_id`.

```text
allow_once     "Allow"                                 allow
allow_always   "Always allow <scope> in this session"  allow, and remember the rule
reject_once    "Deny"                                  deny, reason "The user denied this call."
cancelled outcome                                      cancel
```

`allow_always` is offered only when the call has a rule key: the tool name for every tool except `bash`, and the exact
command for `bash`. A remembered rule allows later matching requests in the same session without asking, under `ask`
and `allow-reads`. An `optionId`
the face did not offer settles the request as `deny`.

A request without a `call_id` first gets its own `tool_call` update: `toolCallId` the `request_id`, kind `execute`,
title the request's `summary`.

`session/cancel` settles every open permission request as `cancel`, without waiting for the client's answer. A request
the client leaves unanswered stays open until the client answers or the turn is cancelled.

## 8. Sessions

`session/load` replays each turn of `session.history`, before its response: a `user_message_chunk` per content part of
the turn's input, then its `activity` through the conversion of section 5. A `reasoning` item is an
`agent_thought_chunk`, an `output` item an `agent_message_chunk`, and a `tool` item a `tool_call` with its final
`tool_call_update`. A replayed edit's diff comes from the call's arguments: `edit_file` as in section 6, `write_file`
with no `oldText`. A turn with no `activity` replays its result's `content` as one `agent_message_chunk`.

## 9. Config options

```text
model             category model           "<provider>/<model>"
reasoning_effort  category thought_level   the current model's reasoning_efforts
approvals         category mode            ask, allow-reads, allow-all (section 7)
```

`model` lists every model of every provider that is installed and whose credentials are `found` or `not_required`.
`reasoning_effort` is absent when the current model lists no reasoning efforts. `approvals` is absent when the host sets
a static policy, and otherwise starts at `allow-reads` for each session this process opens.

The `session/new` response carries the current values with the session's own model as the only choice. The full model
list follows in a `config_option_update` once discovery finishes.

`session/set_config_option` for `model` or `reasoning_effort` calls `set_model` with the new value and the session's
other values, and fails `busy` during a turn. A change of `approvals` calls nothing in the binding and applies to every
later request of the session, also during a turn. Every change answers every config option, and a failed change leaves
the session as it was.

## 10. Prompt content

```text
text              a text part
image             an image part, for image/png, image/jpeg, image/gif, image/webp
resource, text    a text part: <context ref="URI">TEXT</context>
resource, blob    an image part, for the image types above
resource_link     a text part: [name](uri)
```

Any other block, media type, or blob is refused with `-32602`, naming the type.

## 11. Auth

`authMethods` holds one method, id `provider-credentials`, with no `type`, which ACP v1 reads as `agent`.
`session/new` fails `-32000` (auth required) when the selected provider's credentials are `missing`, or when the host
selects no provider and none of the providers the engine would select by default has credentials. The message names
the `credential_variables` to set.
`authenticate` checks those credentials again, and fails the same way while they are still missing.

## Invariants

1. **This face invents no agent behavior.** Updates are the binding's events. The only additions are the diffs, an
   observation of an effect that already ran, and the approval modes and remembered rules of section 7.
2. **The editor never gains authority the host did not grant.** A static `"deny"` is never put to the user, and an
   answer the face did not offer is never read as allow.
3. **Every `session/prompt` gets one response, after every update of its turn.** Every `tool_call` update has a later
   update with status `completed` or `failed`.
4. **stdout carries only ACP messages.**

## What this face cannot carry

- **Caller tools.** A caller-supplied tool is a function in the caller's process, and the editor is not a process the
  face can call into.
- **Live tool output.** A shell command's output arrives when the call ends.
- **Exact diffs in load replay.** A replayed edit shows the change from the call's arguments.
- **Per-model usage and token counts per turn.** Only context fill and cost are shown.
- **Configuration per session.** Instructions, skills, the tool set, and the sessions directory are host settings,
  the same for every session.
- **Unsaved editor buffers.** Tools read files from disk.
- **Audio, SSE MCP servers, non-image binary resources, and session fork.**

## Versioning

`acp-face/1`, independent of the other contracts, of ACP's protocol version, and of releases. Every change is a dated,
owner-ratified amendment in the changelog below.

## Changelog

Dated, owner-ratified amendments only.

- 2026-11-06: v1 FROZEN by owner ratification. The evidence: editors that speak ACP, such as Zed, start their agent as a
  subprocess and talk to it over stdio, where no binding can be embedded, and they need every reply, tool call, diff,
  and approval to show the agent's work in their own interface.
- 2026-11-06: Additive, by owner ratification, with the `agent-interface.v1` amendments of the same date:
  `session/list` and its capability (sections 2 and 3), `session_info_update` after a successful turn (section 5),
  approval requests with no deadline (section 7), and load replay of each turn's activity (section 8). The matching
  entries are removed from What this face cannot carry.
- 2026-11-06: Behavior change, by owner ratification: a host static policy of `"allow"` allows without asking
  (section 7), and `session/new` fails `-32000` when no provider is selected and none has credentials (section 11).
  Sections 6 and 11 state how an absent `oldText` and the untyped auth method are read.
- 2026-11-06: Behavior change, by owner ratification: the `approvals` config option, `ask`, `allow-reads`, or
  `allow-all`, decides requests when the host sets no static policy, and starts at `allow-reads`, so reads inside the
  session's directories run without asking (sections 7 and 9).

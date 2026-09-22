# Tools

The tools decide what the agent is for. A filesystem and a shell make it a coding agent.
Your deployment API makes it a release agent.

The model decides when a tool should run. The agent invokes it. Every tool has exactly one
**executor**, the party that performs the effect and reports what happened.

```
built-in   the agent executes, beneath this interface
caller     your process executes, in your own code
mcp        a configured MCP server executes, in a third process
```

All three reach the model as one flat set, and every tool event names its source. Source
determines executor, so reading a `tool_call` tells you where the effect will land before
it lands.

Your code is never executed anywhere but your process, and no effect happens without a
preceding `tool_call` naming its source.

The rules below do not vary by executor. Where you execute, they are carried across the
callback boundary. Where the agent executes, it holds itself to them. An effect you
cannot see, cannot refuse, or cannot get a truthful answer about is a defect regardless of
which process ran it.

## Declaring a tool

```
name          stable, unique within the agent
description   what it does, for the model
input_schema  JSON Schema, carrying $schema
safety        optional, descriptive
handler       your function
```

Caller tool names contain 1 to 64 letters, digits, underscores, or hyphens. Names must be
unique across caller, built-in, and MCP tools. Duplicate names, a caller declaration
without a handler, or a string that is not a built-in name are refused at construction.

`safety` is descriptive metadata. It does not decide anything by itself. Authority over
effects lives in [approvals](approvals.md).

## Built-in tools and skills

The agent supplies filesystem, shell, search, web, and delegation tools:

```text
read_file   write_file   edit_file   glob   grep
bash        web_fetch   web_search  delegate
```

`tools` is the whole set. Absent, it is every built-in; given, it is exactly its
entries: caller declarations and built-in names, so `[]` is no tools and
`[*BUILTIN_TOOLS, mine]` is every built-in plus yours. A caller tool may take the name
of a built-in that is not in the set. Skills whose commands run automatically need
`"bash"` in the set. Built-ins run with the host process's permissions. Use an
approval handler to decide which requested effects may run.

Relative filesystem paths and shell commands use the working directory captured when
the agent is constructed. Shell commands use its captured environment, wait for
completion, and accept timeouts from 1 to 120 seconds, defaulting to 30 seconds.
A timeout stops the command's process tree and reports an unknown outcome because
effects may already have happened. Approval also applies to tools requested by a
delegated task.

`delegate` accepts an instruction and optional model or `model_role`, plus an optional
list of inherited tool names. `general` keeps the current model; `economy` chooses
within a verified price ordering and keeps the current model when no comparison is
available. A named model and a role cannot be combined.

Configure `skills` with local source directories or Git source URLs. Each skill needs
a `SKILL.md` with a name and description in its frontmatter. The `load_skill` tool's
description lists available names; calling it with a name loads those instructions.
Command preprocessing produces separate `bash` calls with their own approvals and
results. Fork skills run a child task; their model and tool choices stay within the
parent's provider, ceiling, and tool set.
Use commands in skill bodies for executable work. Fork selection accepts a concrete
model or the `general` and `economy` roles. Skill names must be unique across sources.

Local sources resolve from the agent's captured working directory. Remote sources use
`git+https://host/owner/repository@ref#subdirectory=skills`; the ref defaults to `main`
and the subdirectory may be omitted. Git access must be configured on the agent's host.

Skills can run approved command hooks before and after tools and at successful
completion. Fork skills can select named agents from their configured sources.
See [skills](skills.md) for source layout, hook input, and authority inheritance.

## A call

```
call { call_id, name, source, arguments, deadline? }
```

`arguments` arrive decoded, as strict JSON, never as a JSON-encoded string.
Your handler also receives the correlated `call_id` and optional deadline. Deadlines
are absolute UTC times; an absent deadline does not imply a binding-level timeout.

## Exactly one resolution

```
resolution { call_id, outcome, content?, error?, truncated?, original_bytes? }

completed   it ran and produced a result
failed      it ran and failed
cancelled   it did not run to completion
unknown     the executor cannot say whether the effect happened
```

A resolution arriving after the call is settled is ignored.

A completed result is capped at `AgentOptions.tool_result_max_bytes` (262144 by
default; `None` in Python or `null` in TypeScript for no cap), whatever its executor.
The kept content ends with one line such as
`...[tool output reached limit: kept 262144 of 41841565 bytes]`, and the resolution
carries `truncated` and `original_bytes`. The bytes beyond the cap reach no event,
transcript, or model.

```
tool_callback_failed      the executor could not be reached, or died with no result
tool_result_invalid       malformed result, wrong call_id, or a second resolution
tool_failed               the executor reported that the tool failed
tool_completion_unknown   the executor cannot say whether the effect happened
```

By default, each of these ends the turn as `failure`, except
`tool_completion_unknown` when cancellation was already accepted.

## Recovering within a turn

Set `AgentOptions.tool_error_policy="continue"` in Python or
`AgentOptions.toolErrorPolicy: "continue"` in TypeScript to let the model receive
ordinary `tool_failed` and `tool_completion_unknown` results and continue the same
turn. A nonzero shell exit remains `failed`; a timeout remains `unknown`. Captured
stdout and stderr accompany the error, including output captured before timeout.
A successful final answer does not turn those tool results into successes.

After an unknown outcome, only model responses and built-in local inspection tools
(`read_file`, `glob`, `grep`) may start during that turn. New shell, write, caller,
MCP, delegated, and skill effects are blocked, including calls waiting for approval.
Already executing effects drain. Attempting a blocked effect produces a `cancelled`
tool result and terminal `tool_recovery_blocked`, referencing the uncertain call.
Inspect actual effects before requesting further work in a new turn.

Invalid results, unreachable executors, approval refusal, cancellation, and skill
guard failure remain terminal. Inspection still requires approval and passes skill
guards. Recovery and approval policy are independent; neither automatically retries
an uncertain effect.

## Uncertainty is passed through

An uncertain outcome stays uncertain. An effect that may already have landed is never
retried, and never described as rolled back.

This is the one place where a comfortable answer would cost you the ability to trust every
other answer.

## MCP servers

```
mcp_servers: [
  { name, transport: "stdio", command, args?, env? }
  { name, transport: "http",  url, headers? }
]
```

An MCP server runs in its own process and executes its own tools. Its tools carry source
`mcp` and are subject to everything above.

MCP tool names use `mcp_<server>_<tool>`. A server-declared execution error follows
the configured tool error policy. Losing the connection after dispatch produces an
unknown outcome and never replays the call.
Text and structured MCP results are preserved in the tool result's text representation.

Servers connect and expose their tools during agent construction. A failed connection
prevents construction with `engine_unavailable`. `stdio` commands must be executable
on the agent's host; `env` extends its captured environment. HTTP transport uses a
Streamable HTTP MCP endpoint and optional authentication headers.

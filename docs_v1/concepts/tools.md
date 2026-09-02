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

Two tools with the same name, or a tool set without a handler, are refused at
construction.

`safety` is descriptive metadata. It does not decide anything by itself. Authority over
effects lives in [approvals](approvals.md).

## A call

```
call { call_id, name, source, arguments, deadline? }
```

`arguments` arrive decoded, as strict JSON, never as a JSON-encoded string.

## Exactly one resolution

```
resolution { call_id, outcome, content?, error? }

completed   it ran and produced a result
failed      it ran and failed
cancelled   it did not run to completion
unknown     the executor cannot say whether the effect happened
```

A resolution arriving after the call is settled is ignored.

```
tool_callback_failed      the executor could not be reached, or died with no result
tool_result_invalid       malformed result, wrong call_id, or a second resolution
tool_failed               the executor reported that the tool failed
tool_completion_unknown   the executor cannot say whether the effect happened
```

Each of these ends the turn as `failure`, except `tool_completion_unknown` when a
cancellation was already accepted.

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

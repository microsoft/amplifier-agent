# CLI

The `amplifier-agent` command. Every agent command is a composition of library
calls with a terminal-shaped presentation on top, so anything here has a Python
equivalent and the reverse holds too.

```bash
amplifier-agent run "Find the failing test in tests/ and fix it."
```

## Commands

Agent commands compose the library. Each one is listed with the calls it makes,
which is the whole specification of what it does.

```
run          create_agent, create_session | resume_session, run | stream, close
sessions     create_agent, list_sessions, close
sessions rm  create_agent, delete_session, close
skills       create_agent, list_skills, close
models       list_models(provider)
providers    list_providers()
```

`providers` and `models` call module-level functions and never construct an
agent, because you run them to decide what to put in a config.

Installation commands manage the install rather than the agent. They have no
library equivalent because they are not agent capability.

```
version      what is installed, and the surface version it speaks
doctor       check credentials, dependencies, and writable paths
auth         store and remove credentials in the credential store
prepare      warm the caches so the first run is not the slow one
cache clear  drop those caches
update       upgrade the installed distribution
migrate      move state written by an older layout
config show  print the resolved configuration and where each value came from
serve        run the HTTP surface
```

## Running a turn

```bash
amplifier-agent run "Summarize src/parser.py"
amplifier-agent run --session-id ticket-4417 --resume "Now write the tests"
amplifier-agent run --prompt-file ./prompt.md --workspace api
```

- **`--session-id`** names the session. Omit it and the turn is ephemeral.
- **`--resume`** resumes that session instead of starting a new one.
- **`--workspace`** and **`--cwd`** set `AgentConfig.workspace` and
  `AgentConfig.cwd`.
- **`--prompt-file`** reads the prompt from a file, for prompts too long or too
  quote-heavy for a shell.

## The two streams

stdout carries the result. stderr carries everything else. They are independent
because they answer to different readers: stdout is parsed, stderr is watched.

```
--output   text | json      what lands on stdout at the end of the turn
--display  text | ndjson    what lands on stderr while the turn runs
```

This split is what makes the CLI safe to pipe. A tool printing progress to
stdout would corrupt the one thing a caller is trying to parse, so nothing
except the envelope is ever written there.

```bash
amplifier-agent run "..." --output json --display ndjson 2>events.log | jq .reply
```

## The result envelope

`--output json` writes one JSON object, once, when the turn ends. It is a
serialization of `TurnResult` plus what the surface itself knows.

```json
{
  "surface_version": "1",
  "session_id": "ticket-4417",
  "turn_id": "01J8XZ...",
  "reply": "Fixed the off-by-one in tokenize().",
  "stop_reason": "completed",
  "usage": {
    "input_tokens": 18432,
    "output_tokens": 512,
    "cache_read_tokens": 16000,
    "cache_write_tokens": 2048,
    "cost_usd": "0.0431",
    "model": "claude-sonnet-5"
  },
  "error": null,
  "duration_ms": 8140,
  "agent_version": "0.17.0"
}
```

`cost_usd` is a decimal string rather than a JSON number, because JSON numbers
are floats and money is not. Parse it as a decimal.

`error` is `null` on success and otherwise carries the `AgentError`:

```json
{"code": "provider/rate_limited", "message": "...", "retryable": true, "details": {}}
```

A turn that spent tokens and then failed still reports its usage. Those tokens
were charged whether or not a reply came back, and omitting them would make
spend invisible on exactly the runs worth investigating.

`--output text` prints `reply` and nothing else, so the common case pipes
cleanly into another command.

## The event stream

`--display ndjson` writes one JSON object per line to stderr as the turn runs,
one per event in the [registry](../05-interface/events.md). Each line is the
event serialized directly, with its `type` field intact.

```json
{"type": "turn/started", "session_id": "...", "turn_id": "...", "prompt": "..."}
{"type": "tool/call", "session_id": "...", "turn_id": "...", "tool_call_id": "c1", "name": "read_file", "arguments": {"path": "src/parser.py"}, "source": "builtin"}
{"type": "tool/result", "session_id": "...", "turn_id": "...", "tool_call_id": "c1", "name": "read_file", "duration_ms": 12, "result": {"content": "...", "is_error": false}}
```

Field names match the library's, so one reader handles these frames, the
recorded [event log](../04-context-intelligence.md), and the HTTP surface.

`--display text` renders the same events for a person to read. `--quiet`
suppresses them. Neither changes which events occur.

## Approvals

```
(default)          prompt on the terminal, deny when there is no terminal
--yes              allow every request
--no               deny every request
```

Each of these is an `ApprovalHandler` the CLI supplies, so the behavior is the
library's and only the prompt is the CLI's.

The no-terminal case denies rather than allows. A run in CI does not gain
permission because nobody is watching, which is the same rule the library states
for a missing handler. Pass `--yes` when you mean it, and the decision is
recorded in your pipeline rather than implied by its environment.

## Configuration

```
--config <path>    a config file, or $AMPLIFIER_AGENT_CONFIG
--provider <name>  overrides provider.name
--model <id>       overrides provider.model
--mcp-config <path>  MCP servers
```

Flags override file values, which override defaults. `config show` prints the
resolved result with the origin of each value, which is faster than reasoning
about the precedence.

## Exit codes

```
0    the turn completed
1    the turn ran and failed; error is populated in the envelope
2    the command was used wrong, or the configuration is invalid
130  interrupted
```

Exit code 2 means the turn never started, so nothing was spent. Exit code 1
means it did, and the envelope tells you what it cost before it failed.

An envelope is written on every one of these, including the failures. A caller
parsing stdout does not have to special-case a missing document.

## Versioning

`version` reports both the release and the surface version, and
`--surface-version <v>` asserts the one the caller expects.

```bash
amplifier-agent version
amplifier-agent run "..." --surface-version 1 --output json
```

The surface version covers the argv shape, the envelope, and the event frames.
It is not the library's `contract_version` and not the release version, because
the CLI can gain a flag without the library changing and the reverse is just as
true.

A mismatch fails before the turn starts, with exit code 2. A wrapper and the
command it spawns disagreeing about the shape of the envelope is not something
to discover halfway through parsing one.

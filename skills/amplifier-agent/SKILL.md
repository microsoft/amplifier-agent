---
name: amplifier-agent
description: Integrate Amplifier Agent v1 into applications using its Python or TypeScript library or OpenAI-compatible HTTP face. Use for installation, sessions, streaming, tools, MCP, skills, approvals, configuration, and integration troubleshooting.
license: MIT
metadata:
  author: microsoft
  repository: https://github.com/microsoft/amplifier-agent
---

# Amplifier Agent integration

Build against the public bindings and their docs. Read the linked page before writing
API calls. Prefer `docs/` in a matching local checkout when one exists.

Docs root: https://github.com/microsoft/amplifier-agent/blob/v1/docs/index.md

## The v1 branch

This skill targets Amplifier Agent v1, which lives on the `v1` branch and will move to
`main`. Links, install commands, and file paths here use `v1`. If one no longer
resolves, look for the same page or path on `main`; locations may differ slightly.

## Pick a surface and install

Follow [install](https://github.com/microsoft/amplifier-agent/blob/v1/docs/install.md)
and [providers](https://github.com/microsoft/amplifier-agent/blob/v1/docs/providers.md).
Do not assume a package registry release implements v1.

```
Python      Python 3.12+, uv add from the v1 branch      docs/python/{quickstart,names,reference}.md
TypeScript  Node 22, Linux x86-64, glibc 2.35+, ESM;      docs/typescript/{quickstart,names,reference}.md
            built from a v1 checkout, then npm install
HTTP        chat-completions server amplifier-agent-face  docs/http/{quickstart,reference,limits}.md
```

- TypeScript options are camelCase; received records keep snake_case spelling
  (`session_id`, `call_id`). Counters are `bigint`, costs are decimal strings.
- HTTP carries less than a binding. It has no interactive approvals, caller tools, durable
  sessions, or full event stream. Its tools run under a static policy from
  `AMPLIFIER_AGENT_APPROVALS`, `"approvals"` in the config file, or `create_app`
  options; with tools and no policy the server refuses to start. All clients share the server's tools, filesystem, and credentials.
  The face binds `127.0.0.1`; set `AMPLIFIER_AGENT_FACE_BIND` to serve from a container.

## Build the integration

1. Construct the agent with provider, model, and instructions. Configuration is captured
   at construction; recreate the agent after changing credentials. See
   [configuration](https://github.com/microsoft/amplifier-agent/blob/v1/docs/configuration.md)
   and [models](https://github.com/microsoft/amplifier-agent/blob/v1/docs/concepts/models.md).
   Set both provider and model when switching providers. An unavailable model is
   reported by the first turn, not at construction.
2. Sessions are [durable by default](https://github.com/microsoft/amplifier-agent/blob/v1/docs/concepts/sessions.md).
   To resume, keep the session ID, storage root, and workspace, and rebuild tools and
   approvals. Use ephemeral sessions for disposable work or caller-supplied `history`.
   One turn at a time per session.
3. `session.run` returns a final result; `start_turn` / `startTurn` streams. See
   [turns](https://github.com/microsoft/amplifier-agent/blob/v1/docs/concepts/turns.md).
4. Check both a raised `AgentError` and the terminal `TurnResult.state` / `error`, and
   surface `code`, `message`, and `remedy`. Returned text alone is not success. See
   [errors](https://github.com/microsoft/amplifier-agent/blob/v1/docs/concepts/errors.md);
   `context_exceeded` means start a new session or fork from an earlier turn.
5. Close agents and sessions with context managers or `finally`. To stop a streaming
   turn, call `cancel()` and drain to `terminal`; leaving the loop does not cancel.
   An open TypeScript agent keeps the Node process alive.

## Tools and approvals

Read [tools](https://github.com/microsoft/amplifier-agent/blob/v1/docs/concepts/tools.md),
[approvals](https://github.com/microsoft/amplifier-agent/blob/v1/docs/concepts/approvals.md),
and, for reusable instructions and named agents,
[skills](https://github.com/microsoft/amplifier-agent/blob/v1/docs/concepts/skills.md).

- `tools` is the whole set. Omitted, it is every built-in; `[]` is no tools; keep the
  built-ins and add yours with `[*BUILTIN_TOOLS, mine]` / `[...BUILTIN_TOOLS, mine]`.
  Built-ins run with the host process's permissions.
- Every tool call, including reads and MCP, needs an approval handler or a static
  `"allow"` / `"deny"` policy, in `AgentOptions` or through `AMPLIFIER_AGENT_APPROVALS`.
  Without one, tool requests fail with `approval_unavailable`.
- `workspace` separates stored sessions. It is not a sandbox.
- Caller tools need a unique name, a JSON Schema with `$schema`, and a handler. Report
  known failures with `ToolFailed` and uncertain effects with `ToolOutcomeUnknown`;
  never retry an unknown outcome.

## Streaming UI

Use [events](https://github.com/microsoft/amplifier-agent/blob/v1/docs/concepts/events.md)
and [usage](https://github.com/microsoft/amplifier-agent/blob/v1/docs/concepts/usage.md).
Each turn's stream has one consumer. Correlate tools by `call_id` and approvals by
`request_id`. Render `output_delta` parts or the terminal content, not both. Usage
events replace the previous snapshot; do not sum them. A stream without `terminal` is
incomplete.

## Verify

Run one small live turn, one failure path, and cleanup. With tools or streaming, also
exercise an approval denial and a cancellation. Live turns need credentials and cost money.

## Source inspection

Optional, for diagnosing behavior. Keep application code on the public surface; engine
internals and upstream module APIs are not the public interface. There is no `amplifier-agent` CLI.

```bash
git clone --depth 1 --single-branch --branch v1 \
  https://github.com/microsoft/amplifier-agent.git "$(mktemp -d)/amplifier-agent"
```

Start at `docs/development/architecture.md`. Upstream module revisions are in
`packages/engine/pyproject.toml` and `uv.lock`.

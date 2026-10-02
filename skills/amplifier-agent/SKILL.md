---
name: amplifier-agent
description: Integrate Amplifier Agent into applications using its Python or TypeScript library or OpenAI-compatible HTTP face. Use for installation, sessions, streaming, tools, MCP, skills, approvals, configuration, and integration troubleshooting.
license: MIT
metadata:
  author: microsoft
  repository: https://github.com/microsoft/amplifier-agent
  version: "0.20.0"
---

# Amplifier Agent integration

Build against the public bindings and their docs. Read the linked page before writing
API calls. Prefer `docs/` in a matching local checkout when one exists.

Docs root: https://github.com/microsoft/amplifier-agent/blob/main/docs/index.md

## Versions

Links and file paths here use `main`. Installs use a release tag; when the installed
tag differs from `main`, read the docs at that tag.

## Pick a surface and install

Follow [install](https://github.com/microsoft/amplifier-agent/blob/main/docs/install.md)
and [providers](https://github.com/microsoft/amplifier-agent/blob/main/docs/providers.md).
`amplifier-agent-ts` versions before 0.20.0 on npm are an older, incompatible API.
Install the Python binding as `amplifier-agent[github-copilot]` unless the user asks
otherwise; do not ask them whether to include the extra.

```
Python      Python 3.12+, uv add from a release tag      docs/python/{quickstart,names,reference}.md
TypeScript  Node 22, Linux x86-64, glibc 2.35+, ESM;      docs/typescript/{quickstart,names,reference}.md
            npm install amplifier-agent-ts
HTTP        chat-completions server amplifier-agent-face  docs/http/{quickstart,reference,limits}.md
```

- TypeScript options are camelCase; received records keep snake_case spelling
  (`session_id`, `call_id`), except `ImagePart.mediaType`. Counters are `bigint`,
  costs are decimal strings.
- HTTP carries less than a binding: no interactive approvals, caller tools, durable
  sessions, or full event stream. Its tools run under a static policy from
  `AMPLIFIER_AGENT_APPROVALS`, `"approvals"` in the config file, or `create_app`
  options; with tools and no policy the server refuses to start. All clients share the
  server's tools, filesystem, and credentials. Each response carries one `usage` summed
  across the turn. The face binds `127.0.0.1`; set `AMPLIFIER_AGENT_FACE_BIND` to serve
  from a container.

## Build the integration

1. Construct the agent with provider, model, and instructions. Configuration is captured
   at construction; recreate the agent after changing credentials. See
   [configuration](https://github.com/microsoft/amplifier-agent/blob/main/docs/configuration.md)
   and [models](https://github.com/microsoft/amplifier-agent/blob/main/docs/concepts/models.md).
   Set both provider and model when switching providers. An unavailable model is
   reported by the first turn, not at construction.
2. Sessions are [durable by default](https://github.com/microsoft/amplifier-agent/blob/main/docs/concepts/sessions.md).
   To resume, keep the session ID, storage root, and workspace, and rebuild tools and
   approvals. Use ephemeral sessions for disposable work or caller-supplied `history`.
   One turn at a time per session.
3. `session.run` returns a final result; `start_turn` / `startTurn` streams. See
   [turns](https://github.com/microsoft/amplifier-agent/blob/main/docs/concepts/turns.md).
4. Check both a raised `AgentError` and the terminal `TurnResult.state` / `error`, and
   surface `code`, `message`, and `remedy`; the remedy names the fix. Returned text
   alone is not success. See
   [errors](https://github.com/microsoft/amplifier-agent/blob/main/docs/concepts/errors.md).
   Long conversations are compacted automatically; `context_exceeded` means even that
   did not fit, so start a new session.
5. Close agents and sessions with context managers or `finally`. To stop a streaming
   turn, call `cancel()` and drain to `terminal`; leaving the loop does not cancel.
   An open TypeScript agent keeps the Node process alive.
6. Images go inline as base64 image parts (png, jpeg, gif, webp) in input content or
   `user` messages; over HTTP, as `image_url` parts with `data:` URLs. Output is text.
   `image_unsupported` means the model or provider cannot take them; choose another
   model or remove the images. The failed turn's images become one-line descriptions
   in the conversation, so the session continues. Images are sent as given, with no
   size limit or resizing; a provider's size refusal fails `provider_failed`. See
   [providers](https://github.com/microsoft/amplifier-agent/blob/main/docs/providers.md#images).

## Tools and approvals

Read [tools](https://github.com/microsoft/amplifier-agent/blob/main/docs/concepts/tools.md),
[approvals](https://github.com/microsoft/amplifier-agent/blob/main/docs/concepts/approvals.md),
and, for reusable instructions and named agents,
[skills](https://github.com/microsoft/amplifier-agent/blob/main/docs/concepts/skills.md).

- `tools` is the whole set. Omitted, it is every built-in; `[]` is no tools; keep the
  built-ins and add yours with `[*BUILTIN_TOOLS, mine]` / `[...BUILTIN_TOOLS, mine]`.
  Built-ins run with the host process's permissions.
- Every tool call, including reads and MCP, needs an approval handler or a static
  `"allow"` / `"deny"` policy. `AgentOptions.approvals` wins; otherwise the host's
  `AMPLIFIER_AGENT_APPROVALS` or config file `"approvals"` applies. Without one, tool
  requests fail with `approval_unavailable`. `"allow"` permits every effect, including
  writes and shell; `"deny"` ends the turn at the first tool request with `approval_denied`.
- `workspace` separates stored sessions. It is not a sandbox.
- Caller tools need a unique name, a JSON Schema with `$schema`, and a handler. Report
  known failures with `ToolFailed` and uncertain effects with `ToolOutcomeUnknown`;
  never retry an unknown outcome. A handler returns a string, or a list of text and
  image parts; events describe each image in one line, never its bytes.

## Streaming UI

Use [events](https://github.com/microsoft/amplifier-agent/blob/main/docs/concepts/events.md)
and [usage](https://github.com/microsoft/amplifier-agent/blob/main/docs/concepts/usage.md).
Each turn's stream has one consumer. Correlate tools by `call_id` and approvals by
`request_id`. Render `output_delta` parts or the terminal content, not both. Usage
events replace the previous snapshot; do not sum them. A `progress` event with
`context.compacted` reports compaction. A stream without `terminal` is incomplete.

## Verify

Run one small live turn, one failure path, and cleanup. With tools or streaming, also
exercise an approval denial and a cancellation. Live turns need credentials and cost money.

## Source inspection

Optional, for diagnosing behavior. Keep application code on the public surface; engine
internals and upstream module APIs are not the public interface. There is no `amplifier-agent` CLI.

```bash
git clone --depth 1 --single-branch --branch v0.20.0 \
  https://github.com/microsoft/amplifier-agent.git "$(mktemp -d)/amplifier-agent"
```

Start at `docs/development/architecture.md`. Upstream module revisions are in
`packages/engine/pyproject.toml` and `uv.lock`.

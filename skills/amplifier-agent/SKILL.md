---
name: amplifier-agent
description: Integrate Amplifier Agent v1 into applications using its Python or TypeScript library or OpenAI-compatible HTTP face. Use for installation, sessions, streaming, tools, MCP, skills, approvals, configuration, and integration troubleshooting.
license: MIT
metadata:
  author: microsoft
  repository: https://github.com/microsoft/amplifier-agent
---

# Amplifier Agent integration

Build against the public bindings and their contracts. Start with the application's
language, installed package revision, and required capabilities. Use the matching
documentation before writing API calls; v1 contract versions are distinct from package
versions.

## Find the documentation

Use `docs/` and `contracts/` in a matching Amplifier Agent checkout when available.
Otherwise follow the links below, which target the repository's `v1` branch, or
download that branch as described under [Source inspection](#source-inspection).
Installing this skill copies its directory, not the repository's docs or source.

- Start with [installation](https://github.com/microsoft/amplifier-agent/blob/v1/docs/install.md)
  and [provider credentials](https://github.com/microsoft/amplifier-agent/blob/v1/docs/providers.md).
  Use the `v1` branch for source installs and build artifacts from that checkout;
  do not assume the default package registry release implements v1.
- Python: read the [quickstart](https://github.com/microsoft/amplifier-agent/blob/v1/docs/python/quickstart.md),
  then consult [names](https://github.com/microsoft/amplifier-agent/blob/v1/docs/python/names.md)
  and [reference](https://github.com/microsoft/amplifier-agent/blob/v1/docs/python/reference.md)
  for signatures.
- TypeScript: read the [quickstart](https://github.com/microsoft/amplifier-agent/blob/v1/docs/typescript/quickstart.md),
  then consult [names](https://github.com/microsoft/amplifier-agent/blob/v1/docs/typescript/names.md)
  and [reference](https://github.com/microsoft/amplifier-agent/blob/v1/docs/typescript/reference.md).
  Constructed options use camelCase; received records retain contract spelling,
  such as `session_id` and `call_id`.
- HTTP: read the [quickstart](https://github.com/microsoft/amplifier-agent/blob/v1/docs/http/quickstart.md),
  [reference](https://github.com/microsoft/amplifier-agent/blob/v1/docs/http/reference.md),
  and [limits](https://github.com/microsoft/amplifier-agent/blob/v1/docs/http/limits.md).
  Use a binding for interactive approvals, caller-process tools, durable sessions,
  or the full event stream. HTTP requests carry their own conversation history;
  clients cannot supply tools or change server configuration per request.

Read only the concept pages needed for the integration. The
[contract index](https://github.com/microsoft/amplifier-agent/blob/v1/contracts/README.md)
identifies the governing contract when precise semantics matter. Contracts take
precedence over examples and implementation behavior.

## Build the integration

1. Create the agent with the application's provider, model, and instructions.
   Configuration is captured at construction; recreate the agent after changing
   credentials or settings. Follow [configuration](https://github.com/microsoft/amplifier-agent/blob/v1/docs/configuration.md)
   for precedence and [models](https://github.com/microsoft/amplifier-agent/blob/v1/docs/concepts/models.md)
   for ceiling behavior. Set both provider and model when switching providers.
2. Choose [session persistence](https://github.com/microsoft/amplifier-agent/blob/v1/docs/concepts/sessions.md)
   deliberately. Sessions are durable by default. Preserve the session ID, storage
   root, and configured workspace for resumption; reconstruct tools, credentials,
   and approvals in the new agent. Use ephemeral sessions for disposable work or
   caller-supplied history. Serialize turns on each session.
3. Use `session.run` for a final result or Python `session.start_turn` / TypeScript
   `session.startTurn` for streaming. Follow [turns](https://github.com/microsoft/amplifier-agent/blob/v1/docs/concepts/turns.md)
   for input and lifecycle rules. Seed `history` only before an ephemeral session's
   first accepted turn, with no inherited conversation. Content is text-only.
4. Check both method-level `AgentError` and terminal `TurnResult.state` / `error`.
   Show the error's `code`, `message`, and `remedy`; returned text alone does not
   establish success. See [errors](https://github.com/microsoft/amplifier-agent/blob/v1/docs/concepts/errors.md).
5. Close agent and session handles with context managers or `finally` blocks.
   To stop a streaming turn, call `cancel()` and consume through `terminal`.
   Leaving an event loop does not cancel work. On client disconnect, settle pending
   approval prompts with `cancel`; unresolved callbacks can keep close pending.

## Tools and authority

Read [tools](https://github.com/microsoft/amplifier-agent/blob/v1/docs/concepts/tools.md)
and [approvals](https://github.com/microsoft/amplifier-agent/blob/v1/docs/concepts/approvals.md)
before connecting application effects. Caller tools need unique names, a JSON Schema
with `$schema`, and a handler that runs in the application's process. MCP servers
connect at agent construction. For reusable instructions, named agents, and command
hooks, read [skills](https://github.com/microsoft/amplifier-agent/blob/v1/docs/concepts/skills.md).

- Every tool call needs an approval policy, including reads, MCP calls, and delegated
  effects. Missing policy produces `approval_unavailable`. Choose a handler or a
  static policy that matches the application's authority; `approvals="allow"`
  permits all requested tool effects.
- Caller `tools` adds to the built-ins. An empty list does not disable filesystem or
  shell access. `workspace` separates stored sessions, not host permissions; it is
  not a sandbox or an `AgentOptions` field.
- Report a known failure with `ToolFailed` and an uncertain effect with
  `ToolOutcomeUnknown`. Do not retry effects whose outcome is unknown.
  Tool error recovery is opt-in and does not change approval requirements or turn
  failed tool results into successful ones.

## Streaming and application UI

Use the [event vocabulary](https://github.com/microsoft/amplifier-agent/blob/v1/docs/concepts/events.md)
and [usage semantics](https://github.com/microsoft/amplifier-agent/blob/v1/docs/concepts/usage.md).
Each turn's event stream has one consumer; fan out inside the application if several
components need it. Correlate tools by `call_id` and approvals by `request_id`.
Append `output_delta` parts or render terminal content, without appending both.
Usage events replace the cumulative snapshot; do not sum them. Preserve unknown
extension fields. A stream ending without `terminal` is incomplete.

Verify the application's chosen path with a small turn, its failure handling, and
cleanup. For tools or streaming, also exercise approval refusal and cancellation.
Use the application's existing test facilities; live model checks require configured
credentials and can incur provider charges.

## Source inspection

You may download Amplifier Agent's source and any Amplifier modules it uses to
understand behavior or diagnose an integration. A shallow checkout keeps exploration
small and separate from the application:

```bash
agent_reference_dir=$(mktemp -d)
git clone --depth 1 --single-branch --branch v1 \
  https://github.com/microsoft/amplifier-agent.git "$agent_reference_dir/amplifier-agent"
```

For an existing installation, inspect its matching tag or commit when diagnosing
version-specific behavior. Start at `docs/development/architecture.md` for source
ownership. Find upstream repository URLs and selected revisions in
`packages/engine/pyproject.toml` and the resolved `uv.lock`; clone only the relevant
provider, tool, loop, context, core, or foundation sources into a temporary directory.
Inspect the pinned dependency revision when reproducing installed behavior.

Source inspection is optional. Keep application code on the public Python,
TypeScript, or HTTP surface: private engine modules, subprocess protocols, and
upstream module APIs do not define the integration contract. Build shell workflows
over a binding; Amplifier Agent has no supported `amplifier-agent` CLI.

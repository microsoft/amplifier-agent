# Changelog

## Unreleased

### Changed

- Improve the built-in `bash` tool: longer timeouts, background commands, and fuller guidance for the model.
- Start every session with a default system prompt and environment block when
  [`instructions`](docs/concepts/agents.md#agentoptions) is not set.
- Give `openai` `gpt-6` models a 500,000-token context budget, compacting at 400,000. Requests above OpenAI's
  272,000-token input tier are billed at its long-context rate. See [context budget](docs/providers.md#context-budget).
- Place the compaction notice once, where the view ended when compaction took effect, so later requests extend the
  previous one and keep hitting the provider's prompt cache.
- Retry transient provider failures, such as rate limits, server errors, dropped connections, and timeouts, with
  backoff before any output streams. A failure after output began ends the turn with a retryable
  [`provider_failed`](docs/concepts/errors.md#where-a-failure-shows-up).
- Replay reasoning from earlier turns unchanged until compaction drops it, so the first request of each turn extends
  the previous one and hits the provider's prompt cache. Replayed reasoning now counts toward the compaction estimate.
  See [statelessness](docs/providers.md#statelessness).

## 0.22.0 (2026-10-06)

### Breaking

- Refuse `extra_request_params` entries that set the reasoning effort, such as `reasoning`, `thinking`, or `thinking_config`.
  Remove them and set [`reasoning_effort`](docs/concepts/models.md#reasoning-effort) instead.

### Added

- [`list_providers` and `list_models`](docs/concepts/models.md#discovering-providers-and-models), so an application
  can show each provider's [credential status](docs/providers.md#credential-status) and live model list with no agent.
  The HTTP face serves them under [`/v1/providers`](docs/http/reference.md#providers).
- [`reasoning_effort`](docs/concepts/models.md#reasoning-effort) on agent, session, and turn, from `none` to `max`,
  with `AMPLIFIER_AGENT_REASONING_EFFORT`, `turn_started.reasoning_effort`, and each `list_models`
  record's accepted `reasoning_efforts`.
- [`session.set_model`](docs/concepts/sessions.md#switching-models) (`setModel` in TypeScript) switches a session's
  provider, model, and reasoning effort between turns, keeping the conversation. See
  [switching providers](docs/providers.md#switching-providers).
- `provider`, `model`, and the effective `reasoning_effort` on `SessionRecord`, from `session.info` and `list_sessions`.
- Support for `claude-sonnet-5-5` with the `anthropic` provider.

### Changed

- Default the [model](docs/configuration.md#resolution) to `claude-sonnet-5-5`. Under `claude-opus-5-5`, `economy`
  delegation runs on `claude-sonnet-5-5`.
- Let `anthropic`, `azure-openai`, `chat-completions`, `ollama`, and `github-copilot` requests run without a time limit,
  rather than failing `provider_failed` after 5 to 60 minutes. Cancel the turn to stop a slow request.
- Default the reasoning effort to `"medium"`. Anthropic models that take it now run with adaptive thinking,
  and Gemini 3.x models at thinking level `medium`. OpenAI's own default was already `medium`.
  Name a lower `reasoning_effort` that the model takes to reduce it.
- Treat the agent's `provider`, `model`, and `reasoning_effort` as defaults for new sessions, and honor a
  [model or reasoning effort](docs/concepts/models.md) named for a session or turn even when it is higher, rather than
  failing `selector_rejected`. Internal routing and delegated work still never go above the named model.
- Resume a durable session on its saved provider, model, and reasoning effort rather than the resuming agent's,
  and fork a session onto its current selection.
- Require Starlette 1.3.1 or later in `amplifier-agent-http`.

### Fixed

- Fix a rare segfault when a program exits under CPU load.
- Stop `github-copilot` models from repeating tool calls that already completed.

## 0.21.0 (2026-10-05)

### Breaking

- Replace `storage` and `workspace` with [`sessions_directory`](docs/configuration.md#sessions-directory).
  To keep existing sessions, set it to `<old storage>/workspaces/<old workspace>`.
- Default `tool_error_policy` to `"continue"`. Set `"stop"` for the old behavior.
- Name the observation capture's project after the sessions directory, and drop `"workspace"` from `metadata.json`.

### Added

- [`working_directory`](docs/concepts/agents.md#working-directory), `additional_directories`, and `environment` options,
  so one process can run agents in different folders with different credentials.
- Support for `gpt-6.1-sol` with the `openai` provider.

### Fixed

- Stop running shell commands promptly when a turn is cancelled.

## 0.20.0 (2026-10-02)

### Breaking

- New API for the Python and TypeScript bindings and the HTTP face, aligned with the [contracts](contracts/README.md).
  Nothing from 0.17 carries over. Start from [the docs](docs/index.md).

# Changelog

## Unreleased

### Breaking

- Refuse `extra_request_params` entries that set the reasoning effort, such as `reasoning`, `thinking`, or `thinking_config`.
  Remove them and set [`reasoning_effort`](docs/concepts/models.md#reasoning-effort) instead.

### Added

- [`list_providers` and `list_models`](docs/concepts/models.md#discovering-providers-and-models), so an application
  can show each provider's [credential status](docs/providers.md#credential-status) and live model list with no agent.
  The HTTP face serves them under [`/v1/providers`](docs/http/reference.md#providers).
- [`reasoning_effort`](docs/concepts/models.md#reasoning-effort) on agent, session, and turn, a ceiling from `none` to `max`,
  with `AMPLIFIER_AGENT_REASONING_EFFORT`, `turn_started.reasoning_effort`, and each `list_models`
  record's accepted `reasoning_efforts`.

### Changed

- Default the reasoning effort to `"medium"`. Anthropic models that take it now run with adaptive thinking,
  and Gemini 3.x models at thinking level `medium`. OpenAI's own default was already `medium`.
  Name a lower `reasoning_effort` that the model takes to reduce it.

### Fixed

- Fix a rare segfault when a program exits under CPU load.

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

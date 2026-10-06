# Changelog

## Unreleased

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

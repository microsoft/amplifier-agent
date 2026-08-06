# amplifier-agent documentation

User documentation for the **amplifier-agent** CLI and the **`amplifier-agent-ts`** TypeScript SDK for hosts.

> All claims in this documentation set were verified against the live binary
> running in an isolated Digital Twin environment, and against the source
> code in this repository. If you find a discrepancy, prefer the actual
> behavior of the binary — those checks were the ground truth.

Verified against `amplifier-agent 0.5.2` (wire protocol `0.3.0`) and `amplifier-agent-ts 0.6.2`.

## CLI

Start here if you want to **run** the agent.

| Page | What it covers |
|---|---|
| [Overview](overview.md) | What amplifier-agent is, where it fits, what it is not |
| [Quickstart](quickstart.md) | Install, configure, and run your first turn in 5 minutes |
| [Installation](installation.md) | Install via `uv`, update, uninstall, sub-binaries |
| [CLI reference](cli-reference.md) | Every subcommand, every flag, with verified examples |
| [Configuration](configuration.md) | The host config JSON schema, every key, validation rules |
| [Environment variables](environment-variables.md) | All `AMPLIFIER_AGENT_*` and provider env vars |
| [Sessions and storage](sessions-and-storage.md) | Workspaces, session files, resume/fresh, the on-disk layout |
| [Output formats](output-formats.md) | `--output` and `--display`, JSON envelope schema, wire events, exit codes |

## TypeScript SDK (for hosts)

Start here if you want to **embed** the agent in your own app or IDE extension.

| Page | What it covers |
|---|---|
| [Overview](../typescript/overview.md) | What the SDK does, the subprocess-per-turn model |
| [Quickstart](../typescript/quickstart.md) | `npm install`, your first `spawnAgent()` call |
| [API reference](../typescript/api-reference.md) | `spawnAgent`, `SessionHandle`, every public export |
| [Events](../typescript/events.md) | `DisplayEvent` union, wire event shapes |
| [Advanced](../typescript/advanced.md) | Approval handling, MCP config, env allowlist, custom binary paths, models list |

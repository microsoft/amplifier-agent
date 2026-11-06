# Amplifier Agent ACP face

The Amplifier Agent Python binding over the [Agent Client Protocol](https://agentclientprotocol.com), for editors that
start their agent as a subprocess. The `amplifier-agent-acp` executable takes no arguments, speaks ACP on
stdin and stdout, and reads its settings from the host configuration in its environment.

Follow the [installation guide](https://github.com/microsoft/amplifier-agent/blob/main/docs/install.md#acp-face). To
run it from an editor without installing it first, configure the editor to start:

```bash
uvx --from "git+https://github.com/microsoft/amplifier-agent@v0.25.0#subdirectory=packages/acp" amplifier-agent-acp
```

with this environment:

```bash
AMPLIFIER_AGENT_PROVIDER=anthropic
AMPLIFIER_AGENT_MODEL=claude-sonnet-5-5
ANTHROPIC_API_KEY="..."
```

Each ACP session gets its own durable session, working in the session's `cwd`. Every tool call is put to the editor as
a permission request; `AMPLIFIER_AGENT_APPROVALS=deny` denies them all without asking. Results carry a diff for each
file a built-in tool changed. See the
[ACP quickstart](https://github.com/microsoft/amplifier-agent/blob/main/docs/acp/quickstart.md) for editor setup and
[ACP limits](https://github.com/microsoft/amplifier-agent/blob/main/docs/acp/limits.md) for what the face cannot carry.

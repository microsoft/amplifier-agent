# ACP quickstart

Use Amplifier Agent as the agent inside an editor that speaks the
[Agent Client Protocol](https://agentclientprotocol.com). The editor starts `amplifier-agent-acp` as a
subprocess and shows its replies, tool calls, diffs, and approval requests in its own interface.

This is one face, not the product. Read [limits](limits.md) before you build on it.

## Settings

The face is itself a host. It takes no arguments; its settings are the
[host configuration](../configuration.md) and the provider credentials in its process environment. The editor chooses
only the working directory, extra directories, MCP servers, and each session's model.

```
AMPLIFIER_AGENT_PROVIDER    the provider of new sessions
AMPLIFIER_AGENT_MODEL       the model of new sessions
AMPLIFIER_AGENT_APPROVALS   deny refuses every tool call without asking
ANTHROPIC_API_KEY           the selected provider's credentials; see providers.md
```

`~/.amplifier-agent/config.json`, or the file named by `AMPLIFIER_AGENT_CONFIG`, works as for any host. See
[install](../install.md#acp-face) for other ways to obtain the executable.

## Add the agent to your editor

ACP editors take a custom agent as a command, its arguments, and its environment:

```
command   uvx
args      --from git+https://github.com/microsoft/amplifier-agent@v0.25.0#subdirectory=packages/acp amplifier-agent-acp
env       AMPLIFIER_AGENT_PROVIDER=anthropic
          AMPLIFIER_AGENT_MODEL=claude-sonnet-5-5
          ANTHROPIC_API_KEY=...
```

The agent gets provider credentials from its process environment or from `env`, not from keys the editor stores for its
own models. See [providers](../providers.md) for each provider's variables.

When the editor starts the agent on a remote machine or inside WSL, give `command` an absolute path, such as
`/home/you/.local/bin/uvx`; a remote login shell may not have `~/.local/bin` on `PATH`.

## First prompt

Ask something in the thread:

```
Find the Python files here and summarize what each one does.
```

The agent works in the project's directory. Replies stream as they are written, reasoning shows as thoughts, and each
tool call shows as an entry with its kind and title, such as `Read src/app.py` or the first line of a shell command.
Each file a built-in tool changed shows as a diff. Stopping the thread cancels the turn.

Prompts may carry text, images, files and selections attached as context, and links to files. See
[prompt content](reference.md#prompt-content).

## Approvals

Each tool call waits for your answer:

```
Allow                                  run this call
Always allow <scope> in this session   run it, and later calls with the same tool, or the same shell command
Deny                                   do not run it; the model is told and continues
```

An unanswered request expires at the [approval deadline](../concepts/approvals.md#when-the-answer-is-not-allow) and
the turn fails. With `AMPLIFIER_AGENT_APPROVALS=deny`, every tool call is denied without asking and the model answers
without its tools.

## Models

The thread's model picker lists `<provider>/<model>` for every installed provider with credentials, and a reasoning
effort picker when the model has reasoning efforts. A change applies from the next prompt and keeps the conversation.
See [switching models](../concepts/sessions.md#switching-models).

## History

Sessions are durable. An editor that reopens a thread loads it by id: the face replays each earlier prompt and reply,
and the conversation continues where it stopped. Sessions live under the
[sessions directory](../configuration.md#sessions-directory), and any binding can resume them by the same id.

## Missing credentials

When the selected provider has no credentials, starting a session fails with code `-32000`, naming the variables to
set. Editors that support ACP auth methods show the `provider-credentials` method there. Set the variables in the
agent's `env` and start a new thread.

## Next

```
reference.md   methods, capabilities, updates, tool kinds, config options, and errors
limits.md      what this face cannot carry
```

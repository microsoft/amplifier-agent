# Quickstart

Install amplifier-agent, configure a provider, and run your first turn — in five minutes.

## Prerequisites

- **Python 3.12 or newer**. (3.11 is the floor inside the engine, but the package itself requires 3.12.)
- **[uv](https://docs.astral.sh/uv/)** for installation. If you don't have it: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **An API key** for at least one supported provider: Anthropic, OpenAI, Azure OpenAI, or Ollama.

## 1. Install

```bash
uv tool install --from git+https://github.com/microsoft/amplifier-agent amplifier-agent
```

This installs the `amplifier-agent` binary on your PATH. Verify:

```bash
$ amplifier-agent --version
amplifier-agent, version 0.5.2
```

## 2. Set a provider API key

Export the env var for the provider you want to use:

```bash
export ANTHROPIC_API_KEY=sk-ant-...        # for Anthropic
# or
export OPENAI_API_KEY=sk-...               # for OpenAI
# or
export AZURE_OPENAI_API_KEY=...            # for Azure OpenAI
# or
export OLLAMA_HOST=http://localhost:11434  # for a local Ollama daemon
```

The default provider is **Anthropic**. To use a different provider, you need a host config file — see [step 5](#5-configure-a-different-provider-optional).

## 3. Check your install

```bash
$ amplifier-agent doctor
[ OK ] python: 3.12.3
[ OK ] bundle default_provider: anthropic
[ OK ] config home: /Users/you/.amplifier-agent/config
[ OK ] cache home: /Users/you/.amplifier-agent/cache
[ OK ] state home: /Users/you/.amplifier-agent/state
[ OK ] bundle modules: context-simple, tool-mcp present; hooks-logging absent
[ OK ] wire_approval_provider: subclass check passed; all three error codes present
[ OK ] session_store: write/read roundtrip in tempdir succeeded
[ OK ] mcp module: importable
[INFO] bundle cache: needs prepare (/Users/you/.amplifier-agent/cache/prepared/0.5.2/da41ba6300040dd9)
```

The `[INFO] bundle cache: needs prepare` is normal on a fresh install — the bundle's modules get installed lazily on first `run`.

## 4. Run your first turn

```bash
$ amplifier-agent run "Reply with only the word: pong" --session-id smoke-1 -y
pong
```

What happened:

- `--session-id smoke-1` names the session so you can resume it.
- `-y` auto-approves any tool the model wants to call. (Required in non-interactive contexts; without it the run will refuse to start. See [approval](configuration.md#approval).)
- The default `--output text` prints the reply to stdout. Use `--output json` to get the full envelope (see [output formats](output-formats.md)).

### Resume the session

```bash
$ amplifier-agent run "What was your last reply?" --session-id smoke-1 --resume -y
My last reply was "pong".
```

The conversation history persisted between invocations under `~/.amplifier-agent/state/workspaces/<auto-derived>/sessions/smoke-1/`. See [Sessions and storage](sessions-and-storage.md) for the layout.

## 5. Configure a different provider (optional)

Create a host config file:

```bash
mkdir -p ~/.config/amplifier-agent
cat > ~/.config/amplifier-agent/config.json <<'EOF'
{
  "provider": {
    "module": "openai",
    "config": {
      "default_model": "gpt-4o"
    }
  },
  "approval": {
    "mode": "yes"
  }
}
EOF
```

Then pass it on each run:

```bash
amplifier-agent run "Hello" --config ~/.config/amplifier-agent/config.json --session-id s1
```

Or set the env var once for your shell:

```bash
export AMPLIFIER_AGENT_CONFIG=~/.config/amplifier-agent/config.json
amplifier-agent run "Hello" --session-id s1
```

The full schema is documented in [Configuration](configuration.md).

## What's next

- Explore the full CLI: [CLI reference](cli-reference.md)
- Understand session and workspace layout: [Sessions and storage](sessions-and-storage.md)
- Pick a model: [`amplifier-agent models list`](cli-reference.md#models-list)
- Embed amplifier-agent in your own app: [TypeScript SDK](../typescript/overview.md)

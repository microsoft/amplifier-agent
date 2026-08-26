# Ecosystem

Applications that run on `amplifier-agent`. Each one is a different answer to "what does a host look like", and each is a working reference for anyone writing their own integration.

## [amplifier-app-opencode](https://github.com/microsoft/amplifier-app-opencode)

Runs the [opencode](https://opencode.ai) coding TUI on top of a local `amplifier-agent`. There is no fork: `amplifier-opencode` discovers which models your engine serves, writes an opencode config from that discovery, and launches stock opencode against it, re-syncing on every launch.

```bash
curl -fsSL https://raw.githubusercontent.com/microsoft/amplifier-app-opencode/main/install.sh | bash
amplifier-opencode launch
```

**Integration shape:** HTTP face. `amplifier-app-opencode` spawns `amplifier-agent serve chat-completions` and registers it as an opencode provider, so opencode itself only ever speaks the OpenAI protocol.

## [amplifier-app-paperclip](https://github.com/microsoft/amplifier-app-paperclip)

Sets up [paperclip](https://github.com/paperclipai/paperclip) with the `amplifier_local` adapter so paperclip agents run on the engine. The paperclip server invokes `amplifier-agent` once per turn.

**Integration shape:** per-turn subprocess with one host config file per agent instance. This is the reference for the multi-tenant pattern described in the [integration guide](INTEGRATION.md#per-instance-configuration).

## [amplifier-app-nanoclaw](https://github.com/microsoft/amplifier-app-nanoclaw)

Runs [NanoClaw](https://nanoclaw.dev), which routes chat channels into per-agent Docker containers, with `amplifier-agent` as the agent backend instead of its default. Selecting it writes `NANOCLAW_DEFAULT_PROVIDER=amplifier-agent`, which lets you choose between Anthropic, OpenAI, Azure OpenAI, and Ollama per agent group while keeping NanoClaw's sandbox, channels, and security model unchanged.

**Integration shape:** a Node host with the engine running inside each per-agent container.

## Building your own

Start at the [integration guide](INTEGRATION.md). It opens with embedding the engine library, the primary surface, then covers the wrappers for hosts that cannot embed (TypeScript SDK, Python SDK, HTTP face, raw CLI contract), and ends with a checklist for each path.
Each reaches the engine out-of-process for its own reason: opencode is an existing harness, nanoclaw is Node, and paperclip runs the engine in a container. A new host should reach for the library first.

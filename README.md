<h1 align="center">Amplifier Agent</h1>

<p align="center">
  <a href="docs/INSTALL.md">Install</a> &nbsp;&bull;&nbsp;
  <a href="docs/INTEGRATION.md">Integration guide</a> &nbsp;&bull;&nbsp;
  <a href="docs/CONFIGURATION.md">Configuration</a> &nbsp;&bull;&nbsp;
  <a href="docs/CLI.md">CLI reference</a> &nbsp;&bull;&nbsp;
  <a href="docs/ECOSYSTEM.md">Who uses it</a>
</p>

<p align="center">
  <a href="https://github.com/microsoft/amplifier-agent/actions/workflows/ci.yml"><img src="https://github.com/microsoft/amplifier-agent/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://github.com/microsoft/amplifier-agent/releases"><img src="https://img.shields.io/github/v/release/microsoft/amplifier-agent" alt="Release"></a>
  <a href="https://www.npmjs.com/package/amplifier-agent-ts"><img src="https://img.shields.io/npm/v/amplifier-agent-ts?label=amplifier-agent-ts" alt="npm"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue" alt="MIT License"></a>
</p>

---

Need an AI agent in your project, app, or service? Tell your coding agent:

```
Use amplifier-agent for the AI agent parts of this project.
Start here: https://github.com/microsoft/amplifier-agent/blob/main/docs/INTEGRATION.md
```

Or install our skill, so your coding agent knows how to install and integrate the engine:

```bash
npx skills add microsoft/amplifier-agent
```

Alternatively, copy [`skills/amplifier-agent/SKILL.md`](skills/amplifier-agent/SKILL.md) into your agent's skills directory.


**`amplifier-agent`** is an agent engine that other software runs on. Give it a prompt and it runs the full loop, with tools, sub-agents, skills, and MCP, and returns a result.
Anything that can spawn a subprocess can use it: a shell script, a Node app, a Python service, a chat bot, an IDE plugin. 
Python applications can embed the engine library in-process instead.

Public integrations run opencode, paperclip, and NanoClaw on it: see [who has integrated it](docs/ECOSYSTEM.md).

## What comes with it

`amplifier-agent` ships with:

- Five providers behind one interface: Anthropic, OpenAI, Azure OpenAI, Ollama, and GitHub Copilot, with credentials read from the environment
- Role-based model routing, so a sub-agent gets a model matched to its job rather than the frontier model for everything, re-matched when you switch providers
- Context management that keeps long sessions running, compacting history before it overruns the window
- Tools for filesystem, bash, web, search, todo, and MCP
- Sub-agent delegation, skills, and modes

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/microsoft/amplifier-agent/main/install.sh | bash
```

Installs the latest release and primes the bundle cache so your first run is instant. Requires [`uv`](https://docs.astral.sh/uv/) and `curl`; the installer tells you what is missing rather than bootstrapping silently.

To review the script first, pin a version, install without the script, or uninstall, see [`docs/INSTALL.md`](docs/INSTALL.md).

## Quick start

Set a provider key, then run a turn. The `-y` auto-approves tool calls, which is required in headless mode.

```bash
export ANTHROPIC_API_KEY=sk-ant-...

amplifier-agent run -y "Summarize the README of github.com/microsoft/amplifier"
```

Provider is auto-detected from the environment in this order, first match wins:

```
ANTHROPIC_API_KEY  >  OPENAI_API_KEY  >  AZURE_OPENAI_API_KEY + AZURE_OPENAI_ENDPOINT  >  OLLAMA_HOST
```

For "set once, works everywhere" instead of editing shell rc files, persist credentials to `~/.amplifier-agent/credentials.json` (mode `0600`). Resolution stays env-first, so an exported variable still wins and existing workflows keep working:

```bash
amplifier-agent auth set anthropic sk-ant-...
amplifier-agent auth status              # diagnose env-vs-file precedence per provider
amplifier-agent models list              # enumerate available models from providers
```

Full precedence rules, GitHub Copilot's environment-only caveat, and the host config file schema are in [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md).

## Use it from your code

Both SDKs are BYO-engine: they spawn the `amplifier-agent` binary on your `PATH` and expose a typed async API. All inference, tool execution, and session state live in the Python engine.

**TypeScript / Node.js** ([`amplifier-agent-ts`](https://www.npmjs.com/package/amplifier-agent-ts), Node 20+, zero runtime deps)

```typescript
import { spawnAgent } from 'amplifier-agent-ts';

const session = await spawnAgent({ lifecycle: 'one-shot', sessionId: 'chat-42' });

for await (const event of session.submit('Hello, agent.')) {
  if (event.type === 'result') console.log(event.text);
}
```

**Python** ([`amplifier-agent-py`](wrappers/python-py/), zero runtime deps)

```python
from amplifier_agent_py import AaaError, spawn_agent_sync

with spawn_agent_sync(session_id="chat-42", approval={"mode": "yes"}) as handle:
    for event in handle.submit("Hello, agent."):
        if event.type == "result":
            print(event.text)
        elif event.type == "error":
            raise AaaError(event.code, event.message)
```

Python hosts can skip the subprocess entirely and embed `amplifier_agent_lib` in-process. Node hosts, HTTP callers, and anyone building their own adapter should start at the [**integration guide**](docs/INTEGRATION.md), which covers all five surfaces, the wire protocol, session continuity, and approval policy for services.

## Architecture at a glance

Amplifier-agent is standalone. You do not need the Amplifier CLI, bundles, or any other Amplifier repository to use it.

```
Host Application                              ← your code
    ↓
Adapter (host-specific glue)                  ← per-host integration
    ↓
Language Wrapper (TypeScript or Python)       ← typed SDK
    ↓ subprocess (argv in / JSON envelope out, or in-process)
amplifier-agent CLI                           ← this repo
    ↓ (in-process)
amplifier_agent_lib (engine library)          ← this repo
```

The CLI binary is a thin I/O adapter on top of `amplifier_agent_lib`. The library is transport-free, so Python hosts can skip the subprocess entirely.

## Documentation

| Document | Covers |
|---|---|
| [Install](docs/INSTALL.md) | Install, pin, update, uninstall, offline and CI notes |
| [Integration guide](docs/INTEGRATION.md) | **Start here to embed the engine.** TypeScript SDK, Python SDK, in-process library, HTTP face, wire protocol |
| [Configuration](docs/CONFIGURATION.md) | Providers, credentials, approval policy, host config file |
| [CLI reference](docs/CLI.md) | Every command and flag, output and display modes, session continuity, skills and modes |
| [Architecture](docs/ARCHITECTURE.md) | How the layers fit together and what runs where |
| [Specifications](docs/SPEC.md) | Normative contracts: wire protocol, envelope, host config, CLI, HTTP face |
| [Ecosystem](docs/ECOSYSTEM.md) | Applications built on amplifier-agent |
| [Known issues](ISSUES.md) | Tracked defects and current limitations |

## Built with amplifier-agent

See [`docs/ECOSYSTEM.md`](docs/ECOSYSTEM.md) for applications that run on the engine, and the integration shape each one uses.

## Development

This repo is developed spec, e2e, and eval driven: there is no unit test tier, and the contract suite runs the real CLI and HTTP server against a realistic install inside an isolated container. [`DEVELOPMENT.md`](DEVELOPMENT.md) covers first-time setup, the `make` command surface, the four development skills, and the DTU and Gitea prerequisites those skills need.

## Contributing

> [!NOTE]
> This project is not currently accepting external contributions, but we're actively working toward opening this up. We value community input and look forward to collaborating in the future. For now, feel free to fork and experiment!

Most contributions require you to agree to a
Contributor License Agreement (CLA) declaring that you have the right to, and actually do, grant us
the rights to use your contribution. For details, visit [Contributor License Agreements](https://cla.opensource.microsoft.com).

When you submit a pull request, a CLA bot will automatically determine whether you need to provide
a CLA and decorate the PR appropriately (e.g., status check, comment). Simply follow the instructions
provided by the bot. You will only need to do this once across all repos using our CLA.

This project has adopted the [Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/).
For more information see the [Code of Conduct FAQ](https://opensource.microsoft.com/codeofconduct/faq/) or
contact [opencode@microsoft.com](mailto:opencode@microsoft.com) with any additional questions or comments.

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft
trademarks or logos is subject to and must follow
[Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/legal/intellectualproperty/trademarks/usage/general).
Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship.
Any use of third-party trademarks or logos are subject to those third-party's policies.

## License

MIT. See [`LICENSE`](LICENSE).

---

🤖 Built with [Amplifier](https://github.com/microsoft/amplifier).

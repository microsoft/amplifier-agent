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

**`amplifier-agent`** is a thin CLI wrapping the [Amplifier](https://github.com/microsoft/amplifier) kernel as a per-turn stdio subprocess. Anything that can spawn a subprocess (a shell script, a Node app, a Python script, a chat bot, an IDE plugin) can use it as an agentic AI backend.

## What it is

A single binary that:

- **Accepts a prompt and returns a result** (one turn per invocation): `amplifier-agent run -y "your prompt"`
- **Emits one JSON envelope on stdout per invocation** when `--output json` is set. Wrappers spawn one process per turn and pass `--session-id` for continuity

It is *not* a server, daemon, or long-lived service. Each invocation is a fresh process that runs one turn and exits. Multi-turn conversations are managed at the wrapper or session-ID layer, not inside a persistent process.

The engine library inside (`amplifier_agent_lib`) is transport-free Python that any Python app can also embed in-process. No subprocess needed.

## Why

Existing AI agent infrastructure assumes you're building a chat product. `amplifier-agent` is the opposite: it's an *engine you point other software at*. The CLI is the universal adapter. Wherever you can shell out, you can use Amplifier.

The wire protocol is intentionally simple: the engine takes a single invocation (argv + env), runs one turn, and writes one JSON result envelope to stdout. Wrapper SDKs (TypeScript and Python) handle spawning, result parsing, and session continuity on top.

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

amplifier-agent is one layer of the larger Amplifier ecosystem:

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
    ↓
Amplifier Kernel (amplifier-core, amplifier-foundation)
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

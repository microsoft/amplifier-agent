<h1 align="center">Amplifier Agent</h1>

<p align="center">
  <a href="docs/index.md">Documentation</a> &nbsp;&bull;&nbsp;
  <a href="docs/install.md">Install</a> &nbsp;&bull;&nbsp;
  <a href="docs/python/quickstart.md">Python</a> &nbsp;&bull;&nbsp;
  <a href="docs/typescript/quickstart.md">TypeScript</a> &nbsp;&bull;&nbsp;
  <a href="docs/http/quickstart.md">HTTP</a>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue" alt="MIT License"></a>
</p>

---

**Amplifier Agent** is a library you embed in your application, in Python or TypeScript.
Name a model, hand it tools, give it a task. It reasons, acts, and reports back, emitting
a typed event for everything it does along the way.

The tools decide what it is for. A filesystem and a shell make it a coding agent. Your
deployment API makes it a release agent.

## Install

Install the Python binding from the upstream `v1` branch. Requires Python 3.12 or
newer, [uv](https://docs.astral.sh/uv/), and Git. Run this in your application
directory (`uv init` first for a new project):

```bash
uv add "amplifier-agent @ git+https://github.com/microsoft/amplifier-agent#subdirectory=packages/python" --branch v1
```

- **TypeScript:** install the [`@microsoft/amplifier-agent` library](docs/install.md#typescript).
  Requires Node 22 and Linux x86-64 with glibc 2.35 or newer, including a compatible
  WSL2 distribution.
- **HTTP:** install the separate [`amplifier-agent-http` service](docs/install.md#http-face)
  to serve an OpenAI-compatible API.

## Quick start

Set `ANTHROPIC_API_KEY` in your environment. Save this as `hello.py` in your
application directory and run `uv run python hello.py`:

```python
import asyncio
from amplifier_agent import create_agent, AgentOptions, SessionOptions, TurnInput, TextPart

async def main():
    async with await create_agent(AgentOptions(
        provider="anthropic",
        model="claude-sonnet-5",
    )) as agent:
        session = await agent.create_session(SessionOptions(persistence="ephemeral"))
        result = await session.run(TurnInput(content=[TextPart("Say hello.")]))
        if result.error is not None:
            raise RuntimeError(f"{result.error.message} {result.error.remedy}")
        print("".join(part.text for part in result.content or []))

asyncio.run(main())
```

`run` waits for the turn. `start_turn` hands you the same turn as a stream of events, so
you can watch reasoning, tool calls, and output as they happen.

The TypeScript binding provides equivalent operations, records, and errors with
TypeScript naming. See the [TypeScript quickstart](docs/typescript/quickstart.md).

This example uses an ephemeral session. Sessions are durable by default; retain the
session ID to [resume one later](docs/concepts/sessions.md). Tool execution needs
an explicit [approval policy](docs/concepts/approvals.md), including file reads.

## What comes with it

- Provider configuration and credentials supplied by the host. See
  [providers](docs/providers.md).
- A model ceiling that execution never exceeds. See [models](docs/concepts/models.md).
- Tools you write and we call, tools that come with the agent, and MCP servers, all
  resolving through one call path. See [tools](docs/concepts/tools.md).
- Your veto over every effect, before it happens. See
  [approvals](docs/concepts/approvals.md).
- Opt-in [tool error recovery](docs/concepts/tools.md#recovering-within-a-turn)
  that lets the agent continue while preserving failed and unknown outcomes.

## How it fits together

```
  your Python or TypeScript application ---> binding ---> engine
  your HTTP client ---> HTTP face ---> Python binding ---> engine
```

**Binding.** The library you install and call, one per language. This is the whole of
what you build against, and it is what the [contracts](contracts/README.md) freeze.

**Engine.** Coordinates sessions, model requests, and approved tool work behind the
bindings. Its implementation can change without changing the contracted public API.

**Face.** A network endpoint projecting part of the binding's surface, for callers who
cannot embed a library. Point an OpenAI-compatible client at a different base URL and get
an agent instead of a model. A face carries less than a binding does and
[says what it drops](docs/http/limits.md).

Bindings are equivalent: same operations, same events, same failures. A renderer written
once against the event vocabulary is correct against all of them.

## Shell integration

Amplifier Agent has no `amplifier-agent` command. Build shell workflows by calling
the Python or TypeScript binding from your own script. The separate
[`amplifier-agent-face` service](docs/http/quickstart.md) starts the HTTP API.

## Agent skill

Install the [integration skill](skills/amplifier-agent/SKILL.md) in your application
project to help coding agents use the libraries, contracts, and documentation:

```bash
npx skills add https://github.com/microsoft/amplifier-agent/tree/v1
```

This installs from the `v1` branch. See [skill installation](docs/install.md#coding-agent-skill)
for using a local checkout.

## Documentation

```
docs/index.md               start here
docs/install.md             install any surface
docs/concepts/              what everything means, one page per idea
docs/python/                Python spelling and reference
docs/typescript/            TypeScript spelling and reference
docs/http/                  the OpenAI-compatible face, and its limits
docs/configuration.md       knobs settable outside code, and how they resolve
docs/versioning.md          what may change under you, and what may not
```

[`contracts/`](contracts/README.md) is the normative surface: what you may rely on, and
what we keep the right to change underneath you. The documentation explains it, and where
the two disagree, the contracts win.

To verify a checkout without API keys, run:

```bash
uv run --all-packages python scripts/verify.py
```

The script reports Python and HTTP units and public APIs, engine units, provider
integrations, and verification checks with diagnostic logs. See
[development checks](docs/development/checks.md#quick-verification)
for live-provider verification and the separate TypeScript and full conformance gates.

## Contributing

> [!NOTE]
> This project is not currently accepting external contributions, but we're actively working toward opening this up. We value community input and look forward to collaborating in the future. For now, feel free to fork and experiment!

Most contributions require you to agree to a
Contributor License Agreement (CLA) declaring that you have the right to, and actually do, grant us
the rights to use your contribution. For details, visit [Contributor License Agreements](https://cla.opensource.microsoft.com).

When you submit a pull request, a CLA bot will automatically determine whether you need to provide
a CLA and decorate the PR appropriately (e.g., status check, comment). Simply follow the instructions
provided by the bot. You will only need to do this once across all repos using our CLA.

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

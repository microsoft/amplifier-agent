<h1 align="center">Amplifier Agent</h1>

<p align="center">
  <a href="docs_v1/index.md">Documentation</a> &nbsp;&bull;&nbsp;
  <a href="docs_v1/install.md">Install</a> &nbsp;&bull;&nbsp;
  <a href="docs_v1/python/quickstart.md">Python</a> &nbsp;&bull;&nbsp;
  <a href="docs_v1/typescript/quickstart.md">TypeScript</a> &nbsp;&bull;&nbsp;
  <a href="docs_v1/http/quickstart.md">HTTP</a>
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

- **TypeScript:** install the [`@microsoft/amplifier-agent` library](docs_v1/install.md#typescript).
  Requires Node 22 and Linux x86-64 with glibc 2.35 or newer, including a compatible
  WSL2 distribution.
- **HTTP:** install the separate [`amplifier-agent-http` service](docs_v1/install.md#http-face)
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

The TypeScript API is the same agreement spelled the way TypeScript spells things. See
the [TypeScript quickstart](docs_v1/typescript/quickstart.md).

This example uses an ephemeral session. Sessions are durable by default; retain the
session ID to [resume one later](docs_v1/concepts/sessions.md). Tool execution needs
an explicit [approval policy](docs_v1/concepts/approvals.md), including file reads.

## What comes with it

- Provider configuration and credentials supplied by the host. See
  [providers](docs_v1/providers.md).
- A model ceiling that execution never exceeds. See [models](docs_v1/concepts/models.md).
- Tools you write and we call, tools that come with the agent, and MCP servers, all
  resolving through one call path. See [tools](docs_v1/concepts/tools.md).
- Your veto over every effect, before it happens. See
  [approvals](docs_v1/concepts/approvals.md).
- Opt-in [tool error recovery](docs_v1/concepts/tools.md#recovering-within-a-turn)
  that lets the agent continue while preserving failed and unknown outcomes.

## How it fits together

```
  your application ---> binding ---,
                                    +---> engine
  your HTTP client ---> face    ---'
```

**Binding.** The library you install and call, one per language. This is the whole of
what you build against, and it is what the [contracts](contracts/README.md) freeze.

**Engine.** What runs the agent behind the binding. It is ours. You never call it, name
it, or learn what it is written in, so we can replace it without that being an event in
your life.

**Face.** A network endpoint projecting part of the binding's surface, for callers who
cannot embed a library. Point an OpenAI-compatible client at a different base URL and get
an agent instead of a model. A face carries less than a binding does and
[says what it drops](docs_v1/http/limits.md).

Bindings are equivalent: same operations, same events, same failures. A renderer written
once against the event vocabulary is correct against all of them.

## There is no command line

Amplifier Agent is a library, and there is no `amplifier-agent` command to script against. A
command line good enough to depend on becomes the surface everyone integrates against,
and argv cannot evolve the way a typed interface can. Anything you want to run from a
shell, you write over a binding, in your own repo. The separate
[`amplifier-agent-face` service](docs_v1/http/quickstart.md) serves the HTTP API.

## Documentation

```
docs_v1/index.md               start here
docs_v1/install.md             install any surface
docs_v1/concepts/              what everything means, one page per idea
docs_v1/python/                Python spelling and reference
docs_v1/typescript/            TypeScript spelling and reference
docs_v1/http/                  the OpenAI-compatible face, and its limits
docs_v1/configuration.md       knobs settable outside code, and how they resolve
docs_v1/versioning.md          what may change under you, and what may not
```

[`contracts/`](contracts/README.md) is the normative surface: what you may rely on, and
what we keep the right to change underneath you. The documentation explains it, and where
the two disagree, the contracts win.

To verify a checkout without API keys, run:

```bash
uv run --all-packages python scripts/verify.py
```

The script reports Python/HTTP, session, approval, skill, and three-provider checks
with diagnostic logs. See [development checks](docs_v1/development/checks.md#quick-verification)
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

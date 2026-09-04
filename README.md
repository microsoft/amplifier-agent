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

```bash
uv add git+https://github.com/microsoft/amplifier-agent   # Python 3.12+
npm install @microsoft/amplifier-agent                    # Node 20+
```

Pinning a tag, installing from the package index, and running the HTTP server are in
[`docs_v1/install.md`](docs_v1/install.md).

## Quick start

Set a provider credential in your environment, then run a turn.

```python
import asyncio
from amplifier_agent import create_agent, AgentOptions, TurnInput, TextPart

async def main():
    async with await create_agent(AgentOptions(
        provider="anthropic",
        model="claude-sonnet-5",
    )) as agent:
        session = await agent.create_session()
        result = await session.run(TurnInput(content=[TextPart("Say hello.")]))
        print(result.state, result.content[0].text)

asyncio.run(main())
```

`run` waits for the turn. `start_turn` hands you the same turn as a stream of events, so
you can watch reasoning, tool calls, and output as they happen.

The TypeScript API is the same agreement spelled the way TypeScript spells things. See
the [TypeScript quickstart](docs_v1/typescript/quickstart.md).

## What comes with it

- Nine providers behind one interface, credentials read from your environment or a
  device-code sign-in. See [providers](docs_v1/providers.md).
- Role-based model selection below the model you name, so a sub-agent gets a model
  matched to its job. The model you name is a ceiling, never exceeded.
- Context management that keeps long sessions running past the window.
- Tools you write and we call, tools that come with the agent, and MCP servers, all
  resolving through one call path. See [tools](docs_v1/concepts/tools.md).
- Your veto over every effect, before it happens. See
  [approvals](docs_v1/concepts/approvals.md).
- Sub-agent delegation, skills, and modes.

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

Amplifier Agent is a library, and there is no command for you to script against. A
command line good enough to depend on becomes the surface everyone integrates against,
and argv cannot evolve the way a typed interface can. Anything you want to run from a
shell, you write over a binding, in your own repo.

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

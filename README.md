<h1 align="center">Amplifier Agent</h1>

<p align="center">
  <a href="docs/index.md">Documentation</a> &nbsp;&bull;&nbsp;
  <a href="docs/install.md">Install</a> &nbsp;&bull;&nbsp;
  <a href="docs/python/quickstart.md">Python</a> &nbsp;&bull;&nbsp;
  <a href="docs/typescript/quickstart.md">TypeScript</a> &nbsp;&bull;&nbsp;
  <a href="docs/http/quickstart.md">HTTP</a>
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

The [TypeScript library](docs/install.md#typescript) is built from a `v1` checkout and
needs Node 22 on Linux x86-64. The [HTTP face](docs/install.md#http-face) is a separate
service serving an OpenAI-compatible API.

## Quick start

Set `ANTHROPIC_API_KEY` in your environment. Save this as `hello.py` in your
application directory and run `uv run python hello.py`:

```python
import asyncio
from amplifier_agent import create_agent, AgentOptions, SessionOptions, TurnInput, TextPart


async def main():
    async with await create_agent(
        AgentOptions(
            provider="anthropic",
            model="claude-sonnet-5",
        )
    ) as agent:
        session = await agent.create_session(SessionOptions(persistence="ephemeral"))
        result = await session.run(TurnInput(content=[TextPart("Say hello.")]))
        if result.error is not None:
            raise RuntimeError(f"{result.error.message} {result.error.remedy}")
        print("".join(part.text for part in result.content or []))


asyncio.run(main())
```

`run` waits for the turn. `start_turn` hands you the same turn as a stream of events.
Sessions are durable by default; retain the session ID to
[resume one later](docs/concepts/sessions.md). Tool execution, including file reads,
needs an [approval policy](docs/concepts/approvals.md). The
[TypeScript quickstart](docs/typescript/quickstart.md) is the same example in TypeScript.

## What comes with it

- Provider configuration and credentials supplied by the host. See
  [providers](docs/providers.md).
- A model ceiling that execution never exceeds. See [models](docs/concepts/models.md).
- Tools you write and we call, tools that come with the agent, and MCP servers, all
  resolving through one call path. See [tools](docs/concepts/tools.md).
- Your veto over every effect, before it happens. See
  [approvals](docs/concepts/approvals.md).

Start at [docs/index.md](docs/index.md) for how the bindings, engine, and HTTP face fit
together. [`contracts/`](contracts/README.md) is the normative surface; where it and the
documentation disagree, the contracts win.

## Agent skill

Install the [integration skill](skills/amplifier-agent/SKILL.md) in your application
project to help coding agents build on the library:

```bash
npx skills add https://github.com/microsoft/amplifier-agent/tree/v1 --skill amplifier-agent
```

See [skill installation](docs/install.md#coding-agent-skill) for using a local checkout.

## Development

Run `uv run setup-for-dev.py` and read [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).
[Development checks](docs/development/checks.md) covers tests and
[evaluations](evaluations/README.md).

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

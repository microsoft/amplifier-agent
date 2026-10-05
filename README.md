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

Install the Python binding from a release tag. Requires Python 3.12 or
newer, [uv](https://docs.astral.sh/uv/), and Git. Run this in your application
directory (`uv init` first for a new project):

```bash
uv add "amplifier-agent[github-copilot] @ git+https://github.com/microsoft/amplifier-agent#subdirectory=packages/python" --tag v0.21.0
```

Drop `[github-copilot]` to install without the GitHub Copilot provider.

The [TypeScript library](docs/install.md#typescript) installs from npm as `amplifier-agent-ts` and
needs Node 22 on Linux x86-64. The [HTTP face](docs/install.md#http-face) is a separate
service serving an OpenAI-compatible API.

## Quick start

Set `OPENAI_API_KEY` in your environment. Save this as `hello.py` in your
application directory and run `uv run python hello.py`:

```python
import asyncio
from amplifier_agent import create_agent, AgentOptions, SessionOptions, TurnInput, TextPart


async def main():
    async with await create_agent(
        AgentOptions(
            provider="openai",
            model="gpt-6.1-sol",
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

- Nine providers behind one interface: Anthropic, OpenAI, Azure OpenAI, Ollama, GitHub Copilot, ChatGPT (a Plus/Pro/Team subscription via OAuth device-code, no API key), Chat Completions (any OpenAI Chat Completions-compatible endpoint, e.g. llama.cpp, vLLM, LM Studio), Gemini (Google's Gemini API, large context windows plus thinking/reasoning support), and vLLM (a self-hosted or remote vLLM server via its OpenAI-compatible Responses API, for open-weight models like gpt-oss), with credentials read from the environment or a cached OAuth session
- Role-based model routing, so a sub-agent gets a model matched to its job rather than the frontier model for everything, re-matched when you switch providers
- Context management that keeps long sessions running, compacting history before it overruns the window
- Tools for filesystem, bash, web, search, todo, and MCP
- Sub-agent delegation and skills

Start at [docs/index.md](docs/index.md) for how the bindings, engine, and HTTP face fit
together. [`contracts/`](contracts/README.md) is the normative surface; where it and the
documentation disagree, the contracts win.

## Agent skill

Install the [integration skill](skills/amplifier-agent/SKILL.md) in your application
project to help coding agents build on the library:

```bash
npx skills add https://github.com/microsoft/amplifier-agent/tree/main --skill amplifier-agent
```

See [skill installation](docs/install.md#coding-agent-skill) for using a local checkout.

## Development

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

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

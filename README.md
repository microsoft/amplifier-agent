<h1 align="center">Amplifier Agent</h1>

<p align="center">
  Bring AI into your product. Amplifier Agent handles the agentic engineering, so you can focus on the experience.
</p>

<p align="center">
  <a href="docs/index.md">Documentation</a> &nbsp;&bull;&nbsp;
  <a href="docs/install.md">Install</a> &nbsp;&bull;&nbsp;
  <a href="docs/python/quickstart.md">Python</a> &nbsp;&bull;&nbsp;
  <a href="docs/typescript/quickstart.md">TypeScript</a> &nbsp;&bull;&nbsp;
  <a href="docs/http/quickstart.md">HTTP</a>
</p>

---

**Amplifier Agent** is a library that you embed in your Python or TypeScript application. Any other application that
supports chat completions can use it through an [HTTP service](docs/http/quickstart.md). Name a model, set the tools,
and give it a task. The agent reasons, acts, and reports back.

## Why Amplifier Agent

All teams can use the same models now. A good agent comes from the layer above the model: the context it gets, the
tools it can use, and how it keeps long tasks on track. Amplifier Agent is that layer. 
We build it once, so each product does not have to build it again.

- **Use the model you want.** Nine providers use one interface. Use an API key, a GitHub Copilot or ChatGPT
  subscription, or a local model. Change the model during a session and keep the conversation.
- **Add your own tools.** Give the agent a function from your application, or connect an MCP server. Your tools work
  next to the built-in tools for files, shell, search, and web.
- **Let it do the long work.** The agent sends tasks to sub-agents, loads skills when it needs them, and compacts
  history before it fills the context window.
- **Integrate it easily, with your coding agent too.** A few lines start an agent in Python or TypeScript. An
  [integration skill](#use-it-with-a-coding-agent) teaches your coding agent to build with the library.
- **Open.** It is open source under the MIT license, and it is not tied to one model vendor.

## Install

### Python

You need [uv](https://docs.astral.sh/uv/) and Git. If you do not have Python 3.12 or newer, uv installs it for you.

```bash
uv add "amplifier-agent[github-copilot] @ git+https://github.com/microsoft/amplifier-agent#subdirectory=packages/python" --tag v0.22.0
```

### TypeScript

You need Node 22 on Linux x86-64. WSL2 is supported.

```bash
npm install amplifier-agent-ts@0.22.0
```

### HTTP

The [HTTP face](docs/install.md#http-face) is a separate service. It serves an OpenAI-compatible chat completions API
for clients that cannot embed a library.

Refer to the [installation guide](docs/install.md) for all options.

## Quick start

Set `OPENAI_API_KEY` in your environment. Save this file as `hello.py` in your application directory, then run
`uv run python hello.py`:

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

The [TypeScript quickstart](docs/typescript/quickstart.md) shows the same example in TypeScript.

## Give it work

Tools need an approval policy. This agent works in the current directory and asks you before each tool call:

```python
import asyncio
from amplifier_agent import (
    ApprovalResponse, AgentOptions, SessionOptions, TurnInput, TextPart, create_agent,
)


async def approve(request):
    print(f"{request.name}: {request.summary}")
    answer = await asyncio.to_thread(input, "[y/N] ")
    return ApprovalResponse(decision="allow" if answer == "y" else "deny")


async def main():
    async with await create_agent(
        AgentOptions(
            provider="openai",
            model="gpt-6.1-sol",
            working_directory=".",
            approvals=approve,
        )
    ) as agent:
        session = await agent.create_session(SessionOptions(persistence="ephemeral"))
        result = await session.run(
            TurnInput(content=[TextPart("Find the Python files here and summarize what each one does.")])
        )
        print(result.state, "".join(part.text for part in result.content or []))


asyncio.run(main())
```

`approvals="allow"` approves all tool calls with no prompt, including shell commands. Use it only where that is safe.

## Next steps

- [Watch a turn as it runs](docs/concepts/events.md): `start_turn` gives you the turn as a stream of events.
- [Add your own tools](docs/concepts/tools.md) and [MCP servers](docs/concepts/tools.md#mcp-servers).
- [Resume a session later](docs/concepts/sessions.md). Sessions are durable by default.
- [Add skills and named agents](docs/concepts/skills.md).
- [Select a provider and set credentials](docs/providers.md).
- [Change the model during a session](docs/concepts/sessions.md#switching-models). The conversation stays.
- [Set options outside your code](docs/configuration.md).

Start at [docs/index.md](docs/index.md) for how the bindings, engine, and HTTP face fit together. Where the
documentation and the [contracts](contracts/README.md) disagree, the contracts are correct.

## Use it with a coding agent

Install the [integration skill](skills/amplifier-agent/SKILL.md) in your application project. 
It helps coding agents build on the library:

```bash
# Add -g for global
npx skills add microsoft/amplifier-agent --skill amplifier-agent
```

Then ask your coding agent to use Amplifier Agent for the AI parts of your application.

## Built with Amplifier Agent

- [muxplex](https://github.com/bkrabach/muxplex) is a web dashboard for tmux sessions. Its agent chat panel uses
  Amplifier Agent.

[Smart Tools](https://github.com/microsoft/amplifier-smart-tools) are expertise packaged as a tool that any agent can
use. You state what you need, the tool has the knowledge, and you get the result. These Smart Tools use Amplifier Agent:

- [Smart Tool Creator](https://github.com/microsoft/amplifier-smart-tool-creator) creates Smart Tools. It makes the
  structure that the spec requires, checks a tool against the spec, and adds model-backed capabilities to it.
- [Digital Twin Universe](https://github.com/microsoft/amplifier-smart-tool-digital-twin-universe) starts isolated,
  realistic environments from a profile with Docker Compose. Agents use them to test software in a clean environment.
- [Showrun](https://github.com/robotdad/amplifier-smart-tool-showrun) records application walkthroughs. You describe
  what to show, and it operates the real app in a browser or native window and records a video.

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

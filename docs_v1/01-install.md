# Install

## Prerequisites

**[uv](https://docs.astral.sh/uv/).** Everything below goes through it.

```bash
# macOS and Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

```pwsh
# Windows
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**`git` on your `PATH`, at run time as well as install time.** The agent fetches
components it needs the first time it runs. On Windows,
[Git for Windows](https://git-scm.com/download/win) covers this and also provides
the shell the agent's built-in shell tool looks for. Windows additionally needs
long paths enabled:

```bash
git config --global core.longpaths true
```

You do not need to install Python. uv downloads a suitable interpreter if your
system does not already have one.

## The library

```bash
uv add git+https://github.com/microsoft/amplifier-agent
```

That gives you the `amplifier_agent` package, which is everything the rest of
these docs describe. Pin a release rather than tracking the default branch:

```bash
uv add git+https://github.com/microsoft/amplifier-agent --tag v0.17.0
```

Available tags are listed at
<https://github.com/microsoft/amplifier-agent/releases>.

## The CLI

The same distribution provides the `amplifier-agent` command. Install it as a
standalone tool when you want the command on your `PATH` without adding the
library to a project:

```bash
uv tool install git+https://github.com/microsoft/amplifier-agent
```

Pin a release by putting the tag in the URL:

```bash
uv tool install git+https://github.com/microsoft/amplifier-agent@v0.17.0
```

There is also an installer script that resolves the latest release and installs
it in one step:

```bash
curl -fsSL https://raw.githubusercontent.com/microsoft/amplifier-agent/main/install.sh | bash -s -- --yes
```

The script takes `--tag <ref>` to pin a version, `--no-prime` to skip warming the
cache, and `--yes` to skip the confirmation prompt. Flags go after `-s --`, since
everything before that is consumed by `bash` itself.

If a host application spawns `amplifier-agent` as a subprocess, install it as the
user that runs the host process. A tool installed by `root` while the service
runs unprivileged is not on the `PATH` the subprocess inherits.

## The TypeScript SDK

```bash
pnpm add amplifier-agent-ts
```

The SDK does not bundle the agent. Install both. See
[Surfaces](07-surfaces/typescript.md) for what it covers.

## Verify

```bash
amplifier-agent doctor     # environment, providers, paths
amplifier-agent version    # release and contract versions
```

`doctor` reports which providers have credentials it can resolve, which is
usually the fastest answer to "why does my agent say it cannot find a model."

Both are compositions of library calls, formatted for a terminal. The same
answers from Python:

```python
import amplifier_agent
from amplifier_agent import list_providers

print(amplifier_agent.__version__, amplifier_agent.contract_version)

for status in await list_providers():
    print(status.descriptor.name, status.available, status.credential_source)
```

## Update and remove

```bash
uv tool upgrade amplifier-agent
```

A tool installed from a pinned tag stays on it. To move to a different release,
install again with the new tag.

To remove everything, including stored sessions and credentials:

```bash
uv tool uninstall amplifier-agent
rm -rf ~/.amplifier-agent
```

## Next

Build a working agent in the [Quickstart](02-quickstart.md).

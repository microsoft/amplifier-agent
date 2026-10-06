# Install

## Python

Requires Python 3.12 or newer, [uv](https://docs.astral.sh/uv/), and Git.
Install the Python binding from a release tag in your application directory. Run `uv init` first if you are starting a new project.

```bash
uv add "amplifier-agent[github-copilot] @ git+https://github.com/microsoft/amplifier-agent#subdirectory=packages/python" --tag v0.22.0
uv run python -c "import amplifier_agent; print(amplifier_agent.contract_versions)"
```

`uv add` records the dependency and its Git source in `pyproject.toml`, locks the
resolved dependencies, and installs them in the application's environment.
Use `uv sync --locked` to reproduce that environment from its lockfile.

The `github-copilot` extra adds the `github-copilot` provider. To install without it,
drop `[github-copilot]`:

```bash
uv add "amplifier-agent @ git+https://github.com/microsoft/amplifier-agent#subdirectory=packages/python" --tag v0.22.0
```

Run the [Python quickstart](python/quickstart.md) with `uv run python hello.py`
from your application directory.

To test local changes instead, use an editable checkout from your application
directory, adjusting the paths:

```bash
uv add --editable /path/to/amplifier-agent/packages/python /path/to/amplifier-agent/packages/engine --extra github-copilot
uv sync --locked
```

Drop `--extra github-copilot` to install without the `github-copilot` provider.
The two editable paths keep the binding and its execution dependency on the same checkout.
Editable installs use changes in that checkout immediately. Keep it available for
the application's lifetime.

The Git installation brings in the engine dependency automatically from its
separately declared release tag. Selecting another binding revision alone does not
select the same engine revision. The application's `uv.lock` records both resolved
commits; the editable recipe above uses local changes for both packages.

## TypeScript

Requires Node 22 on Linux x86-64 with glibc 2.35 or newer. WSL2 works with a
compatible Linux distribution; native Windows, macOS, ARM64, and Alpine/musl are
outside the bundled runtime's platform support. The library is ESM; use an `.mjs`
file or a project with `"type": "module"`.

Install the package from npm:

```bash
npm install amplifier-agent-ts@0.22.0
```

The package includes its execution runtime and does not require Python, uv, or pnpm.
A Git dependency on this repository contains only source, without the compiled library
or runtime, so install from npm or a build instead.

To build the package yourself, use a checkout on the same platform. The build also needs
Git, [uv](https://docs.astral.sh/uv/), pnpm 11.25.0
(`npm install --global pnpm@11.25.0`), and `objdump` from binutils; uv fetches the
Python version the checkout pins. Run this from the directory that holds your
application directory:

```bash
git clone --depth 1 --single-branch --branch v0.22.0 https://github.com/microsoft/amplifier-agent.git
cd amplifier-agent
uv run --frozen --package amplifier-agent-engine --extra github-copilot --group build python scripts/build_runtime.py --output packages/typescript/runtime/linux-x64
cd packages/typescript
pnpm install --frozen-lockfile
pnpm build
```

`build_runtime.py` bundles the execution runtime and `pnpm build` compiles the
library. The runtime links against the build machine's glibc, so build on the
oldest system you deploy to. Then, from your application, install the built package
directory:

```bash
npm install --install-links ../amplifier-agent/packages/typescript
```

Adjust the path to your checkout. `--install-links` copies the package instead of
linking the application to the checkout. To move a build to another machine, run
`pnpm pack` in `packages/typescript`, which checks the build and writes a `.tgz`, then
install that archive there.

```ts
import { contractVersions } from "amplifier-agent-ts";
console.log(contractVersions);
```

## HTTP face

The face is a separate server package that installs the Python binding and its
engine dependency, with every provider. Run this in your application directory
(`uv init` first for a new project):

```bash
uv add "amplifier-agent-http @ git+https://github.com/microsoft/amplifier-agent#subdirectory=packages/http" --tag v0.22.0
```

To test local changes, install all three packages from the same checkout instead:

```bash
uv add --editable /path/to/amplifier-agent/packages/http /path/to/amplifier-agent/packages/python /path/to/amplifier-agent/packages/engine
```

Start it with `uv run amplifier-agent-face`. It takes no command-line arguments; its
provider credentials, face token, and other settings come from the environment and
config file, as shown in the [HTTP quickstart](http/quickstart.md#settings). A
[self-contained build](development/checks.md#build-artifacts) runs as
`amplifier-agent-face` with the same settings and no separate Python installation.
What this shape cannot carry is in [limits](http/limits.md).

## Credentials

Hosted providers need credentials. Local Ollama and compatible servers can use
their own authentication policy. See [providers](providers.md) for each provider's
settings and credential source.

## Sessions

Durable sessions are written under `~/.amplifier-agent/projects/`, one folder per
working directory, unless you set
[`sessions_directory`](configuration.md#sessions-directory).

## First-run errors

- `provider_failed`: follow the remedy for credentials, availability, or request
  settings. After changing credentials, create a new agent.
- `selector_rejected`: set both provider and model, and choose a model your account
  can access.
- `approval_unavailable`: supply an [approval policy](concepts/approvals.md) before
  requesting tools, in code or through `AMPLIFIER_AGENT_APPROVALS`. Read-only tools
  also need approval.
- `engine_unavailable`: check the remedy for missing provider credentials,
  connection settings, or the `github-copilot` extra. In Node, also check Node 22,
  the supported Linux platform, and that the package contains the complete
  production runtime.

## Coding-agent skill

Install the integration skill in your application directory:

```bash
npx skills add https://github.com/microsoft/amplifier-agent/tree/main --skill amplifier-agent
```

To install from a local checkout, including uncommitted skill changes:

```bash
npx skills add /path/to/amplifier-agent --skill amplifier-agent
```

The skill guides application integration and points to the public docs and contracts; 
it does not install the Amplifier Agent library.

## Next

- [Python quickstart](python/quickstart.md)
- [TypeScript quickstart](typescript/quickstart.md)
- [HTTP quickstart](http/quickstart.md)

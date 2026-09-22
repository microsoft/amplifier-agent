# Install

## Python

Requires Python 3.12 or newer, [uv](https://docs.astral.sh/uv/), and Git.
Install the Python binding from the upstream `v1` branch in your application
directory. Run `uv init` first if you are starting a new project.

```bash
uv add "amplifier-agent @ git+https://github.com/microsoft/amplifier-agent#subdirectory=packages/python" --branch v1
uv run python -c "import amplifier_agent; print(amplifier_agent.contract_versions)"
```

`uv add` records the dependency and its Git source in `pyproject.toml`, locks the
resolved dependencies, and installs them in the application's environment.
Use `uv sync --locked` to reproduce that environment from its lockfile.

Run the [Python quickstart](python/quickstart.md) with `uv run python hello.py`
from your application directory.

To test local changes instead, use an editable `v1` checkout from your application
directory, adjusting the paths:

```bash
uv add --editable /path/to/amplifier-agent/packages/python /path/to/amplifier-agent/packages/engine
uv sync --locked
```

The two editable paths keep the binding and its execution dependency on the same checkout.
Editable installs use changes in that checkout immediately. Keep it available for
the application's lifetime.

The Git installation brings in the engine dependency automatically from its
separately declared `v1` ref. Selecting another binding revision alone does not
select the same engine revision. The application's `uv.lock` records both resolved
commits; the editable recipe above uses local changes for both packages.

```python
import amplifier_agent
print(amplifier_agent.contract_versions)
```

## TypeScript

Requires Node 22 on Linux x86-64 with glibc 2.35 or newer. WSL2 works with a
compatible Linux distribution; native Windows, macOS, ARM64, and Alpine/musl are
outside the bundled runtime's platform support. The library is ESM; use an `.mjs`
file or a project with `"type": "module"`.

Clone the `v1` branch:

```bash
git clone --depth 1 --single-branch --branch v1 https://github.com/microsoft/amplifier-agent.git
```

In that checkout, install the [development toolchain](development/checks.md)
and follow [Build installable artifacts](development/checks.md#build-installable-artifacts).
Then, from your application, install the built package directory:

```bash
npm install --install-links ../amplifier-agent/packages/typescript
```

Adjust the path to your checkout. `--install-links` copies the package instead of
linking the application to the checkout. A `.tgz` from `pnpm pack` is an alternative
when transferring a build to another machine:

```bash
npm install /path/to/microsoft-amplifier-agent-1.0.0-alpha.1.tgz
```

Use the actual archive name produced by the build. Installed packages include their
execution runtime and do not require Python, uv, or pnpm on the consumer machine.

npm and pnpm support Git dependencies. A Git checkout of this package contains
source; installing it also requires compiling the library and bundling its native
runtime. The build step above supplies those outputs. pnpm manages development;
applications can use npm.

```ts
import { contractVersions } from "@microsoft/amplifier-agent";
console.log(contractVersions);
```

## HTTP face

The face is a separate server package that installs the Python binding and its
engine dependency. Run this in your application directory (`uv init` first for a
new project):

```bash
uv add "amplifier-agent-http @ git+https://github.com/microsoft/amplifier-agent#subdirectory=packages/http" --branch v1
```

To test local changes, install all three packages from the same `v1` checkout instead:

```bash
uv add --editable /path/to/amplifier-agent/packages/http /path/to/amplifier-agent/packages/python /path/to/amplifier-agent/packages/engine
```

Set `ANTHROPIC_API_KEY` for the provider and `FACE_TOKEN` to a separate secret for
your HTTP clients. Start the service; it takes no command-line arguments.

```bash
AMPLIFIER_AGENT_PROVIDER=anthropic \
AMPLIFIER_AGENT_MODEL=claude-sonnet-5 \
AMPLIFIER_AGENT_FACE_TOKEN="$FACE_TOKEN" \
uv run amplifier-agent-face
```

A [self-contained build](development/checks.md#build-installable-artifacts) runs as
`amplifier-agent-face` with the same settings and no separate Python installation.

```bash
curl -s localhost:9099/v1/models -H "Authorization: Bearer $FACE_TOKEN"
```

Every setting the face reads is in [the HTTP quickstart](http/quickstart.md), and what
this shape cannot carry is in [limits](http/limits.md).

## Credentials

Hosted providers need credentials. Local Ollama and compatible servers can use
their own authentication policy. See [providers](providers.md) for each provider's
settings and credential source.

## Storage

Durable sessions are written under the storage root, which defaults to
`~/.amplifier-agent`. Point it somewhere else with the `storage` key. See
[configuration](configuration.md).

## First-run errors

- `provider_failed`: follow the remedy for credentials, availability, or request
  settings. After changing credentials, create a new agent.
- `selector_rejected`: set both provider and model, and choose a model your account
  can access.
- `approval_unavailable`: supply an [approval policy](concepts/approvals.md) before
  requesting tools. Read-only tools also need approval.
- `engine_unavailable`: check the remedy for missing provider credentials or
  connection settings. In Node, also check Node 22, the supported Linux platform,
  and that the package contains the complete production runtime.

Errors carry a remedy. Check `result.error` for a completed turn and catch
`AgentError` for refused method calls; see [errors](concepts/errors.md).

## Coding-agent skill

Install the integration skill from the `v1` branch in your application directory:

```bash
npx skills add https://github.com/microsoft/amplifier-agent/tree/v1
```

To install from a local `v1` checkout, including uncommitted skill changes:

```bash
npx skills add /path/to/amplifier-agent --skill amplifier-agent
```

Choose your coding agent in the installer's prompts. The skill guides application
integration and points to the public docs and contracts; it does not install the
Amplifier Agent library.

## Next

- [Python quickstart](python/quickstart.md)
- [TypeScript quickstart](typescript/quickstart.md)
- [HTTP quickstart](http/quickstart.md)

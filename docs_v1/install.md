# Install

## Python

Requires Python 3.12 or newer.

Install the `v1` branch from source:

```bash
uv add "git+https://github.com/microsoft/amplifier-agent#subdirectory=packages/python" --branch v1
uv sync --locked
```

That records the source in your `pyproject.toml`, so the next `uv sync` resolves the same
way:

```toml
[project]
dependencies = ["amplifier-agent"]

[tool.uv.sources]
amplifier-agent = { git = "https://github.com/microsoft/amplifier-agent", branch = "v1", subdirectory = "packages/python" }
```

The equivalent URL spelling is:

```bash
uv add "amplifier-agent @ git+https://github.com/microsoft/amplifier-agent@v1#subdirectory=packages/python"
```

The SDK installs its separate engine dependency from the engine package on `v1`.
The consumer's `uv.lock` records both resolved commits; `uv sync --locked` preserves
them. Selecting another SDK revision does not change the engine dependency's declared
source ref. Dependencies resolve from package metadata without local workspace paths.

```python
import amplifier_agent
print(amplifier_agent.contract_versions)
```

## TypeScript

Requires Node 22. Build the ESM library, declarations, and bundled engine from a
source checkout using the [development toolchain](development/checks.md):

```bash
git clone --depth 1 --branch v1 https://github.com/microsoft/amplifier-agent.git
```

In that checkout, follow [Build installable artifacts](development/checks.md#build-installable-artifacts).
Then, from your application, install the built package directory:

```bash
npm install --install-links ../amplifier-agent/packages/typescript
```

Adjust the path to your checkout. `--install-links` copies the package instead of
linking the application to the checkout. A `.tgz` from `pnpm pack` is an alternative
when transferring a build to another machine.

npm and pnpm support Git dependencies. A Git checkout of this package contains
source; installing it also requires compiling the library and bundling its native
runtime. The build step above supplies those outputs. pnpm manages development;
applications can use npm.

```ts
import { contractVersions } from "@microsoft/amplifier-agent";
console.log(contractVersions);
```

## HTTP face

The face is a separate server package. It installs the SDK and engine dependencies:

```bash
uv add "amplifier-agent-http @ git+https://github.com/microsoft/amplifier-agent@v1#subdirectory=packages/http"
```

Start it. It takes no arguments, because a request never carries configuration and
neither does the command that starts the server.

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

Every surface needs provider credentials. See [providers](providers.md).

## Storage

Durable transcripts are written under the storage root, which defaults to
`~/.amplifier-agent`. Point it somewhere else with the `storage` key. See
[configuration](configuration.md).

## Next

```
python/quickstart.md
typescript/quickstart.md
http/quickstart.md
```

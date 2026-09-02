# Install

## Python

Requires Python 3.12 or newer.

From source, which is where releases are cut:

```bash
uv add git+https://github.com/microsoft/amplifier-agent
```

That records the source in your `pyproject.toml`, so the next `uv sync` resolves the same
way:

```toml
[project]
dependencies = ["amplifier-agent"]

[tool.uv.sources]
amplifier-agent = { git = "https://github.com/microsoft/amplifier-agent" }
```

Pin a tag rather than tracking the default branch:

```bash
uv add git+https://github.com/microsoft/amplifier-agent --tag v1.0.0
```

`--branch` and `--rev` do the same for a branch and a commit.

From the package index, once you would rather track releases than refs:

```bash
uv add amplifier-agent
```

```python
import amplifier_agent
print(amplifier_agent.contract_version)   # agent-interface/1
```

## TypeScript

Requires Node 20 or newer. The package ships ESM with type declarations.

```bash
npm install @microsoft/amplifier-agent
```

```ts
import { contractVersion } from "@microsoft/amplifier-agent";
console.log(contractVersion);   // agent-interface/1
```

## HTTP face

The face is a server you run. It is a self-contained executable, so there is no runtime
to install alongside it.

```bash
curl -fsSL https://raw.githubusercontent.com/microsoft/amplifier-agent/main/install-face.sh | bash
```

Or take the archive for your platform directly, if you would rather see what you are
running before you run it:

```bash
curl -fsSL -o face.tar.gz \
  https://github.com/microsoft/amplifier-agent/releases/latest/download/amplifier-agent-face-linux-x86_64.tar.gz
tar xzf face.tar.gz && install -m 755 amplifier-agent-face /usr/local/bin/
```

Start it. It takes no arguments, because a request never carries configuration and
neither does the command that starts the server.

```bash
AMPLIFIER_AGENT_PROVIDER=anthropic \
AMPLIFIER_AGENT_MODEL=claude-sonnet-5 \
AMPLIFIER_AGENT_FACE_TOKEN="$FACE_TOKEN" \
amplifier-agent-face
```

A container image is published for the same thing:

```bash
docker run --rm -p 9099:9099 \
  -e AMPLIFIER_AGENT_PROVIDER=anthropic \
  -e AMPLIFIER_AGENT_MODEL=claude-sonnet-5 \
  -e AMPLIFIER_AGENT_FACE_TOKEN="$FACE_TOKEN" \
  -e ANTHROPIC_API_KEY \
  ghcr.io/microsoft/amplifier-agent-face:1
```

```bash
curl -s localhost:9099/v1/models -H "Authorization: Bearer $FACE_TOKEN"
```

Every setting the face reads is in [the HTTP quickstart](http/quickstart.md), and what
this shape cannot carry is in [limits](http/limits.md).

## Credentials

Every surface needs credentials for the provider you chose. See
[providers](providers.md) for the environment variable each one reads.

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

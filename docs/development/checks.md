# Development checks

Environment setup and the everyday commands are in [DEVELOPMENT.md](../DEVELOPMENT.md).
The [architecture](architecture.md) explains the source layout.

## Tests

```bash
uv run --all-packages python scripts/check.py --runtime
```

This runs lint, type checks, the Python and HTTP tests, and the TypeScript tests. It
stops at the first failure. `--runtime` rebuilds the TypeScript test runtime first;
leave it out when `packages/typescript/runtime/<platform>/` is current. CI runs this
command on Linux and macOS, and the precommit hooks and the wheel build on Linux, all on
Node 22.

```text
packages/*/tests/            package units
packages/typescript/test/    TypeScript public behavior; engine/ holds private component tests
tests/e2e/python/            Python public behavior
tests/e2e/http/              HTTP face behavior
tests/integration/           native provider adapters against local protocol services
tests/support/               scripted provider, local services, scenarios, test runtime entry
```

Tests replace the provider with scripted responses or local protocol services. They
make no model calls and need no API keys. They are implementation aids, not proof that
the agent works.

## Evaluations

Real tasks with real models, through each binding and the HTTP face, live in
`evaluations/`. See [evaluations/README.md](../../evaluations/README.md).

## Build artifacts

Build native assets on Ubuntu 22.04 so consumers with glibc 2.35 or newer can run them.
The [TypeScript install](../install.md#typescript) gives the minimal package build.

```bash
uv build --all-packages --no-sources
uv run --all-packages python scripts/build_runtime.py --output packages/typescript/runtime/linux-x64
(cd packages/typescript && pnpm build)
uv run --all-packages python scripts/build_runtime.py --face --output build/face
```

`build_runtime.py` builds only for the machine it runs on. The published package carries
both runtimes, so `pnpm pack` (its `prepack` check) needs the `darwin-arm64` runtime from
a macOS arm64 build as well; the release workflow assembles both.

Install the TypeScript package with `npm install --install-links
/path/to/packages/typescript`, or create an archive with `pnpm pack`. The prepack check
rejects missing assets, changed hashes, and test runtimes. Transfer the whole
`build/face/` directory and run `./build/face/amplifier-agent-face`.

# Development

## Prerequisites

Install:

- [Git](https://git-scm.com/)
- [uv](https://docs.astral.sh/uv/getting-started/installation/) 0.12.9 or newer: Manages Python environments.
- [prek](https://github.com/j178/prek): Used for precommit hooks. Recommended to install through PyPI/uv with `uv tool install prek`. Use `uv tool upgrade prek` to update it.
- [Node.js](https://nodejs.org/) 22 and [pnpm](https://pnpm.io/installation) 11.25.0 (`npm install --global pnpm@11.25.0`): the TypeScript binding in `packages/typescript`.
- [Docker](https://docs.docker.com/get-docker/) with Compose, optional: only to run the [evaluations](../evaluations/README.md).

Development pins Python 3.12.14 (`.python-version`), Node 22.23.2 and TypeScript 7.0.2. Python 3.12 is the library's minimum. Update these pins together with CI, package metadata, and DTU profiles.

## Initial Setup

1. Clone this repository and change into it.

1. Run the development installation script (sets up both uv environments, precommit hooks, and the TypeScript dependencies):

   ```bash
   uv run setup-for-dev.py
   ```

## Essential Development Commands

*Commands should be run from the repository root, unless otherwise specified.*

### Precommit hooks

The hooks lock both uv projects, run `ruff check --fix`, `ruff format` (including Markdown code blocks outside `contracts/`), `ty check`, and the TypeScript checks. Nothing else gates a commit; tests are run by you and by CI.

Setup precommit hooks:

```bash
prek install
```

Run precommit hooks:

```bash
prek run --all-files
```

### Python

The root is a uv workspace with three members: `packages/python` (SDK), `packages/engine`, `packages/http`.

Create the uv virtual environment and install dependencies:

```bash
uv sync --frozen --all-packages --all-groups
```

To update dependencies and the lock file:

```bash
uv sync -U --all-packages --all-groups
```

Lint code:

```bash
uv run ruff check --fix
```

Format code (also formats code blocks in .md files):

```bash
uv run ruff format
```

Type check:

```bash
uv run ty check
```

Run tests:

```bash
uv run --all-packages python -m pytest
```

Default discovery covers the package unit tests, the public Python and HTTP tests in `tests/e2e/python` and `tests/e2e/http`, and the provider integrations in `tests/integration`. Shared test helpers live in `tests/support`.

### TypeScript

Run pnpm from inside `packages/typescript`: a corepack-managed pnpm selects the pinned version from the package directory it starts in.

```bash
cd packages/typescript
pnpm install --frozen-lockfile
pnpm check     # biome check --write, then tsc build and tsc --noEmit
pnpm test
```

`biome.json` carries the lint and format rules. `tsconfig.json` stays strict. The tests
import the package by name, so `pnpm test` rebuilds `dist/` first. Most of them drive the
test runtime from `runtime/linux-x64/`, which `scripts/check.py --runtime` builds;
`pnpm test` stops first when that runtime is missing or was built from other sources. To
build just the runtime:

```bash
uv run --all-packages python scripts/build_runtime.py --test --output packages/typescript/runtime/linux-x64
```

### Evaluations

`evaluations/` is a separate uv project (Python 3.13) that runs the agent through live tasks in containers. `setup-for-dev.py` syncs it; its hooks run through the same `prek` config.

```bash
cd evaluations
uv run pytest
uv run amplifier-agent-evaluations run runs/smoke-checkout.yaml
```

[evaluations/README.md](../evaluations/README.md) covers profiles, credentials, and output.

### All checks

```bash
uv run --all-packages python scripts/check.py --runtime
```

This runs lint, type checks, and every Python, HTTP, and TypeScript test, rebuilding the TypeScript test runtime first. CI runs the hooks, this command, and the wheel build. [Development checks](development/checks.md) covers the test layout and building installable artifacts. The [architecture](development/architecture.md) explains the source layout and ownership boundaries.

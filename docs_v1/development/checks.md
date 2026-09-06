# Development checks

Use these development versions:

```text
Python       3.12.14
Node         22.23.2
npm          12.0.2
pnpm         11.25.0
uv           0.12.9
TypeScript   7.0.2
```

Python 3.12 is the library's minimum; development uses its pinned patch release.
Node support is limited to the 22 release line. npm bootstraps pnpm and installs
consumer packages; pnpm manages TypeScript development. TypeScript and Python check
tools are installed from their lockfiles.

Verify upgrades against the [Node release index](https://nodejs.org/dist/index.json),
[npm registry](https://registry.npmjs.org/), and [PyPI](https://pypi.org/).
Update these pins together with CI, package metadata, and DTU profiles. The
[architecture](architecture.md) explains the source layout and ownership boundaries.

## Quick verification

From a checkout with Python dependencies installed:

```bash
uv run --all-packages python scripts/verify.py
```

The script prints a result for each group: Python/HTTP public APIs, session lifecycle
and storage, approvals, skills and ecosystem tools, and Anthropic/OpenAI/Gemini
provider adapters. It uses controlled local services and temporary session data,
without API keys or live model calls. Failed, skipped, empty, and timed-out checks
return a nonzero exit code. Each run saves diagnostic logs and test reports in a
separate directory under `build/verification/`.

To check a live provider, set its [credential](../providers.md) and select a model:

```bash
uv run --all-packages python scripts/verify.py --live anthropic --model claude-sonnet-5
```

`--live` also accepts `openai` and `gemini`; `--model` is required. This mode makes
billable model requests through the Python binding. It checks streamed output, one
approved in-memory caller tool, and durable continuation in a second process. Only
that tool is allowed. Provider endpoint overrides are honored; agent configuration
and storage are isolated from the host and temporary session data is removed afterward.
Live mode runs these two checks instead of the deterministic groups.

This is a focused development check. TypeScript, installed artifacts, complete
contract conformance, and model-quality evaluations require their own acceptance.
Use the source gate below to include TypeScript; it rebuilds the fixture runtime.

## Check source and public behavior

Run from the repository root with the toolchain above. Install pnpm with
`npm install --global pnpm@11.25.0` if it is absent.

```bash
uv sync --all-packages --locked --group build
pnpm --dir packages/typescript install --frozen-lockfile
uv run --all-packages python scripts/check.py --runtime
```

This runs lint, type checks, import-boundary checks, contract inventory validation,
public Python/HTTP scenarios, and TypeScript scenarios through a bundled fixture
runtime. The scripted provider and replacement engine live in `conformance/fixtures`;
production assembly does not select them through public options. Each Python
distribution also has independent artifact and dependency-isolation checks.
The CI workflow runs the same command on Node 22.

Python tests live beside their package, under `tests/integration/`, or under
`conformance/tests/`. Run one group with, for example,
`uv run --all-packages python -m pytest packages/engine/tests`.
Default pytest discovery also includes the offline Gitea harness checks;
installed-artifact tests under `tests/e2e/` require explicit selection and their inputs.

Request complete contract coverage separately:

```bash
uv run --all-packages python conformance/run.py --full --output build/conformance.json
```

The report distinguishes failed, uncovered, and setup-failed checks. A passing
scenario does not satisfy every obligation that mentions its surface. See
[conformance](../../conformance/README.md) for the evidence model.

## Build installable artifacts

```bash
uv build --all-packages --no-sources
uv run --all-packages python -m conformance.surface.check_packages dist/*.whl
uv run --all-packages python scripts/build_runtime.py --output packages/typescript/runtime/linux-x64
pnpm --dir packages/typescript build
pnpm --dir packages/typescript run prepack
uv run --all-packages python scripts/build_runtime.py --face --output build/face
```

The Python distributions, TypeScript package, and standalone HTTP executable are
separate build targets. For TypeScript, run the runtime build, TypeScript build, and
prepack check after installing development dependencies. Install the resulting
directory with `npm install --install-links /path/to/packages/typescript`, or create
a transferable archive with `pnpm --dir packages/typescript pack`.

Transfer the entire `build/face/` directory with its executable, `_internal/`, and
manifest. Run `./build/face/amplifier-agent-face`; copying just the executable omits
required runtime files. Python wheel metadata retains Git dependencies, so a wheel
alone is not an offline installation bundle. For local source consumers, use the
paired package paths in the [installation guide](../install.md#python).

Build native Linux x86-64 assets on Ubuntu 22.04 using
`.amplifier/digital-twin-universe/profiles/runtime-builder.yaml`. Consumers need
glibc 2.35 or newer. The runtime manifest inventories files and hashes. npm's
prepack check rejects missing assets, changed hashes, and fixture runtimes.
The native build also downloads and bundles the Copilot executable. Building on a
newer host can raise the glibc requirement; use the baseline builder for distribution.

The `--fixture` and `--replacement` build variants support installed acceptance.
Transfer their archives only as test artifacts. Compile the TypeScript public driver
with `pnpm --dir packages/typescript build:acceptance`, copy
the generated `conformance`, `sessions`, and `ecosystem` tests from `build/acceptance/`
into a consumer project with `.mjs` extensions, and run `node --test *.test.mjs`. Set
`CONFORMANCE_SCENARIOS` to the transferred `conformance/scenarios/turns.json`.
Set `CONFORMANCE_SESSION_SCENARIOS` to the transferred `conformance/scenarios/sessions.json`.
Run from outside the producer checkout, with Python and uv absent from `PATH`.

`tests/e2e/test_installed_artifacts.py` exercises Anthropic, OpenAI, and Gemini through
installed Python, TypeScript, and the standalone HTTP face against controlled
provider services. Set these artifact inputs before selecting the test file:

```text
AMPLIFIER_AGENT_PYTHON_EXECUTABLE   installed consumer's Python interpreter
AMPLIFIER_AGENT_PYTHON_PROJECT      Python consumer directory outside the checkout
AMPLIFIER_AGENT_NODE_EXECUTABLE     absolute Node executable path
AMPLIFIER_AGENT_NODE_PROJECT        installed npm consumer directory
AMPLIFIER_AGENT_FACE_EXECUTABLE     standalone HTTP binary
```

Missing artifact inputs fail the selected acceptance run. Child processes receive a
`PATH` without Python or uv. The suite checks live output, named failures,
cancellation, HTTP isolation, and durable continuation after killing both caller and
provider processes. Restart cases include both directions between Python and
TypeScript, preserving exact history and usage without repeating a recorded effect.

Select live acceptance separately, using the same installed artifact inputs and real
provider credentials:

```bash
uv run --all-packages python -m pytest tests/e2e/test_live_providers.py
```

`E2E_LIVE_ANTHROPIC_MODEL`, `E2E_LIVE_OPENAI_MODEL`, and `E2E_LIVE_GEMINI_MODEL`
select models for this test harness. Anthropic accepts `claude-sonnet-5` or
`claude-opus-5`. The suite checks streaming, tools, and durable continuation; it fails
setup when required credentials or artifacts are missing.

## Verify a Git source install locally

Install the Gitea and DTU prerequisites using
[amplifier-bundle-gitea](https://github.com/microsoft/amplifier-bundle-gitea) and
[amplifier-bundle-digital-twin-universe](https://github.com/microsoft/amplifier-bundle-digital-twin-universe).
The harness pins its DTU library dependency and uses `amplifier-gitea` and Docker.

```bash
uv run --script tests/e2e/local_forge.py prepare --ref HEAD --state /tmp/agent-forge.json
uv run --script tests/e2e/local_forge.py verify --state /tmp/agent-forge.json
uv run --script tests/e2e/local_forge.py destroy --state /tmp/agent-forge.json
```

`prepare` copies an existing named Git ref into disposable Gitea without committing
or pushing. Uncommitted edits are excluded. The consumer DTU redirects only this
repository's GitHub URL, disables uv's GitHub fast path, and installs with `uv add`.
`verify` installs the SDK first and checks its automatically installed engine, then
adds HTTP and checks all three distributions. It verifies revisions and Git
subdirectories against that commit in both the consumer lockfile and installed metadata.
The Git gate requires a committed candidate; an archive install does not satisfy it.

Keep the state file private: it contains local service credentials. Pull evidence
before `destroy`, and always destroy resources created by the harness after testing.

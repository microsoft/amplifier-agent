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

The script reports Python and HTTP units and public APIs, engine units, Python
provider integrations, and verification-runner checks separately. It uses controlled
local services and temporary session data,
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

Package tests cover Python binding, HTTP projection, and engine units. Public Python
and HTTP scenarios live in `tests/e2e/python/` and `tests/e2e/http/`; native provider
integrations remain in `tests/integration/`. TypeScript public and surface tests live
in `packages/typescript/test/`, with private engine component tests in its `engine/`
subdirectory. `conformance/tests/` validates the kit and static surfaces.

Default pytest discovery includes the Python and HTTP source suites and offline
Gitea harness checks. Installed and live-provider acceptance require explicit
selection. Run the same public scenarios against either engine:

```bash
uv run --all-packages python -m pytest tests/e2e/python tests/e2e/http
uv run --all-packages python -m pytest tests/e2e/python tests/e2e/http --engine replacement
```

Production-specific cases are deselected in the replacement run. TypeScript
replacement acceptance uses the same public test files in a disposable consumer.

Request complete contract coverage separately:

```bash
uv run --all-packages python conformance/run.py --full --build-runtime --output build/conformance.json
```

The full run includes TypeScript and the installed-artifact tests, which require the
artifact inputs below. It also requires current source reviews and replacement
evidence. Use `--typescript --build-runtime` without `--full` for source verification.
The report distinguishes failed, uncovered, and setup-failed checks, attributes
individual outcomes to each required surface, and records the tested source hashes.
A passing scenario does not satisfy every obligation that mentions its surface. See
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

Transfer the entire `build/face/` directory with its executable, license, and
manifest. Run `./build/face/amplifier-agent-face`. The manifest records the tested
artifact and source hashes. Python wheel metadata retains Git dependencies, so a wheel
alone is not an offline installation bundle. For local source consumers, use the
paired package paths in the [installation guide](../install.md#python).

Build native Linux x86-64 assets on Ubuntu 22.04 using
`.amplifier/digital-twin-universe/profiles/runtime-builder.yaml`. Consumers need
glibc 2.35 or newer. The runtime manifest inventories files and hashes. npm's
prepack check rejects missing assets, changed hashes, and fixture runtimes.
The native build also downloads and bundles the Copilot executable. Building on a
newer host can raise the glibc requirement; use the baseline builder for distribution.

The `--fixture` and `--replacement` build variants provide controlled test runtimes.
Transfer their archives only as test artifacts. The conformance runner provisions
the replacement engine and its Node participant for the shared public scenarios.

`tests/e2e/installed/python/` and `typescript/` own independent native consumers;
`http/` exercises the installed service through network requests, and `interop/`
checks cross-binding durable restart. They exercise Anthropic, OpenAI, and Gemini
against controlled provider services, including bounded reasoning replay and
configuration failures. `installed/support.py` shares process and service fixtures.

The Python consumer includes the SDK, engine, and HTTP wheels. Tests that configure
HTTP approvals create the application through the installed HTTP package.
Set these artifact inputs before selecting the tests:

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

```bash
uv run --all-packages python -m pytest tests/e2e/installed
```

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

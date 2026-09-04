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

## Check source and public behavior

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

Build native Linux x86-64 assets on Ubuntu 22.04 using
`.amplifier/digital-twin-universe/profiles/runtime-builder.yaml`. Consumers need
glibc 2.35 or newer. The runtime manifest inventories files and hashes. npm's
prepack check rejects missing assets, changed hashes, and fixture runtimes.

The `--fixture` and `--replacement` build variants support installed acceptance.
Transfer their archives only as test artifacts. Compile the TypeScript public driver
with `pnpm --dir packages/typescript build:acceptance`, copy
`build/acceptance/conformance.test.js` from that package as `conformance.test.mjs`
into a consumer project, and run `node --test conformance.test.mjs`. Set
`CONFORMANCE_SCENARIOS` to the transferred `conformance/scenarios/turns.json`.
Run from outside the producer checkout, with Python and uv absent from `PATH`.

`tests/e2e/test_installed_artifacts.py` exercises the production provider adapter
through installed TypeScript and the standalone face against a controlled HTTP
service. Set `AMPLIFIER_AGENT_NODE_EXECUTABLE` to Node's absolute path,
`AMPLIFIER_AGENT_NODE_PROJECT` to the npm consumer, and
`AMPLIFIER_AGENT_FACE_EXECUTABLE` to the standalone binary, then run that test file
with pytest. The child processes receive a `PATH` without Python or uv.

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

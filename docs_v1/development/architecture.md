# Maintaining the implementation

The [contracts](../../contracts/README.md) define behavior. Bindings present that
behavior; the engine implements it. The HTTP face projects the Python binding.

## Source layout

```text
pyproject.toml, uv.lock                     development workspace and shared tool configuration
packages/python/                            amplifier-agent SDK distribution
  src/amplifier_agent/                      public records, handles, and private engine adapter
packages/engine/                            amplifier-agent-engine distribution
  src/amplifier_agent_engine/               private values, execution policy, and process runtime
packages/http/                              amplifier-agent-http service distribution
  src/amplifier_agent_http/                 HTTP settings, routes, and Python binding projection
packages/typescript/                        independent npm binding and bundled engine executable
packages/python/tests/                      Python binding and record conversion
packages/engine/tests/                      private engine/runtime behavior
packages/http/tests/                        HTTP projection and service behavior
packages/typescript/test/                   TypeScript binding and public scenarios
conformance/                                shared cases, inventory, and reporting
conformance/tests/                          contract scenarios and conformance-kit checks
conformance/fixtures/                       shared providers, servers, and replacement engine
tests/integration/                          Python binding and engine integration
tests/e2e/                                  installed artifacts and Gitea/DTU workflow
scripts/                                    local checks and package/runtime builds
.amplifier/digital-twin-universe/profiles/  isolated builder and source-install environments
.github/workflows/                          automated checks using the local commands
```

Each Python package has its own `pyproject.toml`, import namespace, dependencies,
source distribution, and wheel. The repository's root `pyproject.toml` is a uv
workspace and development-tool configuration; it produces no distribution.

`packages/python/` owns the public Python records and handles. Its private adapter
converts inputs, results, events, errors, and caller callbacks at the engine boundary.
It installs the engine as a dependency without including engine code in the SDK wheel.

`packages/engine/` owns configuration, execution, effects, history, and events. It
assembles the upstream `amplifier-core` kernel, loop, context module, and provider.
Its private records and contract-version declaration are independent of the SDK.
The engine neither imports nor depends on the Python binding or HTTP package.
Its `_runtime/` connects TypeScript's process to that same engine.

`packages/http/` depends on the SDK and owns the HTTP executable, server dependencies,
settings, and projection. `packages/typescript/package.json` defines the npm package,
which contains JavaScript, declarations, and the engine executable. Engine executable
builds exclude the Python SDK and HTTP service; the standalone HTTP build includes
all three Python distributions.

The root `uv.lock` pins workspace development dependencies. Workspace source overrides
resolve local members; installed consumers resolve dependencies from distribution
metadata. `.python-version` selects the development interpreter; each package's
`requires-python` declares its supported minimum. TypeScript has its own lockfile,
Node compatibility range, and pnpm pin. `pnpm-workspace.yaml` holds esbuild's build
permission; TypeScript configurations separate library and acceptance-driver output.

Package tests check their owned behavior. `tests/integration/` exercises the Python
binding and engine together; `tests/e2e/` checks installed delivery. `conformance/`
holds reusable contract evidence and shared fixtures. Tests import shared helpers
from those fixtures, not from other test modules. One root pytest configuration
controls Python discovery. Tests, CI, and build helpers are excluded from production
packages. Generated builds, virtual environments, and runtimes are ignored.

## Dependencies

```text
public Python handles -> SDK-owned interfaces -> private adapter -> engine
TypeScript handles -> private connection -> engine
HTTP application -> public Python handles

engine policy -> contract records and upstream adapter interfaces
assembly -> concrete adapters -> Amplifier components
```

SDK and engine records are distinct types. Conversion preserves exact values and owned
extensions; engine validation receives unknown input fields so it can reject them.
Contract records contain values, not runtime handles. Engine policy does not import
bindings, process transport, or HTTP frameworks. Upstream types are translated at
the adapter boundary. The composition root selects and constructs those adapters.
Importing the public library starts no processes and resolves no host configuration.

The TypeScript API is authored from its contracts and name mapping. A change to a
public record updates both bindings and their shared observations together. Private
framing and connection details do not define public types or errors.

## Ownership

An agent owns its resolved configuration and sessions. A session owns turn admission
and its conversation. A turn owns work, effect authorization, usage, event order, and
its terminal transition. Bindings carry those decisions without adding defaults,
retries, caching, or selection policy.

Every task and subprocess has an owner that awaits its teardown. Cancellation stops
new work and settles admitted work. Close joins the same cleanup path, including
after partial construction. A cancelled await across a native boundary is not proof
that its callback finished.

Caller handlers run in the caller process. Callback dispatch and cancellation remain
able to progress while an event consumer pauses. No layer can turn an uncertain
effect into a successful one or discard an opened tool/approval pair.

## Changing the implementation

- Keep record conversion explicit and lossless, including integers, decimal costs,
  errors, and owned extensions.
- Confine upstream adaptation to focused modules. Add an interface when it separates
  an actual responsibility, not merely to wrap another function.
- Validate policy and conversion logic with focused tests. Establish contract
  behavior through public callers, shared scenarios, and discriminating violations.
- Check installed packages separately from source-tree tests. Git consumers resolve
  their own dependencies and cannot rely on the producer's checkout or environment.
- Exercise replacement through the public corpus after rewiring the owned bindings.
  Do not turn the private connection into a certified third-party engine protocol.

See [development checks](checks.md) for the commands and installation environments.

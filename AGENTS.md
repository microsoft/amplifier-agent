# AGENTS.md: amplifier-agent

Notes for AI agents and humans working **on** this repo. For what the repo
*produces*, see [`README.md`](README.md). For the integration architecture
across all downstream surfaces (layer stack, publish points, version numbers,
and the change-to-release impact matrix), see
[`docs/LAYERS_AND_RELEASES.md`](docs/LAYERS_AND_RELEASES.md). It is the
single source of truth for "if I change X, what needs to be released."

## TL;DR

This is a **multi-artifact monorepo**: one Python engine + CLI, one TypeScript
wrapper SDK, one Python wrapper SDK. Each is independently versioned, with its
own release tag namespace. The wire protocol between engine and wrappers is
**versioned and validated**; mismatches return errors, not silent misbehavior.

The thing that bites people: **bumping the protocol or one wrapper without
coordinating the others.** Read [Cross-component invariants](#cross-component-invariants)
before any change that touches `protocol/`, a wrapper, or a release tag.

---

## Authority

`contracts/` is normative. The six `contracts/*.v1.md` files are FROZEN and
define the v1 surface: what a caller may rely on, in every binding and every
face. [`contracts/README.md`](contracts/README.md) indexes them and says which
contract governs what.

Authority runs one direction: contract, then binding, then engine. Where an
implementation and a contract disagree, **the implementation is what is
wrong.** A clause the code cannot satisfy is a defect in the code, never
grounds to edit the clause.

Editing a `.v1.md` is not a normal change. A frozen clause moves only through a
CANDIDATE amendment the owner ratifies. Changes that move code *toward* a
contract need no amendment and are the ordinary work.

[`docs_v1/`](docs_v1/index.md) is the guide tree for that surface, and it
supersedes `docs/`. `concepts/` carries the semantics once; `python/` and
`typescript/` carry spelling and the contract-name to local-name mapping each
binding owes; `http/` covers the face.

Unlike `contracts/`, `docs_v1/` is meant to be edited as the implementation
lands. A change that moves a binding toward a contract updates the matching
`docs_v1/` page in the same PR. Write each page as the finished description of
the contracted surface, not of whatever happens to work.

`docs/` describes the engine and CLI as they ship. It is descriptive, not
normative, and `contracts/` wins wherever the two disagree.

---

## What lives where

| Path | What it is |
|---|---|
| `src/amplifier_agent_lib/` | Transport-free engine library (`Engine`, runtime, persistence, bundle, protocol) |
| `src/amplifier_agent_lib/protocol/` | `methods.py` (source of truth), generated `schemas/` + `spec.md`, `conformance/` |
| `src/amplifier_agent_lib/protocol_points/` | `ProtocolPoint` base + the CLI and HTTP `display`/`approval` defaults injected at `Engine.boot()` |
| `src/amplifier_agent_lib/config/` | Host-config loader + the layered merger that overlays host config onto bundle module config |
| `src/amplifier_agent_lib/resources.py` | Shared skills/modes discovery; single source of truth for CLI `list` commands and the `/v1/skills` + `/v1/modes` routes |
| `src/amplifier_agent_lib/bundle/skills/` | Vendored built-in skills (`code-review`, `council` + 6 council lens skills), force-included into the wheel |
| `src/amplifier_agent_lib/bundle/modes/` | Vendored built-in modes (`plan`, `brainstorm`), force-included into the wheel |
| `src/amplifier_agent_cli/` | Click-based CLI adapter on top of the library (`run` mode + admin verbs) |
| `src/amplifier_agent_http/` | FastAPI OpenAI-compatible front end (`serve`): `/v1/chat/completions`, `/v1/models`, `/v1/skills`, `/v1/modes` |
| `wrappers/typescript/` | `amplifier-agent-ts`: published to npm via OIDC on `wrapper-v*` tags |
| `wrappers/python-py/` | `amplifier-agent-py`: Python wrapper SDK (uv workspace member) |
| `wrappers/conformance/` | YAML fixtures + Python and TS runners. **Cross-validates both wrappers.** |
| `tests/e2e/` | DTU-based end-to-end suites covering the shipped CLI and HTTP surfaces. There is no unit test tier. See [Three tiers](#three-tiers-spec-e2e-eval). |
| `tests/windows/` | Windows-container end-to-end suites, gated by `-m windows`. An approximation that catches Windows-specific breakage; shares no framework code with `tests/e2e/` and parity between them is a non-goal. See [`docs/E2E_TESTING_WINDOWS.md`](docs/E2E_TESTING_WINDOWS.md). |
| `.amplifier/evaluation/` | Evaluation harness that measures probabilistic agent behavior. |
| `scripts/` | Standalone release/contract guard scripts (`verify-*`), deliberately not pytest. |
| `contracts/` | The FROZEN v1 contracts. Normative. See [Authority](#authority). |
| `docs_v1/` | Guides for the v1 surface, organized per binding. Supersedes `docs/`. See [Authority](#authority). |
| `docs/` | Guides, architecture, and specs for the engine and CLI as they ship. See [Docs map](#docs-map). |
| `notes/` | Durable, checked-in working notes (e.g. coverage gaps, reproducibility notes). Not scratch; do NOT sweep it during a release. |
| `.github/workflows/` | `ci.yml`, `publish-python.yml`, `publish-wrapper.yml`, `release-notes.yml`, `install-script.yml` |
| `DEVELOPMENT.md` | Maintainer setup, the command surface, the development skills, and the DTU/Gitea harnesses |
| `RELEASING.md` | Release steps for all three artifacts + one-time PyPI trusted publisher setup |
| `.amplifier/skills/` | The four development skills: new-feature, bugfix, start-release, finish-release. See [`DEVELOPMENT.md`](DEVELOPMENT.md). |

`Makefile` at the repo root is the canonical command surface. See
[Build, lint, test](#build-lint-test).

---

## Three tiers: spec, e2e, eval

This repo is developed spec + e2e + eval driven. There is no unit test tier,
and that is intentional, not a gap:

```
docs/spec/               describes the shipped CLI + HTTP surfaces in prose
tests/e2e/               proves that description against the real CLI + HTTP
                         server in a DTU
tests/windows/           approximates the same against the real CLI in a
                         Windows container; catches Windows-only breakage
.amplifier/evaluation/   measures probabilistic agent behavior
scripts/verify-*         release and contract guards (NOT tests, deliberately
                         kept out of tests/ so `tests/` unambiguously means
                         "the e2e contract")
```

A change to documented behavior updates `docs/spec/`. A change to observable
behavior gets an e2e case. A change to judgment-laden output quality gets an
eval task.
Nothing gets a unit test, because there is no tier for it: if it needs
coverage, it goes in one of the three above.

---

## Docs map

Three layers. **Contracts** are normative: observable behavior only, and absence
is part of the contract. **Specs** describe what ships. **Guides** are the front
door: task-oriented, written for someone integrating or operating the thing. A
guide never restates a contract, it links to it.

[`contracts/README.md`](contracts/README.md) indexes the contracts.
`docs_v1/index.md` indexes the v1 guides. `README.md` and
[`docs/SPEC.md`](docs/SPEC.md) index the guides and specs for what ships.

```
Normative
contracts/README.md         index of the v1 contracts, and the freeze bar
contracts/*.v1.md           the frozen v1 surface
contracts/VISION.md         what the surface is for

Guides for the v1 surface
docs_v1/index.md            index
docs_v1/concepts/           the semantics, once, shared by every binding
docs_v1/python/             Python spelling and contract-name mapping
docs_v1/typescript/         TypeScript spelling and contract-name mapping
docs_v1/http/               the HTTP face

Guides
docs/INTEGRATION.md         the entry point for embedding the engine: SDKs,
                            in-process library, HTTP face, wire protocol
docs/CONFIGURATION.md       providers, credentials, approval policy, host config
docs/INSTALL.md             install, pin, update, uninstall, CI and containers
docs/CLI.md                 every command and flag, output/display modes
docs/ECOSYSTEM.md           applications built on amplifier-agent
DEVELOPMENT.md              maintainer setup, command surface, dev skills,
                            DTU/Gitea harnesses (repo root, not docs/)

Contracts and structure
docs/SPEC.md                index of the contracts
docs/spec/                  the contract specifications, one file per surface
docs/ARCHITECTURE.md        what the system is and how the pieces connect
docs/architecture/          the diagram, its source, and detailed data-flow traces
docs/E2E_TESTING.md         the end-to-end test framework and how to add a suite
docs/E2E_TESTING_WINDOWS.md the Windows-container harness, its prereqs, and WSL2
docs/LAYERS_AND_RELEASES.md which layer a change lands in and what to release
```

When a spec under `docs/spec/` changes, check whether the corresponding guide
asserts the old behavior. The guides are the surface integrators read first, so
a stale guide is worse than a missing one.

---

## Build, lint, test

`Makefile` at the repo root is the canonical command surface. CI invokes these
same targets, so local and CI cannot drift. [`DEVELOPMENT.md`](DEVELOPMENT.md)
covers first-time setup, what each target costs, and the prerequisites for the
DTU-backed targets.

```bash
uv sync --all-extras --dev

make check    # lint + format-check + pyright. Seconds. Run this constantly.
make verify   # check + every contract/release guard (~1 min). Pre-PR gate.
make e2e SUITE=<name>   # run one e2e suite against a DTU
make eval TASK=<id>     # run one evaluation task against a DTU
make fmt      # auto-fix formatting and lint
```

`make verify` runs `make check` plus `verify-codegen`, `verify-wheel`,
`verify-parity`, and `verify-wrapper`. Run `make help` for the full target list
with descriptions.

**The conformance suite (`make verify-parity`) is non-negotiable for protocol
or wrapper changes.** It spawns both the Python and TS wrappers against the
same YAML fixtures. CI runs it (`wrappers/conformance/verify-parity.py`) on
every PR and on every release tag. If you're touching protocol or either
wrapper, run it locally first.

**End-to-end tests run the real CLI and HTTP server in an isolated DTU.** See
[`docs/E2E_TESTING.md`](docs/E2E_TESTING.md). This is the only test tier and
the way to add coverage for user-facing behavior. Add a suite under
`tests/e2e/suites/<feature>/`. Run `make e2e SUITE=<feature>` (or
`uv run python tests/e2e/framework/cli.py run <feature>` directly) and make
sure it passes before opening a PR. Neither `make e2e` nor `make eval` runs in
CI: both need a DTU, which GitHub-hosted runners cannot provide, so this local
run is the only gate for those two tiers. See [Three tiers](#three-tiers-spec-e2e-eval).

---

## Cross-component invariants

These are the rules that have bitten contributors. Honor them or expect failed
CI and broken downstreams.

### 1. Protocol bumps require coordinated wrapper updates

`PROTOCOL_VERSION` lives in `src/amplifier_agent_lib/protocol/methods.py`. When
you bump it:

- Update **both** wrappers' pinned `--protocol-version` value
- Update the conformance fixtures under
  `src/amplifier_agent_lib/protocol/conformance/fixtures/`. Fixtures pinning the
  skew sentinel `2099-12-future-vN` are the exception: they exist to prove the
  engine refuses a foreign protocol version, so they must stay stale on purpose
- Update the protocol version stated in the docs, currently
  `docs/INTEGRATION.md` (the prose pin and the `--protocol-version` example)
- Land all of these in **one PR**. Splitting them across PRs leaves `main` in a
  broken state where one wrapper rejects the engine.

Nothing enforces this mechanically. There used to be a `verify-versions` gate
that regex-matched the version out of each file, but it broke every time a doc
was reworded, which made it a tax on prose rather than a guard on the protocol.
Finding the pins is now the job of whoever bumps the version: search the repo
for the current value and update every hit you can justify. The release skill
(`.amplifier/skills/amplifier-agent-start-release-process/`) walks an agent
through exactly that.

### 2. Three artifacts, multiple tag namespaces

| Artifact | Tag prefix | Published to |
|---|---|---|
| Python engine + CLI | `v*` | PyPI (OIDC, via `publish-python.yml`) + GitHub Release |
| TypeScript wrapper SDK | `wrapper-v*` | npm (OIDC, via `publish-wrapper.yml`) + GitHub Release |
| Python wrapper SDK | `py-v*` | PyPI (OIDC, via `publish-python.yml`) |

All three publishes are automated via OIDC trusted publishing; no tokens are
stored in the repo.

> **Note:** the engine is published to PyPI on every `v*` tag, but **git is the
> supported install channel.** `install.sh` and `amplifier-agent update` both
> install from git, and nothing in this repo installs the engine from PyPI. See
> [`docs/spec/install-and-distribution.md`](docs/spec/install-and-distribution.md).

See [`RELEASING.md`](RELEASING.md) for the full step-by-step release procedure and
the one-time PyPI trusted publisher setup checklist.

Bumping a version means updating the *correct* `pyproject.toml` / `package.json`
**and** the changelog **and** pushing the matching tag namespace. The wrong tag
namespace silently won't trigger the right workflow.

### 3. Wrappers are siblings; one move forces the other

A protocol bump bumps both. A wrapper-only feature (e.g. a new helper method)
should still preserve behavioral parity unless explicitly scoped otherwise.
The conformance suite enforces this.

### 4. Migrations are user-invoked, not automatic (since PR #52)

Storage layout migrations run only when the user explicitly calls
`amplifier-agent migrate`. Do **not** trigger migrations from `Engine.boot()`,
`doctor`, or any other code path.

What the engine does on an un-migrated layout is *nothing*: `Engine.boot()`
performs no layout check and nothing else detects or warns about a legacy
on-disk layout, so a stale flat `sessions/` tree is simply not found and a fresh
workspace-nested one is created alongside it. The user has to know to run
`amplifier-agent migrate`.

### 5. stdout is reserved for the JSON envelope

The CLI emits exactly **one JSON line** on stdout per invocation. All diagnostic
output (tool calls, thinking, progress, warnings) goes to **stderr**. Adding a
`print(...)` to a code path that the CLI exercises will break wrapper parsing.
When in doubt, write to `sys.stderr` or use the `display` protocol point.

### 6. The bundle is baked into the wheel

`src/amplifier_agent_lib/bundle/bundle.md` and friends are shipped inside the
wheel via `hatchling`'s `force-include`. If you add files to the bundle, update
the `force-include` list in `pyproject.toml`. First-run cache prep depends on
these files being present in the installed package. This includes the vendored
`bundle/skills/*/SKILL.md` and `bundle/modes/*.md` assets: each must be listed
in `force-include` or first-run discovery of the built-in skills/modes silently
misses it.

---

## Specs are the durable output, not design docs

Design docs are **transient working artifacts**, not repo content. Write one if
it helps you think, share it in the PR description or a scratch file, then throw
it away. Do not check one in.

What is durable is [`contracts/`](contracts/README.md) and the guides that
project it. **A change to documented behavior and the doc update for it belong
in the same change.** If your PR alters the wire protocol, the CLI surface, the
envelope, storage layout, the HTTP face, or any other documented behavior, the
matching `docs/spec/*.md` edit is part of that PR, not a follow-up. If it moves
a binding toward a contract, the matching `docs_v1/` page is too.

The contracts are the record of *what a caller may rely on*; the specs and
guides are the record of *what ships*; the code is the record of *how it is
met*; git history is the record of *why*.

---

## Commits and PRs

Conventional commits with scope. Observed scopes in recent history:

| Scope | Used for |
|---|---|
| `feat(engine)` / `fix(engine)` | Engine library or CLI changes |
| `feat(cli)` | CLI-flag-level changes |
| `feat(wrapper-ts)` / `fix(wrapper-ts)` | TypeScript wrapper SDK |
| `feat(wrapper-py)` / `fix(wrapper-py)` | Python wrapper SDK |
| `refactor(migration)` | Migration system changes |
| `chore(release)` | Version bumps for release tags |

PR titles use the same scope. A coordinated change touching engine + both
wrappers picks the broadest scope (usually `feat(engine)`) and describes the
cross-component impact in the body.

---

## Common pitfalls

- **Forgetting the conformance suite needs pnpm/tsx.** CI runs Python + Node
  together. `make verify-parity` auto-installs pnpm deps in
  `wrappers/conformance/` when `node_modules/` is missing; running `pnpm test`
  directly in that directory still needs `pnpm install` first.
- **Running the wrong gate.** `make check` and `make verify` handle working
  directory for you: `make verify-wrapper` cds into `wrappers/typescript/`.
  `make verify-parity` runs `wrappers/conformance/verify-parity.py` from the
  repo root; it only cds into `wrappers/conformance/` for the conditional
  `pnpm install` when `node_modules/` is missing. If you invoke a tool
  directly instead of through `make`, remember TS tests still need
  `wrappers/typescript/` as the working directory.
- **Writing to stdout from anywhere the CLI might call.** See invariant #5.
- **Auto-triggering migrations.** See invariant #4.
- **Backgrounding long harness runs without detaching them.** The e2e and
  evaluation harnesses take several minutes (DTU launch, install, run, grade). If
  an agent backgrounds one with a plain `nohup ... &` inside a tool call, a tool
  timeout kills the whole process group and orphans the DTU container. Launch
  fully detached and poll the log / `state.json` instead:

  ```bash
  setsid bash -c '<run cmd> > /tmp/run.log 2>&1' < /dev/null > /dev/null 2>&1 &
  ```
- **Bumping `pyproject.toml` version without tagging.** Version in the file is
  the *target* of the next tag; the tag is what releases. Both must move
  together.
- **Stale bundle cache + tool venv hiding upstream module fixes.** When a
  module fails with `No module named '...'` or `Module ... failed validation`
  after a `bundle.md` change (or even after an unrelated `uv tool install`),
  the cause is usually a stale checkout in the tool venv, *not* a missing
  dep at the AAA layer. Before adding anything to `pyproject.toml`
  `dependencies`, reset and refresh:

  ```bash
  find ~/.amplifier-agent/cache/prepared/<version>/ -mindepth 1 -delete
  uv tool uninstall amplifier-agent
  uv tool install --refresh --from . amplifier-agent
  amplifier-agent doctor
  ```

  Foundation's resolver *does* follow transitive deps declared in upstream
  module `pyproject.toml`s, but only when given a fresh git clone. The
  cached venv from an earlier install can be missing them. The existing
  `mcp` entry in our `pyproject.toml` is the legacy precedent and may be
  vestigial; don't add new entries in that style without first proving the
  install gap survives a `--refresh` reinstall.

---

## What "done" looks like

For a typical change:

1. `make verify` passes clean (lint, types, and every contract/release guard)
2. If user-facing behavior changed: the relevant `tests/e2e/suites/<feature>/`
   passes (`make e2e SUITE=<feature>`)
3. If documented behavior changed, the matching `docs/spec/*.md` or `docs_v1/`
   page is updated in the same PR
4. PR description states the scope of impact (engine-only / wrapper-only /
   coordinated cross-component)
5. CHANGELOG.md updated under the right section if user-visible

---

## When in doubt

- For what a caller may rely on, read [`contracts/`](contracts/README.md). For
  what ships, read the relevant [`docs/spec/*.md`](docs/SPEC.md), but verify any
  protocol method against `Engine.dispatch` before relying on it: not every
  declared method is implemented.
- For wire-protocol questions: `src/amplifier_agent_lib/protocol/methods.py` is
  the source of truth. The generated `protocol/spec.md` is downstream of it and
  `make verify-codegen` asserts it stays byte-identical to the generator's
  output; the gap to watch is declared-vs-implemented (see above), not
  codegen staleness.
- For wrapper behavior: `wrappers/conformance/` fixtures encode the contract.
- For release process: see [`RELEASING.md`](RELEASING.md) for the full procedure
  (PyPI tag conventions, trusted publisher setup, version verification steps).

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
| `tests/` | Engine/CLI/HTTP/persistence/migration tests, plus `integration/` and `e2e/` suites. |
| `docs/` | Architecture and contract specs. See [Docs map](#docs-map). |
| `.github/workflows/` | `ci.yml`, `publish-python.yml`, `publish-wrapper.yml`, `release-notes.yml`, `install-script.yml` |
| `RELEASING.md` | Release steps for all three artifacts + one-time PyPI trusted publisher setup |

No `Makefile`, no `justfile`. Commands are direct `uv run` / `bun run` calls.

---

## Docs map

Two entry points: [`docs/SPEC.md`](docs/SPEC.md) for contracts,
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for structure. Everything else
hangs off one of those.

```
docs/ARCHITECTURE.md        what the system is and how the pieces connect
docs/architecture/          the diagram, its source, and detailed data-flow traces
docs/SPEC.md                index of the contracts
docs/spec/                  the contract specifications, one file per surface
docs/E2E_TESTING.md         the end-to-end test framework and how to add a suite
docs/LAYERS_AND_RELEASES.md which layer a change lands in and what to release
```

---

## Build, lint, test

These are the gates. Pass all of them before calling work "done."

```bash
# Python engine + CLI + library
uv sync --all-extras --dev
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pyright src/
uv run pytest tests/ -q

# TypeScript wrapper
cd wrappers/typescript
bun install
bun run build
bun run test

# Cross-language conformance (requires BOTH Python AND Node on PATH)
cd wrappers/conformance
pnpm install
pnpm test
```

**The conformance suite is non-negotiable for protocol or wrapper changes.** It
spawns both the Python and TS wrappers against the same YAML fixtures. CI runs
it on every PR. If you're touching protocol or either wrapper, run it locally
first.

**End-to-end tests run the real CLI and HTTP server in an isolated DTU.** See
[`docs/E2E_TESTING.md`](docs/E2E_TESTING.md). This is the preferred way to add
tests for user-facing behavior. Add a suite under `tests/e2e/suites/<feature>/`.
Run `uv run python tests/e2e/framework/cli.py run` and make sure the e2e suite
passes before opening a PR.

---

## Cross-component invariants

These are the rules that have bitten contributors. Honor them or expect failed
CI and broken downstreams.

### 1. Protocol bumps require coordinated wrapper updates

`PROTOCOL_VERSION` lives in `src/amplifier_agent_lib/protocol/methods.py`. When
you bump it:

- Update **both** wrappers' pinned `--protocol-version` value
- Update `wrappers/conformance/` fixtures and `test_protocol_version_bump.py`
- Update the protocol version stated in `README.md`
- Land all of these in **one PR**. Splitting them across PRs leaves `main` in a
  broken state where one wrapper rejects the engine.

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

What is durable is [`docs/spec/`](docs/SPEC.md). **A change to a contract and
the spec update for it belong in the same change.** If your PR alters the wire
protocol, the CLI surface, the envelope, storage layout, the HTTP face, or any
other documented behavior, the matching `docs/spec/*.md` edit is part of that
PR, not a follow-up.

The spec is the record of *what the contract is*; the code is the record of
*how it is met*; git history is the record of *why*.

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
  together; locally, `pnpm install` in `wrappers/conformance/` is required
  before `pnpm test`.
- **Running tests from the wrong directory.** Engine tests run from repo root;
  TS tests run from `wrappers/typescript/`; conformance from
  `wrappers/conformance/`. There is no aggregator script.
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

1. `ruff check`, `ruff format --check`, `pyright`, `pytest tests/ -q` all pass
2. If wrappers or protocol changed: `bun run build && bun run test` in
   `wrappers/typescript/`, and `pnpm test` in `wrappers/conformance/` all pass
3. If a documented contract changed, the matching `docs/spec/*.md` is updated in
   the same PR
4. PR description states the scope of impact (engine-only / wrapper-only /
   coordinated cross-component)
5. CHANGELOG.md updated under the right section if user-visible

---

## When in doubt

- Read the relevant [`docs/spec/*.md`](docs/SPEC.md) first, but verify any
  protocol method against `Engine.dispatch` before relying on it: not every
  declared method is implemented.
- For wire-protocol questions: `src/amplifier_agent_lib/protocol/methods.py` is
  the source of truth. The generated `protocol/spec.md` is downstream of it and
  is known to disagree in places.
- For wrapper behavior: `wrappers/conformance/` fixtures encode the contract.
- For release process: see [`RELEASING.md`](RELEASING.md) for the full procedure
  (PyPI tag conventions, trusted publisher setup, version verification steps).

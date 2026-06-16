# AGENTS.md — amplifier-agent

Notes for AI agents and humans working **on** this repo. For what the repo
*produces*, see [`README.md`](README.md).

## TL;DR

This is a **multi-artifact monorepo**: one Python engine + CLI, one TypeScript
wrapper SDK, one Python wrapper SDK — each independently versioned, each with
its own release tag namespace. The wire protocol between engine and wrappers is
**versioned and validated**; mismatches return errors, not silent misbehavior.

The thing that bites people: **bumping the protocol or one wrapper without
coordinating the others.** Read [Cross-component invariants](#cross-component-invariants)
before any change that touches `protocol/`, a wrapper, or a release tag.

---

## What lives where

| Path | What it is |
|---|---|
| `src/amplifier_agent_lib/` | Transport-free engine library (`Engine`, runtime, persistence, bundle, protocol) |
| `src/amplifier_agent_cli/` | Click-based CLI adapter on top of the library |
| `wrappers/typescript/` | `amplifier-agent-ts` — published to npm via OIDC on `wrapper-v*` tags |
| `wrappers/python/` | `amplifier-agent-py` — Python wrapper SDK (uv workspace member) |
| `wrappers/conformance/` | YAML fixtures + Python and TS runners. **Cross-validates both wrappers.** |
| `tests/` | Engine/CLI/persistence/migration tests. Integration tests are marked separately. |
| `docs/designs/` | Dated design docs (`YYYY-MM-DD-slug.md`). Most non-trivial changes start here. |
| `.github/workflows/` | `ci.yml`, `publish-wrapper.yml`, `release-notes.yml` |

No `Makefile`, no `justfile`. Commands are direct `uv run` / `bun run` calls.

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
it on every PR — if you're touching protocol or either wrapper, run it locally
first.

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

### 2. Three artifacts, three tag namespaces

| Artifact | Tag prefix | Published to |
|---|---|---|
| Python engine + CLI | `engine-v*` | git (uv tool install from tag) |
| TypeScript wrapper SDK | `wrapper-v*` | npm (OIDC, via `publish-wrapper.yml`) |
| Python wrapper SDK | `wrapper-py-v*` | git (used by Python hosts) |

Bumping a version means updating the *correct* `pyproject.toml` / `package.json`
**and** the changelog **and** pushing the matching tag namespace. The wrong tag
namespace silently won't trigger the right workflow.

### 3. Wrappers are siblings; one move forces the other

A protocol bump bumps both. A wrapper-only feature (e.g. a new helper method)
should still preserve behavioral parity unless explicitly scoped otherwise —
the conformance suite enforces this.

### 4. Migrations are user-invoked, not automatic (since PR #52)

Storage layout migrations run only when the user explicitly calls
`amplifier-agent migrate`. Do **not** trigger migrations from `Engine.boot()`,
`doctor`, or any other code path. The contract is: the engine refuses to run
against an outdated layout and tells the user to run `migrate`. Don't break
that.

### 5. stdout is reserved for the JSON envelope

The CLI emits exactly **one JSON line** on stdout per invocation. All diagnostic
output (tool calls, thinking, progress, warnings) goes to **stderr**. Adding a
`print(...)` to a code path that the CLI exercises will break wrapper parsing.
When in doubt, write to `sys.stderr` or use the `display` protocol point.

### 6. The bundle is baked into the wheel

`src/amplifier_agent_lib/bundle/bundle.md` and friends are shipped inside the
wheel via `hatchling`'s `force-include`. If you add files to the bundle, update
the `force-include` list in `pyproject.toml`. First-run cache prep depends on
these files being present in the installed package.

---

## Design-doc-driven development

Non-trivial changes are designed in `docs/designs/` **before** code lands. The
convention:

- Filename: `YYYY-MM-DD-slug.md` (date the design was written, not landed)
- The PR description links to the design doc section by anchor
- If you're amending a prior design, write a new dated doc that references it
  (see `2026-05-24-aaa-v2-mode-a-pivot-amendment.md` as the canonical pattern)

If you find yourself proposing a significant change in a PR description with no
linked design doc, stop and write the design first. The team uses these docs as
the record of *why*; the code is the record of *what*.

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
- **Bumping `pyproject.toml` version without tagging.** Version in the file is
  the *target* of the next tag; the tag is what releases. Both must move
  together.
- **Stale bundle cache + tool venv hiding upstream module fixes.** When a
  module fails with `No module named '...'` or `Module ... failed validation`
  after a `bundle.md` change (or even after an unrelated `uv tool install`),
  the cause is usually a stale checkout in the tool venv — *not* a missing
  dep at the AAA layer. Before adding anything to `pyproject.toml`
  `dependencies`, reset and refresh:

  ```bash
  find ~/.amplifier-agent/cache/prepared/<version>/ -mindepth 1 -delete
  uv tool uninstall amplifier-agent
  uv tool install --refresh --from . amplifier-agent
  amplifier-agent doctor
  ```

  Foundation's resolver *does* follow transitive deps declared in upstream
  module `pyproject.toml`s — but only when given a fresh git clone. The
  cached venv from an earlier install can be missing them. The existing
  `mcp` entry in our `pyproject.toml` is the legacy precedent and may be
  vestigial; don't add new entries in that style without first proving the
  install gap survives a `--refresh` reinstall.

---

## What "done" looks like

For a typical change:

1. `ruff check`, `ruff format --check`, `pyright`, `pytest tests/ -q` — all pass
2. If wrappers or protocol changed: `bun run build && bun run test` in
   `wrappers/typescript/`, and `pnpm test` in `wrappers/conformance/` — all pass
3. If the design doc exists, the PR description links to it
4. PR description states the scope of impact (engine-only / wrapper-only /
   coordinated cross-component)
5. CHANGELOG.md updated under the right section if user-visible

---

## When in doubt

- Read the relevant `docs/designs/*.md` first.
- For wire-protocol questions: `src/amplifier_agent_lib/protocol/methods.py` is
  the source of truth.
- For wrapper behavior: `wrappers/conformance/` fixtures encode the contract.
- For release process: look at the most recent `chore(release)` PR for the
  current pattern.

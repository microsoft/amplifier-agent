amplifier-agent is the Amplifier agent as a library: Python and TypeScript bindings and an HTTP face over one replaceable engine.

## Orientation

- Set up with `uv run setup-for-dev.py`. [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) has every command for linting, formatting, type checking, testing, and building.
- Run only the checks the change can affect, per the ladder in the workflow skills. `prek run --all-files` and `uv run --all-packages python scripts/check.py --runtime` must pass once before the work is done.
- Releases follow [RELEASING.md](RELEASING.md).
- Sources of truth, in order:
  ```
  contracts/VISION.md   why the project exists and what it will not build
  contracts/*.v1.md     language-neutral shape and laws: names, records, vocabularies, ordering, defaults
  docs/                 the current interface of each binding and the HTTP face
  evaluations/          real tasks with real models, through every surface
  tests/                implementation aids; part of no contract
  ```
- Authority runs one way: contract, then binding, then engine. Where code and a contract disagree, the code is wrong.
- Every feature or fix answers these, in order:
  1. **Vision and contracts.** Does it fit? If one must change, stop and raise it. Contracts change rarely, by owner-ratified amendment with a dated changelog entry.
  2. **Public surface.** Does it change what callers see? Update every place that describes it in the same change. A breaking change without a contract change needs explicit sign-off.
     ```
     docs/                     concepts, per-binding reference and names.md, http, install, configuration, providers
     README.md                 repo overview and quickstart
     packages/*/README.md      per-package install and first use
     skills/amplifier-agent/   the integration skill agents use to build on this
     ```
     Pages about internals (`docs/development/`, `docs/DEVELOPMENT.md`) change freely but stay accurate.
  3. **Evaluations.** Is the behavior covered by a task in `evaluations/`? Add one when it is not, and keep `evaluations/README.md` current when the harness changes.
  4. **Tests.** Add unit or integration tests where they help implementation or pin precise internal behavior an evaluation cannot observe.
- For a new feature, follow `.agents/skills/amplifier-agent-new-feature/`. For a bug, follow `.agents/skills/amplifier-agent-bugfix/`.
- Other branches hold code written before the contracts. It is not a reference: do not read it or port from it. If something there looks load-bearing, raise it.
- Failures name what went wrong and how to fix it. The caller is usually an agent.
- Never modify this file unless explicitly told.

## Writing

- Every file is read later by someone with no memory of the session, often a machine. This covers help text, docstrings, comments, and error messages.
- No status in artifacts: no "not implemented yet", "for now", "coming soon". Progress lives in the issue queue and git history.
- Never describe the format inside an instance of the format.
- When working from a spec, fixture, or reference implementation, copy the structure, never the prose.
- Be concise, prefer code blocks over tables, and never write em dashes.

## Python development

- `uv` manages packages and projects; start with `uv --help`. Add dependencies with `uv add <package>`, then match the version bound convention of neighbouring entries in `pyproject.toml`.
- `ty` is the type checker. Never use `# type: ignore` or `# ty: ignore`; leave the issue and raise it.
- `ruff` owns lint and style. A rule wrong for one file gets a `per-file-ignores` entry with a reason, never an inline `noqa`, unless the flagged name is part of the contracted surface.
- Comments record only what code cannot: rationale, trade-offs, links to specs, non-obvious domain facts.
- The existing code is the style reference. Follow the Google Python Style Guide where it is silent.
- Keep `__init__.py` files empty unless the contract requires an export there.
- Use `pathlib`, `Path.open` over `open`, and `.parents[i]` over repeated `.parent`.
- Tests use pytest and pytest-asyncio. Narrow optionals with `assert value is not None`.
- Never add a bare `*,` keyword-only marker; write plain parameters and call them by keyword.
- Only characters a keyboard types: no fancy arrows or typographic quotes.
- Read a dependency's source to learn its types and behaviour rather than guessing.
- When something fails, investigate from first principles rather than blaming the environment.

## TypeScript development

- `pnpm` runs from `packages/typescript`. `pnpm check` (Biome, then `tsc --noEmit`) and `pnpm test` must pass.
- `tsconfig.json` stays strict; `biome.json` decides style. Tests under `test/` use `node:test` and may use non-null assertions; `src/` may not.
- The binding is written against the contracts, not ported from Python.
- Contract names may be respelled to TypeScript conventions, such as camelCase for fields callers construct (`docs/typescript/names.md`). Record the mapping in `names.md`.

## Commits and pull requests

- State facts and rationale only. Never internal working steps, plan files, session transcripts, or any non-public artifact.

## Key Files

@contracts/README.md
@contracts/VISION.md
@docs/DEVELOPMENT.md

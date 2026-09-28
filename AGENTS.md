# AGENTS.md: amplifier-agent

Notes for AI agents and humans working **on** this repo. For what the repo
presents to callers, start at [`contracts/README.md`](contracts/README.md).

## Authority

`contracts/` is normative. The six frozen `contracts/*.v1.md` files define the v1
surface: what a caller may rely on, in every binding and every face.
[`contracts/README.md`](contracts/README.md) indexes them and says which contract
governs what.

Authority runs one direction, and only one:

```
contract  ->  binding  ->  engine

the contract defines the surface
each binding presents it
the engine has to satisfy it
```

Where an implementation and a contract disagree, **the implementation is what is
wrong.** A clause the code cannot satisfy is a defect in the code, never grounds to
edit the clause.

Editing a `.v1.md` is not a normal change. A frozen clause moves only through a
CANDIDATE amendment the owner ratifies, paid for in evidence: a break, a cost, a
platform that will not do it. Changes that move code *toward* a contract need no
amendment and are the ordinary work.

[`contracts/VISION.md`](contracts/VISION.md) sets direction and pins nothing. It
never settles a signature. Its one demand on the contracts is that none of them pin
something that puts that direction out of reach.

## What lives where

```
contracts/               the frozen v1 contracts, normative
docs/                    the guide tree for the contracted surface
packages/                python (SDK), engine, http: one uv workspace;
                         typescript: the independent binding, pnpm
conformance/             the kit that grades compatibility
evaluations/             live capability evaluations in containers;
                         self-contained, with its own pyproject and lock
.amplifier/evaluation/   harness measuring probabilistic agent behavior;
                         self-contained, outside the precommit hooks
```

`docs/` is edited as the implementation lands, unlike `contracts/`. `concepts/`
carries the semantics once; `python/` and `typescript/` carry spelling and the
contract-name to local-name mapping each binding owes; `http/` covers the face. A
change that moves a binding toward a contract updates the matching `docs/` page
in the same pull request.

## Development

- Set up with `uv run setup-for-dev.py`. [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md)
  has every command for linting, formatting, type checking, and testing each surface.
- `prek run --all-files` and the test suites in `docs/DEVELOPMENT.md` must pass before
  the work is done. The hooks fix what they can; fix the rest, never suppress it.
- Never modify this file unless explicitly told.

## Python development

- `uv` is the package and project manager; start with `uv --help`. Add dependencies with
  `uv add <package>`, then edit the version bound in `pyproject.toml` to match the
  convention of the neighbouring entries.
- `ty` is the type checker. Write type hints that pass it. NEVER use `# type: ignore`
  or `# ty: ignore`; leave the issue and raise it instead.
- `ruff` owns lint and style, with the rule set in `pyproject.toml`. A rule that is wrong
  for one file gets a `per-file-ignores` entry with a reason, never an inline `noqa`,
  unless the name it flags is part of the contracted surface.
- Comments record only what code cannot: rationale, trade-offs, links to specs, non-obvious
  domain facts. Everything else goes in names, types, and structure.
- The existing code is the style reference. Follow the Google Python Style Guide where it
  is silent.
- Keep `__init__.py` files empty unless the contract requires an export there.
- Use `pathlib` for files, `Path.open` over `open`, and `.parents[i]` over repeated `.parent`.
- Tests use pytest and pytest-asyncio. Narrow optionals with `assert value is not None`
  instead of reaching through them.
- Never add a bare `*,` keyword-only marker; write plain parameters and call them by keyword.
- Only characters a keyboard types: no fancy arrows or typographic quotes in code or prose.
- Read the source of a dependency to learn its types and behaviour rather than guessing.
- Anything is possible. When something fails, investigate from first principles rather than
  blaming the environment.

## TypeScript development

- `pnpm` runs from inside `packages/typescript`. `pnpm check` (Biome, then `tsc --noEmit`)
  and `pnpm test` must pass.
- `tsconfig.json` is strict and stays strict; `biome.json` decides style. Tests under
  `test/` use `node:test` and may use non-null assertions; `src/` may not.
- The binding is written against the contracts, not ported from Python. See "How work is
  graded" below.

## The pre-v1 implementation is not a reference

Other branches hold code written before these contracts existed, against a different
architecture and a different vocabulary. Do not read it, and do not port from it.
Nothing there carries authority: not its structure, not its naming, not its prose,
not its choices. Reading it to see "how it was done" is how the thing the contracts
were written to replace comes back.

The contracts say what the surface is. `docs/` says how it is explained. For facts
about what the engine is built on, read those upstream repositories directly, at their
current state, rather than any past reading of them here.

If something in an earlier branch looks load-bearing, raise it rather than adopting
it. Absence is frequently deliberate.

## How work is graded

Passing the conformance kit is the definition of compatible.
[`contracts/README.md`](contracts/README.md) names its three parts:

```
runtime black-box scenarios   public APIs only, against the stub provider, so
                              every assertion is a property of the engine rather
                              than of the model
static surface lint           denylist scan and record shapes
replacement acceptance        the same scenarios, a replaced engine, new sessions
```

The stub provider is part of the kit, not of any contract surface.

A claim that something satisfies a contract is worth nothing without the fixture or
lint rule that proves it. Every normative clause maps to something in the kit that
fails when the clause is violated. A fixture that passes against both correct and
broken behavior is not discriminating and does not count as coverage.

`agent-interface` and `turn-events` are proven by Python plus one independently
implemented non-Python binding. Independently implemented means written against the
contract, not ported from the Python one. No binding is the reference, including the
one written in the engine's own language.

## What to read

Two pages before writing anything:

- [`contracts/README.md`](contracts/README.md) indexes the set, says which contract
  governs what, and carries the house rules every contract inherits.
- [`contracts/VISION.md`](contracts/VISION.md) says where this is headed and what it
  deliberately resists.

Open the individual `*.v1.md` files as the work reaches them. `contracts/README.md`
says which one that is.

## Writing

Every file here is read later by someone with no memory of the session that produced
it, and often by a machine. Write for that reader. This covers help text, docstrings,
comments, and error messages, not just markdown.

- No status in artifacts. No "not implemented yet", "for now", "coming soon", "TODO:
  remove this later". If a sentence becomes false after the next commit, it does not
  belong in the file. Progress lives in the issue queue and git history.
- Never describe the format inside an instance of the format.
- When working from a spec, fixture, or reference implementation, copy the structure,
  never the prose.
- Write each page as the finished description of the contracted surface, not of
  whatever happens to work today.

## Commits and pull requests

State facts and rationale only. Never internal working steps, plan files, session
transcripts, or any non-public artifact.

Amend the governing document, file work items against the amendment, then execute.
Code ahead of its document is drift. Never edit a document to describe what already
happened.

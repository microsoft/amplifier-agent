# Development

Maintainer guide: how to set up a machine, which command to run when, and how the four development skills drive feature, bugfix, and release work.

This file owns **setup, the command surface, and the harnesses**. Three other files own the rest, and this one links rather than repeats:

- [`AGENTS.md`](AGENTS.md): repo layout, cross-component invariants, commit and PR conventions, what "done" looks like
- [`RELEASING.md`](RELEASING.md): the mechanical release facts, which tag publishes what, PyPI trusted publisher setup
- [`docs/E2E_TESTING.md`](docs/E2E_TESTING.md): how the e2e framework works internally and how to add a suite

## Prerequisites

Three tiers. You only need the later ones when you run the things that use them.

**Core.** Enough for `make check`, `make fmt`, and most of `make verify`.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Python 3.12 or later, resolved by uv from `requires-python`. There is no `.python-version` file.

**Conformance.** Needed by two `verify` targets that cross-validate the wrappers.

```
pnpm     # make verify-parity, via Node 20+
bun      # make verify-wrapper
```

Both targets fail loud with an install hint rather than skipping, so you will know.

**The DTU harness.** Needed by `make e2e` and `make eval`, and therefore by every development skill.

```bash
uv tool install git+https://github.com/microsoft/amplifier-bundle-digital-twin-universe@main
uv tool install git+https://github.com/microsoft/amplifier-bundle-gitea@main
```

Plus two runtimes those CLIs drive:

- **Incus** runs the DTU containers. Verify with `incus version`. First use needs a one-time `incus admin init`.
- **Docker** runs the Gitea container. Verify with `docker info`. On WSL2, use Docker Desktop with WSL integration enabled.

The harness preflights `uv`, `amplifier-digital-twin`, `amplifier-gitea`, `incus`, and `docker` on `up` and `run`, and tells you exactly what is missing.

**Environment.**

```bash
export ANTHROPIC_API_KEY=sk-ant-...     # required by the e2e and eval harnesses
export GITHUB_TOKEN=$(gh auth token)    # only for the github_copilot e2e suite
```

`GITHUB_TOKEN` is snapshotted into the container when the DTU launches. Exporting it against a running DTU has no effect; re-provision after setting it.

## Setup

```bash
uv sync --all-extras --dev
make check
```

The evaluation harness is a separate uv project with its own lock, so it needs its own sync when you use it:

```bash
cd .amplifier/evaluation && uv sync
```

## The command surface

`Makefile` is canonical. CI invokes these same targets, so local and CI cannot drift. A bare `make` prints the list.

| Target | Cost | When |
|---|---|---|
| `make check` | seconds | Constantly, while iterating. Ruff lint, format check, pyright on `src/` |
| `make fmt` | seconds | Auto-fix formatting and lint |
| `make verify` | ~1 min | Before every PR. `check` plus every contract and release guard |
| `make verify-codegen` | seconds | Checked-in protocol `spec.md` and schemas match the generator |
| `make verify-versions` | seconds | Protocol version agrees across engine, both wrappers, README, fixtures |
| `make verify-wheel` | slower | Built wheel ships the protocol spec, schemas, fixtures, and all bundle content |
| `make verify-parity` | needs pnpm | Python and TypeScript runners agree on every conformance fixture |
| `make verify-wrapper` | needs bun | Build and test the published TypeScript SDK |
| `make e2e SUITE=<name>` | minutes, needs DTU | One e2e suite against a real DTU |
| `make eval TASK=<id>` | slowest, needs DTU | One evaluation task |

`make verify` prints `verify: ALL GATES PASSED` when clean. Omitting `SUITE=` or `TASK=` runs everything and warns you first.

## Development skills

Four skills under `.amplifier/skills/` encode the workflows. They are user-invocable only (`disable-model-invocation: true`), so they never fire on their own. Invoke by name with the work as the argument, from a session whose working directory is the repo root:

```
/amplifier-agent-new-feature <what you want to build>
/amplifier-agent-bugfix <the bug report, verbatim>
/amplifier-agent-start-release-process <what you are cutting>
/amplifier-agent-finish-release-process <the version you are finishing>
```

**Every one of these requires the DTU harness**, because all four either drive e2e suites or gate on them. Set up Incus, Docker, and the two CLIs above before invoking any of them, and export `ANTHROPIC_API_KEY`.

### `amplifier-agent-new-feature`

Ideation through working end-to-end in a DTU, using E2E-test-driven development. Orients you in the specs, makes you choose among options before writing code, walks red then green with a widening verification ladder, and stops at a handoff checklist. It does not release.

New suites land in `tests/e2e/suites/<feature>/`. Nothing under `tests/e2e/framework/` should change.

### `amplifier-agent-bugfix`

Report through verified fix. Rules out the cheap non-code causes first (stale `serve` process, stale prepared-bundle or provider-model cache, DTU misprovisioning), pins a root cause before any fixing, then makes you answer one gate question: **why did e2e miss this?** The answer routes the work to a new case, a deeper case, an eval task, or an escalation. It has a separate track for quality regressions where every contract still holds and the output simply got worse.

### `amplifier-agent-start-release-process`

Scopes which of the three artifacts need cutting, sweeps the diff for anything that should not ship publicly, sets versions in the correct manifest, writes the changelog, runs the local gate, and opens the release PR. **It never pushes a tag.** It stops at an open PR.

### `amplifier-agent-finish-release-process`

Run only after the release PR has merged. Verifies preconditions, pushes the tag that publishes, watches the workflows, confirms what users actually receive, and decides whether the `amplifier-app-opencode` version floor moves.

The two release skills own judgment and sequencing. [`RELEASING.md`](RELEASING.md) owns the mechanical facts and is the canonical reference for them.

## The e2e harness: DTU and Gitea

`tests/` contains `tests/e2e/` and nothing else. There is no unit test tier, and that is deliberate. See "Three tiers" in [`AGENTS.md`](AGENTS.md).

The suites run the real CLI and the real HTTP server inside a Digital Twin Universe container, installed from a Gitea mirror of your working tree. Two pieces make that work:

1. **Gitea** holds a throwaway mirror of your working tree, including staged, unstaged, and untracked files. Your repo is never mutated; there is no add, commit, or stash.
2. **The DTU profile** at `.amplifier/digital-twin-universe/profiles/e2e.yaml` uses `url_rewrites` to redirect `github.com/microsoft/amplifier-agent` at that mirror, so `uv tool install` inside the container pulls your local code rather than upstream.

The result is that a passing check is evidence the shipped binary works from a realistic install. The checks are deliberately light: the baseline assertion is that the command ran without erroring, plus a small structural check. Output quality belongs to evaluations.

```bash
# from the repo root
uv run python tests/e2e/framework/cli.py run              # mirror, fresh DTU, run all suites
uv run python tests/e2e/framework/cli.py run skills       # scope to one suite directory
uv run python tests/e2e/framework/cli.py run modes skills # several
uv run python tests/e2e/framework/cli.py run --skip-setup # re-run against the existing DTU
uv run python tests/e2e/framework/cli.py run -k resume    # remaining args pass through to pytest
uv run python tests/e2e/framework/cli.py up               # provision without running
uv run python tests/e2e/framework/cli.py down             # destroy the DTU, leave Gitea up
```

`make e2e SUITE=skills` is the same thing through the Makefile.

There are 8 suites and 56 collected cases today. Collection needs no DTU, so you can check the inventory cheaply:

```bash
uv run pytest tests/e2e/suites --collect-only -q
```

**Things that will bite you.**

- `uv run pytest` on its own is not a substitute. Without a DTU every case self-skips, giving you a green run that exercised nothing.
- `refresh` reinstalls the tool and wipes the lazily-installed provider module, which breaks `serve`. HTTP tests need a full `run` or `up`. This is why `run` rebuilds instead of updating in place.
- A fresh `run` rebuilds the container, so budget roughly a minute and a half before any test executes.
- Only `aa-e2e` belongs to this harness, and `aa-eval` to the evaluation harness. Do not destroy other DTUs on the machine.

Three other profiles sit alongside `e2e.yaml` for manual integration work: `paperclip-local-agent.yaml` and `nanoclaw-local-agent.yaml` build the downstream apps against your local engine, and `amplifier-agent-host-config-pr27.yaml` is a one-off verification environment.

## The evaluation harness

Evaluations measure probabilistic behavior, the judgment-laden output quality that a contract test cannot pin. Each trial provisions its own isolated DTU, drives the agent, extracts its work, and grades it.

```bash
cd .amplifier/evaluation
uv sync

uv run python run.py validate            # check every agent and task definition loads
uv run python run.py run --list-agents
uv run python run.py run --list-tasks
uv run python run.py run --agents amplifier-agent-local --tasks skill-invoke-and-behave
```

Or `make eval TASK=<id>` from the repo root.

Four agents are defined: `amplifier-agent-local` drives the engine directly from your working tree and is the one to use when evaluating your own change; `opencode-amplifier-agent` and `opencode-vanilla` are the paired comparison; `amplifier-foundation` runs the full Amplifier CLI.

**Things that will bite you.**

- The `aa-eval` Gitea mirror is only stood up when `amplifier-agent-local` is among `--agents`. With any other agent you are measuring upstream, not your change.
- `--tasks` takes flat task ids, not `group/id`.
- Every trial launches and destroys its own container, so a trial count is a container count.
- The harness scores a trial. It does not store a baseline or detect regressions; comparing runs is your job.
- `runs/` is gitignored and must stay that way. It holds provider keys, full prompts and responses, and absolute host paths.

## What CI runs

CI runs `make check`, `verify-codegen`, `verify-versions`, `verify-wheel`, and `verify-parity`, plus the TypeScript wrapper build and tests, which is `make verify-wrapper` split into its own job because it needs Bun. That is the whole gate.

**CI does not run `pytest tests/`, and a pytest step must not be added.** GitHub-hosted runners cannot provide Incus and Docker, so every e2e case would self-skip and the gate would verify nothing while reporting green. The same applies to `make eval`. Both tiers are gated locally, on a machine with a DTU, and that local run is the only gate they get.

So `make verify` passing means CI will pass. It does not mean the contract suite passed.

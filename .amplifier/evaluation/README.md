# Evaluation Harness

One evaluation system that runs a set of tasks against a set of agents, with a
single canonical definition per agent and per task. Each trial provisions an
isolated Digital Twin Universe (DTU) environment, drives the agent, extracts its
work, and grades the result.

## Layout

```
agents/<id>/            one canonical definition per agent
  meta.yaml             id, description, model
  install.yaml          requires.env + setup_cmds run in the DTU
  invocation.md         how to drive the agent (shared across tasks)
  extract.yaml          session/metrics extraction hints

providers/<name>.yaml   provider configs referenced by agents

tasks/<group>/<id>/     one canonical definition per task
  task.yaml             id, description, scenario, timeout, deliverable
  grader.yaml           grader type + config
  profile.yaml          DTU profile
  workspace/            files seeded into /workspace
  seed/                 optional prior sessions planted pre-agent

src/eval/               the harness package
  cli.py                the CLI (validate, run, swebench, automationbench)
  loaders.py            discover/load agents and tasks
  scheduler.py          parallel matrix execution
  task_loaders/         loader-driven task groups (swe-bench, automation-bench)
  graders/, extractor/, ai_user/

run.py                  entry point
runs/                   gitignored per-run outputs
```

## Task groups

```
benchmark/         static tasks (our own benchmark suite)
custom/            static tasks (session-recall, websearch-pdf)
swe-bench-pro/     loader-driven; only an instance_id is stored, problem data is
                   fetched at run time from HuggingFace (ScaleAI/SWE-bench_Pro)
automation-bench/  loader-driven; only the task name is stored, task data is
                   fetched at run time from zapier/AutomationBench
```

`benchmark/` and `custom/` are ours and are vendored in full. `swe-bench-pro/`
and `automation-bench/` store only a selector and pull their content on the fly
(see "Runtime-fetched data" below), so no third-party benchmark content is
redistributed here.

## Setup

```
uv sync
```

Requirements:

- `ANTHROPIC_API_KEY` in the environment.
- The `amplifier-digital-twin` CLI plus Incus, used to launch DTU environments.
- Network access and `uv` (swe-bench-pro and automation-bench fetch data at run
  time; automation-bench also needs Python 3.13, provided on demand by `uv`).

## Validate

Load every agent and task definition, resolve referenced files, check required
fields, and print a pass/fail report:

```
uv run python run.py validate
```

Exits non-zero if any definition is invalid.

## Run a matrix (static tasks)

Run the cartesian product of agents x tasks x trials in parallel. This covers
the `benchmark/` and `custom/` task groups.

```
uv run python run.py run \
  --agents opencode-amplifier-agent,opencode-vanilla,amplifier-foundation \
  --tasks websearch-pdf \
  --trials 1 \
  --max-parallel 3 \
  --output-dir runs/matrix/my-run
```

- `--agents` / `--tasks` are comma-separated and default to every discovered
  agent/task. `--trials` defaults to 1, `--max-parallel` to 4.
- `--list-agents` / `--list-tasks` print the available ids and exit.
- `--output-dir` defaults to `runs/matrix/<timestamp>`.
- Exit 0 only when every cell reaches `completed`.

The scheduler fans cells out under an `asyncio.Semaphore` capped at
`--max-parallel`, reusing one AI User / Extractor and one Grader per task. A
single trial failure never aborts the matrix.

## Run one swe-bench-pro instance

The swe-bench-pro group is loader-driven and has its own subcommand:

```
uv run python run.py swebench \
  --instance instance_flipt-io__flipt-7161f7b876773a911afdd804b281e52681cb7321 \
  --mode agent \
  --agent amplifier-foundation
```

- `--instance` (required): instance id, or path to a
  `tasks/swe-bench-pro/instances/<id>/` dir.
- `--mode`: `gold` grades the dataset reference patch (default); `agent`
  installs and drives an agent.
- `--agent`: agent id for `agent` mode (default `opencode-vanilla`).
- `--cache-dir`, `--launch-timeout`, `--grade-timeout` tune the fetch/build/grade
  steps. Output defaults to `runs/swebench/<timestamp>-<mode>`.

## Run one automation-bench task

The automation-bench group is loader-driven and has its own subcommand:

```
uv run python run.py automationbench \
  --task finance-timesheet-to-invoice \
  --agent amplifier-foundation
```

- `--task` (required): task id (the `task:` in `meta.yaml`), or path to a
  `tasks/automation-bench/<dir>/` dir.
- `--agent`: agent id to run (default `opencode-vanilla`).
- `--launch-timeout`, `--grade-timeout` tune the launch and in-DTU grade steps.
  Output defaults to `runs/automationbench/<timestamp>`.

## Runtime-fetched data

Some task groups store only a selector and pull content on the fly, so no
benchmark content is committed here:

- `tasks/swe-bench-pro/` stores an `instance_id` and fetches the problem
  statement, gold patch, test lists, and base image from HuggingFace
  (`ScaleAI/SWE-bench_Pro`) and the official scaleapi repo.
- `tasks/automation-bench/` stores the `task` name plus `example_id` and fetches
  the prompt, tool set, seeded world state, and assertions from AutomationBench.

For automation-bench the harness clones `zapier/AutomationBench` (pinned commit)
exactly once into a machine-local cache under the system temp dir, then extracts
each task locally from that single checkout. GitHub is contacted at most once per
machine, not once per task. Extraction runs in an isolated, ephemeral `uv`
environment; the package is never added to the host interpreter. AutomationBench
is MIT licensed (Copyright Zapier, Inc.) and is referenced at run time rather
than redistributed.

## Per-run output tree

A run writes one directory under gitignored `runs/` with a stable, analyzable
schema. Top-level `plan.json` and `combined-summary.json` are the analysis
surface; each cell owns one `<trial_id>/` directory named
`<agent>__<task>__trial-<N>`.

```
runs/matrix/<ts>/
  plan.json                     the selected matrix + redacted env snapshot
  combined-summary.json         per-cell status + score + key metrics
  <agent>__<task>__trial-<N>/
    state.json                  atomic per-transition trial state
    trial_result.json           consolidated record: score, metrics, paths
    metrics.json                normalized cost / tokens / wallclock
    interaction.json            AI User verdict + final message summary
    install.log                 agent setup_cmds output
    launch_profile.yaml         the exact composed profile launched
    grader_result.json          full structured grader verdict
    extracted/                  pulled agent artifacts + session logs
    grader/                     per-evaluation grader reports + rubric JSON
```

Any env var whose name looks secret (`KEY`/`TOKEN`/`SECRET`/`PASSWORD`) is
recorded as `<redacted>` in `plan.json`. `runs/` is gitignored and never source
controlled; it can contain prompts, responses, and absolute paths.

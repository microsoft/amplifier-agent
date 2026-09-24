# Evaluations

Run amplifier-agent through real tasks with live models. Each **trial** runs one
task in an isolated container, then a separate agent grades the evidence against
a rubric.

## Quick start

You'll need `uv`, Python 3.13+, `git`, and a running Docker daemon with Compose.
Export `OPENAI_API_KEY` in your shell for the smoke profiles, which use OpenAI for
both the agent and grader.

From the amplifier-agent repository root:

```bash
cd evaluations
uv sync
uv run amplifier-agent-evaluations run runs/smoke-checkout.yaml
```

This runs [core/hello](tasks/core/hello/) once against your local checkout. Use
`runs/smoke-github.yaml` to test the `v1` branch from GitHub instead. The harness
checks host requirements and reports missing credentials before launching.
The command exits with `0` when every trial passes and `1` otherwise.
Results go to `evaluations/output/<UTC datetime>-<run name>/`.
Open `report.html` in that directory.

## Choose what to run

[Run profiles](runs/) select the installation source, agent and grader models,
tasks, repetitions, concurrency, and timeouts. Smoke profiles run `core/hello`;
regression profiles select all tasks. Both have `checkout` and `github` variants.

```bash
# Every task against local code, four trials at a time.
uv run amplifier-agent-evaluations run runs/regression-checkout.yaml --parallel 4
```

`--parallel` (trials running at once) is the only option; it replaces the
profile's `parallel`. Everything else comes from the profile. To run other tasks,
more trials, or another agent, copy
[regression-checkout.yaml](runs/regression-checkout.yaml) and edit it:

```yaml
tasks:
  include: ["provider/*", "tools/web*"]
  exclude: []
trials: 3
agent:
  provider: anthropic
  model: claude-sonnet-5
```

Task IDs are paths under [tasks/](tasks/), grouped into `provider/`, `core/`, and
`tools/`. `include` and `exclude` are glob patterns over those IDs, and `*` matches
across `/`. Task-specific agent settings win over the profile's `agent`. The
resolved configuration is saved as `run.yaml`. Export credentials
for the default agent, grader, task-specific agents, and task `requires_env` entries.

`checkout` serves the working tree as it is on disk, minus gitignored files, as the
`v1` branch; commits, the index, and the checked-out branch do not matter. `github`
installs the remote `v1` branch. Both verify the installed code before running
tasks and record the result in `provenance.json`.

## Read the results

`report.html` shows each trial's score, metrics, and criteria that lost points,
with the grader's reasons. `summary.json` provides results for scripts.

```text
passed   Installation verified, all task segments finished, rubric passed.
failed   Score below the rubric's pass threshold.
error    Harness, installation verification, or grading could not complete.
timeout  A task segment stopped responding and was killed.
```

The rubric judges agent behavior, including expected errors. A nonzero driver exit
code or `driver_error` alone does not fail a trial. A segment that exceeds
`timeout_seconds` records a `segment_timeout` driver error and is graded; a nonzero
driver exit skips the remaining segments.

To investigate, look under `trials/<group>/<name>-<n>/`:

```text
trial_result.json           outcome, reason, score, and failed criteria
grader/grader_result.json  full scores and reasons
grader/<evaluation>/report.md
                           grader's written assessment
driver/result.json         replies, turn states, errors, and usage
driver/events.jsonl        tool calls, results, and other events
workspace/                 agent's workspace after the task
state.json                 stages, timestamps, and errors
```

For setup failures, check `launch.log`, `install.log`, and `provenance.json` in the
trial directory. Grader installation logs are in `grader/install.log`; harness
exceptions are in `harness_error.txt`.

`metrics.json` records agent tokens, cost, tool activity, and timings. Unknown
values are `not_available`. Total wall time excludes grading; grader usage is
reported separately in the grading result and run summary.

## Add a task

Create `tasks/<group>/<name>/` using [core/hello](tasks/core/hello/) as a simple
example or [core/resume](tasks/core/resume/) for a conversation across restarts:

```text
task.yaml      interaction and agent configuration (required)
grader.yaml    scoring rubric (required)
workspace/     optional files seeded into /workspace
grader-data/   optional answer keys and helpers for the grader only
```

The directory path becomes the task ID; no registration is needed. In `task.yaml`,
define `turns` with `user` messages. `restart: true` resumes the session in a new
process, starting a new segment; it needs `session: {persistence: durable,
session_id: ...}`. `{{nonce}}` supplies a fresh value per trial.

```text
tools            built-in tool names; default all
approvals        none (default), allow, deny, or host
host             module in driver/hosts/ providing caller tools and approvals
skills           skill directories, relative to the workspace
agent            provider and model overriding the profile
timeout_seconds  per-segment limit overriding the profile's task_seconds
```

Tasks can also set `env` and `setup` commands. See
[tools/filesystem](tasks/tools/filesystem/) for setup commands,
[tools/delegate](tasks/tools/delegate/) for a seeded workspace,
[tools/skill](tasks/tools/skill/) for a skill seeded under
`workspace/.agents/skills/`, and [driver/hosts/](driver/hosts/) for caller tools
and approval handlers. Task `env` values apply inside the driver and cannot satisfy host
credential checks.

Start `description` with the feature under test, then say what happens and what
outcome shows it works. The grader sees it.

In `grader.yaml`, give each evaluation task-specific investigation `steps` and a
rubric of criteria with points and descriptions. Ground truth belongs in the grader:
put fixed answers, URLs, and error codes in the `steps` text, larger answer keys in
`grader-data/`, and tell the grader where a nonce appears in the `task.json` turns.
Open `steps` with a `What this checks:` paragraph, then number the investigation.
Write each criterion as its plain meaning, then `Evidence:` with the exact check,
then the scoring rule.
Each evaluation gets its own grader session. Its score is awarded points divided by
possible points; `overall_score` is the weighted average. Passing requires
`overall_score >= pass_score`. Both `weight` and `pass_score` default to `1`.
Criteria the evidence cannot resolve receive zero points.

Shared grading instructions and event-inspection examples live in the
[grader system prompt](grader/src/amplifier_agent_grader/prompts/system.md).
Every selected task must have a valid rubric or the run fails to load.

## How it runs

The harness uses dtu-lite containers and the [driver](driver/drive.py) to run task
turns. It saves the agent's evidence before grading, so a grader failure does not
lose that evidence. Rubrics and grader data arrive only after the task finishes.

The grader inspects the same container using its own installation of amplifier-agent,
pinned by [grader/uv.lock](grader/uv.lock), independently of the code under test.
Its instructions require preserving the agent's work and using a separate scratch
directory. Grading has a 900-second limit.

For implementation details, see the [container profiles](profiles/),
[trial lifecycle](src/amplifier_agent_evaluations/trial.py), and
[grader package](grader/src/amplifier_agent_grader/).

## Development

From `evaluations/`:

```bash
# harness and grader tests
uv run pytest
uv run ruff check
uv run ruff format --check
uv run ty check
```

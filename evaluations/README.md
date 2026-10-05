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
`runs/smoke-github.yaml` to test the release tag from GitHub instead, and
`runs/smoke-typescript-checkout.yaml` or `runs/smoke-http-checkout.yaml` for the
TypeScript binding or the HTTP face. The harness
checks host requirements and reports missing credentials before launching.
The command exits with `0` when every trial passes and `1` otherwise.
Results go to `evaluations/output/<UTC datetime>-<run name>/`.
Open `report.html` in that directory.

## Choose what to run

[Run profiles](runs/) select the installation source, agent and grader models,
tasks, repetitions, concurrency, and timeouts. Smoke profiles run one surface's hello
tasks; regression profiles select all tasks. Both have `checkout` and `github` variants.

```bash
# Every task against local code, four trials at a time.
uv run amplifier-agent-evaluations run runs/regression-checkout.yaml --parallel 4
```

`--parallel` (trials running at once) replaces the profile's `parallel`.
`--rebake`, `--no-bake`, and `--keep-images` control the [image cache](#image-cache). Everything
else comes from the profile. To run other tasks or another agent, copy
[regression-checkout.yaml](runs/regression-checkout.yaml) and edit it:

```yaml
tasks:
  include: ["provider/*", "tools/web*"]
  exclude: []
trials: 1
agent:
  provider: anthropic
  model: claude-sonnet-5
```

Task IDs are paths under [tasks/](tasks/), grouped into `provider/`, `core/`, and
`tools/` for the Python binding, and `typescript/` and `http/` for the other surfaces. `include` and `exclude` are glob patterns over those IDs, and `*` matches
across `/`. Task-specific agent settings win over the profile's `agent`. The
resolved configuration is saved as `run.yaml`. Export credentials
for the default agent, grader, task-specific agents, and task `requires_env` entries.
The regression profiles need all of these:

```bash
export OPENAI_API_KEY=...
export ANTHROPIC_API_KEY=...
export GEMINI_API_KEY=...            # or GOOGLE_API_KEY
export GH_TOKEN=$(gh auth token)     # provider/copilot; or COPILOT_GITHUB_TOKEN, GITHUB_TOKEN
```

`checkout` serves the working tree as it is on disk, minus gitignored files and `evaluations/`, tagged with the
release tag; commits, the index, and the checked-out branch do not matter. The rubrics and grader data under
`evaluations/` are never served, so the agent under test cannot fetch them. `github`
installs Python and HTTP from the remote release tag, which must exist, and TypeScript from npm,
verified against the release's package archive. Each task runs in the container profile for its
surface and the run's `install`, `profiles/<surface>/<install>/`, which installs only
that surface as [docs/install.md](../docs/install.md) describes. The TypeScript image adds Node 22 and the build toolchain; on `checkout` it builds
the package in the container, so its bake takes much longer. Every trial verifies the installed code before running
tasks and records the result in `provenance.json`.

### Image cache

A run installs each surface once, in a bake, and its trials start from the result.
The bake launches the container profile as above, so `install.sh` still installs
through the gateway as a user would. Once the install passes the provenance check,
the harness commits the container to an image, with the credentials and gateway
settings the launch added cleared from its environment. The grader is installed
during the first bake to fill its package cache, then removed before the commit.

The image is tagged `amplifier-agent-eval/<surface>-<install>:<key>`. The key is a
hash of the install, the surface, the expected installed identity (the snapshot
HEAD on `checkout`, the release tag's commit or package digest on `github`), the
surface's `Dockerfile`, `install.sh`, and `<install>/compose.yaml`, and a bake
format version. A run whose key matches an existing image reuses it, across runs
too; changing code under test or a profile file gives a new key. The snapshot
commit has a fixed author and date, so the same files give the same HEAD.

The grader's package cache is kept in `evaluations/.cache/grader/<hash>/uv-cache.tar`,
keyed by `grader/uv.lock`, `pyproject.toml`, and `src/`. Each trial unpacks it once
grading starts and installs the grader offline.

```text
--rebake           bake new images even when the key matches
--no-bake          install in every trial, without images or the grader cache
--keep-images N    images per repository and grader caches to keep, default 3; 0 keeps all
```

After the bakes, each `amplifier-agent-eval/<surface>-<install>` repository the run
uses keeps its N newest images, by creation time, plus any image this run uses or a
container still holds. The grader cache keeps the N most recently used keys and the
current one. A removal that fails, for example because another run is using the
image, is reported and the run goes on.

`bake.json` in the run directory records each image's key, tag, image ID, whether it
was reused, the run that baked it, the bake's logs under `bake/`, and what was pruned.
To remove every image and cache by hand:

```bash
docker image ls 'amplifier-agent-eval/*'
docker image rm $(docker image ls -q 'amplifier-agent-eval/*')
rm -rf .cache
```

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
driver/footprint.txt       every file the task wrote anywhere in the container
workspace/                 agent's workspace after the task
state.json                 stages, timestamps, and errors
```

For setup failures, check `launch.log`, `install.log`, and `provenance.json` in the
trial directory, and `bake/<surface>-<install>/` in the run directory. `provenance.json`
names the image the trial ran in. Grader installation logs are in `grader/install.log`; harness
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
sessions/      optional durable sessions seeded into the agent's sessions directory
grader-data/   optional answer keys and helpers for the grader only
```

The directory path becomes the task ID; no registration is needed. In `task.yaml`,
set `surface` to `python` (default), `typescript`, or `http`, and define `turns` with
`user` messages. `restart: true` resumes the session in a new
process, starting a new segment; it needs `session: {persistence: durable,
session_id: ...}`. `{{nonce}}` supplies a fresh value per trial. `session: {resume: true,
...}` makes the first segment resume a session seeded from `sessions/<session_id>/`
instead of creating one. A turn's `images` lists PNG, JPEG, GIF, or WebP files relative
to the workspace, sent after the `user` text as image parts on every surface. See
[core/image](tasks/core/image/), [core/image_multi](tasks/core/image_multi/), and
[http/image](tasks/http/image/).

```text
tools            built-in tool names; default all
approvals        none (default), allow, deny, or host
host             module in driver/hosts/ providing caller tools and approvals
skills           skill directories, relative to the workspace
agent            provider and model overriding the profile
timeout_seconds  per-segment limit overriding the profile's task_seconds
agent_options    tool_error_policy, tool_result_max_bytes, working_directory,
                 additional_directories, and environment, passed to the agent as given
```

`typescript` tasks take the same fields except `host`. `http` tasks take only
`agent`, `env`, `setup`, `requires_env`, and `timeout_seconds`; the face takes tools and
approvals from its server host. Each `http` turn is one request carrying the whole
conversation so far, streamed when the turn sets `stream: true`. Preflight refuses
fields a surface cannot honor. See [http/streaming](tasks/http/streaming/).

Tasks can also set `env` and `setup` commands. See
[tools/filesystem](tasks/tools/filesystem/) for setup commands,
[tools/delegate](tasks/tools/delegate/) for a seeded workspace,
[core/long_context](tasks/core/long_context/) for a seeded session,
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

The harness uses Digital Twin Universe containers and the surface's driver to run task turns:
[drive.py](driver/drive.py), [drive.mjs](driver/drive.mjs), or
[drive_http.py](driver/drive_http.py), which starts the face and sends turns with the
OpenAI Python client. All three write the same `result.json`; the HTTP driver writes no
`events.jsonl` and adds an `http` record per turn. The Python and TypeScript drivers add a
`process` record per segment: the driver's own cwd and which `agent_options.environment`
names reached its environment. Before the first segment the harness touches a marker;
after the last, it lists every file and symlink newer than it, outside `/proc`, `/sys`,
`/dev`, `~/app`, and the grader's home, into `driver/footprint.txt`. The harness saves the agent's
evidence before grading, so a grader failure does not lose that evidence. Rubrics and grader data arrive only after the task finishes.

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

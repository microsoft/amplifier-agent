# JobBench harness

Runs Amplifier agents against **JobBench** inside Digital Twin Universe (DTU)
containers, captures trajectories and token/cost metrics, and grades the
produced deliverables with the JobBench rubric judge.

## Attribution

JobBench -- the benchmark, dataset, rubrics, and judge -- is the work of the
JobBench authors:

- Repository: <https://github.com/Job-Bench/job-bench-eval>
- Project site: <https://job-bench.github.io/>
- Dataset: <https://huggingface.co/datasets/JobBench/job-bench>

This directory contains our runner plus our modifications to the JobBench judge
script. The adaptations are documented at the top of `src/jobbench/judge.py`.

`src/jobbench/judge.py` and the prompt template in `src/jobbench/prompt.py` are
derived from upstream and are licensed Apache-2.0; see `THIRD-PARTY-NOTICES.md`
for the file-by-file attribution and the full license text. The rest of this
harness is MIT under the repository's top-level `LICENSE`.

Run `python run.py fetch` to download the dataset from Hugging Face before the
first run.

## What JobBench measures

Not coding. Multi-source knowledge work: reconciling contradictory records,
cross-referencing data, tracing citations, across 35 white-collar occupations.
The agent receives a folder of source files and must produce deliverables
(xlsx, docx, pdf, ipynb, sqlite, pptx). An LLM judge scores those deliverables
against a weighted, criterion-level rubric.

```
main split   65 tasks, 569 rubrics, 2066 criteria, 4500 total weight
             includes reference material withheld from the sandbox that the
             agent is expected to find via live web search
easy split   63 tasks, self-contained, no withheld material
             a different corpus, not simplified versions of the main tasks
```

Scoring is all-or-nothing per rubric: full weight only if every criterion passes.

## Prerequisites

Host tooling. Every trial runs in a container, so all three are required for
anything past `fetch` and `list-tasks`:

```
uv                       dependency install; also what the bake profiles use
                         inside the container
incus                    src/jobbench/images.py shells out to it directly to
                         publish and delete the golden images
amplifier-digital-twin   the DTU CLI (src/jobbench/dtu.py, CLI constant) --
                         launch, exec, push, pull, destroy
```

Python >=3.11. `uv sync` installs the rest, including the judge's document
extractors (pandas, openpyxl, xlrd, python-pptx, pdfplumber, mammoth).

Environment variables:

```
ANTHROPIC_API_KEY     required. Passed into each trial container by
                      profiles/task.template.yaml passthrough.services; the
                      value never enters this Python process.
ANTHROPIC_BASE_URL    required by the same passthrough block.
OPENAI_API_KEY        required to grade. src/jobbench/grading.py reads it and
                      passes it to the judge as --api-key. Override per
                      invocation with --judge-api-key.
OPENAI_BASE_URL       the judge endpoint, read the same way. Override with
                      --judge-api-base.
JOBBENCH_CACHE_DIR    optional. Moves the dataset cache off the default
                      dataset-cache/ so one download is shared across
                      checkouts.
```

`src/jobbench/judge.py` also honors `JUDGE_API_BASE` / `JUDGE_API_KEY` /
`JUDGE_MODEL`, but only when invoked standalone. Driven through `run` or
`grade` it is always passed explicit flags, so those three are never consulted
on the normal path.

## Quick start

```bash
uv sync
python run.py fetch --split easy               # or: --split main
python run.py list-tasks --split easy
python run.py dtu-check                        # verify the DTU round trip works
python run.py bake --agent amplifier-agent     # build the golden image
python run.py run --agent amplifier-agent --task biostatisticians/task1 --split easy
```

## Commands

```
fetch        download a split from Hugging Face into the local cache
list-tasks   list tasks in a downloaded split, with rubric counts and weights
dtu-check    launch a throwaway DTU and verify exec, push, pull, and destroy
bake         build the shared base image and one agent's golden image
run          execute an (agents x tasks) matrix, grading each trial
grade        re-grade an already-completed run without re-running the agent
```

### Running a matrix

`run` takes any number of agents and tasks and executes the cross product with
bounded concurrency. `--agent` and `--task` are repeatable and also accept
comma-separated values.

```bash
# every agent against every task in the split
python run.py run --agent all --all-tasks --split main --max-parallel 4

# two agents, two tasks
python run.py run --agent amplifier-agent,opencode-vanilla \
    --task biostatisticians/task1 --task biostatisticians/task2 --split easy

# see the matrix and a cost estimate without launching anything
python run.py run --agent all --all-tasks --split main --dry-run
```

There is no checkpoint-resume. Recovery is re-invocation: point `--run-id` at an
existing run directory and add `--skip-existing` to leave finished trials alone
and re-run everything else.

```bash
python run.py run --agent all --all-tasks --split main \
    --run-id 20260817T153414Z --skip-existing
```

Useful flags: `--model`, `--bundle` (amplifier-foundation only), `--timeout`,
`--output-dir`, `--no-grade`, and the `--judge-*` family.

### Cost and runtime

Pilot on a single task before committing to a sweep. `--dry-run` prints the
matrix and a cost estimate without launching anything, and that estimate is the
honest one: it comes from `cost_usd` observed in prior runs' own `trial.json`
telemetry, not a pricing table.

```
$2.04 - $10.07 per trial, mean ~$6.00   src/jobbench/matrix.py
```

That range is agent tokens only. The judge's token usage is captured per rubric
in the grade report but is never priced, so it is not in the figure above and
not in the totals `run` prints. Judge load scales with rubric count (569 across
the main split) and with deliverable size, since every call carries the full
extracted text of every deliverable.

Wall clock, per trial:

```
launch from golden image   ~15s, versus provisioning from scratch
agent run                  bounded by --timeout, default 3600s
grade                      up to --judge-max-workers rubrics in parallel,
                           --judge-timeout-per-rubric (default 300s) each
```

The full `main` split is 65 tasks; all four agents against all of it is 260
trials. Divide by `--max-parallel` (default 2) for a rough wall-clock estimate,
but treat the agent-run number as a ceiling rather than a mean -- how long a
trial actually takes varies by task and by agent, and this harness records no
aggregate of it. Baking is separate and out of band: a full toolchain install
can take tens of minutes (`DEFAULT_BAKE_TIMEOUT_S` is 1800s), but it happens
once per agent image, not once per trial.

### One run per host at a time

Do not start a second `run.py run` on a host that already has one in flight.
`run` sweeps orphaned DTUs before and after the matrix, and that sweep destroys
every `jb-`-prefixed instance the DTU CLI reports (see
`src/jobbench/orphans.py`). It has no way to tell a leaked container from a
peer run's live one, so a second invocation's pre-run sweep will kill the first
run's trials mid-flight.

Pass `--no-orphan-sweep` to the second invocation when concurrent runs on one
host are genuinely needed. Leaked containers then have to be cleaned up by
hand.

### Agents

```
amplifier-agent       the amplifier-agent CLI
amplifier-foundation  amplifier run, on a pinned bundle
opencode-vanilla      stock OpenCode, direct to Anthropic
opencode-amplifier    OpenCode fronting amplifier-agent
```

## How a trial runs

One DTU per (agent, task), launched from a per-agent golden image so the
container is warm in about 15 seconds rather than provisioning from scratch.

```
launch -> seed -> run agent -> pull deliverables -> pull sessions -> destroy
```

The agent sees only `task_folder/`. Rubrics, task cards, and any
search-discoverable reference material stay on the host. It writes deliverables
to a dedicated output directory; that directory is what gets graded.

## Output layout

```
runs/<timestamp>/
  run-manifest.json                      reproducibility record
  <agent>/<occupation>__task<N>/
    trial.json                           status, exit code, timings, dtu id
    launch_profile.yaml                  exact profile used
    prompt.txt                           exact bytes sent to the agent
    agent.log                            stdout + stderr
    metrics.json                         tokens, cost, agent_run_s
    deliverables/                        what the agent produced
    sessions/                            raw agent-native trajectory
    grade/<judge>_judge.json             per-rubric verdicts and score
    grade/judge.log                      judge stdout + stderr
```

Trial status is recorded independently of grading, so a crash, a timeout, and a
legitimate zero are distinguishable. `run` prints a per-trial line and a totals
line at the end, and each trial's score is merged back into its own
`trial.json`; a trial that ran but could not be scored carries `grade_error`
there rather than having its failure folded into `status`. There is no
aggregate rollup across trials -- scoring a whole sweep means reading the
per-trial `trial.json` files.

## Grading

Judge defaults to `gpt-5.6-terra` at medium reasoning effort over an
OpenAI-compatible endpoint (`OPENAI_BASE_URL`, `OPENAI_API_KEY`). One call per
rubric, carrying the full extracted text of every deliverable. Rubrics whose
text mentions plots or figures additionally get up to 8 images attached.

The JobBench authors validated their rubrics against `grok-4.3`. Scores produced
with any other judge, including ours, are internally consistent and valid for
agent-vs-agent comparison, but are not comparable to the published leaderboard.

## Baseline environment

JobBench does not specify an execution environment for the agent under test. A
fixed toolchain is baked into `profiles/jobbench-base.bake.yaml`, identical for
every agent, and its image alias is recorded in each run manifest. An agent that
must install pandas before it can start work is not being measured on the same
footing as one that cannot.

The toolchain covers every format the judge can extract text from, since a
deliverable the judge cannot read scores zero regardless of its quality.

## Known issues

Read this before trusting a number out of this harness.

### tool results are lost on the opencode-amplifier arm

Tool results are sometimes lost crossing the opencode bridge, so the model
repeatedly re-decides it has not read files it already read. The provider
injects a synthetic `[SYSTEM ERROR: Tool result missing from conversation
history]` message and only logs a `logger.warning`
(`amplifier-module-provider-anthropic/__init__.py:2014`); the model then
narrates that error back in its own words, burning wall-clock on a
degenerate loop. Measured on one task: 22 to 27 occurrences per run, and
roughly double the wall-clock of the other three agents, while the trial
still exits 0 and still produces plausible deliverables and a plausible
score. `opencode-vanilla` is the control -- same CLI, same model, talking
directly to Anthropic -- and never exhibits it, so the fault is in the
bridge, not the model.

Root cause is outside this harness; nothing here works around it. Every
trial scans `agent.log` for the failure signatures
(`_detect_tool_result_loss` in `src/jobbench/trial.py`) and, when found,
appends an entry to `warnings` in that trial's `trial.json`:

```
{"kind": "tool_result_loss", "confidence": "direct" | "heuristic", "count": N, "detail": "..."}
```

`confidence` is `"direct"` when the literal signature string is found, and
the weaker `"heuristic"` when it is only inferred from the model repeatedly
narrating that it hasn't read something it already read (the literal string
is often never observable, since it lives in the message history the
provider sends the model, not in `agent.log` itself).

`warnings` is never folded into `status` -- a trial can read `completed`
and still carry this warning. Check `warnings` explicitly before treating an
`opencode-amplifier` trial as clean. See
`profiles/agents/opencode-amplifier.bake.yaml` and
`src/jobbench/agents/opencode_amplifier.py` for the full account.

### opencode-amplifier reports no cost or token telemetry, ever

`OpencodeAmplifierAdapter` declares `session_dirs: tuple[str, ...] = ()`
alongside `metrics_source = "events"` (`src/jobbench/agents/
opencode_amplifier.py`). No location under `/root` was found to hold an
amplifier-agent-style session tree when amplifier-agent is driven through
the opencode wrapper, and the adapter leaves the path empty rather than
guess one. This is deliberate, not a bug: every token and cost field for
this arm reports the exact string `"not_available"`, on every trial, by
construction. Cross-arm cost or token comparisons that include
`opencode-amplifier` cannot be done from this harness's own numbers; the run
summary says so via that same `not_available` string rather than a
fabricated zero.

### judge cost is captured but never priced

Already noted under "Cost and runtime" above, repeated here because it is a
trust caveat, not just a cost-estimation footnote: `_extract_usage` /
`_sum_usage` in `src/jobbench/judge.py` capture the judge's token usage into
a `usage` block on every rubric result and on the report, but nothing prices
it -- upstream JobBench discarded `response.usage` entirely, and this
harness only added token capture, not a rate card for the judge model. Judge
cost is in no total `run` or `grade` prints. To see it, read `usage` out of
`grade/<judge>_judge.json` yourself. Judge load scales with rubric count and
deliverable size, so it is not necessarily small next to agent cost.

### scores are not comparable to the published leaderboard

Already noted under "Grading" above. The JobBench authors validated their
rubrics against `grok-4.3`; this harness defaults to `gpt-5.6-terra`. Scores
produced here are internally consistent and valid for agent-vs-agent
comparison, but are not comparable to the public leaderboard, regardless of
which judge model you point this harness at.

### opencode-vanilla's cost_usd is recomputed, not opencode's own figure

For the `opencode-vanilla` arm (`metrics_source = "opencode_db"`),
`cost_usd` is recomputed from that session's token counts against this
harness's own reference rate card, not taken from opencode's self-reported
`cost` column (`src/jobbench/metrics.py`, the `_opencode_model_id` /
`compute_cost_from_tokens` block). The two are logged and can diverge,
since opencode's own figure is priced from a `models.dev` card that differs
on cache rates. If a session's model is not in this harness's rate card,
`cost_usd` for that session is `"not_available"` even though opencode itself
reported a number -- that number is left out because it is not comparable to
the amplifier arms' figures, not because it does not exist.

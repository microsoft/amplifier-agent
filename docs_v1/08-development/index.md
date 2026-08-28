# Development

Working on Amplifier Agent itself.

Testing here splits along one line: whether a change works at all, and whether
the agent is any good. Those are different questions with different tools, and
conflating them produces a suite that is slow, flaky, and answers neither.

```
End-to-end tests   Does the shipped binary work from a realistic install?
                   Fast, cheap, binary pass or fail.

Evaluations        Is the agent good at the task?
                   Slow, expensive, scored rather than passed.
```

## End-to-end tests

E2E runs the real CLI and HTTP server inside an isolated container, installed
from a git mirror of your working tree. It proves the shipped binary works from a
realistic install rather than proving that unit tests pass.

The tests are deliberately light. The baseline is that the command ran without
erroring, exit 0 or HTTP 200, plus a small structural assertion on the output.
Output quality is out of scope and belongs to evaluations.

```bash
uv run python tests/e2e/framework/cli.py run          # push code, fresh container, all suites
uv run python tests/e2e/framework/cli.py run skills   # one suite
uv run python tests/e2e/framework/cli.py run --skip-setup   # re-run against the existing container
uv run python tests/e2e/framework/cli.py up           # provision without running
uv run python tests/e2e/framework/cli.py down         # tear the container down
```

The framework is split so that adding tests never means touching machinery:

```
tests/e2e/
  framework/     the machinery, stable and rarely edited
  suites/        the tests, one package per feature
    <feature>/
      cases.py             the case data
      test_<feature>.py    a thin pytest wrapper
      fixtures/            optional files pushed into the container
```

To cover a new feature, add `suites/<feature>/`. Nothing in `framework/` changes.

### Why a container

Because "works on my machine" and "works when installed" are different claims,
and only the second one matters to a user. The container installs from a mirror
of your working tree, so the thing under test is the thing that ships, including
the install path.

It also means real credentials. `ANTHROPIC_API_KEY` is passed through and is
required by any suite that runs a model. Suites needing a credential nobody is
guaranteed to have skip themselves rather than failing, so a full run stays green
for someone without a Google key or a vLLM server.

Note that credentials are snapshotted into the container at launch. Exporting one
after the container is already running has no effect, and the failure surfaces
much later as an opaque provider auth error.

## Evaluations

Evaluations run a set of tasks against a set of agents and score the results.
Each trial provisions an isolated environment, drives the agent, extracts its
work, and grades it.

```bash
uv run python run.py validate     # check every agent and task definition

uv run python run.py run \
  --agents amplifier-agent,opencode-vanilla \
  --tasks websearch-pdf \
  --trials 1 \
  --max-parallel 3
```

`validate` exits non-zero on any invalid definition, which makes it the cheap
check to run before a long matrix.

The structure is one canonical definition per agent and per task:

```
agents/<id>/       meta.yaml, install.yaml, invocation.md, extract.yaml
tasks/<group>/<id>/  task.yaml, grader.yaml, profile.yaml, workspace/
providers/         provider configs the agents reference
```

Task groups are either vendored or fetched at run time:

```
benchmark/         ours, vendored in full
custom/            ours, vendored in full
swe-bench-pro/     stores an instance id, fetches the problem at run time
automation-bench/  stores a task name, fetches the task at run time
```

The fetched groups store only a selector, so no third-party benchmark content is
redistributed here.

Two external benchmarks live alongside in their own directories, `deep-swe/` and
`jobbench/`, because each owns a task format, prompt contract, and grading path
that has to be reproduced exactly for its scores to mean anything. Each has its
own README.

### Reading a score honestly

A score is comparable to another score produced the same way, and to nothing
else. Judge model, prompt, and rubric all move the number.

`jobbench` is the clearest case: its authors validated their rubrics against a
different judge model than the one configured here. Scores from this harness are
internally consistent and valid for comparing one agent arm against another. They
are not comparable to the published leaderboard, and reporting them as if they
were would be wrong.

## Which one to reach for

**A change to behavior, a flag, an output shape, or the install path** is an E2E
question. Add a case to the relevant suite, or a new suite.

**A change intended to make the agent better at something** is an evaluation
question. E2E will happily pass a change that made the agent worse, because exit
0 is all it checks.

Most changes want the first. Reach for the second when the claim you are making
is about quality, and be ready for it to take a while.

---
name: amplifier-agent-new-feature
description: "Develop a new amplifier-agent feature from ideation to working end-to-end in a DTU, using E2E-test-driven development. Covers orientation, spec updates, red/green E2E TDD, optional capability evals, and the loop-until-green discipline. Stops before release."
disable-model-invocation: true
user-invocable: true
---

# Develop a New amplifier-agent Feature

## Feature

$ARGUMENTS

If that is empty, ask what feature to build before doing anything else.

## The Method

E2E-test-driven development. Tests come before implementation, they run against
the real CLI and HTTP server inside a DTU container, and they define the
contract. The feature is done when the scoped tests go green and a human has
tried it by hand in a DTU.

This skill ends at a working feature. It does not cover PRs, versioning, or
release. When the loop closes, recommend the
`amplifier-agent-start-release-process` skill.

Work through the phases in order. Use the todo tool to track them.

---

## Phase 0: Orient

Read before touching code. Do not start from source.

```
AGENTS.md                       gates, invariants, what-done-looks-like
docs/SPEC.md                    index of contracts, with a reading order
docs/spec/<relevant>.md         the contract(s) this feature changes
docs/E2E_TESTING.md             the harness, in full
docs/LAYERS_AND_RELEASES.md     blast radius of the change
```

Then answer these explicitly before moving on:

```
Which contract does this feature change, and which docs/spec/*.md owns it?
Which of the AGENTS.md cross-component invariants does it touch?
  (protocol version, tag namespaces, wrapper siblings, user-invoked migrations,
   single-JSON-line stdout, bundle force-include)
Does it touch the wire protocol? If yes, wrappers need coordinated updates.
Does it add bundle assets? If yes, pyproject force-include needs a per-file line.
Which existing E2E suite is closest, and is this a new suite or an addition?
```

Delegation is useful here. A recon agent can map the relevant code paths while
you read the specs. Give it an absolute repo path, a narrow scope, and explicit
anti-scope (read-only, do not modify, do not commit, do not stage). Prefer a
clean-slate context and a self-contained instruction over inherited context.

## Phase 1: Plan

Present options before choosing. Enumerate three to five approaches with real
tradeoffs and let the user pick. Do not propose a single answer.

Write the plan to a scratch file OUTSIDE the repo. Plans are transient working
artifacts, not repo content. Default to `.ai_working/` in the parent workspace
directory, or wherever the user already keeps working notes. Never commit a plan
file.

`AGENTS.md` is explicit on this: design docs do not belong in the repo. What is
durable is `docs/spec/`. A contract change and its spec update belong in the
same change.

The plan states:

```
The contract change, in one sentence
Which docs/spec/*.md files get updated, and how
The E2E cases to write, by name, with what each asserts
Whether an eval is needed (see Phase 3 decision rule)
The E2E subset to run while iterating, and the widening ladder
"Iterate until all relevant e2e tests pass"
```

Update `docs/spec/*.md` in the same change as the contract. Not after.

## Phase 2: RED

Write the tests. Run them. Confirm they fail for the right reason.

```
tests/e2e/suites/<feature>/
  __init__.py
  cases.py            E2ECase definitions
  test_<feature>.py   pytestmark = pytest.mark.dtu, parametrized over CASES
  conftest.py         only if the suite needs to push fixture files
  fixtures/           static payloads pushed into the DTU
```

Nothing under `tests/e2e/framework/` should need to change. If it does, that is
a signal worth raising before proceeding.

Rules that are not negotiable:

```
Assert the PUBLIC contract only. Never assert on internals, log formats, or
  anything that would break on a legitimate refactor.
The test is the contract. If a test looks wrong during implementation, STOP and
  escalate. Do not edit a test to make it pass.
Not-yet-built behavior gets @pytest.mark.xfail(reason=..., strict=True) so an
  unexpected pass is a hard failure the moment the feature lands.
```

Preflight before spending minutes on a DTU run:

```bash
python3 -m py_compile tests/e2e/suites/<feature>/*.py && echo "SYNTAX OK"
uv run pytest tests/e2e/suites/<feature> --collect-only -q 2>&1 | tail -25
```

Then run RED, detached, to its own log:

```bash
rm -f /tmp/red_run.log && setsid bash -c \
  'uv run python tests/e2e/framework/cli.py run <feature> -rxX > /tmp/red_run.log 2>&1' \
  </dev/null >/dev/null 2>&1 &
echo "launched pid $!"
```

Poll it (see the Mechanics appendix). When it finishes, verify the failure
reason, not just the failure count. A test that fails because of a typo in the
case definition is not a red test.

Report the red result to the user before implementing.

## Phase 3: Evals (conditional)

Decide with this rule:

```
E2E test  = deterministic contract and wiring
            "does it appear in the list?", "does it exit 0?",
            "is the field on the response?", "does --cwd resolve correctly?"

Eval      = probabilistic agent behavior
            "does the agent actually invoke the skill and act on it?",
            "does the mode change how it explores?",
            "are arguments passed through to the skill body?"
```

Most features need only E2E. Add an eval when there is a behavioral gap that E2E
structurally cannot cover. If in doubt, skip it and say why.

If an eval is warranted:

```
.amplifier/evaluation/tasks/amplifier-agent-capabilities/<task-id>/
  task.yaml      id, description, timeout, deliverable, scenario prose
  grader.yaml    model_rubric with weighted evaluations and point rubric
  profile.yaml   DTU profile, url_rewrites to the local Gitea mirror
  workspace/     optional seed files the task needs
```

Run the ladder, cheapest first, from `.amplifier/evaluation/`:

```bash
uv sync
uv run python run.py validate 2>&1 | tail -40
uv run python run.py run --list-tasks

setsid bash -c 'uv run python run.py run --agents amplifier-agent-local \
  --tasks <task-id> --trials 1 --max-parallel 1 --output-dir runs/smoke \
  > /tmp/eval_run.log 2>&1' </dev/null >/dev/null 2>&1 &
```

Keep `--trials 1 --max-parallel 1` while iterating, for determinism and one
container at a time. Poll the run's `state.json`, not just the log, because the
summary is written last:

```bash
sleep 60; tail -n 4 /tmp/eval_run.log; \
  grep -o '"state": *"[^"]*"' runs/smoke/*/state.json | tail -1
```

Do not run the full eval matrix for a feature change. Pick the tasks that
touch the changed surface. A dedicated eval-authoring skill will cover this in
more depth later.

## Phase 4: GREEN

Implement, then loop until the scoped tests pass.

Scope discipline is the point of this phase. Start with the narrowest possible
subset and widen only after the narrow scope is green:

```
1. -k "<the one failing case>"            fastest signal, warm DTU
2. -k "<a or b or c>"                     the whole red set for this feature
3. cli.py run <feature>                   the whole suite
4. cli.py run <feature> <adjacent>        regression check on neighbors
5. cli.py run                             everything
6. cli.py run --fresh                     clean-box confirmation
```

Do not run all suites while iterating. Do not run `--fresh` while iterating.
Reuse the warm named DTU with `--skip-setup`.

```bash
rm -f /tmp/green_run.log && setsid bash -c \
  'uv run python tests/e2e/framework/cli.py run <feature> --skip-setup -rxX \
   -k "<subset>" > /tmp/green_run.log 2>&1' </dev/null >/dev/null 2>&1 &
```

Re-launch the SAME subset to the SAME log name across iterations so runs are
comparable. Only change the log name when the scope changes.

When you widen to adjacent suites, name up front which existing failures are
expected and acceptable, so a known-bad suite is not mistaken for a regression.

If a test looks wrong, escalate. Do not edit it.

Also keep the fast local gates green as you go:

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pyright src/
uv run pytest tests/ -q
```

Delegation works well for the implementation loop. Hand a builder agent the plan
file path and a precise scope, with a clean context and explicit anti-scope
(do not commit, do not stage, do not edit tests). Keep the test-running and
result-interpretation in the driving session so the loop stays coherent.

## Phase 5: Human loop

Automated green is not done. Give the user a way to try the feature by hand.

```bash
uv run python tests/e2e/framework/cli.py up
amplifier-digital-twin list
amplifier-digital-twin check-readiness aa-e2e; echo "rc=$?"
```

Then hand them a concrete command to run, not a description of one:

```bash
amplifier-digital-twin exec aa-e2e -- bash -lc '<the actual command>'
```

Wait for their verdict before calling the feature done.

## Phase 6: Handoff

Confirm each of these, with evidence, not assertion:

```
Scoped E2E suite green, plus adjacent suites, plus one --fresh run
ruff check, ruff format --check, pyright src/, pytest tests/ -q all clean
docs/spec/*.md updated in this same change
No hardcoded paths. Would this work for someone else who checks out the repo?
No secrets in code, tests, fixtures, logs, or committed config
CHANGELOG.md updated
If bundle assets were added: pyproject force-include has a line per file
If the wire protocol changed: wrappers updated, PROTOCOL_VERSION bumped
Comments explain what the code cannot
```

If any item cannot be honestly satisfied, report it as a gap. Do not check the
box.

Then STOP.

```
Next step: releasing this work (branch, commit, PR, version, tags) is covered by
the amplifier-agent-start-release-process skill, and then, after that PR merges,
the amplifier-agent-finish-release-process skill. Recommend the first one. Do
not do it here.
```

---

## Mechanics

The parts that silently waste the most time.

**Always detach long runs.** A bash tool timeout kills the process group and
orphans the DTU container. `nohup ... &` is not enough.

```bash
setsid bash -c '<cmd> > /tmp/x.log 2>&1' </dev/null >/dev/null 2>&1 &
echo "launched pid $!"
```

**Poll, never block.** Intervals of 60 to 115 seconds. Never above 120.

```bash
sleep 90; echo "=== $(date +%H:%M:%S) ==="; tail -n 25 /tmp/x.log; \
  pgrep -f "framework/cli.py run" >/dev/null && echo RUNNING || echo DONE
```

**Killing processes.** `pkill -f "<pattern>"` can match the invoking shell's own
argv and SIGKILL itself (returncode -9, empty output). Kill by explicit PID, or
use a bracket regex the literal command line will not match:

```bash
pkill -f "suites/skills[ ]-m[ ]dtu"
```

**Orphaned tmux inside the DTU** after a killed run:

```bash
amplifier-digital-twin exec aa-e2e -- tmux kill-server
```

**Repo pytest addopts interfering** with a run:

```bash
uv run pytest ... -o addopts=""
```

**Big output.** Every pytest pipe ends in `| tail -N`. Use `read_file`, `sed -n
'1,120p'`, or `wc -l` instead of dumping whole files. Add a sentinel echo after
a piped run so a truncated tail is distinguishable from a killed process:

```bash
timeout 115 uv run pytest tests/bundle -q 2>&1 | tail -20; echo "EXIT_CHAIN_DONE"
```

**DTU naming.** `aa-e2e` belongs to this harness. `aa-eval` belongs to the eval
harness. Never destroy a DTU you did not create.

**refresh vs run.** `refresh` is a fast code-only in-place update, CLI iteration
only. It reinstalls the tool and wipes the lazily-installed provider module,
which breaks `serve`. HTTP tests need a full `run` or `up`.

**Environment.** The harness preflights `uv`, `amplifier-digital-twin`,
`amplifier-gitea`, `incus`, and `docker`, and fails loud. If preflight fails,
point the user at the Prerequisites section of `docs/E2E_TESTING.md` rather than
guessing. `ANTHROPIC_API_KEY` must be in the host env. The `github_copilot`
suite additionally needs `GITHUB_TOKEN`, and the value is snapshotted at DTU
launch, so exporting it after the container is running has no effect. Re-provision.

**Your working tree reaches the DTU** through a Gitea mirror snapshot that
includes uncommitted and untracked files. The source repo is never mutated. You
do not need to commit anything to test it.

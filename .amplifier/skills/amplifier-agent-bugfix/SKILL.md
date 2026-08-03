---
name: amplifier-agent-bugfix
description: "Triage and fix an amplifier-agent bug, from report to verified fix, using E2E-test-driven development. Covers intake, ruling out non-code causes, root-cause pinning, the coverage-gap gate, red/green regression TDD, scoped verification, and the evaluation path for quality regressions where contracts hold but output got worse. Stops before release."
disable-model-invocation: true
user-invocable: true
---

# Fix an amplifier-agent Bug

## Bug

$ARGUMENTS

If that is empty, ask what is broken before doing anything else. Ask for the
exact command that failed and the exact output, not a paraphrase.

## The Method

A bug is a contract that was under-specified. The fix is not just working code,
it is an extended contract that makes this class of bug unable to recur silently.

So the work is ordered: rule out the cheap non-code causes, pin the root cause
before touching a line, decide honestly why the E2E suite did not catch this,
and only then write the failing test and the fix.

Two rules override everything below. Fixing stays frozen until the root cause is
pinned. Tests are never edited to make a fix pass.

This skill ends at a verified fix. It does not cover PRs, versioning, or release.
When the loop closes, recommend the `amplifier-agent-release` skill.

Work through the phases in order. Use the todo tool to track them.

---

## Phase 0: Intake

Capture the report before interpreting it. You need enough to attempt a repro.

```
The exact command that failed, verbatim
The exact output or error, verbatim
Which surface: CLI, HTTP server, wrapper, bundle, install path?
Which version, branch, or install is it running?
Reproducible every time, or intermittent?
```

Ask for whatever is missing. Do not proceed on a paraphrase.

Not every report has a failure to capture. If the complaint is that everything
still runs but the output got worse, less thorough, more verbose, wrong tool
chosen, gave up early, then you need different evidence:

```
The prompt or task, and what the agent actually produced
What it used to produce, or what good would have looked like
How often it was observed, and how often it was not
What changed recently: our code, a bundle, a prompt, a model, a provider
```

That is a quality report, not a defect report. It runs the same phases, but
Phase 3 will route it to the evaluation suite rather than to an E2E case.

Do not form a hypothesis yet. Do not open source files yet.

## Phase 1: Rule out the cheap causes

Most bugs are in our code, and that is where this usually ends up. This phase is
not a claim otherwise. It is a sweep over the handful of causes that reading the
source cannot find, followed by a repro on a clean box.

That is the asymmetry worth respecting. If the cause is a stale process, a
cached artifact, a container misconfiguration, or an upstream outage, then no
amount of reading the diff will reveal it, and the diff will look innocent
because it is. Clearing these first buys you the right to trust the code you are
about to read.

The question to ask is: what in this setup, other than the source under test,
could differ from what I am assuming? Below are common instances of that
question in this project. They are examples, not a checklist to complete. Run
the ones that plausibly apply to the reported surface, skip the rest, and add
whatever the actual report suggests.

```
Is a server already running from BEFORE the change or the env export?
  pgrep -af "amplifier-agent serve"
  ps -o lstart= -p <pid>
  tr '\0' '\n' < /proc/<pid>/environ | grep -i <THE_VAR>
  Credentials and env are read once at startup. `launch` reuses a live server.

Is a cache stale? Name WHICH cache. There are two and they are unrelated.
  prepared bundle cache    amplifier-agent cache clear
  provider model cache     ~/.cache/amplifier-provider-*/models_cache.json  (24h TTL)

Is a proxy in play? Inside a DTU this is likely.
  env | grep -i proxy
  retry the same call with --noproxy "*"
  mitmproxy buffers whole response bodies, which silently kills SSE streaming.

Is something upstream down or rate-limiting?
  check the provider's or forge's status page and the raw HTTP status
  a 5xx, a 429, or an auth error from upstream is not our defect

Is the DTU itself misprovisioned rather than the code under test?
  amplifier-digital-twin check-readiness <name>; echo "rc=$?"
  a harness that never came up cleanly produces failures that look like ours

Is the install or venv path the variable rather than the source?
  build a throwaway venv and repro there:
  uv venv /tmp/repro-venv && uv pip install <local checkout> && <the command>

For a quality report: did the model change out from under us?
  compare the model actually used, not the one configured
  a provider default, a routing matrix, or an upstream model revision moves
  behavior with no change on our side
```

Do not linger on the checks above. If they do not settle it, stop poking at the
box it was reported on and reproduce it on a clean one.

That is the decisive move in this phase, and it is hands-on. A fresh DTU carries
none of your local state, so a bug that still reproduces there is not your
machine, and a bug that vanishes there is. Provisioning one is the most
expensive step so far, which is why it comes after the checks above rather than
before, but the box earns its keep: it is the same one you will probe in
Phase 2.

```bash
uv run python tests/e2e/framework/cli.py up
amplifier-digital-twin check-readiness aa-e2e; echo "rc=$?"
amplifier-digital-twin exec aa-e2e -- bash -lc '<the exact reported command>'
```

Use `cli.py run --fresh` instead when you want the harness to build the box from
scratch rather than reuse a warm one. Either way, run the reported command
verbatim before running anything clever. If it does not reproduce, say so and
find out what differs before going further; an unreproducible bug cannot be
verified fixed.

If one of the checks does explain it, the defect is not in the logic you were
about to read, but that does not always mean there is nothing to change. A
startup check, a clearer error, or a guardrail is often the right fix, and it is
still code. Decide that in Phase 3.

Either way, record what you ruled out and how you reproduced it. Phase 2 should
not re-litigate either.

## Phase 2: Pin the root cause

Fixing is frozen for this entire phase. No source edits, no speculative patches,
no "let me just try changing this."

State at most two named hypotheses, and they must imply DIFFERENT fixes. If two
hypotheses lead to the same patch, the distinction does not matter and you are
stalling.

```
(A) <hypothesis>   -> if true, the fix is <X>
(B) <hypothesis>   -> if true, the fix is <Y>

DECISION RULE: <the specific observation that selects A over B>
```

Start with the artifacts you already have: logs, `events.jsonl`, stdout
envelopes, cached state files, the failing run's output.

Then run experiments. This phase is hands-on, not desk analysis. A hypothesis you
cannot settle by changing one variable and observing the result is not a
hypothesis, it is a guess. What wastes time is re-running the same thing
unchanged and hoping it says something new. What earns the answer is deliberately
constructing the two states that tell A and B apart.

```
Single-variable toggle    same call with and without one flag or env var
Two-environment A/B       a DTU on public main vs a DTU on the local tree
Probe the live box        exec into a running DTU and measure the actual value
Branch-diff archaeology   git diff origin/main..HEAD -- <suspect files>
                          git diff HEAD --      (uncommitted work counts too)
```

Most of this happens in a DTU, because that is where the software runs as
installed rather than as imported. Two shapes, depending on the question:

```
One box, many probes    reuse the warm DTU from Phase 1 and exec probes into it
                        push probe scripts in with file-push; see Mechanics for
                        why writing them at exec time does not work

Two boxes, one variable launch a second DTU that differs in EXACTLY one thing
                        and run the identical command in both. Name them so the
                        difference is obvious, and destroy both when done
```

`git bisect` is almost never the right tool here. Constructing the two states
directly is faster and tells you more.

For intermittent bugs, construct the broken state rather than waiting for it. A
bug you can only observe by luck is a bug you cannot verify you fixed.

Delegation works well for recon. Dispatch two to four read-only explorer agents
in parallel with disjoint scopes and clean-slate context. Label static code
reading as provisional: it is a source of hypotheses, not of conclusions. Give
each agent one fact to determine, empirically, with explicit anti-scope (do not
modify, do not commit, do not stage, do not destroy any DTU).

Report the pinned cause and the evidence that pins it to the user before moving
on. "I think it is X" is not pinned. "X, because this observation rules out Y"
is pinned.

## Phase 3: Why did E2E miss it? (gate)

This is a gate, not a reflection. You cannot proceed to Phase 4 until this
question is answered and the answer names where the regression test goes.

Answering this is usually a read, not a run. Open the `cases.py` of the suite
closest to the broken behavior and compare what it asserts against the root
cause you pinned in Phase 2. That is normally enough to tell which class below
applies.

Run the suite only if reading leaves it genuinely ambiguous, and then run the
narrowest slice on the warm DTU rather than the suite:

```bash
uv run python tests/e2e/framework/cli.py run <area> --skip-setup -rxX -k "<case>"
```

If a case that should already cover this turns out to be red, coverage exists,
that failure is your repro, and you can go straight to Phase 5.

Classify the miss into exactly one of these:

```
1. NO COVERAGE
   The contract is testable by the harness and simply was never asserted.
   -> Add a case to the closest existing suite, or a new suites/<area>/.
      This is the normal case. Proceed to Phase 4.

2. SHALLOW COVERAGE
   A test exists and passes, but asserts less than the contract. Exit 0 was
   checked; the behavior underneath was not.
   -> Strengthen the existing case rather than adding a new one. Proceed to
      Phase 4, which is where you confirm the strengthened form goes red.

3. WRONG LAYER
   The contract holds and the commands run. What moved is the QUALITY or the
   judgment of the output: worse answers, skipped steps, wrong tool chosen,
   gave up early, more verbose, less thorough. E2E asserts that a thing
   happened, not that it was any good, so a green suite here is correct rather
   than negligent.
   -> See "When the classification is 3" below. The artifact is an evaluation,
      not an E2E case.

4. STRUCTURALLY INVISIBLE
   The harness cannot see this bug class no matter what test you write, because
   of how it provisions. The DTU installs from a Gitea mirror of the working
   tree via framework/provisioning/install-amplifier-agent.sh, so a missing
   runtime dependency arrives transitively, a stale prepared-bundle cache never
   forms, and a broken published-install path is never exercised.
   -> See "When the classification is 4" below. STOP and escalate.

5. NOT A CODE CONTRACT
   Phase 1 found it: stale process or cache, proxy, container misprovisioning,
   upstream outage. The suite was correct to be silent, because there is no code
   contract to assert.
   -> Do NOT invent an E2E test for this. Writing one tests the box or the
      network, not the code, and it will be flaky forever. If the failure was
      transient and nothing on our side should change, say so and stop; not
      every report is a defect. Otherwise the artifact is a guardrail, a
      startup check, a clearer error, or a doc. Record the classification and
      skip to Phase 5.
```

Write the classification down explicitly. One line, naming the class and the
destination file.

**When the classification is 3, WRONG LAYER.** Evaluations measure what tests cannot: the quality of
open-ended, judgment-laden output, where "correct" is a distribution rather than
a value. The suite lives in `.amplifier/evaluation/`, and the amplifier-agent
capability tasks are under `tasks/amplifier-agent-capabilities/`.

Know what this suite does NOT do before leaning on it:

```
It scores a trial. It does not store a baseline, average across trials, or
  detect a regression. --trials defaults to 1, and nothing compares this run to
  a previous one. That comparison is your job.
One trial is a sample, not a finding. Behavior is probabilistic. Run two or
  three before believing a number moved.
A low score is not proof the agent got worse. A broken task or a badly
  calibrated rubric produces the same number. Read the transcript first.
```

For this path the loop below replaces Phases 4 through 6:

```
1. Reproduce the judgment, not the error. Pick the closest existing task, or
   write a scenario that puts the agent in the reported situation.
2. Run it against current code, several trials, one at a time. Record the
   numbers. They are the only baseline that will exist.
3. Read the artifacts before the score: ai_user_transcript.txt,
   interaction.json, grader/<eval>/initial_report.md, extracted/. Confirm the
   agent really did the bad thing, and that the grader judged it for the right
   reason rather than mis-scoring acceptable work.
4. A/B before rubric. Run the same task against the suspected-good and
   suspected-bad configuration, varying EXACTLY one dimension. The diff is the
   measurement. Only touch a rubric when the diff is genuinely ambiguous.
5. Fix, then re-run the same task at the same trial count and compare against
   the numbers from step 2.
```

```bash
cd .amplifier/evaluation && uv sync
uv run python run.py validate 2>&1 | tail -40
uv run python run.py run --list-tasks

setsid bash -c 'uv run python run.py run --agents amplifier-agent-local \
  --tasks <task-id> --trials 3 --max-parallel 1 \
  --output-dir runs/matrix/<label> > /tmp/eval.log 2>&1' \
  </dev/null >/dev/null 2>&1 &
echo "launched pid $!"
```

Poll each trial's `state.json`, not just the log; the summary is written last.

If no existing task covers the behavior, adding one is real work and it changes
what the suite measures. Propose it and get the user's agreement before
building it.

**When the classification is 4, STRUCTURALLY INVISIBLE.** Typical members are a runtime dependency
that is imported but never declared, a cache key that omits something it depends
on, and a published-install path that breaks while the from-source path works.
They share a signature: the defect lives in how the software is packaged,
installed, or persisted between runs, so a harness that provisions from your
working tree can never observe it.

Catching this class needs a different box: one that pre-installs nothing, runs
the real published install path, and then exercises the command. Before assuming
that box does not exist, check.

```bash
ls .amplifier/digital-twin-universe/profiles/
grep -rn "install.sh\|install-amplifier-agent" tests/e2e/ | head -20
```

If a clean-install harness already exists, use it and treat this as class 1. If
it exists but only probes reachability rather than running install and launch
end to end, extending it is the smaller job. Either way, do not quietly proceed.
STOP and put the choice to the user:

```
(a) Build or extend the clean-install regression box as part of this fix.
    Larger scope, but this bug class stops being invisible.
(b) Fix the bug now, accept no regression coverage, and record the gap
    explicitly so it is not mistaken for covered.
```

Their call, not yours. Either way, Phase 7 requires you to state plainly that
this bug has no regression coverage.

## Phase 4: RED

Write the regression test. Run it. Confirm it fails for the right reason.

The test is a CONTRACT, not a repro script. This is the difference that matters:

```
Repro script    reproduces the exact conditions of this one incident
Contract        asserts the behavior that was violated, so the whole CLASS of
                bug fails the suite, not just the instance you happened to hit
```

Name the case after the contract it protects, not after the bug or its issue
number.

Suite layout, if a new one is warranted:

```
tests/e2e/suites/<area>/
  __init__.py
  cases.py            E2ECase definitions
  test_<area>.py      pytestmark = pytest.mark.dtu, parametrized over CASES
  conftest.py         only if the suite needs to push fixture files
  fixtures/           static payloads pushed into the DTU
```

Nothing under `tests/e2e/framework/` should need to change. If it does, raise it
before proceeding.

Rules that are not negotiable:

```
Assert the PUBLIC contract only. Never assert on internals, log formats, or
  anything a legitimate refactor would break.
Prefer extending an existing suite over creating a new one. A new suite is
  justified when the contract is genuinely new, not when it is merely new to you.
Skip unit tests unless the defect is purely internal with no observable surface.
  The E2E contract is what matters here.
```

Preflight before committing to a DTU run:

```bash
python3 -m py_compile tests/e2e/suites/<area>/*.py && echo "SYNTAX OK"
uv run pytest tests/e2e/suites/<area> --collect-only -q 2>&1 | tail -25
```

Then run RED, detached, to its own log:

```bash
rm -f /tmp/red_run.log && setsid bash -c \
  'uv run python tests/e2e/framework/cli.py run <area> -rxX > /tmp/red_run.log 2>&1' \
  </dev/null >/dev/null 2>&1 &
echo "launched pid $!"
```

Poll it (see the Mechanics appendix). When it finishes, verify the failure
REASON matches the pinned root cause from Phase 2. A test that fails for an
unrelated reason is not capturing the bug, and a test that passes on the first
run is not capturing anything at all.

If the new test passes against the broken code, the test is wrong. Fix the test,
not the expectation, and re-run until it is red for the right reason.

Report the red result to the user before implementing.

## Phase 5: GREEN

Implement the fix. Fix the root cause you pinned, not the symptom the test
happens to catch.

If Phase 3's answer was class 5, there is no test and there will not be one.
Skip the test-weakening rules and the `-k` iteration below; verify the fix by
re-running the Phase 1 repro on a clean box instead.

There is no prescribed shape here. Sometimes the right answer is a one-line
patch; sometimes the honest fix for a pinned cause is a rework of the thing that
was wrong. Judge it on the cause: the best solution is the one that makes this
class of bug not happen again, and a minimal patch that leaves the underlying
defect in place only guarantees a second visit.

What the diff must be is JUSTIFIED, which is not the same as small. Everything
in it should trace back to the root cause. Unrelated improvements you noticed
along the way do not, however tempting. Note them separately.

If the honest fix is substantially larger than the bug appeared to warrant, that
is a scope decision and it belongs to the user. Say what you found, what the
small fix would leave behind, and what the larger one costs. Then let them
choose.

```
Confirm the mechanism CAUSALLY before believing the fix. A test going green
  right after an edit is correlation. Know WHY it went green.
Do NOT refactor adjacent code you noticed along the way. Note it separately.
Do NOT commit or stage.
Check for drift every few iterations:  git diff --stat
  Unrelated hunks and lockfile churn creep in. Split them out.
```

The named failure mode of this phase is test-weakening. In feature work a
failing test means the implementation is unfinished. In bug work a failing test
is authority.

```
If a NEW test looks wrong, you wrote it wrong. Fix it, and re-confirm red.
If a PRE-EXISTING test starts failing, that test is the contract. Revert the
  source and escalate. Do NOT relax the assertion to fit your fix.
If you believe a pre-existing test is genuinely wrong, that is the one case to
  come back to the user. Bring evidence.
```

Iterate narrow. Re-run the single failing case against the warm DTU, not the
whole suite:

```bash
rm -f /tmp/green_run.log && setsid bash -c \
  'uv run python tests/e2e/framework/cli.py run <area> --skip-setup -rxX \
   -k "<the one case>" > /tmp/green_run.log 2>&1' </dev/null >/dev/null 2>&1 &
echo "launched pid $!"
```

Re-launch the SAME subset to the SAME log name across iterations so runs are
comparable. Only change the log name when the scope changes.

Keep the fast local gates green as you go:

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pyright src/
uv run pytest tests/ -q
```

Delegation works for the implementation loop. Hand a builder agent the pinned
root cause and a precise scope, with clean context and explicit anti-scope (do
not commit, do not stage, do not edit tests). Keep test-running and result
interpretation in the driving session so the loop stays coherent.

## Phase 6: Verify

Never declare done on the `-k` slice that proved the point. Widen:

If there is no regression case because Phase 3's answer was class 5, start the
ladder at rung 2 with the suite nearest the change.

```
1. -k "<the regression case>"             the fix works
2. cli.py run <area> --skip-setup         the suite it lives in
3. cli.py run <area> <adjacent>           the neighbors it could have broken
4. uv run pytest tests/ -m "not dtu"      everything non-DTU
5. cli.py run                             all e2e suites
6. cli.py run --fresh                     clean-box confirmation
```

Do not run `--fresh` while iterating. Reuse the warm named DTU with
`--skip-setup` until the narrow scope is green.

Record known-red tests to a baseline before you widen, so only deltas count:

```bash
uv run pytest tests/ -q -m "not dtu" 2>&1 | tail -40 > /tmp/baseline.txt
```

Name up front which existing failures are expected, so a known-bad suite is not
mistaken for a regression you caused.

If the bug was eval-scored, re-run the specific eval task that regressed. Do not
run the full matrix for one fix.

## Phase 7: Handoff

Confirm each of these, with evidence, not assertion:

```
The root cause is stated in one sentence, and the evidence that pins it
The Phase 3 classification is recorded, with the destination it implied
The regression test fails on the old code and passes on the new
If this was an evaluation concern: task id, trial count, and the scores before
  and after, from the same task at the same trial count
Scoped suite green, plus adjacent suites, plus one --fresh run
ruff check, ruff format --check, pyright src/, pytest tests/ -q all clean
Every hunk in the diff traces to the root cause. No unrelated refactors, no
  lockfile churn. If the fix was larger than the bug looked, the user agreed to it
No pre-existing test was weakened or deleted
docs/spec/*.md updated if the fix clarified or changed a contract
CHANGELOG.md updated
Every DTU created for this hunt is destroyed
git status --short shows no stray probe scripts, logs, or patch files
```

If the Phase 3 answer was 4 or 5, state plainly that this bug has no regression
coverage and why. That is an honest gap. Do not let it read as covered.

If any item cannot be honestly satisfied, report it as a gap. Do not check the
box.

Then STOP.

```
Next step: releasing this work (branch, commit, PR, version, tags) is covered by
the amplifier-agent-release skill. Recommend it. Do not do it here.
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

Completion detection on a detached run is not fully reliable. If the log has not
advanced across several consecutive polls, stop polling and re-run in the
foreground under `timeout 115` rather than continuing to wait.

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

**Stale harness state.** `/tmp/amplifier-*-e2e/state.json` can point at a DTU
that no longer exists, and the failure looks like a harness bug. If DTU
discovery behaves strangely, delete it and let it re-provision.

**The DTU filesystem does not persist between `exec` calls.** Files written in
one exec are gone in the next. Ship probe scripts in with `file-push`, do not
write them at exec time.

**Never hot-patch site-packages inside a container.** It contaminates the box you
are measuring and needs a hand-written unpatch to undo. Push a probe alongside
instead, or rebuild.

**Repo pytest addopts interfering** with a run:

```bash
uv run pytest ... -o addopts=""
```

**Big output.** Every pytest pipe ends in `| tail -N`. Use `read_file`, `sed -n
'1,120p'`, or `wc -l` instead of dumping whole files. Add a sentinel echo after
a piped run so a truncated tail is distinguishable from a killed process:

```bash
timeout 115 uv run pytest tests/ -q 2>&1 | tail -20; echo "EXIT_CHAIN_DONE"
```

**DTU naming.** `aa-e2e` belongs to the E2E harness. A throwaway box for one
experiment gets its own name and gets destroyed when the experiment ends. Never
destroy a DTU you did not create.

**Eval harness specifics.** `--tasks` takes flat task ids, not `group/id`. Each
trial launches and destroys its own DTU, so there is no warm box to reuse and no
`--skip-setup` equivalent; a trial count is a container count. The Gitea mirror
that makes a run test YOUR working tree is `aa-eval`, on its own port, separate
from the E2E harness's mirror, and it is only stood up when
`amplifier-agent-local` is among `--agents`. With any other agent the rewrite
variables are unbound and you are measuring upstream, not your change.

**Never commit eval run output.** `runs/` holds provider keys, full prompts and
responses, and absolute host paths. It is gitignored. Keep it that way.

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
do not need to commit anything to test a fix. This is also exactly why the
harness cannot see packaging and published-install bugs. See Phase 3, class 4.

**Triage notes are transient.** If a root cause is systemic and the fix is
deferred, write the note outside the repo, in `.ai_working/` or wherever the user
keeps working notes, and write it in their voice. What is durable in the repo is
`docs/spec/`. If the bug revealed an under-specified contract, the durable
artifact is a spec update, not a note file.

---
name: amplifier-agent-bugfix
description: "Triage and fix an amplifier-agent bug, from report to verified fix. Covers intake, ruling out non-code causes, root-cause pinning, checking the behavior against the contracts, the coverage-gap gate, red/green regression with evaluations and tests, scoped verification, and quality regressions where contracts hold but output got worse. Stops before commit and release."
disable-model-invocation: true
user-invocable: true
---

# Fix an amplifier-agent Bug

## Bug

$ARGUMENTS

If that is empty, ask what is broken. Get the exact code or request that failed
and the exact output, not a paraphrase.

## The Method

A bug is a contract that was violated or under-specified. The fix is working
code plus coverage that stops this class of bug from recurring silently.

```
Fixing stays frozen until the root cause is pinned.
Where code and a contract disagree, the code is wrong.
Tests and rubrics are never edited to make a fix pass.
```

Work through the phases in order with the todo tool. Stop before commit and
release. `references/mechanics.md` covers running and polling evaluations.

---

## 0. Intake

```
Failing code, script, or HTTP request, and its output, verbatim
Surface: Python, TypeScript, or HTTP face
Provider and model
Install: local checkout, v1 branch, built wheel or package, build/face
OS and runtime versions
Every time, or intermittent?
```

If everything runs but the output got worse, it is a quality report: capture
the prompt, what the agent produced, what good looked like, how often, and what
changed recently. It follows `references/quality-regressions.md` after Phase 3.

No hypotheses and no source reading yet.

## 1. Rule out cheap causes, then reproduce

Sweep the causes that reading source cannot find. Pick the ones that apply:

```
Environment drift     uv sync --frozen --all-packages --all-groups
Stale TS build        packages/typescript/dist/ and runtime/linux-x64/ are built;
                      rebuild with scripts/check.py --runtime
Stale face process    pgrep -af amplifier-agent-face  (env is read at start)
Wrong code installed  a trial's provenance.json records what it installed
Upstream failure      5xx, 429, or auth errors from the provider
Model moved           compare the model actually used, not the one configured
```

Then reproduce on a clean box: an evaluation trial against the checkout, using
the closest task or a new one that performs the reported steps. When no real
model is needed, a scripted-provider test is faster. An unreproducible bug
cannot be verified fixed; find out what differs before going further.

A non-code cause can still warrant a change: a clearer error, an earlier check.
Record what you ruled out and how you reproduced it.

## 2. Pin the root cause

No source edits. State at most two hypotheses that imply DIFFERENT fixes:

```
(A) <hypothesis>   -> fix <X>
(B) <hypothesis>   -> fix <Y>
DECISION RULE: <the observation that selects A over B>
```

Start from existing evidence: `driver/result.json`, `driver/events.jsonl`,
`grader/<evaluation>/report.md`, stored sessions. Then change one variable at a
time:

```
Toggle          same call with and without one option or env var
Surface A/B     Python vs TypeScript vs HTTP: one failing points at that
                binding or the face, all failing points at the engine
Install A/B     the same task with install: github and install: checkout
Scripted        tests/support/scripted_provider.py takes the model out, and
                replays the exact sequence behind an intermittent bug
Diff            git diff origin/v1..HEAD -- <files> ; git diff HEAD --
```

Read-only explorer agents with disjoint scopes help with recon; treat static
reading as a source of hypotheses, not conclusions.

Report the cause and the observation that rules out the alternative before
moving on.

## 3. Check the contract

Read the governing clause in `contracts/*.v1.md` and the matching
`docs/<binding>/reference.md`.

```
Code violates a clear contract    proceed
Docs disagree with the contract   fix the docs in this change too
Contract silent or ambiguous      STOP and raise it; contracts change only by
                                  owner-ratified amendment
Behavior IS the contract          not a bug; cite the clause and stop
```

Decide where the fix belongs: binding, face, or engine.

## 4. Why was it missed? (gate)

Usually a read: compare the closest `evaluations/tasks/` rubric and tests
against the root cause. Record one line: the class and the destination file.

```
1 NO COVERAGE        nothing asserts it -> add or extend an evaluation task,
                     plus a test when a scripted provider can reproduce it
2 SHALLOW            coverage passes but asserts less than the contract
                     -> strengthen the criterion or test
3 SCRIPTED ONLY      tests pass with a double; real provider or install differs
                     -> evaluation task; fix the double if it misrepresents
4 QUALITY            contract holds, judgment got worse
                     -> references/quality-regressions.md replaces Phases 5-7
5 NOT CODE           Phase 1 cause -> no invented coverage; a guardrail or
                     error message, or nothing. Skip to Phase 6
6 UNREACHABLE        no harness can observe it (platform, install path,
                     published artifact) -> STOP; the user chooses between
                     extending a harness and fixing with a recorded gap
```

## 5. RED

Write coverage that asserts the violated contract, so the whole class fails, not
just this incident. Name it after the contract, not the bug. Extend existing
tasks and test files before creating new ones. Assert the public surface only.

Run it: the test narrowly, the evaluation once against the checkout. Red means
the grader or assertion names the violated behavior. `error` or `timeout` is the
harness, not red. Coverage that passes on broken code is wrong; fix it until it
is red for the right reason.

Report red before implementing.

## 6. GREEN

Fix the pinned cause, not the symptom. Every hunk traces to the root cause; note
unrelated improvements separately. If the honest fix is much larger than the
bug looked, lay out both options and let the user choose.

Know WHY it went green. Check `git diff --stat` for drift. Do not commit or stage.

```
New coverage looks wrong        you wrote it wrong; fix and re-confirm red
Existing coverage goes red      it is the contract; revert and escalate
Existing coverage seems wrong   bring evidence to the user
```

A builder agent can run the implementation loop with anti-scope (no commits, no
staging, no test or rubric edits). Keep evaluation runs and their
interpretation in the driving session.

## 7. Verify

Name expected existing failures first, then widen:

```
1 pytest <file> -k "<case>"                          the fix
2 pytest tests/e2e/<surface>/ ; pnpm test            the surface
3 regression task, trials: 1                         real model
4 regression task plus neighbors, trials: 3          stable, nothing adjacent broke
5 prek run --all-files
6 uv run --all-packages python scripts/check.py --runtime
```

Class 5 starts at rung 2.

## 8. Handoff

Confirm with evidence:

```
Root cause in one sentence, with the evidence that pins it
Contract clause violated, or the amendment the user ratified
Phase 4 class and destination
Coverage red on old code, green on new
Rungs 2-6 green
No test or rubric weakened or deleted
Public descriptions updated if callers see a change: docs/, README.md,
  packages/*/README.md, skills/amplifier-agent/
Universes you created destroyed; no scratch files in git status --short
```

For classes 5 and 6, state plainly that the bug has no regression coverage and
why. Report anything unmet as a gap. Then STOP.

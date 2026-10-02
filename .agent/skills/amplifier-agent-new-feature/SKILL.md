---
name: amplifier-agent-new-feature
description: "Develop a new amplifier-agent feature from idea to verified behavior. Covers orientation against the contracts, planning, updating every public description, red/green development driven by an evaluation task and supporting tests, the widening verification ladder, and a hands-on human check. Stops before commit and release."
disable-model-invocation: true
user-invocable: true
---

# Develop a New amplifier-agent Feature

## Feature

$ARGUMENTS

If that is empty, ask what feature to build.

## The Method

Contract first, evaluation-proven. The feature is done when a real model passes
an evaluation task through every affected surface, the local checks are green,
and a human has tried it. Scripted-provider tests speed up the loop; they are
not the proof.

```
Contracts change only by owner-ratified amendment. Raise it, never assume it.
Evaluation rubrics and tests are never edited to make a change pass.
Public descriptions change in the same change as the behavior.
```

Work through the phases in order with the todo tool. Stop before commit and
release. `references/mechanics.md` covers running and polling evaluations.

---

## 0. Orient

Read before source:

```
AGENTS.md                          gates and the four questions
contracts/VISION.md, README.md     purpose, non-goals, binding/engine/face
contracts/<relevant>.v1.md         the contract this touches
docs/<binding>/reference.md        current interface per binding and the face
docs/development/architecture.md   source layout and ownership
docs/development/checks.md         test layout
evaluations/README.md              the harness and task-writing rules
```

Answer the four questions from `AGENTS.md` explicitly:

```
1 Vision and contracts   Does it fit? Which clause permits it?
2 Public surface         Every place AGENTS.md lists that describes it.
                         Which surfaces carry it: Python, TypeScript, HTTP face?
                         Additive only within a major version (docs/versioning.md);
                         breaking needs explicit sign-off.
3 Evaluations            Nearest existing task; the task that will prove this.
4 Tests                  Which tier helps the implementation or pins what an
                         evaluation cannot observe.
```

A read-only recon agent can map code paths while you read the contracts.

## 1. Plan

Present three to five approaches with real tradeoffs; the user picks. Write the
plan outside the repo, wherever the user keeps notes. Never commit it.

```
The surface change in one sentence, and the clause that permits it
Public descriptions to update, by file
Surfaces affected
Evaluation task(s) to add or extend, by id, and what each rubric checks
Tests to add, by file, and what each pins
Iteration subset and widening ladder
```

## 2. RED

**Evaluation task** under `evaluations/tasks/<group>/<name>/`, following the
task-writing rules in `evaluations/README.md`; `(cd evaluations && uv run pytest)`
confirms it loads. Run it once against the checkout. Red means `failed`
with the grader naming the missing behavior, or a preflight refusal naming the
missing field. `error` or `timeout` is the harness; fix that first.

**Tests** where they speed the loop, in the tier from
`docs/development/checks.md#tests`. Assert the public contract only. Not-yet-built
behavior gets `@pytest.mark.xfail(reason=..., strict=True)`.

Report red before implementing.

## 3. GREEN

Name expected existing failures first, then widen only when the narrow scope is
green:

```
1 uv run --all-packages python -m pytest <file> -k "<case>"      fastest signal
2 uv run --all-packages python -m pytest tests/e2e/<python|http>/
  (cd packages/typescript && pnpm test)                          the surface
3 prek run --all-files
4 new task, trials: 1                                            real model
5 new task plus neighbors, trials: 3                             nothing adjacent broke
6 uv run --all-packages python scripts/check.py --runtime
```

Do not run the full regression profile while iterating. If a test or rubric
looks wrong, escalate.

A builder agent can run the implementation loop with anti-scope (no commits, no
staging, no test or rubric edits). Keep evaluation runs and their
interpretation in the driving session.

## 4. Human loop

Give the user the exact script or request to try, built on the surface's
quickstart and installed from the checkout as `docs/install.md` describes:

```
Python       docs/python/quickstart.md       editable install, uv run python hello.py
TypeScript   docs/typescript/quickstart.md   build and npm install --install-links
HTTP face    docs/http/quickstart.md         uv run amplifier-agent-face
```

Wait for their verdict.

## 5. Handoff

Confirm with evidence:

```
Fits VISION.md and the contracts, or the user ratified an amendment
Public descriptions updated in this change
Every surface the contract requires carries the feature
New task passes, with trial count and scores; neighbors still pass
prek and scripts/check.py --runtime clean
evaluations/README.md updated if the harness changed
No hardcoded paths, no secrets
Universes you created destroyed; no scratch files in git status --short
```

Report anything unmet as a gap. Then STOP.

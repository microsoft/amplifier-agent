---
name: amplifier-agent-finish-release-process
description: "Finish an amplifier-agent release after the release PR has merged: verify preconditions, push the tag that publishes, watch the publish workflows, confirm what users actually receive, and bump the amplifier-app-opencode floor if this release warrants it. This is the SECOND of two release skills. Run amplifier-agent-start-release-process first; do not run this until the release PR is merged into main."
disable-model-invocation: true
user-invocable: true
---

# Finish an amplifier-agent Release

## Release

$ARGUMENTS

If that is empty, do not ask. Run Phase 0 and derive the pending release from
the repo state, then confirm it with the user.

## The Method

Pushing the tag is the release. There is no undo: PyPI and npm do not allow
re-uploading a version, and a deleted tag has already triggered the workflow.
Everything before the push is therefore verification, and every check is
fail-closed. If a precondition cannot be confirmed, stop and report; do not
push and inspect afterwards.

Two rules override everything below.

**The mechanics live in `RELEASING.md`, not here.** That file is canonical for
the artifact/tag/target table, the exact tag commands, and the one-time PyPI
trusted-publisher setup. Read it before Phase 1 and use its commands. If this
skill and `RELEASING.md` disagree about a mechanical fact, `RELEASING.md` wins
and you should say so.

**Merging publishes nothing; the tag does.** If the release PR is not merged
into `main`, this skill does not run. Send the user back to
`amplifier-agent-start-release-process`.

Work through the phases in order. Use the todo tool to track them.

---

## Phase 0: Preconditions

Every one of these is a hard gate. Report the full result set before acting on
any of it.

```bash
git fetch origin --tags
git checkout main && git pull
git status --porcelain          # must be empty
git log --oneline -5
```

Confirm, one at a time:

**The release PR is merged.** `gh pr list --state merged --limit 5` shows it,
and `main` contains its commit. If it is still open, stop.

**Local `main` is at `origin/main`.** `git rev-parse HEAD origin/main` returns
the same sha twice. The tag must come from the tip of `main`.

**The working tree is clean.** A tag records a commit, not your working tree,
so a dirty tree means the thing you tested is not the thing you are releasing.

**Each manifest version matches the tag you intend to push.** The publish
workflows verify this and hard fail on mismatch, but finding out here is free
and finding out there burns the tag.

```bash
grep -m1 '^version' pyproject.toml                      # -> vX.Y.Z
grep -m1 '"version"' wrappers/typescript/package.json   # -> wrapper-vX.Y.Z
grep -m1 '^version' wrappers/python-py/pyproject.toml   # -> py-vX.Y.Z
```

**The tag does not already exist**, locally or on the remote:

```bash
git tag --list 'vX.Y.Z'
git ls-remote --tags origin 'refs/tags/vX.Y.Z'
```

If it exists on the remote, the release already happened. Do not delete and
re-push a published tag; PyPI and npm will reject the re-upload and the GitHub
Release will be inconsistent with what was actually published. Skip to Phase 3
and verify what is already out there.

**`CHANGELOG.md` has a heading for this version** and `## [Unreleased]` is
empty. If the changelog was not updated, the release is not ready; that is
Phase 3 of the start skill and it belongs in the PR, not in a follow-up.

**A `py-v*` tag is a first.** There are no `py-v*` tags in history. If this
release includes one, tell the user before pushing that it is the first
exercise of that package's OIDC trusted-publisher handshake, that per
`RELEASING.md` the handshake can only be proven by a real tag-triggered run,
and that a failure means the pending publisher on PyPI is not configured
correctly.

---

## Phase 1: Tag

Read `RELEASING.md` and use its commands. The namespaces:

```
v*          engine     publish-python.yml (publish-engine) -> PyPI
                       release-notes.yml                   -> GitHub Release
wrapper-v*  TS SDK     publish-wrapper.yml                 -> npm
                       release-notes.yml                   -> GitHub Release
py-v*       Py SDK     publish-python.yml (publish-wrapper) -> PyPI
                       no GitHub Release
```

A tag outside these namespaces triggers nothing and looks exactly like success.

Prefer annotated tags, which record who cut the release and when:

```bash
git tag -a vX.Y.Z -m "amplifier-agent X.Y.Z"
git push origin vX.Y.Z
```

Existing history mixes annotated and lightweight tags. That is known and is not
yours to fix here; just do not add to the inconsistency.

For a coordinated release, push the engine tag first so its GitHub Release
exists before the wrapper releases reference the same commit. Push tags one at
a time and confirm each workflow starts before pushing the next. A batch push
that half-fails is much harder to read.

**Confirm the exact tag names with the user immediately before pushing.** This
is the irreversible step.

---

## Phase 2: Watch

Do not declare success on a tag push. Watch the run.

```bash
gh run list --limit 5
gh run watch <run-id>
```

Expect, per tag:

- `publish-python.yml` or `publish-wrapper.yml` succeeds
- `release-notes.yml` succeeds for `v*` and `wrapper-v*` only

If a publish job fails, read the failure before retrying. The two common ones:

**Version mismatch.** The job compares the tag against the manifest and fails
deliberately. The tag is wrong or the manifest is wrong. Do not force it; work
out which, fix `main` if needed, and cut a new patch version. Never re-point a
pushed tag.

**Trusted-publisher rejection.** The OIDC handshake failed. The pending
publisher on PyPI, or the `pypi` GitHub environment, is not configured as
`RELEASING.md` describes. This is repo/PyPI settings, not code. Report it to
the user with the exact error; do not attempt to work around it with a token.

Poll rather than blocking on a long wait. Never use a bash timeout above 120
seconds.

---

## Phase 3: Verify what users receive

The workflow going green means the upload succeeded. It does not mean users can
get it. Check the channel that actually reaches them.

**Git is the supported install channel.** `install.sh` and `amplifier-agent
update` install from git, and `install.sh` resolves "latest" through the GitHub
Releases API. So the GitHub Release is the artifact users receive:

```bash
gh release view vX.Y.Z
```

Confirm it exists, is not a draft, and its notes are non-empty.

Note that `release-notes.yml` marks a release as prerelease when the tag
contains `-`, so **every `wrapper-v*` release is marked prerelease**. That is
expected and documented. It also means wrapper tags never win the "latest
release" lookup that `install.sh` uses, which is fortunate rather than
designed.

**PyPI and npm:**

```bash
curl -s https://pypi.org/pypi/amplifier-agent/json    | head -c 400
npm view amplifier-agent-ts version
```

Treat the PyPI engine upload as published-but-unexercised. Nothing in this repo
installs the engine from PyPI, so a successful upload is not evidence that the
installed package works. Do not claim it is verified. If the user wants real
verification, that is a fresh install from the git tag in a clean environment,
not a PyPI metadata check.

---

## Phase 4: Downstream

The start skill recorded a decision in the PR about whether
`amplifier-app-opencode` needs a floor bump. Carry it out now, or confirm
explicitly that it does not apply and say so.

The floor is a requirement statement, not a mirror of the latest release. Bump
it only when opencode depends on something this release introduced: the HTTP
face, `GET /v1/models`, `/v1/skills`, `/v1/modes`, host-config handling passed
through `--host-config`, `PROTOCOL_VERSION`, or the `run`/`serve` contract. An
unnecessary bump forces every opencode user through a reinstall.

If it applies, work in the `amplifier-app-opencode` sibling checkout:

```
src/amplifier_app_opencode/prereqs.py
  MIN_AGENT_VERSION = "X.Y.Z"     AGENT_PINNED_REF and AGENT_HARD_FLOOR follow it
```

Update the comment block above `MIN_AGENT_VERSION` in the same style as the
existing entries: name the specific capability that forces this floor, and keep
the historical notes about earlier floors. That comment is the record of why
each bump happened, and dropping it loses real information.

Then run its fast gate, which is lint and format only:

```bash
make check
```

Nothing in that repo asserts these constants. Its `tests/` tree is e2e-only and
needs a DTU, so it does not run at this stage, and the onboarding suite
deliberately does not track the floor anyway: the fake agent it installs reports
`99.0.0`, comfortably above any real value. Read the constants back instead, and
confirm they are the tag you actually pushed:

```bash
uv run python -c "from amplifier_app_opencode import prereqs as p; print(p.MIN_AGENT_VERSION, p.AGENT_PINNED_REF, p.AGENT_HARD_FLOOR)"
```

Lint accepts any string, so a typo here surfaces on a user's machine at install
time rather than in this gate. That read-back is the check.

Open a PR in `amplifier-app-opencode` with scope `chore(deps)` or `fix`,
explaining which engine capability forces the floor. Do not merge it.

Two hard constraints:

- Work only in the sibling submodule checkout. Never commit or push anything in
  the parent workspace repo.
- If you hit a permissions problem pushing, stop and tell the user. Do not fork
  and do not push to any repo other than the submodule's own remote.

`amplifier-app-paperclip` and `amplifier-app-nanoclaw` caret-pin
`amplifier-agent-ts`, so a minor or patch wrapper release propagates on their
next install or container rebuild with no action here. Raise them only for a
major wrapper bump, which breaks the caret.

---

## Report

Tell the user, plainly:

- Which tags were pushed, and the workflow result for each
- The GitHub Release URL for each `v*` and `wrapper-v*` tag
- Published versions confirmed on PyPI and npm, and explicitly that the PyPI
  engine upload is unexercised rather than verified
- Whether the opencode floor moved, with the reason either way, and the
  downstream PR URL if one was opened
- Anything that failed or could not be confirmed, named as unconfirmed rather
  than assumed fine

Do not report the steps of this session. State the outcome.

---

## Mechanics

**`RELEASING.md` is canonical.** Read it before Phase 1. Tag commands, publish
targets, and one-time PyPI setup are its content. This skill owns verification,
sequencing, and the downstream call.

**A pushed tag cannot be taken back.** Deleting it does not un-publish PyPI or
npm and does not un-run the workflow. The recovery for a bad release is a new
patch version, never a re-pointed tag.

**The publish workflows are not blind to the gate anymore, but they still do not run e2e.**
`ci.yml` now triggers on `v*` / `wrapper-v*` / `py-v*` tag pushes and runs the same `make
verify` targets (lint, types, codegen/version/wheel/parity/wrapper guards) that gated the PR,
and `publish-python.yml` runs `scripts/verify-wheel.py` immediately before the PyPI upload. So
a tag push is not entirely unguarded. What still does not run in CI, on a tag or otherwise, is
`tests/e2e/` or the evaluation suite: both need a DTU, which these runners do not have. The
gate for those was Phase 4 of the start skill, before the PR, and there is no second chance
for them here.

**`workflow_dispatch` on `publish-python.yml` skips the version check.** It
exists for recovery. Do not reach for it as a normal path, and never as a way
around a version mismatch the check caught correctly.

**Foundation is pinned to a git branch.** `amplifier-foundation` is a git
dependency on `main` in `pyproject.toml`, so two builds of the same engine tag
can resolve different foundation code. Worth stating in the report when
relevant; not something to fix mid-release.

**Never stage with `git add -A`.** Stage the paths you intend, in both repos.

**Poll, do not block.** Workflow runs take minutes. Poll at most every 60
seconds and never use a bash timeout above 120 seconds.

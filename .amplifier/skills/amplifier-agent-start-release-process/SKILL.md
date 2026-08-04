---
name: amplifier-agent-start-release-process
description: "Start an amplifier-agent release: scope which artifacts need cutting, sweep the diff for anything that does not belong in a public release, set versions, write the changelog, run the local gate, then open the release PR. This is the FIRST of two release skills. It stops at an open PR. After the PR merges, run amplifier-agent-finish-release-process to tag, publish, and update downstreams."
disable-model-invocation: true
user-invocable: true
---

# Start an amplifier-agent Release

## Release

$ARGUMENTS

If that is empty, do not ask what to release yet. Run Phase 0 first and propose
a scope from the evidence, then confirm it with the user.

## The Method

A release is a public statement. Everything in the diff becomes permanent,
readable by anyone, and attributed to the project. So the work is ordered:
establish what actually changed, remove what should never have been committed,
then version it, describe it, verify it, and propose it.

Two rules override everything below.

**The mechanics live in `RELEASING.md`, not here.** That file is canonical for
the artifact/tag/target table, the exact tag commands, and the one-time PyPI
trusted-publisher setup. Read it. Do not restate or re-derive its commands from
memory, and if this skill and `RELEASING.md` disagree about a mechanical fact,
`RELEASING.md` wins and you should say so.

**This skill never pushes a tag.** Tagging is what publishes. It happens only
after a human has merged the PR, and it happens in
`amplifier-agent-finish-release-process`.

Work through the phases in order. Use the todo tool to track them.

---

## Phase 0: Scope

You cannot clean or version a release you have not bounded. Establish the range
first.

```bash
git fetch origin --tags
git log --oneline origin/main -1
git tag --list 'v*'        | sort -V | tail -1
git tag --list 'wrapper-v*' | sort -V | tail -1
git tag --list 'py-v*'      | sort -V | tail -1
```

Then get the change set since the last release of each artifact:

```bash
git diff --stat "$(git tag --list 'v*' | sort -V | tail -1)"..origin/main
```

Read `docs/LAYERS_AND_RELEASES.md`. Its **release impact matrix** is the
authority on what a given change forces you to release. Do not infer this from
the file paths alone.

Map touched paths to artifacts:

```
src/amplifier_agent_lib/    src/amplifier_agent_cli/
src/amplifier_agent_http/   bundle/  pyproject.toml     -> engine, v*
wrappers/typescript/                                    -> TS SDK,  wrapper-v*
wrappers/python-py/                                     -> Py SDK,  py-v*
```

Three things change the answer:

**A protocol bump forces coordination.** If `PROTOCOL_VERSION` in
`src/amplifier_agent_lib/protocol/methods.py` moved, then per AGENTS.md
invariant #1 the engine, both wrappers, the conformance fixtures, and the
README protocol version must all have landed in one PR. Verify they did. If
they did not, `main` is already in a broken state and that is the problem to
fix, not the release to cut.

**A foundation pin change is an engine release.** `amplifier-foundation` is
pinned as a git dependency in `pyproject.toml`. Consuming a newer foundation
requires cutting an engine release; nothing else propagates it.

**`amplifier-agent-py` has never been released.** There are zero `py-v*` tags.
If this release includes one, say so explicitly: it is the first exercise of
that package's OIDC trusted-publisher handshake, and per `RELEASING.md` that
handshake can only be proven by a real tag-triggered run.

State the scope back to the user before proceeding: which artifacts, which
version each moves to, and why. Get agreement. A release nobody agreed the
shape of is not ready to have its version set.

---

## Phase 1: Sweep

This is the phase that justifies the skill existing. Everything in the range
becomes public. Read the actual diff, not the file list.

```bash
git diff "$(git tag --list 'v*' | sort -V | tail -1)"..origin/main
```

For a large range, walk it file by file rather than dumping it all at once.

### Files that should not be in the repo

AGENTS.md is explicit that design docs are transient working artifacts, not repo
content. The durable artifact is `docs/spec/`. Hunt for:

```
plan / PLAN / *-plan.md / implementation-plan.md / phase-*.md
.ai_working/  scratch/  notes/  tmp/  WIP*
runs/            eval output: provider keys, full prompts, host paths
*.log  *.tmp  *.bak  *.orig  *.rej  .DS_Store
```

If a plan file exists and the user still wants it, it moves outside the repo,
into `.ai_working/` or wherever they keep working notes. It does not ship.

### Comments and code that leak the process

The tell is a comment that only makes sense to someone who watched the work
happen. Grep the diff, then read the hits in context; most of these need
judgment, not a regex.

```bash
git diff "$(git tag --list 'v*' | sort -V | tail -1)"..origin/main \
  | grep -nE '^\+' \
  | grep -inE 'TODO|FIXME|XXX|HACK|phase [0-9]|per the plan|as discussed|step [0-9]+ of|for now|temporar|placeholder|remove (this|before)|/home/|/Users/|localhost:[0-9]|ngrok|session[_-]?id'
```

Remove or rewrite:

- References to plan phases, steps, or the sequence the work was done in
- "For now", "temporary", "will fix later" without an issue behind it
- Commented-out code left as a fallback
- Absolute host paths, personal directories, machine names
- Internal-only URLs, ticket ids, or session identifiers that resolve to nothing
  for an outside reader
- Anything resembling a key, token, or credential. If one is found, stop. It
  needs rotation, not deletion.

A `TODO` is not automatically wrong. A `TODO` that says `TODO(dan): fix after
the demo` is. Judge whether a stranger reading it a year from now learns
something useful or just learns how the sausage was made.

### Two invariant checks that are cheap and catch real breakage

**Bundle files must be in `force-include`** (AGENTS.md invariant #6). If the
diff adds anything under `src/amplifier_agent_lib/bundle/`, including
`bundle/skills/*/SKILL.md` and `bundle/modes/*.md`, confirm each new path is
listed in the `force-include` block in `pyproject.toml`. A miss here ships a
wheel whose first-run discovery silently comes up short. Nothing else catches
this.

**No new stdout writes on CLI paths** (AGENTS.md invariant #5). The CLI emits
exactly one JSON line on stdout. A bare `print(...)` added anywhere the CLI
exercises breaks every wrapper's parsing.

```bash
git diff "$(git tag --list 'v*' | sort -V | tail -1)"..origin/main -- src/ \
  | grep -nE '^\+.*\bprint\(' | grep -v 'file=sys.stderr'
```

### Report before you change

Show the user what you found and what you propose to do about each item. Do not
silently delete things from someone else's commits. Some of what looks like
scratch is deliberate.

---

## Phase 2: Versions

Check whether the bump already happened. The version in the manifest is the
*target of the next tag*, so it may already be ahead of the newest tag.

```bash
grep -m1 '^version' pyproject.toml
grep -m1 '"version"' wrappers/typescript/package.json
grep -m1 '^version' wrappers/python-py/pyproject.toml
```

Compare each against its tag namespace from Phase 0. If a manifest is already
ahead of its latest tag, the bump is done; confirm the number is still the right
one for what shipped and move on.

If a bump is needed, pick it by SemVer against the actual change set, not by
habit:

```
breaking wire, removed flag, changed envelope shape   -> major
new capability, new flag, new endpoint                 -> minor
fix only, no new surface                               -> patch
```

Edit the *correct* manifest. AGENTS.md calls the wrong-file mistake out by name,
and the publish workflows verify tag version against manifest version and hard
fail on mismatch, so a wrong edit here surfaces later as a failed publish:

```
engine   -> pyproject.toml                      [project] version
TS SDK   -> wrappers/typescript/package.json    "version"
Py SDK   -> wrappers/python-py/pyproject.toml   [project] version
```

Do not touch the root `package.json`. It is `amplifier-agent-client-ts`, the
pnpm workspace root manifest, and no workflow publishes it.

If `PROTOCOL_VERSION` moved, verify both wrappers' pinned `--protocol-version`
values, the `wrappers/conformance/` fixtures, `test_protocol_version_bump.py`,
and the protocol version stated in `README.md` all agree.

---

## Phase 3: Changelog

`CHANGELOG.md` is Keep a Changelog with SemVer. It is hand-written and the
existing entries are deliberately prose-heavy: they explain the mechanism and
the reason, not just the surface. Match that register. A one-line bullet in a
file full of paragraphs reads as an afterthought.

Move whatever sits under `## [Unreleased]` into a new version heading, then fill
the gaps from the diff. Leave `## [Unreleased]` in place and empty.

```
## [Unreleased]

## [X.Y.Z] — YYYY-MM-DD

### Added
### Changed
### Fixed
### Notes
```

Write for a user of the artifact, not a reader of the diff. Each entry should
answer: what can I now do, or what stopped being broken, and what do I have to
do to get it. If a change alters a default or requires action, that belongs
under `### Notes` and should say so plainly.

Do not describe internal refactors that no consumer can observe. Do not
reference PR numbers as the explanation; the generated GitHub Release notes
already carry those.

Confirm today's date rather than assuming it.

---

## Phase 4: Gate

Nothing downstream will catch what you miss here. The publish workflows build
and upload without running the test suite, so this local run is the gate.

Always:

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pyright src/
timeout 115 uv run pytest tests/ -q 2>&1 | tail -20; echo "EXIT_CHAIN_DONE"
```

If wrappers or the protocol changed, also:

```bash
cd wrappers/typescript  && bun install && bun run build && bun run test
cd wrappers/conformance && pnpm install && pnpm test
```

Run these from the right directory. There is no aggregator script, and running
engine tests from a wrapper directory produces a confusing pass.

Everything must be green before a PR opens. If something fails, fix it or stop
and report it. Do not open a release PR on a red tree and plan to fix it in the
PR.

---

## Phase 5: PR

Branch, commit, push, open.

```bash
git checkout -b chore/release-X.Y.Z origin/main
```

Name the branch for the artifact actually being released. A coordinated release
picks the broadest one, matching the commit scope convention.

Commit with conventional-commit scope. `chore(release)` is the documented scope
for version bumps. Stage explicitly; never stage with `-A`.

```
chore(release): cut amplifier-agent X.Y.Z
```

If the sweep in Phase 1 removed things, that is a separate concern from the
version bump and reads better as its own commit.

The PR body states facts and rationale only, never the steps of this session.
Cover:

- Which artifacts this releases and which tag each will get
- The user-visible change, in the same voice as the changelog entry
- Scope of impact: engine-only, wrapper-only, or coordinated cross-component
- Whether `PROTOCOL_VERSION` moved
- Whether `amplifier-app-opencode` needs a floor bump, and why or why not
  (Phase 6 decides this; state the conclusion here)
- That merging does not publish anything, and the tag push is the release

Open it with `gh pr create`. Do not merge it.

---

## Phase 6: Downstream call

Decide this now, while the change is fresh, and record the conclusion in the PR.
Do not act on it yet.

`amplifier-app-opencode` pins the engine in
`src/amplifier_app_opencode/prereqs.py`:

```
MIN_AGENT_VERSION   the floor it requires
AGENT_PINNED_REF    f"v{MIN_AGENT_VERSION}", the exact tag its launch-time
                    self-heal installs
AGENT_HARD_FLOOR    equal to the minimum
```

**The floor is a requirement statement, not a mirror of the latest release.** It
moves only when opencode actually depends on something the new engine
introduced. Read the comment block above `MIN_AGENT_VERSION`: every past bump
names the specific capability that forced it.

Bump it when this release changes something opencode relies on:

- The HTTP face (`src/amplifier_agent_http/`), which opencode drives via `serve`
- `GET /v1/models`, `/v1/skills`, `/v1/modes`, which its bridges read
- Host-config handling passed through `--host-config`
- `PROTOCOL_VERSION`
- The `run` stdout envelope or `serve` endpoint contract

Leave it alone for an engine-internal change opencode cannot observe. A floor
bump forces every opencode user through a reinstall, so an unnecessary one has a
real cost.

`amplifier-app-paperclip` and `amplifier-app-nanoclaw` caret-pin
`amplifier-agent-ts`, so a minor or patch wrapper release propagates without
action from us. Mention it only if this release is a major wrapper bump, which
breaks that caret.

---

## Handoff

Tell the user, plainly:

- The PR is open, with its URL
- Which artifacts it releases and which tags will be pushed after merge
- What the sweep removed
- The downstream call from Phase 6
- That **nothing is published until a tag is pushed**, and merging alone
  publishes nothing

Then stop and wait. When they have merged it, or they tell you to merge it, run
`amplifier-agent-finish-release-process`.

---

## Mechanics

**`RELEASING.md` is canonical.** Read it in Phase 0 and again before you write
the PR body. Tag namespaces, publish targets, and one-time setup are its
content, not this skill's. This skill owns judgment and sequencing.

**Tag namespaces are per-artifact and a wrong one triggers nothing.**

```
v*          engine     -> PyPI + GitHub Release
wrapper-v*  TS SDK     -> npm  + GitHub Release
py-v*       Py SDK     -> PyPI, no GitHub Release
```

**Git is the supported install channel, not PyPI.** `install.sh` and
`amplifier-agent update` install from git, and `install.sh` resolves "latest"
through the GitHub Releases API. That makes the GitHub Release the thing users
actually receive, and it is created by tag push, not by merge.

**Never stage with `git add -A`.** Stage the paths you intend. A release branch
in a submodule workspace picks up unrelated noise otherwise.

**Big output.** Pipe every test run through `tail -N` and add a sentinel echo so
a truncated tail is distinguishable from a killed process. Use `read_file` or
`sed -n '1,120p'` rather than dumping whole files, and walk a large diff file by
file.

**Do not push a tag from this skill under any circumstance.** If the user asks
you to finish the release now, they still merge first, and you still switch to
`amplifier-agent-finish-release-process`.

# E2E Testing

End-to-end tests that run the real `amplifier-agent` CLI and HTTP server inside an
isolated Digital Twin Universe (DTU) container, installed from a Gitea mirror of your
local working tree. They prove the shipped binary actually works from a realistic
install, not just that unit tests pass.

These tests are deliberately light. The baseline check is "the command ran without
erroring" (CLI exit 0 / HTTP 200) plus a small structural assertion on the output.
Output quality is out of scope here and belongs to evaluations.

This document describes the framework. The individual tests live under
`tests/e2e/suites/<feature>/` and are the source of truth for what is covered.

## Layout

```
tests/e2e/
  conftest.py                 # pytest fixtures (warm-DTU, in-DTU HTTP server)
  framework/                  # the machinery (stable; rarely touched)
    dtu.py                    # DTU + Gitea subprocess wrappers (exec, file-push, launch, ...)
    dtu_manager.py            # provision / refresh / teardown orchestration
    state.py                  # warm-DTU state file
    harness.py                # E2ECase / Step model + case runners
    assertions.py             # reusable checks (expect_set, expect_contains, ...)
    cli.py                    # the up/refresh/run/down entry point
    progress.py               # timestamped progress logging
    provisioning/             # how amplifier-agent is installed in the DTU
      install-amplifier-agent.sh
      host-config.json
  suites/                     # the tests (grows per feature)
    <feature>/
      cases.py                # E2ECase data
      test_<feature>.py       # thin pytest wrapper parametrizing the cases
      conftest.py             # optional: suite-local fixtures (e.g. seeding files into the DTU)
      fixtures/               # optional: static files pushed into the DTU at test time
```

Framework code is the reusable half; `suites/` is where features add tests. To test a
new feature, add a `suites/<feature>/` package. Nothing in `framework/` needs to change.

## Prerequisites

The harness shells out to two Amplifier CLIs, which need a container runtime. All of this
is host-side. `cli.py` runs a preflight on `up`/`run` and fails loud if anything is missing.

```bash
# uv (runs everything; this harness is never installed, always `uv run`)
curl -LsSf https://astral.sh/uv/install.sh | sh

# DTU CLI (Incus-backed environments)
uv tool install git+https://github.com/microsoft/amplifier-bundle-digital-twin-universe@main

# Gitea CLI (Docker-backed git mirror)
uv tool install git+https://github.com/microsoft/amplifier-bundle-gitea@main
```

Transitive runtimes:

- Incus (DTU container runtime). Verify `incus version`. One-time `incus admin init`.
- Docker (runs the Gitea container). Verify `docker info`. On WSL2, Docker Desktop with WSL
  integration.

`ANTHROPIC_API_KEY` must be set in your host env. It is passed through to the DTU and is
required by any test that runs a real model and by the HTTP server startup.

`GITHUB_TOKEN` must be set for the `github_copilot` suite (only that suite; everything else
runs without it). Set it with `export GITHUB_TOKEN=$(gh auth token)` and re-provision. The
value is snapshotted into the container at launch, so exporting it after a DTU is already
running has no effect. `dtu_manager.provision()` warns when any of these variables is
missing, because DTU's passthrough silently skips an unset value and the failure would
otherwise surface much later as an opaque provider auth error.

`GOOGLE_API_KEY` is optional and only the `gemini` suite uses it. Without it that suite
skips itself rather than failing, so a full run stays green for anyone who has no Google
credential. Set it to run the suite, and re-provision afterwards for the same
snapshot-at-launch reason. `GOOGLE_API_KEY` is the canonical variable even though the
Google GenAI SDK also accepts `GEMINI_API_KEY`: it takes precedence, and it is the one
`providers list` and `models list` consult.

`VLLM_BASE_URL` is optional and only the `vllm` suite uses it. It names a vLLM server you
are running yourself, so that suite skips unless you point it at one — and skips again if
the endpoint is set but not answering, since a server being down is a fact about your
machine rather than a defect in amplifier-agent.

```bash
export VLLM_BASE_URL=http://localhost:8007/v1   # required to run the suite
export VLLM_MODEL=your-org/your-model           # optional; see below
export VLLM_API_KEY=...                         # optional; local servers rarely need one
uv run python tests/e2e/framework/cli.py run vllm
```

Write the URL exactly as you would use it on the host: `localhost` is rewritten to the
container bridge gateway IP at launch, because inside the DTU `localhost` is the container
itself. That rewriting is why these three travel as DTU `--var` values rather than
`passthrough` entries — passthrough copies host values verbatim, and a verbatim
`localhost` would resolve to the wrong machine. The plumbing is
`dtu_manager._build_varmap()` plus `provisioning/setup-vllm-env.sh`, which also exempts
the vLLM host from the interception proxy so streaming is not buffered.

`VLLM_MODEL` is optional. When unset, the suite uses the first model id the server
advertises on `/v1/models`, which is the right answer for a single-model vLLM process.
Set it when your server hosts more than one. Your server must bind `0.0.0.0` rather than
`127.0.0.1`, or the container cannot reach it.

## Running

```bash
# from the amplifier-agent repo root
uv run python tests/e2e/framework/cli.py run       # push latest code -> fresh DTU -> run all suites
uv run python tests/e2e/framework/cli.py up        # same provisioning as run, without running tests
uv run python tests/e2e/framework/cli.py down      # destroy the DTU (leaves Gitea running)
```

`run` provisions a fresh DTU each time: it re-mirrors the working tree to Gitea, destroys any
existing `aa-e2e` container, launches a clean one (~90s), and runs the suite. So `run` alone
covers a cold start, a re-run after editing code, or CI, always against the latest code.

It rebuilds rather than updating in place because `uv tool install --reinstall` (what `refresh`
does) wipes amplifier-agent's lazily-installed provider module, breaking the HTTP server's model
enumeration (`serve` exits 2). A clean launch avoids that.

### Scoping and flags

```bash
uv run python tests/e2e/framework/cli.py run skills          # only suites/skills
uv run python tests/e2e/framework/cli.py run modes shadowing # two features
uv run python tests/e2e/framework/cli.py run --skip-setup    # fast re-run against the existing DTU
uv run python tests/e2e/framework/cli.py run --ephemeral     # tear the DTU down after the run
uv run python tests/e2e/framework/cli.py run -k resume       # pass args through to pytest
uv run python tests/e2e/framework/cli.py refresh             # fast in-place code-only update (no run)
```

Feature selection is directory-based: bare words matching a `suites/` subdirectory scope the run,
and the first `-` or path-like token ends feature parsing so the rest passes through to pytest. An
unknown feature name fails loud with the valid list.

`--skip-setup` is the fast inner loop once a DTU is warm. `refresh` does a code-only in-place
update for CLI iteration, but it leaves `serve` broken (provider-module note above), so HTTP tests
need a full `run` / `up`.

### Testing against extra repos

By default only `amplifier-agent` (plus a dirty `amplifier-core` / `amplifier-foundation`) reaches
the DTU. Anything else amplifier-agent depends on is fetched from real GitHub. `--repo` extends
that set: it mirrors an additional repo into Gitea *and* injects the matching `url_rewrites` rule,
so the DTU actually installs your version instead of the GitHub one. It is available on `up`, `run`
and `refresh`, and is repeatable.

```bash
# your local amplifier-bundle-skills checkout, working tree and all
uv run python tests/e2e/framework/cli.py run --repo amplifier-bundle-skills

# a specific branch, ignoring whatever is in the working tree
uv run python tests/e2e/framework/cli.py run --repo amplifier-bundle-skills@my-branch skills

# a repo you have no local checkout of, cloned from GitHub at a ref (default main)
uv run python tests/e2e/framework/cli.py up --repo amplifier-bundle-modes@v2

# non-microsoft owner, and more than one repo at once
uv run python tests/e2e/framework/cli.py run --repo someorg/their-bundle --repo amplifier-bundle-skills
```

The value is `[owner/]name[@ref]`. A bare name implies owner `microsoft`. The split is on the last
`@`, so a ref containing `/` works. `--repo` is consumed by the harness and is never forwarded to
pytest, so it can sit anywhere in the command line.

The set of repos is recorded in the warm-DTU state file, because rewrite rules are baked into the
container at launch. `run --skip-setup` with a different `--repo` set warns that the running DTU
does not match what you asked for and that a full `run` / `up` is needed. `refresh` with no `--repo`
re-mirrors exactly what the DTU was provisioned with.

A normal `uv run pytest` (without the harness) still stays green, but that is now a weaker
statement than it sounds: `tests/` contains only e2e trees, so a plain `pytest` run
self-skips every collected test when `amplifier-digital-twin` is absent or no warm DTU
exists, and exercises nothing. (`tests/windows/` is the other tree and self-skips the same
way; see [`E2E_TESTING_WINDOWS.md`](E2E_TESTING_WINDOWS.md).) It is not a substitute for `make check` (the fast local gate)
or for actually running this harness.

## How local code reaches the DTU

1. `framework/dtu.py` snapshots each in-scope repo's working tree (committed + staged + unstaged +
   untracked) into a throwaway clone and force-pushes it to a local Gitea mirror. Your source repo
   is never mutated (no add/commit/stash).
2. The DTU profile (`.amplifier/digital-twin-universe/profiles/e2e.yaml`) uses `url_rewrites` to
   redirect `github.com/microsoft/amplifier-agent` to that Gitea mirror, so
   `uv tool install --from git+...amplifier-agent` inside the DTU pulls your local tree.
3. Only `amplifier-agent` is mirrored by default. `amplifier-core` / `amplifier-foundation` are
   additionally snapshotted when their working trees are dirty. Mirroring alone changes nothing
   inside the DTU, so those two still resolve from GitHub until a rewrite rule exists for them; the
   harness prints a warning naming any repo in that state.
4. `--repo <name>[@<ref>]` adds a repo to both halves at once: it is mirrored *and* gets a rewrite
   rule injected into a temp copy of the profile at launch. The checked-in profile is never
   modified at runtime.

Where the content of an extra repo comes from:

```
local checkout in the workspace, no @ref  -> working-tree snapshot (same as amplifier-agent)
local checkout in the workspace, w/ @ref  -> that committed ref; the working tree is ignored
no local checkout                         -> cloned from GitHub at @ref (default main)
```

Two properties of this worth knowing:

- Pushing only ever targets the local Gitea container. Nothing is ever pushed to GitHub. GitHub is
  touched read-only, and only in the third case above, to clone or fetch a repo you have no local
  copy of.
- Your source repo is never mutated, in any case. For `@ref` on a local checkout the harness clones
  it into a temp dir first and resolves or fetches the ref inside that clone, so no git command
  ever runs against your checkout.

Whatever ref you pick lands in the mirror as `main`. That is deliberate: a bundle that references
`...@main` resolves to the mirror's `main`, so pointing a `--repo` at a PR branch tests that branch
without editing any `@main` reference.

Everything about *how* amplifier-agent is installed lives in
`framework/provisioning/install-amplifier-agent.sh` and `host-config.json`. Change the install
story there; the profile skeleton and `framework/dtu.py` do not change.

## The case model

A test case is data: an `E2ECase` (in `framework/harness.py`). Three kinds:

```python
# 1. cli: run a subcommand inside the DTU
E2ECase("name", "cli", ["run", "-y", "--config", CFG, "hello"], check=None)

# 2. http: hit the in-DTU HTTP server
E2ECase("name", "http", ("GET", "/v1/models"), check=expect_set({...}))

# 3. cli-multi: an ordered sequence of commands sharing one --session-id
E2ECase("name", "cli-multi", [], steps=(
    Step(["run", "-y", "--config", CFG, "--session-id", "{SID}", "seed a fact"]),
    Step(["run", "-y", "--config", CFG, "--session-id", "{SID}", "--resume", "recall it?"],
         check=expect_contains("fact")),
))

# cli with a launch directory: behavior that keys off the working dir (e.g. skill discovery,
# which reads the launch dir's .amplifier/skills/). Runs via `bash -lc 'cd <cwd> && amplifier-agent ...'`.
E2ECase("name", "cli", ["run", "-y", "--config", CFG, "!amplifier:skill foo"], cwd="/root/e2e/ws")
```

- `command` for `cli` is the argv after `amplifier-agent`; for `http` it is `(method, path)`.
- `cli-multi` runs each `Step` in order against a generated session id. The literal token `{SID}`
  is replaced with that id, so steps share state (used for session-resume tests).
- `cwd` (cli only) sets the launch directory inside the DTU. When set, the command runs via
  `bash -lc 'cd <cwd> && amplifier-agent ...'`. Use it when behavior depends on the working
  directory. `None` (default) runs from the exec default.
- `check` is an optional structural assertion on the parsed output (`None` = ran-clean only). The
  runner always enforces the baseline (CLI exit 0 / HTTP 200) *before* calling `check`, so a
  failure names the real cause.
- Reusable checks live in `framework/assertions.py`: `expect_set`, `expect_contains`, `names`,
  `expect_active_mode`, `expect_shadow`, `expect_no_shadows`.

## Adding a feature suite

1. Create `tests/e2e/suites/<feature>/` with `__init__.py`, `cases.py`, `test_<feature>.py`.
2. In `cases.py`, define lists of `E2ECase`.
3. In `test_<feature>.py`, parametrize over the cases and dispatch by kind:

```python
import pytest
from framework import harness
from suites.myfeature.cases import CASES

pytestmark = pytest.mark.dtu

@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_myfeature(case, dtu_id, server):
    if case.kind == "cli":
        harness.run_cli_case(dtu_id, case)
    elif case.kind == "cli-multi":
        harness.run_multi_case(dtu_id, case)
    else:
        harness.run_http_case(server["base_url"], server["token"], dtu_id, case)
```

Request only the fixtures you need: `dtu_id` for CLI tests, plus `server` for HTTP tests.

### Seeding files into the DTU

When a case needs files present inside the container (config, skill files, a workspace), push
them at test time with `dtu.push_file(dtu_id, local_src, dest)`, a thin wrapper over
`amplifier-digital-twin file-push` (parent dirs are created automatically; pass `recursive=True`
for directories). Keep the payloads as static files under `suites/<feature>/fixtures/` and do the
pushing from a suite-local `conftest.py` fixture that returns the in-DTU paths. Pair this with a
case's `cwd` when the behavior under test keys off the launch directory. See `suites/skills/` for a
worked example (seeds a skill into a launch-dir `.amplifier/skills/` and a configured location).

### The `coexistence` suite

Every other suite runs in a container where amplifier-app-cli was never installed, so
`~/.amplifier` is nearly empty. That is the easy case. `coexistence` tests the case a real
user is in: both applications installed side by side, with app-cli's live module clones
sitting in `~/.amplifier`.

What it proves is `docs/spec/foundation-cache-ownership.md`: amplifier-agent operates
entirely from `~/.amplifier-agent` and leaves app-cli's tree strictly alone. It records
`~/.amplifier` (path + size + mtime per file, plus the directory set), exercises
amplifier-agent hard, records it again, and asserts the two are identical. It then runs
app-cli again to confirm it still works, checks that `doctor`'s foundation-isolation guard
actually fires when isolation is broken, and confirms the two cache roots are separate
storage rather than two names for one directory.

```bash
uv run python tests/e2e/framework/cli.py run coexistence
```

It is slower than the other suites, and deliberately so. The suite installs
amplifier-app-cli inside the DTU on demand from its own `conftest.py` rather than from the
DTU profile's `setup_cmds`, so a normal `run` of anything else never pays for the download.
The first run in a container also has to prime app-cli's bundle cache, which clones its
whole module set. Progress is logged as it goes so a slow run is not mistaken for a hang.

All five tests run by default. The one covering remote skill clones used to skip, because
upstream `tool-skills` hardcoded `~/.amplifier/cache/skills` as its clone root regardless
of `AMPLIFIER_HOME`; microsoft/amplifier-bundle-skills#61 fixed that and has merged, so a
stock DTU now installs a `tool-skills` that honours `AMPLIFIER_HOME` and the test exercises
the real thing. It still probes for the fix rather than assuming it, so a DTU provisioned
from an older skills checkout skips with a clear reason instead of failing as if
amplifier-agent had regressed.

### Tests for features that do not exist yet

Mark them `@pytest.mark.xfail(reason="...", strict=True)`. The test still runs (it really hits the
DTU and must actually fail), so it stays honest; `strict=True` turns an unexpected pass into a hard
failure the moment the feature lands, telling you to remove the marker and treat it as a real test.
This keeps the suite green so genuine regressions stand out. See both outcomes with `pytest -rxX`.

## Faster startup with a pre-baked image

The slow part of `up` is installing the dependency tree into a bare base image. The profile's
`base.image` is a var (`AA_E2E_BASE_IMAGE`, stock `ubuntu:24.04`). Point it at a pre-baked Incus
image carrying `git` + the heavy deps to drop install to a thin amplifier-agent fetch. No test
changes required.

## Troubleshooting

- `git executable not found` during install: the base image is bare; the install script
  apt-installs `git`. A pre-baked image would carry it.
- Tests skip with "no warm DTU": run `... cli.py up` first, or just use `run` (it auto-provisions).
- Inspect the live DTU directly:
  `amplifier-digital-twin exec aa-e2e -- amplifier-agent --version`,
  `amplifier-digital-twin check-readiness aa-e2e`.
- A stale warm-DTU pointer (instance destroyed out of band) is detected via `check-readiness` and
  triggers a fresh `up`.
- Never destroy other DTUs: only `aa-e2e` (and the `aa-e2e` Gitea env) belong to this harness.

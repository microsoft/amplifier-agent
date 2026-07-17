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
    dtu.py                    # DTU + Gitea subprocess wrappers
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
uv run python tests/e2e/framework/cli.py run run modes       # two features
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

A normal `uv run pytest` (without the harness) stays green: the e2e tests self-skip when
`amplifier-digital-twin` is absent or no warm DTU exists.

## How local code reaches the DTU

1. `framework/dtu.py` snapshots each in-scope repo's working tree (committed + staged + unstaged +
   untracked) into a throwaway clone and force-pushes it to a local Gitea mirror. Your source repo
   is never mutated (no add/commit/stash).
2. The DTU profile (`.amplifier/digital-twin-universe/profiles/e2e.yaml`) uses `url_rewrites` to
   redirect `github.com/microsoft/amplifier-agent` to that Gitea mirror, so
   `uv tool install --from git+...amplifier-agent` inside the DTU pulls your local tree.
3. Only `amplifier-agent` is mirrored by default. `amplifier-core` / `amplifier-foundation` are
   additionally snapshotted when their working trees are dirty (add matching `url_rewrites` rules
   to extend redirection to them).

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
```

- `command` for `cli` is the argv after `amplifier-agent`; for `http` it is `(method, path)`.
- `cli-multi` runs each `Step` in order against a generated session id. The literal token `{SID}`
  is replaced with that id, so steps share state (used for session-resume tests).
- `check` is an optional structural assertion on the parsed output (`None` = ran-clean only). The
  runner always enforces the baseline (CLI exit 0 / HTTP 200) *before* calling `check`, so a
  failure names the real cause.
- Reusable checks live in `framework/assertions.py`: `expect_set`, `expect_contains`, `names`.

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

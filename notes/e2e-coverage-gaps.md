# E2E coverage gaps

Queue of behavior that lost its only coverage when the unit test suite was
deleted. Each entry is a candidate e2e suite, not a bug.

This file exists so the loss is explicit rather than silent. Work it with
`/amplifier-agent-new-feature`, one suite at a time, highest risk first.

## Context

The repo previously carried ~920 in-process tests across `tests/*.py`,
`tests/cli/`, `tests/http/`, `tests/config/`, `tests/integration/`, and
`tests/bundle/`. They were deleted because they tested implementation rather
than contract, and because their existence made `pytest tests/` mean two
different things depending on whether a DTU happened to be warm.

What survived that deletion:

```
tests/e2e/              8 suites, 56 cases, the contract
scripts/verify-*        release and contract guards, extracted not deleted
.amplifier/evaluation/  8 capability tasks, probabilistic behavior
[tool.ruff.lint] T20    stdout discipline, formerly a test
```

## Covered today

Collected test counts, not `E2ECase` counts. The two differ because suites
parametrize. Measured with `uv run pytest tests/e2e/suites/<name> --collect-only -q`.

```
modes            tests/e2e/suites/modes/           14
skills           tests/e2e/suites/skills/          13
github_copilot   tests/e2e/suites/github_copilot/  11   needs GITHUB_TOKEN
shadowing        tests/e2e/suites/shadowing/        6
streaming        tests/e2e/suites/streaming/        4
launch_dir       tests/e2e/suites/launch_dir/       3
raw_capture      tests/e2e/suites/raw_capture/      3
run              tests/e2e/suites/run/              2
                                                   --
                                                   56
```

45 of those run without `GITHUB_TOKEN`. All 45 verified green in a DTU on
2026-08-03.

## Not covered by anything

Ordered by how badly a silent regression would hurt a user.

```
session persistence and resume
  was: test_persistence.py, test_session_store.py, test_resume_continuity.py,
       test_incremental_save.py, test_transcript_repair.py
  why it matters: a user losing conversation history has no workaround.
                  Resume is also the most stateful path in the product.
  candidate: tests/e2e/suites/persistence/ - write a turn, restart, resume,
             assert the prior turn is in context

workspace isolation
  was: test_persistence_workspaces.py, test_session_store_per_workspace.py,
       test_session_store_cross_workspace_load.py, test_runtime_workspace.py,
       test_runtime_fresh_workspace.py, test_spawn_workspace_propagation.py
  why it matters: cross-workspace leakage is a correctness AND privacy bug.
  candidate: tests/e2e/suites/workspaces/ - two workspaces, assert sessions
             do not bleed across

XDG / config migration
  was: test_xdg_migration.py, test_migration.py, test_runtime_migration_wired.py
  why it matters: runs once, on upgrade, on a real user's machine, with their
                  only copy of their data. Failure is unrecoverable and
                  invisible until it is too late.
  candidate: tests/e2e/suites/migration/ - provision a DTU with an old-layout
             home, upgrade, assert data survived

subagent spawn
  was: test_spawn.py, test_spawn_capability_inheritance.py,
       cli/test_delegation_e2e.py
  why it matters: delegation is a headline capability and the inheritance
                  rules are subtle.
  candidate: tests/e2e/suites/spawn/ - delegate, assert the child ran with the
             expected tool set and the result came back

runtime config merge
  was: test_runtime_config_merge.py, test_runtime_initialize_cwd.py,
       test_runtime_audit_path.py, test_runtime_hook_mount.py
  why it matters: precedence bugs are quiet and produce wrong behavior rather
                  than errors.

MCP threading
  was: test_runtime_mcp_threading.py
  why it matters: no e2e suite touches MCP at all today.

approval provider wiring
  was: test_wire_approval_provider.py
  why it matters: approvals are a safety surface.

wrapper hang detection over a long real turn
  was: wrappers/typescript/test/timeout-longwindow-integration.test.ts
  why it was deleted: it spawned a mock engine that slept 12 real seconds,
       twice, which was 24s of the 25s TypeScript suite and ~40% of total CI
       wall clock. Its three cases are already covered faster and better by
       session-subprocess.test.ts, which uses 300ms observation windows:
         (k) timeoutMs: 0         -> no engine_hung   (same regression guard)
         (l) timeoutMs: undefined -> no engine_hung   (no silent default)
         (e) timeoutMs: 250       -> engine_hung fires
         (j) timeoutMs: 150       -> engine_hung fires
       The 12s sleep also never tested what its comment claimed: the silent
       default it guarded against was 10 minutes (session.ts DEFAULT_TIMEOUT_MS),
       which a 12 second window cannot detect.
  what is genuinely uncovered now: the same contract over a REAL long-running
       agent turn through the public spawnAgent() API, rather than a mock engine
       through SessionHandle. That is an e2e concern, not a unit one.
  candidate: an e2e case that runs a genuinely slow turn and asserts it
       completes without a spurious hang error. See ISSUE-002 in ISSUES.md,
       which proposes progress-based stuck detection and will need this anyway.

HTTP surface
  was: tests/http/ (80 tests, FastAPI TestClient)
  note: tests/e2e/ does start a real server on :9099, so this is partially
        covered. Audit before writing anything new.
```

## Deliberately not backfilled

Anything that only ever asserted internal structure. Examples: import
graph shape, dataclass field presence, error-message wording. If a behavior
cannot be observed through the CLI, the HTTP API, or an evaluation, it is not
part of the contract and does not get a test.

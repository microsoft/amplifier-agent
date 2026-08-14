# deep-swe benchmark harness

Runs Amplifier agents against [deep-swe](https://github.com/datacurve-ai/deep-swe),
a 113-task agentic SWE benchmark. Each task is a Harbor-format directory pinned to
a prebuilt Docker image. The `pier` CLI (a Harbor fork) owns the environment,
verification and grading. This directory supplies only the agents plus a thin runner.

```
reward   binary  1 only if ALL fail-to-pass AND ALL pass-to-pass tests pass
partial  0..1    fraction of tests passing -- this is the useful dev signal
```

Work is graded as `git diff <base_commit> HEAD` in `/app`, so **uncommitted work
scores zero**. deep-swe's own `instruction.md` already tells the agent to commit;
we pass it through verbatim and add nothing. As a backstop the harness runs a
fallback commit afterward, which announces itself in the trial log:

```
PIER_AMPLIFIER_FALLBACK_COMMIT: committed          <- dev signal only, NOT comparable
PIER_AMPLIFIER_FALLBACK_COMMIT: nothing-to-commit  <- agent committed its own work
```

Check that marker before quoting any number as leaderboard-comparable -- no
leaderboard run gets a fallback commit.

## Setup

Docker running, `ANTHROPIC_API_KEY` exported, and:

```bash
uv tool install --force git+https://github.com/datacurve-ai/pier@0daf53d3599e58c4506cf0bcff5e12c77dc282d2

cd .amplifier/evaluation/deep-swe
uv pip install --python "$(dirname "$(readlink -f "$(which pier)")")/python" -e .
```

The second command installs the agents into pier's venv so `--agent-import-path`
can resolve them.

Install pier **from git, not PyPI**. The PyPI 0.3.0 build has no
`[[verifier.collect]]` hook support, so `model.patch` is never produced and every
task silently scores 0. Both builds self-report `0.3.0`, so the version string
proves nothing; `run.py` probes for the feature and refuses to run on the PyPI build.

## Running

```bash
python run.py --list-agents
python run.py --list-tasks

# one agent, one task
python run.py --agents amplifier-agent --tasks textual-richlog-follow-state

# the same deterministic 15 tasks for three arms
python run.py --agents amplifier-foundation,amplifier-agent,opencode-vanilla \
              -n 15 --seed 1234

# print the pier commands, resolved task list and pins without running
python run.py --dry-run --agents amplifier-agent -n 15 --seed 1234
```

An explicit `--tasks` or `-n` is required: a full 113-task sweep is expensive in
both API spend and wall-clock time, and per-task cost varies widely -- a task
solved early costs far less than one that runs to its timeout.

Task data is cloned at runtime into `~/.cache/deep-swe/<sha>/` (`--tasks-dir` or
`DEEP_SWE_CACHE_DIR`). It is never copied into this repo.

```
-n N --seed S               deterministic sample; same seed, same subset, any machine
--n-concurrent N            trials pier runs in parallel WITHIN one agent's job
--agent-timeout-multiplier  multiplier on each task's timeout_sec (default 1.5)
--local-source PATH         install a local checkout instead of the pinned ref
--no-pin                    install from moving branches instead of resolved SHAs
--pier-arg=--foo            forward a raw arg to `pier run` (repeatable)
```

Agents run sequentially, one `pier run` each. To run arms in parallel, launch one
`run.py` per arm against a shared `--jobs-dir`.

`--n-concurrent` and `--agent-timeout-multiplier` are first-class flags; passing
them through `--pier-arg` duplicates them.

## What keeps the numbers valid

**The instruction is passed through untouched.** Do not append scoring hints or
commit reminders. Every leaderboard score came from `mini-swe-agent`, which
augments nothing; any addition makes our numbers incomparable in our own favor.

**Every agent runs the identical task list.** Selection is resolved once on the
host and passed as explicit `--include-task-name` args. pier's own `--n-tasks` /
`--sample-seed` are never used -- this runner invokes pier once per agent in a
separate process, so delegating the sampling would let each arm draw its own
subset while every summary still lined them up side by side.

**Moving refs are pinned to SHAs at run start** (`--no-pin` opts out). A multi-task
run takes hours; installing from `@main` lets upstream move mid-run so tasks get
graded against different code. The pins, task list, seed, deep-swe SHA and pier
SHA land in `<run-ts>/run-manifest.json`.

**Timeouts get 1.5x headroom by default.** A timed-out trial scores 0
indistinguishably from a capability failure.

**Only one duration is reported:** `agent_run_s`, measured with `time.monotonic()`
around the agent command. Wall clocks can step backward under NTP correction --
observed here by enough to make pier report a trial finishing before the timeout
that killed it -- so no pier-derived duration is read at all.

**A $0 is never reported as a free run.** If no cost figure could be produced, the
field is the string `not_available`.

**opencode's cost is recomputed, not read.** It prices from a models.dev card that
disagrees with the reference card on cache rates and ignores the `cost.cache`
override in `opencode.json`, so `metrics.parse_opencode_db` takes only token
counts and applies `MODEL_RATES_PER_M` (mirroring `_RATES` in
`amplifier-module-provider-anthropic/_cost.py`). If Anthropic changes pricing,
both tables must move together. opencode's own figure is kept in `notes`.

## Output

One timestamped directory per invocation under `<workspace>/evaluation_results/`
(`DEEP_SWE_RESULTS_DIR`), one job dir per agent. `run.py` prints a per-trial
summary at the end.

```
<run-ts>/run-manifest.json      task list, seed, resolved pins, deep-swe + pier SHAs
<run-ts>/deepswe-<agent>/<task>__<id>/
  verifier/reward.json     reward, f2p_passed/total, p2p, partial
  result.json              pier timings + agent_result (tokens, cost, metadata)
  artifacts/model.patch    the graded submission
  agent/agent.log          agent stdout/stderr
  agent/metrics.json       token/cost detail, dedup notes, timing
  agent/sessions/...       the agent's session tree, pulled per SESSION_DIRS
```

## Agents

```
amplifier-agent           amplifier-agent CLI driven directly
amplifier-foundation      full amplifier stack (`amplifier run`) with the anchors bundle
opencode-amplifier-agent  OpenCode frontend backed by amplifier-agent
opencode-vanilla          stock OpenCode talking straight to Anthropic (control arm)
```

`--local-source` replaces the pinned ref with a filtered upload of a local
checkout: `amplifier-agent` and `opencode-amplifier-agent` -> amplifier-agent,
`amplifier-foundation` -> amplifier, `opencode-vanilla` -> unsupported. The agent
logs `INSTALLED VERSION: '...'` so the trial log proves which build ran. A missing
path is a hard error, never a silent fallback to the git ref.

Per-arm gotchas, each learned the hard way:

**amplifier-agent** writes nothing to disk unless `--session-id` is passed --
without it the CLI mints a telemetry-only `ephemeral-<hex>` id and silently skips
`transcript.jsonl`, `metadata.json` and `audits/`. The adapter passes
`--session-id deepswe-trial`. Do NOT relocate `AMPLIFIER_AGENT_HOME` to get the
session onto the bind mount: the context-intelligence hook's `base_path` is a
literal that does not expand it, so relocating splits the trajectory in two and
loses the token data.

**amplifier-foundation** runs `amplifier run --bundle <anchors> --mode single`.
`--bundle` is explicit so a stray `/app/.amplifier/settings.yaml` in a task repo
cannot swap the stack out from under the benchmark. The anchors bundle composes
both logging hooks, so every LLM call is written to disk twice in two envelope
shapes; `parse_events` de-duplicates on the provider response id. Do not "fix"
the duplication by narrowing the glob. Do not add anchors with
`amplifier bundle add --app` -- it is already the default primary bundle, so
`--app` composes it on top of itself.

**opencode-vanilla** records usage in SQLite, not events.jsonl. The whole data dir
is pulled rather than `opencode.db` alone: opencode runs SQLite in WAL mode and
the newest writes live in the `opencode.db-wal` sidecar, so pulling the db by
itself yields a stale database that silently under-reports.

**opencode-amplifier-agent** declares no `SESSION_DIRS`, so it collects no
trajectory and reports no token or cost data.

## Raw LLM payloads (amplifier-agent)

Off by default -- it multiplies the size of `events.jsonl`, and a full-budget run
makes well over a hundred requests.

```bash
python run.py --agents amplifier-agent --tasks <id> --pier-arg=--ak --pier-arg=raw_llm_payloads=true
```

Sets `{"debug": {"rawLlmPayloads": true}}` in the container host-config, which
attaches full `data.raw` (messages, system, tools, model, untruncated) to every
`llm:request` and `llm:response`. It must be a real JSON boolean; a string is
rejected deliberately, because `"false"` is truthy and would silently enable
capture. The trial log records which mode was used.

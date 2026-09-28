# Mechanics

Profiles, credentials, and trial evidence are in `evaluations/README.md`.

## Running one task

Copy `evaluations/runs/regression-checkout.yaml` outside the repo, set `name`,
and narrow `tasks.include`. Run detached; a bash tool timeout kills the process
group and leaves universes behind.

```bash
rm -f /tmp/eval.log && (cd evaluations && setsid bash -c \
  'uv run amplifier-agent-evaluations run /tmp/<name>.yaml > /tmp/eval.log 2>&1' \
  </dev/null >/dev/null 2>&1 &)
```

## Polling

Every 60 to 115 seconds, never above 120. Trial `state.json` moves before the
log does; `summary.json` is written last. If the log stops advancing across
several polls, find out why instead of waiting.

```bash
sleep 90; tail -n 10 /tmp/eval.log; ls -t evaluations/output | head -1; \
  pgrep -f "amplifier-agent-evaluations[ ]run" >/dev/null && echo RUNNING || echo DONE
```

The bracket keeps `pgrep` and `pkill` from matching their own command line. Kill
by PID otherwise.

## Cleanup

An interrupted run can leave universes. Destroy only ones you created:

```bash
(cd evaluations && uv run dtu-lite list)
(cd evaluations && uv run dtu-lite destroy --id <id>)
```

## Hygiene

- `pnpm test` refuses a missing or stale `runtime/linux-x64/`;
  `scripts/check.py --runtime` rebuilds it.
- `evaluations/output/` holds prompts, responses, and host paths. Never commit it.

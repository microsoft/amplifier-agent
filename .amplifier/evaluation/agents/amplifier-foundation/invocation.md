# Driving amplifier-foundation

This agent is the Amplifier CLI with the anchors bundle composed. The harness
installed it into this Digital Twin Universe, so `amplifier` is on PATH once you
export the uv tool dir.

Treat the scenario as a real, MULTI-TURN conversation with ONE agent session.
The first turn starts a session; every follow-up must resume that SAME session
with `--resume <session_id>` so the agent keeps its context. The agent often
finishes a turn without producing the deliverable -- it may ask you a question,
offer options without picking one, or pause for direction. That is normal: answer
as the user would and let it continue, exactly like a real person would. Only
conclude once the deliverable exists or the agent has genuinely failed.

## Each turn runs in the background (it can take many minutes)

A turn takes several minutes and may delegate to sub-agents. Do NOT run
`amplifier run` as a single blocking command -- it will hit a command timeout.
Launch every turn (first turn AND every resume) in the BACKGROUND with a
completion sentinel, then POLL.

Turn pattern (this is the command you wrap with the DTU exec pattern):

```
cd /workspace && rm -f eval-run.out eval-run.done && \
nohup bash -lc 'PATH=/root/.local/bin:$PATH <AMPLIFIER_CMD> > /workspace/eval-run.out 2>&1; echo "EXIT:$?" > /workspace/eval-run.done' >/dev/null 2>&1 &
echo launched
```

- First turn: `<AMPLIFIER_CMD>` = `amplifier run "<the user's message>"`
- Every follow-up: `<AMPLIFIER_CMD>` = `amplifier run --resume <SID> "<your reply>"`

If a message contains a double quote, write it to `/workspace/eval-prompt.txt`
first and use `amplifier run "$(cat /workspace/eval-prompt.txt)"` (or the
`--resume <SID> "$(cat ...)"` form). This applies to the first turn and resumes.

## Step 1 -- first turn, then capture the session id

Launch the first turn (`amplifier run "<the user's message>"`) with the pattern
above. After it completes (see Step 2), capture the session id -- you need it for
every follow-up:

```
grep -oE 'Session ID: [0-9a-fA-F-]+' /workspace/eval-run.out | head -1
```

Remember that id (call it `<SID>`). It stays the same for the whole conversation.

## Step 2 -- poll until the sentinel appears

Repeat this every ~30s, for as long as it takes (be patient -- up to this task's
timeout; delegation runs are slow):

```
if [ -f /workspace/eval-run.done ]; then echo "COMPLETE $(cat /workspace/eval-run.done)"; else echo RUNNING; tail -c 300 /workspace/eval-run.out 2>/dev/null; fi
```

Do NOT act while it still prints `RUNNING`. Keep polling. A run spawning a
sub-session is expected, not an error.

## Step 3 -- check the deliverable, and continue the conversation if needed

Once you see `COMPLETE`, read what the agent produced:

```
cat /workspace/eval-run.out; echo '--- answer.txt ---'; cat /workspace/answer.txt 2>/dev/null
```

Then decide:

- **Deliverable produced** (the task's answer file / edits exist) and the
  sentinel shows `EXIT:0` -> conclude `verdict=success`.

- **Crashed** (sentinel shows a non-zero exit, or the run errored out) ->
  conclude `verdict=failure`.

- **Finished cleanly (`EXIT:0`) but no deliverable, because it asked you
  something / offered options / paused for direction** -> this is NOT a failure
  yet. Respond as the user and continue the SAME session:
  1. Compose a SHORT, in-character reply (you are the user from the scenario).
     Answer only what you, as that user, actually know. If it asks you to confirm
     or supply something you do NOT know (for example, which of several sources is
     authoritative), say so plainly and tell it to use its best judgment -- to
     work it out from what is on the host and from any notes from your earlier
     sessions on this box -- and to go ahead and write its best answer to the
     requested file. Do NOT invent facts or feed it an answer you don't have; just
     remove the "I'm waiting on you" blocker and tell it to commit.
  2. Resume the session with that reply as a new turn:
     `amplifier run --resume <SID> "<your reply>"`, using the same
     background + sentinel + poll pattern (Steps titled above), then re-check the
     deliverable.
  3. Repeat this at most ~3 follow-ups. If, after you have clearly told it to use
     its best judgment and commit, it still refuses to produce the deliverable,
     THEN conclude `verdict=failure` and say it would not commit an answer.

Put a short note in your summary about what was produced and how many turns it
took. Do NOT judge correctness yourself -- the grader does that.

## Model pinning

Opus 4.8 is pinned at install time via the provider's `default_model:
claude-opus-4-8` plus the `opus48` routing matrix in
`/root/.amplifier/settings.yaml`. That is the single source of truth for which
model runs, so `amplifier run` needs no model flag -- just invoke it as shown.

## Notes

- The agent reads plain files with its filesystem tool, but a PDF is binary.
  For PDF tasks the agent extracts text with `pdftotext` (poppler-utils) via its
  bash tool; the task environment provides it.
- When a task involves fetching something served on this host, the agent must use
  the host's primary LAN IP (e.g. `hostname -I`), not localhost / 127.0.0.1. The
  task instructions already spell this out; just let the agent follow them.

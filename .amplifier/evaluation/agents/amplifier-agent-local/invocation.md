The agent is the `amplifier-agent` CLI, driven DIRECTLY (not through opencode).
It was installed into this Digital Twin Universe by the harness from the local
working tree, so it is already on PATH once you export the tool directory.

## Setup on every command

The DTU engine runs every exec command in a login shell, so `amplifier-agent`
(installed to `/root/.local/bin`) is already on PATH; you do not need to export
it yourself. Always run from `/workspace` so deliverables land in a predictable
place. Always pass `-y` (auto-approve, headless: there is no human at a TTY to
answer approval prompts) and `--config /root/host-config.json` (headless
approval, provider/model pin, and the extra skills source location).

Single-turn template:

    cd /workspace && amplifier-agent run -y --config /root/host-config.json --session-id <SID> "<message>"

The CLI prints the agent's final response to stdout and exits. Capture the
response from stdout.

## Invoking a SKILL

A skill is invoked with a sigil AS the message. The text after the skill name is
passed verbatim to the skill as `$ARGUMENTS`:

    cd /workspace && amplifier-agent run -y --config /root/host-config.json "!amplifier:skill <name> <optional args>"

For example, `"!amplifier:skill code-review"` runs the code-review skill, and
`"!amplifier:skill echo-args ZULU-PHRASE-4291"` passes `ZULU-PHRASE-4291` through
as `$ARGUMENTS`.

## Invoking a MODE

A mode is invoked with the `--mode` FLAG (not a sigil). The active mode is NOT
sticky: it is set per turn. To keep a mode active across a `--resume` you MUST
pass `--mode` again on that turn. OMITTING `--mode` on a resumed turn DISABLES
the mode; there is no separate "clear" verb.

Multi-turn template (reuse the SAME `--session-id`; add `--resume` on every turn
after the first):

    # turn 1 (mode active)
    cd /workspace && amplifier-agent run -y --config /root/host-config.json --session-id S --mode <mode> "<msg>"

    # turn 2 (mode persists: --mode re-passed)
    cd /workspace && amplifier-agent run -y --config /root/host-config.json --session-id S --resume --mode <mode> "<msg>"

    # turn 3 (mode DISABLED: --mode omitted)
    cd /workspace && amplifier-agent run -y --config /root/host-config.json --session-id S --resume "<msg>"

## Following the scenario exactly

Each task scenario specifies the EXACT flags to pass on each turn (which
`--session-id`, whether `--resume` is present, whether `--mode` is present).
Follow them precisely: the mode tasks are measuring the per-turn `--mode`
contract, so an extra or missing `--mode` changes what is being tested.

## Notes

- Always run from `/workspace`.
- The agent writes its session data (events, tool calls) under
  `/root/.amplifier-agent/state/workspaces/`.

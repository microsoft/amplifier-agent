The agent is the `amplifier-opencode` CLI: opencode driven by the
amplifier-agent runtime against Anthropic models. It runs as a one-shot per
turn. It was installed into this Digital Twin Universe by the harness, so it is
already on PATH once you export the two tool directories.

## One-shot per message

Run each turn with a single command. Note the PATH export (opencode and uv
tools live in `$HOME/.opencode/bin` and `$HOME/.local/bin`) and the model pin:

    export PATH="$HOME/.opencode/bin:$HOME/.local/bin:$PATH"; cd /workspace && amplifier-opencode launch -- run --auto --model amplifier/claude-opus-4-8 "<your message>"

The CLI prints the agent's final response to stdout and exits. Capture the
response from stdout.

## Auto-approve permissions (required for headless runs)

Always pass opencode's `--auto` flag (as shown above). There is no human at a
TTY to answer permission prompts, so without `--auto` opencode auto-REJECTS any
request that would otherwise ask (notably `external_directory` reads outside
`/workspace`, e.g. the agent's own session store under
`/root/.amplifier-agent/state/workspaces/...`). `--auto` approves any request
that is not explicitly denied; explicit `deny` rules still apply. Keep `--auto`
on every invocation.

## Model pinning

opus-4-8 is pinned via opencode's `--model amplifier/claude-opus-4-8` flag. The
amplifier adapter writes no default model, so the flag is the single source of
truth for which model runs. Keep it on every invocation.

## Fresh session per turn

Each `amplifier-opencode launch -- run` call is a fresh one-shot session. It
does NOT persist conversation context between separate invocations. For a
scenario that needs continuity (e.g. "now refine what you just did"), include
the necessary prior context in the message text itself.

## Notes

- Always run from `/workspace` so the agent's deliverables and session data
  land in a predictable place.
- Native PDF `read` does not work through this stack. The agent extracts PDF
  text via `pdftotext` (poppler-utils), which the task environment provides.
- opencode's `webfetch` tool blocks loopback URLs (127.0.0.1 / localhost) but
  accepts the container's own LAN IP. When a task involves fetching something
  served on this host, the agent must use the host's primary IPv4 address.

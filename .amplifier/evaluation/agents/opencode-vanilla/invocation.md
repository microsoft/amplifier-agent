The agent is vanilla `opencode` talking directly to the Anthropic API (no
amplifier-agent). It was installed into this Digital Twin Universe by the
harness, so it is already on PATH once you export its bin directory. It runs as
a one-shot per turn.

## One-shot per message

Run each turn with a single command. Note the PATH export (opencode lives in
`$HOME/.opencode/bin`), the model pin, and `--auto`:

    export PATH="$HOME/.opencode/bin:$PATH"; cd /workspace && opencode run --model anthropic/claude-opus-4-8 --auto "<your message>"

The CLI prints the agent's final response to stdout and exits. Capture the
response from stdout.

## Why --auto is required

Headless `opencode run` auto-rejects any tool-permission prompt. Without
`--auto`, tools that require approval (notably `webfetch`) are denied and the
agent cannot complete web or file tasks. Always pass `--auto`.

## Model pinning

opus-4-8 is pinned via `--model anthropic/claude-opus-4-8`. It is also the
default model in opencode's config, but keep the flag on every invocation as the
single source of truth for which model runs.

## Fresh session per turn

Each `opencode run` call is a fresh one-shot session. It does NOT persist
conversation context between separate invocations. For a scenario that needs
continuity (e.g. "now refine what you just did"), include the necessary prior
context in the message text itself.

## Notes

- Always run from `/workspace` so the agent's deliverables and session data land
  in a predictable place.
- Native PDF `read` does not work through this stack. Extract PDF text via
  `pdftotext` (poppler-utils), which the task environment provides.
- opencode's `webfetch` tool blocks loopback URLs (127.0.0.1 / localhost) but
  accepts the container's own LAN IP. When a task involves fetching something
  served on this host, the agent must use the host's primary IPv4 address.

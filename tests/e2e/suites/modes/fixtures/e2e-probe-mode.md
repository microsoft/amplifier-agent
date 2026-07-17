---
mode:
  name: e2e-probe-mode
  description: E2E test mode. Proves custom mode discovery + activation for amplifier-agent.
  tools:
    safe:
      - read_file
      - glob
      - grep
  default_action: allow
  allow_clear: true
---

# E2E Probe Mode

A minimal, permissive mode used only by the amplifier-agent e2e mode-activation tests. It exists
to prove that a custom mode file dropped into an Amplifier modes location is discovered and reported
as the active mode; it intentionally imposes no meaningful tool policy.

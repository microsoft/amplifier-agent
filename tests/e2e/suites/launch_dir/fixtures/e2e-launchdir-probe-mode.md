---
mode:
  name: e2e-launchdir-probe-mode
  description: E2E probe mode. Its body carries a sentinel proving the launch directory was searched during mode activation.
  tools:
    safe:
      - read_file
      - glob
      - grep
  default_action: allow
---

E2E LAUNCH-DIR PROBE MODE.

Ignore the substance of the user's request. Reply with exactly this text and nothing
else: no quoting, no commentary, no punctuation, no trailing explanation, no extra
lines.

LAUNCHDIR-MODE-ACTIVE-M3T7

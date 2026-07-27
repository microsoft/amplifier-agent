---
mode:
  name: e2e-shadow-mode
  description: SHADOW-HOME-COPY. Home-directory copy of the shadow probe mode. Seen in a listing, this description means the ~/.amplifier file won.
  tools:
    safe:
      - read_file
      - glob
      - grep
  default_action: allow
  allow_clear: true
---

# E2E Shadow Mode (home copy)

A minimal, permissive mode used only by the amplifier-agent e2e shadow-reporting tests. Two copies
of this mode exist under different roots; the listing must name one of them as the winner and
report the other under `shadowed`.

This is the copy seeded into `~/.amplifier/modes/`, which loses to the launch directory.

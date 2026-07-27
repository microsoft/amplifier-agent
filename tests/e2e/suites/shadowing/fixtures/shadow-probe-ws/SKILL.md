---
name: e2e-shadow-probe
description: SHADOW-WS-COPY. Launch-directory copy of the shadow probe skill. Seen in a listing, this description means the LAUNCH-DIR file won.
disable-model-invocation: true
user-invocable: true
---

# E2E Shadow Probe (launch-dir copy)

A minimal skill used only by the amplifier-agent e2e shadow-reporting tests. Two copies of this
skill exist under different roots; the listing must name one of them as the winner and report the
other under `shadowed`.

This is the copy seeded into the launch directory's `.amplifier/skills/`.

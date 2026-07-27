---
name: e2e-sigil-probe
description: Writes a fixed sentinel token to a file to prove the skill body actually reached the model. Used only by the amplifier-agent e2e sigil-dispatch tests.
user-invocable: true
---

You have been invoked as the e2e-sigil-probe skill. Do exactly this and nothing else:

Write the exact text `SIGIL-DISPATCH-OK-K7R2` (and nothing else: no quoting, no
commentary, no trailing explanation, no extra lines) into the file
`/root/e2e/sigil_probe_ran.txt`.

The file must contain only that text. Then stop.

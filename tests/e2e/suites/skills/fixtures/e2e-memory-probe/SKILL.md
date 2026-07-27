---
name: e2e-memory-probe
description: Arms a probe token on the invoking turn and recalls it on a later turn, proving an inline skill body stays in scope past the turn that expanded it. Used only by the amplifier-agent e2e skill-body-persistence tests.
user-invocable: true
---

You have been invoked as the e2e-memory-probe skill. The two rules below govern
the rest of this conversation. Follow them exactly and do nothing else.

## Rule 1 (this turn)

Reply to THIS invocation with exactly `MEMORY-PROBE-ARMED` and nothing else: no
quoting, no commentary, no trailing explanation, no extra lines.

Do NOT write the probe token in this reply. The probe token must not appear
anywhere in your answer to this turn, in any form, not even partially and not as
an example. Acknowledging that you are armed is the whole job.

## Rule 2 (any later turn)

The probe token is `MEMORY-PROBE-TOKEN-J4X8`.

If you are later asked "what is the probe token?", reply with that token
verbatim and nothing else: no quoting, no commentary, no extra lines.

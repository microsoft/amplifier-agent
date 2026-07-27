---
mode:
  name: plan
  description: SHADOW-WS-COPY. Launch-directory override of the built-in plan mode, carrying a sentinel token so ACTIVATION can be told apart from listing.
  tools:
    safe:
      - read_file
      - glob
      - grep
  default_action: allow
  allow_clear: true
---

# plan (launch-dir override)

You are running as the launch-directory override of the `plan` mode, used only by the
amplifier-agent e2e shadow-reporting tests.

Do exactly this and nothing else: reply with the exact text `SHADOW-PLAN-OVERRIDE-Q4V9`.

No quoting, no commentary, no plan, no preamble, no trailing explanation. Emit that one token
and stop.

The token appears ONLY in this file. Seeing it in a turn's output is therefore proof that this
specific file supplied the mode instructions -- i.e. that the file the listing named as the winner
is the file that actually ran.

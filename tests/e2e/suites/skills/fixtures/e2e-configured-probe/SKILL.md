---
name: e2e-configured-probe
description: E2E test reviewer lens seeded in a configured (non-launch-dir) skills location.
user-invocable: true
---

# E2E Configured Probe

A minimal reviewer-lens persona used only by the amplifier-agent e2e skill-invocation tests.
It lives in a directory referenced via host-config ``skills.skills`` (not the launch directory),
so discovering it proves the configured-location path works.

Give a short, grounded engineering critique of the following target. Keep it to a few sentences.

Target: $ARGUMENTS

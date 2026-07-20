---
bundle:
  name: agent-adapter-designer
  version: 1.0.0
  description: >-
    Design workspace for integrating amplifier-agent into host applications.
    Provides surface selection guidance, host adapter case study patterns,
    cross-cutting concern coverage, and produces a concrete adapter design document.
    Activate /mode amplifier-agent-adapter-designer to begin.

includes:
  - bundle: git+https://github.com/microsoft/amplifier-foundation@main
  - bundle: agent-adapter-designer:behaviors/agent-adapter-designer
---

# amplifier-agent Adapter Designer

This session is equipped for designing host adapter integrations for `amplifier-agent`.

Activate the design mode to begin a guided, self-sufficient design conversation:

    /mode amplifier-agent-adapter-designer

Or delegate directly to the expert agent for specific questions:

    delegate to agent-adapter-designer:adapter-design-expert

---

@foundation:context/shared/common-system-base.md

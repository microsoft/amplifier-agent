---
bundle:
  name: dev
  version: 0.1.0
  description: >
    Developer bundle for amplifier-agent contributors. Surfaces the
    coupling-diagram skill (cross-layer dependency maps), the dot-graph
    authoring and review pipeline, and foundation's investigator agents.

includes:
  # Foundation: explorer, delegate, recipes, skills-behavior, hooks, orchestrator,
  # context-simple, loop-streaming — everything a general Amplifier session needs.
  - bundle: git+https://github.com/microsoft/amplifier-foundation@main

  # dot-graph: dot-author agent, diagram-reviewer agent, tool-dot-graph,
  # dot-graph skills (syntax, patterns, quality, analysis).
  - bundle: git+https://github.com/microsoft/amplifier-bundle-dot-graph@main

  # Explicit Anthropic provider so contributors don't need an external
  # settings.yaml entry to get started. Sonnet-class: good balance of quality
  # and speed for exploratory and diagram work.
  - bundle: foundation:providers/anthropic-sonnet

# tool-skills already ships with foundation (skills-behavior) and dot-graph
# (dot-core behavior). Each sets its own `skills:` list; the later include wins
# on deep-merge, discarding the earlier source. To surface ALL THREE sources —
# foundation curated skills, dot-graph skills, AND the local dev skills —
# we re-declare the module here with the full combined list.
# (No `source:` needed — the module is already installed by the includes above.)
tools:
  - module: tool-skills
    config:
      skills:
        - "git+https://github.com/microsoft/amplifier-bundle-skills@main#subdirectory=skills"
        - "git+https://github.com/microsoft/amplifier-bundle-dot-graph@main#subdirectory=skills"
        # Local dev skills — resolved relative to CWD (repo root).
        # Run amplifier from the repo root for this path to resolve correctly.
        - ".amplifier/bundles/dev/skills"
---

# amplifier-agent Contributor Dev Bundle

You are running with the **amplifier-agent developer bundle**. You have access to
all of foundation's capability (explorer, planner, delegate, recipes, bash, filesystem,
web, git-ops, LSP, python-check, …) plus the dot-graph pipeline and a local
`coupling-diagram` skill specific to this repo.

## Key capabilities for contributors

| What you need | How to get it |
|---|---|
| Investigate a code path | Delegate to `foundation:explorer` |
| Regenerate coupling diagrams | Load and run the `coupling-diagram` skill (or `/coupling-diagram`) |
| Author a DOT diagram | Delegate to `dot-graph:dot-author` |
| Review a DOT diagram | Delegate to `dot-graph:diagram-reviewer` |
| Design decision analysis | Load `architecture-primitives` skill |

## The coupling diagram skill

`coupling-diagram` runs the three-step pipeline that regenerates
`/tmp/amplifier-agent-coupling/{coupling-overview,mcp-chain,provider-chain}.{dot,svg,png}`.
It maps every CLI flag through the wire protocol, engine dispatch, module layer,
and bundle membership — color-coded by failure mode so you can see which modules
break silently if removed from `bundle.md`.

Invoke it any time you change:
- `src/amplifier_agent_lib/bundle/bundle.md` (module composition)
- `src/amplifier_agent_lib/_runtime.py` (engine dispatch)
- `src/amplifier_agent_cli/modes/single_turn.py` (CLI → wire packing)
- `src/amplifier_agent_cli/provider_detect.py` or `provider_sources.py`

---

@foundation:context/shared/common-agent-base.md

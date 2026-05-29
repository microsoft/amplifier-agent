# amplifier-agent Dev Bundle

Developer-facing Amplifier bundle for contributors working on this repo.
Not shipped with the product — lives in `.amplifier/bundles/dev/` and is
intended for local use only.

## What it provides

| Capability | Source |
|---|---|
| `foundation:explorer`, `foundation:git-ops`, `foundation:zen-architect` | foundation |
| `dot-graph:dot-author`, `dot-graph:diagram-reviewer` | amplifier-bundle-dot-graph |
| `coupling-diagram` skill (`/coupling-diagram`) | `.amplifier/bundles/dev/skills/` |
| Anthropic Sonnet provider | foundation:providers/anthropic-sonnet |
| All foundation tools (bash, filesystem, grep, LSP, python-check, recipes…) | foundation |

## Quick start

```bash
# From the repo root:
amplifier run --bundle .amplifier/bundles/dev/bundle.md
```

Then inside the session:

```
/coupling-diagram
```

This runs the three-step pipeline: `foundation:explorer` maps the dependency
chain, `dot-graph:dot-author` produces the diagrams, and
`dot-graph:diagram-reviewer` validates quality. Output lands in
`/tmp/amplifier-agent-coupling/`.

## When to regenerate the coupling diagrams

Regenerate any time you change:

- `src/amplifier_agent_lib/bundle/bundle.md` — module composition changes
- `src/amplifier_agent_lib/_runtime.py` — engine dispatch path changes
- `src/amplifier_agent_cli/modes/single_turn.py` — CLI → wire field packing
- `src/amplifier_agent_cli/provider_detect.py` or `provider_sources.py`

The diagrams show which capabilities fail SILENTLY (orange bold) vs which
produce a visible error (red bold) when a module is removed from `bundle.md`.

## Skill sources

The dev bundle surfaces three skill collections:

| Collection | What it contains |
|---|---|
| `amplifier-bundle-skills` (curated) | Architecture, brainstorming, debugging, design patterns, etc. |
| `amplifier-bundle-dot-graph` (DOT) | `dot-syntax`, `dot-patterns`, `dot-quality`, `dot-graph-intelligence`, etc. |
| `.amplifier/bundles/dev/skills/` (local) | `coupling-diagram` — repo-specific |

Skills are discovered from the repo root, so run amplifier from the repo root
(the default when using `amplifier run` from within the repo).

## Adding more dev skills

Put any new repo-specific skill in `.amplifier/bundles/dev/skills/<name>/SKILL.md`.
It will be auto-surfaced the next time you start a session with this bundle.

Standard frontmatter:

```yaml
---
name: my-skill
description: One-liner that the visibility hook shows — include trigger phrases.
version: 0.1.0
---
```

Add `user-invocable: true` to make it available as `/my-skill`.

## Bundle composition notes

This bundle is intentionally minimal. It adds exactly:

1. Two `includes:` beyond foundation (dot-graph + anthropic-sonnet provider)
2. A `tool-skills` config override that surfaces all three skill sources

Everything else — orchestrator, context module, hooks, file/bash/search tools,
delegate tool, todo tool, recipes, LSP, python-check — comes from foundation
and requires no redeclaration here.

The `skills` config list is explicitly declared rather than using additive
merge because `tool-skills` treats the `skills` key as winner-takes-all across
the composition chain (foundation, dot-graph, and the dev bundle each set it,
and only the last one wins). The dev bundle's override restores all three
sources.

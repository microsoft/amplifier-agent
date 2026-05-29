---
name: coupling-diagram
description: >
  Regenerate the amplifier-agent cross-layer coupling diagrams (L0 CLI →
  L1 wire → L2 engine → L3 modules → L4 bundle). Traces CLI flags and env
  vars through the wire protocol, engine dispatch, and module layer to show
  which modules break silently if removed from bundle.md. Use when asked to
  regenerate diagrams, update coupling maps, trace CLI flag → module paths,
  or understand the failure mode when a module is removed.
version: 0.1.0
user-invocable: true
---

# Coupling Diagram Regeneration

Regenerate `/tmp/amplifier-agent-coupling/` with fresh cross-layer coupling
diagrams that reflect the current state of the amplifier-agent codebase.

**Output**: 3 DOT + SVG + PNG diagram sets plus a README.md, all in
`/tmp/amplifier-agent-coupling/` (overwrite existing). Then print file paths,
the top 5 fragile-coupling findings in prose, any design-doc-vs-code
discrepancies found during exploration, and open `coupling-overview.svg` with
`open /tmp/amplifier-agent-coupling/coupling-overview.svg`.

---

## Color taxonomy (stable across runs — do not change)

| Color | Meaning |
|---|---|
| Red bold border | HARD FAIL — error envelope returned, visible non-zero exit |
| **Orange bold border** | **SILENT NO-OP — exit 0, feature dead, no error (the dangerous class)** |
| Yellow | WARNING + degraded — stderr warn, partial function |
| Green | Safe — wire-only or stateless; no module dependency |
| Orange `note` shape | ENV VAR BRIDGE — invisible out-of-band coupling hop |
| Yellow dashed edge | DESIGN GAP — parsed but never stored or wired |

Every diagram with ≥10 nodes MUST include a `cluster_legend` subgraph using
these exact semantics. Save `.dot` sources alongside `.svg` and `.png`.

---

## Step 1 — Delegate to `foundation:explorer` (model_role: research)

Produce a markdown dependency-mapping document with these three tables plus
narrative findings. This document is handed verbatim to the diagram author in
Step 2.

**Scope**: wire ↔ module coupling only. One hop into CLI arg-parsing on the
left side, one hop into provider/transport on the right. NOT a full
architecture overview.

**Primary sources (read in this order):**

a) All design docs in `docs/designs/` — read these FIRST; the auto-injected
   `hooks-design-context` hook lists them in the session context.

b) `src/amplifier_agent_lib/protocol/` — wire TypedDicts and `PROTOCOL_VERSION`

c) `src/amplifier_agent_lib/_runtime.py` and `engine.py` — where wire fields
   dispatch to modules

d) `src/amplifier_agent_cli/modes/single_turn.py` — CLI flag → wire field
   packing

e) `src/amplifier_agent_lib/bundle/bundle.md` — declared module composition

f) `src/amplifier_agent_cli/provider_detect.py` + `provider_sources.py` —
   provider catalog

g) `~/.amplifier/cache/amplifier-module-*/` — upstream module source
   (especially `tool-mcp`, `provider-anthropic`, `provider-azure-openai`)
   to confirm what each module reads from the environment

**Required output — three tables:**

**Table 1 — CLI-chain:** `L0 CLI flag / env var` | `L1 wire field` |
`L2 engine consumer (file:line)` | `L3 module(s) required` |
`L4 bundle membership` | `Failure mode if module absent`

**Table 2 — Module-chain:** For each L3 module in Table 1: `Module` |
`Env vars it reads` | `Where set (L2 file:line)` | `Who sets it` |
`Verification that reader exists?`

**Table 3 — Provider-chain:** `Env var` | `Detected by (file:line)` |
`Provider module` | `Injection path` | `Pre-warm? (bundle.md entry?)`

**Narrative:** After the tables, write a "Fragile coupling chains" section
calling out every coupling that is SILENT (env var bridge with no
verification that a reader exists) or HARD (explicit error path). Include
any discrepancies found between the design docs and the actual
implementation.

---

## Step 2 — Delegate to `dot-graph:dot-author`

Hand the complete explorer markdown verbatim in the instruction. Produce:

1. `coupling-overview.dot/.svg/.png` — full L0→L4 layered map. One cluster
   per layer (`cluster_l0_cli`, `cluster_l1_wire`, `cluster_l2_engine`,
   `cluster_l3_modules`, `cluster_l4_bundle`). All significant CLI flags
   traced end-to-end. Color-coded per the taxonomy above. The ENV VAR BRIDGE
   node(s) sit OUTSIDE all clusters to make the invisible hop visible.

2. `mcp-chain.dot/.svg/.png` — the MCP silent-no-op chain in isolation.
   File:line annotations on critical edges. Both the working path (green
   outcome) and the silent breakage scenario (orange SILENT NO-OP node with
   the "bundle removed → silent exit 0" arc).

3. `provider-chain.dot/.svg/.png` — provider env-var fan-in →
   `detect_provider()` → injection → module fan-out. Distinguish pre-warmed
   modules (declared in bundle.md) from cold-prepared modules (cloned at
   first call). Show the legacy `AZURE_OPENAI_KEY` → `AZURE_OPENAI_API_KEY`
   deprecation path with a yellow dashed edge if it still exists.

4. `README.md` — color legend (matching the taxonomy above), 5 key coupling
   paths in prose, and a "What breaks silently" section.

Use `rankdir=LR`. Use `compound=true` for cluster edges. Place file:line
annotations as edge labels on engine-dispatch edges.

---

## Step 3 — Delegate to `dot-graph:diagram-reviewer`

Apply the 5-level review to all three diagrams.

**Iterate once** if any diagram comes back WARN: send it back to
`dot-graph:dot-author` with the specific issues and the instruction "fix the
listed issues and re-render". After one fix iteration, accept the result
regardless of residual WARNs (do not loop more than once).

Stop when all three diagrams are PASS or after one fix iteration.

---

## After all steps complete

1. Print a table of the 9 output files with sizes.
2. Print "Top 5 fragile coupling findings" in prose.
3. Print any design-doc-vs-code discrepancies the explorer found.
4. Run: `open /tmp/amplifier-agent-coupling/coupling-overview.svg`

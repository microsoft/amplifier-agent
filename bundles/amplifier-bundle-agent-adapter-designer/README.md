# amplifier-bundle-agent-adapter-designer

An Amplifier bundle providing a **self-sufficient design workspace** for developers
integrating [`amplifier-agent`](https://github.com/microsoft/amplifier-agent) into
host applications.

## What it does

When you compose this bundle (or activate its mode), you get a focused workspace for
designing an `amplifier-agent` host adapter end-to-end. You come out the other side
with a concrete adapter design document covering your chosen integration surface,
borrowed patterns from real host adapters, cross-cutting decisions, and a risk register.

### Coverage

**Three integration surfaces:**
- `amplifier-agent-py` — Python Client SDK (single-turn subprocess)
- `amplifier-agent-ts` — TypeScript Client SDK (single-turn subprocess, Node >=20)
- `amplifier-agent serve chat-completions` — HTTP server (OpenAI-compatible sidecar)

**Three host adapter case studies:**
- **opencode** → HTTP face: auto-start + model discovery + config-write pattern
- **paperclip** → TypeScript SDK: adapter registry + per-turn spawn + workspace isolation
- **nanoclaw** → TypeScript SDK in Docker: build-time priming + MCP passthrough + CI version-lint

**All cross-cutting concerns:** credentials, MCP injection, bundle cache priming, protocol
version pinning, workspace isolation, env allowlist, binary discovery, multi-turn patterns,
DisplayEvent stream handling.

---

## Usage

### Activate the design mode

```
/mode amplifier-agent-adapter-designer
```

The mode is **self-sufficient** — it carries the full picture of surfaces, case studies,
and concerns. A fresh session entering the mode needs no prior context to begin productive
adapter design.

The mode guides you through:
1. Host runtime characterization
2. Surface selection (with trade-off analysis)
3. Pattern borrowing from the closest case study
4. Cross-cutting checklist
5. Risk register
6. Producing a structured adapter design document (`adapter-design.md`)

### Or delegate to the expert agent directly

For a specific question without entering the full design mode:

```
delegate to agent-adapter-designer:adapter-design-expert
  with: "Which surface fits a FastAPI host? What are the gotchas?"
```

---

## Architecture

```
bundle.md (thin)
├── behaviors/agent-adapter-designer.yaml   # Wires agent + awareness context
│   ├── agents/adapter-design-expert.md    # Context sink: full integration reference
│   └── context/adapter-design-awareness.md # Thin pointer (~200 tokens, always-loaded)
├── modes/amplifier-agent-adapter-designer.md  # User-facing entry point
└── context/integration-reference.md       # Full knowledge base (agent-only, ~2500 tokens)
```

### Context-sink discipline

The full integration reference (~2,500 tokens: all surfaces, case studies, cross-cutting
concerns) lives in the **agent's context**. It is only loaded when the expert agent is
spawned — never in the root session or mode injection. Root sessions carry only the thin
awareness pointer (~200 tokens).

This means:
- **Mode active**: ~900 tokens ephemeral injection (mode body) + ~200 tokens awareness
- **Agent delegated**: ~2,500 tokens in child session (disposable after agent completes)
- **No mode, no delegation**: ~200 tokens only

---

## Mechanism mix

| Mechanism | Name | Purpose |
|-----------|------|---------|
| Mode | `amplifier-agent-adapter-designer` | Design conversation, tool policies, workflow, document template |
| Agent | `adapter-design-expert` | Context sink: full integration reference, precise Q&A |
| Context (thin) | `adapter-design-awareness.md` | Root session pointer (~200 tokens, always) |
| Context (heavy) | `integration-reference.md` | Full knowledge base (agent-only, ~2500 tokens) |
| Behavior | `agent-adapter-designer-behavior` | Wires agent + awareness into composed sessions |

**No recipe**: Design conversations are inherently interactive. A rigid multi-step recipe
would reduce the flexibility developers need when exploring unfamiliar integration surfaces.

**No skill**: The agent covers all reference and reasoning needs. A skill would duplicate
the agent at higher per-turn visibility cost without additional capability.

---

## Loading the bundle

Add to your bundle's `includes:`:

```yaml
includes:
  - bundle: git+https://github.com/microsoft/amplifier-bundle-agent-adapter-designer@main
```

Or run standalone:

```bash
amplifier run \
  --bundle git+https://github.com/microsoft/amplifier-bundle-agent-adapter-designer@main \
  "Help me design an amplifier-agent host adapter"
```

---

## Tool policies (design mode)

The mode enforces a design-conversation-appropriate tool surface:

| Policy | Tools |
|--------|-------|
| `safe` | `read_file`, `glob`, `grep`, `delegate`, `web_fetch`, `todo`, `load_skill`, `mode` |
| `warn` (1 confirmation) | `bash`, `write_file`, `edit_file` |
| `block` | Everything else |

Shell commands (`bash`) and file writes (`write_file`, `edit_file`) require one
acknowledgment step. This prevents accidental file creation during a design conversation
while still allowing the final `write_file` call for the design document.

---

## Repository structure

```
amplifier-bundle-agent-adapter-designer/
├── bundle.md                                      # Thin root bundle
├── README.md                                      # This file
├── behaviors/
│   └── agent-adapter-designer.yaml               # Behavior: agent + awareness context
├── agents/
│   └── adapter-design-expert.md                  # Expert agent (context sink)
├── context/
│   ├── adapter-design-awareness.md               # Thin awareness pointer
│   └── integration-reference.md                  # Full integration reference (agent-only)
├── modes/
│   └── amplifier-agent-adapter-designer.md       # Design mode
└── docs/
    └── BEHAVIORAL_MODEL.md                        # Pre-implementation verification artifact
```

---

## Design philosophy notes

This bundle was designed following the Amplifier bundle lifecycle:
1. **Mechanism design** → Mode + Agent (context sink) + thin awareness context
2. **Behavioral model** → 10 scenarios covering surface selection, case study reference, cross-cutting concerns, design document production, and edge cases (wrong surface, env allowlist blocker, protocol mismatch)
3. **Verification** → Scenarios reviewed before implementation

See `docs/BEHAVIORAL_MODEL.md` for the full behavioral model, including assumptions and known gaps.

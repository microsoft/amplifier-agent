# amplifier-agent Adapter Design

This session can design host adapter integrations for `amplifier-agent` —
Microsoft's modular AI agent engine for embedding Amplifier inside host applications.

## What this capability covers

**Three integration surfaces:**
- **Python SDK** (`amplifier-agent-py`) — single-turn subprocess for Python hosts
- **TypeScript SDK** (`amplifier-agent-ts`) — single-turn subprocess for Node.js >=20 hosts
- **HTTP server** (`amplifier-agent serve chat-completions`) — OpenAI-compatible sidecar

**Three host adapter case studies** with pattern-layer analysis:
- **opencode** — HTTP face (CLI, OpenAI-shaped host, auto-start + model discovery)
- **paperclip** — TypeScript SDK (Node SaaS, adapter registry pattern)
- **nanoclaw** — TypeScript SDK inside Docker (container product, build-time priming)

**All cross-cutting concerns:** credentials, MCP injection, bundle cache priming,
protocol version pinning, workspace isolation, env allowlist, binary discovery,
multi-turn patterns, DisplayEvent handling.

## Entry points

**Design mode** (recommended) — self-sufficient workspace with design journey and document template:

    /mode amplifier-agent-adapter-designer

**Expert agent** — direct access to the full integration reference:

    delegate to agent-adapter-designer:adapter-design-expert

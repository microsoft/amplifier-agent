# Workspace Identity and Storage Flexibility

**Status:** COMPANION — forward-looking extensibility analysis of the workspace identity design.
**Author:** Manoj Prabhakar Paidiparthy
**Date drafted:** 2026-06-09
**Companion to:** `docs/designs/2026-06-09-workspace-resolution-and-migration.md` (the core resolution + migration design; to be written).
**Audience:** amplifier-agent engineers evaluating a new adapter or storage backend 6–18 months from now. You need to know what the workspace design already enables, what it explicitly defers, and how to extend it without breaking existing invariants.

---

## 1. Purpose

This document explains the **extensibility properties** of the workspace identity design (the resolution + migration design dated 2026-06-09). It does **not** re-litigate that design — the resolution order, slug format, cwd derivation, and migration path are settled there.

What this document does: explain what the design enables for future evolution along two axes the design owner flagged as important —

1. **Richer organizational structures** (projects, tenants, users, workspaces) that future adapters may need to model.
2. **Non-filesystem storage backends** (SQLite, HTTP, virtual) that future deployments may require.

The original framing this document answers, in the design owner's words:

> "An 'amplifier agent home' path or something in general, but then if the particular adapter/use-case has concepts like projects, workspaces, working dir, etc., how do we want to generally support them? Not all will be actual file path based, so may be virtual (db, web, etc.)."

The short version: today's design satisfies the home-path and adapter-concept requirements through a single opaque `workspace` string, and it **defers** virtual storage — but it preserves the one invariant that makes virtual storage substitutable later without a rewrite. That invariant is the subject of §2.

---

## 2. The load-bearing invariant

The single concept this document protects:

> **Session identity is separable from storage backend.**

Concretely:

- `workspace` is a string the adapter sets. **AAA does not interpret its meaning.** It is a leaf identifier — the name of *this specific session's bucket*.
- Today, AAA materializes that string as a filesystem path:
  ```
  <state_root>/workspaces/<workspace>/sessions/<session_id>/...
  ```
- The **identity** (the string) and the **materialization** (the path) are independent concerns that happen to live in the same code today.

This separation is what enables every future extension below. Identity is set per-spawn by the adapter; materialization is a mechanism AAA owns and can swap. The fact that today's materialization is "concatenate the string into a path" is an implementation detail, not a contract.

> **I1 — the identity/backend separation invariant.** Workspace is a string the adapter sets and AAA does not interpret. The backend that turns that string into stored bytes is a substitution point, independent of the identity itself. Every extension in this document depends on I1 holding.

---

## 3. Future expansion: richer organizational structures

### 3a. Multi-dimensional scope

**Today:** one string. If an adapter needs hierarchy, it encodes the hierarchy in the string (`acme:my-app:main`). AAA does not parse it — it is an opaque bucket name.

**Tomorrow,** when a real adapter needs *structural queries* (e.g., "list all workspaces under tenant `acme`"), the extension is additive:

- Add `coordinator.config["tenant"]`, `coordinator.config["user"]`, etc. as **additional keys** alongside `workspace`.
- `workspace` remains the leaf identifier — what identifies this specific session's bucket.
- New keys are additive — they do not break existing hooks that only read `workspace` / `project_slug`. Those hooks continue to see a flat namespace.

Concrete adapter examples:

| Adapter | Today | Future (when multi-tenant ships) |
|---------|-------|----------------------------------|
| CLI | `workspace=amplifier-agent-7f3a9d2c` (cwd-derived) | Same |
| Paperclip | `workspace=my-app` (VS Code workspace) | Same |
| NanoClaw | `workspace=group-7f3a` | Same |
| Hosted multi-tenant | `workspace=salils-app` | Add `tenant=acme`, `user=salil` |

> **D1 — additive scope expansion.** New organizational dimensions are accommodated by **new keys** in `coordinator.config`, not by splitting or reinterpreting `workspace`. The rationale: splitting `workspace` would break every hook that reads it; adding keys breaks nothing and the leaf identifier stays stable.

### 3b. Workspace listing / discovery

**Today:** AAA does not enumerate workspaces. The filesystem layout makes `ls workspaces/` work; that is the entire discovery API.

**Tomorrow:** if an adapter needs programmatic listing, a `session.discovery` capability could be added — **separately from storage**. Discovery and persistence are different concerns: one answers "what sessions exist under this scope?", the other answers "where do this session's bytes go?". Conflating them is how storage interfaces leak.

> **D2 — discovery is a separate concern.** We have not designed a discovery API today and do not need one. When an adapter actually needs programmatic enumeration, it rides its own capability, decoupled from the storage substitution point in §4.

### 3c. Per-workspace configuration

**Today:** workspace is identity-only, not a config scope. The same bundle config applies regardless of workspace.

**Tomorrow:** if adapters want per-workspace overrides (different models per project, different tool policies per tenant), this rides the **existing host config tier** — D7 of `docs/designs/2026-06-01-host-config-layer-revisit.md` — as a `workspace_overrides:` block keyed by workspace slug. This adds a layer *on top of* identity; it does not change the identity contract.

> **D3 — config scoping is an additive layer.** Per-workspace configuration, if it ships, is a new block in the host config schema keyed by workspace. It does not change the workspace identity contract; it consumes the identity as a lookup key.

---

## 4. Future expansion: non-filesystem backends

This is the section the design owner is most concerned about. Be honest: **today's design hardcodes a filesystem layout.** Both `IncrementalSaveHook` and `hook-context-intelligence` compute filesystem paths directly. That coupling is the technical debt. What the design preserves is the *invariant* (I1) that makes substitution possible without a rewrite.

### 4a. The substitution point

The day a second backend arrives, the substitution point is clear:

1. Introduce a `session.storage` capability on the coordinator.
2. Route every "where does this session's data go?" question through it.
3. Provide an XDG-filesystem implementation that matches today's behavior **exactly** (no user-visible change).

Contract sketch:

```python
class SessionStorage(Protocol):
    async def write_event(self, session_id: str, event: dict) -> None: ...
    async def read_events(self, session_id: str) -> AsyncIterator[dict]: ...
    async def list_sessions(self, scope: dict) -> list[SessionInfo]: ...
    async def exists(self, session_id: str) -> bool: ...
```

The `scope` dict carries `workspace` (and any future scope keys like `tenant` from §3a). The backend knows what to do with them. The hook and engine code stop computing paths and start consuming the capability.

> **D4 — the storage capability is the substitution seam.** A `session.storage` capability is registered **when (not before) a second backend ships.** Until then, the seam is identified, not implemented. This is deliberate: designing the interface before a second backend exists means speculative abstraction against semantics we cannot yet see (see D5).

### 4b. Filesystem backend (today's behavior, formalized)

```
write_event(session_id, event) →
  append JSONL to <state_root>/workspaces/<scope.workspace>/sessions/<session_id>/transcript.jsonl

list_sessions(scope) →
  scan <state_root>/workspaces/<scope.workspace>/sessions/

exists(session_id) →
  check <state_root>/workspaces/<scope.workspace>/sessions/<session_id>/
```

Behavior identical to today. The change is purely structural — hooks and the engine consume the capability rather than computing paths inline.

### 4c. Hypothetical DB backend (illustrative)

```
write_event(session_id, event) →
  INSERT INTO events (session_id, workspace, ts, payload) VALUES (...)
  Backed by SQLite locally, Postgres hosted, etc.

list_sessions(scope) →
  SELECT DISTINCT session_id FROM events WHERE workspace = ? ORDER BY last_event_ts DESC

exists(session_id) →
  SELECT 1 FROM events WHERE session_id = ? LIMIT 1
```

Same identity (`workspace` + `session_id`), different materialization. The hook code does not change — it consumes the capability.

### 4d. Hypothetical HTTP backend (illustrative)

```
write_event(session_id, event) →
  POST /workspaces/{workspace}/sessions/{session_id}/events

list_sessions(scope) →
  GET /workspaces/{workspace}/sessions

exists(session_id) →
  HEAD /workspaces/{workspace}/sessions/{session_id}
```

For hosted AAA-as-a-service, AAA-in-the-browser via WASM, or remote analytics streaming.

### 4e. The semantic mismatches we will face when this lands

Be honest about the leaky-abstraction problem (Joel Spolsky, *The Law of Leaky Abstractions*, https://www.joelonsoftware.com/2002/11/11/the-law-of-leaky-abstractions/). Filesystem, DB, and HTTP have incompatible semantics. A unifying interface that pretends they don't lands as either lowest-common-denominator (useless) or filesystem-shaped with awkward shims (the leaky-abstraction tax).

| Concern | Filesystem | DB | HTTP |
|---------|------------|-----|------|
| Atomicity | rename | transaction | none / idempotency keys |
| Ordering | mtime | timestamp + sequence | retry + dedup |
| Partial failure | torn file | rollback | retry storm |
| Latency | µs | ms | 10–500ms |

The capability interface will need to make these explicit — e.g., is `write_event` fire-and-forget, durable, or transactional? We do **not** design that today. We surface the question so the future designer does not pretend it doesn't exist.

> **D5 — backend semantic differences must surface in the capability contract.** They must not be hidden behind a lowest-common-denominator interface. The future designer who introduces `session.storage` owns the job of making durability, ordering, and atomicity semantics explicit in the contract.

---

## 5. What this design explicitly does NOT do

A short, honest list. Future engineers reading this know exactly what they are picking up when they extend it.

- **No storage capability ships today.** The substitution seam is identified (D4), not implemented.
- **No multi-dimensional scope ships today.** Single opaque string only.
- **No discovery API.** Filesystem layout is the API.
- **No backend-agnostic resume contract.** Resume is filesystem-only — `--resume <id>` reads `transcript.jsonl`.
- **No hosted AAA. No virtual storage. No multi-tenancy.**

These are deferrals, not gaps. Each is deferred because there is exactly one backend and a small number of adapters today; designing for backends and dimensions that do not exist yet is speculative abstraction.

---

## 6. The migration scenarios — concrete sketches

For each likely future change, here is what the migration looks like *given today's design*. This is the payoff section — proof that today's design does not paint into a corner.

### Scenario A — adding a second filesystem-backed hook

**Already supported.** The hook reads `coordinator.config["project_slug"]` (or `workspace`) and computes its own path under `state_root()`. No design change. This is exactly what the `hook-context-intelligence` adoption looks like.

**Cost:** none beyond the hook itself.

### Scenario B — adding a multi-tenant adapter

The adapter sets `coordinator.config["tenant"]` and `coordinator.config["workspace"]`. Existing hooks read `workspace` as before — they see a flat namespace. New hooks that care about tenant read both. The filesystem layout either stays `workspaces/<workspace>/...` (unchanged) or extends to `tenants/<tenant>/workspaces/<workspace>/...` (new convention), depending on operational preference.

**Cost:** small. The workspace identity remains the leaf (D1).

### Scenario C — adding a SQLite backend for local hosted AAA

A new module ships providing the `session.storage` capability backed by SQLite. The XDG-filesystem implementation is converted into a capability provider (no behavior change for users). Hooks that today compute paths are refactored to consume the capability. The workspace value flows through unchanged.

**Cost:** moderate. One module change per hook, one new module for the SQLite backend, one refactor of the FS backend into a capability provider. Workspace contract unchanged.

### Scenario D — adding an HTTP backend for hosted multi-tenant AAA

Same as Scenario C, but the backend module talks to an HTTP API. Plus: the host config gets a new `storage:` block declaring the backend choice and endpoint. Plus: the resume contract has to negotiate "where do you look for this `session_id`?" across backends — the first real backend-aware contract.

**Cost:** high. This is where the semantic mismatches in D5 stop being hypothetical. But the workspace identity layer survives unchanged.

---

## 7. Invariants to preserve

When future engineers extend this, these are the contracts that must not break:

1. **I1 — Identity/backend separation.** Workspace is a string. Backend is a substitution. They are independent.
2. **I2 — Adapter contract stability.** `--workspace` argv, `AMPLIFIER_AGENT_WORKSPACE` env, cwd-derived fallback. Adapters built today must keep working when backends change.
3. **I3 — Additive scope.** New organizational dimensions are new keys in `coordinator.config`, not modifications to `workspace`.
4. **I4 — Engine-level identity, not host config.** Workspace identity is set per-spawn by the adapter (argv/env), not in the strict 5-key host config schema. Future scope keys ride the same tier.
5. **I5 — Ecosystem alias.** `coordinator.config["project_slug"]` and `coordinator.config["workspace"]` are written as aliases. When the ecosystem aligns on one name, drop the other.

---

## 8. Signals that say "extend now"

Concrete criteria the future engineer can monitor:

- A second hook lands that also computes filesystem paths → **extract the storage capability** (D4).
- A non-filesystem backend has shipping intent within 90 days → **design the storage capability proactively.**
- An adapter needs to list/query workspaces programmatically → **add a discovery capability** (D2).
- An adapter needs to model independent organizational dimensions → **add scope keys** (tenant, user, etc.) (D1).
- Resume across workspaces becomes a hot path → **consider a session-locator capability** separate from storage.

---

## 9. Catalytic question

**"What would have to be true for the identity/backend separation to fail us?"**

Honest answers:

- If backends end up needing identity-shaped knowledge to operate (e.g., an HTTP backend needs to know whether `workspace` is a path component vs. a URL component), the separation leaks.
- If multi-dimensional scope ends up being so tightly coupled that "workspace" alone isn't a meaningful leaf, the abstraction breaks.
- If the ecosystem standardizes on a different identity name (not `project_slug` and not `workspace`), we end up with three names instead of two.

None of these look likely. But the future engineer reading this should know which assumptions they are inheriting.

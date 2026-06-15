# Workspace Resolution and Migration

**Status:** LOCKED — D1–D10 specified; implementation pending.
**Author:** Manoj Prabhakar Paidiparthy
**Date drafted:** 2026-06-09
**Companion document:** `docs/designs/2026-06-09-workspace-identity-and-storage-flexibility.md` (forward-looking extensibility analysis of the identity layer specified here).
**Audience:** amplifier-agent engineers implementing this design now, plus future engineers reading the design corpus 6–18 months from now. You need to understand the contract, the wire-up points, the migration mechanics, and the rationale for each locked decision.

---

## 1. Purpose

This document specifies how amplifier-agent resolves a per-session `workspace` identity from adapter inputs, and how it migrates the existing flat `sessions/<id>/` layout to the new `workspaces/<workspace>/sessions/<id>/` layout.

The companion document `docs/designs/2026-06-09-workspace-identity-and-storage-flexibility.md` explains the extensibility properties of this design — what it enables for richer organizational structures and non-filesystem storage backends. This document is the load-bearing technical design; the companion explains the future-flexibility properties.

The original framing this design answers, in the design owner's words:

> "An 'amplifier agent home' path or something in general, but then if the particular adapter/use-case has concepts like projects, workspaces, working dir, etc., how do we want to generally support them? Not all will be actual file path based, so may be virtual (db, web, etc.)."

This design addresses the **identity layer** of that question — a single `workspace` string that any adapter can populate from whatever organizational concept it has. It **defers** the virtual-storage layer (db, web, etc.) to the companion document, which preserves the invariant that makes virtual storage substitutable later without a rewrite.

---

## 2. Background and prior decisions this builds on

This design sits on top of decisions already locked elsewhere. None of them change here.

| Prior decision | Source | What it means for this design |
|----------------|--------|-------------------------------|
| XDG state convention | `persistence.py` | `state_root()` = `$XDG_STATE_HOME/amplifier-agent`, fallback `~/.local/state/amplifier-agent`. The new `workspaces/` tree lives under this root. Locked. |
| Programs-first; no XDG default for config | D1 of `2026-06-01-host-config-layer-revisit.md` | AAA's primary caller is a program. Workspace is set per-spawn by that program, not read from a config default. Locked. |
| Strict 5-key host config schema | D7 of `2026-06-01-host-config-layer-revisit.md` | Workspace must **not** enter this schema. It is engine-level identity, not module config (see D6 below). |
| No dependency on `~/.amplifier/registry.json` | I3 of `2026-06-01-host-config-layer-revisit.md` | Workspace stays under AAA's own state tree. It does not read amplifier-app-cli's registry. |
| Sealed `bundle.md` | D4 of `2026-05-19-baked-in-bundle-decision.md` | This design does **not** modify `bundle.md`. Ecosystem hooks read `coordinator.config["project_slug"]` automatically (see D5). |

---

## 3. The problem this solves

Two concrete pains.

**1. Flat session bucketing.** Today, every session AAA writes — regardless of which repo, project, or invoking host — lands in `state_root() / "sessions" / <id>/`. There is no per-project separation. Multi-repo users cannot tell which session came from where.

**2. No `project_slug` for ecosystem hooks.** Hooks designed for the broader Amplifier ecosystem (notably `hook-context-intelligence`) expect `coordinator.config["project_slug"]` to identify a logical bucket. AAA never sets it. Result: those hooks fall back to a flat `"default"` workspace.

The smaller, also-true pain:

**3. No clean way for adapters to express organizational identity.** NanoClaw has groups. Paperclip has VS Code workspaces. Future hosts will have tenants/users/projects. Today there is no single contract for "what is this session's organizational identity?"

---

## 4. Goals and non-goals

**Goals:**

- One contract that adapters can populate from whatever organizational concept they have.
- Per-workspace filesystem bucketing of session state.
- Zero-config integration with ecosystem hooks expecting `project_slug`.
- Stable cwd-derived default so adapters that don't set anything still get useful bucketing.
- Safe, idempotent migration of existing flat sessions.

**Non-goals (explicitly deferred):**

- Storage backend abstraction (filesystem only — see companion doc D4).
- Multi-dimensional scope keys (`tenant`, `user`, etc.) — additive, not today.
- Workspace listing / discovery API.
- Per-workspace configuration overrides (would ride D7 of host config; not today).

---

## 5. Locked decisions

### D1 — The adapter-facing name is `workspace`

Argv flag: `--workspace <slug>`. Env var: `AMPLIFIER_AGENT_WORKSPACE`. A single string. The adapter is free to construct it from any organizational concept it has.

Rationale: matches `hook-context-intelligence`'s user-facing knob naming. The noun survives multiple adapter contexts (VS Code workspaces, NanoClaw groups, CI jobs, future multi-tenant workspaces) and does not assume a filesystem.

### D2 — Resolution order: argv > env > cwd-derived

First non-empty hit wins. Never `None`. Never empty. The cwd-derived fallback ensures every session has a workspace, even when no adapter intervenes.

```python
def resolve_workspace(
    argv_workspace: str | None,
    env: Mapping[str, str],
    cwd: Path,
) -> str:
    if argv_workspace:
        return validate_slug(argv_workspace)
    env_value = env.get("AMPLIFIER_AGENT_WORKSPACE", "").strip()
    if env_value:
        return validate_slug(env_value)
    return derive_workspace_from_cwd(cwd)
```

### D3 — Slug format

`^[a-z0-9][a-z0-9-]{0,63}$`. Lowercase. Leading `_` reserved for AAA-internal workspaces (e.g., `_legacy`). Length-bounded for filesystem safety. Validated at parse, not at use — path traversal is blocked before the value ever reaches the filesystem.

```python
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

def validate_slug(value: str) -> str:
    if not SLUG_RE.match(value):
        raise WorkspaceError(
            f"invalid workspace slug: {value!r}; "
            f"must match [a-z0-9][a-z0-9-]{{0,63}}"
        )
    return value
```

| Input | Result |
|-------|--------|
| `acme-api` | `acme-api` ✓ |
| `ACME` | `WorkspaceError` (not lowercase) |
| `../etc` | `WorkspaceError` (path traversal blocked at parse) |
| `_legacy` | `WorkspaceError` (leading `_` reserved) |
| `""` / unset | Fall through to next tier |
| 64+ chars | `WorkspaceError` |

### D4 — Cwd-derived default

Stable: same cwd → same slug. The algorithm mirrors `amplifier_app_cli.project_utils.get_project_slug` verbatim so the `project_slug` alias (D5) actually aligns across hosts — the same cwd produces an identical `project_slug` under both amplifier-agent and amplifier-app-cli, which is the only way ecosystem hooks like `hook-context-intelligence` can compute the same bucket regardless of which host launched the session.

```python
def derive_workspace_from_cwd(cwd: Path) -> str:
    slug = str(cwd.resolve()).replace("/", "-").replace("\\", "-").replace(":", "")
    if not slug.startswith("-"):
        slug = "-" + slug
    return slug
```

Examples:

- `/Users/me/repos/amplifier-agent` → `-Users-me-repos-amplifier-agent`
- `/` → `-`
- `C:\projects\web-app` → `-C-projects-web-app` (Windows)

The cwd-derived slug deliberately does **not** conform to `validate_slug`: it starts with `-`, preserves case, can exceed 64 chars, and may contain spaces. Explicit argv/env values are still validated; the cwd fallback bypasses validation. The reserved `_` prefix (I7) remains unreachable because every cwd-derived slug starts with `-`.

> **Algorithm parity is the contract.** Any future normalization (case folding, space handling, length cap) must land in both amplifier-agent and amplifier-app-cli together — divergence silently breaks the D5 alias.

### D5 — Dual-key write to `coordinator.config`

`_runtime.py` writes both keys:

```python
coordinator.config["workspace"] = workspace      # AAA-canonical
coordinator.config["project_slug"] = workspace   # ecosystem-canonical alias
```

Rationale: `workspace` is the name AAA controls; `project_slug` is what existing ecosystem hooks read. Aliasing makes the context-intelligence hook (and any other ecosystem module expecting `project_slug`) work zero-config. When the ecosystem aligns on one name, drop the other.

### D6 — Engine-level identity, not host config

`workspace` does **not** enter the strict 5-key host config schema (preserves D7 of `2026-06-01-host-config-layer-revisit.md`). The adapter sets it per-spawn via argv/env.

Rationale: workspace identity is per-session, set by the spawner. Host config is for module parameterization, not engine identity. Conflating the two would force a schema amendment for a value that has nothing to do with module config.

### D7 — Child session propagation by inheritance

In `spawn.py`, child coordinators inherit the parent's workspace verbatim:

```python
workspace = parent_coordinator.config["workspace"]
child_coordinator.config["workspace"] = workspace
child_coordinator.config["project_slug"] = workspace
```

Rationale: cwd may have changed mid-session; re-deriving would silently bucket subsession output elsewhere. Inheritance preserves session-tree locality — a delegate's output lands in the same workspace as its parent.

### D8 — Filesystem layout

```
$XDG_STATE_HOME/amplifier-agent/
└── workspaces/
    └── <workspace>/
        └── sessions/
            └── <session_id>/
                ├── transcript.jsonl
                ├── metadata.json
                └── <hook-specific-subdirs>/
```

Hooks that write per-session state target subdirectories under `sessions/<session_id>/`.

### D9 — Migration: lazy, one-shot, idempotent, locked

The migration runs on the first AAA boot after upgrade. Trigger: presence of `state_root() / "sessions"` with children. All existing sessions move to a reserved `_legacy` workspace. No data deletion. A file lock prevents concurrent processes from racing. Mechanics in §7.

### D10 — Cross-workspace resume fallback

`SessionStore.load()` first checks the current workspace, then walks `workspaces/*/sessions/<id>/` to find a session in any workspace. It logs which workspace it was found in. Rationale: users don't need to remember which workspace a session belonged to. Mechanics in §7.

---

## 6. File-level change inventory

Audits, `--fresh` cleanup, and all per-session metadata follow the unified workspace-scoped layout. There is no flat `sessions/<id>/` tree post-migration, and no split between "user data" (transcripts, hook output) and "operational metadata" (audits, `--fresh` cleanup) — everything per-session lives under `workspaces/<workspace>/sessions/<id>/`. The migrator moves the entire session directory verbatim, including any `audits/` subdirectory under it (see §7).

| File | Change | Risk |
|------|--------|------|
| `persistence.py` | Add `workspaces_root()` helper. No behavior change to existing helpers. | None |
| `_runtime.py` | Call `resolve_workspace`; write `coordinator.config["workspace"]` and `["project_slug"]`; construct `SessionStore` with per-workspace root. Move the audit-write path from `state_root() / "sessions" / <id> / "audits" / ...` to `state_root() / "workspaces" / <workspace> / "sessions" / <id> / "audits" / ...`. | Touches hot path — needs incremental-save unit tests. |
| `_runtime.py` (`--fresh` cleanup) | Change cleanup target from `state_root() / "sessions" / <id>` to `state_root() / "workspaces" / <workspace> / "sessions" / <id>`. | `--fresh` must resolve the workspace before computing the cleanup path. |
| `spawn.py` (around lines 453-456) | Propagate workspace to child coordinator alongside existing capability propagation (D7). | Forgetting this = silent bucketing bug in subsessions. |
| `session_store.py` | Constructor takes the per-workspace root; layout falls out. Add cross-workspace `load()` fallback (D10). | Low. |
| `incremental_save.py` | No change — already takes the store object. | None. |
| `modes/single_turn.py` | Add `--workspace <slug>` click option. | Trivial. |
| `config/loader.py` | **No change.** Workspace is engine-level (D6). | None. |
| `bundle.md` | **No change.** Hooks read `coordinator.config["project_slug"]` automatically (D5). | None. |

---

## 7. Migration mechanics

The migrator behavior:

- **Trigger:** `state_root() / "sessions"` exists and has children.
- **Target:** `state_root() / "workspaces" / "_legacy" / "sessions" / <id>/`.
- **Lock:** `flock` on `state_root() / ".migration.lock"`.
- **Re-check after acquiring lock** (handles concurrent boot race).
- **For each session dir:** `shutil.move`; skip if target exists (log warning, leave source in place).
- **Remove old `sessions/` root** only if empty after migration.
- **Return** `MigrationResult(migrated=N, skipped=bool, collided=M)`.

No migrator code change is required for the unified layout. `shutil.move(session_dir, target)` moves the **entire** session directory tree, so any `audits/` subdirectory living under `sessions/<id>/` is carried along automatically — the migrator already brings every per-session artifact (transcript, metadata, audits, hook output) into the workspace-scoped tree in one move. The only forward-looking change is in `_runtime.py`, where the audit-write path and `--fresh` cleanup target are recomputed under `workspaces/<workspace>/` (see §6).

```python
LEGACY_WORKSPACE = "_legacy"
LOCK_PATH = state_root() / ".migration.lock"

def migrate_legacy_sessions_if_needed() -> MigrationResult:
    old_root = state_root() / "sessions"
    if not old_root.exists() or not any(old_root.iterdir()):
        return MigrationResult(migrated=0, skipped=True)

    new_root = state_root() / "workspaces" / LEGACY_WORKSPACE / "sessions"

    with file_lock(LOCK_PATH):
        if not old_root.exists():
            return MigrationResult(migrated=0, skipped=True)

        new_root.mkdir(parents=True, exist_ok=True)
        moved, collided = 0, 0
        for session_dir in old_root.iterdir():
            if not session_dir.is_dir():
                continue
            target = new_root / session_dir.name
            if target.exists():
                log.warning("migration: %s already at target; leaving in place", session_dir.name)
                collided += 1
                continue
            shutil.move(str(session_dir), str(target))
            moved += 1

        try:
            old_root.rmdir()
        except OSError:
            pass

        log.info("migration: moved %d sessions to _legacy (%d collisions)", moved, collided)
        return MigrationResult(migrated=moved, skipped=False, collided=collided)
```

Cross-workspace resume fallback (D10):

```python
async def load(self, session_id: str) -> Optional[Transcript]:
    path = self.root / session_id / "transcript.jsonl"
    if path.exists():
        return await read_transcript(path)

    workspaces_root = state_root() / "workspaces"
    if not workspaces_root.exists():
        return None
    for ws_dir in workspaces_root.iterdir():
        if ws_dir.name == self.root.parent.name:
            continue
        candidate = ws_dir / "sessions" / session_id / "transcript.jsonl"
        if candidate.exists():
            log.info("resume: found %s in workspace %s (current=%s)",
                     session_id, ws_dir.name, self.root.parent.name)
            return await read_transcript(candidate)
    return None
```

---

## 8. Logging contract

| Event | Level | Cadence |
|-------|-------|---------|
| Migration started | INFO | Per-process, once |
| N sessions migrated, M collisions | INFO | Per-process, once |
| Migration skipped (nothing to migrate) | DEBUG | Per-process, once |
| Resume found session in different workspace | INFO | Per-resume |
| Migration error (target exists) | WARNING | Per-session |

---

## 9. Adapter contract examples

| Adapter | What it sets | Resulting workspace |
|---------|--------------|---------------------|
| CLI in `/repos/amplifier-agent/` | nothing | `amplifier-agent-a1b2c3d4` (cwd-derived) |
| CLI with flag | `--workspace foo` | `foo` |
| NanoClaw | env `AMPLIFIER_AGENT_WORKSPACE=group-7f3a` | `group-7f3a` |
| Paperclip | env from VS Code workspace name | `my-app` |
| CI | env or flag from job | `pr-1234` |

---

## 10. Invariants

- **I1 — Identity/backend separation.** Workspace is a string. Filesystem materialization is the backend. They are independent.
- **I2 — Adapter contract stability.** `--workspace` argv, `AMPLIFIER_AGENT_WORKSPACE` env, cwd-derived fallback. Stable across releases. Adapters built today keep working when backends change.
- **I3 — Engine-level identity, not host config.** Workspace does not enter the strict 5-key host config schema (D7 of `2026-06-01-host-config-layer-revisit.md`).
- **I4 — Ecosystem alias.** `project_slug` and `workspace` are written as aliases. When the ecosystem aligns on one name, drop the other.
- **I5 — Cwd-derivation stability.** Same cwd always produces the same slug. The 8-char SHA hash gives ~2³² buckets — practically collision-free.
- **I6 — No data deletion in migration.** Sessions are moved, never deleted.
- **I7 — Reserved `_` prefix.** Workspaces beginning with `_` are AAA-internal (only `_legacy` exists today).
- **I8 — Unified per-session layout.** All per-session state — transcripts, metadata, audits, hook output, and any future per-session artifact — lives under `workspaces/<workspace>/sessions/<id>/`. There is no second tree and no split between user data and operational metadata. Future hooks and engine surfaces that need to write per-session data must compose their path under this root.

---

## 11. Risk register

| Risk | Likelihood | Severity | Mitigation |
|------|------------|----------|------------|
| Migration leaves split state | Low | Low | Cross-workspace resume lookup handles split state transparently. Re-running completes the rest. |
| Two processes race on first post-upgrade boot | Medium | Low | flock + re-check after acquire. |
| Real workspace named `_legacy` collides | Very low | Medium | `_` prefix reserved in validate_slug. |
| User automation reads old `sessions/` path | Medium | High for them | Release note. No compat shim. |
| Cross-filesystem move | Very low | Low | shutil.move falls back to copy+delete. |
| Stale lock from killed process | Low | Low | flock released on process death by kernel. |
| Cwd-derived slug collision | Very low (2⁻³²) | Low | If observed, grow hash to 12 chars. |
| Cross-workspace audit walks for drift detection | Low (drift detection is opt-in operational tooling) | Low | Future admin command if/when needed. |

---

## 12. Success metrics

- First-boot post-upgrade: `MigrationResult.migrated == old_session_count`.
- All subsequent boots: `MigrationResult.skipped=True`.
- Zero "my sessions disappeared" support reports in the first 30 days.
- Resume telemetry: count of `resume: found in different workspace` log lines (high during the first few days, taper toward zero).

---

## 13. Open questions and follow-ups

- **Should we add an admin diagnostic command (e.g., `amplifier-agent workspaces list`) to enumerate workspaces and their session counts?** Deferred. Filesystem layout makes `ls` work; defer a programmatic API until an adapter needs it (companion D2).
- **Should `--legacy-layout` exist for one release?** Decided no. Release note is sufficient. Re-evaluate if a user reports automation breakage.
- **Should the cwd hash grow to 12 chars?** Decided no for now. 8 chars gives 2³² buckets; collision is practically impossible. Monitor.
- **Should audits and `--fresh` cleanup stay on the flat path while transcripts move?** **Decided: unified layout.** Audits, `--fresh`, and all per-session metadata move to the workspace-scoped tree alongside transcripts. There is no split between user data and operational metadata. Rationale: a split tree creates a second naming convention engineers must remember, complicates the migration story, and offers no benefit that justifies the additional surface area.

---

## 14. Catalytic question

> **"What would have to be true for this resolution + migration design to be wrong?"**

1. **A non-filesystem backend arrives mid-implementation.** Then the migration is moot — the data lives elsewhere. Verify timing with whoever owns the roadmap.
2. **Users have built non-trivial automation against the flat `sessions/<id>/` tree.** A release note isn't sufficient; this would need a `--legacy-layout` compat flag for one version. Survey before shipping.
3. **Cwd-derived slugs collide meaningfully.** The 8-char hash gives 2³² buckets; collision is practically zero but worth noting. If users see same-basename repos landing in the same workspace, grow the hash.
4. **The cross-workspace resume fallback masks real bugs.** If users see "found in different workspace" logs constantly, workspace identity isn't stable across invocations — cwd-derivation is unreliable in their environment. Monitor that log line.
5. **Cross-workspace audit aggregation becomes a hot path before workspace-scoped audit storage is implemented.** If operators need to compare audits across workspaces frequently (per the mode-A pivot R8' drift detection), the unified layout makes that a walk over `workspaces/*/sessions/*/audits/` — feasible but slower than a flat tree. Mitigation: ship an `amplifier-agent audits show <session_id>` admin command that uses the same cross-workspace lookup pattern as `SessionStore.load()`. Flagged as a future tooling concern, not a design concern.

None look likely.

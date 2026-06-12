# Drop XDG, Unify on `~/.amplifier-agent/`, and Clean Up CLI Flags

**Date:** 2026-06-11
**Status:** Plan, pending approval
**Branch:** `feat/drop-xdg-unify-storage-root` (cut from `origin/main` at `9f650db`)

---

## Motivation

Two related cleanups:

1. **Drop XDG path support.** The current code partially honors `XDG_CACHE_HOME`, `XDG_CONFIG_HOME`, `XDG_STATE_HOME` in the engine, but the `hook-context-intelligence` config has the literal `~/.local/state/amplifier-agent/workspaces` baked in (see `bundle.md:201`). The XDG override silently diverges engine storage from hook storage. The wrapper layer additionally strips all `XDG_*` env vars before invoking the engine subprocess — meaning any user expecting XDG to "just work" through a wrapper has been quietly running on `~/.local/state/...` defaults all along.
2. **Clean up CLI flags.** The investigation surfaced silent flag-ignoring, default favoring wrappers over humans, and undocumented flag combinations.

Both changes are wrapper-safe in the form described below, per the investigation in this session.

---

## Wrapper-Contract Invariants (Non-Negotiable)

These MUST hold after the refactor:

1. `--output json` remains a valid value of the `--output` flag. Both wrappers pass it explicitly.
2. All currently-emitted flags remain valid: `--session-id`, `--resume`/`--fresh`, `--cwd`, `--provider`, `--config`, `--output`, `--protocol-version`, `--display`, `--workspace`, `-y`, `-n`.
3. Stdout JSON envelope shape unchanged (`protocolVersion`, `sessionId`, `turnId`, `reply`, `error`, `metadata`).
4. Exit-code semantics unchanged (0 ok, 1 engine error, 2 protocol error, 3 approval error).
5. `AMPLIFIER_MCP_CONFIG` env-var injection contract unchanged.
6. Stderr NDJSON contract under `--display ndjson` unchanged.

These can change without breaking wrappers (per investigation Part A2–A4):

- Engine storage path defaults (wrappers never construct engine storage paths)
- `--output` flag DEFAULT (wrappers always pass `--output json` explicitly)
- Adding `AMPLIFIER_AGENT_HOME` env-var support (passes through wrappers via `AMPLIFIER_*` prefix rule)
- Engine config_show output (wrappers don't read it)
- Adding new flag conflict checks for combinations wrappers don't emit

---

## Phase 1: Drop XDG, Unify on `~/.amplifier-agent/`

### Target storage layout

```
~/.amplifier-agent/
  cache/            # bundle prepare cache (was XDG_CACHE_HOME or ~/.cache/amplifier-agent)
  config/           # host config (was XDG_CONFIG_HOME or ~/.config/amplifier-agent)
  state/
    workspaces/     # session/transcript/audit/context-intelligence per workspace
  .migrated         # sentinel; presence means legacy XDG layout was migrated
```

Override: a single env var `AMPLIFIER_AGENT_HOME` that relocates the entire tree. No three-variable XDG juggling.

### Code changes

#### 1.1 `src/amplifier_agent_lib/persistence.py`

Replace XDG resolvers with a single home resolver and sub-directory helpers.

```python
def amplifier_agent_home() -> Path:
    """Single root for all amplifier-agent on-disk state.

    Default: ~/.amplifier-agent/
    Override: $AMPLIFIER_AGENT_HOME
    """
    override = os.environ.get("AMPLIFIER_AGENT_HOME")
    if override:
        return Path(override).expanduser()
    return _home() / ".amplifier-agent"

def cache_root() -> Path:    return amplifier_agent_home() / "cache"
def config_root() -> Path:   return amplifier_agent_home() / "config"
def state_root() -> Path:    return amplifier_agent_home() / "state"
```

`workspaces_root()` and `session_state_dir()` unchanged in shape — they call `state_root()`, so they pick up the new layout transparently.

`_home()` is retained as-is (`HOME` env var with `~` expansion fallback) — it's still used by the new resolver as the default-root anchor.

Drop the `XDG_CACHE_HOME` / `XDG_CONFIG_HOME` / `XDG_STATE_HOME` reads at lines 99, 109, 119.

#### 1.2 `src/amplifier_agent_lib/bundle/bundle.md`

Update the `hook-context-intelligence` config (line ~201):

```diff
- base_path: "~/.local/state/amplifier-agent/workspaces"
+ base_path: "~/.amplifier-agent/state/workspaces"
```

The hook's `config_resolver.py` calls `.expanduser()` on this string (per investigation Q2b), so `~` works. The `$AMPLIFIER_AGENT_HOME` override is NOT honored by the hook — this is a known cross-org limitation. Document in the design doc and in the bundle.md comment that follows.

**Known limitation:** if a user sets `$AMPLIFIER_AGENT_HOME` to a non-default location, the context-intelligence hook will still write to `~/.amplifier-agent/state/workspaces` (the literal in bundle.md), NOT to the override root. A real fix requires upstream `expandvars` support in `hook-context-intelligence/config_resolver.py`. This is the same divergence we have today with `$XDG_STATE_HOME`, just simplified to one variable.

#### 1.3 `src/amplifier_agent_cli/admin/config_show.py`

Drop reporting of `XDG_CACHE_HOME`, `XDG_CONFIG_HOME`, `XDG_STATE_HOME` (lines ~160, 166-168).
Add reporting of `AMPLIFIER_AGENT_HOME` with source annotation (`env:AMPLIFIER_AGENT_HOME` or `default`).

#### 1.4 Migration logic — new `src/amplifier_agent_lib/migration.py` (or extend existing)

```python
def maybe_migrate_legacy_storage() -> MigrationResult:
    """One-way move of XDG-era storage into ~/.amplifier-agent/.

    Idempotent via sentinel file at <home>/.migrated.
    Atomic per-directory via rename().
    Concurrent-safe via fcntl.flock on lock file.

    Moves:
      ~/.local/state/amplifier-agent/  -> $home/state/
      ~/.cache/amplifier-agent/        -> $home/cache/
      ~/.config/amplifier-agent/       -> $home/config/

    Also handles $XDG_*_HOME variants if set, but does not re-read them
    after migration completes.
    """
    home = amplifier_agent_home()
    sentinel = home / ".migrated"
    if sentinel.exists():
        return MigrationResult.SKIPPED_ALREADY_MIGRATED

    home.mkdir(parents=True, exist_ok=True)
    lock_path = home / ".migration.lock"
    with _flock_exclusive(lock_path):
        if sentinel.exists():           # re-check under lock
            return MigrationResult.SKIPPED_ALREADY_MIGRATED

        moved = []
        for legacy, target_subdir in [
            (_legacy_state(), "state"),
            (_legacy_cache(), "cache"),
            (_legacy_config(), "config"),
        ]:
            if legacy.exists() and not (home / target_subdir).exists():
                shutil.move(str(legacy), str(home / target_subdir))
                moved.append((legacy, home / target_subdir))

        sentinel.write_text(f"migrated at {datetime.now(UTC).isoformat()}\n"
                            f"from: {[str(m[0]) for m in moved]}\n")
    return MigrationResult.MIGRATED if moved else MigrationResult.NOTHING_TO_MIGRATE


def _legacy_state() -> Path:
    """Pre-refactor state path (honors XDG_STATE_HOME for users who set it)."""
    xdg = os.environ.get("XDG_STATE_HOME")
    return (Path(xdg) if xdg else _home() / ".local" / "state") / "amplifier-agent"

# similar for _legacy_cache(), _legacy_config()
```

Migration runs at most once per `$AMPLIFIER_AGENT_HOME`.

#### 1.5 `src/amplifier_agent_cli/admin/update.py`

After the `uv tool install --reinstall --force ...` invocation succeeds (line ~296-304), call `maybe_migrate_legacy_storage()` and report its result in the command output.

```python
if completed.returncode == 0:
    migration = maybe_migrate_legacy_storage()
    # ...report in text or JSON output...
```

This makes `amplifier-agent update` the canonical migration trigger. Engine startup does NOT auto-migrate — users who skip `update` and run the engine directly continue working from the new layout (which starts empty) without disturbing the legacy tree.

#### 1.6 Cosmetic: TS wrapper docstrings

Replace `~/.local/state/amplifier-agent/...` references in:
- `wrappers/typescript/src/session.ts:201`
- `wrappers/typescript/src/argv-builder.ts:72, 136`
- `wrappers/typescript/src/index.ts:203, 232`

These are JSDoc comments only — no behavior change. Update to `~/.amplifier-agent/state/workspaces/...`.

#### 1.7 Tests

Search and update tests that reference XDG paths or `~/.local/state/amplifier-agent`. Expected impact: small (tests typically use `tmp_path` fixtures).

### Phase 1 verification

- New install: `~/.amplifier-agent/` is created on first session; legacy paths untouched (no migration triggered because update wasn't run).
- Existing install + `amplifier-agent update`: legacy paths move to new layout; sentinel created; subsequent commands use new paths.
- Existing install without `amplifier-agent update`: engine writes to new layout; legacy paths sit unused.
- `AMPLIFIER_AGENT_HOME=/tmp/test`: everything relocates to `/tmp/test/`.
- Wrapper test pass-through: TS and Python wrapper integration tests unchanged (XDG removal is invisible to them).

---

## Phase 2: CLI Flag Cleanup

### 2.1 Default `--output` from `json` → `text`

`src/amplifier_agent_cli/modes/single_turn.py:501`:

```diff
- default="json",
+ default="text",
```

Update help text to reflect that text-by-default optimizes for humans, and that wrappers explicitly pass `--output json`.

**Wrapper impact:** none — both wrappers pass `--output json` explicitly (Python `argv_builder.py:43`, TS `argv-builder.ts:121-122`).

### 2.2 Reject conflicting flag combinations at parse time

Add validation in `single_turn.py` after Click parses args, before any engine boot:

```python
if quiet and (verbose or debug):
    raise click.UsageError("--quiet conflicts with -v/--verbose and --debug")

if resume and fresh:
    raise click.UsageError("--resume and --fresh are mutually exclusive")

# Note: --output text + --display ndjson is NOT a conflict — they govern
# different streams (stdout vs stderr) and the combination is meaningful.
```

The existing `-y` / `-n` mutex check (line 575) remains.

**Wrapper impact:** none — wrappers never pass conflicting combinations (Python always `-y` + `--fresh` or `--resume`; TS conditional but mutually-exclusive paths).

### 2.3 Help-text polish

Update `--verbose`, `--debug`, `--quiet` help text to note: *"only applies when --display text; ignored in --display ndjson mode (host filters)."*

Update `--display` help to clarify that it governs **stderr** format, independent of `--output` (which governs **stdout**).

### 2.4 Naming asymmetry — defer

`--output {text, json}` vs `--display {text, ndjson}` is a real asymmetry but renaming breaks the wrapper contract (both wrappers hard-code `--output json` and `--display text|ndjson` literal values).

Document the asymmetry in `--help` epilogue instead of renaming. Revisit when there is a wrapper-major-version bump.

### Phase 2 verification

- `amplifier-agent run "hi"` (no flags) — text reply on stdout, text events on stderr. Currently produces JSON envelope on stdout.
- `amplifier-agent run "hi" --quiet -v` — usage error, exit 2.
- `amplifier-agent run "hi" --resume --fresh` — usage error, exit 2.
- All existing wrapper integration tests pass unchanged.

---

## Out of Scope

- Per-event filtering (separate decision still pending)
- Cancellation gap fixes (separate decision still pending)
- Schema versioning on `--display ndjson` notifications (separate decision)
- Updating `docs/host-config-reference.md` and `docs/cancellation-behavior.md` to reflect post-PR-#48 reality (separate task; should follow this refactor)

---

## Risk Summary

| Risk                                                                 | Likelihood | Mitigation                                                                                       |
| -------------------------------------------------------------------- | ---------- | ------------------------------------------------------------------------------------------------ |
| context-intelligence hook stays on old literal path                  | Certain    | Update bundle.md literal; document `$AMPLIFIER_AGENT_HOME` limitation; track upstream `expandvars` ask |
| Migration fails partway (disk full, perms)                           | Low        | Idempotent + sentinel-guarded + fcntl-locked; sentinel only written on full success              |
| User runs new engine WITHOUT running `amplifier-agent update` first  | Likely on existing installs | New engine writes to new layout. Legacy tree sits unused. User notices "where are my sessions?" — they run `update`, migration runs, history reappears. |
| Test fixtures reference XDG paths                                    | Likely     | Find/replace pass; expected small impact                                                          |
| Some unknown caller depends on `--output json` default                | Low        | Wrappers don't. Any external script will be one-line-fixable.                                     |

---

## Execution Order

1. Land Phase 1 (XDG drop + migration) in one PR.
2. Verify end-to-end on a real install (create some sessions on old layout, run update, confirm new layout).
3. Land Phase 2 (flag cleanup) as a separate PR for clean review.
4. Update the two human-facing docs (`docs/host-config-reference.md`, `docs/cancellation-behavior.md`) once both phases are merged.

---

## Amendment — Migration Auto-Invocation Removed (follow-up to Phase 1)

**Branch:** `feat/standalone-migrate-subcommand`

Phase 1 shipped with `maybe_migrate_legacy_xdg_storage()` called automatically
after a successful `amplifier-agent update`, and `migrate_legacy_sessions_if_needed()`
called automatically on the first engine turn per process.  Both produced log noise
during normal operation.

**Change:** Both auto-invocations have been removed:

- `_runtime.py` — `migrate_legacy_sessions_if_needed()` call and the `_MIGRATION_RAN`
  process guard are deleted.  The migration function itself is unchanged.
- `update.py` — `maybe_migrate_legacy_xdg_storage()` call and all result-reporting
  code are deleted from the post-install success path.

**New entry point:** `amplifier-agent migrate` standalone subcommand
(`src/amplifier_agent_cli/admin/migrate.py`).  Calls both migrations in order and
reports each result separately (text or JSON).  Idempotent — safe to run multiple
times.  The library functions in `migration.py` are unchanged.

The body of this design doc is preserved as a historical record.

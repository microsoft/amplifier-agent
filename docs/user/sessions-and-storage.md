# Sessions and storage

amplifier-agent is a single-turn CLI, but conversations span multiple turns. State is persisted to disk between invocations under a *workspace* and a *session id*.

## The single-turn model

Every `amplifier-agent run` call is one turn:

1. Boot the engine (deserialize the prepared bundle, wire up the configured provider, install hooks).
2. Load transcript from disk if `--resume`. Wipe transcript if `--fresh`.
3. Submit the prompt and stream the result through the orchestrator (`loop-streaming`).
4. Persist the new turn to disk.
5. Exit.

Multi-turn conversation is achieved by re-running with the same `--session-id` and `--resume`. There is no daemon; nothing persists in memory between turns.

```bash
amplifier-agent run "What's 2+2?" --session-id math -y
#   4

amplifier-agent run "Add 3" --session-id math --resume -y
#   2+2+3 = 7
```

## Workspaces

A *workspace* isolates sessions belonging to one project. Resolution order:

1. `--workspace <slug>` flag.
2. `AMPLIFIER_AGENT_WORKSPACE` env var.
3. **Auto-derived** from `--cwd` (or process cwd if unset): `<basename_slug>-<sha256(abs_cwd)[:8]>`.

Auto-derivation makes the slug deterministic per-directory and disambiguates same-name directories in different paths. Example for `/home/alice/work/my-project`:

```
my-project-7a3b8c9d
```

The base name is lowercased and slug-sanitized; the suffix is 8 hex chars of the sha256 of the absolute path.

### Slug grammar

```
[a-z0-9][a-z0-9-]{0,63}
```

A leading underscore is reserved for internal workspaces:

- `_legacy` — the target of the one-time migration of pre-workspace sessions (flat `state/sessions/<id>/` → `state/workspaces/_legacy/sessions/<id>/`).

## The on-disk layout

```
~/.amplifier-agent/                                            ← $AMPLIFIER_AGENT_HOME, default
├── cache/
│   └── prepared/
│       └── 0.5.2/                                              ← <amplifier_agent_version>
│           └── da41ba6300040dd9/                               ← <sha256(bundle.md)[:16]>
│               ├── prepared.pickle                             ← Pickled PreparedBundle
│               └── manifest.json                               ← {"aaa_version", "bundle_sha256_prefix"}
├── config/                                                     ← Reserved for future use
└── state/
    ├── .migrated_from_xdg                                      ← Sentinel; XDG migration ran
    └── workspaces/
        ├── _legacy/                                            ← Pre-workspace sessions migrated here
        │   └── sessions/...
        └── my-project-7a3b8c9d/                                ← Auto-derived workspace slug
            └── sessions/
                └── smoke-1/                                    ← <session_id>
                    ├── transcript.jsonl                        ← One JSON message per line
                    ├── metadata.json                           ← Session metadata
                    ├── audits/
                    │   └── turn-turn-1.json                    ← Per-turn audit
                    └── context-intelligence/
                        ├── events.jsonl                        ← Kernel/delegate lifecycle events
                        └── metadata.json
```

### `transcript.jsonl`

One JSON object per line. Each line is a kernel-side message:

```jsonl
{"role": "user", "content": "Reply with just: pong", "metadata": {"timestamp": "..."}}
{"role": "assistant", "content": [{"type": "thinking", "thinking": "...", "signature": "..."}, {"type": "text", "text": "pong"}]}
```

The transcript is the source of truth for resume. `--resume` replays this file into the orchestrator's context before the new prompt is appended.

### `metadata.json`

Minimal — currently just a turn status marker:

```json
{ "last_turn": "complete" }
```

### `audits/turn-<turn_id>.json`

One file per `run` invocation. Only written when `--session-id` is provided (anonymous runs do not write audits).

```json
{
  "argvDigest": "sha256:<64 hex>",
  "envDigest":  "sha256:<64 hex>",
  "protocolVersion": "0.3.0",
  "exitCode": 0,
  "correlationId": "dd548f41-c9a4-4f08-b45b-203d9fc2b349",
  "startedAt": "2026-06-12T08:22:02.255700+00:00",
  "endedAt":   "2026-06-12T08:22:23.695709+00:00"
}
```

Notes:

- `argvDigest` is `sha256(' '.join(sys.argv))`.
- `envDigest` is currently a placeholder (`sha256({"extra": {}})`) — env allowlisting moved to the SDK side; the engine no longer captures the inbound env.
- `correlationId` matches the `correlationId` in the JSON envelope `error` and `metadata` fields, and in the NDJSON wire events.
- In Mode A, each `run` is "turn-1" — multiple runs against the same session ID **overwrite** the same audit file. The transcript and context-intelligence event log preserve the full history.

### `context-intelligence/`

Written by the `hook-context-intelligence` bundle. Captures kernel and `delegate`-tool lifecycle events:

```jsonl
{"event": "session/start", "timestamp": "...", ...}
{"event": "delegate:agent_spawned", "agent": "explorer", ...}
{"event": "delegate:agent_completed", "result": "...", ...}
```

These events are local-only — no remote dispatch unless `AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL` is set (currently no support for setting it inside amplifier-agent).

## Resume, fresh, and anonymous runs

| Mode | Behavior |
|---|---|
| `--session-id <id>` only | New session. Creates `<workspace>/sessions/<id>/` if missing. Writes transcript, metadata, audit. If the dir already exists, raises (use `--fresh` or `--resume`). |
| `--session-id <id> --resume` | Loads existing transcript, replays it, then runs the new prompt. Falls back to *any other workspace* under `workspaces_root()` if not found in the current workspace (cross-workspace resume). |
| `--session-id <id> --fresh` | **Deletes** `<workspace>/sessions/<id>/` before booting. Use to wipe a corrupt session and start over. |
| (no `--session-id`) | **Anonymous run**. No audit is written. No session directory is created. Transcript still exists in-memory but is not persisted. |

`--resume` includes transcript repair: if a previous run was interrupted leaving an orphaned tool call (a `tool_use` block with no matching `tool_result`), the loader injects a synthetic cancellation result so the orchestrator can proceed. This is what makes session resume robust across SIGTERM, crashes, and timeouts.

## Listing and inspecting sessions

There is no `sessions list` subcommand. Use the filesystem:

```bash
# List all sessions in a workspace
ls ~/.amplifier-agent/state/workspaces/<workspace>/sessions/

# Find a session anywhere
find ~/.amplifier-agent/state -type d -name '<session-id>'

# Inspect a transcript
jq -c . < ~/.amplifier-agent/state/workspaces/.../sessions/.../transcript.jsonl | head

# Inspect audits
cat ~/.amplifier-agent/state/workspaces/.../sessions/.../audits/*.json
```

## Migrations

Two one-time migrations run automatically:

### XDG → unified storage

Pre-0.5.x amplifier-agent followed XDG conventions: `~/.local/state/amplifier-agent/`, `~/.cache/amplifier-agent/`, `~/.config/amplifier-agent/`. The current layout unifies everything under `~/.amplifier-agent/`. The migration:

- Runs once after a successful `amplifier-agent update`.
- Moves `state/`, `cache/`, `config/` into the new root.
- Writes a sentinel at `~/.amplifier-agent/.migrated_from_xdg` so it never runs twice.

### Flat sessions → workspace tree

Sessions created under the previous flat layout (`state/sessions/<id>/`) are moved to `state/workspaces/_legacy/sessions/<id>/` on first runtime. This runs at most once per process and is idempotent.

## Cleaning up

There is no automatic GC. Clean up by hand:

```bash
# Remove one session
rm -rf ~/.amplifier-agent/state/workspaces/<ws>/sessions/<id>/

# Remove one workspace
rm -rf ~/.amplifier-agent/state/workspaces/<ws>/

# Remove all sessions (preserves bundle cache)
rm -rf ~/.amplifier-agent/state/

# Nuke everything (sessions, cache, sentinel)
rm -rf ~/.amplifier-agent/
```

The bundle cache is independent of sessions:

```bash
amplifier-agent cache clear
# removes ~/.amplifier-agent/cache/prepared/* across all versions
```

Next `run` re-clones and re-installs the bundle modules.

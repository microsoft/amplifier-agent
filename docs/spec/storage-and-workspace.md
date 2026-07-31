# Storage and Workspace

## Scope

Everything about on-disk identity and layout: the single storage root and its override, how a
workspace slug is resolved and validated, where per-session state lands, how a workspace propagates
to child sessions, and the two user-invoked migrations. The host config file that may live under this
root is specified in `host-config.md`. The prepared-bundle cache under `cache/` is specified in
`bundle-and-cache.md`.

## Storage root

One tree, one override:

```
~/.amplifier-agent/                             default
  cache/
    prepared/<version>/<bundle-hash>/           prepared-bundle cache
  config/                                       host config
  state/
    workspaces/
      <workspace>/
        sessions/
          <session_id>/
            transcript.jsonl
            metadata.json
            audits/turn-<turnId>.json
            context-intelligence/
    .migration.lock                             lock file, sessions migration
  .migrated_from_xdg                            sentinel; XDG layout was migrated
  .migration.lock                               lock file, XDG migration
```

`$AMPLIFIER_AGENT_HOME` relocates the entire tree. A leading `~` in its value is expanded. When it is
unset the root is `$HOME/.amplifier-agent/`, falling back to the platform's home-directory lookup if
`$HOME` is absent.

`XDG_CACHE_HOME`, `XDG_CONFIG_HOME`, and `XDG_STATE_HOME` do not affect any path. They are read only
by the XDG migration below, solely to locate pre-refactor directories to move, and are never
consulted again once the migration sentinel exists.

Known limitation: `hook-context-intelligence` is configured with the literal base path
`~/.amplifier-agent/state/workspaces`. It expands `~` but does not expand `$AMPLIFIER_AGENT_HOME`, so
a user who relocates the tree still gets context-intelligence output under the default root.

## Workspace resolution

Three tiers, first non-empty wins. The result is never empty and never absent.

```
1. --workspace <slug>              argv flag; whitespace is stripped
2. $AMPLIFIER_AGENT_WORKSPACE      environment variable; whitespace is stripped
3. derived from the process cwd    see below
```

Tiers 1 and 2 are validated against the slug grammar. A whitespace-only value in EITHER explicit tier
is treated as absent: `--workspace "  "` is forgiven the same way an empty env var is.

In server mode the workspace is resolved once at process startup and applies to every request; there
is no per-request override. The server accepts `$AMPLIFIER_AGENT_HTTP_WORKSPACE` in addition to
`$AMPLIFIER_AGENT_WORKSPACE`, with the HTTP-specific variable taking precedence.

The adapter-facing name is `workspace` because it matches `hook-context-intelligence`'s user-facing
knob and survives multiple adapter contexts (VS Code workspaces, NanoClaw groups, CI jobs) without
assuming a filesystem. Adapters in practice:

```
CLI in /repos/amplifier-agent/   sets nothing                              -> -repos-amplifier-agent
CLI with flag                    --workspace foo                           -> foo
NanoClaw                         AMPLIFIER_AGENT_WORKSPACE=group-7f3a      -> group-7f3a
CI                               env or flag from the job                  -> pr-1234
```

## Slug grammar

```
^[a-z0-9][a-z0-9-]{0,63}$
```

Lowercase only, length-bounded for filesystem safety, must start with an alphanumeric. Validation
happens at PARSE time, not at use, so path traversal is blocked before the value can ever be joined
into a filesystem path. Failure is surfaced as `argv_workspace_invalid`, classification `protocol`,
exit 2.

```
acme-api    ok
ACME        rejected (not lowercase)
../etc      rejected (path traversal blocked)
_legacy     rejected (leading _ reserved)
""/unset    falls through to the next tier
64+ chars   rejected
```

The `_` prefix is RESERVED for amplifier-agent-internal workspaces. Only `_legacy`, the sessions
migration target, exists today. The reservation is enforced structurally from both directions: the
grammar cannot match a leading underscore, and every cwd-derived slug starts with `-`, so `_` is
unreachable from either tier.

## The cwd-derived default

When neither explicit tier supplies a value, the workspace is derived from the process working
directory by this algorithm:

```
1. Resolve cwd to an absolute path with symlinks resolved.
2. Replace every "/" with "-".
3. Replace every "\" with "-".
4. Delete every ":".
5. If the result does not already start with "-", prepend "-".
```

This is PATH REPLACEMENT, not hashing. The full resolved path is encoded verbatim, so collisions are
structurally impossible rather than merely improbable, and the same cwd always yields the same slug.

```
/Users/me/repos/amplifier-agent  ->  -Users-me-repos-amplifier-agent
/repos/amplifier-agent           ->  -repos-amplifier-agent
/                                ->  -
C:\projects\web-app              ->  -C-projects-web-app
```

The result deliberately does NOT conform to the slug grammar: it starts with `-`, preserves case, can
exceed 64 characters, and may contain spaces. Explicit argv and env values are still validated; the
cwd fallback bypasses validation by design.

Algorithm parity is the contract. This algorithm is byte-for-byte identical to the amplifier CLI's
project-slug derivation, so the same cwd produces an identical slug under both hosts. That is the
only way ecosystem hooks like `hook-context-intelligence` can compute the same bucket regardless of
which host launched the session. Any future normalization (case folding, space handling, a length
cap) must land in both implementations together; divergence silently breaks the alias below.

## Workspace identity in session configuration

The resolved workspace is written into the session configuration under TWO keys, both carrying the
same value:

```
workspace       the name amplifier-agent controls
project_slug    the ecosystem-canonical alias existing hooks read
```

Any hook or downstream consumer reading session configuration sees both. Aliasing is what makes the
context-intelligence hook work zero-config. When the ecosystem aligns on one name, the other is
dropped.

The same pair is additionally seeded into `hook-context-intelligence`'s own module configuration
before the session is created, because the `session:start` event fires during session creation and
would otherwise be resolved before the session-level values exist. The hook's resolution chain is:

```
its own module config       (the seed wins)
-> session configuration    (the pair above)
-> slugified working dir    (the bundle install dir, wrong)
-> "default"
```

Without the seed, `session:start` output lands in the wrong on-disk bucket.

Workspace does NOT enter the host config schema. Workspace identity is per-session and set by the
spawner; host config is for module parameterization, not engine identity. `--workspace` is argv-only.

## Child session inheritance

Child sessions inherit the parent's resolved workspace VERBATIM, under both keys, and never re-derive
from cwd. This is load bearing: cwd may have changed mid-session, and re-deriving would silently
bucket a delegate's output into a different workspace than its parent. Inheritance preserves
session-tree locality. When the parent has no workspace set, nothing is propagated.

## Per-session layout

```
<state_root>/workspaces/<workspace>/sessions/<session_id>/
  transcript.jsonl              JSONL, one message per line
  metadata.json                 session metadata
  audits/turn-<turnId>.json     per-turn audit records
  context-intelligence/         hook-written per-session output
```

This is an invariant: ALL per-session state, including transcripts, metadata, audits, hook output,
and any future per-session artifact, lives under this one root. There is no second tree and no split
between "user data" and "operational metadata". A future hook or engine surface that needs to write
per-session data composes its path under this root. A split tree was rejected because it means a
second naming convention engineers must remember plus a complicated migration, with no offsetting
benefit.

## Cross-workspace resume fallback

Resuming a session id first looks under the current workspace. On a miss it searches every workspace
under `<state_root>/workspaces/*/sessions/<id>/` and uses the first match, logging at INFO which
workspace it was found in. Users do not need to remember which workspace a session belonged to. If it
is found nowhere, the resume fails as a missing session. Presence is determined by
`transcript.jsonl`; `metadata.json` is optional and defaults to `{}`.

## Migrations

Both migrations are USER-INVOKED ONLY, via `amplifier-agent migrate`. Neither runs automatically
anywhere in the engine: not after an update, not on the first turn of a process. Auto-invocation was
removed because it produced log noise during normal operation.

The command runs both, in order:

1. Sessions migration: flat `state/sessions/` to `state/workspaces/_legacy/sessions/`.
2. XDG migration: legacy XDG roots to `~/.amplifier-agent/`.

Both are idempotent, and concurrent invocations are safe: only one performs the work while the others
observe the completed state. A lock file is created if absent, and it is released even if the process
is killed, so a crashed run never strands it. Migration is supported on Unix only; Windows is out of
scope for both.

Sessions migration:

```
trigger   <state_root>/sessions exists and has children
target    <state_root>/workspaces/_legacy/sessions/<id>/
lock      <state_root>/.migration.lock, re-checked after acquiring
per dir   the whole session directory is moved, including audits/; if the target already exists,
          a warning is logged, a collision is counted, and the source is left in place
cleanup   the old sessions/ root is removed only if empty afterwards
result    counts of migrated sessions, collisions, and whether the migration was skipped
```

XDG migration:

```
sentinel  <home>/.migrated_from_xdg, re-checked under the lock
lock      <home>/.migration.lock
moves     $XDG_STATE_HOME/amplifier-agent/  (or ~/.local/state/amplifier-agent/) -> <home>/state/
          $XDG_CACHE_HOME/amplifier-agent/  (or ~/.cache/amplifier-agent/)       -> <home>/cache/
          $XDG_CONFIG_HOME/amplifier-agent/ (or ~/.config/amplifier-agent/)      -> <home>/config/
skip      any source whose target subdir already exists; counted as a collision, source left alone
```

The sentinel is written ONLY on full completion, recording the timestamp and the moved source paths,
so a partial failure leaves it absent and the next invocation retries the remaining moves. Runs at
most once per `$AMPLIFIER_AGENT_HOME`. The sentinel filename is `.migrated_from_xdg`, not
`.migrated`; there are two migrations and the sentinel must not be ambiguous.

No data deletion. Sessions and directories are MOVED, never deleted. On a target collision the source
stays in place and the collision is counted and logged at WARNING.

`amplifier-agent migrate --output json` emits:

```json
{
  "sessions_migration": { "migrated": 0, "skipped": true, "collided": 0 },
  "xdg_migration":      { "migrated": 0, "skipped": true, "collided": 0, "from_xdg": true }
}
```

Exit 0 on success, including the nothing-to-do case. Exit 1 on either migration failing, with
`{"error": "sessions-migration-failed: ..."}` or `{"error": "xdg-migration-failed: ..."}` in JSON
mode and a message on stderr in text mode.

Logging cadence:

```
Migration started                            INFO     per-process, once
N sessions migrated, M collisions            INFO     per-process, once
Migration skipped (nothing to migrate)       DEBUG    per-process, once
Resume found session in different workspace  INFO     per-resume
Migration collision (target exists)          WARNING  per-session / per-directory
```

## Non-goals

- No automatic migration. `amplifier-agent migrate` is the sole entry point for both.
- No data deletion. Migration moves; collisions leave the source in place.
- No migration support on Windows.
- No session-state schema version and no validation of old state. Resume reads whatever JSONL and
  JSON it finds.
- No storage-backend abstraction. Filesystem only. The session-storage capability seam is identified
  but not implemented; it gets registered when, and not before, a second backend ships.
- No multi-dimensional scope keys (`tenant`, `user`). A single opaque string only. An adapter needing
  hierarchy encodes it in the string (`acme:my-app:main`) and amplifier-agent does not parse it. New
  organizational dimensions arrive as NEW session-config keys, never by splitting or reinterpreting
  `workspace`.
- No workspace listing or discovery API, and no `amplifier-agent workspaces list`. The filesystem
  layout is the API.
- No per-workspace configuration overrides. Workspace is identity-only, not a config scope.
- No backend-agnostic resume contract. Resume is filesystem-only.
- No `--legacy-layout` compatibility flag.
- No per-request workspace override in server mode.
- No XDG influence on live paths. XDG variables are read only by the one-shot legacy migration.

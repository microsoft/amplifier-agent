# Foundation cache ownership

amplifier-agent operates entirely from its own `~/.amplifier-agent` tree. This
document records how the module-acquisition path gets there, why it did not
before, and what the update mechanism does end to end.

## The problem this replaces

`~/.amplifier` is owned by **amplifier-app-cli** — a different application, with
a different release cadence and a cache design this project is deliberately
moving away from. Until this change, every `amplifier-module-*` git clone that
amplifier-agent triggered was written into `~/.amplifier/cache`.

Nothing in this repository asked for that. The coupling was created by an
*absent* argument. `bundle/loader.py` calls:

```python
bundle = await load_bundle(f"file://{target}")
...
prepared = await bundle.prepare(install_deps=install_deps)
```

Neither call passes a cache root, so foundation applied its own default.
Foundation resolves every location it owns through `get_amplifier_home()`
(`amplifier_foundation/paths/resolution.py:136-152`):

```python
env_home = os.environ.get("AMPLIFIER_HOME")
if env_home:
    return Path(env_home).expanduser().resolve()
return (Path.home() / ".amplifier").resolve()
```

amplifier-agent never set `AMPLIFIER_HOME` — `grep -rn "AMPLIFIER_HOME" .`
returned zero matches before this change — so the fallback applied on every
machine.

This is worth stating plainly because it explains why the problem persisted:
**there was no `.amplifier` string in this repository to find.** A repo-wide
search for the literal returns 94 hits, and every one of them is skills/modes
discovery or an e2e fixture. The actual dependency was invisible to exactly the
kind of search you would run to look for it.

PR #141 confirmed the location while treating a symptom. Its helper is explicit:

```python
def _module_clone_root() -> Path:
    """Mirrors foundation's own default (``~/.amplifier/cache``).  This is *not*
    persistence.cache_root(), which is amplifier-agent's own tree -- module clones
    belong to foundation and are shared with other Amplifier apps on the same machine."""
    return Path.home() / ".amplifier" / "cache"
```

That PR deleted stale clones in place. It did not relocate them, and it accepted
the shared-ownership premise. This change rejects that premise.

## Where `.amplifier-agent` lives and how the path resolves

Unchanged, and already correct before this work:

| Root | Resolves to |
|---|---|
| `amplifier_agent_home()` | `$AMPLIFIER_AGENT_HOME`, else `~/.amplifier-agent` |
| `cache_root()` | `<home>/cache` |
| `config_root()` | `<home>/config` |
| `state_root()` | `<home>/state` |
| `prepared_bundle_dir()` | `<home>/cache/prepared/<version>` |

This change adds one sibling:

| Root | Resolves to |
|---|---|
| `foundation_home()` | `$AMPLIFIER_AGENT_FOUNDATION_HOME`, else `<home>/foundation` |
| `module_cache_root()` | `<foundation_home>/cache` |

`foundation/` is a sibling of `cache/`, not a child of it, so the ownership
boundary is legible on disk: everything below `foundation/` is written by
foundation's resolver on foundation's schedule; everything beside it is written
by this application.

## How modules are fetched from git

Unchanged — foundation does it, using its documented public contract. This
change does not patch, wrap, or fork foundation's source resolution. It only
tells foundation *where* to work, by setting `AMPLIFIER_HOME` to
`foundation_home()` before `amplifier_foundation` is imported.

The bind lives in `amplifier_agent_lib/foundation_home.py` and is invoked from
the package `__init__` of both `amplifier_agent_lib` and `amplifier_agent_http`.

### Why the bind must precede import

`amplifier_foundation/session/finder.py:36` computes a module-level constant at
import time:

```python
DEFAULT_SESSIONS_ROOT: Path = Path.home() / ".amplifier" / "projects"
```

and `session/__init__.py:128` imports `finder` unconditionally. A bind applied
after that import would be too late for anything that constant feeds. Package
`__init__` is the earliest point that reliably runs before any
`amplifier_foundation` import in this codebase — all of which are either
function-local or in submodules.

### Why the bind is unconditional

`bind()` overwrites an inherited `AMPLIFIER_HOME` rather than deferring to it.
A user who exported `AMPLIFIER_HOME` to steer amplifier-app-cli would otherwise
silently re-couple amplifier-agent to app-cli's cache — reintroducing this exact
bug, and doing so only on the machines of users most likely to have customised
their setup.

Two supported overrides remain, both honoured:

- `$AMPLIFIER_AGENT_FOUNDATION_HOME` — relocate this subtree only.
- `$AMPLIFIER_AGENT_HOME` — relocate the whole application tree.

## How pinning is expressed

Unchanged, and deliberately floating. Every module source in `bundle.md` is
declared at `@main`:

```yaml
providers:
  - module: provider-anthropic
    source: git+https://github.com/microsoft/amplifier-module-provider-anthropic@main
```

`docs/spec/bundle-and-cache.md` records this as an explicit non-goal: *"Module
sources are not pinned to tags or SHAs. Pinning would gate amplifier-agent
releases on module-repo state."* Upstream module updates are intended to flow
automatically.

## How the cache is keyed, invalidated, and refreshed

**Keyed** by foundation, unchanged: each clone lands at
`<module_cache_root>/<repo-name>-<sha256(git_url@ref)[:16]>`.

**The invalidation problem.** Because the key includes the ref and the ref is
the floating string `main`, a floating-ref clone owns exactly one stable
directory. Foundation's `resolve()` returns an existing clone whenever it is
present and structurally intact — no fetch, no ref comparison, no commit check.
That directory is therefore written once, at first install, and pinned to
whatever `main` pointed at that day for the life of the machine.
`uv tool install --reinstall --force` does not help: it empties the tool venv
and genuinely reinstalls every module, but each one rebuilds from the same
frozen clone.

**Refreshed** by deleting the clone, which is the only lever that works — and
which this change makes safe. Deleting entries under `~/.amplifier/cache` was a
cross-application side effect on a directory this app did not own. Deleting
entries under `<home>/foundation/cache` is housekeeping in its own tree.

The upstream behaviour is unchanged and still affects every app that consumes
modules this way. This document does not claim to fix it; it claims ownership of
the directory so the workaround is legitimate.

## What the update command does, end to end

`amplifier-agent update` (`admin/update.py`):

1. Resolve the target ref — `--tag`, or `tag_name` from the GitHub releases API.
2. Detect the install method from PEP 610 `direct_url.json`. Only `uv-tool`
   proceeds; `editable` and `other` print the equivalent manual command and exit.
3. Run `uv tool install --reinstall --force git+https://github.com/microsoft/amplifier-agent@<tag>`.
4. Run `amplifier-agent-post-install`.

`amplifier-agent-post-install` (`post_install.py`):

1. If the prepared-bundle cache for the running version exists **and** its
   manifest exists, print `cache already prepared` and return. This is the
   idempotence gate.
2. Otherwise — a cold cache, meaning either a fresh install or a version change
   — delete every `amplifier-module-*` directory under `module_cache_root()`.
3. Prepare the bundle, which re-clones each module at current `main` and caches
   the prepared artefact.

Ordering matters: step 2 only *removes* clones; step 3 is what re-creates them.

The refresh is not gated behind a one-shot migration marker. It does not need to
be: it is only reachable on a cold cache, which is precisely when a refresh is
wanted, and the version-keyed prepared-bundle cache already provides the
idempotence. A marker would add a second mechanism that can silently stop firing.

## Migration for existing installs

None required, and none performed.

Existing clones under `~/.amplifier/cache` are **left alone**. They belong to
amplifier-app-cli, and deleting them is the cross-application side effect this
change exists to stop. On first run after upgrading, `module_cache_root()` is
empty, so foundation clones every module fresh into amplifier-agent's tree.

The one-time cost is a full re-clone on the first prepare after upgrade — the
same cost PR #141 imposed deliberately, arrived at here as a consequence of
correct ownership rather than as a wipe.

Users who want the disk space back can remove `~/.amplifier` themselves if they
do not also run amplifier-app-cli. This project will not do it for them.

## Verification

`amplifier-agent doctor` gains `foundation isolation`, which fails if
`AMPLIFIER_HOME` is unset, points somewhere other than `foundation_home()`, or
resolves inside `~/.amplifier`.

This is checked at runtime rather than asserted in a unit test because the
property that matters is about the installed process's environment, which is
where the import-order hazard lives. The regression this guards against is
silent by construction: if the bind stops running, foundation falls back to
`~/.amplifier`, every clone returns to app-cli's tree, and nothing else in the
system reports a problem.

`amplifier-agent config show` surfaces `foundation_home` and
`module_cache_root` for the same reason.

## Known residuals

Three places in foundation hardcode `Path.home() / ".amplifier"` and bypass
`AMPLIFIER_HOME`. They are recorded here rather than fixed, because fixing them
belongs in an upstream foundation PR:

| Location | What | Reachable from amplifier-agent? |
|---|---|---|
| `registry.py:453` | `cache_root` used as a directory-walk stop boundary in `_load_single` | Yes — `load_bundle` is defined in `registry.py`. Only consulted when `resolved.source_root` is falsy, which is not the normal path. |
| `session/finder.py:36` | `DEFAULT_SESSIONS_ROOT`, module-level | Evaluated at import (amplifier-agent imports `amplifier_foundation.session` for `diagnose_transcript`/`repair_transcript`), but never consulted: this app passes explicit transcript paths. |
| `configurator/_state_manager.py:756` | `settings.yaml` location | No — amplifier-agent imports five symbols from foundation and none reach the configurator. |

None of the three causes a write to `~/.amplifier` on amplifier-agent's runtime
path. The isolation check in `doctor` and the filesystem assertion in the DTU
verification both confirm this empirically rather than by inspection.

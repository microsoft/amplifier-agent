# Install and Distribution

## Scope

How amplifier-agent gets onto a machine and stays current: `install.sh`, `amplifier-agent update`,
the post-install cache prime, the `version --json` probe, the publish paths, and what survives an
upgrade. It does not cover the CLI flag surface (see `cli.md`), the envelope (see
`envelope-and-errors.md`), or the on-disk state layout (see `storage-and-workspace.md`).

## The supported install channel is git

`install.sh` runs `uv tool install` against `git+https://github.com/microsoft/amplifier-agent@<TAG>`.
That is the path that CI tests, the path the DTU e2e harness exercises (see `docs/E2E_TESTING.md`),
and the path `amplifier-agent update` reproduces.

PyPI artifacts are published and `pip install amplifier-agent` will resolve. Nothing in this repo
tests that path, and no documented workflow uses it. Treat PyPI as a published-but-unexercised
artifact, not a second supported channel.

## `install.sh`

Canonical invocation:

```bash
curl -fsSL https://raw.githubusercontent.com/microsoft/amplifier-agent/main/install.sh | bash
```

Pinned:

```bash
curl -fsSL https://raw.githubusercontent.com/microsoft/amplifier-agent/main/install.sh | bash -s -- --tag v0.9.0
```

The command it ultimately runs:

```bash
uv tool install --reinstall --force \
    --from "git+https://github.com/microsoft/amplifier-agent@${TAG}" \
    amplifier-agent
```

### Flags

```
--tag <ref>     tag, branch, or commit. Also accepts --tag=<ref>. Empty value is an error.
--no-prime      skip the post-install bundle cache prime.
--yes           skip the confirmation prompt.
--help, -h      print usage, exit 0.
```

Any other argument is a hard error: `error: unknown flag: <arg>`, exit 1.

### Prerequisites

Checked in order, each failing loud with a remediation line:

```
bash >= 3.2     (macOS ships 3.2; the script uses bash-specific syntax)
curl            required for the releases API call
uv              required for the install itself
```

The script NEVER bootstraps `uv`. On a missing `uv` it prints the astral install one-liner, tells
the user to open a new shell, and exits 1. Silently installing a package manager on a user's
machine from a piped script is not something this installer does.

### Tag resolution

With no `--tag`, the default is resolved from
`https://api.github.com/repos/microsoft/amplifier-agent/releases/latest`. An empty result is a hard
error pointing at the releases page. There is no fallback to `main`.

### Confirmation gate

The "Press Enter to continue, Ctrl+C to abort" prompt appears only when `--yes` was not passed AND
stdin is a TTY. Under `curl | bash` stdin is the pipe, so the prompt is skipped automatically. It
exists for the download-then-run flow.

### Exit codes

```
0   success, or --help
1   unknown flag; --tag with an empty value; bash too old; curl missing; uv missing;
    latest-tag resolution failed; `uv tool install` failed
```

A failed bundle prime does not fail the install: it prints a warning naming
`amplifier-agent prepare` as the retry and continues.

### Output banner

Stable, and worth treating as a contract for anything scraping installer output:

```
=======================================================================
  amplifier-agent <TAG> installed successfully!
=======================================================================

  Install location:  <uv tool dir>/amplifier-agent
  Run:               amplifier-agent --help
  Update:            amplifier-agent update
  Uninstall:         uv tool uninstall amplifier-agent
                     rm -rf ~/.amplifier-agent  # optional: removes cached data
```

### What install.sh does not do

No OS or architecture detection: `uv` handles the wheel and interpreter resolution. No checksum or
signature verification of anything it downloads, including itself. No distinction between a fresh
install and an upgrade; they are the same operation. No rollback: if an install lands a broken
version, recovery is re-running with an earlier `--tag`.

## `amplifier-agent update`

```
--check              show status only, do not install. NOT --check-only.
--tag REF            install a specific tag/branch/SHA; short-circuits the GitHub API call.
--force              reinstall even when the versions match.
--output text|json   default text.
```

### Install-method detection

The installed CLI is classified into one of three install methods, reported as `install_method`:

```
editable   installed from a local source tree in editable mode
uv-tool    installed as a uv tool
other      neither
```

Behavior per method:

```
editable   REFUSED. Prints the editable source path and "git pull && uv sync". Exit 2.
other      Prints the equivalent manual `uv tool install` command. Does not run it. Exit 0.
uv-tool    Runs `uv tool install --reinstall --force git+<repo>@<tag>`, then runs
           `amplifier-agent-post-install`. Exit 0 on success, 2 on a non-zero uv exit.
```

### JSON output

Seven keys, always all seven:

```json
{
  "current": "0.12.0",
  "latest": "0.12.1",
  "tag": "v0.12.1",
  "release_url": "https://github.com/microsoft/amplifier-agent/releases/tag/v0.12.1",
  "install_method": "uv-tool",
  "action": "updated",
  "error": null
}
```

`action` values: `checked`, `skipped_editable`, `skipped_other`, `skipped_up_to_date`, `updated`,
`failed`.

On a GitHub API failure the payload carries `latest`, `tag`, `release_url` as `null`,
`action: "failed"`, and `error: "github-api-unreachable: <exc>"`.

### Exit codes

```
0   --check; up-to-date without --force; install_method == other; a successful update
2   --tag with an empty value; GitHub API unreachable; editable install refused;
    `uv tool install` exited non-zero
```

### Version comparison semantics

Versions are compared by stripping a leading `v` and truncating each dotted segment at its first
non-digit, so `0.5.0rc1` compares as `0.5.0`. The comparison is equality, not ordering.

Consequences worth stating plainly:

- `--tag v0.12.0` when 0.12.0 is already installed is a no-op. `--force` is the only way through.
- A non-numeric ref such as `--tag main` never equals the current version and therefore reinstalls
  on every invocation. That is the intended way to track a branch.
- Because comparison is equality, a `latest` older than `current` is treated as "different" and
  triggers a downgrade install. There is no "you are ahead of the release" branch.

No rollback exists here either.

## Post-install cache prime

`amplifier-agent-post-install` is a separate command installed alongside the CLI. Contract:

- Optional. `install.sh --no-prime` skips it; nothing else requires it.
- Idempotent: returns immediately when both the cache dir and its `manifest.json` exist.
- Never fails an install. Every failure is swallowed; it always exits 0.
- All output on stderr, so it cannot corrupt a piped stdout.

What is lost by skipping it: the first `amplifier-agent run` pays the cold prepare (bundle manifest
resolution, module clone, dependency install, cache write), which `install.sh` describes as a 30-60
second delay.

`amplifier-agent prepare` does the same work but is NOT the same contract: it prints a traceback
and exits 1 on failure. Use `prepare` when you want the failure to be visible; use `post-install`
when a failure must not break the surrounding install.

## `version --json`

```json
{"version": "0.12.0", "protocolVersion": "0.3.0"}
```

Exactly two keys. This is the wrapper pre-spawn probe: both SDKs run `<binPath> version --json`
once during init and parse the result before constructing a session.

Two hard dependencies follow. It is on the latency path of every wrapper init, so it must stay a
fast, import-light command. And its stdout must stay clean: any stray write on this path breaks
JSON parsing for every wrapper-driven host.

## Distribution

Three tag namespaces, each publishing a different artifact:

```
v*          -> amplifier-agent to PyPI via OIDC trusted publishing
            -> GitHub Release with generated notes

py-v*       -> amplifier-agent-py to PyPI via OIDC
            -> NO GitHub Release

wrapper-v*  -> amplifier-agent-ts to npm via OIDC trusted publishing with automatic provenance
            -> GitHub Release with generated notes
```

A tag whose name contains `-` is auto-marked a prerelease.

Each tag-driven publish verifies that the tag matches the packaged version and fails the run on a
mismatch (`v*` against the engine version, `py-v*` and `wrapper-v*` against their respective
wrapper versions). A manually dispatched publish is not subject to that check and can publish
whatever version is on the default branch under no tag correspondence.

`amplifier-agent-client-ts` is unpublished workspace scaffolding. No workflow publishes it, and no
remediation should name it.

## Versioning and compatibility

Four artifacts version independently:

```
amplifier-agent          0.12.0    engine, the release truth
amplifier-agent-ts       0.7.0     TypeScript wrapper SDK
amplifier-agent-py       0.3.0     Python wrapper SDK
protocol version         0.3.0     declared by the engine and pinned by each wrapper
```

Only the protocol version couples them. The engine reports its own version from installed package
metadata.

### The compatibility rule

**Strict string equality on the protocol version.** There is NO support window, NO N-1 policy, and
NO compatibility matrix. `0.3.0` and `0.3.1` are as incompatible as `0.3.0` and `9.0.0`.

Three independent enforcement points:

```
1. Wrapper pre-spawn probe
   The wrapper compares its pinned protocol version against `version --json` before spawning.
   Bypass: the `allowProtocolSkew` (TS) / `allow_protocol_skew` (Py) spawn parameter.

2. Engine argv validation
   `--protocol-version` differing from the engine's protocol version emits
   protocol_version_mismatch, classification protocol, exit 2.
   Bypass: host_config.allowProtocolSkew.

3. Engine boot
   An initialize protocolVersion differing from the engine's raises protocol_version_mismatch.
   Bypass: allowProtocolSkew in the initialize params, sourced from the host config.
```

The only override is `allowProtocolSkew: true` in the host config file. The
`AMPLIFIER_AGENT_ALLOW_PROTOCOL_SKEW` environment variable is honored nowhere and stays dead.

## Upgrade and backwards compatibility

### Prepared-bundle cache

The cache key is version-keyed and content-keyed, so an engine version bump or a `bundle.md` edit
can never serve a stale artifact to a new build. The corollary is accumulation: every engine
version and every bundle edit leaves its own cache directory behind, nothing garbage-collects them,
and reclaiming the space is a manual `amplifier-agent cache clear`. The key shape and the
corruption-is-a-miss rule are specified in `bundle-and-cache.md`.

### On-disk session state

There is no schema version on session state, and nothing validates it on load. It survives upgrades
because no code checks it, not because anything guarantees compatibility. A transcript written by
one version is read by any other without complaint. A shape change surfaces as a runtime error at
replay time, not as a migration prompt.

### Migrations

Two migrations exist: flat sessions to nested workspaces, and XDG dirs to `~/.amplifier-agent/`.

- **User-invoked only.** `amplifier-agent migrate` is the sole entry point. No startup path, and
  neither `doctor` nor `update`, triggers them.
- The XDG sentinel is `<home>/.migrated_from_xdg`, not `.migrated`. It is written only on full
  completion, so a partial failure leaves it absent and the next invocation retries the remaining
  moves.
- Concurrent invocations are safe. Only one performs the work; the others observe the completed
  sentinel and do nothing.
- Neither destroys data: when a target already exists the legacy source is left in place and
  counted as a collision.
- **Unix only.** Windows is unsupported for migration.

Nothing detects or warns about a legacy on-disk layout. The engine runs against one without
complaint, so `migrate` has to be discovered by the operator some other way.

## Non-goals

- Checksum or signature verification anywhere in the install path.
- Rollback, in `install.sh` or in `update`. Recovery is `--tag <older>`.
- Automatic migration on startup or upgrade. This is a deliberate contract, not an omission.
- Cache garbage collection.
- Any protocol compatibility window.
- Publishing `amplifier-agent-client-ts`.

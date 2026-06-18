# Installation

## Requirements

- **Python ≥ 3.12** (the package requires 3.12; the engine internals require 3.11 minimum).
- **[uv](https://docs.astral.sh/uv/)** — the recommended installer.
- **Network access** at install time. First `run` downloads the bundle's module dependencies from GitHub.
- **A provider API key** — see [Environment variables](environment-variables.md).

## Recommended install: `uv tool install`

```bash
uv tool install --from git+https://github.com/microsoft/amplifier-agent amplifier-agent
```

This installs the `amplifier-agent` binary and a sub-binary `amplifier-agent-post-install` on your PATH (typically `~/.local/bin`). Confirm:

```bash
$ which amplifier-agent
/Users/you/.local/bin/amplifier-agent
$ amplifier-agent --version
amplifier-agent, version 0.5.2
```

### Why uv?

The package is published on GitHub, not PyPI. `uv tool install` clones the git repo, builds the wheel with `hatchling`, and installs it into an isolated environment with its own Python. You don't need to manage a virtualenv yourself.

### Editable install (development)

If you have a local clone:

```bash
uv tool install --editable /path/to/amplifier-agent
```

Changes to source under `/path/to/amplifier-agent/src/` take effect immediately on the next `amplifier-agent` invocation. The `update` command detects the editable install and warns instead of clobbering:

```bash
$ amplifier-agent update --check
Checking latest amplifier-agent release...
  Current:  0.5.2
  Latest:   0.5.2  (v0.5.2 from 2026-06-09T04:02:00Z)
  Install:  editable
```

## What gets installed

The wheel contains the CLI (`amplifier_agent_cli/`) and the library (`amplifier_agent_lib/`), plus four files vendored as data:

- The bundle manifest (`amplifier_agent_lib/bundle/bundle.md`).
- The four sub-agent definitions (`explorer.md`, `planner.md`, `coder.md`, `tester.md`).
- The wire protocol spec (`amplifier_agent_lib/protocol/spec.md`).

Everything else (the orchestrator, provider, hooks, tools — see `bundle.md`) is **not** in the wheel. They are git-cloned and pip-installed lazily into a per-version cache on first invocation. This is why the first `run` is slow (cold cache) and subsequent runs are fast.

## Post-install warmup

To pre-warm the bundle cache (e.g., in a CI image or container):

```bash
amplifier-agent prepare
```

Or, equivalently, the entry point installed by the wheel:

```bash
amplifier-agent-post-install
```

Both clone and install the bundle modules into `~/.amplifier-agent/cache/prepared/<version>/<sha>/` and pickle the prepared bundle for reuse. The post-install variant swallows errors so a failed prep doesn't break the install; the `prepare` subcommand exits non-zero on failure so you can gate CI on it.

## Storage locations

By default, all per-user state lives under `~/.amplifier-agent/`:

| Directory | Contents |
|---|---|
| `~/.amplifier-agent/cache/prepared/<version>/<sha>/` | Pickled prepared bundle, manifest |
| `~/.amplifier-agent/config/` | Reserved for future host config |
| `~/.amplifier-agent/state/workspaces/<ws>/sessions/<id>/` | Transcripts, metadata, audits, observability events |

Override the entire root with `AMPLIFIER_AGENT_HOME`:

```bash
export AMPLIFIER_AGENT_HOME=/var/lib/amplifier-agent
```

This relocates `cache`, `config`, and `state` together. See [Environment variables](environment-variables.md#amplifier_agent_home) for caveats.

## Updating

The `update` subcommand wraps `uv tool install --reinstall --force` with version detection:

```bash
$ amplifier-agent update --check         # Show status only, do not install.
$ amplifier-agent update                  # Install the latest tagged release.
$ amplifier-agent update --tag v0.5.2     # Install a specific tag, branch, or SHA.
$ amplifier-agent update --force          # Reinstall even when versions match.
$ amplifier-agent update --output json    # Emit JSON for scripts.
```

`update` detects how amplifier-agent was installed:

- `uv-tool` (the normal case) — `uv tool install --reinstall --force git+...@<tag>`.
- `editable` — refuses to reinstall (it would clobber your checkout). Pass `--force` if you really mean it.
- `other` — anything `update` can't classify (e.g. pip install from PyPI). Falls back to `uv tool install` if possible.

After a successful update, `update` invokes the XDG-to-`~/.amplifier-agent/` migration once. Storage that lived under `~/.local/state/amplifier-agent/`, `~/.cache/amplifier-agent/`, and `~/.config/amplifier-agent/` (pre-0.5.x layouts) is moved into `~/.amplifier-agent/state/`, `~/.amplifier-agent/cache/`, and `~/.amplifier-agent/config/`. A sentinel at `~/.amplifier-agent/.migrated_from_xdg` ensures the migration runs only once.

## Uninstall

```bash
uv tool uninstall amplifier-agent
```

This removes the binary and the tool's virtualenv. It does **not** delete `~/.amplifier-agent/` — transcripts, caches, and audits persist. To remove them:

```bash
rm -rf ~/.amplifier-agent/
```

If you set `AMPLIFIER_AGENT_HOME`, remove that path instead.

## Verifying a fresh install

```bash
amplifier-agent doctor             # Cross-check everything the engine needs.
amplifier-agent verify              # Verify the installation invariants.
amplifier-agent verify --check-hooks # Confirm wire-event coverage.
amplifier-agent version --json      # Machine-readable version pair.
```

`doctor` is the one to run if anything seems wrong. It checks Python version, bundle integrity, writability of cache/config/state, MCP module import, and approval-provider contract. See [CLI reference: doctor](cli-reference.md#doctor) for a full breakdown.

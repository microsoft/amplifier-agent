# CLI reference

Every command runs one turn or one query and exits. Nothing here starts a daemon except `serve`.

Normative contract: [`spec/cli.md`](spec/cli.md).

## run

```bash
amplifier-agent run [OPTIONS] PROMPT
```

| Flag | Type | Purpose |
|---|---|---|
| `--session-id` | str | Session ID for continuity |
| `--workspace` | str | Workspace name for isolating session state |
| `--resume` | flag | Resume from saved transcript |
| `--fresh` | flag | Discard saved state and start over |
| `--config` | path | Host config file |
| `--cwd` | path | Working directory for the agent. Defaults to the launch directory, which is what makes `<launch-dir>/.amplifier/modes` discoverable |
| `--mode` | str | Per-turn mode to activate (non-sticky) |
| `-y` / `-n` | flag | Auto-approve or auto-deny all approval requests (mutually exclusive) |
| `--protocol-version` | str | Wrapper's pinned protocol version; engine validates match |
| `--output` | text \| json | stdout mode (default `text`) |
| `--display` | text \| ndjson | stderr mode (default `text`) |

## Output and display modes

Two independent flags govern what goes where:

| Flag | Controls | Values | Default |
|---|---|---|---|
| `--output` | **stdout** | `text` (reply only) \| `json` (full envelope) | `text` |
| `--display` | **stderr** | `text` (human-readable summaries) \| `ndjson` (one JSON-RPC notification per line) | `text` |

Wrappers always pass `--output json --display ndjson` explicitly. Humans typically want the defaults. `--verbose`, `--debug`, and `--quiet` further tune the human-readable stderr stream and are ignored under `--display ndjson`.

## Session continuity

```bash
# First turn
amplifier-agent run -y --session-id chat-42 "My favorite color is blue."

# Continue the conversation
amplifier-agent run -y --session-id chat-42 --resume "What did I say my favorite color was?"

# Start fresh in the same session ID (overwrites prior transcript)
amplifier-agent run -y --session-id chat-42 --fresh "Start over."
```

`--resume` and `--fresh` are mutually exclusive; passing both exits with `Error: --resume and --fresh are mutually exclusive`.

Sessions persist as transcript JSONL under `$AMPLIFIER_AGENT_HOME/state/workspaces/<workspace>/sessions/<session-id>/`. Continuity is per `(workspace, session-id)`. Pass `--workspace <name>` to isolate session state by project; without it, sessions are scoped to the current working directory.

## Skills and modes

Skills are invocable workflows. Modes are per-turn behavioral overlays.

```bash
amplifier-agent skills list                  # table on a TTY, JSON when piped
amplifier-agent skills list --json           # force JSON (built-in: code-review, council)
amplifier-agent skills list --config PATH    # also discover skills.skills locations from a host config
amplifier-agent modes list --json            # shipped modes (built-in: plan, brainstorm)

# invoke a skill via the sigil prompt (args after the name flow to $ARGUMENTS)
amplifier-agent run -y '!amplifier:skill code-review'
amplifier-agent run -y '!amplifier:skill council src/auth.py'

# or just ask in plain language; the agent drives the skill load itself
amplifier-agent run -y 'review my staged changes'

# run a single turn under a mode (non-sticky; re-pass to persist, omit to disable)
amplifier-agent run -y --mode plan 'add a multiply function to calc.py'
```

`--output {auto,json,table}` picks the format explicitly (`--json` is shorthand for `--output json`). Each entry carries `source` (the absolute path of the file that won discovery) and `shadowed` (same-named files that lost, empty when there is no collision). Table output marks a conflicted row with `(!)` and prints a footer showing which file `runs:` and which are `shadowed:`.

Discovery order, first match wins:

| | Roots |
|---|---|
| Skills | built-in bundle, then `$AMPLIFIER_SKILLS_DIR`, `./.amplifier/skills`, `~/.amplifier/skills`, then any `--config` `skills.skills` locations |
| Modes | `<cwd>/.amplifier/modes`, `~/.amplifier/modes`, built-in bundle |

The six council lens skills are model-invocable only, not user-invocable, so they do not show up in `skills list`.

An unknown `--mode` is rejected rather than run: exit 2 with `argv_mode_unknown` if the name is simply not found, exit 1 with `modes_unavailable` if mode discovery itself failed. Omitting `--mode` still runs unrestricted.

Normative contract: [`spec/skills-and-modes.md`](spec/skills-and-modes.md).

## serve

```bash
amplifier-agent serve chat-completions
```

The one long-lived command. Exposes an OpenAI-compatible, bearer-authenticated HTTP face. See the [integration guide](INTEGRATION.md#http-face) and [`spec/http-face.md`](spec/http-face.md).

## Admin commands

```bash
amplifier-agent doctor              # Diagnose env, providers, paths, bundle cache
amplifier-agent prepare             # Pre-warm the bundle cache (run once after install)
amplifier-agent verify              # Verify install integrity and hook coverage
amplifier-agent version             # Engine version and wire protocol version
amplifier-agent --version           # Engine version only (Click-standard)
amplifier-agent config show         # Print resolved config with source annotations
amplifier-agent cache clear         # Invalidate the prepared-bundle cache
amplifier-agent migrate             # Migrate legacy storage layouts to current
amplifier-agent providers list      # Provider credential-resolution reporting
amplifier-agent models list         # Enumerate available models from providers
amplifier-agent skills list [--json] # List user-invocable skills
amplifier-agent modes list [--json]  # List shipped modes
amplifier-agent update              # Check for and install the latest release
```

Migrations are user-invoked only. The engine does not check for a legacy on-disk layout at boot and does not auto-migrate; a stale layout is simply not detected, so you have to know to run `migrate` yourself.

## auth

Credential storage and precedence are documented in [CONFIGURATION.md](CONFIGURATION.md#credentials).

```bash
amplifier-agent auth set anthropic sk-ant-...
amplifier-agent auth list
amplifier-agent auth status
amplifier-agent auth remove openai
amplifier-agent auth clear --force
```

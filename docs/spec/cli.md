# CLI Surface

## Scope

The complete `amplifier-agent` command surface: `run` and every admin subcommand, their flags,
defaults, and parse-time rejections. It does not cover the JSON envelope shape or exit-code
semantics (see `envelope-and-errors.md`), the host config file schema (see `host-config.md`), or
install and update mechanics (see `install-and-distribution.md`).

## Dispatch

```
amplifier-agent                 prints help on stdout, exit 0
amplifier-agent --version       prints `amplifier-agent <version>`, exit 0
amplifier-agent <unknown>       exit 2
SIGINT, at any point            writes `\n[info] Interrupted` to stderr, exit 130
```

Subcommands: `run`, `doctor`, `migrate`, `prepare`, `verify`, `version`, `update`, `config`,
`cache`, `models`, `skills`, `modes`, `serve`, `auth`, `providers`.

## `run`

```
amplifier-agent run [OPTIONS] [PROMPT]

  PROMPT                        positional, optional (str)
  --session-id TEXT             default None. Session ID to resume or tag.
  --resume                      flag, default False. Mutex with --fresh.
  --fresh                       flag, default False. Discards saved state. Mutex with --resume.
  --bundle TEXT                 default None. HIDDEN. Accepted and ignored.
  --config PATH                 default None. Host config file path.
  --cwd PATH                    default None. Working directory for the agent.
  -v, --verbose                 flag, default False. --display text only.
  --debug                       flag, default False. --display text only.
  -y, --yes                     flag, default False. Auto-approve. Mutex with -n.
  -n, --no                      flag, default False. Auto-decline. Mutex with -y.
  --quiet                       flag, default False. --display text only. Mutex with -v/--debug.
  --output [text|json]          default "text". Governs STDOUT.
  --display [text|ndjson]       default "text". Governs STDERR. Independent of --output.
  --protocol-version TEXT       default None. Wrapper's pinned version; engine self-validates.
  --workspace TEXT              default None. Session-state isolation slug; defaults to cwd-derived.
  --mode TEXT                   default None. Per-turn mode (non-sticky).
```

Sixteen options, one of them hidden, plus the positional `PROMPT` and `--help`. `--output` and
`--display` values are case-insensitive.

`--bundle` is accepted, hidden from help, and has no effect on the turn. It exists only so that a
caller passing it is not rejected.

### Mutual exclusion, with exact user-facing text

All three print the message on stderr and exit 2.

```
-y with -n                    -> "-y and -n are mutually exclusive"
--quiet with -v or --debug    -> "--quiet conflicts with -v/--verbose and --debug;
                                 choose one verbosity tier"
--resume with --fresh         -> "--resume and --fresh are mutually exclusive"
```

`--output text` with `--display ndjson` is explicitly NOT a conflict. They govern different
streams. Do not add a check for it.

### Prompt discipline

```
PROMPT omitted, stdin IS a TTY      -> stderr: "Missing argument 'PROMPT'."            exit 2
PROMPT omitted, stdin NOT a TTY     -> stderr line, exit 2:
    [error] prompt_required: pass prompt as argument: `amplifier-agent run "..."`.
```

The non-TTY branch writes a bare stderr line, not an envelope.

### Verbosity and approval resolution

Verbosity is a strict ladder: `debug` > `verbose` > `quiet` > `normal`. It affects the human-facing
`--display text` renderer only. Under `--display ndjson` the verbosity flags have no effect on the
event stream.

Approval mode precedence: `-y`/`-n` on argv, then `approval.mode` in the host config, then
`"prompt"` when stdin is a TTY. A non-interactive invocation with no policy at any tier is a hard
failure, not a silent deny (see `envelope-and-errors.md`).

### `--resume` / `--fresh` defaults

Both absent is fresh-but-non-destructive: a new turn, no transcript load, no transcript deletion.
`--fresh` with a `--session-id` deletes that session's stored state under the resolved workspace
before the turn runs. An unknown `--mode` is rejected before that deletion, so a rejected turn
never destroys state.

### Process group

`run` makes itself a process-group leader before doing any work, so every child process it starts
(MCP servers in particular) belongs to that group. A caller that cancels a turn by signalling the
group terminates the children with the engine; children do not outlive the run and are not left
orphaned. When the environment already owns the session (a debugger, a test harness), the setup is
skipped and the turn proceeds normally.

`AMPLIFIER_AGENT_DEBUG_SIDLOG=1` writes one diagnostic line, `engine-sid-ok pid=<..> sid=<..>`, to
stderr when the setup succeeds.

## Admin subcommands

Every admin command writes its payload to stdout and diagnostics to stderr. The `list`-style
commands that support `--output auto` resolve `auto` to `table` on a stdout TTY and `json` when
piped.

```
doctor [--strict] [--quick] [--emit-sha]
    Self-diagnostics: Python version, bundle default_provider, writability of config/cache/state
    roots, bundle module presence, routing matrix, approval-provider shape, session-store
    roundtrip, MCP availability, prepared-cache presence. Reports only; never primes.
    --strict  turns a missing prepared cache from [INFO] into [FAIL] (image-build gate).
    --quick   Python version + cache presence only.
    --emit-sha  prints sha256 of each bundle module SOURCE URL, not of its content.
    Exit 1 on any hard check failure, or on a missing cache under --strict. Else 0.

prepare
    Primes the prepared-bundle cache so the first `run` does not pay resolve + clone +
    pip-install. No flags. Prints "[ OK ] bundle cache primed". Exit 1 with traceback on failure.

verify [--check-hooks]
    Without the flag: prints an advisory and exits 0 ("nothing to check"). With it: verifies the
    streaming hook exposes the required canonical wire events.

migrate [--output text|json]
    The SOLE entry point for both storage migrations (flat-sessions -> workspaces, XDG ->
    ~/.amplifier-agent). Idempotent. Exit 1 if either migration raises, else 0.
    JSON payload: {"sessions_migration": {migrated, skipped, collided},
                   "xdg_migration": {migrated, skipped, collided, from_xdg}}

version [--json]
    Plain: `amplifier-agent <version> (wire <protocolVersion>)`.
    --json: {"version", "protocolVersion"}.
    This is the wrapper pre-spawn probe. See install-and-distribution.md.

update [--check] [--tag REF] [--force] [--output text|json]
    Detects the install method and reinstalls from git. See install-and-distribution.md for the
    full contract.

config show [--config PATH]
    Prints resolved configuration as indented JSON to stdout. The payload has four top-level keys
    (unrelated to the host-config file's seven, see host-config.md): `provider` (from the bundle
    default), `host_config` (path + resolution source + parsed values), `skills` (post-merge
    block), `amplifier_agent_home` (value + `env:AMPLIFIER_AGENT_HOME` or `default`). On a config
    parse failure it still reports the resolved path and source so the operator can find the file.

cache clear
    Removes the prepared-bundle cache directory wholesale, under the cache root implied by
    $AMPLIFIER_AGENT_HOME. Idempotent, always exit 0, message on stderr. No flags.

models list [--provider ID] [--output auto|json|table] [--timeout SECONDS] [--latest]
    With --provider: queries one provider. Without: queries every known provider in parallel and
    emits a per-provider aggregate. --timeout defaults to 15.0. --latest restores the provider's
    own filtered subset; the CLI default is the full list. Unknown provider exits 1; a provider
    error exits 2; an empty list exits 0 with an advisory.

skills list [--json] [--output auto|json|table] [--config PATH]
    Lists user-invocable (slash-command) skills. --json wins over --output. --config adds the host
    config's `skills.skills` locations to discovery; a config error prints
    `# skills list: <message>` to stderr and exits 2. A table row whose name also appeared in a
    lower-priority root is marked `(!)` and expanded in a footer.

modes list [--json] [--output auto|json|table]
    Same shape as `skills list`, minus --config.

providers list [--output table|json] [--json]
    Read-only credential-resolution report. Never prints key material: only whether each provider
    resolves and from which source (env / file / default / none). --output defaults to table on a
    TTY, json otherwise. --json is shorthand for --output json.

serve chat-completions [--bind HOST] [--port N] [--api-key KEY] [--workspace SLUG]
                       [--model-id ID] [--config PATH] [--log-level ...]
    Starts the OpenAI Chat Completions wire face (POST /v1/chat/completions, GET /v1/models),
    single process, single worker. Defaults: --bind 127.0.0.1, --port 9099, --log-level info.
    --api-key / --workspace / --model-id fall back to $AMPLIFIER_AGENT_HTTP_API_KEY (else
    `local-dev-secret`), $AMPLIFIER_AGENT_HTTP_WORKSPACE > $AMPLIFIER_AGENT_WORKSPACE >
    cwd-derived, and $AMPLIFIER_AGENT_HTTP_MODEL_ID (else `amplifier`). A nonexistent --config
    path exits 2. The startup banner, including the API key, goes to stderr only.

serve status
    Reads the state file, checks the recorded PID is alive, then probes GET /v1/models over the
    wire. Exit 0 when not running, when a stale state file was cleaned, or when healthy; exit 1
    when the PID is alive but the endpoint does not answer.

serve stop [--force] [--timeout SECONDS]
    SIGTERM, wait up to --timeout (default 5.0), then SIGKILL. --force skips straight to SIGKILL.
    Exit 1 when there is nothing to stop.

serve restart
    Replays the stored launch args (host, port, api-key, workspace, host_config_path), stops the
    old PID, relaunches detached, and waits up to 30 s for a new state file with a different PID.
    No flags. Exit 1 when there is nothing to restart or readiness times out.

auth set PROVIDER [API_KEY] [--stdin] [--endpoint URL]
    Writes ~/.amplifier-agent/credentials.json (mode 0600, atomic write). --stdin reads the key
    from stdin so it never appears in argv. --endpoint carries an Azure-style deployment URL.
    `github-copilot` is refused: it reads its token from the environment. `openai-chatgpt` is
    also refused: it has no static key, authenticating instead via OAuth device-code.

auth list
    Per-provider table: masked value plus source (`env=<VAR>` / `file` / `default` / `not set`).
    Environment variables always outrank the file.

auth remove PROVIDER
    Deletes one file entry. Never touches the environment.

auth status
    Same resolution chain as `auth list`, rendered as a per-provider verdict
    (USING env=... / USING file entry / USING built-in default / NOT SET with remediation).

auth clear --force
    Deletes the whole credentials file. Without --force it prints a warning and exits 2.
```

## Non-goals

Argv surfaces that are not accepted. Each is a live negative contract: callers depend on them not
existing, and introducing one reopens a deliberate decision. Every one of these is rejected as an
unknown option and appears nowhere in `--help`.

```
--provider              -> host_config.provider.module, else bundle.md `default_provider:`
--model                 -> host_config.provider.config.default_model
--effort                -> host_config.provider.config.effort
--mcp-config-path       -> host_config.mcp.configPath, or AMPLIFIER_MCP_CONFIG in the process env
--env-allowlist         -> host config (env allow-listing is a config concern, not an argv one)
--env-extra             -> host config
--allow-protocol-skew   -> host_config.allowProtocolSkew. The corresponding environment
                           variable is honored nowhere.
--skills-dir            -> host_config.skills.skills
--host-capabilities     -> nothing. There is no metadata.hostCapabilities field either.
--mcp-servers           -> host_config.mcp.configPath / AMPLIFIER_MCP_CONFIG
--stdio                 -> nothing. There is no multi-turn stdio transport.
```

Also out of scope, and deliberately so: per-event filtering on `--display ndjson`, schema
versioning of ndjson notifications, and mid-turn cancellation.

The `--output {text,json}` vs `--display {text,ndjson}` naming asymmetry is a known, deliberate
wart. Both wrappers hard-code the literal values; renaming is a wrapper-major-version change.

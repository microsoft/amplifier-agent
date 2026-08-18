# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **ChatGPT provider.** `provider.module: "openai-chatgpt"` is now a valid host-config value,
  backed by `amplifier-module-provider-openai-chatgpt`. It uses a ChatGPT Plus/Pro/Team
  subscription as the backend instead of a per-token API key, talking to the ChatGPT backend
  (Codex API) rather than the public OpenAI API. Default model is `gpt-5.5`.
  Auth is OAuth device-code, not an environment variable: the provider module drives an
  interactive login at mount time (`login_on_mount`, default true) and caches tokens to
  `~/.amplifier/openai-chatgpt-oauth.json`, refreshing them itself. Requires "Sign in with
  device code" enabled in the account's ChatGPT Security settings. Like `github-copilot`,
  `auth set openai-chatgpt` is refused — there is no static key to store.
- **Chat Completions provider.** `provider.module: "chat-completions"` is now a valid
  host-config value, backed by `amplifier-module-provider-chat-completions`. It integrates
  any server speaking the OpenAI Chat Completions wire format
  (`/v1/chat/completions`) — llama.cpp, vLLM, LM Studio, LocalAI, SGLang, TGI, and other
  OpenAI-compatible endpoints — distinct from `openai`, which uses the OpenAI Responses
  API. Its credential is an endpoint, not a key: `CHAT_COMPLETIONS_BASE_URL` (required)
  selects the server, and `CHAT_COMPLETIONS_API_KEY` (optional) is sent only when set,
  since local servers commonly need none. Both are environment-only; the persisted
  credentials file is not consulted for this provider. Default model is `default`.

### Fixed

- **`git` was a silent, undocumented requirement.** The installer checked for
  `uv` and `curl` but not `git`, and neither `README.md` nor `docs/INSTALL.md`
  listed it. `git` is not a build-time nicety: bundles and modules are fetched
  by cloning git repositories, so a machine without `git` on `PATH` fails both
  while priming the cache during install and every subsequent time a bundle is
  mounted. On a bare Windows host -- where `git` is not present by default --
  this surfaced as an opaque clone failure with no statement of the missing
  dependency.

  `README.md` and `docs/INSTALL.md` now list `git` alongside `uv` and `curl`,
  noting that it is needed at run time and that Git for Windows supplies both
  `git` and the `bash` the shell tool looks for. The documentation is what
  carries this on Windows: `install.sh` is a bash script, and on Windows bash
  is itself supplied by Git for Windows, so a user who can run the installer
  necessarily already has `git`. `install.sh` also gained an up-front check
  with a per-platform install hint, which covers the environments where bash
  is present without `git` -- minimal Linux containers, slim CI images, and
  fresh WSL distributions.

- **`amplifier-agent run` crashed with `UnicodeEncodeError` after the turn had
  already completed.** Python picks the console code page for stdio, which on
  Windows is a legacy single-byte encoding (cp1252 on the guests we test), so
  writing a reply containing any character outside that page raised at the
  final write -- after the model call had run and been billed. `main()` now
  reconfigures stdout and stderr to UTF-8 before dispatch, honoring an explicit
  `PYTHONIOENCODING` and skipping streams that are already UTF-8 or cannot be
  reconfigured.

  Not gated on the platform, because the trigger is a stdio encoding that
  cannot represent the payload rather than the OS: POSIX under
  `LC_ALL=C PYTHONCOERCECLOCALE=0 PYTHONUTF8=0` selects ASCII and fails
  identically. On a normal UTF-8 host this is a no-op.

  Two corrections to the original Windows report while confirming this. First,
  the exposed surface is exactly one line, `single_turn.py`'s text-mode reply
  write: the JSON envelope paths go through `json.dumps`, whose default
  `ensure_ascii=True` makes them ASCII by construction; `jsonrpc.write_message`
  encodes to UTF-8 bytes itself; and `click.echo` does its own encoding and
  never raises. Second, an em dash is *not* the trigger -- U+2014 is
  representable in cp1252. Em dashes in `--help` and in skill descriptions
  degrade to a replacement character rather than crashing. What actually
  crashes is a character with no cp1252 mapping, such as U+2713 or CJK.

- **`amplifier-agent run` crashed on Windows with `AttributeError: module 'os'
  has no attribute 'getsid'`.** The SC-B session-leader step at the top of the
  `run` callback called `os.getsid`/`os.setsid` unconditionally, and
  `AttributeError` was not in its `except` tuple. Neither name exists on
  Windows, so every `run` died before reaching any engine code. The step is now
  gated on `hasattr(os, "setsid")` and the debug SIDLOG branch reports `n/a`
  instead of raising. On POSIX the behavior is unchanged: the engine still
  becomes session leader, verified by `AMPLIFIER_AGENT_DEBUG_SIDLOG` reporting
  `sid == pid`.

  This does not give Windows an equivalent guarantee, and pretending otherwise
  would be worse than the crash. Windows has no session groups; the containment
  primitive is a Job Object, a different mechanism on both sides of the wrapper
  boundary. Until that is built, cancellation on Windows reaches the engine but
  MCP children may outlive it. Recorded in `docs/spec/wrapper-contract.md`.

- **Every CLI command failed on Windows with `ModuleNotFoundError: No module
  named 'fcntl'`**, `--version` included. `amplifier_agent_lib/migration.py`
  imported `fcntl` unconditionally, and it sits on the CLI's eager import path
  (`__main__.py` -> `admin/migrate.py` -> here), so a module that only one
  subcommand needs took down every subcommand. The module was always scoped to
  Unix and says so in its own docstring; the defect was expressing that scope
  as a bare import. `fcntl` is now imported optionally and `file_lock` raises
  `MigrationUnsupportedError` when it is absent, so the limitation is enforced
  at call time and stays scoped to the one command that cannot work there.
  `amplifier-agent migrate` on such a platform now exits 1 with
  `{"error": "migration-unsupported: ..."}` rather than crashing the CLI.
  Refusing rather than locking as a no-op is deliberate: the migrations move
  user data and re-check their preconditions under the lock, so running
  unlocked would trade a documented platform limitation for a data-loss race.
  No behavior change on Linux or macOS.

## [0.12.0] — 2026-07-29

### Added

- **`debug.rawLlmPayloads` host-config key captures full raw LLM requests and
  responses.** Set `{"debug": {"rawLlmPayloads": true}}` and the engine sets
  `raw: true` on the mounted provider, which attaches the complete outbound
  request kwargs to `llm:request` and the complete accumulated response to
  `llm:response`; `hook-context-intelligence` writes both verbatim to the
  session's `events.jsonl`. Off by default — a developer diagnostic, not a
  production setting. `debug` is a closed block, and the value must be a real
  JSON boolean: a string is rejected rather than coerced, because every provider
  reads `raw` with a bare `config.get("raw", False)` and `"false"` is truthy.
- **`serve --config` now honours `provider.config`, matching `run --config`.**
  The HTTP face previously dropped it entirely: `_session_runner` clears
  `mount_plan["providers"]` before per-request injection, discarding the overlay
  the lifespan applied, and called `inject_provider` with no `extra_config`.
  `run_chat_turn` now takes a `provider_config` argument that the route derives
  from `app.state.host_config`. This is what lets `amplifier-app-opencode`,
  which spawns the engine with `--config`, enable capture with no change on its
  side.

### Fixed

- **Every CLI command failed with `ModuleNotFoundError: No module named
  'httpx'`** on installs created on or after 2026-07-28, `--version`
  included. `admin/serve_lifecycle.py` imports `httpx` but never declared
  it; it arrived transitively via `mcp`, and `mcp` 2.0.0 switched to the
  separate `httpx2` distribution, which has a different import name. Not a
  regression between releases — v0.9.3 through v0.11.0 are equally
  affected, so rolling back does not help. `httpx` is now declared
  explicitly.

### Notes

- **Enabling capture writes full conversation text to disk.** `redact_secrets()`
  matches by key name only and never scans string values, so prompts, tool
  results, and file contents are captured as-is. No truncation, no size cap.
- **Provider coverage is uneven.** `anthropic`, `openai`, and `azure-openai`
  capture full request kwargs and the full accumulated response, secret-redacted,
  on both stream paths. `ollama` covers both paths but does **not** redact.
  `github-copilot` accepts the flag but emits counts and lengths only — no prompt
  text, no response content, no usage — so it does not deliver what the key
  promises there. Per-provider table in `docs/configuration.md`.

## [0.11.0] — 2026-07-29

### Added

- **GitHub Copilot provider.** `provider.module: "github-copilot"` is now a valid
  host-config value, backed by
  `amplifier-module-provider-github-copilot`. It serves models from several
  vendors through one Copilot seat (Anthropic, OpenAI, Google, xAI families);
  the exact set depends on your plan, so enumerate it with
  `amplifier-agent models list --provider github-copilot` rather than assuming.
  Auth is environment-only: the provider reads
  `COPILOT_AGENT_TOKEN` → `COPILOT_GITHUB_TOKEN` → `GH_TOKEN` → `GITHUB_TOKEN`
  (first non-empty wins), typically `export GITHUB_TOKEN=$(gh auth token)`.
- **Reseller model ids are namespaced `<provider>/<id>`.** Copilot resells models
  that other providers also serve — both it and anthropic serve `claude-sonnet-5`
  and `claude-opus-5` under byte-identical ids. `served_models_registry` is keyed
  on the model id, so without this the provider enumerated last silently captured
  the other's traffic (and `KNOWN_PROVIDERS` order put `github-copilot` last, so
  it always won). Copilot's models are now served as
  `github-copilot/claude-sonnet-5`; native providers keep their bare ids, so no
  existing client, config, or `opencode.json` changes. The namespace is stripped
  before the id reaches the provider module, which only knows its own bare id.
- **`(GitHub)` suffix on Copilot-served model display names.** The human-facing
  half of the same fix: a namespaced id is not what a picker shows. `GET
  /v1/models` and `models list --provider github-copilot` both append the
  suffix. Namespacing and suffixing read the same `RESELLER_PROVIDERS` map, and
  the suffix is applied through one shared helper
  (`provider_sources.decorate_display_name`). The CLI aggregate view (`models
  list` with no `--provider`) is the exception: it prints bare ids and
  distinguishes providers by its `PROVIDER` column instead.
  `amplifier-app-opencode` maps `display_name` onto opencode's per-model
  `name`, which its model dialog renders verbatim, so no client-side change is
  needed.
- **E2E coverage.** `tests/e2e/suites/github_copilot/` exercises single-shot
  replies, multi-turn continuity, and tool calling across three model families
  (Anthropic, OpenAI, Google backends), plus the `(GitHub)` labelling on
  `/v1/models`. A non-xfail guard test verifies `GITHUB_TOKEN` actually reaches
  the DTU and carries Copilot entitlement, so a credential problem surfaces as
  itself rather than as three unrelated-looking failures. The suite requires
  `GITHUB_TOKEN` on the host. Setting it used to perturb unrelated
  provider-enumeration tests, which auto-enabled github-copilot and saw an extra
  provider; those fixtures now clear their env through one shared helper derived
  from `PROVIDER_CREDENTIAL_VARS`, so the whole non-e2e suite passes identically
  with and without `GITHUB_TOKEN` exported.

### Changed

- **`bundle.md` pre-wires five providers instead of four.** Cold-prepare now
  installs `provider-github-copilot` (and pulls `github-copilot-sdk`) for all
  users, whether or not they hold a Copilot seat. The wheel is the only install
  cost — the SDK's ~157 MB CLI binary is fetched lazily on first use — but this
  is a real footprint increase. Making the pre-wired set conditional is open.
- **`auth set github-copilot` is now refused instead of silently succeeding.**
  Stored credentials reach providers through the mount config, and the Copilot
  provider reads only the environment, so the command used to store a token,
  report the provider configured, and change nothing the provider could see. It
  now exits non-zero and points at `COPILOT_AGENT_TOKEN`,
  `COPILOT_GITHUB_TOKEN`, `GH_TOKEN`, `GITHUB_TOKEN` (first non-empty wins)
  instead. A temporary limitation: the real fix is in the provider module, whose
  token resolver needs to read the agent-delivered credential from its config
  before falling back to the environment.

### Notes

- Copilot auth is environment-only for now. `export GITHUB_TOKEN=$(gh auth
  token)`, or rely on an existing `gh` or VS Code login, which the SDK can reuse
  through its cached OAuth.

## [0.10.0] — 2026-07-27

### Added

- **Skills and modes discovery and invocation.** New `amplifier-agent skills
  list [--json]` (user-invocable skills) and `amplifier-agent modes list
  [--json]` (shipped modes) CLI commands, plus `GET /v1/skills` and
  `GET /v1/modes` HTTP routes (both
  `{"object":"list","data":[{name,description,source,shadowed}]}`). CLI and
  HTTP share a single discovery source of truth in
  `src/amplifier_agent_lib/resources.py`.
- **`run --mode <name>`** activates a mode for a single turn (non-sticky:
  re-pass each turn to persist, omit to disable). The active mode is echoed in
  the output envelope as `metadata.activeMode` (`null` when omitted).
- **Skill invocation** via the `!amplifier:skill <name> <args>` sigil prompt
  (args flow to `$ARGUMENTS`) and via plain natural language (the agent drives
  `load_skill` itself). The sigil also dispatches over the HTTP face, gated to
  the final `role=user` message; a sigil replayed from history is re-hydrated
  into the skill's expanded body so it survives the turn boundary.
- **Built-in skills** (`code-review`, `council`, plus 6 council lens skills that
  are not user-invocable) and **modes** (`plan`, `brainstorm`) vendored under
  `src/amplifier_agent_lib/bundle/{skills,modes}/` and force-included into the
  wheel.
- One-shot runs (no `--session-id`) now mint an ephemeral session id so
  telemetry is captured.
- **Testing.** DTU-based E2E suites for skills and modes under
  `tests/e2e/suites/{skills,modes}`, and `amplifier-agent-capabilities`
  evaluation tasks exercising skill invocation (sigil, arguments, configured
  and natural-language discovery, council) and mode activation, persistence,
  and per-turn disable end to end.
- **Skill and mode listings report name collisions instead of hiding them.**
  Every entry from `skills list` / `modes list` / `GET /v1/skills` /
  `GET /v1/modes` carries `source` (absolute path of the file that wins) and
  `shadowed` (list of `{"source": <path>}` for every same-named file that
  lost, empty when there was no collision). Table output (`skills list` /
  `modes list` without `--json`) marks conflicted rows `(!)` and prints a
  footer naming the winning and shadowed paths. Discovery roots are collapsed
  by resolved path first, so a process whose CWD is the home directory
  doesn't report every skill as shadowing itself.
- `amplifier_agent_lib.mode_resolution` — the single source of truth both
  faces use to resolve a mode name (`resolve_mode`, `discover_known_modes`,
  `ModeUnknownError`, `ModeDiscoveryUnavailableError`).

### Changed

- **BREAKING: an unknown `--mode` / mode directive is now rejected instead of
  silently ignored.** Previously both faces logged a warning and ran the turn
  anyway with `active_mode` set to the unresolved name, so every downstream
  reader (the envelope's `metadata.activeMode`, `hooks-mode`, host UIs)
  believed a mode was active while nothing was enforced.

  - Mode name not found (discovery ran, name absent) — CLI exits 2 with
    `error.code = "argv_mode_unknown"`; HTTP returns 400 with
    `code = "unknown_mode"`.
  - Mode could not be verified (discovery itself failed) — CLI exits 1 with
    `error.code = "modes_unavailable"`; HTTP returns 503 with
    `code = "modes_unavailable"`.

  Omitting the mode is unchanged and still runs unrestricted. Callers that
  relied on a bad mode name being ignored must now pass a valid name or omit
  it.

- **BREAKING (HTTP): prompt selection no longer searches backwards for the
  last `role=user` message.** Only a final `role=user` message becomes the
  prompt; a trailing assistant/tool/system/developer message (or no user
  message at all) now yields an empty prompt with the full message array
  treated as continuation history. Clients that relied on an earlier user
  message being re-submitted as the prompt when the array ends in
  non-user content will see an empty-prompt continuation turn instead.

- An unknown skill sigil is not an error. `!amplifier:skill <unknown>` passes
  through to the model as ordinary text, unlike an unknown mode, because a
  sigil arrives inside the user's own prompt rather than a structured channel.

### Fixed

- **HTTP responses now report the mode that actually ran.** `activeMode` was
  hardwired to `null` on every `/v1/chat/completions` response. It is now a
  top-level field on the terminal SSE chunk (`stop` and `tool_calls` alike)
  and on the non-streaming `chat.completion` body — always present, `null`
  when no mode is active, matching the CLI envelope's `metadata.activeMode`.
- Skills and modes discovery are now attempted independently at lifespan;
  previously a failure enumerating modes reset an already-successful
  `available_skills` back to `[]`.
- A rejected turn no longer runs the `--fresh` session-state cleanup. Mode
  validation happens before that `rmtree`, so an invalid mode name cannot
  delete session state.
- **`--cwd` now defaults to the launch directory, so launch-directory modes
  actually activate.** `run` previously passed no working directory when
  `--cwd` was omitted, so the `session.working_dir` capability defaulted to
  the installed bundle directory and `<working_dir>/.amplifier/modes` was
  never searched. The CLI now matches what the wire face and the HTTP session
  runner already did. Skill discovery was unaffected (it resolves against the
  process CWD, not this capability). Passing `--cwd` explicitly is unchanged.

## [0.9.3] — 2026-07-21

### Added

- **`auth set --stdin`.** `amplifier-agent auth set <provider> --stdin` reads
  the API key from stdin instead of argv, so it never appears in the process
  list (`ps`, `/proc/<pid>/cmdline`). The positional `api_key` argument still
  works; passing both or neither is rejected with a clear message.

## [0.9.2] — 2026-07-14

Patch release carrying #86: the HTTP/wire face now reports the host-supplied
working directory instead of the installed bundle directory.

### Fixed

- **Working directory honored on the HTTP/wire face** (#86). `handle_initialize`
  now passes the `InitializeParams.cwd` wire field (falling back to the server
  process working directory) to `create_session`, and `run_chat_turn` uses the
  process working directory. Previously `session.working_dir` defaulted to the
  installed bundle directory, so hosts (e.g. the OpenCode desktop app) reported a
  package-internal path and file-rooted tools resolved relative paths against it.

## [0.9.1] — 2026-07-14

Release cut carrying #82: amplifier-agent now resolves provider credentials
from `credentials.json` at serve startup. This is the floor that
amplifier-app-opencode (>= 0.9.1) and amplifier-app-paperclip rely on for
zero-config provider auth — apps that dropped their own credential handling
require this version.

### Fixed

- **Provider credentials resolved from `credentials.json` at serve startup**
  (#82). Providers configured via `amplifier-agent auth set` are auto-enabled
  when the server starts, so downstream apps no longer need to inject
  `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` env vars to serve a provider.

## [0.9.0] — 2026-06-22

Adds chat-completions session resume + client-authoritative reconciliation. opencode users get zero-config session continuity via the `X-Session-Id` header fallback. The reconciler now runs foundation's transcript-repair pass before persisting, defending Anthropic's API contract against broken client transcripts (orphaned `tool_use`, ordering violations, incomplete turns).

### Added

- **Chat-completions session resume via `X-Client-Session-Id`.** When the
  client sends this header, amplifier-agent now uses a deterministic
  ``http-<client_sid>`` as the amplifier session_id, auto-detects whether
  this is the first turn or a continuation by checking if the session state
  dir exists on disk, and passes ``is_resumed`` to the existing kernel
  resume mechanism (same primitive the CLI face's ``--resume`` flag uses).
  One opencode conversation = one amplifier session — unified audit trail,
  persistent hook state across turns, append-mode events.jsonl.

- **Client-authoritative transcript reconciliation** in
  ``src/amplifier_agent_http/_reconciler.py``.  Since the chat-completions
  wire is stateless and the client sends full history every turn, on
  divergence between stored and incoming the client wins by fiat — we
  persist the client's view over our stored copy without any rewind
  ceremony.  Sufficient for opencode and any well-behaved OpenAI-compatible
  client.  No new event types introduced.

- **`bundle.md` declares all 4 default providers** (anthropic, openai, azure-openai, ollama). Previously only anthropic was declared; the other 3 had to be installed lazily at first use of `amplifier-agent run` against a host_config that referenced them. Now all 4 ship as part of the prepared bundle — the top-level `providers:` section is processed by `bundle.prepare(install_deps=True)` during cold-prep and the post-install hook, ensuring every provider module is importable before any session is created.

### Changed

- **`reconcile_client_history` now runs foundation's transcript-repair pass before persisting** the client's view. Catches broken chat-completions clients (orphaned `tool_use` without paired `tool_result`, ordering violations, incomplete assistant turns) that would otherwise cause Anthropic to reject the next LLM call with HTTP 400. Mirrors `_runtime.py:_repair_loaded_transcript_if_needed` from the CLI face. Healthy transcripts pass through unchanged with negligible overhead (<10ms diagnostic).

- **`X-Session-Id` header is now recognized as a fallback** for the existing `X-Client-Session-Id` correlation mechanism (PR #71). opencode and other Vercel AI SDK-based clients send `X-Session-Id` by default; amplifier-agent now picks it up automatically, so session-resume + client-authoritative reconciliation works for opencode with zero config. `X-Client-Session-Id` remains authoritative when both headers are present.

- **Workspace name is no longer suffixed with the client session id.** Previously, `X-Client-Session-Id: abc` would route requests into `workspaces/<base>-abc/`. Now the workspace stays at `<base>` and per-client distinction is purely at the session_id level (`workspaces/<base>/sessions/http-abc/`). This keeps workspace-level hook state (context-intelligence, etc.) shared across all sessions of the same server process, where it belongs.

- **`amplifier-agent serve chat-completions` lifespan now triggers the same module-install path that `amplifier-agent run` uses.** Previously, a fresh `uv tool install amplifier-agent` followed by `serve chat-completions` would fail with `ProviderModuleNotInstalledError` because the lazy-install that `run` gets via `create_session() → session.initialize() → resolver.async_resolve()` never fires for `serve`. The lifespan now calls `prepared.resolver.async_resolve(module_id, source)` for every `PROVIDER_CATALOG` entry before the providers loop — idempotent (no-op on warm cache) and asynchronous (lifespan waits for completion before opening the wire).

- **`single_turn.py` now explicitly clears `mount_plan["providers"]` before `inject_provider`.** `bundle.md` now populates the top-level `providers:` section with 4 stubs so `bundle.prepare()` installs them. Without the clear, `inject_provider`'s "no-op if providers already present" guard would fire and skip injecting the runtime provider (with env-var credentials). This mirrors the pattern already used by `_session_runner.run_chat_turn`.

- **Breaking (server mode only):** `amplifier-agent serve chat-completions` now requires `host_config.providers` to be a non-empty dict. Any provider declared there that cannot initialize (missing credentials, module not installed, `list_models()` raises, returns 0 models) causes the server to exit 2 with a structured error listing every problem. The previous behavior — iterating a hardcoded `KNOWN_PROVIDERS` list, silently skipping unreachable providers, and falling back to an unusable placeholder model — is gone. Single-turn mode (`amplifier-agent run`) is unaffected; the `provider` (singular) block continues to work for it.

- **`POST /v1/chat/completions` now validates `model` against the served registry.** Requests with an unknown model return HTTP 400 `{"error": {"code": "unknown_model", ...}}` immediately, instead of being silently routed to whichever provider loaded first and failing 4 seconds later with an upstream `not_found_error` embedded in `delta.content`.

- **`stream: false` is now honored.** Requests with that flag return a single JSON body; only `stream: true` (or absent) uses SSE.

- **Upstream errors raised before any content chunks are emitted now surface as HTTP 502** with a structured OpenAI-shape error envelope, instead of being embedded inside `delta.content` of a 200 SSE response.

- **`/v1/models` no longer falls back to a placeholder `{"id": "amplifier", ...}` entry.** The lifespan now guarantees `served_models_registry` is non-empty (or the server exits at boot), so the fallback was unreachable in practice.

### Added

- **`amplifier-agent serve status / stop / restart` subcommands** — operational lifecycle for the chat-completions HTTP server. Status reports whether the server is running, where it's reachable, how many models from which providers it's serving, and self-cleans stale state files when the PID no longer exists. Stop sends SIGTERM with a configurable graceful-exit window (`--timeout`), escalating to SIGKILL on expiry or on `--force`. Restart performs an identity-restart using the args stored at original launch (host, port, api-key, workspace, host_config). State is tracked in `~/.amplifier-agent/state/serve.json` (mode 0600, parent dir 0700; api_key is sensitive — never logged).

- **`host_config.providers` (plural) registry** — declares which providers the server-mode lifespan loads and how to instantiate each. Schema: `providers: {<provider_id>: {module?: str, config?: dict}}`. The `module` defaults to the provider_id when omitted. Each provider's `config` is passed through as the `extra_config` arg to `list_provider_models()` and then to the provider module's constructor.

### Internal

- New `_validate_providers_registry()` in `amplifier_agent_lib/config/loader.py` enforces the closed schema for the new block.
- HTTP-face tests introduced from scratch under `tests/http/` covering lifespan boot scenarios and chat-completions validation.

### Migration

For server-mode users on `<= 0.8.0`: add a `providers` block to your `host_config.json`. Minimum to keep working with just Anthropic:

```json
{
  "providers": {
    "anthropic": {}
  }
}
```

Multi-provider example:

```json
{
  "providers": {
    "anthropic": {},
    "openai":    {"config": {"base_url": "https://api.openai.com/v1"}}
  }
}
```

If you don't pass `host_config.providers`, the server will exit at boot with a clear error message rather than running in a broken half-state.

## [0.8.0] — 2026-06-20

Adds an OpenAI-compatible chat-completions HTTP face for embedding amplifier-agent in third-party tools (opencode and similar), a persistent `auth` subcommand for provider credentials, and integrates the model-routing matrix for per-provider model selection. Existing JSON-RPC wire protocol unchanged — no wrapper bump required.

### Added

- **OpenAI-compatible chat-completions HTTP face** (`amplifier-agent serve chat-completions`). Exposes `/v1/models` and `/v1/chat/completions` over HTTP with bearer-token auth (`Authorization: Bearer ...`). Streams responses, returns OpenAI-shape envelopes, and supports multi-provider routing: the model field on each request is resolved through the served-models registry to the upstream provider, so a single server can serve Anthropic, OpenAI, Azure, and Ollama models from one endpoint. Enables direct integration with opencode (via the separate [`amplifier-app-opencode`](https://github.com/microsoft/amplifier-app-opencode) wrapper) and any other OpenAI-compatible client.

- **`amplifier-agent auth` subcommand** for persistent provider credentials. Stores at `~/.amplifier-agent/credentials.json` (mode `0600`) via the `set / list / remove / status / clear` actions. Resolution chain is **env-first**: shell env vars (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, …) always win over the file, so existing shell-rc workflows are unchanged. The file lets users configure credentials once and have every subsequent invocation pick them up automatically — including the HTTP server, the `models list` command, and wrappers like `amplifier-opencode`. UX matches `claude login` / `gh auth login` / `aws configure` without the OAuth ceremony.

- **Host-tool delegation** over the chat-completions wire face. Tools declared by the host (in `host_config.json` under `host_tools`) are surfaced to the model with stub schemas; when the model invokes one, amplifier-agent emits a signal tool_call back to the client (carrying the same `chunk_id`), the client executes the tool host-side, and the result is returned for the model to continue. Lets the host own filesystem, shell, browser, or any custom tool without amplifier-agent having to bundle it.

- **Model routing matrix integration** (#64). The routing matrix can declare per-role provider/model preferences; amplifier-agent resolves the right provider per turn based on the matrix. Used by the new HTTP face for cross-provider model dispatch.

- **`X-Client-Session-Id` request header** for workspace correlation. Wrappers pass their own session ID; the server uses it as the workspace name when writing transcript logs, so client-side and server-side session bookkeeping stay aligned.

### Changed

- **Lifespan provider initialization** now iterates `KNOWN_PROVIDERS` and registers every provider whose module is installed AND whose credentials are present. Previously the chat-completions face hardcoded `inject_provider("anthropic")` at lifespan; injection is now per-request based on the model the client picks. Boot log surfaces a line per skipped provider (`"Skipping provider 'openai' -- module not installed"`, `"Skipping provider 'ollama' -- no credentials in env"`) so it's clear what amplifier-agent thinks it can serve.

- **`/v1/models` response** surfaces a `_provider` tag per model so OpenAI-compatible clients can see which provider serves each entry. Standard clients ignore the non-standard field per the OpenAI spec; aware clients can use it for routing decisions or display.

- **Usage-counter telemetry** in chat-completions responses now correctly reflects the provider that actually served the turn (was previously misattributed when routing across providers).

- **Bundle preparation pipeline** refactored to support the new HTTP face cleanly — same cache key semantics, no migration required.

### Internal

- `_resolve_env_credential` in `provider_sources.py` extended to chain env → `credentials.json` → empty. Lazy-imports the file reader from `admin/auth.py` to avoid a module-load cycle.
- New `admin/auth.py` (~330 lines) implements the `auth` subcommand surface: atomic JSON write (mode 0600), parent dir mode 0700, versioned envelope `{version: 1, providers: {...}}`, schema-tolerant load that round-trips unknown providers/fields.
- `routes/chat_completions.py` looks up the requested model in `app.state.served_models_registry` and passes the resolved `provider_id` + `upstream_model` through to `_session_runner.run_chat_turn` for per-request `inject_provider` under the existing `_create_session_lock` (save-restore pattern).
- `routes/models.py` surfaces `_provider` in the `/v1/models` response.

### Wire protocol

- Existing JSON-RPC wire face unchanged at `0.3.0`. **No wrapper bump required.** TypeScript wrapper stays at `0.7.0`, Python wrapper stays at `0.3.0`.
- New OpenAI-compatible chat-completions face is a separate wire — independent versioning is not currently surfaced; the schema is the OpenAI chat-completions subset documented in `README.md`.

### Migration

- No breaking changes for users on the JSON-RPC wire (existing `run`, `serve`, `models list`, etc. unchanged).
- New users / new integrations: prefer the chat-completions face for OpenAI-compatible clients; prefer `amplifier-agent auth set` over shell-rc exports for the "set once, works everywhere" UX. Both env vars and the file still work side-by-side.

## [0.7.0] — 2026-06-17

Built-in bundle replaced with vendored behavioral-anchor. Agent set, tool roster, and bundle name all change. Wire protocol unchanged — no wrapper bump required.

### Changed

- **Built-in bundle replaced with `amplifier-agent-behavioral-anchor`** (was `amplifier-agent-builtin`, v1.3.0). Adapted from the experimental `behavioral-anchor` bundle in `amplifier-foundation@main:experiments/behavioral-anchor/` with five amplifier-agent-specific modifications (see below). Manifest text + 6 agent definitions + `context/system.md` are vendored inside the wheel.

- **New sub-session agent set**: `architect`, `builder`, `debugger`, `git-ops`, `researcher` (plus retained `explorer`). Replaces the previous set of `planner`, `coder`, `tester`. **Breaking for users who scripted `delegate(agent="planner"|"coder"|"tester", ...)`** — those names are no longer recognized.

- **Tool roster expanded**: `tool-web` (web_search, web_fetch), `tool-apply-patch`, `tool-mode`, `tool-recipes`, `hooks-mode` added. Existing `tool-mcp`, `tool-skills`, `tool-todo`, `tool-delegate`, `tool-bash`, `tool-filesystem`, `tool-search`, `hooks-redaction`, `hooks-status-context`, `hooks-todo-reminder`, `hooks-session-naming`, `hook-context-intelligence` retained.

- **System prompt structure** is principle-led — a short set of named behavioral principles loaded once at the head via vendored `context/system.md`. Per-agent definitions are intentionally lean (no per-agent `tools:` blocks); sub-agents inherit the parent's tool roster through `tool-delegate.context_inheritance.enabled: true`.

### Amplifier-agent-specific modifications from upstream behavioral-anchor

| Upstream behavioral-anchor | This release | Why |
|---|---|---|
| no `default_provider` | `default_provider: anthropic` | Engine reads directly from frontmatter |
| `behaviors/streaming-ui.yaml` include | omitted | stdout reserved for JSON envelope (invariant #5); engine handles streaming via `bundle/hook_streaming.py` |
| `hooks-todo-display` | omitted | same stdout-contract reason |
| `behaviors/logging.yaml` include | `hook-context-intelligence` instead | preserves workspace JSONL alignment with amplifier-app-cli (per PR #57) |
| `hooks-approval` | omitted | no wire-protocol approval round-trip yet — would deadlock on policy-driven rules |
| no `tool-mcp` | added | preserves MCP support and `doctor` checks for existing users |

### Internal

- `AGENTS.md` gains a "Common pitfalls" entry on stale-cache troubleshooting. Foundation's source resolver *does* follow transitive deps declared in upstream module `pyproject.toml`s — but only when given a fresh git clone. Early bundle-swap failures (`No module named 'aiohttp'`, `No module named 'context_intelligence'`) were all stale-cache problems, not real install gaps. The existing `mcp` entry in our `pyproject.toml` may be vestigial.
- All `pyproject.toml` `force-include` entries updated to match the new agent + context filenames.
- Test suite rewritten/updated across 7 files for the new agent set; lean parameterized tests replace the previous per-old-agent body-section assertions.

### Engine compatibility

- Requires Python `>=3.12` (unchanged).
- Wire protocol: `0.3.0` (unchanged). **No wrapper bump required.**
- Bundle cache key (`sha256(bundle.md)`) changes — existing prepared pickles auto-invalidate. First run after upgrade does a cold-prep (~30–90s; larger module set than 0.6.0).

### Migration

- Scripts or wrapper code that delegates by agent name must be updated to the new set: `planner`→`architect`, `coder`→`builder`, `tester`→`debugger`. The retained `explorer` agent is unchanged in name (lean version of the definition).
- `default_provider: anthropic` is unchanged. No host-config migration.
- First run will re-prepare the bundle and re-install modules. Budget 30–90s.

## [ts-wrapper 0.6.2] — 2026-06-08

### Fixed

- **Wall-clock timeout is now opt-in.** Previously, `timeoutMs: undefined` silently inherited a 10-minute `DEFAULT_TIMEOUT_MS` cap inside `SessionHandle.submit()` — long agent turns (>600s) were killed with a synthesized `engine_hung` error and SIGTERM/SIGKILL. The new contract: the wall-clock hang timer is armed only when `timeoutMs` is a positive number. `undefined`, `0`, or any negative value disables it entirely.
- The Amplifier CLI itself imposes no per-turn timeout, so the wrapper SDK no longer does either. Callers that want the legacy cap can opt in explicitly with `timeoutMs: DEFAULT_TIMEOUT_MS` (now exported from the package).
- Real-world impact: agent tasks in Paperclip (and any other consumer) that legitimately ran past 10 minutes will no longer be killed mid-work.

### Added

- **`DEFAULT_TIMEOUT_MS` is now exported** from the package root, so callers that want the original 10-minute cap can opt in with `timeoutMs: DEFAULT_TIMEOUT_MS`.

### Tests

- New unit cases `(k) timeoutMs: 0` and `(l) timeoutMs: undefined` in `test/session-subprocess.test.ts` — 300ms windows confirm no `engine_hung` is synthesized.
- New `test/timeout-longwindow-integration.test.ts` — three end-to-end cases through the public `spawnAgent() → submit()` API against a real ~12s mock-engine subprocess: (1) `timeoutMs: 0` completes normally with no `engine_hung`, (2) `timeoutMs: undefined` same, (3) positive control `timeoutMs: 500` proves the timer still arms and cancels correctly.
- Full suite: 101/101 passing under `bun run test`; typecheck clean.

### Known issue

- With the wall-clock timer opt-in, callers that pass `0` or `undefined` get no wrapper-side hang detection. The 2s activity ticker emits heartbeats but does not escalate. A future iteration will add progress-based detection (`stuckDetection` config) so genuinely-hung subprocesses are recovered without re-introducing a wall-clock cap. Tracked in `ISSUES.md` as ISSUE-002.

### Engine compatibility

- Wire protocol: `0.3.0` (unchanged).

## [0.5.0] - 2026-06-03

New `update` subcommand for self-management + delegate sub-session approval/display inheritance fix.

### NEW

- **`amplifier-agent update` subcommand** — wraps the previously-required `uv tool install --reinstall --force "git+https://...@v<tag>"` ritual behind a single command:
  - No args: check latest GitHub Release, install if newer
  - `--check`: status-only, no install
  - `--tag <ref>`: install a specific tag/branch/SHA (`v0.4.0`, `main`, etc.)
  - `--force`: reinstall even when versions match (clears corrupted installs)
  - `--output json`: structured envelope for tooling
  - Detects install method (`uv tool` vs editable vs other) and refuses operations that would clobber a dev checkout

- **Engine bump 0.4.1 → 0.5.0**: additive feature (new subcommand) + delegate sub-session inheritance fix. No wire-protocol change. No wrapper version bump.

### Fixed

- **Side-effecting tool calls in `delegate` sub-sessions no longer auto-deny when the parent is configured with `-y` / `approval.mode: "yes"`.** Surfaced by a consumer report. Root cause: parent's approval provider was registered via `coordinator.register_capability("approval.request", ...)` (the capability registry), but `spawn_sub_session` was reading `parent.coordinator.approval_system` (a separate Rust-backed property slot). The two slots were uncoupled, so the child session inherited a `None` approval provider and hooks-approval auto-denied every tool that needed approval. Now `spawn.py` explicitly copies the `approval.request` and `display.emit` capabilities from parent to child after the child's session has mounted, restoring the inherit-policy semantics consumers expect.
- **Sub-session display events.** Same structural bug affected `display.emit` — sub-session events (token streams, tool/started, tool/completed) were silently dropped because parent registered via capability registry but spawn read from `coordinator.display_system`. Now both capabilities propagate. Consumers using `display.onEvent` (PR #36 / wrapper 0.6.1) on sub-session events will see them flow through correctly.

### Internal

- Followed `self-managing-tool-patterns` skill conventions for the update mechanism.
- API call to GitHub Releases is best-effort with clear failure messaging — no cached fallbacks.

### Engine compatibility

- Requires Python `>=3.12` (unchanged).
- Wire protocol: `0.3.0` (unchanged).

## [ts-wrapper 0.6.1] — 2026-06-03

### Fixed

- **`test/transport.test.ts > terminate() resolves with SIGTERM signal or non-zero exit code` flaked on CI with `Error: Test timed out in 5000ms`.** The test exercises actual subprocess SIGTERM handling, which is slower on Ubuntu runners than on local macOS. Per-test timeout bumped to 15s. Same class of fix as `#19 fix(wrapper): bump vitest testTimeout to 15s for CI transport test` from a prior release window.

### Why this didn't ship as part of 0.6.0

The 0.6.0 publish workflow run failed at the Test step before reaching `npm publish`. `amplifier-agent-ts@0.6.0` was never published. This 0.6.1 release supersedes that aborted attempt; consumers can install 0.6.1 directly without first installing 0.6.0.

### Released

- `amplifier-agent-ts` (TypeScript wrapper) 0.6.1

## [0.4.1] - 2026-06-03

### Fixed

- **uv workspace declaration referenced non-existent directories.** `pyproject.toml` declared `[tool.uv.workspace] members = ['packages/amplifier-agent', 'packages/amplifier-agent-session-spawner', 'wrappers/python']`, but the two `packages/...` directories have never existed in the repository. Most uv versions handle this gracefully (warn or silently ignore), but specific uv-version + config combinations would resolve the workspace install to an ancestor commit where pre-PR-#27 packaging bugs were still present, producing confusing hatchling errors at `uv tool install` time. Now declares only the real `wrappers/python` member.

### Migration

Consumers who hit `uv tool install` failures with `v0.4.0` should retry with `v0.4.1`. No code changes are needed on the consumer side.

### Credits

Surfaced by a consumer report against `v0.4.0`.
## [ts-wrapper 0.6.0] — 2026-06-03

Wrapper hardening release closing 8 consumer-reported gaps at 0.5.0.

### NEW

- **`SpawnAgentParams.configPath?: string`** (#1) — surface engine's `--config <path>` flag and `host_config.json` resolution to TS callers (engine side: PR #27 / v0.4.0; wrapper side: this release).
- **`SpawnAgentParams.runChildProcess?: ChildProcessFactory`** (#3) — injection point for substituting `child_process.spawn` (testability, sandboxing). `ChildProcessFactory` exported `@public`.
- **`SpawnAgentParams.approval?: { mode: 'yes' | 'no' | 'prompt' }`** (#10) — wires to engine `-y` / `-n` argv. `'prompt'` emits no flag and lets the engine fall back to `host_config.approval.mode` (PR #34) or the bundle's TTY-based default. The legacy `{ onRequest, timeoutMs }` shape still throws `approval_not_supported_in_v1` — Mode A has no mid-turn channel.
- **`SpawnAgentParams.allowProtocolSkew?: boolean`** (#9) — bypass the wrapper-side protocol-version check. Mirrors the engine's `host_config.allowProtocolSkew` knob.
- **Stderr NDJSON event pipeline** (#2, #4, #6) — `parseNdjsonStream` extracted as a standalone `@public` helper and wired onto the child subprocess's stderr stream inside `SessionHandle`. The 9 wire event types emitted by the engine (progress, result/delta, result/final, thinking/delta, thinking/final, tool/started, tool/completed, approval/request, approval/timeout, plus wire-level error) are parsed into a new `{type:'notification', method, params}` `DisplayEvent` variant and dispatched to `display.onEvent`. Previously stderr was buffered as raw text and `display.onEvent` was silently dropped.
- **`getEngineInfo()` implementation** (#7) — `engineVersion` populated from the `amplifier-agent version --json` probe that `spawnAgent()` now runs at init. `bundleDigest` populated from the same payload when present (forward-compatible — engine currently omits it; will populate automatically when a future engine release exposes it).
- **`checkProtocolVersion()` wired into init path** (#9) — wrapper-side fast-fail on protocol-version skew before subprocess spawn. Previously the utility existed but was never called.
- **Re-exports from `index.ts`** (#5) — `assembleArgv`, `AssembleArgvInput`, `resolveMcpConfigPath`, `cleanupSpillFile`, `McpSpillResult`, `buildEnv`, `resolveBinaryPath`, `probeEngineVersion`, `DEFAULT_ALLOWLIST`, `BLOCKED_ENV_KEYS`, `Transport`, `TransportOptions`, `ExitInfo`, `parseNdjsonStream`, `ParseNdjsonStreamOptions`, `checkProtocolVersion`, `VersionCheckResult`, `parseRunOutput`, `STDERR_TAIL_BYTES`, `SubprocessOutcome`, `makeApprovalHandler`, `ApprovalAdapter`, `ApprovalRequest`, `ApprovalHandler`, `ChildProcessFactory` — all annotated `@public`.
- **`PROTOCOL_VERSION_REQUIRED_BY_WRAPPER`** bumped `"0.2.0"` → `"0.3.0"` to match the engine's current wire protocol. The previous pin was stale; the new `checkProtocolVersion()` wiring would have surfaced this at startup.

### BREAKING

- **`display.onEvent` now actually fires.** (#4) Callers that registered the callback expecting it to be a no-op may see new event flow. The `DisplayEvent` discriminated union has a new `notification` variant; exhaustive switch statements on `event.type` need a corresponding branch.
- **`SpawnAgentParams.approval` is now a union shape.** (#10) Callers passing `{ mode }` no longer hit `approval_not_supported_in_v1`. Callers that defensively caught that error when passing `mode` need to remove the try/catch.
- **`PROTOCOL_VERSION_REQUIRED_BY_WRAPPER` value changed.** (#9) Wrappers pinned at `"0.2.0"` will fail-fast against engines speaking `"0.3.0"` rather than discovering the mismatch at first `submit()`. This is wrapper-internal; the engine already requires `"0.3.0"` since 0.4.0.
- (Minor) The re-export surface of `index.ts` is now larger (#5). Callers that relied on the previously-implicit "these aren't public" assumption may see new TypeScript completion entries.

### Fixed

- Stderr event loss (#2)
- `display.onEvent` silent drop (#4)
- `Transport` dead code (#6 — root cause of #2/#4)
- No `configPath` plumbing (#1, wrapper side)
- No `runChildProcess` injection (#3)
- Missing public re-exports (#5)
- `getEngineInfo()` Task-9 TODO (#7)
- `checkProtocolVersion()` not called (#9)
- Approval API stub (#10)

### Not changed (clarification for the consumer report)

- `InitializeParams.mcpConfigPath` wire-protocol field is **intentionally retained** in protocol-0.3.0. The engine still reads it via `handle_initialize` → `AMPLIFIER_MCP_CONFIG`. Only the `--mcp-config-path` argv flag was removed (PR #29). The TS type (auto-generated from `schemas/InitializeParams.schema.json`) correctly reflects this and was not modified.

### Engine compatibility

- Requires `amplifier-agent >= 0.4.0` (host config layer + `approval.mode` config key).
- Pinned protocol: `0.3.0`.

### Released

- `amplifier-agent-ts` (TypeScript wrapper) 0.6.0

## [0.4.0] — 2026-06-03

### BREAKING

**Engine argv surface removed:**
- `--host-capabilities` (#27) — write-only, zero read sites
- `--env-allowlist`, `--env-extra` (#27) — subsumed by host config layer
- `--allow-protocol-skew` + `AMPLIFIER_AGENT_ALLOW_PROTOCOL_SKEW` env var (#27) — moved to host config `allowProtocolSkew: true`
- `--mcp-config-path` (#29) — subsumed by `mcp.configPath` host-config key + `$AMPLIFIER_MCP_CONFIG` env var
- `--skills-dir` (#30) — subsumed by `skills:` host-config key + `$AMPLIFIER_SKILLS_DIR` env var

**CLI behavior changes:**
- **CLI (BREAKING)** `--skills-dir` argv flag removed from `amplifier-agent run`. Migration paths (per D13):
  1. **Preferred — env var**: set `$AMPLIFIER_SKILLS_DIR` (preserved as the adapter-bridge surface). The `tool-skills` module continues to honour it.
  2. **Or — host_config**: add a `skills:` block to your host_config JSON (per D11) and pass it via `--config <path>` or `$AMPLIFIER_AGENT_CONFIG`. Example:
     ```json
     {
       "skills": {
         "skills": ["/path/to/extra/skills"],
         "visibility": {"max_skills_visible": 20}
       }
     }
     ```
- **CLI (BREAKING — G3)** Headless `amplifier-agent run` invocations (non-TTY stdin) now **fail fast at startup** when neither `-y` / `-n` nor `host_config.approval.mode` declares an explicit approval policy (#34). The previous behavior — silently defaulting to `approval.mode='no'` and producing success-shaped no-op runs in which every tool call was auto-denied — was indefensible: monitoring saw green, the agent appeared to succeed, and zero work happened with no programmatic signal to catch it. The new behavior writes a §4.1 error envelope (`code: approval_unconfigured`, `classification: protocol`) and exits 2 with a remediation line pointing at the three escape hatches. Migration: pass `-y` (auto-approve), `-n` (explicit auto-deny), or set `{"approval": {"mode": "yes"|"no"|"prompt"}}` in `--config` / `$AMPLIFIER_AGENT_CONFIG`. Interactive runs from a TTY are unaffected — the default remains `prompt`.

**Wire surface removed (envelope + initialize):**
- `metadata.hostCapabilities` from response envelope (#27)
- `InitializeParams.host` (#27)
- `InitializeParams.mcpServers` renamed to `mcpConfigPath` (PR #24, prior release window)

**Wire protocol bumped:** `0.2.0` → `0.3.0`. Old wrappers fail handshake with `protocol_version_mismatch`, exit 2 (intentional).

**Wrapper API removed (TS + Python parity):**
- `SpawnAgentParams.host` / `HostCapabilities` type / `InitializeHostParams` type (#27)
- `mcpConfigPath` field + argv emission (#29) — wrappers now inject `AMPLIFIER_MCP_CONFIG` env var
- `envAllowlist` / `envExtra` / `allowProtocolSkew` fields + argv emission (#31)

### NEW

**Host config layer (#27, #30, #34):**
- `--config <path>` argv flag + `$AMPLIFIER_AGENT_CONFIG` env var (2-tier resolution)
- 4 top-level config keys: `mcp`, `approval`, `provider`, `allowProtocolSkew`
- Pass-through schema mirroring downstream module configs
- Layered merge with bundle defaults at module mount time
- Strict-by-default validation
- `default_provider:` field in vendored `bundle.md`
- `amplifier-agent config show` reports resolved path + source + parsed values
- XDG resolution consolidated through `persistence.py`
- **`approval.mode` config key (#34, G3)** — values `"yes" | "no" | "prompt"`. Lets hosts that drive `amplifier-agent` via host_config (no argv access) express the same intent as CLI flags `-y` / `-n`. Validated at parse time (`config_invalid_type` on unknown values or non-strings). Precedence: argv flag > host_config > bundle default. `VALID_APPROVAL_MODES` exported for downstream policy validation.

**Engine dependency management (#34, G4):**
- `mcp` added as a declared transitive dependency in `pyproject.toml`. The canonical install command — `uv tool install git+https://github.com/microsoft/amplifier-agent` — now works out of the box. Hosts no longer need to know to pass `--with mcp`, and forgetting it no longer produces the downstream `'Bundle' object has no attribute 'origins'` AttributeError that masked the real cause.
- New doctor check `_check_mcp_importable()` — `amplifier-agent doctor` gains an `mcp module: importable` check that fires whenever `tool-mcp` is declared in `bundle.md`. Reports `[ OK ]`, `[FAIL]` with a clear remediation line, or `[INFO]` (skipped) if `tool-mcp` is not in the bundle. Catches the "forgot `--with mcp` on an old install" condition that the prior doctor passed silently.

**Skills block in host config (#30):**
- 5th top-level config key: `skills:` — pass-through to `tool-skills` module
- `skills.skills: list[str]` list-concatenated with bundle-declared sources (D12: bundle-first, host-appended)
- `skills.visibility: dict` dict-overlaid on bundle visibility defaults (D11)
- `tool-skills` module declared in vendored `bundle.md` (sourced from `git+https://github.com/microsoft/amplifier-bundle-skills@main#subdirectory=modules/tool-skills`) with three default skill sources (curated bundle, `.amplifier/skills`, `~/.amplifier/skills`)
- `amplifier-agent config show` reports post-merge `skills` block — bundle defaults plus host additions
- Bundle cache invalidates on upgrade (`bundle.md` sha256 changes) — run `amplifier-agent prepare` after upgrade

### Internal

- `provider_detect.py` deleted (vestigial)
- `src/amplifier_agent_cli/skill_sources.py` (`inject_skill_dirs()` helper) deleted — unreachable after `--skills-dir` removal
- `pyproject.toml` wheel-build duplicate-include fix (#27)
- Conformance suite restored to green + new baseline/skew-override fixtures (#32)
- `tests/test_phase_2_1_exit_gate.py` fixture-name fix (#32 side fix)
- `host_config` schema reference docs added — `docs/configuration.md` (#34, N1/N2). Authoritative reference for the closed top-level host_config schema, per-key semantics, precedence model (argv flag > host_config > bundle default), error codes (`approval_unconfigured`), and concrete examples for common host integrations.
- Test infrastructure (#34): `conftest.py` adds autouse fixture defaulting `is_stdin_tty` to True for all tests, plus a session-scoped fixture seeding `AMPLIFIER_AGENT_CONFIG` with `{approval:{mode:yes}}` for subprocess tests, so existing tests behave as TTY-attached by default and subprocess tests don't hit the new G3 headless check.

### Migration

- **Existing wrappers / hosts**: must drop the removed argv flags and wire fields. Mismatch is loud (`protocol_version_mismatch`, exit 2) — no silent downgrades.
- **Skills path consumers**: prefer `$AMPLIFIER_SKILLS_DIR` (preserved as adapter-bridge env var) or add a `skills:` block to host_config. The `--skills-dir` argv flag is gone.
- **MCP config path consumers**: prefer `$AMPLIFIER_MCP_CONFIG` env var or add an `mcp:` block to host_config. The `--mcp-config-path` argv flag is gone.
- **Headless / non-TTY callers (#34, G3)**: must declare approval intent explicitly. Either pass `-y` / `-n` on the command line, or set `{"approval": {"mode": "yes"|"no"|"prompt"}}` in `--config` / `$AMPLIFIER_AGENT_CONFIG`. Non-TTY runs without explicit policy now exit 2 with `approval_unconfigured`.

### Cross-repo follow-ups (NOT in this release)

Downstream consumers (notably `amplifier-module-provider-nc`) must catch up:
1. Drop `host: { capabilities }` from `spawnAgent` call (#27)
2. Migrate `--mcp-config-path` argv → `AMPLIFIER_MCP_CONFIG` env var injection (#29)
3. Stop passing `envAllowlist` / `envExtra` / `allowProtocolSkew` to `spawnAgent` (#31)

### Design references

- `docs/designs/2026-06-01-host-config-layer-revisit.md` (D11/D12/D13 — skills block)
- `docs/designs/2026-06-01-drop-host-capabilities.md`
- `docs/configuration.md` (host_config schema reference, G3 approval policy details — #34)

### Released

- `amplifier-agent` (engine) 0.4.0
- `amplifier-agent-client` (Python wrapper) 0.4.0
- `amplifier-agent-ts` (TypeScript wrapper) 0.5.0 — bumped past published 0.4.0 because the accumulated breaking API changes since 0.4.0 was published (PRs #27, #29, #30, #31) cannot be released as a patch or minor and 0.4.0 is already on npm.
- Wire protocol 0.3.0

## [0.3.0 engine / 0.4.0 wrapper] — 2026-05-27

### Fixed

- **Engine** `_runtime.py` — three latent runtime-crashing bugs in MCP server config handling, all silenced by `# pyright: ignore` suppressions:
  - `AttributeError: 'PreparedBundle' object has no attribute 'config'` — author wrote prose comments asserting `PreparedBundle.config` was the merged bundle yaml; it does not exist. The merged yaml lives on `mount_plan`.
  - `AttributeError: 'list' object has no attribute 'get'` — `mount_plan["tools"]` is a list of `{module, source, config}` dicts, not a dict keyed by module name. The author treated it as a dict.
  - `TypeError: PreparedBundle.create_session() got an unexpected keyword argument 'tool_overrides'` — the kwarg does not exist on the foundation API.
  Each suppression masked a real attribute or call error pyright had flagged. The whole `--mcp-servers` flow was non-functional at 0.2.0; the file-based discovery paths documented in `amplifier-module-tool-mcp` continued to work.

### Changed

- **Wire (BREAKING)** `PROTOCOL_VERSION` bumped `0.1.0` → `0.2.0`. MCP server delivery refactored from inline `mcpServers: dict` to path-based `mcpConfigPath: str`. The engine forwards the path to `tool-mcp` via `AMPLIFIER_MCP_CONFIG` (one of four documented config priorities in the module). Old wrappers fail with a clean `protocol_version_mismatch` rather than a confusing runtime crash.
- **Engine CLI** `--mcp-servers` flag renamed to `--mcp-config-path`. The engine no longer parses MCP config contents — it validates the path exists and forwards it to the module.
- **Wrapper** `mcp-spill.ts` now always spills to a `0600` tmpfile (dropping the inline-JSON-on-argv branch — also eliminates server-config visibility in `ps aux`) and writes content in the format the module expects (`{"mcpServers": <map>}`).

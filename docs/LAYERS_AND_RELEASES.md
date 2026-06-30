# Amplifier Agent — Layers and Releases

This document explains how the `amplifier-agent` ecosystem is layered, what each layer publishes, and what needs to be released when something changes. It is the reference for anyone integrating `amplifier-agent` into a host application or contributing changes to it.

## TL;DR

`amplifier-agent` is a **per-turn stdio subprocess** that wraps the Amplifier kernel plus a fixed bundle of modules, with an **optional OpenAI-compatible HTTP server** for hosts that already speak chat-completions. Hosts integrate through one of three surfaces:

| Surface | Package | For |
|---|---|---|
| Python SDK | `amplifier-agent-py` (PyPI) | Python hosts |
| TypeScript SDK | `amplifier-agent-ts` (npm) | Node / TypeScript hosts |
| HTTP server | `amplifier-agent serve chat-completions` | Hosts that already speak the chat-completions REST shape (e.g. opencode) |

All three sit on the same engine. The same release of `amplifier-agent` powers all three.

## The Layer Stack

```
+---------------------------------------------------------------------+
|  Host application                                                   |
|  (nanoclaw fork, paperclip fork, opencode, your app, ...)           |
+---------------------------------------------------------------------+
                                  |
                                  v
+---------------------------------------+-----------------------------+
|  Adapter                              |  HTTP bridge                |
|  (per-host integration code,          |  (e.g. amplifier-app-       |
|   uses one of the SDKs)               |   opencode)                 |
+---------------------------------------+-----------------------------+
                  |                                  |
                  v                                  v
+------------------------------+   +------------------------------------+
|  Client SDK                  |   |  amplifier-agent serve             |
|  amplifier-agent-py  (PyPI)  |   |    chat-completions                |
|  amplifier-agent-ts  (npm)   |   |  FastAPI HTTP face (POC)           |
+------------------------------+   +------------------------------------+
                  |                                  |
                  +--------------+-------------------+
                                 v
+---------------------------------------------------------------------+
|  amplifier-agent (PyPI: amplifier-agent)                            |
|    Engine     (amplifier_agent_lib)                                 |
|    CLI        (amplifier_agent_cli)                                 |
|    HTTP face  (amplifier_agent_http)                                |
|    bundle.md  (shipped in the wheel)                                |
+---------------------------------------------------------------------+
                                 |
                                 v
+---------------------------------------------------------------------+
|  amplifier-foundation        (load + prepare bundles)               |
+---------------------------------------------------------------------+
                                 |
                                 v
+---------------------------------------------------------------------+
|  amplifier-core              (kernel)                               |
+---------------------------------------------------------------------+
                                 |
                                 v
+---------------------------------------------------------------------+
|  Amplifier modules                                                  |
|    Providers, tools, orchestrator, context, hooks                   |
|    Fetched at first run per bundle.md                               |
+---------------------------------------------------------------------+
```

## Each layer in detail

### 1. `amplifier-agent` (the engine)

- **Repo:** [microsoft/amplifier-agent](https://github.com/microsoft/amplifier-agent)
- **PyPI:** `amplifier-agent` (current: `0.9.0`)
- **Install:** `uv tool install git+https://github.com/microsoft/amplifier-agent`

A single Python package containing three internal subpackages:

| Subpackage | Role |
|---|---|
| `amplifier_agent_lib` | The engine — `boot`, `submit_turn`, `shutdown`. Mode-agnostic, no I/O. Calls `foundation.load_and_prepare_cached()`. |
| `amplifier_agent_cli` | The CLI — `amplifier-agent run`, `serve`, `doctor`, etc. Owns stdout / stderr discipline. |
| `amplifier_agent_http` | The HTTP face — FastAPI app, `/v1/chat/completions` and `/v1/models`. Currently labelled `0.0.2-poc`. |

**Console scripts:**

- `amplifier-agent` — dispatcher for `run`, `serve {chat-completions,status,stop,restart}`, `doctor`, `prepare`, `verify`, `update`, `version`, `config show`, `cache clear`, `models list`, `auth`.
- `amplifier-agent-post-install` — first-run setup hook.

**stdio protocol (mode A — `amplifier-agent run`):**

- **stdout:** exactly one JSON envelope per invocation:
  ```json
  {"protocolVersion":"...","sessionId":"...","turnId":"...","reply":"...","error":null,"metadata":{...}}
  ```
- **stderr (optional, with `--display ndjson`):** newline-delimited JSON-RPC notifications for SDKs to consume as a streaming event source. NDJSON is **not** on stdout.

**HTTP protocol (mode B — `amplifier-agent serve chat-completions`):**

- `POST /v1/chat/completions` — OpenAI-compatible, streams SSE chunks. Client sends full conversation history each turn; server is stateless-on-the-wire but reconciles to an internal session via the `X-Client-Session-Id` header (client-wins on divergence).
- `GET /v1/models` — OpenAI-shape model list with extension fields (`display_name`, `limit`, `capabilities`, `reasoning`, `defaults`, `_provider`).
- `GET /docs` — OpenAPI UI.
- Lifecycle commands `serve status`, `serve stop`, `serve restart` use a state file on disk to discover and manage the running server.

### 2. The shipped bundle — `bundle.md`

The engine ships with `bundle.md` baked into the wheel. It declares which modules the engine loads at first run.

- **Bundle name:** `amplifier-agent-behavioral-anchor` (v0.1.0)
- **Path in repo:** `src/amplifier_agent_lib/bundle/bundle.md`

**Pre-wired modules:**

- **Providers:** `provider-anthropic`, `provider-openai`, `provider-azure-openai`, `provider-ollama`
- **Orchestrator:** `loop-streaming` (with `extended_thinking: true`)
- **Context:** `context-simple` (300K tokens, auto-compact at 80%)
- **Tools:** `tool-filesystem`, `tool-bash`, `tool-web`, `tool-search`, `tool-todo`, `tool-apply-patch`, `tool-delegate`, `tool-mcp`, `tool-skills`, `tool-mode`, `tool-recipes`
- **Hooks:** `hooks-status-context`, `hooks-redaction`, `hooks-todo-reminder`, `hooks-session-naming`, `hooks-mode`, `hooks-routing`, `hook-context-intelligence`
- **Vendored agents:** `explorer`, `architect`, `builder`, `debugger`, `git-ops`, `researcher`

Modules are **not** bundled — they are git-cloned and editable-installed on first run. The prepared bundle is cached at `~/.amplifier-agent/cache/prepared/<aaa_version>/<sha256(bundle.md)>/`. **Editing `bundle.md` self-invalidates the cache** because the cache key includes its hash.

### 3. Client SDKs

Both SDKs live inside the `amplifier-agent` repo under `wrappers/`.

#### `amplifier-agent-py` — Python SDK

- **PyPI:** `amplifier-agent-py` (current: `0.3.0`)
- **Source:** `wrappers/python-py/`
- **Runtime deps:** none
- **Model:** BYO-engine. Discovers `amplifier-agent` on PATH and spawns it per turn. Verifies protocol version on first spawn.

#### `amplifier-agent-ts` — TypeScript SDK

- **npm:** `amplifier-agent-ts` (current: `0.7.0`)
- **Source:** `wrappers/typescript/`
- **Runtime deps:** none
- **Node:** `>=20`
- **Model:** Spawns `amplifier-agent` per turn, consumes stderr NDJSON as a stream.

> **Deprecated:** The repo's root `package.json` historically published `amplifier-agent-client-ts`. **Do not use it.** All current adapters depend on `amplifier-agent-ts` from `wrappers/typescript/`. The root package will be marked deprecated on npm.

### 4. HTTP bridge apps

#### `amplifier-app-opencode`

- **Repo:** [microsoft/amplifier-app-opencode](https://github.com/microsoft/amplifier-app-opencode)
- **PyPI:** `amplifier-app-opencode` (current: `0.1.0`)
- **CLI:** `amplifier-opencode`
- **Install:** `uv tool install git+https://github.com/microsoft/amplifier-app-opencode`

**Pattern.** The opencode bridge is the canonical HTTP-face consumer. On launch it:

1. Discovers `amplifier-agent` on PATH (does not pin a version).
2. Spawns `amplifier-agent serve chat-completions --port ... --workspace ... --api-key ...` as a background process.
3. Queries `GET /v1/models`.
4. Writes a working `~/.config/opencode/opencode.jsonc` (or `--project-dir/opencode.json`) from the discovered model catalog. Default port `9099`.
5. `execvp`s `opencode`.

If no `--host-config` is passed, the bridge auto-generates a minimal `host_config.json` from whatever provider env vars are set among `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `AZURE_OPENAI_API_KEY`, `OLLAMA_HOST`.

### 5. SDK-based host adapters

#### `amplifier-app-paperclip`

- **Repo:** [microsoft/amplifier-app-paperclip](https://github.com/microsoft/amplifier-app-paperclip)
- **Adapter package:** `@paperclipai/adapter-amplifier-local` (npm, current: `0.0.1`)
- **Pattern:** TypeScript SDK. Paperclip's native adapter framework calls `amplifier-agent-ts` per turn.
- **Pin:** `amplifier-agent-ts ^0.7.0` (caret — minor and patch auto-upgrade).

#### `amplifier-app-nanoclaw`

- **Repo:** [microsoft/amplifier-app-nanoclaw](https://github.com/microsoft/amplifier-app-nanoclaw)
- **Published artifact:** none. Fork is clone-and-run with Docker.
- **Pattern:** TypeScript SDK **inside a per-agent Docker container**. The container image installs `amplifier-agent` and `amplifier-agent-ts`; the host code routes messages from chat channels into containers.
- **Pin:** `amplifier-agent-ts ^0.7.0`.

## Release impact matrix

When something changes, here is what needs to be released:

| Change in... | Cut release of... | Downstream impact |
|---|---|---|
| An Amplifier module (e.g. `tool-bash`, `provider-anthropic`) | The module itself; no engine release **unless** you bump the version pin in `bundle.md`. | Existing installs keep their cached pin until `bundle.md` changes or the cache is cleared. |
| `bundle.md` (which modules / which versions) | `amplifier-agent` | All SDK consumers and HTTP bridges pick it up on next install/upgrade. Existing hosts re-prepare the bundle on next turn (cache invalidates automatically — different `bundle.md` hash). |
| `amplifier_agent_lib` (engine internals) | `amplifier-agent` | All SDKs and HTTP-bridge apps re-spawn against the new engine on next turn. Bump the **protocol version** if the stdio envelope shape or the NDJSON event schema changed. |
| `amplifier_agent_cli` (CLI flags, subcommands, output) | `amplifier-agent` | If you changed `run`'s stdout JSON shape or `serve`'s endpoints, the wire changed — bump protocol version, then release SDKs / bridges that depend on the changed surface. |
| `amplifier_agent_http` (HTTP face) | `amplifier-agent` | `amplifier-app-opencode` re-validates on next launch; opencode's config is rewritten from `/v1/models`. |
| `amplifier-agent-py` source | `amplifier-agent-py` | Any Python host consuming the SDK. |
| `amplifier-agent-ts` source | `amplifier-agent-ts` | `amplifier-app-nanoclaw` (next container rebuild) and `amplifier-app-paperclip` (next adapter publish). The caret pin (`^0.7.0`) means minor / patch propagate without a republish from adapters. |
| `amplifier-app-opencode` source | `amplifier-app-opencode` | End users `uv tool upgrade amplifier-app-opencode`. |
| `amplifier-app-paperclip` (adapter source) | `@paperclipai/adapter-amplifier-local` (npm) | Paperclip's release machinery propagates. |
| `amplifier-app-nanoclaw` (host or container) | No published artifact — push the fork. | Operators rebuild the Docker image. |
| `amplifier-foundation` | (foundation releases itself.) `amplifier-agent`'s `pyproject.toml` pins it as a git dependency, so to consume the new version cut an `amplifier-agent` release with the bumped pin. | Same as an `amplifier-agent` release. |

**Rule of thumb.** If a change crosses the stdio or HTTP wire — envelope shape, endpoint contract, NDJSON event schema — bump the protocol version field in addition to the package version. SDKs verify protocol version on spawn and will refuse to talk to a mismatched engine.

## Current release process

Releases are **manual** today:

1. Bump the version in the package's `pyproject.toml` (or `package.json`).
2. Commit and push to `main`.
3. Push a git tag matching the version (e.g. `v0.9.1`).
4. For PyPI / npm-published packages, build and publish from the tagged commit.

There is **no CI-driven release automation yet** — automating build and publish on tag push is open (see Open items below).

## Open items

- **Automate releases via CI** — currently fully manual; tag push should trigger build + publish.
- **HTTP face graduation** — `amplifier_agent_http` is labelled `0.0.2-poc`. Promoting it out of POC is on the backlog.
- **Provider catalog vs `bundle.md`** — only four providers are pre-wired (anthropic, openai, azure-openai, ollama). Adding another provider (e.g. github-copilot, gemini, chat-completions, vllm) currently requires editing `bundle.md`. A dynamic provider concept is open.
- **Deprecate `amplifier-agent-client-ts` on npm** — the legacy root-of-repo package should be marked deprecated to avoid confusion with the inner `amplifier-agent-ts`.
- **Pricing in `/v1/models`** — providers should expose a per-model pricing table as part of their model info so HTTP-bridge apps don't need to hand-maintain pricing catalogs (currently a workaround for opencode).

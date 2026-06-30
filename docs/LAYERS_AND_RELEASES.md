# Amplifier Agent — Layers and Releases

This document explains how the `amplifier-agent` ecosystem is layered, what each layer publishes, and what needs to be released when something changes. It is the reference for anyone integrating `amplifier-agent` into a host application or contributing changes to it.

It deliberately does **not** hard-code version numbers — those go stale. See [Where to find current versions](#where-to-find-current-versions) for authoritative sources.

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
- **PyPI:** `amplifier-agent`
- **Install:** `uv tool install git+https://github.com/microsoft/amplifier-agent`

A single Python package containing three internal subpackages:

| Subpackage | Role |
|---|---|
| `amplifier_agent_lib` | The engine — `boot`, `submit_turn`, `shutdown`. Mode-agnostic, no I/O. Calls `foundation.load_and_prepare_cached()`. |
| `amplifier_agent_cli` | The CLI — `amplifier-agent run`, `serve`, `doctor`, etc. Owns stdout / stderr discipline. |
| `amplifier_agent_http` | The HTTP face — FastAPI app, `/v1/chat/completions` and `/v1/models`. Currently labelled as a POC (see the `version=` argument in `src/amplifier_agent_http/app.py`). |

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

- **Bundle name:** `amplifier-agent-behavioral-anchor`
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

- **PyPI:** `amplifier-agent-py`
- **Source:** `wrappers/python-py/`
- **Runtime deps:** none
- **Model:** BYO-engine. Discovers `amplifier-agent` on PATH and spawns it per turn. Verifies protocol version on first spawn.

#### `amplifier-agent-ts` — TypeScript SDK

- **npm:** `amplifier-agent-ts`
- **Source:** `wrappers/typescript/`
- **Runtime deps:** none
- **Node:** `>=20`
- **Model:** Spawns `amplifier-agent` per turn, consumes stderr NDJSON as a stream.

> **Deprecated:** The repo's root `package.json` historically published `amplifier-agent-client-ts`. **Do not use it.** All current adapters depend on `amplifier-agent-ts` from `wrappers/typescript/`. The root package will be marked deprecated on npm.

### 4. HTTP bridge apps

#### `amplifier-app-opencode`

- **Repo:** [microsoft/amplifier-app-opencode](https://github.com/microsoft/amplifier-app-opencode)
- **PyPI:** `amplifier-app-opencode`
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
- **Adapter package:** `@paperclipai/adapter-amplifier-local` (npm)
- **Pattern:** TypeScript SDK. Paperclip's native adapter framework calls `amplifier-agent-ts` per turn.
- **Pin:** caret pin on `amplifier-agent-ts` — minor and patch propagate without a republish from the adapter. See `packages/adapters/amplifier-local/package.json` in the paperclip repo.

#### `amplifier-app-nanoclaw`

- **Repo:** [microsoft/amplifier-app-nanoclaw](https://github.com/microsoft/amplifier-app-nanoclaw)
- **Published artifact:** none. Fork is clone-and-run with Docker.
- **Pattern:** TypeScript SDK **inside a per-agent Docker container**. The container image installs `amplifier-agent` and `amplifier-agent-ts`; the host code routes messages from chat channels into containers.
- **Pin:** caret pin on `amplifier-agent-ts`. See `container/agent-runner/package.json` in the nanoclaw repo.

## Where to find current versions

This doc deliberately avoids hard-coding version numbers; they drift. To find the current version of any artifact, look in these places:

| Artifact | Authoritative source in repo | Latest released |
|---|---|---|
| Engine (`amplifier-agent`) | `pyproject.toml` (repo root) | `git tag --list 'engine-v*' \| sort -V \| tail -1`, or the [Releases page](https://github.com/microsoft/amplifier-agent/releases) |
| TS SDK (`amplifier-agent-ts`) | `wrappers/typescript/package.json` | `git tag --list 'wrapper-v*' \| sort -V \| tail -1`, or [npmjs.com/package/amplifier-agent-ts](https://www.npmjs.com/package/amplifier-agent-ts) |
| Python SDK (`amplifier-agent-py`) | `wrappers/python-py/pyproject.toml` | `git tag --list 'wrapper-py-v*' \| sort -V \| tail -1` |
| Shipped bundle | `bundle:` block in `src/amplifier_agent_lib/bundle/bundle.md` | Moves with engine releases |
| Protocol version | `PROTOCOL_VERSION` in `src/amplifier_agent_lib/protocol/methods.py` | Bumped in the same PR as wrapper updates |
| HTTP face status | `version=` in `src/amplifier_agent_http/app.py` FastAPI factory | Same |
| `amplifier-app-opencode` | `pyproject.toml` in [its repo](https://github.com/microsoft/amplifier-app-opencode) | Its own tags / releases |
| `@paperclipai/adapter-amplifier-local` | `packages/adapters/amplifier-local/package.json` in [paperclip's repo](https://github.com/microsoft/amplifier-app-paperclip) | Its own releases, or [npmjs.com](https://www.npmjs.com/package/@paperclipai/adapter-amplifier-local) |

## Release impact matrix

When something changes, here is what needs to be released:

| Change in... | Cut release of... | Downstream impact |
|---|---|---|
| An Amplifier module (e.g. `tool-bash`, `provider-anthropic`) | The module itself; no engine release **unless** you bump the version pin in `bundle.md`. | Existing installs keep their cached pin until `bundle.md` changes or the cache is cleared. |
| `bundle.md` (which modules / which versions) | `amplifier-agent` (`engine-v*` tag) | All SDK consumers and HTTP bridges pick it up on next install/upgrade. Existing hosts re-prepare the bundle on next turn (cache invalidates automatically — different `bundle.md` hash). |
| `amplifier_agent_lib` (engine internals) | `amplifier-agent` (`engine-v*` tag) | All SDKs and HTTP-bridge apps re-spawn against the new engine on next turn. Bump the **protocol version** if the stdio envelope shape or the NDJSON event schema changed. |
| `amplifier_agent_cli` (CLI flags, subcommands, output) | `amplifier-agent` (`engine-v*` tag) | If you changed `run`'s stdout JSON shape or `serve`'s endpoints, the wire changed — bump protocol version, then release SDKs / bridges that depend on the changed surface. |
| `amplifier_agent_http` (HTTP face) | `amplifier-agent` (`engine-v*` tag) | `amplifier-app-opencode` re-validates on next launch; opencode's config is rewritten from `/v1/models`. |
| `amplifier-agent-py` source | `amplifier-agent-py` (`wrapper-py-v*` tag) | Any Python host consuming the SDK. |
| `amplifier-agent-ts` source | `amplifier-agent-ts` (`wrapper-v*` tag) — auto-published to npm via `publish-wrapper.yml` | `amplifier-app-nanoclaw` (next container rebuild) and `amplifier-app-paperclip` (next adapter publish). The caret pin in each adapter means minor / patch propagate without a republish from the adapter. |
| `amplifier-app-opencode` source | `amplifier-app-opencode` (its own repo / tags) | End users `uv tool upgrade amplifier-app-opencode`. |
| `amplifier-app-paperclip` (adapter source) | `@paperclipai/adapter-amplifier-local` (npm; paperclip repo) | Paperclip's release machinery propagates. |
| `amplifier-app-nanoclaw` (host or container) | No published artifact — push the fork. | Operators rebuild the Docker image. |
| `amplifier-foundation` | (foundation releases itself.) `amplifier-agent`'s `pyproject.toml` pins it as a git dependency, so to consume the new version cut an `amplifier-agent` release with the bumped pin. | Same as an `amplifier-agent` release. |

**Rule of thumb.** If a change crosses the stdio or HTTP wire — envelope shape, endpoint contract, NDJSON event schema — bump the protocol version field in addition to the package version. SDKs verify protocol version on spawn and will refuse to talk to a mismatched engine.

## Current release process

The engine, TS wrapper, and Python wrapper each release independently from this repo, and **tag namespaces are per-artifact**. Pushing the wrong tag namespace silently won't trigger the right workflow.

| Artifact | Tag prefix | Publish target | Automation |
|---|---|---|---|
| Engine (`amplifier-agent`) | `engine-v*` | git (consumers `uv tool install` from the tag) | Manual — bump `pyproject.toml`, push tag. `.github/workflows/release-notes.yml` generates the release notes. |
| TS SDK (`amplifier-agent-ts`) | `wrapper-v*` | npm | Automated — `.github/workflows/publish-wrapper.yml` performs an OIDC publish on `wrapper-v*` tag push. |
| Python SDK (`amplifier-agent-py`) | `wrapper-py-v*` | git (consumers install from the tag) | Manual — bump `pyproject.toml`, push tag. |

To cut a release of any of these:

1. Bump the version in the package's `pyproject.toml` (or `package.json`).
2. Commit and push to `main`.
3. Push the matching tag with the correct prefix.
4. For the TS wrapper, `publish-wrapper.yml` runs automatically. For the engine and Python wrapper, no further action is needed for git-based consumers.

The downstream apps (`amplifier-app-opencode`, `amplifier-app-paperclip`, `amplifier-app-nanoclaw`) live in their own repos and have their own release processes — they are not driven from this repo's tag namespaces.

## Open items

- **Automate engine + Python wrapper release on tag push.** The TS wrapper auto-publishes via OIDC on `wrapper-v*` tags. The engine (`engine-v*`) and Python wrapper (`wrapper-py-v*`) are still manual; both could be wired through equivalent workflows.
- **HTTP face graduation.** `amplifier_agent_http` is labelled as a POC. Promoting it out of POC is on the backlog.
- **Provider catalog vs `bundle.md`.** Only four providers are pre-wired (anthropic, openai, azure-openai, ollama). Adding another provider (e.g. github-copilot, gemini, chat-completions, vllm) currently requires editing `bundle.md`. A dynamic provider concept is open.
- **Deprecate `amplifier-agent-client-ts` on npm.** The legacy root-of-repo package should be marked deprecated to avoid confusion with the inner `amplifier-agent-ts`.
- **Pricing in `/v1/models`.** Providers should expose a per-model pricing table as part of their model info so HTTP-bridge apps don't need to hand-maintain pricing catalogs (currently a workaround for opencode).

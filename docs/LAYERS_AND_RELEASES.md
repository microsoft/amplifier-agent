# Amplifier Agent: Layers and Releases

This document explains how the `amplifier-agent` ecosystem is layered, what each layer publishes, and what needs to be released when something changes. It is the reference for anyone integrating `amplifier-agent` into a host application or contributing changes to it.

It deliberately does **not** hard-code version numbers. Those go stale. See [Where to find current versions](#where-to-find-current-versions) for authoritative sources.

Scope: which layer a change lands in, and what that means for releasing. The install, update, versioning, and compatibility contract lives in [`docs/spec/install-and-distribution.md`](spec/install-and-distribution.md) and is not restated here.

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
|  amplifier-agent (installed from git)                               |
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
- **Install:** `uv tool install git+https://github.com/microsoft/amplifier-agent`
- **PyPI:** `amplifier-agent` is published by CI on every `v*` tag, but git is the supported and tested channel. `install.sh` and `amplifier-agent update` both install from git; nothing in this repo installs from PyPI. See [`docs/spec/install-and-distribution.md`](spec/install-and-distribution.md).

A single Python package containing three internal subpackages:

| Subpackage | Role |
|---|---|
| `amplifier_agent_lib` | The engine: `boot`, `submit_turn`, `shutdown`. Mode-agnostic, no I/O. Calls `foundation.load_and_prepare_cached()`. |
| `amplifier_agent_cli` | The CLI: `amplifier-agent run`, `serve`, `doctor`, etc. Owns stdout / stderr discipline. |
| `amplifier_agent_http` | The HTTP face: FastAPI app, `/v1/chat/completions` and `/v1/models`. Currently labelled as a POC (see the `version=` argument in `src/amplifier_agent_http/app.py`). |

**Console scripts:**

- `amplifier-agent`: dispatcher for `run`, `serve {chat-completions,status,stop,restart}`, `doctor`, `prepare`, `verify`, `update`, `version`, `config show`, `cache clear`, `models list`, `skills list`, `modes list`, `auth`.
- `amplifier-agent-post-install`: first-run setup hook.

**stdio protocol (mode A: `amplifier-agent run`):**

- **stdout:** exactly one JSON envelope per invocation:
  ```json
  {"protocolVersion":"...","sessionId":"...","turnId":"...","reply":"...","error":null,"metadata":{...}}
  ```
- **stderr (optional, with `--display ndjson`):** newline-delimited JSON-RPC notifications for SDKs to consume as a streaming event source. NDJSON is **not** on stdout.

**HTTP protocol (mode B: `amplifier-agent serve chat-completions`):**

- `POST /v1/chat/completions`: OpenAI-compatible, streams SSE chunks. Client sends full conversation history each turn; server is stateless-on-the-wire but reconciles to an internal session via the `X-Client-Session-Id` header (client-wins on divergence).
- `GET /v1/models`: OpenAI-shape model list with extension fields (`display_name`, `limit`, `capabilities`, `reasoning`, `defaults`, `_provider`).
- `GET /v1/skills`: user-invocable skills as `{"object":"list","data":[{name,description,source,shadowed}]}`. Shares its `resources.py` source of truth with `skills list`.
- `GET /v1/modes`: all shipped modes as `{"object":"list","data":[{name,description,source,shadowed}]}`. Shares its `resources.py` source of truth with `modes list`.
- `GET /docs`: OpenAPI UI.
- Lifecycle commands `serve status`, `serve stop`, `serve restart` use a state file on disk to discover and manage the running server.

### 2. The shipped bundle: `bundle.md`

The engine ships with `bundle.md` baked into the wheel. It declares which modules the engine loads at first run.

- **Bundle name:** `amplifier-agent-behavioral-anchor`
- **Path in repo:** `src/amplifier_agent_lib/bundle/bundle.md`

**Pre-wired modules:**

- **Providers:** `provider-anthropic`, `provider-openai`, `provider-azure-openai`, `provider-ollama`, `provider-github-copilot`
- **Orchestrator:** `loop-streaming` (with `extended_thinking: true`)
- **Context:** `context-simple` (300K tokens, auto-compact at 80%)
- **Tools:** `tool-filesystem`, `tool-bash`, `tool-web`, `tool-search`, `tool-todo`, `tool-apply-patch`, `tool-delegate`, `tool-mcp`, `tool-skills`, `tool-mode`, `tool-recipes`
- **Hooks:** `hooks-status-context`, `hooks-redaction`, `hooks-todo-reminder`, `hooks-session-naming`, `hooks-mode`, `hooks-routing`, `hook-context-intelligence`
- **Vendored agents:** `explorer`, `architect`, `builder`, `debugger`, `git-ops`, `researcher`

Modules are **not** bundled. They are git-cloned and editable-installed on first run. The prepared bundle is cached at `~/.amplifier-agent/cache/prepared/<aaa_version>/<sha256(bundle.md)>/`. **Editing `bundle.md` self-invalidates the cache** because the cache key includes its hash.

### 3. Client SDKs

Both SDKs live inside the `amplifier-agent` repo under `wrappers/`.

#### `amplifier-agent-py`: Python SDK

- **PyPI:** `amplifier-agent-py`
- **Source:** `wrappers/python-py/`
- **Runtime deps:** none
- **Model:** BYO-engine. Discovers `amplifier-agent` on PATH and spawns it per turn. Verifies protocol version on first spawn.

#### `amplifier-agent-ts`: TypeScript SDK

- **npm:** `amplifier-agent-ts`
- **Source:** `wrappers/typescript/`
- **Runtime deps:** none
- **Node:** `>=20`
- **Model:** Spawns `amplifier-agent` per turn, consumes stderr NDJSON as a stream.

> **The root `package.json` is not a release artifact.** It is named `amplifier-agent-client-ts`, but it is the pnpm workspace root manifest (`pnpm-workspace.yaml` lists `wrappers/typescript` and `wrappers/conformance`). No workflow publishes it, and it carries no deprecation marker. All current adapters depend on `amplifier-agent-ts` from `wrappers/typescript/`, which is what `publish-wrapper.yml` publishes.

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
- **Pin:** caret pin on `amplifier-agent-ts`. Minor and patch propagate without a republish from the adapter. See `packages/adapters/amplifier-local/package.json` in the paperclip repo.

#### `amplifier-app-nanoclaw`

- **Repo:** [microsoft/amplifier-app-nanoclaw](https://github.com/microsoft/amplifier-app-nanoclaw)
- **Published artifact:** none. Fork is clone-and-run with Docker.
- **Pattern:** TypeScript SDK **inside a per-agent Docker container**. The container image installs `amplifier-agent` and `amplifier-agent-ts`; the host code routes messages from chat channels into containers.
- **Pin:** caret pin on `amplifier-agent-ts`. See `container/agent-runner/package.json` in the nanoclaw repo.

## Where to find current versions

This doc deliberately avoids hard-coding version numbers; they drift. To find the current version of any artifact, look in these places:

| Artifact | Authoritative source in repo | Latest released |
|---|---|---|
| Engine (`amplifier-agent`) | `pyproject.toml` (repo root) | `git tag --list 'v*' \| sort -V \| tail -1`, or the [Releases page](https://github.com/microsoft/amplifier-agent/releases) |
| TS SDK (`amplifier-agent-ts`) | `wrappers/typescript/package.json` | `git tag --list 'wrapper-v*' \| sort -V \| tail -1`, or [npmjs.com/package/amplifier-agent-ts](https://www.npmjs.com/package/amplifier-agent-ts) |
| Python SDK (`amplifier-agent-py`) | `wrappers/python-py/pyproject.toml` | `git tag --list 'py-v*' \| sort -V \| tail -1`, or [pypi.org/project/amplifier-agent-py](https://pypi.org/project/amplifier-agent-py/) |
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
| `bundle.md` (which modules / which versions) | `amplifier-agent` (`v*` tag) | All SDK consumers and HTTP bridges pick it up on next install/upgrade. Existing hosts re-prepare the bundle on next turn (cache invalidates automatically: different `bundle.md` hash). |
| `amplifier_agent_lib` (engine internals) | `amplifier-agent` (`v*` tag) | All SDKs and HTTP-bridge apps re-spawn against the new engine on next turn. Bump the **protocol version** if the stdio envelope shape or the NDJSON event schema changed. |
| `amplifier_agent_cli` (CLI flags, subcommands, output) | `amplifier-agent` (`v*` tag) | If you changed `run`'s stdout JSON shape or `serve`'s endpoints, the wire changed. Bump protocol version, then release SDKs / bridges that depend on the changed surface. |
| `amplifier_agent_http` (HTTP face) | `amplifier-agent` (`v*` tag) | `amplifier-app-opencode` re-validates on next launch; opencode's config is rewritten from `/v1/models`. |
| `amplifier-agent-py` source | `amplifier-agent-py` (`py-v*` tag), auto-published to PyPI via `publish-python.yml` | Any Python host consuming the SDK. No GitHub Release is created: `release-notes.yml` does not trigger on `py-v*`. |
| `amplifier-agent-ts` source | `amplifier-agent-ts` (`wrapper-v*` tag), auto-published to npm via `publish-wrapper.yml` | `amplifier-app-nanoclaw` (next container rebuild) and `amplifier-app-paperclip` (next adapter publish). The caret pin in each adapter means minor / patch propagate without a republish from the adapter. |
| `amplifier-app-opencode` source | `amplifier-app-opencode` (its own repo / tags) | End users `uv tool upgrade amplifier-app-opencode`. |
| `amplifier-app-paperclip` (adapter source) | `@paperclipai/adapter-amplifier-local` (npm; paperclip repo) | Paperclip's release machinery propagates. |
| `amplifier-app-nanoclaw` (host or container) | No published artifact. Push the fork. | Operators rebuild the Docker image. |
| `amplifier-foundation` | (foundation releases itself.) `amplifier-agent`'s `pyproject.toml` pins it as a git dependency, so to consume the new version cut an `amplifier-agent` release with the bumped pin. | Same as an `amplifier-agent` release. |

**Rule of thumb.** If a change crosses the stdio or HTTP wire (envelope shape, endpoint contract, NDJSON event schema), bump the protocol version field in addition to the package version. SDKs verify protocol version on spawn and will refuse to talk to a mismatched engine.

## Current release process

The engine, TS wrapper, and Python wrapper each release independently from this repo, and **tag namespaces are per-artifact**. There are exactly three, and a tag outside them triggers nothing:

```
v*          publish-python.yml (publish-engine job)   amplifier-agent    -> PyPI via OIDC
            release-notes.yml                         GitHub Release
py-v*       publish-python.yml (publish-wrapper job)  amplifier-agent-py -> PyPI via OIDC
            (no GitHub Release: release-notes.yml does not trigger on py-v*)
wrapper-v*  publish-wrapper.yml                       amplifier-agent-ts -> npm via OIDC
            release-notes.yml                         GitHub Release
```

Nothing here is manual. All three publish jobs use OIDC trusted publishing, so there are no token secrets, and each one verifies the tag version against the package manifest and fails the job on a mismatch.

To cut a release:

1. Bump the version in the package's `pyproject.toml` (or `package.json`).
2. Commit and merge to `main`.
3. Push the matching tag from the tip of `main`. The workflow does the rest.

Engine consumers install from git, so the git tag is what reaches them; the PyPI upload rides along on the same tag push. Step-by-step commands and the one-time PyPI trusted-publisher setup are in [`RELEASING.md`](../RELEASING.md).

`release-notes.yml` marks a release as a prerelease when the tag name contains `-`. Every `wrapper-v*` tag does, so TS wrapper releases are always marked prerelease.

The downstream apps (`amplifier-app-opencode`, `amplifier-app-paperclip`, `amplifier-app-nanoclaw`) live in their own repos and have their own release processes. They are not driven from this repo's tag namespaces.

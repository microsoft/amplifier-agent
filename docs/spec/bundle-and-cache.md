# Bundle and Cache

## Scope

What the vendored bundle manifest declares, how the prepared-bundle cache is keyed and invalidated,
and what the cold and warm paths cost. The per-session transforms applied to a prepared bundle (mcp
env, config merge, workspace seed) belong to `engine-api.md`; this document does not duplicate them.
The `host_config` schema that parameterizes the manifest is in `host-config.md`. The on-disk root the
cache lives under is in `storage-and-workspace.md`.

## The vendored manifest

The bundle manifest is an opinionated `bundle.md` shipped inside the wheel. There is no user-supplied
bundle, no `--bundle` flag, and no bundle discovery. What varies between deployments is host config,
never bundle composition.

```
IN  the wheel: the bundle manifest text, agent definitions, the system context, skills, modes
NOT in wheel:  module source trees, module wheels, prepared cache artifacts, third-party module deps
```

Every module is declared inline with an explicit module id and source URL. There is no `includes:`
block, no named-bundle references, and no dependency on a registry populated out of band by another
tool.

What the manifest declares:

```
default_provider: anthropic                REQUIRED, engine-level, top-level key

providers:                                 install-only stubs, no config and no credentials
  provider-anthropic, provider-openai, provider-azure-openai,
  provider-ollama, provider-github-copilot, provider-openai-chatgpt,
  provider-chat-completions, provider-gemini

session.orchestrator: loop-streaming       extended_thinking: true
session.context:      context-simple       max_tokens 300000, auto_compact
session.provider:     the anthropic provider, as the runtime default entry

tools:
  tool-filesystem, tool-bash, tool-web, tool-search, tool-todo, tool-apply-patch,
  tool-delegate (self_delegation, session_resume, context_inheritance, provider_selection;
                 excludes tool-delegate from sub-agents),
  tool-mcp, tool-skills, tool-mode, tool-recipes

hooks:
  hooks-status-context, hooks-redaction, hooks-todo-reminder, hooks-session-naming,
  hooks-mode, hooks-routing (default_matrix: balanced), hook-context-intelligence

agents:
  explorer, architect, builder, debugger, git-ops, researcher
```

Agents declare no `tools:` blocks; they inherit the parent tool roster through tool-delegate's
`context_inheritance`. Modules referenced only by agent definitions are installed alongside the
top-level ones, so a delegated session can always mount what its agent declares.

Four upstream modules are deliberately absent relative to the upstream behavioral-anchor bundle:
`hooks-streaming-ui` and `hooks-todo-display` would break the JSON-stdout contract,
`behaviors/logging.yaml` is replaced by `hook-context-intelligence`, and `hooks-approval` is dropped
because the wire protocol has no approval round-trip yet and policy-driven rules would deadlock.

The `providers:` stubs carry NO config and NO credentials. They exist so that every provider module
is installed during cold prepare, which matters for `amplifier-agent serve chat-completions`, where
the server lists provider models before creating the first session. Credentials flow from env vars at
runtime, and both the CLI and the HTTP runner clear the mounted provider list first so only the
single user-selected provider is mounted.

`default_provider:` is REQUIRED. It is a top-level key, sibling to `bundle:`, `session:`, and
`tools:`, not nested under `bundle:`. A missing or non-string value raises `bundle_load_failed`,
classification `protocol`, with the message "bundle.md missing required `default_provider:`
top-level field. This is a bundle integrity error; reinstall amplifier-agent."

Sealed means sealed MANIFEST TEXT, nothing more. Every module source is pinned at `@main`, so
upstream module updates flow automatically. Drift is intentional product behavior, not a defect.
amplifier-agent has no commit access to those repos and no release coordination with them. Every
declared module must install without building `amplifier-core` from source, which would require Rust
and protobuf toolchains users do not have.

## Cache key and invalidation

The prepared bundle is cached under a two-part key: the amplifier-agent version and the first 16 hex
characters of the SHA-256 of the manifest text.

```
~/.amplifier-agent/cache/prepared/<version>/<sha256(bundle.md)[:16]>/
    <opaque binary artifact>   the prepared bundle
    manifest.json              { "aaa_version": "<version>", "bundle_sha256_prefix": "<sha256[:16]>" }
```

Both artifacts must be present for a warm hit. The content hash is load bearing: keying on version
alone means two builds with identical version strings but different manifests share a cache directory
and serve an incorrect warm hit. Two-part keying makes that structurally impossible rather than
merely unlikely.

The consequence rule is a real user-facing contract: CHANGING THE SHIPPED MANIFEST OR THE VERSION
CHANGES THE CACHE KEY AND INVALIDATES THE PREVIOUS ENTRY AUTOMATICALLY. The old entry is bypassed,
the cold path runs once, and a new entry lands under the new key. No `cache clear` is needed, ever,
for correctness.

The cached artifact is opaque and its format is not part of the contract. A corrupted or unreadable
artifact is a cache MISS, never a hard error: it is discarded, a warning is logged, and the entry is
rebuilt from the cold path. Reading an artifact written by a different version is structurally
impossible, since the version is a path component.

## Cold and warm paths

First run for a given key takes roughly 30 to 60 seconds while the prepared bundle is built. The
result is cached, and every subsequent run with the same version and manifest is a warm hit that
completes in well under a second.

An opt-in `amplifier-agent-post-install` console script primes the cache so the first real invocation
is warm. It stays opt-in, it is idempotent (it returns 0 immediately when the cache entry already
exists), and its failure never fails an install, because the runtime cold path on first invocation is
the safety net.

## Cache lifecycle

Old cache directories are NEVER garbage collected. Every version bump and every manifest change
creates a new `<version>/<hash>/` directory and leaves the previous ones in place, so `prepared/`
accumulates indefinitely. This is real contract, not an oversight to be silently fixed: nothing
prunes it.

The only removal path is `amplifier-agent cache clear`, which removes the ENTIRE `prepared/` root,
all versions and all hashes. It is idempotent and exits 0 whether or not the directory existed, and
it prints the path it removed.

There is no partial invalidation. You cannot clear one version, one manifest hash, or one module.

## Non-goals

- No user-supplied bundle. The manifest is vendored and sealed; there is no `--bundle` flag and no
  bundle discovery. Host config parameterizes what the manifest declares and cannot change what it
  declares.
- No partial cache invalidation. The key is whole-manifest; `cache clear` is all-or-nothing.
- No cache garbage collection. Nothing prunes old `prepared/<version>/<hash>/` directories.
- No `includes:` block, no dependency on a bundle registry, and no sharing of the amplifier CLI's
  bundle. amplifier-agent diverges from amplifier-app-cli on packaging by design.
- Module sources are not pinned to tags or SHAs. Pinning would gate amplifier-agent releases on
  module-repo state.
- Vendoring module wheels or a pre-built prepared artifact is rejected. Both force pinning module
  SHAs at release time, and a vendored prepared artifact additionally couples the wheel to a specific
  runtime version.
- Mandatory post-install cache priming. The console script stays opt-in.

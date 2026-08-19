# Providers and Models

## Scope

Covers the supported providers, credential resolution, the credentials file, provider selection at
boot, model-id namespacing, provider configuration precedence, and `amplifier-agent models list`.
It does not cover the `host_config.json` schema itself (see `host-config.md`) or how the HTTP face
routes a wire `model` field to a provider (see `http-face.md`).

## Supported providers

Nine providers are supported, and only nine. The provider name is the value used in configuration,
in `auth` subcommands, and in `models list --provider`.

```
anthropic          provider-anthropic
openai             provider-openai
azure-openai       provider-azure-openai
ollama             provider-ollama
github-copilot     provider-github-copilot
openai-chatgpt     provider-openai-chatgpt
chat-completions   provider-chat-completions
gemini             provider-gemini
vllm               provider-vllm
```

Each module is installed from `git+https://github.com/microsoft/amplifier-module-<module>@main`.
All nine are declared by the shipped bundle (`bundle.md`'s top-level `providers:` stub list) as
install-only, so preparing the bundle makes every provider importable before any session exists.

The agent holds no static table of default models, credential field shapes, or display names. Those
come from the provider module at runtime, so they cannot drift from provider truth.

## Credential resolution

Environment first, matching the `gh` / `aws` / `claude` convention: a shell export wins over the
persisted file, so a one-off invocation can point at a different key without disturbing stored
configuration.

```
1. primary environment variable
2. alias environment variable(s)   one-time deprecation notice on stderr
3. credentials file entry          ~/.amplifier-agent/credentials.json
4. nothing                         source "none"  (ollama: source "default", built-in local host)
```

Primary variables, in the order consulted:

```
anthropic          ANTHROPIC_API_KEY
openai             OPENAI_API_KEY
azure-openai       AZURE_OPENAI_API_KEY, then AZURE_OPENAI_KEY
ollama             OLLAMA_HOST, then OLLAMA_BASE_URL
github-copilot     GITHUB_TOKEN
openai-chatgpt     (none -- OAuth device-code)
chat-completions   CHAT_COMPLETIONS_BASE_URL, plus optional CHAT_COMPLETIONS_API_KEY
gemini             GOOGLE_API_KEY
vllm               VLLM_BASE_URL (required), plus optional VLLM_API_KEY
```

`AZURE_OPENAI_KEY` is the only deprecated alias. Consulting it emits a one-time warning on stderr.
`OLLAMA_BASE_URL` is a second, non-deprecated ollama variable and emits no warning. When neither
ollama variable is set, the host is `http://localhost:11434`.

`AZURE_OPENAI_ENDPOINT` supplies the `endpoint` field alongside `api_key`, falling back to the
`endpoint` stored in the credentials file.

github-copilot lists only `GITHUB_TOKEN` here. The provider module resolves its own chain
(`COPILOT_AGENT_TOKEN`, `COPILOT_GITHUB_TOKEN`, `GH_TOKEN`, `GITHUB_TOKEN`); listing those here
would mark them deprecated, which they are not.

openai-chatgpt has no environment variable at all. It resolves from a cached OAuth token file
(`~/.amplifier/openai-chatgpt-oauth.json`), written by the provider module's own device-code login
flow (`login_on_mount`) and refreshed automatically thereafter. Its resolution reports source
`"file"` when a token is cached and `"none"` otherwise -- never `"env"`.

chat-completions has its own dedicated resolution branch, distinct from the generic
env-then-file chain above: its primary variable's value lands in `fields["base_url"]`, not
`fields["api_key"]`, because the thing it needs to know is *which server to talk to*, not a
secret. `CHAT_COMPLETIONS_API_KEY` is consulted only when set (local servers such as llama.cpp,
vLLM, LM Studio, and LocalAI commonly need none) and lands in `fields["api_key"]` alongside it.
Unlike every other provider, the persisted credentials file is never consulted for
chat-completions -- with no `CHAT_COMPLETIONS_BASE_URL` in the environment it resolves
unconditionally to `source == "none"`, with no file fallback and no usable default to fall back
to (unlike ollama's built-in localhost).

vllm has the same shape of dedicated resolution branch, for the same reason: `VLLM_BASE_URL`
lands in `fields["base_url"]`, and `VLLM_API_KEY` lands in `fields["api_key"]` alongside it.
`VLLM_API_KEY` differs from chat-completions' optional key in one way: when it is unset,
`fields["api_key"]` is still populated, with the same `"EMPTY"` placeholder the provider module
defaults to. A local vLLM server commonly needs no auth, but the OpenAI SDK the module wraps
still requires some value, and `models list` builds the provider straight from these fields
rather than through the module's `mount()` — so omitting the field entirely would hand that
path an empty key and fail against a keyless server. The placeholder is not a credential: a
key supplied through host config's `provider.config` takes precedence over it, while a real
`VLLM_API_KEY` from the environment is re-asserted over host config as usual. The persisted
credentials file
is never consulted for vllm either -- with no `VLLM_BASE_URL` in the environment it resolves
unconditionally to `source == "none"`, with no file fallback and no usable default to fall back
to. The distinction from chat-completions is the wire, not the credential shape: vllm targets
vLLM's OpenAI-compatible **Responses API** (`/v1/responses`), not the Chat Completions API, which
is what lets it support reasoning models, reasoning-block separation, and tool calling.

gemini lists only `GOOGLE_API_KEY` here. The Google GenAI SDK also accepts `GEMINI_API_KEY`
(`GOOGLE_API_KEY` takes precedence when both are set), and the provider module's own env read
honours that; listing `GEMINI_API_KEY` in this table would mark it deprecated, which it is not.
Otherwise gemini follows the generic env-then-file chain like anthropic and openai: it is a normal
key-based provider, `auth set gemini` is accepted, and it is not excluded from the credential model
the way github-copilot, openai-chatgpt, and chat-completions are.

A resolution reports the provider, whether it resolved, the source (`env`, `file`, `default`, or
`none`), the variable consulted, and the resolved fields. Ollama backed only by the built-in default
host reports unresolved on purpose, so auto-enrollment does not enlist a local daemon that may not
be running.

Requesting credentials for a key-based provider that resolves to `none` is an error. Ollama,
chat-completions, vllm, and unrecognized provider names never raise -- chat-completions and vllm
are not among the key-based providers this rule applies to, so a missing `CHAT_COMPLETIONS_BASE_URL`
or `VLLM_BASE_URL` is left for the respective provider module to reject at call time, not raised
here.

## The credentials file

Managed by `amplifier-agent auth set | list | remove | status | clear`.

```
path   ~/.amplifier-agent/credentials.json   (honours AMPLIFIER_AGENT_HOME)
file   mode 0600
dir    mode 0700
```

v1 schema:

```json
{
  "version": 1,
  "providers": {
    "anthropic": { "api_key": "sk-ant-..." },
    "azure-openai": { "api_key": "...", "endpoint": "https://..." }
  }
}
```

Writes are atomic and the file is never observable at a looser mode: the permission bits are set on
the temporary file before it is renamed into place. Setting the parent directory mode is best-effort
and does not fail the write on a shared mount.

A legacy flat `{provider: key}` file is read as if wrapped in the v1 envelope and silently upgraded
on the next write. Unknown provider keys round-trip verbatim. A malformed file fails the write path
with an error but resolves as empty on the read path, so one bad write does not break every later
invocation.

`auth set github-copilot` and `auth set openai-chatgpt` are both refused, for different reasons.
The agent normalizes every credential into an `api_key` config field: github-copilot's module reads
only the environment and ignores it, so a stored value would report success and change nothing.
openai-chatgpt has no static key at all -- it authenticates via OAuth device-code and caches tokens
to `~/.amplifier/openai-chatgpt-oauth.json`, refreshed by the provider module itself. Both refusals
are enumerated in the same `_CONFIG_CREDENTIAL_UNSUPPORTED` gate; this is temporary and specific to
these two providers.

`auth set chat-completions` is accepted (unlike github-copilot, it is not refused), but as noted
above the chat-completions resolution branch never reads the credentials file -- only
`CHAT_COMPLETIONS_BASE_URL` / `CHAT_COMPLETIONS_API_KEY` in the environment are consulted. Set
those instead of relying on `auth set` for this provider.

`auth set vllm` is likewise accepted and likewise ignored at resolution time -- only
`VLLM_BASE_URL` / `VLLM_API_KEY` in the environment are consulted. Set those instead of relying on
`auth set` for this provider.

`auth clear` without `--force` exits 2.

## Provider selection at boot

```
1. host_config provider.module
2. the default provider declared by the shipped bundle (currently "anthropic")
3. no further fallback: a bundle declaring neither is a hard error at boot
```

`provider.module` is closed to the nine supported names. Any other value fails validation with
error code `config_invalid_provider_module`. `"auto"` is not a valid value.

There is no `--provider` flag and no environment-based provider auto-detection. See Non-goals.

## Provider configuration

`host_config.json` is the single configuration channel. There is no argv tier.

```
provider module      host_config provider.module
                   > bundle default provider

per-provider config  host_config provider.config[*]   (pass-through, keys not enumerated)
                   > the provider module's own defaults
```

`provider.config` is forwarded verbatim to the mounted provider, so `default_model`, `effort`,
`temperature`, `max_tokens`, `thinking_budget_tokens`, and any future provider-specific key reach
it unchanged. Keys absent from `provider.config` are not synthesized, so the provider's own
defaults apply.

Guarantees that hold regardless of what is on disk:

- Credentials are resolved per invocation, never cached. A rotated key takes effect on the next run
  with no cache clear, and no cached artifact on disk ever contains secrets.
- Freshly resolved credential fields and the provider's mount priority are reapplied over
  `provider.config`, so a stale config file cannot downgrade a credential or hijack mount priority.
- `debug.rawLlmPayloads: true` enables raw payload capture on the provider, and `provider.config`
  is layered on top of it.
- Both the CLI and the HTTP face build provider configuration the same way, so `--config` means the
  same thing under `run` and under `serve`.

There is no boot-time validation that a configured `default_model` belongs to the chosen provider.
The provider rejects a bad id at its first API call.

The CLI additionally selects a provider-appropriate model routing matrix:

```
anthropic      -> anthropic
openai         -> openai
azure-openai   -> openai       (same model family)
ollama         -> ollama
github-copilot -> copilot
```

When the bundle declares no routing hook, or the provider is not in that map, the bundle's own
default matrix stands. The HTTP face does not perform this selection; a served request always uses
the bundle's default matrix.

Server mode uses a separate `host_config.providers` block, a registry keyed by provider name:

```json
{ "providers": { "anthropic": { "module": "anthropic", "config": { "...": "..." } } } }
```

The server enumerates exactly these providers and passes each entry's `config` through to model
enumeration. When the block is absent or empty, every provider with a resolvable credential is
enabled instead. The entry schema is specified in `host-config.md`; the startup behavior is
specified in `http-face.md`.

## Model id namespacing

The namespace separator is `/`. A *reseller* serves models it did not originate under a
byte-identical id: github-copilot serves `claude-sonnet-5`, and so does anthropic. github-copilot is
currently the only reseller, and it carries the display suffix `" (GitHub)"`.

```
reseller model id      github-copilot/claude-sonnet-5
reseller display name  Claude Sonnet 5 (GitHub)
native model id        claude-sonnet-5          (bare, never namespaced)
```

Namespacing and display decoration are idempotent. Stripping a namespace removes only a recognized
reseller prefix, so an upstream id that merely contains a `/` survives intact.

Where each form appears:

- **Namespaced** on every cross-provider surface: `GET /v1/models` ids, and the `model` field a
  client sends over the wire.
- **Bare** wherever an id reaches a provider module, which only knows its own ids. The namespace is
  stripped before hand-off.
- **Bare** for native providers on both counts. Namespacing them would break every existing client
  and config keyed on the bare id.

## `models list`

```
amplifier-agent models list [--provider NAME] [--output auto|json|table]
                            [--timeout SECONDS] [--latest]

--provider NAME          optional. Omit for aggregate mode: every provider, queried in parallel.
--output auto|json|table default auto -> table on a TTY, json when piped or redirected.
--timeout SECONDS        default 15.0, applied to the live provider query.
--latest                 only the latest model per family. Without it, the full list is requested.
```

Single-provider JSON:

```json
{
  "schema_version": 1,
  "provider": "anthropic",
  "fetched_at": "2026-06-10T17:36:53.123456+00:00",
  "models": [
    {
      "id": "claude-sonnet-5",
      "display_name": "Claude Sonnet 5",
      "context_window": 200000,
      "max_output_tokens": 8192,
      "capabilities": ["tools", "vision", "thinking"],
      "defaults": { "temperature": 0.7, "max_tokens": 8192 }
    }
  ]
}
```

Each `models` entry is the provider's full model record, with `display_name` decorated by the same
rule `/v1/models` uses, so the two surfaces cannot label a model differently. `fetched_at` is the
UTC time of the call. `schema_version` and `fetched_at` exist so consumers can evolve and make
cache-TTL decisions.

Aggregate JSON:

```json
{
  "schema_version": 1,
  "fetched_at": "...",
  "results": [
    { "provider": "anthropic", "status": "ok", "models": [ ... ] },
    { "provider": "openai", "status": "credentials_missing", "models": [],
      "error": "OPENAI_API_KEY not set ..." }
  ]
}
```

Status is one of `ok`, `credentials_missing`, `module_not_installed`, `error`. Providers are queried
concurrently and independently, so one provider's failure never suppresses another's result. Each
failing entry carries a human-readable `error` string.

Single-provider table is four columns; `CAPABILITIES` is comma-joined:

```
ID                 DISPLAY NAME       CONTEXT   CAPABILITIES
claude-sonnet-5    Claude Sonnet 5    200000    tools, vision, thinking
```

Aggregate table is three columns, `PROVIDER STATUS MODELS`. The models cell lists the first three
ids plus a `(N total)` suffix when there are more. For any non-`ok` status the cell is a single
horizontal-dash placeholder character followed by the error message in parentheses, so the table
stands on its own without consulting the JSON form.

Failure order is fixed and does not fall back: credentials are checked first (cheapest, most
actionable failure), then the provider module import, then the live query.

Exit codes:

```
0  success, INCLUDING a legitimately empty list (azure-openai by design, ollama daemon down)
1  usage error: unrecognized --provider value
2  provider error: credentials missing, module not installed, or the provider query failed
2  aggregate mode: only when NO provider reached status "ok"
```

An empty list is a valid answer, not an error: exit 0 plus a one-line advisory on stderr. stdout
carries the structured payload only; every diagnostic goes to stderr.

## Non-goals

- **No `--provider` flag.** Provider selection is `provider.module` in the host config only.
- **No `--model` flag and no `--effort` flag.** Model and effort are configured through
  `provider.config`. There is no argv equivalent and no shim.
- **No provider auto-detection from the environment.** It must not exist. It conflated
  "which provider is configured to run" with "which provider has credentials available." `"auto"`
  is not a valid `provider.module` value and hard-errors at validation. There is no
  `providerAutoDetected` envelope flag and no auto-detect warning.
- **No `models default` subcommand.** Different data shape from `models list`; a consumer reading
  provider defaults can do so itself.
- **No `--source live|catalog` flag and no catalog fallback** on `models list`. The listing is
  always live.
- **No boot-time validation** that a configured `default_model` belongs to the chosen provider.
- The TypeScript wrapper has no spawn-time `providerOverride` / `modelOverride` / `effortOverride`
  fields. The wire-protocol field `InitializeParams.providerOverride` is a separate, live concern
  and is unaffected.

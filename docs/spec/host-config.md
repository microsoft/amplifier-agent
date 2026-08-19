# Host Config

## Scope

The host configuration contract: how the config file is located, the closed schema it must conform
to, how it layers over bundle defaults, and every error it can raise. Workspace identity is
deliberately not part of this schema and lives in `storage-and-workspace.md`. What the bundle itself
declares lives in `bundle-and-cache.md`.

## Resolution

Two tiers, first hit wins:

```
1. --config <path>              argv flag on `amplifier-agent run`
2. $AMPLIFIER_AGENT_CONFIG      environment variable
```

Neither present means there is no config tier at all: bundle defaults apply unchanged. There is
deliberately no `$XDG_CONFIG_HOME/amplifier-agent/config.json` fallback and no implicit file anywhere
on disk. An XDG default under a shared `$HOME` (CI runners with a shared UID, co-resident
non-containerized hosts) creates silent collision, and the case an XDG default would help is exactly
the case where bundle defaults already suffice.

An unreadable path is a HARD ERROR, never a silent skip. A missing file, a directory in place of a
file, a permission failure, or any other I/O failure raises `config_unreadable`. This applies to the
env var identically: setting `$AMPLIFIER_AGENT_CONFIG` is an affirmative declaration that this file
is the config, so a bad path fails loudly instead of producing "why aren't my settings applying?" at
2am.

The format is JSON. The whole amplifier-agent I/O surface is already JSON, JSON parsing has no
code-execution vector, and JSON's explicit typing eliminates the YAML Norway problem (`"no"` is
unambiguously the string, `false` unambiguously the boolean). The stated tradeoff: JSON has no
comment syntax, and `_comment` keys collide with the strict unknown-key rule below. Use an external
companion document when explanation is needed.

A JSON `null` literal at the document root normalizes to `{}`, matching omitted-block semantics. Any
other non-mapping root raises `config_malformed_json`.

## Top-level schema (closed)

Seven keys, and only these:

```
mcp  approval  provider  providers  allowProtocolSkew  skills  debug
```

Any key outside the set raises `config_unknown_key`. There is no `--strict-config` opt-in to soften
this and no escape hatch for forward-compatible keys: strict IS the default. Forward compatibility is
the host's responsibility via `--protocol-version`.

The schema is a pass-through to the configs of modules the bundle already declares. amplifier-agent
does not invent vocabulary, rename keys, or curate which knobs a host can set. Block names match the
modules they parameterize. The load-bearing consequence: amplifier-agent's effective config surface
is COUPLED to module schemas. If the MCP, skills, or a provider module renames a key, the effective
surface changes. This is accepted because the bundle is sealed at each release.

## Block shapes

`mcp` overlays the MCP tool module's config verbatim. No inner validation is performed. `configPath`
is the one convenience key amplifier-agent adds: when set, the engine exports
`$AMPLIFIER_MCP_CONFIG` on the process and the MCP module's own resolution consumes it.
amplifier-agent does not reinvent path resolution.

```json
{ "mcp": { "configPath": "/var/run/amplifier/mcp.json", "verbose_servers": false } }
```

A host that already owns the subprocess environment can set `$AMPLIFIER_MCP_CONFIG` directly and omit
the block entirely. A third path exists but is not a host_config surface: the wire-level
`mcpConfigPath` field on `initialize` params sets the same env var. There is no argv equivalent.

`approval` overlays the approval hook module's config AND feeds the CLI approval-mode resolver. Two
keys are validated; everything else passes through.

```
approval.mode      must be one of {"yes", "no", "prompt"}
approval.patterns  must be a list of strings
```

`provider` selects the provider module and carries its config.

```
provider.module   one of: anthropic, openai, azure-openai, ollama, github-copilot,
                  openai-chatgpt, chat-completions, gemini, vllm
provider.config   free-form; belongs to the provider module
```

The module set is closed. `"auto"` is not a valid value and hard-errors with
`config_invalid_provider_module`. What a host writes under `provider.config` reaches the selected
provider module unchanged.

`providers` (plural) is the server-mode registry, read at HTTP startup and not merged into any
module config. Closed per-entry schema:

```json
{
  "providers": {
    "anthropic": { "module": "anthropic", "config": { "default_model": "claude-sonnet-5" } },
    "copilot":   { "module": "github-copilot" }
  }
}
```

`module` defaults to the entry's own id when omitted and must be one of the nine valid module names.
`config` must be an object. Unknown keys inside an entry raise `config_unknown_key`. An empty
`providers` object passes validation; HTTP startup rejects it separately at boot so single-turn mode
never trips on a stale block.

`skills` has a closed inner shape against exactly two sub-keys:

```
skills.skills      list of source URIs (git+https://, @bundle:path, local paths)
skills.visibility  dict of visibility-hook config; inner keys pass through unvalidated
```

Unknown sub-keys under `skills.*` raise `config_invalid_type`, NOT `config_unknown_key`. That
distinction is deliberate: `config_unknown_key` is reserved for the top level and for the
`providers.<id>` entry schema.

`debug` has a closed inner shape against `{"rawLlmPayloads"}`, which must be a real JSON boolean.
Strings are rejected rather than coerced: providers read their `raw` key as a plain truthiness test,
so `"false"` would silently ENABLE full payload capture. These are developer-only diagnostics, so an
unrecognized sub-key is far more likely a typo leaving diagnostics off than a forward-compatible
extension.

What the key does: `rawLlmPayloads: true` folds into the provider config overlay as `raw: true`.
Both faces apply it identically, so `--config` means the same thing under `run` and under `serve`.
`provider.config` is applied LAST in that overlay, so an explicit `provider.config.raw` beats the
debug block: the debug key is sugar over the same provider flag and the low-level key stays the final
say. What `raw` then captures is the provider module's own observability contract, not
amplifier-agent's. Coverage differs per provider, and the payloads that are captured land in the
session's `events.jsonl` uncapped and untruncated. Treat it as a developer switch, not an audit
feature.

`allowProtocolSkew` is a bare boolean and the one top-level key that is not a module pass-through. It
is engine-level and suppresses the protocol-version skew check. It replaced both the
`--allow-protocol-skew` flag and the `AMPLIFIER_AGENT_ALLOW_PROTOCOL_SKEW` env var, neither of which
exists any more.

## Precedence and merge

```
argv flag  >  host_config  >  bundle default
```

An argv flag is always a forceful override that config cannot silence. host_config is the persistent
expression of host intent. Bundle defaults are the floor.

Within a block, the merge is a shallow per-key overlay onto the module's static config: host keys
replace bundle keys of the same name, and nothing else changes. No recursive merging, no key
renaming, no schema translation. Host omits a block, bundle applies. Host omits a key inside a
present block, bundle's value for that key applies. Host supplies no config file, behavior is
identical to having no config layer. Bundle-declared config is never mutated in place.

The overlay targets, by block:

```
mcp        -> the MCP tool module's config
approval   -> the approval hook module's config
provider   -> the selected provider module's config
skills     -> the skills tool module's config
providers  -> not merged into any module; server-mode registry only
```

`skills.skills` is the exception to shallow overlay: it merges by CONCATENATION, bundle sources
first, host additions appended. The host EXTENDS but cannot SUBTRACT. Replace semantics were rejected
because they silently drop the curated bundle skills the moment a host forgets to re-include the
bundle's source URL. Concatenate-plus-dedupe was rejected as marginal since discovery is already
first-match-wins at the module tier. The rule generalizes: any future list-shaped sub-key uses
concatenate unless a separate decision overrides it. `skills.visibility` stays dict-shaped and merges
shallow per-key.

## $AMPLIFIER_SKILLS_DIR

`$AMPLIFIER_SKILLS_DIR` and the `skills:` block are both live and do not duplicate each other. The
two surfaces serve different audiences.

```
skills: block          persistent declarative surface; requires a JSON file; for host installs
                       whose skill-source set is static across the life of the install
$AMPLIFIER_SKILLS_DIR  per-spawn adapter bridge; no file management; the adapter sets the env var
                       on the subprocess environment it already owns
```

The skills module consults its own default roots ONLY when its configured skill list is empty, and
amplifier-agent always supplies that list. The effective first-match-wins order is therefore:

```
1. <BUNDLE_DIR>/skills        prepended; vendored built-ins win
2. config.skills              bundle sources then host_config additions (concatenation)
3. $AMPLIFIER_SKILLS_DIR      appended, when set (adapter bridge)
4. .amplifier/skills          appended, relative, resolved against the process cwd
5. ~/.amplifier/skills        appended
```

host_config additions therefore OUTRANK the adapter bridge and the workspace and user directories,
and are outranked only by the vendored built-ins. See `skills-and-modes.md` for the discovery path
used by `skills list` and `GET /v1/skills`, which is ordered separately.

## Error codes

Every config error carries classification `protocol`. The classification-to-exit-code mapping is in
`envelope-and-errors.md`, which also carries these codes in its CLI-only code list. What follows is
the per-field detail that only makes sense against the schema above.

```
config_unreadable              Resolved path does not exist, is a directory, or cannot be read.
config_malformed_json          File is not valid JSON, or the root is a non-null non-object.
config_unknown_key             A top-level key outside the 7-key set, or an unknown key inside a
                               `providers.<id>` entry.
config_invalid_type            A typed field has the wrong shape: approval.patterns not a list or
                               containing a non-string; approval.mode not a string or not in
                               {yes,no,prompt}; unknown sub-key under skills.*; skills.visibility
                               not a dict; skills.skills not a list or containing a non-string;
                               debug not a dict, unknown sub-key under debug.*, or
                               debug.rawLlmPayloads not a bool; providers not an object, bad entry
                               shape, or non-dict entry config.
config_invalid_provider_module provider.module outside the 8 valid names, or providers.<id>.module
                               outside them.
config_no_matching_module      host_config declares a non-empty `skills:` block but the bundle has
                               no skills tool module mounted. An empty skills block plus a missing
                               module stays a no-op. No other block raises this code.
```

## Headless approval fail-fast

No approval policy in a non-interactive context is a hard error, not a silent deny. Resolution order:

```
1. -y / --yes                 -> "yes"   (-y with -n is a usage error)
2. -n / --no                  -> "no"
3. host_config.approval.mode  -> that value (already validated at load time)
4. stdin is a TTY             -> "prompt"
5. otherwise                  -> FAIL FAST
```

The fail-fast path emits an error envelope on stdout and exits 2. For the exact code,
classification, message, and remediation text see `envelope-and-errors.md`, which owns error codes
and exit codes.

Only the config-relevant half is stated here: `approval.mode` is tier 3 of the ladder, and a valid
value has already been checked at load time, so tier 3 either supplies one of the three legal modes
or is absent. `approval_unconfigured` is classified `protocol` rather than `approval` because it is a
config-validation failure, not an approval-runtime failure.

The HTTP face deliberately does NOT apply `approval.mode` even when present; it auto-approves,
because the chat-completions wire has no human-in-the-loop seam.

## Provider selection at boot

```
1. host_config.provider.module, if set
2. else `default_provider:` from the vendored bundle manifest
3. no further fallback
```

A missing or non-string `default_provider` is a bundle integrity error and raises
`bundle_load_failed`.

Env-var provider detection does not exist and must not be introduced. It conflated "which provider
is configured to run" with "which provider has credentials available." Config or bundle decides which
provider runs; the provider module raises loudly at startup if its API key env var is missing.

## Non-goals

- No XDG config default and no implicit config file. The two tiers above are the whole resolution.
- No YAML. JSON only.
- No partial or lenient parsing. A malformed or unreadable file stops the run.
- No escape hatch for unknown keys. No `--strict-config`, no `_comment` keys, no
  forward-compatibility allowance at the top level.
- The auto-detect warning machinery, the `providerAutoDetected` envelope flag, and a
  `provider: "auto"` silencer must never exist.
- Four argv flags are not accepted and must stay that way: `--env-allowlist`, `--env-extra`,
  `--allow-protocol-skew`, `--skills-dir`. The last two are expressed as `allowProtocolSkew` and
  `skills.skills` respectively.
- Modes are not a host_config key. A mode is activated per turn by the `--mode` argv flag; there is
  no `modes` block and no persistent mode setting.
- `workspace` is not a host_config key. It is engine-level identity set by the spawner. See
  `storage-and-workspace.md`.
- Mid-turn config changes are not supported. The engine reads config once at subprocess startup; a
  host that edits the file mid-session sees the change on the next turn's subprocess. Accepted, not
  a gap.
- The config file is non-secret by design. Secrets flow through provider env vars and, for MCP
  servers, through the MCP module's own config path.

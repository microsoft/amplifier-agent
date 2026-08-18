# Configuration

Three things configure a run, in increasing order of precedence: bundle defaults, the host config file, and argv flags. There is no implicit `settings.yaml`; with no config file and no flags, bundle defaults and argv are the whole story.

## Providers

Provider is auto-detected from environment variables in this precedence:

1. `ANTHROPIC_API_KEY`
2. `OPENAI_API_KEY`
3. `AZURE_OPENAI_API_KEY` + `AZURE_OPENAI_ENDPOINT`
4. `OLLAMA_HOST` (defaults to `http://localhost:11434`)
5. `GOOGLE_API_KEY` (`GEMINI_API_KEY` is also accepted by the provider module, `GOOGLE_API_KEY` takes precedence)

`github-copilot`, `openai-chatgpt`, and `chat-completions` are excluded from this auto-detect chain
-- none of them resolves from a single API-key environment variable. `github-copilot` reads its own
token chain from the environment (see below). `openai-chatgpt` has no credential env var at all: it
authenticates via OAuth device-code, caching tokens to `~/.amplifier/openai-chatgpt-oauth.json`.
`chat-completions` needs an endpoint rather than a key (`CHAT_COMPLETIONS_BASE_URL`, plus optional
`CHAT_COMPLETIONS_API_KEY`) and has no implicit default endpoint to fall back to. All three must be
selected explicitly with `provider.module` in a host config file, rather than silently winning a
"first match" race.

Override by passing `--config <path>` at a host config file that names a provider explicitly.

> **Deprecated alias:** `AZURE_OPENAI_KEY` (without `_API_`) is still accepted as a fallback for backwards compatibility and triggers a one-time stderr warning when used. Prefer `AZURE_OPENAI_API_KEY`. The legacy name will be removed in a future release.

Enumerate what a provider actually serves:

```bash
amplifier-agent models list                       # aggregate across all configured providers
amplifier-agent models list --provider anthropic  # one provider only
amplifier-agent models list --latest              # only the newest of each family
amplifier-agent providers list                    # credential-resolution reporting
```

## Model ids

GitHub Copilot resells models from several vendors, so a model id like `claude-sonnet-5` is served both by it and by its original vendor. Copilot's models are therefore namespaced on the wire so the two stay separately addressable, and their display names carry a `(GitHub)` suffix so they are distinguishable in a picker. Native providers keep their bare ids.

```
claude-sonnet-5                   Claude Sonnet 5             (anthropic)
github-copilot/claude-sonnet-5    Claude Sonnet 5 (GitHub)    (github-copilot)
```

The namespace applies only to the HTTP `serve` surface, where a single model list spans every enabled provider. `provider.config.default_model` in a host config already names one provider, so it takes the bare id (`claude-sonnet-5`, not `github-copilot/claude-sonnet-5`).

## Credentials

For "set once, works everywhere" instead of editing shell rc files, the `auth` subcommand persists provider credentials at `~/.amplifier-agent/credentials.json` (mode `0600`):

```bash
amplifier-agent auth set anthropic    sk-ant-...
amplifier-agent auth set openai       sk-...
amplifier-agent auth set azure-openai sk-... --endpoint https://...
amplifier-agent auth set gemini       AIza...
amplifier-agent auth list             # configured providers, api keys masked
amplifier-agent auth status           # diagnose env-vs-file precedence per provider
amplifier-agent auth remove openai    # delete a single entry
amplifier-agent auth clear --force    # delete the whole file
```

Resolution is **env-first**, so existing shell-rc workflows keep working unchanged:

1. Shell environment variable (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, and so on): wins when set
2. `~/.amplifier-agent/credentials.json`: fallback for the set-once path
3. Empty: the caller decides whether the missing credential is an error or a no-op

This matters for hosts that spawn `amplifier-agent` as a subprocess: once you have run `auth set` a single time, every subsequent invocation picks the key up automatically, from any terminal, from any directory, with or without exported environment variables.

> **`github-copilot` is environment-only.** The other providers receive their credential through the mount config, so `auth set` works for them. The Copilot provider reads its token directly from the environment and ignores the config value, so `auth set github-copilot` is refused rather than storing a token the provider can never see. Set one of these instead (first non-empty wins): `COPILOT_AGENT_TOKEN`, `COPILOT_GITHUB_TOKEN`, `GH_TOKEN`, `GITHUB_TOKEN`.

> **`openai-chatgpt` has no static key to store, so `auth set openai-chatgpt` is also refused.** It authenticates via OAuth device-code instead: the provider module drives an interactive login at mount time (`login_on_mount`, default true) and caches tokens to `~/.amplifier/openai-chatgpt-oauth.json`, refreshing them itself. Requires "Sign in with device code" enabled in the account's ChatGPT Security settings.
>
> ```bash
> export GITHUB_TOKEN=$(gh auth token)
> ```
>
> An existing `gh` or VS Code login may already authenticate it through the SDK's cached OAuth, so try it before exporting anything. This is a temporary limitation: the real fix is in the provider module, whose token resolver needs to read the agent-delivered credential from its config before falling back to the environment. `auth set` support returns once that lands.

> **`chat-completions` is environment-only too, but for a different reason.** Its "credential" is the target `base_url`, not an API key, so it is read purely from the environment: `CHAT_COMPLETIONS_BASE_URL` (required — the server to talk to) and `CHAT_COMPLETIONS_API_KEY` (optional; local servers like llama.cpp, vLLM, LM Studio, and LocalAI commonly need none). The persisted credentials file is not consulted for this provider.
>
> ```bash
> export CHAT_COMPLETIONS_BASE_URL=http://localhost:8000/v1
> # export CHAT_COMPLETIONS_API_KEY=...   # only if your server requires one
> ```

The file format is a versioned JSON envelope:

```jsonc
{
  "version": 1,
  "providers": {
    "anthropic":    { "api_key": "sk-ant-..." },
    "openai":       { "api_key": "sk-..." },
    "azure-openai": { "api_key": "...", "endpoint": "https://..." }
  }
}
```

Unknown providers and unknown fields round-trip through reads and writes, so future releases can extend the schema without dropping pre-existing user configuration. The file is plaintext, matching `aws credentials`, `gh hosts.yml`, and `claude/credentials.json`. OS keychain integration is a future concern.

## Approval policy

Tool calls can request approval before they run. The policy is resolved once, at startup:

1. `-y` / `--yes` approves every request
2. `-n` / `--no` declines every request
3. `approval.mode` in the host config file (`"yes"`, `"no"`, or `"prompt"`)
4. stdin is a TTY, so prompt
5. none of the above, so exit 2 with `approval_unconfigured`

```bash
# Interactive: prompts on stderr when a tool wants approval
amplifier-agent run "Create /tmp/test.txt containing 'hello'"

# Auto-approve everything
amplifier-agent run -y "Create /tmp/test.txt"

# Auto-deny everything
amplifier-agent run -n "Create /tmp/test.txt"

# Headless with no policy: exits 2, runs nothing
echo "" | amplifier-agent run "Create /tmp/test.txt"
```

Step 5 is deliberate. A headless run with no declared policy used to auto-deny every tool call and still exit 0, which looked like success while doing no work. Headless callers have to say what they want.

The interactive prompt is one line on stderr:

```
Approve [<kind>] <summary> [y/N]:
```

`<summary>` is the tool name when the payload carries one, otherwise a JSON dump of the payload truncated to 80 characters. `y` or `yes` in any case approves; anything else, including a bare Enter, declines. There is no cancel-the-turn answer.

The shipped bundle does not currently mount `hooks-approval` (see [`ISSUES.md`](../ISSUES.md), ISSUE-001), so few tool calls raise a request in practice. The startup policy gate still applies to every run.

## Host config file

Persistent settings live in a JSON file passed as `--config <path>`, or named by `$AMPLIFIER_AGENT_CONFIG`. Argv flags beat the file; the file beats bundle defaults.

The smallest useful file, enough to make a headless run legal:

```json
{ "approval": { "mode": "yes" } }
```

Pick a provider and a model:

```json
{
  "approval": { "mode": "yes" },
  "provider": {
    "module": "anthropic",
    "config": { "default_model": "claude-sonnet-5" }
  }
}
```

Point `tool-mcp` at a host-managed MCP server file:

```json
{ "mcp": { "configPath": "/var/run/amplifier/mcp.json" } }
```

Add skill sources and tune visibility. Host sources append to the bundle's rather than replacing them:

```json
{
  "skills": {
    "skills": ["/var/run/amplifier/host-managed-skills"],
    "visibility": { "max_skills_visible": 20 }
  }
}
```

A subprocess host typically writes one file per agent instance and passes `--config <path>` on every turn:

```json
{
  "approval": { "mode": "yes" },
  "provider": { "module": "anthropic", "config": { "default_model": "claude-sonnet-5" } },
  "mcp": { "configPath": "/var/run/paperclip/instances/<agent-id>/mcp.json" },
  "skills": { "skills": ["/var/run/paperclip/instances/<agent-id>/skills"] }
}
```

**The top level is closed: an unknown key is an error, not a warning.** Full schema, merge rules, and error codes are in [`spec/host-config.md`](spec/host-config.md).

Inspect what actually resolved, with source annotations per value:

```bash
amplifier-agent config show
```

## Related

- Normative provider contract: [`spec/providers-and-models.md`](spec/providers-and-models.md)
- Normative host config contract: [`spec/host-config.md`](spec/host-config.md)
- Storage locations and workspace scoping: [`spec/storage-and-workspace.md`](spec/storage-and-workspace.md)

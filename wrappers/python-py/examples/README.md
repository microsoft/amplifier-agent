# amplifier-agent-py examples

Three small Python programs demonstrating the wrapper.

| Example | What it shows | Requires API key |
|---|---|---|
| `diagnostic.py` | Binary discovery + version probe (no LLM call) | No |
| `async_chat.py` | Full async surface — streaming events, on_event callback, error handling | Yes |
| `sync_chat.py` | Sync surface — context manager, simple for-loop iteration | Yes |

## Prerequisites

```bash
# 1. Install the engine binary (BYO-engine model — wrapper has no dep on engine)
uv tool install amplifier-agent
# or
pipx install amplifier-agent

# 2. Install the wrapper
uv add amplifier-agent-py
# or
pip install amplifier-agent-py

# 3. Configure provider credentials (engine reads them at startup)
export ANTHROPIC_API_KEY=sk-ant-...
```

> **Important — secrets must be opted in.** The wrapper's `DEFAULT_ALLOWLIST` deliberately excludes
> credential variables. Provider keys (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `AZURE_OPENAI_API_KEY`,
> etc.) only reach the engine subprocess when the host forwards them explicitly via
> `env={"extra": {...}}`. The examples below do this with a small helper that picks up the common
> provider env vars from the caller's environment. If you copy the pattern into your own host,
> forward only the keys your provider actually needs.

## Verify your install (no API key needed)

```bash
python examples/diagnostic.py
```

Expected output:

```
OK   binary discovered: /Users/you/.local/bin/amplifier-agent
OK   protocol version: wrapper=0.3.0 engine=0.3.0
OK   engine version:   0.6.0
OK   spawn_agent() returned a SessionHandle without launching the engine subprocess.
```

If `binary_not_found`: install the engine (`uv tool install amplifier-agent`) or set `AMPLIFIER_AGENT_BIN`.

If `protocol_version_mismatch`: install a matching engine version, or pass `allow_protocol_skew=True` to bypass (unsafe).

## Run a real turn

```bash
python examples/async_chat.py "Explain what amplifier-agent is in two sentences."
```

The async example prints structured events to stderr (`[init]`, `[tool/started]`, `[tool/completed]`) and the final reply to stdout.

```bash
python examples/sync_chat.py "What tools do you have access to?"
```

The sync example is simpler — context manager + for-loop, no asyncio knowledge required.

## What's NOT shown (and why)

- `approval={"on_request": ...}` — rejected in v1; raises `AaaError(approval_not_supported_in_v1)`. The Mode A wire has no mid-turn host channel.
- `provider_override=...` — removed from the wrapper surface; configure providers via `--config <host_config.json>` instead.
- `mcp_servers=...` — supported by the wrapper (spilled to a 0600 tmpfile and injected as `AMPLIFIER_MCP_CONFIG`). Not shown here to keep the examples short.

For the full configuration surface, see the package README.

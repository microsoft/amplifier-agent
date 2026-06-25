# amplifier-agent-py

Python SDK for the [Amplifier agent](https://github.com/microsoft/amplifier-agent). Spawns and drives the `amplifier-agent` CLI over a stdio protocol.

This package mirrors the [TypeScript wrapper](https://github.com/microsoft/amplifier-agent/tree/main/wrappers/typescript) (`amplifier-agent-ts`) one-to-one — same transport model, same configuration surface, same error taxonomy. Symmetry is enforced by the conformance suite under `wrappers/conformance/`.

## Install model: bring your own engine

This wrapper does **not** depend on the engine package. The `amplifier-agent` binary is a runtime dependency that you install separately.

> Neither `amplifier-agent` nor `amplifier-agent-py` is published to PyPI yet. Install both from the git source as shown below. Once releases land on PyPI, these commands will collapse to `uv tool install amplifier-agent` and `pip install amplifier-agent-py`.

Install the engine binary (recommended — uses the official installer, which pins the latest release and primes the bundle cache):

```bash
curl -fsSL https://raw.githubusercontent.com/microsoft/amplifier-agent/main/install.sh | bash
```

Or do it manually with `uv` / `pipx`:

```bash
# Isolated tool install from git source
uv tool install --from git+https://github.com/microsoft/amplifier-agent amplifier-agent

# Or
pipx install git+https://github.com/microsoft/amplifier-agent
```

Then install the wrapper into your host project, also from the git source (the wrapper lives in a subdirectory of the engine repo):

```bash
uv add "amplifier-agent-py @ git+https://github.com/microsoft/amplifier-agent#subdirectory=wrappers/python-py"
# or
pip install "amplifier-agent-py @ git+https://github.com/microsoft/amplifier-agent#subdirectory=wrappers/python-py"
```

The wrapper discovers the binary in this order:

1. `AMPLIFIER_AGENT_BIN` environment variable
2. `shutil.which("amplifier-agent")` (PATH lookup)

This pattern matches `docker-py`, the Kubernetes Python client, and the GitHub CLI extension model: the wrapper is a thin transport client, the engine is installed by whatever mechanism is right for your host.

## Quick start — async (recommended)

```python
import asyncio
from amplifier_agent_py import spawn_agent

async def main():
    handle = await spawn_agent(
        session_id="demo-1",
        display_mode="ndjson",         # stream structured events
        approval={"mode": "yes"},      # auto-approve tool calls
        timeout_ms=10 * 60 * 1000,     # 10 minute wall-clock cap
    )

    try:
        async for event in handle.submit("Summarize the README in this repo."):
            if event.type == "result":
                print(event.text)
                break
            elif event.type == "notification":
                print(f"[{event.method}]", event.params)
            elif event.type == "error":
                print(f"ERROR: {event.code} — {event.message}")
                break
    finally:
        await handle.dispose()

asyncio.run(main())
```

## Quick start — sync

For Django, Flask, scripts, and notebooks:

```python
from amplifier_agent_py import spawn_agent_sync

with spawn_agent_sync(session_id="demo-2") as handle:
    for event in handle.submit("Hello, agent!"):
        if event.type == "result":
            print(event.text)
            break
```

`spawn_agent_sync()` owns a dedicated `asyncio` event loop for the lifetime of the handle. Use it as a context manager so the loop is always closed.

## Protocol version pinning

This wrapper version is pinned to **wire protocol 0.3.0**. On `spawn_agent()`, the wrapper runs `amplifier-agent version --json` and compares the engine's reported protocol version against `PROTOCOL_VERSION_REQUIRED_BY_WRAPPER`. Mismatch raises `AaaError(protocol_version_mismatch)` unless you pass `allow_protocol_skew=True`.

| Wrapper version | Required engine protocol |
|---|---|
| `amplifier-agent-py` 0.3.x | `amplifier-agent` reporting protocol `0.3.0` |

Wrapper version tracks the wire protocol, not the engine version. Multiple engine versions can speak the same protocol.

## Configuration parameters

`spawn_agent()` and `spawn_agent_sync()` accept identical parameters. Each maps to the corresponding TypeScript wrapper field:

| Parameter | Type | Purpose |
|---|---|---|
| `session_id` | `str` | Required. Caller-supplied session identifier. |
| `resume` | `bool` | When True, emit `--resume`; else `--fresh`. |
| `cwd` | `str \| None` | Working directory for the engine subprocess. |
| `env` | `dict` | `{"allowlist": [...], "extra": {...}}`. Defaults to a safe allowlist. |
| `approval` | `dict` | `{"mode": "yes" \| "no" \| "prompt"}`. Mid-turn callbacks not supported in v1. |
| `display` | `dict` | `{"on_event": callable, "subagent_events": ...}`. `on_event` is sync. |
| `display_mode` | `"text" \| "ndjson"` | `"ndjson"` required for structured events. |
| `workspace` | `str \| None` | Workspace slug for state isolation. |
| `mcp_servers` | `dict` | Spilled to a 0600 tmpfile; injected via `AMPLIFIER_MCP_CONFIG`. |
| `timeout_ms` | `int \| None` | Per-submit wall-clock cap. None / 0 disables. |
| `config_path` | `str \| None` | Engine host config file path (`--config`). |
| `allow_protocol_skew` | `bool` | Skip the wrapper-side version probe. |

See the TypeScript wrapper README for the full semantic reference — every field has identical behavior.

## DisplayEvent shape

`submit()` yields a stream of `DisplayEvent` values. Discriminate on `event.type`:

| `type` | Variant | Fields |
|---|---|---|
| `"init"` | `InitEvent` | `session_id` |
| `"activity"` | `ActivityEvent` | (heartbeat; emitted every 2s while alive) |
| `"result"` | `ResultEvent` | `text` |
| `"error"` | `ErrorEvent` | `code`, `classification`, `severity`, `message`, `correlation_id`, `stderr_tail`, `retryable` |
| `"notification"` | `NotificationEvent` | `method`, `params` (wire-protocol notification from engine stderr) |

## Error taxonomy

`AaaError` carries the same fields as the TypeScript wrapper's `AaaError` class:

```python
from amplifier_agent_py import AaaError

try:
    handle = await spawn_agent(session_id="x")
except AaaError as e:
    print(e.code)            # e.g. "binary_not_found"
    print(e.classification)  # "transport" | "protocol" | "engine" | "approval" | "unknown"
    print(e.severity)        # "error" | "warning"
    print(e.remediation)     # human-readable
```

Common codes:

| Code | When |
|---|---|
| `binary_not_found` | `amplifier-agent` not on PATH and `AMPLIFIER_AGENT_BIN` unset |
| `engine_probe_failed` | `amplifier-agent version --json` failed |
| `protocol_version_mismatch` | Engine speaks a different wire protocol than the wrapper |
| `lifecycle_unsupported` | `lifecycle` other than `"one-shot"`, or `submit()` called twice |
| `approval_not_supported_in_v1` | Passed `approval={"on_request": ...}` |
| `env_injection_rejected` | `env.extra` contains a blocked key (e.g. `PYTHONPATH`) |

## Project status

Version 0.3.0 corresponds to wire protocol 0.3.0. The wrapper is a clean port of the TypeScript wrapper at the same protocol revision. Conformance is enforced by the shared fixture suite under `wrappers/conformance/`.

## License

MIT

<!-- GENERATED FILE — DO NOT HAND-EDIT.
     Regenerate with:
       uv run python -m amplifier_agent_lib.protocol._gen \
           --output-dir src/amplifier_agent_lib/protocol
-->

# Amplifier Agent — Wire Spec

**Protocol version:** `0.3.0`

**Framing:** JSON-RPC 2.0 over NDJSON over stdio. 
Stdout carries frames only; stderr is free-form log output.

## Methods

| RPC | Params | Result |
|---|---|---|
| `initialize` | [`InitializeParams`](schemas/InitializeParams.schema.json) | [`InitializeResult`](schemas/InitializeResult.schema.json) |
| `turn/submit` | [`TurnSubmitParams`](schemas/TurnSubmitParams.schema.json) | [`TurnSubmitResult`](schemas/TurnSubmitResult.schema.json) |
| `session/create` | [`SessionCreateParams`](schemas/SessionCreateParams.schema.json) | [`SessionCreateResult`](schemas/SessionCreateResult.schema.json) |
| `session/end` | [`SessionEndParams`](schemas/SessionEndParams.schema.json) | [`SessionEndResult`](schemas/SessionEndResult.schema.json) |
| `agent/shutdown` | [`AgentShutdownParams`](schemas/AgentShutdownParams.schema.json) | [`AgentShutdownResult`](schemas/AgentShutdownResult.schema.json) |
| `cache/info` | [`CacheInfoParams`](schemas/CacheInfoParams.schema.json) | [`CacheInfoResult`](schemas/CacheInfoResult.schema.json) |

## Notifications

Canonical display event taxonomy (engine → client):

- `result/delta`
- `result/final`
- `tool/started`
- `tool/completed`
- `progress`
- `thinking/delta`
- `thinking/final`
- `usage`
- `error`

Notification payload schemas:

| TypedDict | Schema |
|---|---|
| `ApprovalRequestNotification` | [`schemas/ApprovalRequestNotification.schema.json`](schemas/ApprovalRequestNotification.schema.json) |
| `ApprovalTimeoutNotification` | [`schemas/ApprovalTimeoutNotification.schema.json`](schemas/ApprovalTimeoutNotification.schema.json) |
| `ErrorNotification` | [`schemas/ErrorNotification.schema.json`](schemas/ErrorNotification.schema.json) |
| `ProgressNotification` | [`schemas/ProgressNotification.schema.json`](schemas/ProgressNotification.schema.json) |
| `ResultDeltaNotification` | [`schemas/ResultDeltaNotification.schema.json`](schemas/ResultDeltaNotification.schema.json) |
| `ResultFinalNotification` | [`schemas/ResultFinalNotification.schema.json`](schemas/ResultFinalNotification.schema.json) |
| `ThinkingDeltaNotification` | [`schemas/ThinkingDeltaNotification.schema.json`](schemas/ThinkingDeltaNotification.schema.json) |
| `ThinkingFinalNotification` | [`schemas/ThinkingFinalNotification.schema.json`](schemas/ThinkingFinalNotification.schema.json) |
| `ToolCompletedNotification` | [`schemas/ToolCompletedNotification.schema.json`](schemas/ToolCompletedNotification.schema.json) |
| `ToolStartedNotification` | [`schemas/ToolStartedNotification.schema.json`](schemas/ToolStartedNotification.schema.json) |
| `UsageNotification` | [`schemas/UsageNotification.schema.json`](schemas/UsageNotification.schema.json) |

## Errors

See [`schemas/error_codes.schema.json`](schemas/error_codes.schema.json) for the authoritative enum.

| Code | Wire value |
|---|---|
| `AGENT_NOT_READY` | `agent_not_ready` |
| `APPROVAL_DENIED` | `approval_denied` |
| `APPROVAL_PROTOCOL_VIOLATION` | `approval_protocol_violation` |
| `APPROVAL_TIMEOUT` | `approval_timeout` |
| `APPROVAL_TRANSLATION_FAILED` | `approval_translation_failed` |
| `BUNDLE_LOAD_FAILED` | `bundle_load_failed` |
| `CONFIG_VALIDATION` | `config_validation` |
| `ENV_INJECTION_REJECTED` | `env_injection_rejected` |
| `INTERNAL` | `internal` |
| `INVALID_SESSION` | `invalid_session` |
| `PROMPT_REQUIRED` | `prompt_required` |
| `PROTOCOL_VERSION_MISMATCH` | `protocol_version_mismatch` |
| `PROVIDER_INIT_FAILED` | `provider_init_failed` |
| `PROVIDER_NOT_CONFIGURED` | `provider_not_configured` |
| `RUNTIME` | `runtime` |
| `SESSION_NOT_FOUND` | `session_not_found` |
| `SPAWN_FAILED` | `spawn_failed` |
| `STALE_SESSION` | `stale_session` |
| `TOOL_EXECUTION_FAILED` | `tool_execution_failed` |
| `WIRE_PROTOCOL_VIOLATION` | `wire_protocol_violation` |

## Capabilities

| TypedDict | Schema |
|---|---|
| `ApprovalCapability` | [`schemas/ApprovalCapability.schema.json`](schemas/ApprovalCapability.schema.json) |
| `ClientCapabilities` | [`schemas/ClientCapabilities.schema.json`](schemas/ClientCapabilities.schema.json) |
| `DisplayCapability` | [`schemas/DisplayCapability.schema.json`](schemas/DisplayCapability.schema.json) |
| `ServerCapabilities` | [`schemas/ServerCapabilities.schema.json`](schemas/ServerCapabilities.schema.json) |


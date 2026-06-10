// GENERATED FILE — DO NOT HAND-EDIT.
// Regenerate with: cd wrappers/typescript && pnpm run gen:types
// Source: src/amplifier_agent_lib/protocol/schemas/*.schema.json


/**
 * Parameters for the ``agent/shutdown`` JSON-RPC method (none required).
 */
export interface AgentShutdownParams {}

/**
 * Result returned by the ``agent/shutdown`` JSON-RPC method (none required).
 */
export interface AgentShutdownResult {}

/**
 * Approval-flow capabilities: the approval action strings a party supports.
 */
export interface ApprovalCapability {
  actions: string[];
}

/**
 * Requests human (or automated) approval before proceeding.
 */
export interface ApprovalRequestNotification {
  sessionId: string;
  turnId: string;
  approvalId: string;
  kind: string;
  payload: unknown;
  timeoutMs: number;
}

/**
 * Emitted when an approval request exceeds its timeout.
 */
export interface ApprovalTimeoutNotification {
  sessionId: string;
  turnId: string;
  approvalId: string;
  kind: string;
}

/**
 * Parameters for the ``cache/info`` JSON-RPC method (none required).
 */
export interface CacheInfoParams {}

/**
 * Result returned by the ``cache/info`` JSON-RPC method.
 */
export interface CacheInfoResult {
  cachePath: string;
  preparedBundles: string[];
}

/**
 * Capabilities advertised by the connecting client.
 */
export interface ClientCapabilities {
  approval?: ApprovalCapability;
  display?: DisplayCapability;
  experimental?: {
    [k: string]: unknown;
  };
}
/**
 * Approval-flow capabilities: the approval action strings a party supports.
 */
/**
 * Display capabilities: the notification event types a party can handle.
 */
export interface DisplayCapability {
  events: string[];
}

/**
 * Identity of the connecting client.
 */
export interface ClientInfo {
  name: string;
  version: string;
}

/**
 * Display capabilities: the notification event types a party can handle.
 */

/**
 * Error event.  ``turnId`` is optional for session-level errors.
 */
export interface ErrorNotification {
  sessionId: string;
  turnId?: string;
  code: string;
  message: string;
  recoverable: boolean;
}

/**
 * Parameters for the ``initialize`` JSON-RPC method.
 */
export interface InitializeParams {
  protocolVersion: string;
  clientInfo: ClientInfo;
  capabilities: {
    [k: string]: unknown;
  };
  sessionId?: string;
  resume?: boolean;
  providerOverride?: string;
  cwd?: string;
  mcpConfigPath?: string;
}
/**
 * Identity of the connecting client.
 */

/**
 * Result returned by the ``initialize`` JSON-RPC method.
 */
export interface InitializeResult {
  capabilities: {
    [k: string]: unknown;
  };
  serverInfo: ServerInfo;
  sessionState: SessionState;
}
/**
 * Identity of the agent server.
 */
export interface ServerInfo {
  name: string;
  version: string;
}
/**
 * Returned session state after initialize or session/create.
 */
export interface SessionState {
  sessionId: string;
  resumed: boolean;
}

/**
 * Per-server MCP configuration passed via ``initialize.params.mcpServers``.
 */
export interface McpServerConfig {
  transport: string;
  command?: string;
  args?: string[];
  env?: {
    [k: string]: string;
  };
  url?: string;
  headers?: {
    [k: string]: string;
  };
}

/**
 * Arbitrary progress update.  ``percent`` is optional (0-100).
 */
export interface ProgressNotification {
  sessionId: string;
  turnId: string;
  message: string;
  percent?: number;
}

/**
 * Incremental text chunk from an in-progress turn.
 */
export interface ResultDeltaNotification {
  sessionId: string;
  turnId: string;
  text: string;
}

/**
 * Final text for a completed turn.  ``usage`` may be omitted (e.g. L14 synthesis).
 */
export interface ResultFinalNotification {
  sessionId: string;
  turnId: string;
  text: string;
  usage?: unknown;
}

/**
 * Capabilities advertised by the agent server.
 */
export interface ServerCapabilities {
  approval?: ApprovalCapability;
  display?: DisplayCapability;
  experimental?: {
    [k: string]: unknown;
  };
}
/**
 * Approval-flow capabilities: the approval action strings a party supports.
 */
/**
 * Display capabilities: the notification event types a party can handle.
 */

/**
 * Identity of the agent server.
 */

/**
 * Parameters for the ``session/create`` JSON-RPC method.
 */
export interface SessionCreateParams {
  sessionId: string;
  resume?: boolean;
}

/**
 * Result returned by the ``session/create`` JSON-RPC method.
 */
export interface SessionCreateResult {
  sessionState: SessionState;
}
/**
 * Returned session state after initialize or session/create.
 */

/**
 * Parameters for the ``session/end`` JSON-RPC method.
 */
export interface SessionEndParams {
  sessionId: string;
}

/**
 * Result returned by the ``session/end`` JSON-RPC method.
 */
export interface SessionEndResult {
  ended: boolean;
}

/**
 * Returned session state after initialize or session/create.
 */

/**
 * Incremental thinking/reasoning chunk (extended thinking models).
 */
export interface ThinkingDeltaNotification {
  sessionId: string;
  turnId: string;
  text: string;
}

/**
 * Final thinking/reasoning content for a turn.
 */
export interface ThinkingFinalNotification {
  sessionId: string;
  turnId: string;
  text: string;
}

/**
 * Emitted when a tool call finishes execution.
 */
export interface ToolCompletedNotification {
  sessionId: string;
  turnId: string;
  toolCallId: string;
  name: string;
  result: unknown;
  durationMs: number;
  agentName?: string;
}

/**
 * Emitted when a tool call begins execution.
 */
export interface ToolStartedNotification {
  sessionId: string;
  turnId: string;
  toolCallId: string;
  name: string;
  args: unknown;
  agentName?: string;
}

/**
 * Parameters for the ``turn/submit`` JSON-RPC method.
 */
export interface TurnSubmitParams {
  sessionId: string;
  turnId: string;
  prompt: string;
  attachments?: {
    [k: string]: unknown;
  }[];
}

/**
 * Result returned by the ``turn/submit`` JSON-RPC method.
 */
export interface TurnSubmitResult {
  reply: string | null;
  turnId: string;
  sessionId: string;
  finalEvent?: {
    [k: string]: unknown;
  };
}

/**
 * Token usage and optional cost summary for a turn.
 */
export interface UsageNotification {
  sessionId: string;
  turnId: string;
  inputTokens: number;
  outputTokens: number;
  cost?: string;
  llmDurationMs?: number;
  model?: string;
  provider?: string;
  cacheReadTokens?: number;
  cacheWriteTokens?: number;
  sessionCostTotal?: string;
  agentName?: string;
}

export type ErrorCode =
  | "agent_not_ready"
  | "approval_denied"
  | "approval_protocol_violation"
  | "approval_timeout"
  | "approval_translation_failed"
  | "bundle_load_failed"
  | "config_validation"
  | "env_injection_rejected"
  | "internal"
  | "invalid_session"
  | "prompt_required"
  | "protocol_version_mismatch"
  | "provider_init_failed"
  | "provider_not_configured"
  | "runtime"
  | "session_not_found"
  | "spawn_failed"
  | "stale_session"
  | "tool_execution_failed"
  | "wire_protocol_violation";

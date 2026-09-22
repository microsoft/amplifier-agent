import type { AgentError } from "./errors.js";

export interface TextPart { type: "text"; text: string }
export type ContentPart = TextPart;
export interface ConversationMessage {
  role: "system" | "developer" | "user" | "assistant";
  content: ContentPart[];
}
export interface TurnInput { content: ContentPart[]; model?: string; history?: ConversationMessage[] }
export interface TurnResult {
  state: "success" | "failure" | "rejected" | "cancelled";
  content?: ContentPart[];
  error?: AgentError;
  usage?: Usage;
}
export interface SessionRecord { session_id: string; persistence: "durable" | "ephemeral" }
export interface TurnInfo { session_id: string; turn_id: string }
export interface TurnRecord { turn_id: string; input: TurnInput; result: TurnResult }
export interface UsageEntry {
  provider: string;
  model: string;
  tokens_in?: bigint;
  tokens_out?: bigint;
  cache_read_tokens?: bigint;
  cache_write_tokens?: bigint;
  cost?: Record<string, string>;
}
export interface Usage { entries: UsageEntry[] }

export interface ToolContext { readonly call_id: string; readonly deadline?: string }
export type ToolHandler = (args: Record<string, unknown>, context: ToolContext) => Promise<string>;
export interface Tool {
  name: string;
  description: string;
  inputSchema: Record<string, unknown>;
  handler: ToolHandler;
  safety?: Record<string, unknown>;
}
export interface ToolCall {
  call_id: string;
  name: string;
  source: "built-in" | "caller" | "mcp";
  arguments: Record<string, unknown>;
  deadline?: string;
}
export interface ToolResolution {
  call_id: string;
  outcome: "completed" | "failed" | "cancelled" | "unknown";
  content?: string;
  error?: AgentError;
  truncated?: boolean;
  original_bytes?: number;
}
export type McpServer =
  | { name: string; transport: "stdio"; command: string; args?: string[]; env?: Record<string, string> }
  | { name: string; transport: "http"; url: string; headers?: Record<string, string> };
export interface ApprovalRequest {
  readonly request_id: string;
  readonly summary: string;
  readonly call_id?: string;
  readonly name?: string;
}
export interface ApprovalResponse { decision: "allow" | "deny" | "cancel"; reason?: string }
export type ApprovalHandler = (request: ApprovalRequest) => Promise<ApprovalResponse>;
export interface ApprovalResolution {
  request_id: string;
  decision: "allow" | "deny" | "cancel" | "timeout" | "unavailable" | "invalid";
  reason?: string;
}
export interface AgentOptions {
  provider?: string;
  model?: string;
  instructions?: string;
  tools?: Tool[];
  skills?: string[];
  mcpServers?: McpServer[];
  storage?: string;
  approvals?: ApprovalHandler | "allow" | "deny";
  toolErrorPolicy?: "stop" | "continue";
  toolResultMaxBytes?: number | null;
}
export interface SessionOptions { sessionId?: string; persistence?: "durable" | "ephemeral"; model?: string }

interface EventEnvelope<T extends string, P> {
  contract_version: "turn-events/1";
  session_id: string;
  turn_id: string;
  sequence: bigint;
  type: T;
  payload: P;
  at?: string;
}
export interface TurnStarted { continuation: "fresh" | "resumed"; primary_actual: { provider: string; model: string } }
export interface OutputDelta { content: ContentPart[] }
export interface ReasoningDelta { text: string }
export interface ReasoningFinal { text: string }
export interface ToolCallEvent { call: ToolCall }
export interface ToolResultEvent { resolution: ToolResolution }
export interface ApprovalRequestEvent { request: ApprovalRequest }
export interface ApprovalDecision { resolution: ApprovalResolution }
export interface Progress { data: unknown }
export interface UsageEvent { snapshot: Usage }
export type Event =
  | EventEnvelope<"turn_started", TurnStarted>
  | EventEnvelope<"output_delta", OutputDelta>
  | EventEnvelope<"reasoning_delta", ReasoningDelta>
  | EventEnvelope<"reasoning_final", ReasoningFinal>
  | EventEnvelope<"tool_call", ToolCallEvent>
  | EventEnvelope<"tool_result", ToolResultEvent>
  | EventEnvelope<"approval_request", ApprovalRequestEvent>
  | EventEnvelope<"approval_decision", ApprovalDecision>
  | EventEnvelope<"progress", Progress>
  | EventEnvelope<"usage", UsageEvent>
  | EventEnvelope<"terminal", TurnResult>
  | EventEnvelope<`${string}.${string}`, unknown>;

export interface Agent extends AsyncDisposable {
  createSession(options?: SessionOptions): Promise<Session>;
  resumeSession(sessionId: string): Promise<Session>;
  listSessions(): Promise<SessionRecord[]>;
  deleteSession(sessionId: string): Promise<void>;
  close(): Promise<void>;
}
export interface Session extends AsyncDisposable {
  readonly info: SessionRecord;
  readonly history: TurnRecord[];
  run(input: TurnInput): Promise<TurnResult>;
  startTurn(input: TurnInput): Promise<Turn>;
  fork(): Promise<Session>;
  close(): Promise<void>;
}
export interface Turn {
  readonly info: TurnInfo;
  events(): AsyncIterable<Event>;
  cancel(): Promise<void>;
}

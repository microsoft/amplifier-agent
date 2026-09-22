import { AgentError, BUILTIN_TOOLS, createAgent, contractVersion, contractVersions } from "@microsoft/amplifier-agent";
import type {
  Agent, AgentOptions, ApprovalHandler, ApprovalRequest, ApprovalResolution, ApprovalResponse,
  ContentPart, ConversationMessage, Event, McpServer, Session, SessionOptions, SessionRecord, Tool,
  ToolCall, ToolContext, ToolHandler, ToolResolution, Turn, TurnInfo, TurnInput, TurnRecord,
  TurnResult, Usage, UsageEntry,
} from "@microsoft/amplifier-agent";

type Equal<A, B> = (<T>() => T extends A ? 1 : 2) extends (<T>() => T extends B ? 1 : 2) ? true : false;
type Assert<T extends true> = T;
type RegisteredEvent = Exclude<Event["type"], `${string}.${string}`>;

export type ContractShapes = [
  Assert<Equal<Parameters<typeof createAgent>, [options: AgentOptions]>>,
  Assert<Equal<ReturnType<typeof createAgent>, Promise<Agent>>>,
  Assert<Equal<keyof Agent, "createSession" | "resumeSession" | "listSessions" | "deleteSession" | "close" | typeof Symbol.asyncDispose>>,
  Assert<Equal<Parameters<Agent["createSession"]>, [options?: SessionOptions | undefined]>>,
  Assert<Equal<ReturnType<Agent["createSession"]>, Promise<Session>>>,
  Assert<Equal<Parameters<Agent["resumeSession"]>, [sessionId: string]>>,
  Assert<Equal<ReturnType<Agent["resumeSession"]>, Promise<Session>>>,
  Assert<Equal<Parameters<Agent["listSessions"]>, []>>,
  Assert<Equal<ReturnType<Agent["listSessions"]>, Promise<SessionRecord[]>>>,
  Assert<Equal<Parameters<Agent["deleteSession"]>, [sessionId: string]>>,
  Assert<Equal<ReturnType<Agent["deleteSession"]>, Promise<void>>>,
  Assert<Equal<Parameters<Agent["close"]>, []>>,
  Assert<Equal<ReturnType<Agent["close"]>, Promise<void>>>,
  Assert<Equal<keyof Session, "info" | "history" | "run" | "startTurn" | "fork" | "close" | typeof Symbol.asyncDispose>>,
  Assert<Equal<Session["info"], SessionRecord>>,
  Assert<Equal<Session["history"], TurnRecord[]>>,
  Assert<Equal<Parameters<Session["run"]>, [input: TurnInput]>>,
  Assert<Equal<ReturnType<Session["run"]>, Promise<TurnResult>>>,
  Assert<Equal<Parameters<Session["startTurn"]>, [input: TurnInput]>>,
  Assert<Equal<ReturnType<Session["startTurn"]>, Promise<Turn>>>,
  Assert<Equal<Parameters<Session["fork"]>, []>>,
  Assert<Equal<ReturnType<Session["fork"]>, Promise<Session>>>,
  Assert<Equal<Parameters<Session["close"]>, []>>,
  Assert<Equal<ReturnType<Session["close"]>, Promise<void>>>,
  Assert<Equal<keyof Turn, "info" | "events" | "cancel">>,
  Assert<Equal<Turn["info"], TurnInfo>>,
  Assert<Equal<Parameters<Turn["events"]>, []>>,
  Assert<Equal<ReturnType<Turn["events"]>, AsyncIterable<Event>>>,
  Assert<Equal<Parameters<Turn["cancel"]>, []>>,
  Assert<Equal<ReturnType<Turn["cancel"]>, Promise<void>>>,
  Assert<Equal<keyof AgentOptions, "provider" | "model" | "instructions" | "tools" | "skills" | "mcpServers" | "storage" | "approvals" | "toolErrorPolicy" | "toolResultMaxBytes">>,
  Assert<Equal<keyof SessionOptions, "sessionId" | "persistence" | "model">>,
  Assert<Equal<AgentOptions, {
    provider?: string; model?: string; instructions?: string; tools?: (Tool | string)[]; skills?: string[];
    mcpServers?: McpServer[]; storage?: string; approvals?: ApprovalHandler | "allow" | "deny";
    toolErrorPolicy?: "stop" | "continue"; toolResultMaxBytes?: number | null;
  }>>,
  Assert<Equal<SessionOptions, { sessionId?: string; persistence?: "durable" | "ephemeral"; model?: string }>>,
  Assert<Equal<McpServer,
    { name: string; transport: "stdio"; command: string; args?: string[]; env?: Record<string, string> }
    | { name: string; transport: "http"; url: string; headers?: Record<string, string> }
  >>,
  Assert<Equal<keyof TurnInput, "content" | "model" | "history">>,
  Assert<Equal<keyof TurnResult, "state" | "content" | "error" | "usage">>,
  Assert<Equal<TurnResult["state"], "success" | "failure" | "cancelled" | "rejected">>,
  Assert<Equal<keyof SessionRecord, "session_id" | "persistence">>,
  Assert<Equal<keyof TurnRecord, "turn_id" | "input" | "result">>,
  Assert<Equal<TurnInfo, { session_id: string; turn_id: string }>>,
  Assert<Equal<TurnInput, { content: ContentPart[]; model?: string; history?: ConversationMessage[] }>>,
  Assert<Equal<TurnResult, { state: "success" | "failure" | "rejected" | "cancelled"; content?: ContentPart[]; error?: AgentError; usage?: Usage }>>,
  Assert<Equal<SessionRecord, { session_id: string; persistence: "durable" | "ephemeral" }>>,
  Assert<Equal<TurnRecord, { turn_id: string; input: TurnInput; result: TurnResult }>>,
  Assert<Equal<ConversationMessage, { role: "system" | "developer" | "user" | "assistant"; content: ContentPart[] }>>,
  Assert<Equal<ConversationMessage["role"], "system" | "developer" | "user" | "assistant">>,
  Assert<Equal<ContentPart, { type: "text"; text: string }>>,
  Assert<Equal<Parameters<ToolHandler>, [args: Record<string, unknown>, context: ToolContext]>>,
  Assert<Equal<ReturnType<ToolHandler>, Promise<string>>>,
  Assert<Equal<keyof Tool, "name" | "description" | "inputSchema" | "handler" | "safety">>,
  Assert<Equal<ToolCall["source"], "built-in" | "caller" | "mcp">>,
  Assert<Equal<ToolResolution["outcome"], "completed" | "failed" | "cancelled" | "unknown">>,
  Assert<Equal<ToolCall, { call_id: string; name: string; source: "built-in" | "caller" | "mcp"; arguments: Record<string, unknown>; deadline?: string }>>,
  Assert<Equal<ToolResolution, { call_id: string; outcome: "completed" | "failed" | "cancelled" | "unknown"; content?: string; error?: AgentError; truncated?: boolean; original_bytes?: number }>>,
  Assert<Equal<ToolContext, { readonly call_id: string; readonly deadline?: string }>>,
  Assert<Equal<ApprovalRequest, { readonly request_id: string; readonly summary: string; readonly call_id?: string; readonly name?: string }>>,
  Assert<Equal<ApprovalResponse, { decision: "allow" | "deny" | "cancel"; reason?: string }>>,
  Assert<Equal<ApprovalResolution, { request_id: string; decision: "allow" | "deny" | "cancel" | "timeout" | "unavailable" | "invalid"; reason?: string }>>,
  Assert<Equal<Parameters<ApprovalHandler>, [request: ApprovalRequest]>>,
  Assert<Equal<ReturnType<ApprovalHandler>, Promise<ApprovalResponse>>>,
  Assert<Equal<ApprovalResponse["decision"], "allow" | "deny" | "cancel">>,
  Assert<Equal<ApprovalResolution["decision"], "allow" | "deny" | "cancel" | "timeout" | "unavailable" | "invalid">>,
  Assert<Equal<Usage["entries"], UsageEntry[]>>,
  Assert<Equal<UsageEntry["tokens_in"], bigint | undefined>>,
  Assert<Equal<UsageEntry["tokens_out"], bigint | undefined>>,
  Assert<Equal<UsageEntry["cache_read_tokens"], bigint | undefined>>,
  Assert<Equal<UsageEntry["cache_write_tokens"], bigint | undefined>>,
  Assert<Equal<UsageEntry["cost"], Record<string, string> | undefined>>,
  Assert<Equal<Event["sequence"], bigint>>,
  Assert<Equal<Event["contract_version"], "turn-events/1">>,
  Assert<Equal<keyof Event, "contract_version" | "session_id" | "turn_id" | "sequence" | "type" | "payload" | "at">>,
  Assert<Equal<Event["at"], string | undefined>>,
  Assert<Equal<Extract<Event, { type: "turn_started" }>["payload"], { continuation: "fresh" | "resumed"; primary_actual: { provider: string; model: string } }>>,
  Assert<Equal<Extract<Event, { type: "output_delta" }>["payload"], { content: ContentPart[] }>>,
  Assert<Equal<Extract<Event, { type: "reasoning_delta" }>["payload"], { text: string }>>,
  Assert<Equal<Extract<Event, { type: "reasoning_final" }>["payload"], { text: string }>>,
  Assert<Equal<Extract<Event, { type: "tool_call" }>["payload"], { call: ToolCall }>>,
  Assert<Equal<Extract<Event, { type: "tool_result" }>["payload"], { resolution: ToolResolution }>>,
  Assert<Equal<Extract<Event, { type: "approval_request" }>["payload"], { request: ApprovalRequest }>>,
  Assert<Equal<Extract<Event, { type: "approval_decision" }>["payload"], { resolution: ApprovalResolution }>>,
  Assert<Equal<Extract<Event, { type: "progress" }>["payload"], { data: unknown }>>,
  Assert<Equal<Extract<Event, { type: "usage" }>["payload"], { snapshot: Usage }>>,
  Assert<Equal<Extract<Event, { type: "terminal" }>["payload"], TurnResult>>,
  Assert<Equal<RegisteredEvent, "turn_started" | "output_delta" | "reasoning_delta" | "reasoning_final" | "tool_call" | "tool_result" | "approval_request" | "approval_decision" | "progress" | "usage" | "terminal">>,
  Assert<Equal<typeof contractVersion, "agent-interface/1">>,
  Assert<Equal<typeof contractVersions, readonly string[]>>,
  Assert<Equal<typeof BUILTIN_TOOLS, readonly string[]>>,
  Assert<Equal<ConstructorParameters<typeof AgentError>, [record: {
    code: string; category: string; message: string; remedy: string; retryable: boolean;
    correlation_id?: string; details?: unknown;
  }]>>,
];

function rejectsBrokenSurface(agent: Agent, session: Session, turn: Turn, context: ToolContext): void {
  // @ts-expect-error Turns require the contracted input record.
  session.run("hello");
  // @ts-expect-error Prompt-string overloads are not part of the binding.
  session.startTurn("hello");
  // @ts-expect-error Cancellation has no binding-specific reason argument.
  turn.cancel("stop");
  // @ts-expect-error Sessions expose no direct engine access.
  session.engine;
  // @ts-expect-error Agent configuration exposes no transport options.
  createAgent({ enginePort: 4000 });
  // @ts-expect-error Session creation exposes no resume-or-create option.
  agent.createSession({ resume: true });
  // @ts-expect-error Session identity is a read-only property.
  session.info = { session_id: "changed-id", persistence: "ephemeral" };
  // @ts-expect-error Turn identity is a read-only property.
  turn.info = { session_id: "changed-id", turn_id: "changed-turn" };
  // @ts-expect-error The call correlation identifier is read-only.
  context.call_id = "changed-call";
  // @ts-expect-error Tool messages cannot seed a conversation.
  const toolMessage: ConversationMessage = { role: "tool", content: [] };
  // @ts-expect-error Media content is outside the v1 content vocabulary.
  const image: ContentPart = { type: "image", text: "photo" };
  // @ts-expect-error An error without a remedy is not a complete error record.
  new AgentError({ code: "invalid_input", category: "input", message: "Invalid", retryable: false });
  // @ts-expect-error Usage counters cannot round through number.
  const usage: UsageEntry = { provider: "anthropic", model: "claude-sonnet-5", tokens_in: 1 };
  // @ts-expect-error Cost cannot round through binary floating point.
  const cost: UsageEntry = { provider: "anthropic", model: "claude-sonnet-5", cost: { USD: 0.1 } };
  void [toolMessage, image, usage, cost];
}

void rejectsBrokenSurface;

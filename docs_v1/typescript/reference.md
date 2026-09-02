# TypeScript reference

Signatures. What each one means lives in [concepts](../concepts/).

## Module

```ts
export const contractVersion: string;   // "agent-interface/1"
export const version: string;           // the package version

export function createAgent(options: AgentOptions): Promise<Agent>;
```

## Agent

```ts
interface Agent extends AsyncDisposable {
  createSession(options?: SessionOptions): Promise<Session>;
  resumeSession(sessionId: string): Promise<Session>;
  listSessions(): Promise<SessionRecord[]>;
  deleteSession(sessionId: string): Promise<void>;
  close(): Promise<void>;
}
```

[agents](../concepts/agents.md)

## Session

```ts
interface Session extends AsyncDisposable {
  readonly info: SessionRecord;
  readonly history: TurnRecord[];

  run(input: TurnInput): Promise<TurnResult>;
  startTurn(input: TurnInput): Promise<Turn>;
  fork(): Promise<Session>;
  close(): Promise<void>;
}
```

[sessions](../concepts/sessions.md)

## Turn

```ts
interface Turn {
  readonly info: TurnInfo;

  events(): AsyncIterable<Event>;
  cancel(): Promise<void>;
}
```

[turns](../concepts/turns.md)

## Options

```ts
interface AgentOptions {
  provider: string;
  model: string;
  instructions?: string;
  tools?: Tool[];
  skills?: string[];
  mcpServers?: McpServer[];
  storage?: string;
  approvals?: ApprovalHandler | "allow" | "deny";
}

interface SessionOptions {
  sessionId?: string;
  persistence?: "durable" | "ephemeral";
  model?: string;
}
```

[agents](../concepts/agents.md), [models](../concepts/models.md)

## Records

```ts
interface TextPart {
  type: "text";
  text: string;
}

type ContentPart = TextPart;

interface TurnInput {
  content: ContentPart[];
  model?: string;
}

interface TurnResult {
  state: "success" | "failure" | "rejected" | "cancelled";
  content?: ContentPart[];
  error?: AgentError;
  usage?: Usage;
}

interface SessionRecord {
  session_id: string;
  persistence: "durable" | "ephemeral";
}

interface TurnInfo {
  session_id: string;
  turn_id: string;
}

interface TurnRecord {
  turn_id: string;
  input: TurnInput;
  result: TurnResult;
}
```

[turns](../concepts/turns.md)

## Events

```ts
interface Event {
  contract_version: string;   // "turn-events/1"
  session_id: string;
  turn_id: string;
  sequence: number;
  type: string;
  payload: unknown;
  at?: string;
}
```

`payload` by `type`:

```
turn_started        TurnStarted        continuation, primary_actual
output_delta        OutputDelta        content
reasoning_delta     ReasoningDelta     text
reasoning_final     ReasoningFinal     text
tool_call           ToolCallEvent      call
tool_result         ToolResultEvent    resolution
approval_request    ApprovalRequest    request
approval_decision   ApprovalDecision   resolution
progress            Progress           data
usage               UsageEvent         snapshot
terminal            TurnResult         state, content, error, usage
```

Owned extension types arrive as `Event` with the extension name in `type` and the raw
payload preserved.

[events](../concepts/events.md)

## Tools

```ts
type ToolHandler = (args: Record<string, unknown>) => Promise<string>;

interface Tool {
  name: string;
  description: string;
  inputSchema: Record<string, unknown>;
  handler: ToolHandler;
  safety?: Record<string, unknown>;
}

interface ToolCall {
  call_id: string;
  name: string;
  source: "built-in" | "caller" | "mcp";
  arguments: Record<string, unknown>;
  deadline?: string;
}

interface ToolResolution {
  call_id: string;
  outcome: "completed" | "failed" | "cancelled" | "unknown";
  content?: string;
  error?: AgentError;
}

class ToolFailed extends Error {}
class ToolOutcomeUnknown extends Error {}
```

[tools](../concepts/tools.md)

## MCP servers

```ts
type McpServer =
  | { name: string; transport: "stdio"; command: string; args?: string[]; env?: Record<string, string> }
  | { name: string; transport: "http"; url: string; headers?: Record<string, string> };
```

## Approvals

```ts
type ApprovalHandler = (request: ApprovalRequest) => Promise<ApprovalResponse>;

interface ApprovalRequest {
  request_id: string;
  summary: string;
  call_id?: string;
  name?: string;
}

interface ApprovalResponse {
  decision: "allow" | "deny" | "cancel";
  reason?: string;
}
```

[approvals](../concepts/approvals.md)

## Usage

```ts
interface UsageEntry {
  provider: string;
  model: string;
  tokens_in?: number;
  tokens_out?: number;
  cache_read_tokens?: number;
  cache_write_tokens?: number;
  cost?: Record<string, string>;
}

interface Usage {
  entries: UsageEntry[];
}
```

[usage](../concepts/usage.md)

## Errors

```ts
class AgentError extends Error {
  code: string;
  category: string;
  remedy: string;
  retryable: boolean;
  correlation_id?: string;
  details?: unknown;
}
```

[errors](../concepts/errors.md)

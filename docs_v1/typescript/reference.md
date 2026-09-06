# TypeScript reference

Signatures. What each one means lives in [concepts](../concepts/).

## Module

```ts
export const contractVersion: string;   // "agent-interface/1"
export const contractVersions: readonly string[];
export const version: string;           // the package version

export function createAgent(options: AgentOptions): Promise<Agent>;
```

`contractVersions` is frozen and contains `agent-interface/1`, `turn-events/1`,
`language-binding/1`, and `host-config/1`. Importing the package starts no work.

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

`info` and `history` are read-only observations. `history` is a snapshot of completed
turns; read it again after a turn reaches `terminal` to get the updated conversation.
Closing the session makes both properties unavailable with `closed`.

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
  provider?: string;
  model?: string;
  instructions?: string;
  tools?: Tool[];
  skills?: string[];
  mcpServers?: McpServer[];
  storage?: string;
  approvals?: ApprovalHandler | "allow" | "deny";
  toolErrorPolicy?: "stop" | "continue";
}

interface SessionOptions {
  sessionId?: string;
  persistence?: "durable" | "ephemeral";
  model?: string;
}
```

[agents](../concepts/agents.md), [models](../concepts/models.md)

Omitted provider and model values resolve through [configuration](../configuration.md).
Sessions default to `persistence: "durable"`. Options are snapshotted at construction;
changing the original options does not reconfigure an existing agent.

## Records

```ts
interface TextPart {
  type: "text";
  text: string;
}

type ContentPart = TextPart;

interface ConversationMessage {
  role: "system" | "developer" | "user" | "assistant";
  content: ContentPart[];
}

interface TurnInput {
  content: ContentPart[];
  model?: string;
  history?: ConversationMessage[];
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
  sequence: bigint;
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
approval_request    ApprovalRequestEvent request
approval_decision   ApprovalDecision   resolution
progress            Progress           data
usage               UsageEvent         snapshot
terminal            TurnResult         state, content, error, usage
```

Owned extension types arrive as `Event` with the extension name in `type` and the raw
payload preserved.

The exported `Event` type is a discriminated union. Checking a registered `type`
narrows `payload` to its corresponding record.

`ApprovalResolution.decision` is `allow`, `deny`, `cancel`, `timeout`, `unavailable`,
or `invalid`. A caller's `ApprovalResponse` chooses only `allow`, `deny`, or `cancel`.

[events](../concepts/events.md)

## Tools

```ts
interface ToolContext {
  readonly call_id: string;
  readonly deadline?: string;
}

type ToolHandler = (
  args: Record<string, unknown>,
  context: ToolContext,
) => Promise<string>;

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

[MCP servers](../concepts/tools.md#mcp-servers)

## Approvals

```ts
type ApprovalHandler = (request: ApprovalRequest) => Promise<ApprovalResponse>;

interface ApprovalRequest {
  readonly request_id: string;
  readonly summary: string;
  readonly call_id?: string;
  readonly name?: string;
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
  tokens_in?: bigint;
  tokens_out?: bigint;
  cache_read_tokens?: bigint;
  cache_write_tokens?: bigint;
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

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
  provider?: string | undefined;
  model?: string | undefined;
  instructions?: string | undefined;
  tools?: (Tool | string)[] | undefined;
  skills?: string[] | undefined;
  mcpServers?: McpServer[] | undefined;
  approvals?: ApprovalHandler | "allow" | "deny" | undefined;
  toolErrorPolicy?: "stop" | "continue" | undefined;
  toolResultMaxBytes?: number | null | undefined;
  workingDirectory?: string | undefined;
  additionalDirectories?: string[] | undefined;
  sessionsDirectory?: string | undefined;
  environment?: Record<string, string> | undefined;
}

interface SessionOptions {
  sessionId?: string | undefined;
  persistence?: "durable" | "ephemeral" | undefined;
  model?: string | undefined;
}
```

[agents](../concepts/agents.md), [models](../concepts/models.md)

An option set to `undefined` is treated as omitted. Omitted provider, model,
`sessionsDirectory`, and approvals values resolve through [configuration](../configuration.md).
`toolErrorPolicy` defaults to `"continue"`. `toolResultMaxBytes` defaults to `131072`, and
`null` removes the cap. Omitted `workingDirectory` uses `process.cwd()` at
construction; see [working directory](../concepts/agents.md#working-directory).
Sessions default to `persistence: "durable"`. Options are snapshotted at construction;
changing the original options does not reconfigure an existing agent.

## Records

```ts
interface TextPart {
  type: "text";
  text: string;
}

interface ImagePart {
  type: "image";
  mediaType: string;  // image/png, image/jpeg, image/gif, image/webp
  data: string;       // standard base64
}

type ContentPart = TextPart | ImagePart;

interface ConversationMessage {
  role: "system" | "developer" | "user" | "assistant";
  content: ContentPart[];
}

interface TurnInput {
  content: ContentPart[];
  model?: string | undefined;
  history?: ConversationMessage[] | undefined;
}

interface TurnResult {
  state: "success" | "failure" | "rejected" | "cancelled";
  content?: TextPart[];
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

`TurnStarted.primary_actual` is `{ provider: string; model: string }`.
`ApprovalDecision.resolution` is an `ApprovalResolution` with `request_id`, `decision`,
and optional `reason`. `ApprovalRequestEvent.request` is an `ApprovalRequest`.
Decision values and the compaction payload are in
[events](../concepts/events.md#the-eleven-types) and [approvals](../concepts/approvals.md).

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
) => Promise<string | ContentPart[]>;

export const BUILTIN_TOOLS: readonly string[];   // the nine built-in tool names

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
  truncated?: boolean;
  original_bytes?: number;
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
  readonly code: string;
  readonly category: string;
  readonly remedy: string;
  readonly retryable: boolean;
  readonly correlation_id?: string;
  readonly details?: unknown;
}
```

[errors](../concepts/errors.md)

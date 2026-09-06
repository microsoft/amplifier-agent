import type { SessionRecord, TurnInfo, TurnInput, TurnRecord, TurnResult } from "../records.js";
import type { CallbackFrame, CallbackReply } from "./callbacks.js";

export interface HostBridge {
  encode(value: unknown): string;
  decode(text: string): unknown;
  dispatch(frame: CallbackFrame, reply: (response: CallbackReply) => void): void;
  settled(): Promise<void>;
}

export interface HostTurn {
  readonly info: TurnInfo;
  events(): AsyncIterable<unknown>;
  cancel(): Promise<void>;
}

export interface HostSession {
  readonly info: SessionRecord;
  readonly history: TurnRecord[];
  run(input: TurnInput): Promise<TurnResult>;
  start_turn(input: TurnInput): Promise<HostTurn>;
  fork(): Promise<HostSession>;
  close(): Promise<void>;
}

export interface HostAgent {
  create_session(options?: Record<string, unknown>): Promise<HostSession>;
  resume_session(sessionId: string): Promise<HostSession>;
  list_sessions(): Promise<SessionRecord[]>;
  delete_session(sessionId: string): Promise<void>;
  close(): Promise<void>;
}

export interface HostModule {
  createAgent(options: {
    options: Record<string, unknown>;
    callback_tools: string[];
    callback_approvals: boolean;
  }, bridge: HostBridge, versions: readonly string[]): Promise<HostAgent>;
}

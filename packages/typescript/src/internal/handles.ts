import { AgentError } from "../errors.js";
import type { Agent, AgentOptions, Session, SessionOptions, SessionRecord, Turn, TurnInfo, TurnInput, TurnRecord, TurnResult } from "../records.js";
import { agentOptions, Callbacks } from "./callbacks.js";
import { freeze, receiveHistory, receiveResult, snapshot } from "./codec.js";
import { Connection } from "./connection.js";
import type { EventStream } from "./events.js";

interface SessionResponse { info: SessionRecord; history: TurnRecord[] }
interface AgentLifetime { assertOpen: () => void; closing: () => Promise<void> | undefined }

function closed(): AgentError {
  return new AgentError({ code: "closed", category: "lifecycle", message: "This object is closed.",
    remedy: "Create a new agent or session before requesting more work.", retryable: false });
}

export async function createAgent(options: AgentOptions): Promise<Agent> {
  const serialized = agentOptions(options);
  const callbacks = new Callbacks(options);
  const callbackTools = (Array.isArray(options.tools) ? options.tools : []).filter((tool) => typeof tool?.handler === "function").map((tool) => tool.name);
  const callbackApprovals = typeof options.approvals === "function";
  const connection = await Connection.open(callbacks);
  try {
    const { agent_id } = await connection.request<{ agent_id: string }>("agent.create", {
      options: serialized, callback_tools: callbackTools, callback_approvals: callbackApprovals,
    });
    return new AgentHandle(connection, agent_id);
  } catch (error) {
    await connection.abort();
    throw error;
  }
}

class AgentHandle implements Agent {
  readonly #connection: Connection;
  readonly #id: string;
  #close: Promise<void> | undefined;

  constructor(connection: Connection, id: string) { this.#connection = connection; this.#id = id; }

  #assertOpen(): void { if (this.#close) throw closed(); }

  #session(response: SessionResponse): Session {
    return new SessionHandle({ assertOpen: () => this.#assertOpen(), closing: () => this.#close }, this.#connection, response);
  }

  async createSession(options?: SessionOptions): Promise<Session> {
    this.#assertOpen();
    const translated: Record<string, unknown> = { ...options };
    if ("sessionId" in translated) { translated.session_id = translated.sessionId; delete translated.sessionId; }
    const response = await this.#connection.request<SessionResponse>("agent.create_session", { agent_id: this.#id, options: translated });
    return this.#session(response);
  }

  async resumeSession(sessionId: string): Promise<Session> {
    this.#assertOpen();
    return this.#session(await this.#connection.request<SessionResponse>("agent.resume_session", { agent_id: this.#id, session_id: sessionId }));
  }

  async listSessions(): Promise<SessionRecord[]> {
    this.#assertOpen();
    return this.#connection.request("agent.list_sessions", { agent_id: this.#id });
  }

  async deleteSession(sessionId: string): Promise<void> {
    this.#assertOpen();
    await this.#connection.request("agent.delete_session", { agent_id: this.#id, session_id: sessionId });
  }

  close(): Promise<void> {
    this.#close ??= (async () => {
      try { await this.#connection.request("agent.close", { agent_id: this.#id }); }
      finally { await this.#connection.close(); }
    })();
    return this.#close;
  }

  [Symbol.asyncDispose](): Promise<void> { return this.close(); }
}

class SessionHandle implements Session {
  readonly #agent: AgentLifetime;
  readonly #connection: Connection;
  readonly #info: SessionRecord;
  #history: TurnRecord[];
  #close: Promise<void> | undefined;

  constructor(agent: AgentLifetime, connection: Connection, response: SessionResponse) {
    this.#agent = agent;
    this.#connection = connection;
    this.#info = freeze(response.info);
    this.#history = receiveHistory(response.history);
  }

  get info(): SessionRecord { return this.#info; }
  get history(): TurnRecord[] { return this.#history; }

  #assertOpen(): void { this.#agent.assertOpen(); if (this.#close) throw closed(); }

  async run(input: TurnInput): Promise<TurnResult> {
    this.#assertOpen();
    const response = await this.#connection.request<{ result: TurnResult; history: TurnRecord[] }>("session.run", {
      session_id: this.#info.session_id, input: snapshot(input),
    });
    this.#history = receiveHistory(response.history);
    return receiveResult(response.result);
  }

  async startTurn(input: TurnInput): Promise<Turn> {
    this.#assertOpen();
    const { info } = await this.#connection.request<{ info: TurnInfo }>("session.start_turn", {
      session_id: this.#info.session_id, input: snapshot(input), event_window: 64,
    });
    const stream = this.#connection.turn(info, (history) => { this.#history = history; });
    return new TurnHandle(this.#connection, info, stream);
  }

  async fork(): Promise<Session> {
    this.#assertOpen();
    return new SessionHandle(this.#agent, this.#connection, await this.#connection.request<SessionResponse>("session.fork", { session_id: this.#info.session_id }));
  }

  close(): Promise<void> {
    this.#close ??= this.#agent.closing() ?? this.#connection.request("session.close", { session_id: this.#info.session_id }).then(() => undefined);
    return this.#close;
  }

  [Symbol.asyncDispose](): Promise<void> { return this.close(); }
}

class TurnHandle implements Turn {
  readonly #connection: Connection;
  readonly #info: TurnInfo;
  readonly #stream: EventStream;

  constructor(connection: Connection, info: TurnInfo, stream: EventStream) {
    this.#connection = connection; this.#info = freeze(info); this.#stream = stream;
  }

  get info(): TurnInfo { return this.#info; }
  events(): ReturnType<Turn["events"]> { return this.#stream.events(); }
  async cancel(): Promise<void> { await this.#connection.request("turn.cancel", { turn_id: this.#info.turn_id }); }
}

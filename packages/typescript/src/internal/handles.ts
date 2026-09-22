import { AgentError } from "../errors.js";
import type { Agent, AgentOptions, Event, Session, SessionOptions, SessionRecord, Turn, TurnInfo, TurnInput, TurnRecord, TurnResult } from "../records.js";
import { contractVersions } from "../version.js";
import { agentOptions, Callbacks } from "./callbacks.js";
import { decode, encode, freeze, receiveError, receiveEvent, receiveHistory, receiveResult, snapshot } from "./codec.js";
import type { HostAgent, HostModule, HostSession, HostTurn } from "./host.js";

function native<T>(operation: () => T): T {
  try { return operation(); }
  catch (error) { throw error instanceof AgentError ? error : receiveError(error); }
}

async function returned<T>(operation: () => Promise<T>): Promise<T> {
  try { return await operation(); }
  catch (error) { throw error instanceof AgentError ? error : receiveError(error); }
}

export async function createAgent(options: AgentOptions): Promise<Agent> {
  const serialized = agentOptions(options);
  const callbacks = new Callbacks(options);
  let host: HostModule;
  try {
    host = await import(new URL("../../runtime/linux-x64/node-host/index.mjs", import.meta.url).href) as HostModule;
  } catch {
    throw new AgentError({ code: "engine_unavailable", category: "lifecycle",
      message: "The agent could not continue running.",
      remedy: "Close this agent and create a new one; verify that the installed package supports your platform.",
      retryable: false });
  }
  const agent = await returned(() => host.createAgent({
    options: serialized,
    callback_tools: (Array.isArray(options.tools) ? options.tools : []).flatMap((tool) => typeof tool === "object" && typeof tool?.handler === "function" ? [tool.name] : []),
    callback_approvals: typeof options.approvals === "function",
  }, {
    encode, decode,
    dispatch: (frame, reply) => callbacks.dispatch(frame, reply),
    settled: () => callbacks.settled(),
  }, contractVersions));
  return new AgentHandle(agent);
}

class AgentHandle implements Agent {
  readonly #host: HostAgent;
  constructor(host: HostAgent) { this.#host = host; }

  async createSession(options?: SessionOptions): Promise<Session> {
    const translated: Record<string, unknown> = { ...options };
    if ("sessionId" in translated) { translated.session_id = translated.sessionId; delete translated.sessionId; }
    return new SessionHandle(await returned(() => this.#host.create_session(snapshot(translated))));
  }

  async resumeSession(sessionId: string): Promise<Session> {
    return new SessionHandle(await returned(() => this.#host.resume_session(sessionId)));
  }

  async listSessions(): Promise<SessionRecord[]> {
    return snapshot(await returned(() => this.#host.list_sessions()));
  }

  async deleteSession(sessionId: string): Promise<void> { await returned(() => this.#host.delete_session(sessionId)); }
  close(): Promise<void> { return returned(() => this.#host.close()); }
  [Symbol.asyncDispose](): Promise<void> { return this.close(); }
}

class SessionHandle implements Session {
  readonly #host: HostSession;
  constructor(host: HostSession) { this.#host = host; }

  get info(): SessionRecord { return native(() => freeze(snapshot(this.#host.info))); }
  get history(): TurnRecord[] { return native(() => receiveHistory(snapshot(this.#host.history))); }

  async run(input: TurnInput): Promise<TurnResult> {
    return receiveResult(snapshot(await returned(() => this.#host.run(snapshot(input)))));
  }

  async startTurn(input: TurnInput): Promise<Turn> {
    return new TurnHandle(await returned(() => this.#host.start_turn(snapshot(input))));
  }

  async fork(): Promise<Session> { return new SessionHandle(await returned(() => this.#host.fork())); }
  close(): Promise<void> { return returned(() => this.#host.close()); }
  [Symbol.asyncDispose](): Promise<void> { return this.close(); }
}

class TurnHandle implements Turn {
  readonly #host: HostTurn;
  constructor(host: HostTurn) { this.#host = host; }

  get info(): TurnInfo { return native(() => freeze(snapshot(this.#host.info))); }
  events(): AsyncIterable<Event> {
    const events = native(() => this.#host.events());
    return { [Symbol.asyncIterator]: () => {
      const iterator = native(() => events[Symbol.asyncIterator]());
      return {
        next: async () => {
          const item = await returned(() => iterator.next());
          return item.done ? { done: true, value: undefined } : {
            done: false, value: freeze(receiveEvent(snapshot(item.value) as Event)),
          };
        },
        ...(iterator.return ? { return: async () => {
          const item = await returned(() => iterator.return!());
          return item.done ? { done: true, value: undefined } : {
            done: false, value: freeze(receiveEvent(snapshot(item.value) as Event)),
          };
        } } : {}),
      };
    } };
  }
  cancel(): Promise<void> { return returned(() => this.#host.cancel()); }
}

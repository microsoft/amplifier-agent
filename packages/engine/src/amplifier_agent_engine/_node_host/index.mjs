import { failure, freeze } from "./records.mjs";
import { Connection } from "./connection.mjs";

function closed() {
  return failure({
    code: "closed",
    category: "lifecycle",
    message: "This handle is closed.",
    remedy: "Create a new agent or session before doing work.",
    retryable: false
  });
}
async function createAgent(options, bridge, versions) {
  const connection = await Connection.open(bridge, versions);
  try {
    const { agent_id } = await connection.request("agent.create", options);
    return new AgentHandle(connection, agent_id);
  } catch (error) {
    await connection.abort();
    throw error;
  }
}
class AgentHandle {
  #connection;
  #id;
  #close;
  constructor(connection, id) {
    this.#connection = connection;
    this.#id = id;
  }
  #assertOpen() {
    if (this.#close) throw closed();
  }
  #session(response) {
    return new SessionHandle({ assertOpen: () => this.#assertOpen(), closing: () => this.#close }, this.#connection, response);
  }
  async create_session(options) {
    this.#assertOpen();
    const translated = { ...options };
    const response = await this.#connection.request("agent.create_session", { agent_id: this.#id, options: translated });
    return this.#session(response);
  }
  async resume_session(sessionId) {
    this.#assertOpen();
    return this.#session(await this.#connection.request("agent.resume_session", { agent_id: this.#id, session_id: sessionId }));
  }
  async list_sessions() {
    this.#assertOpen();
    return this.#connection.request("agent.list_sessions", { agent_id: this.#id });
  }
  async delete_session(sessionId) {
    this.#assertOpen();
    await this.#connection.request("agent.delete_session", { agent_id: this.#id, session_id: sessionId });
  }
  close() {
    this.#close ??= (async () => {
      try {
        await this.#connection.request("agent.close", { agent_id: this.#id });
      } finally {
        await this.#connection.close();
      }
    })();
    return this.#close;
  }
}
class SessionHandle {
  #agent;
  #connection;
  #info;
  #handleId;
  #history;
  #close;
  constructor(agent, connection, response) {
    this.#agent = agent;
    this.#connection = connection;
    this.#handleId = response.handle_id;
    this.#info = freeze(response.info);
    this.#history = freeze(response.history);
  }
  get info() {
    this.#assertOpen();
    return this.#info;
  }
  get history() {
    this.#assertOpen();
    return this.#history;
  }
  #assertOpen() {
    this.#agent.assertOpen();
    if (this.#close) throw closed();
  }
  async run(input) {
    const turn = await this.start_turn(input);
    for await (const event of turn.events()) if (event.type === "terminal") return event.payload;
    throw failure({
      code: "internal_failed",
      category: "internal",
      message: "The turn ended without a terminal result.",
      remedy: "Close this agent and create a new one.",
      retryable: false
    });
  }
  async start_turn(input) {
    this.#assertOpen();
    const { info } = await this.#connection.request("session.start_turn", {
      handle_id: this.#handleId,
      input,
      event_window: 64
    });
    const stream = this.#connection.turn(info, (history) => {
      this.#history = history;
    });
    return new TurnHandle(this.#connection, info, stream, () => this.#assertOpen());
  }
  async fork() {
    this.#assertOpen();
    return new SessionHandle(this.#agent, this.#connection, await this.#connection.request("session.fork", { handle_id: this.#handleId }));
  }
  close() {
    this.#close ??= this.#agent.closing() ?? this.#connection.request("session.close", { handle_id: this.#handleId }).then(() => void 0);
    return this.#close;
  }
}
class TurnHandle {
  #connection;
  #info;
  #stream;
  #assertOpen;
  constructor(connection, info, stream, assertOpen) {
    this.#connection = connection;
    this.#info = freeze(info);
    this.#stream = stream;
    this.#assertOpen = assertOpen;
  }
  get info() {
    this.#assertOpen();
    return this.#info;
  }
  events() {
    return this.#stream.events();
  }
  async cancel() {
    await this.#connection.request("turn.cancel", { turn_id: this.#info.turn_id });
  }
}
export {
  createAgent
};

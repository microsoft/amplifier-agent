/** Independent Node participant for the deterministic replacement engine. */
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

function failure(code, category, message, remedy, fields = {}) {
  return { code, category, message, remedy, retryable: false, ...fields };
}

function closed() {
  return failure("closed", "lifecycle", "This handle is closed.",
    "Create a new agent or session before doing work.");
}

function unreachable() {
  return failure("engine_unavailable", "lifecycle", "The engine process is unavailable.",
    "Create a new agent and resume the durable session before requesting more work.");
}

class Journal {
  constructor(host, info) {
    this.host = host;
    this.info = info;
    this.entries = [];
    this.waiting = new Set();
    this.consumer = false;
    this.done = false;
    this.cancelled = false;
    this.calls = new Map();
    this.approvals = new Map();
    this.replies = new Map();
    this.output = [];
    this.reasoning = "";
    this.usage = undefined;
    this.history = undefined;
    this.session = undefined;
  }

  append(event, history) {
    if (this.done) return;
    const payload = event.payload;
    if (event.type === "output_delta") this.output.push(...payload.content);
    if (event.type === "reasoning_delta") this.reasoning += payload.text;
    if (event.type === "reasoning_final") this.reasoning = "";
    if (event.type === "usage") this.usage = payload.snapshot;
    if (event.type === "tool_call") this.calls.set(payload.call.call_id, payload.call);
    if (event.type === "tool_result") this.calls.delete(payload.resolution.call_id);
    if (event.type === "approval_request") this.approvals.set(payload.request.request_id, payload.request);
    if (event.type === "approval_decision") this.approvals.delete(payload.resolution.request_id);
    if (event.type === "terminal") {
      this.done = true;
      this.history = history;
      if (this.session && history !== undefined) this.session.completed = history;
    }
    this.entries.push(event);
    for (const wake of this.waiting) wake();
    this.waiting.clear();
  }

  events() {
    if (this.consumer) throw failure("stream_already_consumed", "turn", "The turn stream has a consumer.",
      "Consume each turn through one event iterator.");
    this.consumer = true;
    const journal = this;
    return (async function* () {
      let offset = 0;
      while (true) {
        while (offset < journal.entries.length) yield journal.host.copy(journal.entries[offset++]);
        if (journal.done) return;
        await new Promise((resolve) => journal.waiting.add(resolve));
      }
    })();
  }

  emit(type, payload) {
    const sequence = this.entries.length ? BigInt(this.entries.at(-1).sequence) + 1n : 1n;
    this.append({ ...this.info, contract_version: "turn-events/1", sequence, type, payload });
  }

  lost(error) {
    if (this.done) return;
    for (const [call_id] of this.calls) {
      const reply = this.replies.get(call_id);
      let resolution;
      if (reply && typeof reply.result === "string") {
        resolution = { call_id, outcome: "completed", content: reply.result };
      } else if (reply?.error?.kind === "tool_not_executed") {
        resolution = { call_id, outcome: "cancelled" };
      } else {
        const code = reply?.error?.kind === "tool_failed" ? "tool_failed"
          : reply?.error?.kind === "tool_completion_unknown" ? "tool_completion_unknown"
          : reply?.error ? "tool_callback_failed" : reply ? "tool_result_invalid" : "engine_unavailable";
        resolution = { call_id, outcome: code === "tool_failed" ? "failed" : "unknown", error:
          code === "engine_unavailable" ? error : failure(code, "executor",
            reply?.error?.message ?? "The caller tool did not return one text result.",
            "Inspect the effect before requesting further work.", { correlation_id: call_id }) };
      }
      this.emit("tool_result", { resolution });
    }
    for (const [request_id] of this.approvals) {
      const reply = this.replies.get(request_id);
      const result = reply?.result;
      const valid = result && typeof result === "object" && !Array.isArray(result)
        && ["allow", "deny", "cancel"].includes(result.decision)
        && (result.reason === undefined || typeof result.reason === "string")
        && Object.keys(result).every((key) => ["decision", "reason"].includes(key));
      this.emit("approval_decision", { resolution: { request_id,
        decision: valid ? result.decision : reply ? "invalid" : "cancel",
        ...(valid && result.reason !== undefined ? { reason: result.reason } : {}) } });
    }
    if (this.reasoning) this.emit("reasoning_final", { text: this.reasoning });
    if (this.usage !== undefined) this.emit("usage", { snapshot: this.usage });
    this.emit("terminal", {
      state: this.cancelled ? "cancelled" : "failure",
      error: this.cancelled ? failure("turn_cancelled", "turn", "Cancellation was accepted.",
        "Create a new agent before requesting further work.", { correlation_id: this.info.turn_id }) : error,
      ...(this.output.length ? { content: this.output } : {}),
      ...(this.usage === undefined ? {} : { usage: this.usage }),
    });
  }
}

class Host {
  constructor(bridge) {
    this.bridge = bridge;
    this.process = spawn(fileURLToPath(new URL("../amplifier-agent-engine", import.meta.url)), [],
      { stdio: ["pipe", "pipe", "ignore"] });
    this.pending = new Map();
    this.journals = new Map();
    this.callbacks = [];
    this.next = 1;
    this.dead = false;
    this.stopping = false;
    let buffered = "";
    this.process.stdout.setEncoding("utf8");
    this.process.stdout.on("data", (chunk) => {
      buffered += chunk;
      let offset;
      while ((offset = buffered.indexOf("\n")) >= 0) {
        const line = buffered.slice(0, offset);
        buffered = buffered.slice(offset + 1);
        try { this.receive(bridge.decode(line)); } catch { void this.lost(); }
      }
    });
    this.exited = new Promise((resolve) => {
      this.process.once("exit", () => { resolve(); void this.lost(); });
      this.process.once("error", () => { resolve(); void this.lost(); });
    });
    this.process.stdin.on("error", () => { void this.lost(); });
  }

  copy(value) { return this.bridge.decode(this.bridge.encode(value)); }

  journal(info) {
    if (!this.journals.has(info.turn_id)) this.journals.set(info.turn_id, new Journal(this, info));
    return this.journals.get(info.turn_id);
  }

  send(message) {
    if (this.dead) throw unreachable();
    this.process.stdin.write(this.bridge.encode(message) + "\n");
  }

  request(method, params = {}) {
    if (this.dead) return Promise.reject(unreachable());
    const id = this.next++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { void this.lost(); this.process.kill("SIGKILL"); }, 15_000);
      this.pending.set(id, { resolve, reject, timer });
      try { this.send({ id, method, params }); }
      catch (error) { clearTimeout(timer); this.pending.delete(id); reject(error); }
    });
  }

  receive(frame) {
    if (frame.event === "turn_event") {
      const event = frame.event_data;
      this.journal({ session_id: event.session_id, turn_id: event.turn_id }).append(event, frame.history);
      this.send({ method: "turn.ack", params: { turn_id: event.turn_id, count: 1 } });
      this.dispatchCallbacks();
    } else if (frame.event === "cancel_accepted") {
      const journal = this.journals.get(frame.turn_id);
      if (journal) journal.cancelled = true;
    } else if (frame.event === "callback") {
      this.callbacks.push(frame);
      this.dispatchCallbacks();
    } else if (frame.id !== undefined) {
      const waiter = this.pending.get(frame.id);
      if (waiter) {
        clearTimeout(waiter.timer);
        this.pending.delete(frame.id);
        if (frame.error) waiter.reject(frame.error); else waiter.resolve(frame.result);
      }
    }
  }

  dispatchCallbacks() {
    const ready = [], pending = [];
    for (const frame of this.callbacks) {
      const journal = this.journals.get(frame.turn_id);
      const visible = frame.kind === "tool" ? journal?.calls.has(frame.args.context?.call_id)
        : journal?.approvals.has(frame.args.request?.request_id);
      (visible ? ready : pending).push(frame);
    }
    this.callbacks = pending;
    for (const frame of ready) {
      const journal = this.journals.get(frame.turn_id);
      const accept = !this.stopping && !this.dead && !journal?.cancelled && !journal?.done;
      const deliver = (reply) => {
        const correlation = reply.call_id ?? reply.request_id;
        if (journal && correlation) journal.replies.set(correlation, this.copy(reply));
        if (!this.dead) this.send({ method: "callback.resolve", params: reply });
      };
      if (accept) this.bridge.dispatch(this.copy(frame), deliver);
      else deliver({ callback_id: frame.callback_id,
        ...(frame.args.context ? { call_id: frame.args.context.call_id } : {}),
        ...(frame.args.request ? { request_id: frame.args.request.request_id } : {}),
        error: { kind: "tool_not_executed", message: "The owning turn is closing." } });
    }
  }

  async lost() {
    if (this.dead) return;
    this.dead = true;
    const error = unreachable();
    for (const waiter of this.pending.values()) { clearTimeout(waiter.timer); waiter.reject(error); }
    this.pending.clear();
    await this.bridge.settled();
    for (const journal of this.journals.values()) journal.lost(error);
  }

  async stop() {
    this.stopping = true;
    await this.bridge.settled();
    this.process.stdin.end();
    const timer = setTimeout(() => this.process.kill("SIGKILL"), 5_000);
    try { await this.exited; } finally { clearTimeout(timer); }
  }
}

class Session {
  constructor(agent, response) {
    this.agent = agent;
    this.id = response.handle_id;
    this.identity = response.info;
    this.completed = response.history;
    this.closing = undefined;
  }

  check() { if (this.closing || this.agent.closing) throw closed(); }
  get info() { this.check(); return this.agent.host.copy(this.identity); }
  get history() { this.check(); return this.agent.host.copy(this.completed); }
  async start_turn(input) {
    this.check();
    const result = await this.agent.host.request("session.start_turn",
      { handle_id: this.id, input, event_window: 64 });
    const journal = this.agent.host.journal(result.info);
    journal.session = this;
    if (journal.history !== undefined) this.completed = journal.history;
    const session = this;
    return {
      get info() { session.check(); return session.agent.host.copy(result.info); },
      events: () => journal.events(),
      cancel: async () => {
        if (!journal.done) await session.agent.host.request("turn.cancel", { turn_id: result.info.turn_id });
      },
    };
  }
  async run(input) {
    const turn = await this.start_turn(input);
    for await (const event of turn.events()) if (event.type === "terminal") return event.payload;
    throw unreachable();
  }
  async fork() {
    this.check();
    return new Session(this.agent, await this.agent.host.request("session.fork", { handle_id: this.id }));
  }
  close() {
    this.closing ??= this.agent.closing ?? this.agent.host.request("session.close", { handle_id: this.id });
    return this.closing;
  }
}

class Agent {
  constructor(host, id) { this.host = host; this.id = id; this.closing = undefined; }
  check() { if (this.closing) throw closed(); }
  async create_session(options) {
    this.check();
    return new Session(this, await this.host.request("agent.create_session",
      { agent_id: this.id, ...(options === undefined ? {} : { options }) }));
  }
  async resume_session(session_id) {
    this.check();
    return new Session(this, await this.host.request("agent.resume_session", { agent_id: this.id, session_id }));
  }
  async list_sessions() { this.check(); return this.host.request("agent.list_sessions", { agent_id: this.id }); }
  async delete_session(session_id) {
    this.check();
    await this.host.request("agent.delete_session", { agent_id: this.id, session_id });
  }
  close() {
    if (!this.closing) {
      this.host.stopping = true;
      this.closing = this.host.request("agent.close", { agent_id: this.id }).finally(() => this.host.stop());
    }
    return this.closing;
  }
}

export async function createAgent(options, bridge, versions) {
  const host = new Host(bridge);
  try {
    const hello = await host.request("hello", { contract_versions: versions });
    if (!versions.every((version) => hello.contract_versions.includes(version))) {
      throw failure("contract_version_mismatch", "lifecycle", "The engine contract versions differ.",
        "Install matching engine and binding artifacts.");
    }
    const response = await host.request("agent.create", options);
    return new Agent(host, response.agent_id);
  } catch (error) { await host.stop(); throw error; }
}

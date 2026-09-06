import { spawn } from "node:child_process";
import type { ChildProcessWithoutNullStreams } from "node:child_process";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";
import { AgentError } from "../errors.js";
import type { Event, TurnInfo, TurnRecord } from "../records.js";
import { contractVersions } from "../version.js";
import type { CallbackFrame, CallbackReply, Callbacks } from "./callbacks.js";
import { decode, encode, freeze, receiveError, receiveEvent, receiveHistory } from "./codec.js";
import { EventStream } from "./events.js";
import { TurnSupervision } from "./supervision.js";

interface Pending { resolve: (value: unknown) => void; reject: (error: AgentError) => void }
interface TurnChannel { stream: EventStream; supervision: TurnSupervision; history: (history: TurnRecord[]) => void; prepaidAcks: number; callbackCreditOutstanding: boolean }
interface ResponseFrame { id: string; result?: unknown; error?: unknown }
interface EventFrame { event: "turn_event"; turn_id: string; event_data: Event; history?: TurnRecord[] }
interface CancelFrame { event: "cancel_accepted"; turn_id: string }
interface CallbackCancelFrame { event: "callback_cancel"; callback_id: string; turn_id: string }

function unavailable(details?: unknown): AgentError {
  return new AgentError({ code: "engine_unavailable", category: "lifecycle",
    message: "The agent could not continue running.",
    remedy: "Close this agent and create a new one; verify that the installed package supports your platform.",
    retryable: false, ...(details === undefined ? {} : { details }) });
}

export class Connection {
  readonly #child: ChildProcessWithoutNullStreams;
  readonly #callbacks: Callbacks;
  readonly #pending = new Map<string, Pending>();
  readonly #turns = new Map<string, TurnChannel>();
  readonly #earlyEvents = new Map<string, EventFrame[]>();
  readonly #waitingCallbacks: CallbackFrame[] = [];
  readonly #earlyRefusals = new Map<string, Array<{ frame: CallbackFrame; reply: CallbackReply }>>();
  readonly #exited: Promise<void>;
  #failure: AgentError | undefined;
  #nextId = 0;
  #stopping = false;
  #stderr = "";

  private constructor(child: ChildProcessWithoutNullStreams, callbacks: Callbacks) {
    this.#child = child;
    this.#callbacks = callbacks;
    this.#exited = new Promise<void>((resolve) => child.once("close", () => resolve()));
    child.on("error", () => this.#fail(unavailable()));
    child.stdin.on("error", () => this.#fail(unavailable()));
    child.stderr.setEncoding("utf8");
    child.stderr.on("data", (text: string) => { this.#stderr = (this.#stderr + text).slice(-8192); });
    const lines = createInterface({ input: child.stdout, crlfDelay: Infinity });
    lines.on("line", (line) => {
      try { this.#receive(decode(line) as ResponseFrame | EventFrame | CallbackFrame | CancelFrame | CallbackCancelFrame); }
      catch { this.#fail(unavailable()); child.kill(); }
    });
    child.on("close", (code, signal) => {
      if (!this.#stopping || this.#pending.size) this.#fail(unavailable({ exit_code: code, signal, diagnostic: this.#stderr }));
    });
  }

  static async open(callbacks: Callbacks): Promise<Connection> {
    if (process.platform !== "linux" || process.arch !== "x64") throw unavailable();
    const executable = fileURLToPath(new URL("../../runtime/linux-x64/amplifier-agent-engine", import.meta.url));
    const connection = new Connection(spawn(executable, [], { stdio: ["pipe", "pipe", "pipe"] }), callbacks);
    try {
      const hello = await connection.request<{ contract_versions: string[] }>("hello", { contract_versions: contractVersions });
      if (!Array.isArray(hello?.contract_versions) || !contractVersions.every((version) => hello.contract_versions.includes(version))) {
        throw new AgentError({ code: "contract_version_mismatch", category: "lifecycle",
          message: "The installed agent does not satisfy this binding's contract versions.",
          remedy: "Reinstall a matching Amplifier Agent package.", retryable: false });
      }
      return connection;
    } catch (error) {
      await connection.abort();
      throw error;
    }
  }

  request<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
    if (this.#failure) return Promise.reject(this.#failure);
    const id = String(++this.#nextId);
    const line = encode({ id, method, params });
    return new Promise<T>((resolve, reject) => {
      this.#pending.set(id, { resolve: (value) => resolve(value as T), reject });
      this.#child.stdin.write(`${line}\n`);
    });
  }

  notify(method: string, params: Record<string, unknown>): void {
    if (!this.#failure && !this.#child.stdin.destroyed) this.#child.stdin.write(`${encode({ method, params })}\n`);
  }

  turn(info: TurnInfo, history: (history: TurnRecord[]) => void): EventStream {
    const stream = new EventStream(() => {
      if (channel.prepaidAcks > 0) channel.prepaidAcks--;
      else this.notify("turn.ack", { turn_id: info.turn_id, count: 1 });
    });
    const channel = { stream, supervision: new TurnSupervision(info), history, prepaidAcks: 0, callbackCreditOutstanding: false };
    this.#turns.set(info.turn_id, channel);
    for (const { frame, reply } of this.#earlyRefusals.get(info.turn_id) ?? []) channel.supervision.callback(frame, reply);
    this.#earlyRefusals.delete(info.turn_id);
    for (const frame of this.#earlyEvents.get(info.turn_id) ?? []) this.#event(frame, channel);
    this.#earlyEvents.delete(info.turn_id);
    this.#dispatchCallbacks();
    if (this.#failure) {
      for (const event of channel.supervision.lost(this.#failure)) stream.push(event);
      this.#turns.delete(info.turn_id);
    }
    return stream;
  }

  async close(): Promise<void> {
    await this.#callbacks.settled();
    this.#stopping = true;
    this.#child.stdin.end();
    await this.#exited;
  }

  async abort(): Promise<void> {
    this.#stopping = true;
    this.#child.kill();
    await this.#exited;
    await this.#callbacks.settled();
  }

  #receive(frame: ResponseFrame | EventFrame | CallbackFrame | CancelFrame | CallbackCancelFrame): void {
    if ("event" in frame) {
      if ((frame.event === "callback" || frame.event === "callback_cancel")
          && (typeof frame.turn_id !== "string" || !frame.turn_id)) {
        this.#fail(unavailable());
        this.#child.kill();
        return;
      }
      switch (frame.event) {
        case "turn_event": {
          const channel = this.#turns.get(frame.turn_id);
          if (channel) this.#event(frame, channel);
          else {
            const queue = this.#earlyEvents.get(frame.turn_id) ?? [];
            queue.push(frame);
            this.#earlyEvents.set(frame.turn_id, queue);
          }
          break;
        }
        case "callback":
          this.#waitingCallbacks.push(frame);
          this.#dispatchCallbacks();
          break;
        case "callback_cancel": {
          const index = this.#waitingCallbacks.findIndex((callback) => callback.callback_id === frame.callback_id && callback.turn_id === frame.turn_id);
          if (index >= 0) this.#refuseCallback(this.#waitingCallbacks.splice(index, 1)[0]!);
          break;
        }
        case "cancel_accepted":
          this.#turns.get(frame.turn_id)?.supervision.cancellationAccepted();
          this.#dispatchCallbacks();
          break;
      }
    } else {
      const pending = this.#pending.get(frame.id);
      if (!pending) return;
      this.#pending.delete(frame.id);
      if (frame.error !== undefined) pending.reject(receiveError(frame.error));
      else pending.resolve(frame.result);
    }
  }

  #event(frame: EventFrame, channel: TurnChannel): void {
    const event = freeze(receiveEvent(frame.event_data));
    if (frame.history) channel.history(receiveHistory(frame.history));
    channel.supervision.observe(event);
    channel.callbackCreditOutstanding = false;
    channel.stream.push(event);
    if (event.type === "terminal") {
      for (let index = this.#waitingCallbacks.length - 1; index >= 0; index--) {
        if (this.#waitingCallbacks[index]!.turn_id === frame.turn_id) this.#refuseCallback(this.#waitingCallbacks.splice(index, 1)[0]!);
      }
      this.#turns.delete(frame.turn_id);
    }
    this.#dispatchCallbacks();
  }

  #dispatchCallbacks(): void {
    if (this.#failure) return;
    for (let index = 0; index < this.#waitingCallbacks.length;) {
      const frame = this.#waitingCallbacks[index]!;
      const channel = this.#turns.get(frame.turn_id);
      if (!channel) { index++; continue; }
      if (!channel.supervision.allowsCallback()) { this.#waitingCallbacks.splice(index, 1); this.#refuseCallback(frame); continue; }
      if (!channel.supervision.accepts(frame)) { index++; continue; }
      this.#waitingCallbacks.splice(index, 1);
      this.#callbacks.dispatch(frame, (reply) => {
        channel.supervision.callback(frame, reply);
        this.notify("callback.resolve", { ...reply });
      });
    }
    // A paused consumer must not strand the public event preceding a callback.
    for (const turn_id of new Set(this.#waitingCallbacks.map((frame) => frame.turn_id))) {
      const channel = this.#turns.get(turn_id);
      if (!channel || channel.callbackCreditOutstanding) continue;
      channel.callbackCreditOutstanding = true;
      channel.prepaidAcks++;
      this.notify("turn.ack", { turn_id, count: 1 });
    }
  }

  #refuseCallback(frame: CallbackFrame): void {
    const reply: CallbackReply = {
      callback_id: frame.callback_id,
      ...(frame.kind === "tool" ? {
        ...(frame.args.context ? { call_id: frame.args.context.call_id } : {}),
        error: { kind: "tool_not_executed" as const, message: "The turn was cancelled before the caller executor began." },
      } : { ...(frame.args.request ? { request_id: frame.args.request.request_id } : {}), result: { decision: "cancel" } }),
    };
    const channel = this.#turns.get(frame.turn_id);
    if (channel) channel.supervision.callback(frame, reply);
    else {
      const pending = this.#earlyRefusals.get(frame.turn_id) ?? [];
      pending.push({ frame, reply });
      this.#earlyRefusals.set(frame.turn_id, pending);
    }
    this.notify("callback.resolve", { ...reply });
  }

  #fail(error: AgentError): void {
    if (this.#failure) return;
    this.#failure = error;
    this.#waitingCallbacks.length = 0;
    for (const pending of this.#pending.values()) pending.reject(error);
    this.#pending.clear();
    void this.#callbacks.settled().then(() => {
      for (const channel of this.#turns.values()) {
        for (const event of channel.supervision.lost(error)) channel.stream.push(event);
      }
      this.#turns.clear();
    });
  }
}

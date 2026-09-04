import { AgentError } from "../errors.js";
import type { ApprovalResolution, ContentPart, Event, ToolCall, ToolResolution, TurnInfo, Usage } from "../records.js";
import type { CallbackFrame, CallbackReply } from "./callbacks.js";

/** Owns the terminal transition when the engine connection itself is lost. */
export class TurnSupervision {
  readonly #info: TurnInfo;
  #sequence = 0n;
  #terminal = false;
  readonly #content: ContentPart[] = [];
  #usage: Usage | undefined;
  readonly #tools = new Map<string, ToolCall>();
  readonly #approvals = new Set<string>();
  readonly #toolOutcomes = new Map<string, ToolResolution>();
  readonly #approvalOutcomes = new Map<string, ApprovalResolution>();

  constructor(info: TurnInfo) { this.#info = info; }

  observe(event: Event): void {
    this.#sequence = event.sequence;
    switch (event.type) {
      case "output_delta": this.#content.push(...event.payload.content); break;
      case "usage": this.#usage = event.payload.snapshot; break;
      case "tool_call": this.#tools.set(event.payload.call.call_id, event.payload.call); break;
      case "tool_result":
        this.#tools.delete(event.payload.resolution.call_id);
        this.#toolOutcomes.delete(event.payload.resolution.call_id);
        break;
      case "approval_request": this.#approvals.add(event.payload.request.request_id); break;
      case "approval_decision":
        this.#approvals.delete(event.payload.resolution.request_id);
        this.#approvalOutcomes.delete(event.payload.resolution.request_id);
        break;
      case "terminal": this.#terminal = true; break;
    }
  }

  callback(frame: CallbackFrame, reply: CallbackReply): void {
    if (frame.kind === "tool" && frame.args.context) {
      const call_id = frame.args.context.call_id;
      if (!this.#tools.has(call_id)) return;
      if (typeof reply.result === "string") this.#toolOutcomes.set(call_id, { call_id, outcome: "completed", content: reply.result });
      else {
        const code = reply.error?.kind === "tool_failed" ? "tool_failed"
          : reply.error?.kind === "tool_completion_unknown" ? "tool_completion_unknown"
          : reply.error ? "tool_callback_failed" : "tool_result_invalid";
        this.#toolOutcomes.set(call_id, { call_id, outcome: code === "tool_completion_unknown" || code === "tool_callback_failed" ? "unknown" : "failed",
          error: new AgentError({ code, category: "executor", message: reply.error?.message ?? "Caller tool returned an invalid result.",
            remedy: "Inspect the caller tool outcome before starting another effect.", retryable: false }) });
      }
    } else if (frame.args.request && reply.result && typeof reply.result === "object") {
      const response = reply.result as { decision?: unknown; reason?: unknown };
      if (["allow", "deny", "cancel"].includes(String(response.decision))) {
        this.#approvalOutcomes.set(frame.args.request.request_id, {
          request_id: frame.args.request.request_id,
          decision: response.decision as "allow" | "deny" | "cancel",
          ...(typeof response.reason === "string" ? { reason: response.reason } : {}),
        });
      }
    }
  }

  lost(error: AgentError): Event[] {
    if (this.#terminal) return [];
    this.#terminal = true;
    const events: Event[] = [];
    for (const call of this.#tools.values()) events.push(this.#event("tool_result", {
      resolution: this.#toolOutcomes.get(call.call_id) ?? { call_id: call.call_id, outcome: "unknown", error },
    }));
    for (const request_id of this.#approvals) events.push(this.#event("approval_decision", {
      resolution: this.#approvalOutcomes.get(request_id) ?? { request_id, decision: "cancel", reason: error.message },
    }));
    events.push(this.#event("terminal", {
      state: "failure", error,
      ...(this.#content.length ? { content: this.#content } : {}),
      ...(this.#usage === undefined ? {} : { usage: this.#usage }),
    }));
    return events;
  }

  #event(type: Event["type"], payload: unknown): Event {
    return { ...this.#info, contract_version: "turn-events/1", sequence: ++this.#sequence, type, payload } as Event;
  }
}

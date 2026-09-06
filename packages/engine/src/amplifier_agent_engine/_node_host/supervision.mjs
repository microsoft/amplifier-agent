import { failure, freeze } from "./records.mjs";

class TurnSupervision {
  #info;
  #sequence = 0n;
  #terminal = false;
  #cancelled = false;
  #content = [];
  #reasoning = [];
  #usage;
  #tools = /* @__PURE__ */ new Map();
  #approvals = /* @__PURE__ */ new Set();
  #toolOutcomes = /* @__PURE__ */ new Map();
  #approvalOutcomes = /* @__PURE__ */ new Map();
  constructor(info) {
    this.#info = info;
  }
  cancellationAccepted() {
    this.#cancelled = true;
  }
  accepts(frame) {
    if (this.#terminal) return false;
    return frame.kind === "tool" ? this.#tools.has(frame.args.context?.call_id ?? "") : this.#approvals.has(frame.args.request?.request_id ?? "");
  }
  allowsCallback() {
    return !this.#cancelled && !this.#terminal;
  }
  observe(event) {
    this.#sequence = BigInt(event.sequence);
    switch (event.type) {
      case "output_delta":
        this.#content.push(...event.payload.content);
        break;
      case "reasoning_delta":
        this.#reasoning.push(event.payload.text);
        break;
      case "reasoning_final":
        this.#reasoning.length = 0;
        break;
      case "usage":
        this.#usage = event.payload.snapshot;
        break;
      case "tool_call":
        this.#tools.set(event.payload.call.call_id, event.payload.call);
        break;
      case "tool_result":
        this.#tools.delete(event.payload.resolution.call_id);
        this.#toolOutcomes.delete(event.payload.resolution.call_id);
        break;
      case "approval_request":
        this.#approvals.add(event.payload.request.request_id);
        break;
      case "approval_decision":
        this.#approvals.delete(event.payload.resolution.request_id);
        this.#approvalOutcomes.delete(event.payload.resolution.request_id);
        break;
      case "terminal":
        this.#terminal = true;
        break;
    }
  }
  callback(frame, reply) {
    if (frame.kind === "tool" && frame.args.context) {
      const call_id = frame.args.context.call_id;
      if (reply.error?.kind === "tool_not_executed") {
        this.#toolOutcomes.set(call_id, { call_id, outcome: "cancelled" });
        return;
      }
      if (!this.#tools.has(call_id)) return;
      if (typeof reply.result === "string") this.#toolOutcomes.set(call_id, { call_id, outcome: "completed", content: reply.result });
      else {
        const code = reply.error?.kind === "tool_failed" ? "tool_failed" : reply.error?.kind === "tool_completion_unknown" ? "tool_completion_unknown" : reply.error ? "tool_callback_failed" : "tool_result_invalid";
        this.#toolOutcomes.set(call_id, {
          call_id,
          outcome: code === "tool_failed" ? "failed" : "unknown",
          error: failure({
            code,
            category: "executor",
            message: reply.error?.message ?? "Caller tool returned an invalid result.",
            remedy: "Inspect the caller tool outcome before starting another effect.",
            retryable: false,
            correlation_id: call_id
          })
        });
      }
    } else if (frame.args.request && this.#approvals.has(frame.args.request.request_id)) {
      const response = reply.result && typeof reply.result === "object" && !Array.isArray(reply.result) ? reply.result : void 0;
      const valid = response !== void 0 && ["allow", "deny", "cancel"].includes(String(response.decision)) && (response.reason === void 0 || typeof response.reason === "string") && Object.keys(response).every((key) => ["decision", "reason"].includes(key));
      this.#approvalOutcomes.set(frame.args.request.request_id, {
        request_id: frame.args.request.request_id,
        decision: valid ? response.decision : "invalid",
        ...valid && typeof response.reason === "string" ? { reason: response.reason } : {}
      });
    }
  }
  lost(error) {
    if (this.#terminal) return [];
    this.#terminal = true;
    const events = [];
    for (const call of this.#tools.values()) events.push(this.#event("tool_result", {
      resolution: this.#toolOutcomes.get(call.call_id) ?? { call_id: call.call_id, outcome: "unknown", error }
    }));
    for (const request_id of this.#approvals) events.push(this.#event("approval_decision", {
      resolution: this.#approvalOutcomes.get(request_id) ?? { request_id, decision: "cancel", reason: error.message }
    }));
    if (this.#reasoning.length) events.push(this.#event("reasoning_final", { text: this.#reasoning.join("") }));
    if (this.#usage !== void 0) events.push(this.#event("usage", { snapshot: this.#usage }));
    const terminalError = this.#cancelled ? failure({
      code: "turn_cancelled",
      category: "turn",
      message: "The caller cancelled the turn.",
      remedy: "Create a new agent and resume the durable session before starting another turn.",
      retryable: false,
      correlation_id: this.#info.turn_id,
      details: { cause: error.code }
    }) : error;
    events.push(this.#event("terminal", {
      state: this.#cancelled ? "cancelled" : "failure",
      error: terminalError,
      ...this.#content.length ? { content: this.#content } : {},
      ...this.#usage === void 0 ? {} : { usage: this.#usage }
    }));
    return events;
  }
  #event(type, payload) {
    return { ...this.#info, contract_version: "turn-events/1", sequence: ++this.#sequence, type, payload };
  }
}
export {
  TurnSupervision
};

import { AgentError, ToolFailed, ToolOutcomeUnknown } from "../errors.js";
import type { AgentOptions, ApprovalHandler, ApprovalRequest, ToolContext, ToolHandler } from "../records.js";
import { freeze, snapshot } from "./codec.js";

export interface CallbackFrame {
  event: "callback";
  callback_id: string;
  turn_id: string;
  kind: "tool" | "approval";
  args: { name?: string; arguments?: Record<string, unknown>; context?: ToolContext; request?: ApprovalRequest };
}
export interface CallbackReply {
  callback_id: string;
  call_id?: string;
  request_id?: string;
  result?: unknown;
  error?: { kind: "tool_failed" | "tool_completion_unknown" | "tool_not_executed" | "callback_failed"; message: string };
}

export class Callbacks {
  readonly #tools = new Map<string, ToolHandler>();
  readonly #approval: ApprovalHandler | undefined;
  readonly #tasks = new Set<Promise<void>>();

  constructor(options: AgentOptions) {
    for (const tool of Array.isArray(options.tools) ? options.tools : []) {
      if (typeof tool === "object" && typeof tool?.handler === "function") this.#tools.set(tool.name, tool.handler);
    }
    this.#approval = typeof options.approvals === "function" ? options.approvals : undefined;
  }

  dispatch(frame: CallbackFrame, reply: (response: CallbackReply) => void): void {
    const task = this.#invoke(frame).then(reply);
    this.#tasks.add(task);
    void task.finally(() => this.#tasks.delete(task)).catch(() => undefined);
  }

  async settled(): Promise<void> {
    while (this.#tasks.size) await Promise.allSettled(this.#tasks);
  }

  async #invoke(frame: CallbackFrame): Promise<CallbackReply> {
    try {
      let result: unknown;
      if (frame.kind === "tool") {
        const handler = this.#tools.get(frame.args.name ?? "");
        if (!handler || !frame.args.context || !frame.args.arguments) throw new Error("Caller tool handler is unavailable.");
        result = await handler(frame.args.arguments, freeze(frame.args.context));
      } else {
        if (!this.#approval || !frame.args.request) throw new Error("Caller approval handler is unavailable.");
        result = await this.#approval(freeze(frame.args.request));
      }
      let encoded: unknown;
      try { encoded = snapshot(result); } catch { encoded = null; }
      return { callback_id: frame.callback_id, result: encoded,
        ...(frame.args.context ? { call_id: frame.args.context.call_id } : {}),
        ...(frame.args.request ? { request_id: frame.args.request.request_id } : {}),
      };
    } catch (error) {
      return { callback_id: frame.callback_id,
        ...(frame.args.context ? { call_id: frame.args.context.call_id } : {}),
        ...(frame.args.request ? { request_id: frame.args.request.request_id } : {}),
        error: {
        kind: error instanceof ToolOutcomeUnknown ? "tool_completion_unknown" : error instanceof ToolFailed ? "tool_failed" : "callback_failed",
        message: error instanceof Error ? error.message : "Caller callback threw a non-error value.",
      } };
    }
  }
}

export function agentOptions(options: AgentOptions): Record<string, unknown> {
  if (!options || typeof options !== "object" || Array.isArray(options)) {
    throw new AgentError({ code: "invalid_input", category: "input", message: "Agent options must be an object.",
      remedy: "Pass an AgentOptions object to createAgent.", retryable: false });
  }
  const output: Record<string, unknown> = { ...options };
  if ("mcpServers" in output) { output.mcp_servers = output.mcpServers; delete output.mcpServers; }
  if ("toolErrorPolicy" in output) { output.tool_error_policy = output.toolErrorPolicy; delete output.toolErrorPolicy; }
  if ("toolResultMaxBytes" in output) { output.tool_result_max_bytes = output.toolResultMaxBytes; delete output.toolResultMaxBytes; }
  if (typeof options.approvals === "function") delete output.approvals;
  if (Array.isArray(options.tools)) output.tools = options.tools.map((tool) => {
    if (!tool || typeof tool !== "object" || Array.isArray(tool)) return tool;
    const translated: Record<string, unknown> = { ...tool };
    if (typeof tool.handler === "function") delete translated.handler;
    if ("inputSchema" in translated) { translated.input_schema = translated.inputSchema; delete translated.inputSchema; }
    return translated;
  });
  return snapshot(output);
}

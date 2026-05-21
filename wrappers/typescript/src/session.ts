/**
 * SessionHandle — one-shot session wrapper.
 *
 * submit(prompt) returns AsyncIterable<DisplayEvent>:
 *   - Sends turn/submit via JSON-RPC
 *   - Yields every display/event-shaped notification that arrives
 *   - Terminates the iterator when result/final notification is observed
 *     OR when the turn/submit JSON-RPC response arrives (whichever first)
 *   - L14 safety net: if turn/submit response contains a non-null reply and
 *     result/final was never observed, synthesizes a result/final DisplayEvent
 *     with synthesized: true as the last yielded event.
 *
 * Per design D10, only one submit() per subprocess lifetime in v1.
 * A second call throws AaaError(lifecycle_unsupported).
 *
 * cancel()/dispose() both call terminate() on the underlying transport (D3).
 */

import { synthesizeFinalIfMissing } from "./l14.js";
import { makeApprovalHandler } from "./approval.js";
import type { ApprovalAdapter } from "./approval.js";
import { applyDisplayFilter } from "./display.js";
import type { DisplayAdapter } from "./display.js";

/** A display event yielded by SessionHandle.submit(). */
export interface DisplayEvent {
  /** Notification method name, e.g. 'result/delta', 'result/final'. */
  type: string;
  sessionId: string;
  turnId: string;
  /** Present on sub-agent events. */
  parentTurnId?: string;
  /** True if wrapper-synthesized via L14 path. */
  synthesized?: boolean;
  /** The full notification params object. */
  payload: Record<string, unknown>;
}

/** Typed error for AaA wrapper lifecycle and protocol violations. */
export class AaaError extends Error {
  code: string;
  remediation?: string;

  constructor(code: string, remediation?: string) {
    super(remediation ?? code);
    this.code = code;
    this.remediation = remediation;
    this.name = "AaaError";
  }
}

/** The notification method that signals end-of-turn. */
export const TERMINAL_NOTIFICATION = "result/final";

/** Dependencies injected into SessionHandle. */
export interface SessionDeps {
  sessionId: string;
  /** Called by cancel() and dispose() to SIGTERM the subprocess (D3). */
  terminate: () => Promise<unknown>;
}

/** Minimal interface for the JSON-RPC client used by SessionHandle. */
export interface RpcLike {
  call(method: string, params?: unknown): Promise<unknown>;
  onNotification(cb: (notif: { method: string; params?: unknown }) => void): void;
  /** Register a handler for a specific server-initiated request method. */
  onRequest?(method: string, handler: (params: unknown) => Promise<unknown>): void;
}

/** One-shot session handle. */
export class SessionHandle {
  private submitted = false;

  constructor(
    private readonly rpc: RpcLike,
    private readonly deps: SessionDeps,
    approval?: ApprovalAdapter,
    private readonly display?: DisplayAdapter,
  ) {
    // Wire the approval bridge if an adapter is supplied (§5.2).
    if (approval && rpc.onRequest) {
      rpc.onRequest("approval/request", makeApprovalHandler(approval));
    }
  }

  /**
   * Submit a prompt and return an AsyncIterable of DisplayEvents.
   *
   * One-shot per session (D10): throws AaaError on second call.
   */
  submit(prompt: string): AsyncIterable<DisplayEvent> {
    if (this.submitted) {
      throw new AaaError(
        "lifecycle_unsupported",
        "SessionHandle.submit() is one-shot per session (D10); already submitted",
      );
    }
    this.submitted = true;

    const { sessionId } = this.deps;
    const rand = Math.random().toString(36).slice(2);
    const turnId = `turn-${Date.now()}-${rand}`;

    return this.makeIterable(sessionId, turnId, prompt);
  }

  /**
   * Async generator that buffers display events from notification subscription
   * and terminates when result/final arrives or turn/submit response settles.
   *
   * L14 safety net: if turn/submit response contains a non-null reply and
   * result/final was never observed, synthesizes a result/final DisplayEvent
   * with synthesized: true as the last yielded event before the iterator ends.
   *
   * Display filtering: events are passed through applyDisplayFilter(display)
   * before being delivered to both the iterator and the onEvent push callback.
   */
  private async *makeIterable(
    sessionId: string,
    turnId: string,
    prompt: string,
  ): AsyncGenerator<DisplayEvent> {
    type QueueItem = DisplayEvent | null; // null = sentinel (stop)

    const queue: QueueItem[] = [];
    let wakeUp: (() => void) | null = null;
    // L14: track whether result/final notification has been observed.
    let sawFinal = false;

    // Build display filter predicate and onEvent callback reference.
    const keep = this.display !== undefined
      ? applyDisplayFilter(this.display)
      : (_ev: DisplayEvent): boolean => true;
    const onEvent = this.display?.onEvent;

    const push = (item: QueueItem): void => {
      queue.push(item);
      if (wakeUp !== null) {
        const w = wakeUp;
        wakeUp = null;
        w();
      }
    };

    // Subscribe to all notifications — filter and buffer into queue.
    this.rpc.onNotification((notif) => {
      const params = (notif.params ?? {}) as Record<string, unknown>;
      const event: DisplayEvent = {
        type: notif.method,
        sessionId: (params["sessionId"] as string | undefined) ?? sessionId,
        turnId: (params["turnId"] as string | undefined) ?? turnId,
        payload: params,
      };
      // Populate parentTurnId from payload if present.
      const parentTurnId = params["parentTurnId"] as string | undefined;
      if (parentTurnId !== undefined) {
        event.parentTurnId = parentTurnId;
      }

      // Apply display filter: only deliver kept events.
      if (!keep(event)) {
        // Event is suppressed; still check for result/final sentinel.
        if (notif.method === TERMINAL_NOTIFICATION) {
          sawFinal = true;
          push(null);
        }
        return;
      }

      // Invoke push callback before queuing (same filtered stream).
      if (onEvent !== undefined) {
        onEvent(event);
      }

      push(event);
      // result/final signals end-of-turn; push sentinel after the event.
      if (notif.method === TERMINAL_NOTIFICATION) {
        sawFinal = true;
        push(null);
      }
    });

    // Start turn/submit request.
    // On success: L14 synthesis check — if no result/final was observed and
    //   reply is non-null, push a synthesized result/final event before sentinel.
    // On error: push sentinel immediately (no reply to synthesize from).
    void this.rpc
      .call("turn/submit", { sessionId, turnId, prompt })
      .then(
        (result) => {
          const r = result as { reply?: string | null } | null;
          const reply = r != null ? (r.reply ?? null) : null;
          const syn = synthesizeFinalIfMissing({ sawFinal, reply, sessionId, turnId });
          if (syn !== null) {
            // Synthesized events always pass through (no filter for synthetic).
            if (onEvent !== undefined) {
              onEvent(syn);
            }
            push(syn);
          }
          push(null);
        },
        () => {
          // On error, still terminate the iterator.
          push(null);
        },
      );

    // Drain queue until sentinel.
    outer: while (true) {
      while (queue.length > 0) {
        const item = queue.shift();
        if (item === undefined || item === null) break outer;
        yield item;
      }
      await new Promise<void>((resolve) => {
        wakeUp = resolve;
      });
    }
  }

  /** SIGTERM the subprocess (D3). */
  async cancel(): Promise<void> {
    await this.deps.terminate();
  }

  /** Graceful shutdown; SIGTERM if needed (D3). */
  async dispose(): Promise<void> {
    await this.deps.terminate();
  }
}

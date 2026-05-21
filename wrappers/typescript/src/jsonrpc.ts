/**
 * JSON-RPC 2.0 client with per-request-id correlation and notification fanout.
 *
 * Layers JSON-RPC 2.0 semantics on top of any TransportLike (send/onFrame).
 *
 * Design:
 * - call(): allocates a unique request id, creates a Promise, sends the request frame.
 * - dispatch(): routes incoming frames:
 *   - response (has id, no method) → resolve/reject matching pending Promise
 *   - server-initiated request (has id AND method) → call registered handler, send result
 *   - notification (has method, no id) → fanout to all onNotification subscribers
 * - NC-L16 is designed out: each call() has its own independent Promise row in the
 *   pending Map, so two concurrent calls can never interfere.
 */

/** Minimal transport interface: send a frame and register a frame callback. */
export interface TransportLike {
  send(obj: unknown): void;
  onFrame(cb: (obj: unknown) => void): void;
}

/** Notification object fanned out to subscribers. */
export interface Notification {
  method: string;
  params?: unknown;
}

/** Handler for server-initiated requests. Returns the result to send back. */
export type RequestHandler = (params: unknown) => Promise<unknown>;

export class JsonRpcClient {
  private nextId = 1;
  private readonly pending = new Map<
    number,
    { resolve: (value: unknown) => void; reject: (reason: unknown) => void }
  >();
  private readonly notifSubs: Array<(notif: Notification) => void> = [];
  private readonly requestHandlers = new Map<string, RequestHandler>();

  constructor(private readonly transport: TransportLike) {
    transport.onFrame((frame) => this.dispatch(frame));
  }

  /**
   * Send a JSON-RPC 2.0 request and return a Promise that resolves with the result.
   * Allocates a unique id per call — two concurrent calls have independent Promise rows.
   */
  call(method: string, params?: unknown): Promise<unknown> {
    const id = this.nextId++;
    const promise = new Promise<unknown>((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
    });
    this.transport.send({ jsonrpc: "2.0", id, method, params });
    return promise;
  }

  /**
   * Register a subscriber for server-initiated notifications (no id, has method).
   * All subscribers are called for every notification.
   */
  onNotification(cb: (notif: Notification) => void): void {
    this.notifSubs.push(cb);
  }

  /**
   * Register a handler for a specific server-initiated request method.
   * The handler is awaited and its return value is sent back as the result.
   * Unregistered methods receive a -32601 (Method not found) error response.
   */
  onRequest(method: string, handler: RequestHandler): void {
    this.requestHandlers.set(method, handler);
  }

  /** Route an incoming frame to the appropriate handler. */
  private dispatch(frame: unknown): void {
    if (typeof frame !== "object" || frame === null) return;
    const f = frame as Record<string, unknown>;

    const hasId = "id" in f;
    const hasMethod = "method" in f;

    if (hasId && !hasMethod) {
      // Response to a client call (result or error)
      this.handleResponse(f);
    } else if (hasId && hasMethod) {
      // Server-initiated request — dispatch to registered handler
      void this.handleRequest(f);
    } else if (hasMethod && !hasId) {
      // Notification — fanout to subscribers
      this.handleNotification(f);
    }
    // Unknown frame shapes are silently dropped
  }

  private handleResponse(frame: Record<string, unknown>): void {
    const id = frame["id"] as number;
    const pending = this.pending.get(id);
    if (!pending) return;

    this.pending.delete(id);
    if ("error" in frame) {
      pending.reject(frame["error"]);
    } else {
      pending.resolve(frame["result"]);
    }
  }

  private async handleRequest(frame: Record<string, unknown>): Promise<void> {
    const id = frame["id"] as number;
    const method = frame["method"] as string;
    const params = frame["params"];
    const handler = this.requestHandlers.get(method);

    if (!handler) {
      this.transport.send({
        jsonrpc: "2.0",
        id,
        error: { code: -32601, message: "Method not found" },
      });
      return;
    }

    try {
      const result = await handler(params);
      this.transport.send({ jsonrpc: "2.0", id, result });
    } catch (err) {
      this.transport.send({
        jsonrpc: "2.0",
        id,
        error: { code: -32603, message: String(err) },
      });
    }
  }

  private handleNotification(frame: Record<string, unknown>): void {
    const notif: Notification = {
      method: frame["method"] as string,
      params: frame["params"],
    };
    for (const sub of this.notifSubs) {
      sub(notif);
    }
  }
}

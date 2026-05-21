/**
 * Conformance runner — TypeScript.
 *
 * Loads a YAML fixture (same shape as Plan 2 Python loader), drives a minimal
 * JSON-RPC client through a ScriptedTransport that replays server_to_client
 * frames in script order, captures observable events, evaluates fixture
 * assertions, and emits a structured JSON conformance report to stdout.
 *
 * Usage:
 *   tsx runner_ts.ts <fixture_path>
 *
 * Exit code 0 = all assertions passed, 1 = one or more failures.
 */

import { parse as parseYaml } from "yaml";
import { readFileSync } from "node:fs";
import { resolve as resolvePath } from "node:path";

// ---------------------------------------------------------------------------
// Fixture types (mirrors Python loader.py Fixture shape)
// ---------------------------------------------------------------------------

interface ScriptFrame {
  direction: "client_to_server" | "server_to_client";
  method?: string;
  id?: number;
  params?: unknown;
  result?: unknown;
  error?: unknown;
}

interface Assertion {
  kind: string;
  method?: string;
  id?: number;
  source?: string;
  payload_contains?: Record<string, unknown>;
  result?: unknown;
  code?: string;
}

interface Fixture {
  name: string;
  description?: string;
  setup: Record<string, unknown>;
  script: ScriptFrame[];
  assertions: Assertion[];
}

// ---------------------------------------------------------------------------
// loadFixture — ports Python loader.py shape in ~40 lines
// ---------------------------------------------------------------------------

export function loadFixture(fixturePath: string): Fixture {
  const raw = parseYaml(readFileSync(resolvePath(fixturePath), "utf-8")) as Record<string, unknown>;
  if (typeof raw !== "object" || raw === null) {
    throw new Error(`${fixturePath}: top-level must be a mapping`);
  }
  for (const key of ["name", "setup", "script", "assertions"]) {
    if (!(key in raw)) throw new Error(`${fixturePath}: missing top-level key: ${key}`);
  }
  const script = raw["script"] as ScriptFrame[];
  if (!Array.isArray(script) || script.length === 0) {
    throw new Error(`${fixturePath}: script must be a non-empty list`);
  }
  for (const frame of script) {
    if (!("direction" in frame)) throw new Error(`${fixturePath}: script frame missing 'direction'`);
  }
  const assertions = raw["assertions"] as Assertion[];
  if (!Array.isArray(assertions) || assertions.length === 0) {
    throw new Error(`${fixturePath}: assertions must be a non-empty list`);
  }
  for (const a of assertions) {
    if (!("kind" in a)) throw new Error(`${fixturePath}: assertion missing 'kind'`);
  }
  return raw as Fixture;
}

// ---------------------------------------------------------------------------
// ScriptedTransport — replays server_to_client frames synchronously
// ---------------------------------------------------------------------------

type FrameCb = (frame: unknown) => void;

class ScriptedTransport {
  private pos = 0;
  private readonly cbs: FrameCb[] = [];

  constructor(private readonly script: ScriptFrame[]) {}

  onFrame(cb: FrameCb): void {
    this.cbs.push(cb);
  }

  /** Called when the JSON-RPC client sends a client_to_server frame. */
  send(_obj: unknown): void {
    // Advance past the current client_to_server frame.
    while (this.pos < this.script.length) {
      const frame = this.script[this.pos++]!;
      if (frame.direction === "client_to_server") break;
    }
    // Deliver all subsequent server_to_client frames.
    while (this.pos < this.script.length) {
      const frame = this.script[this.pos]!;
      if (frame.direction === "client_to_server") break;
      const wire: Record<string, unknown> = {};
      for (const k of ["id", "method", "params", "result", "error"] as const) {
        if (k in frame) wire[k] = frame[k as keyof ScriptFrame];
      }
      this.pos++;
      for (const cb of this.cbs) cb(wire);
    }
  }
}

// ---------------------------------------------------------------------------
// Minimal JSON-RPC 2.0 client
// ---------------------------------------------------------------------------

interface Notification {
  method: string;
  params?: unknown;
}

type NotifCb = (notif: Notification) => void;
type Resolver = { resolve: (v: unknown) => void; reject: (r: unknown) => void };

class JsonRpcClient {
  private nextId = 1;
  private readonly pending = new Map<number, Resolver>();
  private readonly notifSubs: NotifCb[] = [];

  constructor(transport: { send(o: unknown): void; onFrame(cb: FrameCb): void }) {
    transport.onFrame((frame) => this.dispatch(frame));
    this.transport = transport;
  }

  private readonly transport: { send(o: unknown): void; onFrame(cb: FrameCb): void };

  call(method: string, params?: unknown): Promise<unknown> {
    const id = this.nextId++;
    const p = new Promise<unknown>((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
    });
    this.transport.send({ jsonrpc: "2.0", id, method, params });
    return p;
  }

  onNotification(cb: NotifCb): void {
    this.notifSubs.push(cb);
  }

  private dispatch(frame: unknown): void {
    if (typeof frame !== "object" || frame === null) return;
    const f = frame as Record<string, unknown>;
    const hasId = "id" in f;
    const hasMethod = "method" in f;
    if (hasId && !hasMethod) {
      // Response to a prior call.
      const resolver = this.pending.get(f["id"] as number);
      if (!resolver) return;
      this.pending.delete(f["id"] as number);
      "error" in f ? resolver.reject(f["error"]) : resolver.resolve(f["result"]);
    } else if (hasMethod && !hasId) {
      // Notification.
      const notif: Notification = { method: f["method"] as string, params: f["params"] };
      for (const sub of this.notifSubs) sub(notif);
    }
  }
}

// ---------------------------------------------------------------------------
// ConformanceReport types
// ---------------------------------------------------------------------------

interface AssertionResult {
  kind: string;
  passed: boolean;
  detail: string;
}

export interface ConformanceReport {
  fixture: string;
  language: "typescript";
  passed: boolean;
  assertions: AssertionResult[];
}

// ---------------------------------------------------------------------------
// runFixture
// ---------------------------------------------------------------------------

export async function runFixture(fixturePath: string): Promise<ConformanceReport> {
  const fixture = loadFixture(fixturePath);
  const transport = new ScriptedTransport(fixture.script);
  const rpc = new JsonRpcClient(transport);

  // allNotifs: every notification seen by the consumer (includes synthesized).
  // engineNotifs: only notifications that came from the scripted transport.
  const allNotifs: Notification[] = [];
  const engineNotifs: Notification[] = [];

  rpc.onNotification((notif) => {
    engineNotifs.push(notif);
    allNotifs.push(notif);
  });

  const responses = new Map<number, unknown>();
  const errors = new Map<number, unknown>();

  for (const frame of fixture.script) {
    if (frame.direction !== "client_to_server") continue;

    const method = frame.method!;
    const params = frame.params;
    const frameId = frame.id ?? 0;

    try {
      const result = await rpc.call(method, params);
      responses.set(frameId, result);

      // L14 safety net: after turn/submit, synthesize result/final if the
      // engine omitted it but provided a non-null reply.
      if (method === "turn/submit") {
        const sawFinal = engineNotifs.some((n) => n.method === "result/final");
        const r = result as { reply?: string | null } | null;
        const reply = r != null ? (r.reply ?? null) : null;
        if (!sawFinal && reply !== null) {
          const p = params as Record<string, string> | undefined;
          const sessionId = p?.["sessionId"] ?? "";
          const turnId = p?.["turnId"] ?? "";
          const synth: Notification = {
            method: "result/final",
            params: { sessionId, turnId, text: reply, synthesized: true },
          };
          allNotifs.push(synth); // NOT added to engineNotifs
        }
      }
    } catch (err) {
      errors.set(frameId, err);
    }
  }

  return evaluate(fixture, allNotifs, engineNotifs, responses, errors);
}

// ---------------------------------------------------------------------------
// evaluate
// ---------------------------------------------------------------------------

function evaluate(
  fixture: Fixture,
  allNotifs: Notification[],
  engineNotifs: Notification[],
  responses: Map<number, unknown>,
  errors: Map<number, unknown>,
): ConformanceReport {
  const results: AssertionResult[] = [];

  for (const assertion of fixture.assertions) {
    const kind = assertion.kind;

    if (kind === "notification_emitted") {
      const method = assertion.method!;
      const payloadContains = assertion.payload_contains;
      let passed = false;
      for (const notif of allNotifs) {
        if (notif.method !== method) continue;
        if (payloadContains !== undefined) {
          const notifParams = (notif.params ?? {}) as Record<string, unknown>;
          if (!dictContains(notifParams, payloadContains)) continue;
        }
        passed = true;
        break;
      }
      results.push({ kind, passed, detail: `notification ${JSON.stringify(method)} ${passed ? "found" : "not found"}` });
    } else if (kind === "no_notification") {
      const method = assertion.method!;
      const source = assertion.source;
      const checkList = source === "engine" ? engineNotifs : allNotifs;
      const found = checkList.some((n) => n.method === method);
      const passed = !found;
      results.push({ kind, passed, detail: `notification ${JSON.stringify(method)} ${passed ? "correctly absent" : "unexpectedly found"}` });
    } else if (kind === "error_returned") {
      const id = assertion.id ?? 0;
      const code = assertion.code;
      const err = errors.get(id);
      let passed = false;
      if (err !== undefined) {
        // Use JSON.stringify so structured error objects (e.g. {data:{code:"..."}} )
        // are searchable, mirroring Python's str(frame["error"]) behaviour.
        const errStr = typeof err === "string" ? err : JSON.stringify(err);
        passed = code == null || errStr.includes(code);
      }
      results.push({ kind, passed, detail: `error for id=${id}: ${passed ? "found" : "not found"}` });
    } else if (kind === "response_matches") {
      const id = assertion.id ?? 0;
      const expected = (assertion.result ?? {}) as Record<string, unknown>;
      const actual = responses.get(id);
      const passed = actual !== undefined && typeof actual === "object" && actual !== null &&
        dictContains(actual as Record<string, unknown>, expected);
      results.push({ kind, passed, detail: `response for id=${id}: ${passed ? "matches" : "no match"}` });
    } else {
      // Unknown assertion kinds are skipped with ok=true per spec.
      results.push({ kind, passed: true, detail: `kind ${JSON.stringify(kind)} not evaluated (skipped)` });
    }
  }

  return {
    fixture: fixture.name,
    language: "typescript",
    passed: results.every((r) => r.passed),
    assertions: results,
  };
}

// ---------------------------------------------------------------------------
// _dict_contains helper
// ---------------------------------------------------------------------------

function dictContains(actual: Record<string, unknown>, expected: Record<string, unknown>): boolean {
  for (const [k, v] of Object.entries(expected)) {
    if (!(k in actual)) return false;
    if (typeof v === "object" && v !== null && typeof actual[k] === "object" && actual[k] !== null) {
      if (!dictContains(actual[k] as Record<string, unknown>, v as Record<string, unknown>)) return false;
    } else if (actual[k] !== v) {
      return false;
    }
  }
  return true;
}

// ---------------------------------------------------------------------------
// main (CLI entry point)
// ---------------------------------------------------------------------------

async function main(): Promise<void> {
  const args = process.argv.slice(2);
  if (args.length < 1) {
    process.stderr.write("Usage: tsx runner_ts.ts <fixture_path>\n");
    process.exit(1);
  }
  const report = await runFixture(args[0]!);
  process.stdout.write(JSON.stringify(report) + "\n");
  process.exit(report.passed ? 0 : 1);
}

// Run main when executed directly (not when imported by tests).
if (process.argv[1] !== undefined && process.argv[1].endsWith("runner_ts.ts")) {
  void main();
}

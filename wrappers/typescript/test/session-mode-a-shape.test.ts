/**
 * RED test for A3'/CR-C — simplified Mode A DisplayEvent shape.
 *
 * Asserts the shape specified in the 2026-05-24 Mode A pivot amendment §5.2:
 *
 *   export type DisplayEvent =
 *     | { type: 'init';     sessionId: string }
 *     | { type: 'activity' }
 *     | { type: 'result';   text: string }
 *     | { type: 'error';    code: string;
 *                           classification: 'transport' | 'protocol' | 'engine' | 'approval' | 'unknown';
 *                           severity: 'error' | 'warning';
 *                           correlationId: string;
 *                           message: string;
 *                           stderrTail?: string;
 *                           retryable: boolean }
 *
 * Why this test must FAIL at the RED step:
 *
 *   The current `DisplayEvent` (wrappers/typescript/src/session.ts:26-37) is a flat
 *   interface with required `type: string`, `sessionId: string`, `turnId: string`,
 *   and `payload: Record<string, unknown>` — not a discriminated union. So:
 *
 *   1. The narrow object literals below (e.g. `{ type: 'init', sessionId }` with no
 *      `turnId` or `payload`) do not satisfy the current interface → TS2741 "Property
 *      'turnId' is missing", TS2741 "Property 'payload' is missing".
 *
 *   2. The `// @ts-expect-error` directives that anticipate `turnId` and `payload`
 *      being absent will NOT fire (because both fields still exist on the current
 *      interface) → TS2578 "Unused '@ts-expect-error' directive".
 *
 *   3. The `error`-event literal has fields the current interface does not declare
 *      (`code`, `classification`, `severity`, `correlationId`, `message`, `retryable`)
 *      → TS2353 "Object literal may only specify known properties".
 *
 *   4. The exhaustive `switch (ev.type)` discriminator narrowing relies on a union,
 *      not a flat interface; under the current interface, `ev.text` / `ev.code` etc.
 *      do not exist → TS2339 "Property '...' does not exist on type 'DisplayEvent'".
 *
 *   The compile-time failures above will surface during `npm test` because vitest's
 *   esbuild transform — while it tolerates many TS errors at runtime — will not turn
 *   missing-property and unknown-property errors into valid JS in a way that lets the
 *   runtime expectations match the new shape. To make the runtime signal unambiguous,
 *   this file additionally asserts that `session.ts` exports a `DisplayEvent` whose
 *   serialized literal form (as inspected by the wrapper code) is the simplified
 *   union — i.e. an `init` event has exactly two own keys (`type`, `sessionId`) and
 *   no `turnId`. Under the current interface, the same `submit()` machinery in
 *   session.ts populates `turnId` and `payload` on every emitted event, so the
 *   runtime check fails: production code emits {type, sessionId, turnId, payload, ...}
 *   not {type, sessionId}.
 *
 * GREEN (a later task): rewrite `DisplayEvent` in src/session.ts to the union above
 * and rewrite the emitter in `makeIterable()` to only populate the fields declared
 * by each variant. After that, this test passes.
 */

import { describe, it, expect } from "vitest";
import type { DisplayEvent } from "../src/session.js";
// Value-side import to anchor module resolution (mirrors test/types.test.ts pattern).
import "../src/session.js";

describe("DisplayEvent — simplified Mode A shape (CR-C)", () => {
  it("(i) init event has only { type, sessionId } — no turnId", () => {
    const ev: DisplayEvent = { type: "init", sessionId: "sess-abc" };

    // Narrow to the init branch.
    if (ev.type !== "init") {
      throw new Error("expected init branch");
    }

    expect(ev.sessionId).toBe("sess-abc");

    // turnId must not exist on the init variant under the new shape.
    // @ts-expect-error - turnId is not a property of the init variant (CR-C).
    const turnIdProbe = ev.turnId;
    expect(turnIdProbe).toBeUndefined();

    // Structural assertion: only `type` and `sessionId` keys are present.
    expect(Object.keys(ev).sort()).toEqual(["sessionId", "type"]);
  });

  it("(ii) activity event has no payload fields — just { type }", () => {
    const ev: DisplayEvent = { type: "activity" };

    if (ev.type !== "activity") {
      throw new Error("expected activity branch");
    }

    // No sessionId, turnId, parentTurnId, synthesized, or payload on activity.
    // @ts-expect-error - sessionId is not a property of the activity variant.
    const sidProbe = ev.sessionId;
    // @ts-expect-error - turnId is not a property of the activity variant.
    const tidProbe = ev.turnId;
    // @ts-expect-error - payload is not a property of the activity variant.
    const payloadProbe = ev.payload;

    expect(sidProbe).toBeUndefined();
    expect(tidProbe).toBeUndefined();
    expect(payloadProbe).toBeUndefined();

    // Structural assertion: only the `type` key is present.
    expect(Object.keys(ev)).toEqual(["type"]);
  });

  it("(iii) result event carries `text` only — no payload", () => {
    const ev: DisplayEvent = { type: "result", text: "hello from engine" };

    if (ev.type !== "result") {
      throw new Error("expected result branch");
    }

    expect(ev.text).toBe("hello from engine");

    // payload must not exist on the result variant under the new shape.
    // @ts-expect-error - payload is not a property of the result variant (CR-C).
    const payloadProbe = ev.payload;
    // @ts-expect-error - turnId is not a property of the result variant.
    const turnIdProbe = ev.turnId;

    expect(payloadProbe).toBeUndefined();
    expect(turnIdProbe).toBeUndefined();

    // Structural assertion: only `type` and `text`.
    expect(Object.keys(ev).sort()).toEqual(["text", "type"]);
  });

  it("(iv) error event carries code, classification='engine', severity, correlationId, message, retryable=false", () => {
    const ev: DisplayEvent = {
      type: "error",
      code: "engine_exit_1",
      classification: "engine",
      severity: "error",
      correlationId: "corr-abc-123",
      message: "engine exited non-zero",
      retryable: false,
    };

    if (ev.type !== "error") {
      throw new Error("expected error branch");
    }

    expect(ev.code).toBe("engine_exit_1");
    expect(ev.classification).toBe("engine");
    expect(ev.severity).toBe("error");
    expect(ev.correlationId).toBe("corr-abc-123");
    expect(ev.message).toBe("engine exited non-zero");
    expect(ev.retryable).toBe(false);

    // stderrTail is optional — absent here.
    expect(ev.stderrTail).toBeUndefined();

    // No payload / turnId on the error variant.
    // @ts-expect-error - payload is not a property of the error variant (CR-C).
    const payloadProbe = ev.payload;
    // @ts-expect-error - turnId is not a property of the error variant.
    const turnIdProbe = ev.turnId;
    expect(payloadProbe).toBeUndefined();
    expect(turnIdProbe).toBeUndefined();
  });

  it("(v) discriminated-union exhaustiveness — switch on ev.type narrows each branch", () => {
    // This test pins the union shape: the compiler must be able to narrow `ev`
    // via `switch (ev.type)`. Under the current flat-interface `DisplayEvent`,
    // each case's `ev.text` / `ev.code` / etc. access is a TS2339 error
    // ("Property '...' does not exist on type 'DisplayEvent'").
    const events: DisplayEvent[] = [
      { type: "init", sessionId: "s" },
      { type: "activity" },
      { type: "result", text: "r" },
      {
        type: "error",
        code: "engine_exit_1",
        classification: "engine",
        severity: "error",
        correlationId: "c",
        message: "m",
        retryable: false,
      },
    ];

    const summaries: string[] = [];
    for (const ev of events) {
      switch (ev.type) {
        case "init":
          summaries.push(`init:${ev.sessionId}`);
          break;
        case "activity":
          summaries.push("activity");
          break;
        case "result":
          summaries.push(`result:${ev.text}`);
          break;
        case "error":
          summaries.push(`error:${ev.code}:${ev.classification}:${ev.retryable}`);
          break;
        default: {
          // Exhaustiveness check: the union must be exactly the four variants above.
          const _exhaustive: never = ev;
          throw new Error(`unhandled DisplayEvent variant: ${JSON.stringify(_exhaustive)}`);
        }
      }
    }

    expect(summaries).toEqual([
      "init:s",
      "activity",
      "result:r",
      "error:engine_exit_1:engine:false",
    ]);
  });
});

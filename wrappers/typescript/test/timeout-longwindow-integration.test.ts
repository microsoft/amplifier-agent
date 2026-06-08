/**
 * Long-window integration proof for the opt-in timeout contract.
 *
 * Unlike `session-subprocess.test.ts` (which uses ~300ms observation windows),
 * this test exercises the REAL public API path — `spawnAgent()` + `submit()` —
 * against a REAL shell-script "engine" subprocess that genuinely stays alive
 * for ~12 seconds. That is well past:
 *   - the point a `setTimeout(…, 0)` would have fired under the OLD
 *     `timeoutMs ?? DEFAULT_TIMEOUT_MS` bug (`0` was kept, fired immediately);
 *   - and far enough to prove the disabled timer is truly OFF, not just delayed.
 *
 * The three cases mirror the production failure modes we are fixing:
 *
 *   (1) timeoutMs: 0        — the Stark/Paperclip adapter path (adapter now
 *       passes an explicit 0). Must complete normally with NO engine_hung
 *       across the full ~12s.
 *   (2) timeoutMs: undefined — the original Paperclip path. Must complete
 *       normally with NO engine_hung (the silent 10-min default is gone).
 *   (3) timeoutMs: 500       — POSITIVE CONTROL. The timer MUST still fire
 *       (~500ms) and cancel the long-lived subprocess, proving we didn't
 *       just disable the feature wholesale.
 *
 * The mock engine is the same shell-script approach the existing
 * session-subprocess harness uses (a POSIX script written to a tmpfile in
 * beforeAll), extended to: emit one NDJSON wire event on stderr, sleep ~12s
 * to simulate a long silent span of deep work, then emit a valid §4.1
 * envelope on stdout and exit 0.
 */
import { describe, it, expect, beforeAll } from "vitest";
import { mkdtemp, writeFile, chmod } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  spawnAgent,
  PROTOCOL_VERSION_REQUIRED_BY_WRAPPER,
} from "../src/index.js";
import type { SpawnAgentParams } from "../src/index.js";
import type { DisplayEvent } from "../src/session.js";

/** How long the mock engine stays alive before emitting its result. */
const ENGINE_ALIVE_MS = 12_000;

let workDir = "";
/** Mock engine: emits NDJSON on stderr, sleeps ~12s, emits envelope, exits 0. */
let slowEngineBin = "";

beforeAll(async () => {
  workDir = await mkdtemp(join(tmpdir(), "timeout-longwindow-test-"));

  slowEngineBin = join(workDir, "slow-engine.sh");
  const envelope = JSON.stringify({
    protocolVersion: PROTOCOL_VERSION_REQUIRED_BY_WRAPPER,
    sessionId: "sess-long",
    turnId: "turn-long",
    reply: "completed after long work",
    error: null,
    metadata: {},
  });
  await writeFile(
    slowEngineBin,
    [
      "#!/bin/sh",
      "# Emit a wire NDJSON event on stderr immediately (exercises the",
      "# parseNdjsonStream path), then go silent for ~12s — the exact",
      "# 'deep work with no events' span the new contract must tolerate.",
      'echo \'{"method":"tool/started","params":{"name":"bash"}}\' >&2',
      "sleep 12",
      "# Long work done: emit a valid §4.1 envelope on stdout and exit clean.",
      "cat <<'EOF'",
      envelope,
      "EOF",
    ].join("\n"),
    { mode: 0o755 },
  );
  await chmod(slowEngineBin, 0o755);
});

function makeSpawnParams(
  overrides: Partial<SpawnAgentParams>,
): SpawnAgentParams {
  return {
    lifecycle: "one-shot",
    sessionId: "sess-long",
    // Inject the mock engine binary + a synthetic version probe so spawnAgent
    // does not try to resolve/probe a real amplifier-agent install.
    _binaryResolver: () => slowEngineBin,
    _engineVersionProbe: async () => ({
      version: "0.0.0-mock",
      protocolVersion: PROTOCOL_VERSION_REQUIRED_BY_WRAPPER,
    }),
    ...overrides,
  };
}

/** Collect EVERY event across the whole run (no early break) so we can prove
 *  no engine_hung appears anywhere in the ~12s window. */
async function collectAll(
  iter: AsyncIterable<DisplayEvent>,
): Promise<DisplayEvent[]> {
  const out: DisplayEvent[] = [];
  for await (const ev of iter) {
    out.push(ev);
  }
  return out;
}

function hungEvents(events: DisplayEvent[]): DisplayEvent[] {
  return events.filter(
    (e) => e.type === "error" && e.code === "engine_hung",
  );
}

describe("Long-window timeout contract (real spawnAgent + ~12s engine)", () => {
  it(
    "(1) timeoutMs: 0 → engine runs ~12s, completes with result, NO engine_hung",
    async () => {
      const start = Date.now();
      const handle = await spawnAgent(makeSpawnParams({ timeoutMs: 0 }));
      const events = await collectAll(handle.submit("deep work"));
      const elapsed = Date.now() - start;

      // The engine genuinely stayed alive the full window (not killed early).
      expect(elapsed).toBeGreaterThanOrEqual(ENGINE_ALIVE_MS - 1500);

      // No hang error anywhere across the entire ~12s.
      expect(hungEvents(events)).toHaveLength(0);

      // Run completed normally via the engine's envelope.
      const last = events[events.length - 1];
      expect(last).toEqual({ type: "result", text: "completed after long work" });
      expect(events[0]).toMatchObject({ type: "init" });
    },
    20_000,
  );

  it(
    "(2) timeoutMs: undefined → engine runs ~12s, completes with result, NO engine_hung",
    async () => {
      const start = Date.now();
      // timeoutMs intentionally omitted → spawnAgent forwards nothing →
      // SessionHandle sees undefined → no timer armed (no silent 10-min default).
      const handle = await spawnAgent(makeSpawnParams({}));
      const events = await collectAll(handle.submit("deep work"));
      const elapsed = Date.now() - start;

      expect(elapsed).toBeGreaterThanOrEqual(ENGINE_ALIVE_MS - 1500);
      expect(hungEvents(events)).toHaveLength(0);

      const last = events[events.length - 1];
      expect(last).toEqual({ type: "result", text: "completed after long work" });
      expect(events[0]).toMatchObject({ type: "init" });
    },
    20_000,
  );

  it(
    "(3) POSITIVE CONTROL timeoutMs: 500 → engine_hung fires ~500ms and cancels the ~12s subprocess",
    async () => {
      const start = Date.now();
      const handle = await spawnAgent(makeSpawnParams({ timeoutMs: 500 }));
      const events = await collectAll(handle.submit("deep work"));
      const elapsed = Date.now() - start;

      // The timer fired and cancelled — we did NOT wait the full ~12s.
      // (Includes cancel()'s SIGTERM→grace path, so allow generous headroom
      //  but stay well under the 12s the engine would otherwise run.)
      expect(elapsed).toBeLessThan(ENGINE_ALIVE_MS - 2000);

      // The hang error WAS emitted and is terminal.
      const hung = hungEvents(events);
      expect(hung).toHaveLength(1);
      const last = events[events.length - 1];
      expect(last?.type).toBe("error");
      if (last?.type !== "error") return;
      expect(last.code).toBe("engine_hung");
      expect(last.classification).toBe("engine");
      expect(last.message).toMatch(/hung past 500ms/);
    },
    20_000,
  );
});

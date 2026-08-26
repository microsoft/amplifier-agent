/**
 * Tests for SessionHandle as Mode A v2 subprocess driver (amendment §5.2, A3').
 *
 * Covers the §5.2 contract:
 *   (a) submit() yields {type:'init', sessionId} synchronously, BEFORE spawning;
 *   (b) submit() is one-shot — second call throws AaaError(lifecycle_unsupported);
 *   (c) successful subprocess (envelope on stdout) emits {type:'result', text};
 *   (d) non-zero exit without envelope synthesizes {type:'error', code:'engine_exit_<n>'};
 *   (e) per-submit timeoutMs triggers {type:'error', code:'engine_hung'};
 *   (f) MCP spill file is cleaned up after a normal exit (CR-A);
 *   (g) detached:true so PID == PGID (SC-B group-signal precondition);
 *   (h) cancel() on a never-spawned handle is a no-op.
 *
 * The "engine binary" used here is a tiny POSIX shell script written to a
 * tmpfile in beforeAll() — the wrapper passes argv to it but the script
 * ignores argv and writes a fixed JSON envelope (or sleeps, in the timeout
 * case). This keeps tests platform-portable on macOS + Linux.
 */
import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { mkdtemp, writeFile, chmod, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { SessionHandle, AaaError } from "../src/session.js";
import type { DisplayEvent, SessionHandleParams } from "../src/session.js";

let workDir = "";
let echoBin = "";
let exitBin = "";
let sleepBin = "";

beforeAll(async () => {
  workDir = await mkdtemp(join(tmpdir(), "session-subprocess-test-"));

  // (a) echoBin — writes a valid §4.1 envelope on stdout, exits 0.
  echoBin = join(workDir, "echo-envelope.sh");
  await writeFile(
    echoBin,
    [
      "#!/bin/sh",
      "# Ignore all argv; emit a §4.1 envelope.",
      "cat <<'EOF'",
      JSON.stringify({
        protocolVersion: "0.2.0",
        sessionId: "sess-test",
        turnId: "turn-test",
        reply: "hello from fake engine",
        error: null,
        metadata: {},
      }),
      "EOF",
    ].join("\n"),
    { mode: 0o755 },
  );
  await chmod(echoBin, 0o755);

  // (b) exitBin — exits non-zero with empty stdout. Triggers Rule-2 synthesis.
  exitBin = join(workDir, "exit-nonzero.sh");
  await writeFile(
    exitBin,
    ["#!/bin/sh", "echo 'boom' >&2", "exit 7"].join("\n"),
    { mode: 0o755 },
  );
  await chmod(exitBin, 0o755);

  // (c) sleepBin — sleeps forever. The wrapper's timeoutMs cancels it.
  sleepBin = join(workDir, "sleep-forever.sh");
  await writeFile(
    sleepBin,
    ["#!/bin/sh", "exec sleep 30"].join("\n"),
    { mode: 0o755 },
  );
  await chmod(sleepBin, 0o755);
});

afterAll(async () => {
  // mkdtemp dir cleanup is best-effort; OS reaps /tmp regardless.
});

/** Drain an AsyncIterable<DisplayEvent> into an array (with a hard cap to keep
 *  runaway iterators from hanging the test if the contract regresses). */
async function drain(
  iter: AsyncIterable<DisplayEvent>,
  cap = 20,
): Promise<DisplayEvent[]> {
  const out: DisplayEvent[] = [];
  for await (const ev of iter) {
    out.push(ev);
    if (out.length >= cap) break;
  }
  return out;
}

function makeParams(overrides: Partial<SessionHandleParams>): SessionHandleParams {
  return {
    binaryPath: echoBin,
    sessionId: "sess-test",
    subprocessEnv: { PATH: "/usr/bin:/bin" },
    protocolVersion: "0.2.0",
    ...overrides,
  };
}

describe("SessionHandle (Mode A v2 subprocess driver, §5.2)", () => {
  it("(a) yields {type:'init', sessionId} synchronously before spawning", async () => {
    const handle = new SessionHandle(makeParams({ binaryPath: echoBin }));
    const iter = handle.submit("hi")[Symbol.asyncIterator]();

    // The first event must be available without waiting on any subprocess I/O —
    // i.e. it is produced synchronously by the generator before async work.
    const first = await iter.next();
    expect(first.done).toBe(false);
    expect(first.value).toEqual({ type: "init", sessionId: "sess-test" });

    // Drain the rest so the subprocess is reaped before the test ends.
    const rest: DisplayEvent[] = [];
    while (true) {
      const r = await iter.next();
      if (r.done) break;
      rest.push(r.value);
      if (rest.length > 20) break;
    }
  }, 10000);

  it("(b) second submit() throws AaaError(lifecycle_unsupported)", async () => {
    const handle = new SessionHandle(makeParams({ binaryPath: echoBin }));

    const iter1 = handle.submit("first");
    // Drain the first iter so the subprocess is reaped.
    await drain(iter1);

    let caught: unknown;
    try {
      handle.submit("second");
    } catch (e) {
      caught = e;
    }
    expect(caught).toBeInstanceOf(AaaError);
    expect((caught as AaaError).code).toBe("lifecycle_unsupported");
    expect((caught as AaaError).message).toMatch(/one-shot|already submitted/i);
  }, 10000);

  it("(c) successful subprocess emits {type:'result', text} from envelope", async () => {
    const handle = new SessionHandle(makeParams({ binaryPath: echoBin }));
    const events = await drain(handle.submit("hello"));

    // init must come first; then 0+ activity ticks; then result.
    expect(events[0]).toEqual({ type: "init", sessionId: "sess-test" });

    const result = events[events.length - 1];
    expect(result).toMatchObject({
      type: "result",
      text: "hello from fake engine",
    });
    // Protocol 0.4.0: the identity fields the envelope carries are surfaced on
    // the terminal event rather than discarded by the parser.
    if (result?.type !== "result") throw new Error("expected result event");
    expect(typeof result.sessionId).toBe("string");
    expect(typeof result.turnId).toBe("string");
    expect(result.exitCode).toBe(0);

    // No "error" variants in the stream.
    for (const ev of events) {
      expect(ev.type).not.toBe("error");
    }
  }, 10000);

  it("(d) non-zero exit without envelope synthesizes engine_exit_<n> error", async () => {
    const handle = new SessionHandle(makeParams({ binaryPath: exitBin }));
    const events = await drain(handle.submit("boom"));

    const last = events[events.length - 1];
    expect(last?.type).toBe("error");
    if (last?.type !== "error") return;
    expect(last.code).toBe("engine_exit_7");
    expect(last.classification).toBe("engine");
    expect(last.severity).toBe("error");
    expect(last.retryable).toBe(false);
    // stderr "boom\n" should be captured into stderrTail.
    expect(last.stderrTail).toMatch(/boom/);
  }, 10000);

  it("(e) timeoutMs: 250 (opted-in) → engine_hung error event yielded", async () => {
    const handle = new SessionHandle(
      makeParams({ binaryPath: sleepBin, timeoutMs: 250 }),
    );
    const events = await drain(handle.submit("never returns"));

    const last = events[events.length - 1];
    expect(last?.type).toBe("error");
    if (last?.type !== "error") return;
    expect(last.code).toBe("engine_hung");
    expect(last.classification).toBe("engine");
    expect(last.message).toMatch(/hung past/);
  }, 10000);

  it("(j) timeoutMs: 150 (opted-in) → engine_hung emitted when subprocess outlives timer", async () => {
    // Proves the wall-clock hang timer still works when the caller explicitly
    // opts in with a positive timeoutMs.
    const handle = new SessionHandle(
      makeParams({ binaryPath: sleepBin, timeoutMs: 150 }),
    );
    const events = await drain(handle.submit("never returns"));

    const last = events[events.length - 1];
    expect(last?.type).toBe("error");
    if (last?.type !== "error") return;
    expect(last.code).toBe("engine_hung");
    expect(last.classification).toBe("engine");
    expect(last.message).toMatch(/hung past 150ms/);
  }, 10000);

  it("(k) timeoutMs: 0 → no engine_hung within 300ms window (explicit disable; must NOT fire immediately)", async () => {
    // Regression guard: the old code did `timeoutMs ?? DEFAULT_TIMEOUT_MS`
    // which kept 0 (nullish coalescing only replaces null/undefined), then
    // called setTimeout(callback, 0) — firing engine_hung on the very next
    // event-loop tick.  The new contract: 0 disables the timer entirely.
    const handle = new SessionHandle(
      makeParams({ binaryPath: sleepBin, timeoutMs: 0 }),
    );

    const observed: DisplayEvent[] = [];
    const iterPromise = (async () => {
      for await (const ev of handle.submit("never returns")) {
        observed.push(ev);
      }
    })();

    // 300ms is comfortably longer than a setTimeout(…, 0) would take to fire.
    await new Promise<void>((r) => setTimeout(r, 300));

    // Cancel the subprocess so the iterator terminates and we can await it.
    await handle.cancel();
    await iterPromise;

    const hungCodes = observed
      .filter((e) => e.type === "error")
      .map((e) => (e as { type: "error"; code: string }).code);
    expect(hungCodes).not.toContain("engine_hung");
    expect(observed[0]).toMatchObject({ type: "init" });
  }, 10000);

  it("(l) timeoutMs: undefined → no engine_hung within 300ms window (no silent default timeout)", async () => {
    // Proves the new contract: omitting timeoutMs arms NO timer at all.
    // Under the old code this would set a 10-minute timer (harmless in a
    // 300ms window), but documents the intent: turns run until the engine
    // exits, with no wall-clock cap unless the caller passes a positive value.
    const handle = new SessionHandle(
      makeParams({ binaryPath: sleepBin }), // timeoutMs intentionally omitted
    );

    const observed: DisplayEvent[] = [];
    const iterPromise = (async () => {
      for await (const ev of handle.submit("never returns")) {
        observed.push(ev);
      }
    })();

    await new Promise<void>((r) => setTimeout(r, 300));

    await handle.cancel();
    await iterPromise;

    const hungCodes = observed
      .filter((e) => e.type === "error")
      .map((e) => (e as { type: "error"; code: string }).code);
    expect(hungCodes).not.toContain("engine_hung");
    expect(observed[0]).toMatchObject({ type: "init" });
  }, 10000);

  it("(f) cancel() before submit is a no-op and clears no spill", async () => {
    const handle = new SessionHandle(makeParams({}));
    // Must not throw and must not leave the handle in a one-shot-consumed state.
    await handle.cancel();
    await handle.dispose();
  });

  it("(g) cleanup spill file after exit (CR-A)", async () => {
    // Use an MCP config with an env-bearing server so a spill file IS created.
    const handle = new SessionHandle(
      makeParams({
        binaryPath: echoBin,
        mcpServers: {
          "secret-server": {
            command: "node",
            args: ["server.js"],
            env: { API_KEY: "s3cr3t" },
          } as never,
        },
      }),
    );

    // We don't easily intercept the spill path from outside; instead, after
    // the iterator completes the wrapper's `cleanupSpillFile` must have been
    // called. We verify that no spill files remain under the per-session dir.
    const events = await drain(handle.submit("hi"));
    expect(events.some((e) => e.type === "result")).toBe(true);

    // The per-session spill dir is `$XDG_RUNTIME_DIR/amplifier-agent/<sid>/`
    // or `os.tmpdir()/amplifier-agent/<sid>/`. We pin sessionId=sess-test.
    const spillBase =
      process.env["XDG_RUNTIME_DIR"] && process.env["XDG_RUNTIME_DIR"].length > 0
        ? join(process.env["XDG_RUNTIME_DIR"], "amplifier-agent", "sess-test")
        : join(tmpdir(), "amplifier-agent", "sess-test");
    const spillFile = join(spillBase, "mcp.json");
    let exists = true;
    try {
      await stat(spillFile);
    } catch {
      exists = false;
    }
    expect(exists).toBe(false);
  }, 10000);

  it("(h) getEngineInfo reports binaryPath + protocolVersion from params", () => {
    const handle = new SessionHandle(
      makeParams({ binaryPath: "/fake/path/agent", protocolVersion: "0.2.0" }),
    );
    const info = handle.getEngineInfo();
    expect(info.binaryPath).toBe("/fake/path/agent");
    expect(info.protocolVersion).toBe("0.2.0");
  });

  it("(i) spawn failure (ENOENT binary) yields spawn_failed error", async () => {
    const handle = new SessionHandle(
      makeParams({ binaryPath: join(workDir, "this-file-does-not-exist") }),
    );
    const events = await drain(handle.submit("anything"));

    expect(events[0]).toEqual({ type: "init", sessionId: "sess-test" });
    const last = events[events.length - 1];
    expect(last?.type).toBe("error");
    if (last?.type !== "error") return;
    expect(last.code).toBe("spawn_failed");
    expect(last.classification).toBe("transport");
  }, 10000);
});

/**
 * Identity on the synthesized (Rule 2) failure paths.
 *
 * No envelope came back on any of these, so there is nothing to READ the
 * identity from — but the handle was handed a sessionId at construction time,
 * so it knows one anyway and a host correlating the failure needs it. The
 * turnId is the opposite case: the engine assigns it, nothing came back, and
 * inventing one would be a fabrication.
 */
describe("SessionHandle — synthesized failures carry the session id, never a turn id", () => {
  it("(m) spawn_failed reports the handle's sessionId and no turnId", async () => {
    const handle = new SessionHandle(
      makeParams({
        binaryPath: join(workDir, "this-file-does-not-exist"),
        sessionId: "sess-synth-1",
      }),
    );
    const events = await drain(handle.submit("anything"));

    const last = events[events.length - 1];
    expect(last?.type).toBe("error");
    if (last?.type !== "error") return;
    expect(last.code).toBe("spawn_failed");
    expect(last.sessionId).toBe("sess-synth-1");
    expect(last.turnId).toBeUndefined();
  }, 10000);

  it("(n) engine_hung reports the handle's sessionId and no turnId", async () => {
    const handle = new SessionHandle(
      makeParams({
        binaryPath: sleepBin,
        sessionId: "sess-synth-2",
        timeoutMs: 250,
      }),
    );
    const events = await drain(handle.submit("never returns"));

    const last = events[events.length - 1];
    expect(last?.type).toBe("error");
    if (last?.type !== "error") return;
    expect(last.code).toBe("engine_hung");
    expect(last.sessionId).toBe("sess-synth-2");
    expect(last.turnId).toBeUndefined();
  }, 10000);

  it("(o) engine_exit_<n> from the parser reports the handle's sessionId and no turnId", async () => {
    // Proves the parser's Rule 2 path is reached WITH the fallback wired in by
    // SessionHandle, not just when parseRunOutput is called directly.
    const handle = new SessionHandle(
      makeParams({ binaryPath: exitBin, sessionId: "sess-synth-3" }),
    );
    const events = await drain(handle.submit("boom"));

    const last = events[events.length - 1];
    expect(last?.type).toBe("error");
    if (last?.type !== "error") return;
    expect(last.code).toBe("engine_exit_7");
    expect(last.sessionId).toBe("sess-synth-3");
    expect(last.turnId).toBeUndefined();
  }, 10000);

  it("(p) a parsed envelope still wins: result keeps the ENVELOPE's ids, not the handle's", async () => {
    // echoBin emits sessionId "sess-test" / turnId "turn-test" regardless of
    // argv, so a mismatched handle id proves the envelope is authoritative.
    const handle = new SessionHandle(
      makeParams({ binaryPath: echoBin, sessionId: "sess-handle-mismatch" }),
    );
    const events = await drain(handle.submit("hi"));

    const last = events[events.length - 1];
    expect(last?.type).toBe("result");
    if (last?.type !== "result") return;
    expect(last.sessionId).toBe("sess-test");
    expect(last.turnId).toBe("turn-test");
  }, 10000);
});

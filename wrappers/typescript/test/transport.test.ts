/**
 * Tests for Transport: subprocess spawn + NDJSON framing.
 *
 * RED: fails because wrappers/typescript/src/transport.ts does not exist yet.
 * GREEN: passes once Transport is implemented.
 *
 * TDD bullets:
 * (a) cat echo: send JSON frame, receive it back via onFrame callback
 * (b) non-JSON dropped: only valid JSON lines trigger onFrame
 * (c) terminate: SIGTERMs a long-running child and resolves on the child's own
 *     exit, even when an orphaned grandchild keeps the stdio pipes open
 */
import { describe, it, expect } from "vitest";
import { Transport } from "../src/transport.js";

describe("Transport", () => {
  it(
    "round-trip: cat echoes back JSON frame",
    async () => {
      const t = new Transport({ command: "cat", args: [], env: {} });

      // Resolve on first frame so we wait for the echo before terminating.
      let resolveFirst!: (obj: unknown) => void;
      const firstFrame = new Promise<unknown>((r) => {
        resolveFirst = r;
      });
      t.onFrame((obj) => resolveFirst(obj));

      await t.spawn();
      await t.send({ hello: "world" });
      const frame = await firstFrame;
      await t.terminate();

      expect(frame).toEqual({ hello: "world" });
    },
    5000,
  );

  it(
    "drops non-JSON lines silently",
    async () => {
      const frames: unknown[] = [];

      // Resolve once we receive the first (and only) valid JSON frame.
      // This guarantees printf has run before we inspect frames.
      let resolveFirstJson!: () => void;
      const firstJson = new Promise<void>((r) => {
        resolveFirstJson = r;
      });

      const t = new Transport({
        command: "sh",
        args: ["-c", String.raw`printf "not json\n{\"ok\":true}\n"`],
        env: {},
      });
      t.onFrame((obj) => {
        frames.push(obj);
        resolveFirstJson();
      });

      await t.spawn();
      await firstJson; // wait until the JSON line has been parsed and dispatched
      await t.terminate();

      expect(frames).toEqual([{ ok: true }]);
    },
    5000,
  );

  it(
    "terminate() resolves with SIGTERM signal or non-zero exit code",
    async () => {
      // `sleep 60 &` + `wait` forces the shell to FORK the sleep instead of
      // exec'ing into it, so SIGTERM to the shell leaves `sleep` alive holding
      // the stdout/stderr pipes it inherited. That is the orphaned-grandchild
      // case: the child is dead, but its pipes never reach EOF, so the child
      // process 'close' event never fires. terminate() must resolve on the
      // child's own exit -- otherwise it blocks for as long as the ORPHAN
      // lives (60s here), which is exactly how this test timed out in CI.
      //
      // Do not "simplify" this to spawn("sleep", ["60"]) or `sh -c "sleep 60"`:
      // neither reproduces the bug reliably. Whether a shell forks or execs for
      // `sh -c "<single command>"` varies by shell and version, which is why
      // this failure looked like flakiness rather than the deterministic hang
      // it actually is.
      const t = new Transport({
        command: "sh",
        args: ["-c", "sleep 60 & wait"],
        env: {},
      });
      await t.spawn();
      const started = Date.now();
      const exit = await t.terminate();
      const elapsedMs = Date.now() - started;

      // On Unix, SIGTERM: signal === 'SIGTERM' and code === null
      expect(
        exit.signal === "SIGTERM" || (exit.code !== null && exit.code !== 0),
      ).toBe(true);
      // The real regression gate: bounded by the child's death, not the
      // orphan's. Generous vs. the ~250ms stdio drain grace so it cannot flake
      // on a loaded runner, but far below the 60s a regression would cost.
      expect(elapsedMs).toBeLessThan(5000);
    },
    15000,
  );
});

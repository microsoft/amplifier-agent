/**
 * Tests for Transport: subprocess spawn + NDJSON framing.
 *
 * RED: fails because wrappers/typescript/src/transport.ts does not exist yet.
 * GREEN: passes once Transport is implemented.
 *
 * TDD bullets:
 * (a) cat echo: send JSON frame, receive it back via onFrame callback
 * (b) non-JSON dropped: only valid JSON lines trigger onFrame
 * (c) terminate: kills sleep 60 with SIGTERM / non-zero exit
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
      const t = new Transport({
        command: "sh",
        args: ["-c", "sleep 60"],
        env: {},
      });
      await t.spawn();
      const exit = await t.terminate();

      // On Unix, SIGTERM: signal === 'SIGTERM' and code === null
      expect(
        exit.signal === "SIGTERM" || (exit.code !== null && exit.code !== 0),
      ).toBe(true);
    },
    5000,
  );
});

/**
 * Exit-gate driver — TypeScript.
 *
 * Spawns a real ``amplifier-agent`` subprocess via the published
 * ``amplifier-agent-client-ts`` wrapper, submits the sentinel prompt
 * ``'say hi'`` with a one-shot session, drains the event iterator looking for
 * a ``result/final`` event, then disposes the session.
 *
 * Output (stdout):
 *   A single JSON line: ``{"sawResultFinal": true | false}``
 *
 * Exit code:
 *   0 — at least one ``result/final`` event was observed (success).
 *   1 — no ``result/final`` event observed, or an error occurred.
 *
 * Usage (from wrappers/conformance/):
 *   pnpm exec tsx exit_gate_driver.ts
 */

import { spawnAgent } from "amplifier-agent-client-ts";

async function main(): Promise<void> {
  let sawResultFinal = false;

  const session = await spawnAgent({
    lifecycle: "one-shot",
    sessionId: "phase-2-2-gate-ts",
  });

  try {
    for await (const event of session.submit("say hi")) {
      if (event.type === "result/final") {
        sawResultFinal = true;
      }
    }
  } finally {
    await session.dispose();
  }

  // Write the report to stdout so the test can parse it.
  process.stdout.write(JSON.stringify({ sawResultFinal }) + "\n");

  // Exit 0 on success, 1 if no result/final was observed.
  process.exit(sawResultFinal ? 0 : 1);
}

void main().catch((err: unknown) => {
  const msg = err instanceof Error ? err.message : String(err);
  process.stderr.write(`exit_gate_driver error: ${msg}\n`);
  // Still emit a valid JSON report so the test can parse stdout even on error.
  process.stdout.write(JSON.stringify({ sawResultFinal: false }) + "\n");
  process.exit(1);
});

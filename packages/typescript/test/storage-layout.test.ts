import assert from "node:assert/strict";
import { mkdtemp, readdir, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, relative } from "node:path";
import { test } from "node:test";
import { stringify } from "lossless-json";
import { createAgent } from "@microsoft/amplifier-agent";
import type { TurnInput } from "@microsoft/amplifier-agent";

const selection = { provider: "anthropic", model: "claude-sonnet-5" };
const input: TurnInput = { content: [{ type: "text", text: "Say hello" }] };

function recordInput(script: unknown): TurnInput {
  return { content: [{ type: "text", text: "conformance-script:" + stringify(script) }] };
}

test("contract: durable sessions use the amplifier session layout with an observation capture", { timeout: 20_000 }, async () => {
  const folder = await mkdtemp(join(tmpdir(), "agent-session-layout-"));
  const secret = "tp_fixture_secret_00001";
  const reply = `The key is ${secret}`;
  const sessionDir = join(folder, "workspaces", "default", "sessions", "layout-session");
  const previous = process.env.AMPLIFIER_AGENT_WORKSPACE;
  delete process.env.AMPLIFIER_AGENT_WORKSPACE;
  try {
    let history: unknown[];
    {
      await using agent = await createAgent({ ...selection, storage: folder });
      const session = await agent.createSession({ sessionId: "layout-session" });
      const result = await session.run(recordInput([{ text: reply, chunks: [reply] }]));
      assert.equal(result.state, "success");
      history = session.history;
      await session.close();
    }
    const transcript = await readFile(join(sessionDir, "transcript.jsonl"), "utf8");
    assert.ok(transcript.includes(reply));
    assert.ok(JSON.parse(await readFile(join(sessionDir, "metadata.json"), "utf8")).session_id === "layout-session");
    const capture = await readFile(join(sessionDir, "context-intelligence", "events.jsonl"), "utf8");
    const events = capture.split("\n").filter((line) => line.trim()).map((line) => JSON.parse(line) as { event: string; data: { session_id: string } });
    assert.equal(events[0]?.event, "session:start");
    assert.ok(events.some((event) => event.event === "prompt:submit"));
    assert.ok(events.every((event) => event.data.session_id === "layout-session"));
    assert.ok(!capture.includes(secret));
    assert.ok(capture.includes("[REDACTED:SECRET]"));
    const found: string[] = [];
    const walk = async (directory: string): Promise<void> => {
      for (const entry of await readdir(directory, { withFileTypes: true })) {
        if (entry.isDirectory()) await walk(join(directory, entry.name));
        else if (entry.name === "transcript.jsonl") found.push(relative(folder, join(directory, entry.name)));
      }
    };
    await walk(folder);
    assert.deepEqual(found, [join("workspaces", "default", "sessions", "layout-session", "transcript.jsonl")]);
    await rm(join(sessionDir, "context-intelligence"), { recursive: true, force: true });
    {
      await using agent = await createAgent({ ...selection, storage: folder });
      assert.deepEqual((await agent.listSessions()).map((record) => record.session_id), ["layout-session"]);
      const resumed = await agent.resumeSession("layout-session");
      assert.deepEqual(resumed.history, history);
      assert.equal((await resumed.run(input)).state, "success");
      assert.equal(resumed.history.length, 2);
      await resumed.close();
    }
    const appended = (await readFile(join(sessionDir, "context-intelligence", "events.jsonl"), "utf8")).split("\n").filter((line) => line.trim()).map((line) => JSON.parse(line) as { event: string });
    assert.equal(appended[0]?.event, "session:resume");
  } finally {
    if (previous !== undefined) process.env.AMPLIFIER_AGENT_WORKSPACE = previous;
    await rm(folder, { recursive: true, force: true });
  }
});

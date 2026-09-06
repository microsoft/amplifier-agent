import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { AgentError, createAgent } from "@microsoft/amplifier-agent";
import type { Event, TurnInput } from "@microsoft/amplifier-agent";

const model = { provider: "anthropic", model: "claude-sonnet-5" };
const greeting: TurnInput = { content: [{ type: "text", text: "Another greeting" }] };
interface Scenario { id: string; persistence: "durable" | "ephemeral"; turns: TurnInput[] }
const scenarios: Scenario[] = JSON.parse(await readFile(
  process.env.CONFORMANCE_SESSION_SCENARIOS ?? new URL("../../../conformance/scenarios/sessions.json", import.meta.url), "utf8",
)) as Scenario[];

function named(code: string): (error: unknown) => boolean {
  return (error) => error instanceof AgentError && error.code === code && error.remedy.length > 0;
}

for (const scenario of scenarios) {
  test(`shared sessions: ${scenario.id}`, { timeout: 30_000 }, async () => {
    const storage = await mkdtemp(join(tmpdir(), "agent-sessions-"));
    const agent = await createAgent({ ...model, storage });
    try {
      const parent = await agent.createSession({ persistence: scenario.persistence });
      for (const input of scenario.turns) {
        const result = await parent.run(input);
        assert.equal(result.state, "success");
        assert.deepEqual(parent.history.at(-1)?.result, result);
      }
      assert.deepEqual(parent.history.map((turn) => turn.input), scenario.turns);
      const before = parent.history;
      const child = await parent.fork();
      assert.match(child.info.session_id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
      assert.notEqual(child.info.session_id, parent.info.session_id);
      assert.equal(child.info.persistence, scenario.persistence);
      assert.deepEqual(child.history, before);
      if (scenario.turns.length || scenario.persistence === "durable") {
        await assert.rejects(child.startTurn({ content: [], history: [{ role: "user", content: greeting.content }] }), named("invalid_input"));
      }
      const turn = await child.startTurn(greeting);
      const events: Event[] = [];
      for await (const event of turn.events()) events.push(event);
      assert.deepEqual(events.map((event) => event.sequence), events.map((_, index) => BigInt(index + 1)));
      assert.equal(events.filter((event) => event.type === "terminal").length, 1);
      assert.deepEqual(child.history.at(-1)?.result, events.at(-1)?.payload);
      assert.equal(child.history.length, before.length + 1);
      assert.deepEqual(parent.history, before);
      const grandchild = await child.fork();
      assert.deepEqual(grandchild.history, child.history);
      await grandchild.close();
      const childId = child.info.session_id;
      const childHistory = child.history;
      await child.close();
      assert.throws(() => child.info, named("closed"));
      assert.throws(() => child.history, named("closed"));
      if (scenario.persistence === "durable") {
        const resumed = await agent.resumeSession(childId);
        assert.deepEqual(resumed.history, childHistory);
        await resumed.close();
      } else await assert.rejects(agent.resumeSession(childId), named("not_found"));
      await parent.close();
    } finally {
      await agent.close();
      await rm(storage, { recursive: true, force: true });
    }
  });
}

test("durable ownership spans independent agents and releases on close", { timeout: 30_000 }, async () => {
  const storage = await mkdtemp(join(tmpdir(), "agent-ownership-"));
  const first = await createAgent({ ...model, storage });
  const second = await createAgent({ ...model, storage });
  try {
    const session = await first.createSession({ sessionId: "shared-session" });
    assert.equal(session.info.persistence, "durable");
    await assert.rejects(second.createSession({ sessionId: "shared-session" }), named("already_exists"));
    await assert.rejects(second.resumeSession("shared-session"), named("session_in_use"));
    await assert.rejects(second.deleteSession("shared-session"), named("session_in_use"));
    assert.ok((await second.listSessions()).some((record) => record.session_id === "shared-session"));
    const result = await session.run(greeting);
    const history = session.history;
    await session.close();
    const resumed = await second.resumeSession("shared-session");
    assert.deepEqual(resumed.history, history);
    assert.deepEqual(resumed.history[0]?.result, result);
    const turn = await resumed.startTurn(greeting);
    for await (const event of turn.events()) if (event.type === "turn_started") assert.equal(event.payload.continuation, "resumed");
    await resumed.close();
    await first.deleteSession("shared-session");
    await assert.rejects(second.resumeSession("shared-session"), named("not_found"));
    await assert.rejects(first.deleteSession("shared-session"), named("not_found"));
  } finally {
    await Promise.all([first.close(), second.close()]);
    await rm(storage, { recursive: true, force: true });
  }
});

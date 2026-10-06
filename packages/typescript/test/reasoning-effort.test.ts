import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import type { Event, TurnInput } from "amplifier-agent-ts";
import { createAgent } from "amplifier-agent-ts";
import { collect, named, trace } from "./trace.js";

const selection = { provider: "anthropic", model: "claude-sonnet-5" };

function observed(reasoningEffort?: string): TurnInput {
  const script = JSON.stringify([{ observe_request: true, text: "Done" }]);
  return { content: [{ type: "text", text: `scripted:${script}` }], reasoningEffort };
}

function startedEffort(events: Event[]): string | undefined {
  const started = events[0];
  assert.ok(started?.type === "turn_started");
  return started.payload.reasoning_effort;
}

function requestedEffort(events: Event[]): unknown {
  const request = events.find((event) => event.type === "org.example.request");
  assert.ok(request, "The scripted provider observes its request.");
  return (request.payload as { reasoning_effort?: unknown }).reasoning_effort;
}

test("reasoning effort: agent, session, and turn options refine the value primary work runs at", {
  timeout: 30_000,
}, async () => {
  await using agent = await createAgent({ ...selection, reasoningEffort: "high" });
  await using session = await agent.createSession({ persistence: "ephemeral", reasoningEffort: "medium" });
  const lowered = await collect(await session.startTurn(observed("low")));
  assert.equal(trace(lowered).state, "success");
  assert.equal(startedEffort(lowered), "low");
  assert.equal(requestedEffort(lowered), "low");
  const inherited = await collect(await session.startTurn(observed()));
  assert.equal(trace(inherited).state, "success");
  assert.equal(startedEffort(inherited), "medium");
  assert.equal(session.history[0]?.input.reasoningEffort, "low");
});

test("reasoning effort: absent everywhere, turn_started reports medium", {
  timeout: 30_000,
}, async () => {
  await using agent = await createAgent(selection);
  await using session = await agent.createSession({ persistence: "ephemeral" });
  const events = await collect(await session.startTurn(observed()));
  assert.equal(trace(events).state, "success");
  assert.equal(startedEffort(events), "medium");
  assert.equal(requestedEffort(events), "medium");
});

test("reasoning effort: a model that takes none omits turn_started.reasoning_effort", {
  timeout: 30_000,
}, async () => {
  await using agent = await createAgent({ provider: "openai", model: "gpt-4.1", reasoningEffort: "high" });
  await using session = await agent.createSession({ persistence: "ephemeral" });
  const events = await collect(await session.startTurn(observed()));
  assert.equal(trace(events).state, "success");
  const started = events[0];
  assert.ok(started?.type === "turn_started");
  assert.equal(Object.hasOwn(started.payload, "reasoning_effort"), false);
});

test("reasoning effort: unregistered values are invalid_input at the method that received them", {
  timeout: 30_000,
}, async () => {
  for (const value of ["High", " low", "", "extreme"]) {
    await assert.rejects(createAgent({ ...selection, reasoningEffort: value }), named("invalid_input"));
    await using agent = await createAgent(selection);
    await assert.rejects(
      agent.createSession({ persistence: "ephemeral", reasoningEffort: value }),
      named("invalid_input"),
    );
    await using session = await agent.createSession({ persistence: "ephemeral" });
    await assert.rejects(session.startTurn(observed(value)), named("invalid_input"));
  }
});

test("reasoning effort: values above the agent's are honored", {
  timeout: 30_000,
}, async () => {
  await using agent = await createAgent({ ...selection, reasoningEffort: "low" });
  await using higher = await agent.createSession({ persistence: "ephemeral", reasoningEffort: "high" });
  const atSession = await collect(await higher.startTurn(observed()));
  assert.equal(trace(atSession).state, "success");
  assert.equal(startedEffort(atSession), "high");
  assert.equal(requestedEffort(atSession), "high");
  await using session = await agent.createSession({ persistence: "ephemeral" });
  const atTurn = await collect(await session.startTurn(observed("medium")));
  assert.equal(trace(atTurn).state, "success");
  assert.equal(startedEffort(atTurn), "medium");
  assert.equal(requestedEffort(atTurn), "medium");
});

test("reasoning effort: AMPLIFIER_AGENT_REASONING_EFFORT in the Node process environment reaches the engine", {
  timeout: 30_000,
}, async () => {
  const folder = await mkdtemp(join(tmpdir(), "agent-reasoning-effort-"));
  const path = join(folder, "host.json");
  const keys = ["AMPLIFIER_AGENT_CONFIG", "AMPLIFIER_AGENT_REASONING_EFFORT"];
  const previous = Object.fromEntries(keys.map((key) => [key, process.env[key]]));
  try {
    await writeFile(path, "{}");
    process.env.AMPLIFIER_AGENT_CONFIG = path;
    process.env.AMPLIFIER_AGENT_REASONING_EFFORT = "low";
    await using agent = await createAgent(selection);
    await using session = await agent.createSession({ persistence: "ephemeral" });
    const events = await collect(await session.startTurn(observed()));
    assert.equal(startedEffort(events), "low");
  } finally {
    for (const key of keys) {
      const value = previous[key];
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
    await rm(folder, { recursive: true, force: true });
  }
});

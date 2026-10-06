import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import type { Event } from "amplifier-agent-ts";
import { AgentError, createAgent } from "amplifier-agent-ts";
import { collect, named, trace } from "./trace.js";

const selection = { provider: "anthropic", model: "claude-sonnet-5" };
const prompt = { content: [{ type: "text" as const, text: "Reply" }] };

function started(events: Event[]): { provider: string; model: string; reasoningEffort: string | undefined } {
  const first = events[0];
  assert.ok(first?.type === "turn_started");
  const { provider, model } = first.payload.primary_actual;
  return { provider, model, reasoningEffort: first.payload.reasoning_effort };
}

test("setModel: the next turn runs on the new selection and info reports it", {
  timeout: 30_000,
}, async () => {
  await using agent = await createAgent(selection);
  await using session = await agent.createSession({ persistence: "ephemeral" });
  assert.equal(typeof session.setModel, "function");
  assert.deepEqual([session.info.provider, session.info.model], ["anthropic", "claude-sonnet-5"]);
  const first = await collect(await session.startTurn(prompt));
  assert.equal(trace(first).state, "success");
  await session.setModel({ provider: "openai", model: "gpt-6-sol", reasoningEffort: "low" });
  assert.deepEqual(
    [session.info.provider, session.info.model, session.info.reasoning_effort],
    ["openai", "gpt-6-sol", "low"],
  );
  const second = await collect(await session.startTurn(prompt));
  assert.equal(trace(second).state, "success");
  assert.deepEqual(started(first), { provider: "anthropic", model: "claude-sonnet-5", reasoningEffort: "medium" });
  assert.deepEqual(started(second), { provider: "openai", model: "gpt-6-sol", reasoningEffort: "low" });
});

test("setModel: errors cross as AgentError and leave the session unchanged", {
  timeout: 30_000,
}, async () => {
  await using agent = await createAgent(selection);
  const session = await agent.createSession({ persistence: "ephemeral" });
  await assert.rejects(session.setModel({ provider: "not-a-provider", model: "x" }), named("invalid_input"));
  assert.deepEqual([session.info.provider, session.info.model], ["anthropic", "claude-sonnet-5"]);
  await session.close();
  await assert.rejects(session.setModel({ provider: "openai", model: "gpt-6-sol" }), (error: unknown) => {
    assert.ok(error instanceof AgentError);
    assert.equal(error.code, "closed");
    return true;
  });
});

test("setModel: listed and resumed sessions carry the saved selection", {
  timeout: 30_000,
}, async () => {
  const folder = await mkdtemp(join(tmpdir(), "agent-set-model-"));
  try {
    {
      await using agent = await createAgent({ ...selection, sessionsDirectory: folder });
      await using session = await agent.createSession({ sessionId: "switch-typescript" });
      await session.setModel({ provider: "openai", model: "gpt-6-sol" });
      assert.equal(trace(await collect(await session.startTurn(prompt))).state, "success");
    }
    await using agent = await createAgent({ ...selection, sessionsDirectory: folder });
    const [record] = await agent.listSessions();
    assert.deepEqual(
      [record?.session_id, record?.provider, record?.model],
      ["switch-typescript", "openai", "gpt-6-sol"],
    );
    await using resumed = await agent.resumeSession("switch-typescript");
    const events = await collect(await resumed.startTurn(prompt));
    assert.equal(trace(events).state, "success");
    assert.deepEqual([started(events).provider, started(events).model], ["openai", "gpt-6-sol"]);
  } finally {
    await rm(folder, { recursive: true, force: true });
  }
});

test("setModel: a fork inherits the parent's current selection", {
  timeout: 30_000,
}, async () => {
  await using agent = await createAgent(selection);
  await using parent = await agent.createSession({ persistence: "ephemeral" });
  assert.equal(trace(await collect(await parent.startTurn(prompt))).state, "success");
  await parent.setModel({ provider: "openai", model: "gpt-6-sol" });
  await using child = await parent.fork();
  assert.deepEqual([child.info.provider, child.info.model], ["openai", "gpt-6-sol"]);
});

import assert from "node:assert/strict";
import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import { test } from "node:test";
import { listModels, listProviders } from "amplifier-agent-ts";
import { named } from "./trace.js";

const limits = { timeout: 30_000 };
/** A PATH without the GitHub CLI, so a developer's own login never reaches the result. */
const noGitHubCli = { PATH: "/nonexistent" };
const secret = "sentinel-credential-value";
const providerFields = ["credential_variables", "credentials", "display_name", "installed", "provider"];
const modelFields = new Set(["id", "display_name", "context_window", "max_output_tokens"]);

/** Serves the Ollama model listing on a loopback port. */
async function ollama(models: Record<string, unknown>[]): Promise<{ url: string; server: Server }> {
  const server = createServer((request, response) => {
    if (request.url === "/api/tags") {
      response.writeHead(200, { "content-type": "application/json" });
      response.end(JSON.stringify({ models }));
      return;
    }
    response.writeHead(404);
    response.end();
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  return { url: `http://127.0.0.1:${(server.address() as AddressInfo).port}`, server };
}

async function closed(server: Server): Promise<void> {
  await new Promise<void>((resolve) => server.close(() => resolve()));
}

test("discovery: listProviders and listModels are exported functions", limits, () => {
  assert.equal(typeof listProviders, "function");
  assert.equal(typeof listModels, "function");
});

test("discovery: provider records keep contract field names and carry no values", limits, async () => {
  const before = process.env.ANTHROPIC_API_KEY;
  const listed = await listProviders({ environment: { ANTHROPIC_API_KEY: secret, ...noGitHubCli } });
  assert.equal(listed.length, 9);
  for (const record of listed) assert.deepEqual(Object.keys(record).sort(), providerFields);
  const anthropic = listed.find((record) => record.provider === "anthropic");
  assert.equal(anthropic?.credentials, "found");
  assert.ok(anthropic?.credential_variables.includes("ANTHROPIC_API_KEY"));
  assert.ok(!JSON.stringify(listed).includes(secret));
  assert.equal(process.env.ANTHROPIC_API_KEY, before);
  const again = await listProviders({ environment: noGitHubCli });
  assert.deepEqual(
    again.map((record) => record.provider),
    listed.map((record) => record.provider),
  );
});

test("discovery: model records keep contract field names", limits, async () => {
  const { url, server } = await ollama([{ name: "fixture-model", model: "fixture-model" }]);
  try {
    const listed = await listModels("ollama", { environment: { OLLAMA_HOST: url } });
    assert.deepEqual(
      listed.map((record) => [record.id, record.display_name]),
      [["fixture-model", "fixture-model"]],
    );
    for (const record of listed) {
      for (const key of Object.keys(record)) assert.ok(modelFields.has(key), key);
      if (record.context_window !== undefined) assert.equal(typeof record.context_window, "number");
    }
  } finally {
    await closed(server);
  }
});

test("discovery: an unreachable provider fails provider_failed", limits, async () => {
  const { url, server } = await ollama([]);
  await closed(server);
  await assert.rejects(listModels("ollama", { environment: { OLLAMA_HOST: url } }), named("provider_failed"));
});

test("discovery: an unknown provider fails invalid_input", limits, async () => {
  await assert.rejects(listModels("not-a-provider"), named("invalid_input"));
});

test("discovery: model records list reasoning_efforts, absent where the engine cannot know", limits, async () => {
  const server = createServer((request, response) => {
    if (request.url === "/v1/models") {
      response.writeHead(200, { "content-type": "application/json" });
      const data = ["gpt-5.5-pro", "gpt-6-astra"].map((id) => ({
        id,
        object: "model",
        created: 0,
        owned_by: "fixture",
      }));
      response.end(JSON.stringify({ object: "list", data }));
      return;
    }
    response.writeHead(404);
    response.end();
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const url = `http://127.0.0.1:${(server.address() as AddressInfo).port}/v1`;
  try {
    const listed = await listModels("openai", { environment: { OPENAI_API_KEY: "fixture-key", OPENAI_BASE_URL: url } });
    assert.deepEqual(Object.fromEntries(listed.map((record) => [record.id, record.reasoning_efforts])), {
      "gpt-5.5-pro": ["medium", "high", "xhigh"],
      "gpt-6-astra": ["low", "medium", "high", "xhigh", "max"],
    });
  } finally {
    await closed(server);
  }
  const local = await ollama([{ name: "fixture-model", model: "fixture-model" }]);
  try {
    const listed = await listModels("ollama", { environment: { OLLAMA_HOST: local.url } });
    assert.equal(Object.hasOwn(listed[0]!, "reasoning_efforts"), false);
  } finally {
    await closed(local.server);
  }
});

import assert from "node:assert/strict";
import { mkdir, mkdtemp, readdir, readFile, realpath, rm, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import type { AgentOptions, TurnInput } from "amplifier-agent-ts";
import { createAgent } from "amplifier-agent-ts";
import { collect, named, trace } from "./trace.js";

const selection = { provider: "anthropic", model: "claude-sonnet-5", approvals: "allow" } as const;
const scripted = (steps: Record<string, unknown>[]): TurnInput => ({
  content: [{ type: "text", text: `scripted:${JSON.stringify(steps)}` }],
});
const call = (name: string, args: Record<string, unknown>) => ({ tool: { name, arguments: args } });
const locate = scripted([
  call("bash", { command: "pwd > where" }),
  call("write_file", { file_path: "note.txt", content: "relative" }),
  { text: "Done" },
]);

interface Folders {
  root: string;
  host: string;
  project: string;
}

/** Runs a body with a fresh HOME and process directory, restoring both afterwards. */
async function isolated(body: (folders: Folders) => Promise<void>): Promise<void> {
  const root = await realpath(await mkdtemp(join(tmpdir(), "agent-directories-")));
  const host = join(root, "host");
  const project = join(root, "project");
  await mkdir(host);
  await mkdir(project);
  await mkdir(join(root, "home"));
  const previous = { cwd: process.cwd(), home: process.env.HOME };
  process.env.HOME = join(root, "home");
  process.chdir(host);
  try {
    await body({ root, host, project });
  } finally {
    process.chdir(previous.cwd);
    if (previous.home === undefined) delete process.env.HOME;
    else process.env.HOME = previous.home;
    await rm(root, { recursive: true, force: true });
  }
}

async function run(options: AgentOptions, input: TurnInput = locate) {
  await using agent = await createAgent(options);
  await using session = await agent.createSession({ persistence: "ephemeral" });
  return trace(await collect(await session.startTurn(input)));
}

/** Asserts construction is refused, closing any agent that was built instead. */
async function refused(options: AgentOptions, ...patterns: string[]): Promise<void> {
  let built: Awaited<ReturnType<typeof createAgent>> | undefined;
  try {
    built = await createAgent(options);
  } catch (error) {
    named("invalid_input")(error);
    for (const pattern of patterns) assert.ok((error as Error).message.includes(pattern), (error as Error).message);
    return;
  }
  await built.close();
  assert.fail("Construction was accepted.");
}

async function landedIn(directory: string): Promise<void> {
  assert.equal((await readFile(join(directory, "where"), "utf8")).trim(), directory);
  assert.equal(await readFile(join(directory, "note.txt"), "utf8"), "relative");
}

test(
  "directories: the default working directory is the Node process directory at construction",
  {
    timeout: 20_000,
  },
  () =>
    isolated(async ({ host, project }) => {
      await using agent = await createAgent({ ...selection });
      process.chdir(project);
      await using session = await agent.createSession({ persistence: "ephemeral" });
      assert.equal(trace(await collect(await session.startTurn(locate))).state, "success");
      await landedIn(host);
      assert.deepEqual(await readdir(project), []);
    }),
);

test(
  "directories: agents built from different process directories work in their own",
  {
    timeout: 20_000,
  },
  () =>
    isolated(async ({ host, project }) => {
      await using first = await createAgent({ ...selection });
      process.chdir(project);
      await using second = await createAgent({ ...selection });
      for (const agent of [first, second]) {
        await using session = await agent.createSession({ persistence: "ephemeral" });
        assert.equal(trace(await collect(await session.startTurn(locate))).state, "success");
      }
      await landedIn(host);
      await landedIn(project);
    }),
);

test("directories: workingDirectory directs built-in tools whatever the process directory", { timeout: 20_000 }, () =>
  isolated(async ({ host, project }) => {
    assert.equal((await run({ ...selection, workingDirectory: project } as AgentOptions)).state, "success");
    await landedIn(project);
    assert.deepEqual(await readdir(host), []);
  }),
);

test("directories: a relative workingDirectory resolves against the Node process directory", { timeout: 20_000 }, () =>
  isolated(async ({ host }) => {
    await mkdir(join(host, "nested"));
    assert.equal((await run({ ...selection, workingDirectory: "nested" } as AgentOptions)).state, "success");
    await landedIn(join(host, "nested"));
  }),
);

test("directories: a workingDirectory that is not a directory is refused", { timeout: 20_000 }, () =>
  isolated(async ({ root }) => {
    await (await createAgent({ ...selection, workingDirectory: root } as AgentOptions)).close();
    const missing = join(root, "missing");
    await refused({ ...selection, workingDirectory: missing } as AgentOptions, missing);
  }),
);

test("directories: additionalDirectories accept writes that other directories refuse", { timeout: 20_000 }, () =>
  isolated(async ({ root, project }) => {
    const shared = join(root, "shared");
    const outside = join(root, "outside");
    await mkdir(shared);
    await mkdir(outside);
    const input = scripted([
      call("write_file", { file_path: join(shared, "shared.txt"), content: "shared" }),
      call("write_file", { file_path: join(outside, "outside.txt"), content: "outside" }),
      { text: "Done" },
    ]);
    const options = {
      ...selection,
      workingDirectory: project,
      additionalDirectories: [shared],
      toolErrorPolicy: "continue",
    } as AgentOptions;
    await run(options, input);
    assert.equal(await readFile(join(shared, "shared.txt"), "utf8"), "shared");
    await assert.rejects(stat(join(outside, "outside.txt")));
  }),
);

test("directories: sessionsDirectory holds durable sessions in the contracted layout", { timeout: 20_000 }, () =>
  isolated(async ({ root, host }) => {
    const sessions = join(root, "sessions-directory");
    const options = { ...selection, sessionsDirectory: sessions } as AgentOptions;
    await using agent = await createAgent(options);
    const session = await agent.createSession({ sessionId: "kept-session" });
    assert.equal((await session.run({ content: [{ type: "text", text: "Say hello" }] })).state, "success");
    await session.close();
    assert.ok((await stat(join(sessions, "sessions", "kept-session", "transcript.jsonl"))).isFile());
    assert.deepEqual(await readdir(join(root, "home")), []);
    assert.deepEqual(await readdir(host), []);
  }),
);

test("directories: the default sessions directory is named by the working directory slug", { timeout: 20_000 }, () =>
  isolated(async ({ root, host }) => {
    await using agent = await createAgent({ ...selection });
    const session = await agent.createSession({ sessionId: "slugged-session" });
    assert.equal((await session.run({ content: [{ type: "text", text: "Say hello" }] })).state, "success");
    await session.close();
    const slug = host.replaceAll("/", "-");
    const projects = join(root, "home", ".amplifier-agent", "projects");
    assert.deepEqual(await readdir(projects), [slug]);
    assert.ok((await stat(join(projects, slug, "sessions", "slugged-session", "transcript.jsonl"))).isFile());
  }),
);

test("directories: environment reaches bash and never changes process.env", { timeout: 20_000 }, () =>
  isolated(async ({ host }) => {
    const before = { ...process.env };
    const input = scripted([call("bash", { command: 'printf %s "$AGENT_MARKER" > seen' }), { text: "Done" }]);
    const options = { ...selection, environment: { AGENT_MARKER: "agent" } } as AgentOptions;
    assert.equal((await run(options, input)).state, "success");
    assert.equal(await readFile(join(host, "seen"), "utf8"), "agent");
    assert.deepEqual({ ...process.env }, before);
  }),
);

test("directories: an environment entry with an invalid name is refused", { timeout: 20_000 }, () =>
  isolated(async () => {
    await (await createAgent({ ...selection, environment: { AGENT_MARKER: "valid" } } as AgentOptions)).close();
    for (const environment of [{ "": "value" }, { "A=B": "value" }, { NUMBER: 1 }]) {
      await refused({ ...selection, environment } as unknown as AgentOptions, "environment");
    }
  }),
);

test("directories: storage is refused as an unregistered option", { timeout: 20_000 }, () =>
  isolated(async ({ root }) => {
    const storage = join(root, "legacy");
    await refused({ ...selection, storage } as AgentOptions, "storage");
    await assert.rejects(stat(storage));
  }),
);

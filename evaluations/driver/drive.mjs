// Run one segment of a task's turn plan against the installed @microsoft/amplifier-agent.
// Writes the same env.json, result.json and events.jsonl as drive.py. See ../README.md for the task format.

import { appendFileSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import path from "node:path";
import { parseArgs } from "node:util";

import { AgentError, BUILTIN_TOOLS, contractVersions, createAgent, version } from "@microsoft/amplifier-agent";

const DRAIN_SECONDS = 30;

const now = () => new Date().toISOString();

// Library records carry bigint counts; JSON has no bigint, so write them as numbers like drive.py does.
function plain(value) {
  if (typeof value === "bigint") return Number.isSafeInteger(Number(value)) ? Number(value) : value.toString();
  if (value instanceof AgentError) return { type: value.name, message: value.message, ...plain({ ...value }) };
  if (value instanceof Error) return { type: value.name, str: String(value) };
  if (Array.isArray(value)) return value.map(plain);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, plain(item)]));
  }
  return value ?? null;
}

function errorRecord(err) {
  if (err === undefined || err === null) return null;
  return {
    code: err.code ?? null,
    message: err.message ?? String(err),
    remedy: err.remedy ?? null,
    category: err.category ?? null,
    retryable: err.retryable ?? null,
    details: plain(err.details ?? null),
  };
}

function splitSegments(turns) {
  const segments = [[]];
  for (const turn of turns) {
    if (turn.restart) segments.push([]);
    else segments.at(-1).push(turn);
  }
  return segments;
}

function writeJsonAtomic(file, data) {
  const tmp = `${file}.tmp`;
  writeFileSync(tmp, `${JSON.stringify(data, null, 2)}\n`, "utf8");
  renameSync(tmp, file);
}

function buildOptions(task) {
  const agent = task.agent ?? {};
  for (const [key, value] of Object.entries(task.env ?? {})) process.env[key] = String(value);
  if (task.host) throw new Error(`host ${task.host}: driver/hosts/ modules are Python and cannot load here`);
  // The binding refuses undefined values as non-JSON, so only set what the task gives.
  const options = {};
  if (agent.provider) options.provider = agent.provider;
  if (agent.model) options.model = agent.model;
  const tools = task.tools ?? "all";
  if (tools !== "all") options.tools = [...tools];
  if (task.skills?.length) options.skills = task.skills;
  const approvals = task.approvals ?? "none";
  if (approvals === "allow" || approvals === "deny") options.approvals = approvals;
  else if (approvals !== "none") throw new Error(`unknown approvals value: ${JSON.stringify(approvals)}`);
  const extra = task.agent_options ?? {};
  if ("tool_error_policy" in extra) options.toolErrorPolicy = extra.tool_error_policy;
  if ("tool_result_max_bytes" in extra) options.toolResultMaxBytes = extra.tool_result_max_bytes;
  return options;
}

function turnInput(spec) {
  const user = spec.user ?? "";
  const input = { content: user ? [{ type: "text", text: user }] : [] };
  if (spec.history) {
    input.history = spec.history.map((m) => ({ role: m.role, content: [{ type: "text", text: m.content }] }));
  }
  return input;
}

async function consume(turn, spec, record, events) {
  for await (const event of turn.events()) {
    const envelope = {
      contract_version: event.contract_version,
      session_id: event.session_id,
      turn_id: event.turn_id,
      sequence: plain(event.sequence),
      at: event.at ?? null,
      type: event.type,
      payload: plain(event.payload),
    };
    events(`${JSON.stringify(envelope)}\n`);
    record.event_count += 1;
    if (event.type === "terminal") {
      const result = event.payload;
      record.state = result.state;
      record.content = (result.content ?? []).map((part) => part.text).join("");
      record.error = errorRecord(result.error);
      record.usage = plain(result.usage ?? null);
    }
  }
  if (spec.consume_twice) {
    try {
      for await (const _ of turn.events()) break;
      record.second_events_error = null;
    } catch (err) {
      record.second_events_error = { type: err?.name ?? typeof err, code: err?.code ?? null };
    }
  }
}

async function runTurn(session, spec, record, events, active) {
  Object.assign(record, {
    state: null,
    content: null,
    error: null,
    usage: null,
    started_at: now(),
    ended_at: null,
    event_count: 0,
  });
  try {
    const turn = await session.startTurn(turnInput(spec));
    record.turn_id = turn.info.turn_id;
    active.turn = turn;
    active.consumer = consume(turn, spec, record, events);
    active.record = record;
    await active.consumer;
  } finally {
    active.turn = null;
    record.ended_at = now();
  }
}

async function runSegment(task, segment, segRecord, events, active) {
  const segments = splitSegments(task.turns ?? []);
  if (segment >= segments.length) throw new Error(`segment ${segment} out of range: task has ${segments.length}`);
  const firstIndex = segments.slice(0, segment).reduce((total, s) => total + s.length, 0);
  const sessionSpec = task.session ?? {};
  const agent = await createAgent(buildOptions(task));
  active.agent = agent;
  try {
    const sessionOptions = { persistence: sessionSpec.persistence ?? "durable" };
    if (sessionSpec.session_id) sessionOptions.sessionId = sessionSpec.session_id;
    const session =
      segment === 0 && !sessionSpec.resume
        ? await agent.createSession(sessionOptions)
        : await agent.resumeSession(sessionSpec.session_id);
    try {
      for (const [offset, spec] of segments[segment].entries()) {
        if ("tools" in spec) console.log(`turn ${firstIndex + offset}: per-turn tools ignored, not supported by the API`);
        const record = { index: firstIndex + offset };
        segRecord.turns.push(record);
        await runTurn(session, spec, record, events, active);
      }
    } finally {
      await session.close();
    }
  } finally {
    await agent.close();
  }
}

class SegmentTimeout extends Error {}

function withTimeout(work, seconds, onTimeout) {
  let timer;
  const limit = new Promise((_, reject) => {
    timer = setTimeout(() => {
      onTimeout().finally(() => reject(new SegmentTimeout()));
    }, seconds * 1000);
  });
  return Promise.race([work, limit]).finally(() => clearTimeout(timer));
}

async function drain(active) {
  const turn = active.turn;
  if (!turn) return;
  try {
    await turn.cancel();
    await withTimeout(active.consumer, DRAIN_SECONDS, async () => {});
  } catch (err) {
    active.record.drain_error = `${err?.name ?? "Error"}: ${err?.message ?? err}`;
  }
}

async function main() {
  const { values } = parseArgs({
    options: {
      task: { type: "string" },
      out: { type: "string" },
      segment: { type: "string" },
      cwd: { type: "string", default: "/workspace" },
    },
  });
  if (!values.task || !values.out || values.segment === undefined) {
    console.error("usage: drive.mjs --task FILE --out DIR --segment N [--cwd DIR]");
    return 2;
  }
  const out = path.resolve(values.out);
  const segment = Number.parseInt(values.segment, 10);
  let task;
  try {
    task = JSON.parse(readFileSync(values.task, "utf8"));
  } catch (err) {
    console.error(`cannot read task ${values.task}: ${err}`);
    return 2;
  }

  mkdirSync(out, { recursive: true });
  const logFile = path.join(out, "driver.log");
  for (const name of ["log", "error"]) {
    const original = console[name].bind(console);
    console[name] = (...args) => {
      original(...args);
      appendFileSync(logFile, `${args.map(String).join(" ")}\n`);
    };
  }
  console.log(`--- segment ${segment} pid ${process.pid} at ${now()}`);
  process.chdir(values.cwd);

  writeJsonAtomic(path.join(out, "env.json"), {
    pid: process.pid,
    segment,
    node: process.versions.node,
    amplifier_agent: version,
    contract_versions: [...contractVersions],
  });

  const resultPath = path.join(out, "result.json");
  let result;
  try {
    result = JSON.parse(readFileSync(resultPath, "utf8"));
  } catch {
    result = { segments: [], host: null, driver_error: null };
  }
  const segRecord = { segment, pid: process.pid, started_at: now(), ended_at: null, turns: [] };
  result.segments.push(segRecord);

  const eventsPath = path.join(out, "events.jsonl");
  const events = (line) => appendFileSync(eventsPath, line);
  const active = { agent: null, turn: null, consumer: null, record: null };
  const timeout = task.timeout_seconds ?? 300;
  let exitCode = 0;
  try {
    await withTimeout(runSegment(task, segment, segRecord, events, active), timeout, () => drain(active));
  } catch (err) {
    if (err instanceof SegmentTimeout) {
      result.driver_error = {
        type: "TimeoutError",
        code: "segment_timeout",
        message: `segment ${segment} exceeded ${timeout}s`,
        remedy: "Raise timeout_seconds or investigate the stalled turn.",
        traceback: err.stack ?? null,
      };
    } else {
      result.driver_error = {
        type: err?.name ?? typeof err,
        code: err?.code ?? null,
        message: err?.message ?? String(err),
        remedy: err?.remedy ?? null,
        traceback: err?.stack ?? null,
      };
      if (err instanceof AgentError) {
        console.error(`driver_error ${err.code}: ${err.message}`);
      } else {
        exitCode = 2;
        console.error(err?.stack ?? String(err));
      }
    }
  }

  segRecord.ended_at = now();
  writeJsonAtomic(resultPath, result);
  console.log(`--- segment ${segment} done, driver_error=${result.driver_error?.code ?? null}`);
  if (result.driver_error?.code === "segment_timeout") await active.agent?.close().catch(() => {});
  return exitCode;
}

process.exitCode = await main();
// A timed-out segment can leave engine work behind; exit instead of waiting on it.
process.exit(process.exitCode);

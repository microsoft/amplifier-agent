import { parse, stringify } from "lossless-json";
import { AgentError } from "../errors.js";
import type { Event, TurnInput, TurnRecord, TurnResult, Usage } from "../records.js";

export function decode(text: string): unknown {
  return parse(text, undefined, (value) => {
    if (/^-?\d+$/.test(value)) {
      const integer = BigInt(value);
      return integer > BigInt(Number.MAX_SAFE_INTEGER) || integer < BigInt(Number.MIN_SAFE_INTEGER)
        ? integer
        : Number(value);
    }
    const number = Number(value);
    if (!Number.isFinite(number)) throw new SyntaxError("Non-finite JSON number");
    return number;
  });
}

export function encode(value: unknown): string {
  assertJson(value, "$", new Set());
  const encoded = stringify(value);
  if (encoded === undefined)
    throw invalid("$", "a value with no JSON form", "Pass a string, number, boolean, null, array, or plain object.");
  return encoded;
}

// Keys set to undefined mean "not given" in an options record, as omission does.
// Anything but a plain object passes through untouched for the strict JSON check to judge.
export function defined<T>(record: T): T {
  if (!record || typeof record !== "object" || Object.getPrototypeOf(record) !== Object.prototype) return record;
  const copy = { ...record } as Record<string, unknown>;
  for (const key of Object.keys(copy)) if (copy[key] === undefined) delete copy[key];
  return copy as T;
}

function invalid(path: string, found: string, remedy: string): AgentError {
  return new AgentError({
    code: "invalid_input",
    category: "input",
    message: `${path} is ${found}, which is not strict JSON.`,
    remedy,
    retryable: false,
  });
}

function assertJson(value: unknown, path: string, parents: Set<object>): void {
  if (value === null || typeof value === "string" || typeof value === "boolean" || typeof value === "bigint") return;
  if (typeof value === "number") {
    if (!Number.isFinite(value))
      throw invalid(path, `the non-finite number ${value}`, `Replace the value at ${path} with a finite number.`);
    if (Number.isInteger(value) && !Number.isSafeInteger(value))
      throw invalid(
        path,
        "an integer beyond Number.MAX_SAFE_INTEGER",
        `Pass the integer at ${path} as a bigint to keep it exact.`,
      );
    return;
  }
  if (value === undefined) throw invalid(path, "undefined", `Remove ${path} or give it a JSON value such as null.`);
  if (typeof value !== "object")
    throw invalid(
      path,
      `a ${typeof value}`,
      `Replace the value at ${path} with a string, number, boolean, null, array, or plain object.`,
    );
  if (parents.has(value))
    throw invalid(path, "a circular reference", `Remove the reference at ${path} to an object that contains it.`);
  const prototype = Object.getPrototypeOf(value);
  if (!Array.isArray(value) && prototype !== Object.prototype && prototype !== null)
    throw invalid(
      path,
      `an instance of ${prototype?.constructor?.name || "a class"}`,
      `Convert the value at ${path} to a plain object, array, or string.`,
    );
  if (Object.getOwnPropertySymbols(value).length)
    throw invalid(path, "an object with symbol keys", `Remove the symbol-keyed properties from ${path}.`);
  parents.add(value);
  if (Array.isArray(value)) {
    for (let index = 0; index < value.length; index++) assertJson(value[index], `${path}[${index}]`, parents);
  } else {
    for (const [key, entry] of Object.entries(value)) assertJson(entry, `${path}.${key}`, parents);
  }
  parents.delete(value);
}

export function snapshot<T>(value: T): T {
  return decode(encode(value)) as T;
}

export function freeze<T>(value: T): T {
  if (value && typeof value === "object") {
    for (const entry of Object.values(value)) freeze(entry);
    Object.freeze(value);
  }
  return value;
}

export function receiveError(value: unknown): AgentError {
  if (!value || typeof value !== "object") throw new SyntaxError("Expected an error record");
  const record = value as Record<string, unknown>;
  for (const key of ["code", "category", "message", "remedy"]) {
    if (typeof record[key] !== "string") throw new SyntaxError(`Invalid error ${key}`);
  }
  if (typeof record.retryable !== "boolean") throw new SyntaxError("Invalid error retryable");
  return new AgentError(record as unknown as ConstructorParameters<typeof AgentError>[0]);
}

function receiveUsage(usage: Usage): Usage {
  for (const entry of usage.entries) {
    for (const key of ["tokens_in", "tokens_out", "cache_read_tokens", "cache_write_tokens"] as const) {
      if (entry[key] !== undefined) entry[key] = BigInt(entry[key]);
    }
  }
  return usage;
}

export function receiveResult(value: TurnResult): TurnResult {
  if (value.error !== undefined) value.error = receiveError(value.error);
  if (value.usage !== undefined) receiveUsage(value.usage);
  return value;
}

// An image part's mediaType is media_type at the engine. Only that key is respelled;
// every other shape passes through for the engine's validation to judge.
const plain = (value: unknown): value is Record<string, unknown> =>
  !!value &&
  typeof value === "object" &&
  [Object.prototype, null].includes(Object.getPrototypeOf(value) as object | null);

function respellParts(value: unknown, from: string, to: string): unknown {
  const part = (item: unknown): unknown => {
    if (!plain(item) || item.type !== "image" || !Object.hasOwn(item, from)) return item;
    const { [from]: moved, ...rest } = item;
    return { ...rest, [to]: moved };
  };
  return Array.isArray(value) ? value.map(part) : value;
}

function respellImages(input: unknown, from: string, to: string): unknown {
  if (!plain(input)) return input;
  const output: Record<string, unknown> = { ...input, content: respellParts(input.content, from, to) };
  if (!Object.hasOwn(input, "content")) delete output.content;
  if (Array.isArray(input.history))
    output.history = input.history.map((message) =>
      plain(message) && Object.hasOwn(message, "content")
        ? { ...message, content: respellParts(message.content, from, to) }
        : message,
    );
  return output;
}

export function sendToolResult(result: unknown): unknown {
  return respellParts(result, "mediaType", "media_type");
}

// TurnInput.reasoningEffort is reasoning_effort at the engine.
function respellKey(value: unknown, from: string, to: string): unknown {
  if (!plain(value) || !Object.hasOwn(value, from)) return value;
  const { [from]: moved, ...rest } = value;
  return { ...rest, [to]: moved };
}

export function sendInput(input: TurnInput): unknown {
  return respellKey(respellImages(defined(input), "mediaType", "media_type"), "reasoningEffort", "reasoning_effort");
}

export function receiveHistory(value: TurnRecord[]): TurnRecord[] {
  for (const record of value) {
    record.input = respellKey(
      respellImages(record.input, "media_type", "mediaType"),
      "reasoning_effort",
      "reasoningEffort",
    ) as TurnInput;
    receiveResult(record.result);
  }
  return freeze(value);
}

export function receiveEvent(value: Event): Event {
  value.sequence = BigInt(value.sequence);
  switch (value.type) {
    case "terminal":
      receiveResult(value.payload);
      break;
    case "usage":
      receiveUsage(value.payload.snapshot);
      break;
    case "tool_result":
      if (value.payload.resolution.error) value.payload.resolution.error = receiveError(value.payload.resolution.error);
      break;
  }
  return value;
}

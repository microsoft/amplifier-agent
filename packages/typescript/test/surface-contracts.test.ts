import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { after, test } from "node:test";
import { API, TypeFlags, type Type, type UnionType, type StringLiteralType, type TemplateLiteralType } from "typescript/unstable/sync";

const exported = [
  "Agent", "AgentError", "AgentOptions", "ApprovalDecision", "ApprovalHandler", "ApprovalRequest",
  "ApprovalRequestEvent", "ApprovalResolution", "ApprovalResponse", "ContentPart", "ConversationMessage",
  "Event", "McpServer", "OutputDelta", "Progress", "ReasoningDelta", "ReasoningFinal", "Session",
  "SessionOptions", "SessionRecord", "TextPart", "Tool", "ToolCall", "ToolCallEvent", "ToolContext",
  "ToolFailed", "ToolHandler", "ToolOutcomeUnknown", "ToolResolution", "ToolResultEvent", "Turn",
  "TurnInfo", "TurnInput", "TurnRecord", "TurnResult", "TurnStarted", "Usage", "UsageEntry", "UsageEvent",
  "contractVersion", "contractVersions", "createAgent", "version",
].sort();
const eventTypes = [
  "turn_started", "output_delta", "reasoning_delta", "reasoning_final", "tool_call", "tool_result",
  "approval_request", "approval_decision", "progress", "usage", "terminal",
].sort();
const operations: Record<string, string> = {
  create_agent: "createAgent", "agent.create_session": "Agent.createSession",
  "agent.resume_session": "Agent.resumeSession", "agent.list_sessions": "Agent.listSessions",
  "agent.delete_session": "Agent.deleteSession", "agent.close": "Agent.close",
  "session.info": "Session.info", "session.run": "Session.run", "session.start_turn": "Session.startTurn",
  "session.fork": "Session.fork", "session.history": "Session.history", "session.close": "Session.close",
  "turn.info": "Turn.info", "turn.events": "Turn.events", "turn.cancel": "Turn.cancel",
  contract_version: "contractVersion", contract_versions: "contractVersions",
};

const api = new API({ cwd: fileURLToPath(new URL("../", import.meta.url)) });
const snapshot = api.updateSnapshot({ openProjects: [fileURLToPath(new URL("../tsconfig.build.json", import.meta.url))] });
after(() => { snapshot.dispose(); api.close(); });
const project = snapshot.getProjects()[0]!;
const program = project.program;
const checker = project.checker;
const source = program.getSourceFile(fileURLToPath(new URL("../src/index.ts", import.meta.url)))!;
const symbols = new Map(checker.getExportsOfModule(checker.getSymbolAtLocation(source)!)
  .map((symbol) => [symbol.name, symbol]));
function declared(name: string): Type {
  const symbol = symbols.get(name);
  assert.ok(symbol, `Missing public declaration ${name}`);
  return checker.getDeclaredTypeOfSymbol(symbol);
}
function exportedNames(names: string[]): void {
  assert.deepEqual([...names].sort(), exported, "Public exports differ from the binding mapping");
}
function registeredEvents(names: string[]): void {
  assert.deepEqual([...names].sort(), eventTypes, "The unqualified event vocabulary changed");
}
function mappedOperation(local: string): boolean {
  const [owner, member] = local.split(".");
  return member === undefined ? symbols.has(owner!) : checker.getPropertyOfType(declared(owner!), member) !== undefined;
}

test("contract: TypeScript public exports reject missing and private capabilities", () => {
  exportedNames([...symbols.keys()]);
  assert.throws(() => exportedNames([...symbols.keys(), "EngineConfig"]), assert.AssertionError);
  assert.throws(() => exportedNames([...symbols.keys()].filter((name) => name !== "Turn")), assert.AssertionError);
  assert.equal(program.getSyntacticDiagnostics().length, 0);
});

test("contract: TypeScript name mapping resolves every documented operation and record", async () => {
  const document = await readFile(new URL("../../../docs/typescript/names.md", import.meta.url), "utf8");
  const block = document.split("## Operations")[1]!.split("```")[1]!;
  const mapping = Object.fromEntries(block.trim().split("\n").map((line) => line.trim().split(/\s+/)));
  assert.deepEqual(mapping, operations, "Documented operations must match the complete contract mapping");
  for (const local of Object.values(mapping)) assert.ok(mappedOperation(local), `Dangling operation ${local}`);
  assert.equal(mappedOperation("Agent.getEngine"), false);
  const records = document.split("## Records")[1]!.split("```")[1]!;
  for (const line of records.trim().split("\n")) {
    const local = line.trim().split(/\s+/)[1]!;
    assert.ok(symbols.has(local), `Dangling record ${local}`);
  }
});

test("contract: TypeScript event declarations reserve unqualified names and retain owned extensions", () => {
  const event = declared("Event");
  const discriminator = checker.getPropertyOfType(event, "type")!;
  const values = checker.getTypeOfSymbolAtLocation(discriminator, source);
  assert.ok(values.flags & TypeFlags.Union, "Events require a discriminated union");
  const members = (values as UnionType).getTypes();
  registeredEvents(members.filter((value) => value.flags & TypeFlags.StringLiteral).map((value) => (value as StringLiteralType).value));
  const extensions = members.filter((value) => !(value.flags & TypeFlags.StringLiteral));
  assert.equal(extensions.length, 1);
  assert.equal(extensions[0]!.flags & TypeFlags.TemplateLiteral, TypeFlags.TemplateLiteral);
  assert.deepEqual((extensions[0] as TemplateLiteralType).texts, ["", ".", ""]);
  assert.throws(() => registeredEvents([...eventTypes, "loop_started"]), assert.AssertionError);
  assert.throws(() => registeredEvents(eventTypes.filter((name) => name !== "terminal")), assert.AssertionError);
});

test("contract: TypeScript package exposes no supported command or private entry point", async () => {
  const manifest = JSON.parse(await readFile(new URL("../package.json", import.meta.url), "utf8")) as Record<string, unknown>;
  function validate(value: Record<string, unknown>): void {
    assert.equal(value.bin, undefined, "The binding does not publish commands");
    assert.deepEqual(value.exports, { ".": { types: "./dist/index.d.ts", import: "./dist/index.js" } }, "Only the public binding entry point is exported");
  }
  validate(manifest);
  assert.throws(() => validate({ ...manifest, bin: { "amplifier-agent": "./dist/cli.js" } }), assert.AssertionError);
  assert.throws(() => validate({ ...manifest, exports: { "./engine": "./dist/engine.js" } }), assert.AssertionError);
});

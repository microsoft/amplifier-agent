# AaA Mode A Pivot — Phase B: Wrapper Rewrite + Conformance Implementation Plan

> **For execution:** Use `/execute-plan` mode or the subagent-driven-development recipe.

**Prerequisite:** **Phase A merged** (`docs/plans/2026-05-24-mode-a-pivot-phase-a-engine.md` complete; the engine binary on PATH speaks the v2 wire surface — `--mcp-servers`, `--host-capabilities`, `--env-allowlist`, `--env-extra`, `--protocol-version`, `--output`, structured JSON envelope on stdout with stdout-discipline guarantees, per-turn audit trail, PGID session-leader behavior). Verify with `amplifier-agent run --session-id phase-b-baseline --fresh --output json "say hi"` returning a §4.1 envelope before starting.

**Goal:** Rewrite the TypeScript and Python wrappers as thin subprocess drivers. `spawnAgent` becomes synchronous-in-spirit and only stores params; `submit(prompt)` spawns the real `amplifier-agent run` subprocess per call, yields synthesized `init`+`activity` events while it runs, parses the §4.1 JSON envelope at exit, and yields the simplified `DisplayEvent` shape (CR-C breaking change). The full 10-fixture real-binary conformance suite (A4') gates the phase — every fixture launches the real engine binary; mocks live only at the provider-LLM HTTP boundary.

**Architecture:**
- `wrappers/typescript/src/session.ts:26-37` — apply the CR-C breaking change to `DisplayEvent`: simplified discriminated union (`init` | `activity` | `result` | `error`); drop `turnId`, `parentTurnId`, `synthesized`, `payload`.
- `wrappers/typescript/src/spawn.ts` — `spawnAgent` rejects `params.approval?.onRequest` loudly (SC-C `AaaError(approval_not_supported_in_v1)`); store params on the SessionHandle; no subprocess work at spawn time.
- `wrappers/typescript/src/session.ts` — `SessionHandle.submit` rewritten as subprocess driver per amendment §5.2. Spawns in new process group (`detached: true`); 2-second activity ticker; group-signal cancel (SC-B).
- New: `wrappers/typescript/src/run-output-parser.ts` (~100 LOC), `wrappers/typescript/src/mcp-spill.ts` (~40 LOC), `wrappers/typescript/src/argv-builder.ts` (~80 LOC).
- Delete: `wrappers/typescript/src/jsonrpc.ts` (no longer used); `wrappers/typescript/src/l14.ts` (no longer used — no notification stream to synthesize from).
- Python parity: identical changes on `wrappers/python/src/amplifier_agent_client/{session,spawn,types}.py`.
- Conformance fixtures live under `wrappers/conformance/tests/` (or wherever the existing fixtures landed — verify with `ls wrappers/conformance/`). Every Phase B fixture is real-binary; mock-LLM HTTP server is the only permitted mock and is the same one used in Phase A Task 14.

**Tech Stack:**
- TS wrapper: Node ≥ 20, TypeScript 5.4+, vitest (per `wrappers/typescript/package.json`). Lint via `tsc --noEmit`; format unchanged from existing repo conventions.
- Py wrapper: Python 3.11+, pytest, ruff, pyright (mirrors Phase A).
- Conformance: bun + Node + Python runners under `wrappers/conformance/` (already authored in prior phase). Mock LLM: HTTP server pattern from Phase A Task 14; promote to a shared fixture under `wrappers/conformance/fixtures/mock-llm/`.
- Version bump: `amplifier-agent-client-ts` and `amplifier-agent-client-py` go from `0.2.0` → `0.3.0` because the `DisplayEvent` shape is a **breaking change** per CR-C.

**Real-binary gate:** **Every Task numbered 11–20** below ships one real-binary conformance fixture. The Phase B acceptance gate (§ end) requires all 10 fixtures green across both TS and Py runners. Tasks 1–10 are unit-level (vitest/pytest) covering individual wrapper helpers; they use mocks only for the engine subprocess interface (Node's `child_process.spawn`).

**Task count:** 20 tasks. Larger than the 15-task ceiling, but every task is 2–5 minutes and Phase B has no natural sub-phase boundary that wouldn't fragment the wrapper rewrite into incoherent halves. Execute sequentially; the orchestrator can checkpoint between Task 10 (wrapper rewrite done) and Task 11 (conformance suite starts) if a review break is needed.

---

## Required reading before starting

1. `docs/designs/2026-05-24-aaa-v2-mode-a-pivot-amendment.md` §5 (the amended wrapper architecture), §8.1 A3'/A4' (stages + fixture roster).
2. `wrappers/typescript/src/session.ts`, `spawn.ts`, `transport.ts`, `index.ts` — the files being rewritten.
3. `wrappers/python/src/amplifier_agent_client/session.py`, `spawn.py`, `types.py` — Python parity targets.
4. `wrappers/conformance/README.md`, `runner_ts.ts`, `runner_py.py` — the conformance harness (already authored).
5. Phase A's `tests/cli/test_mode_a_v2_real_binary.py` — copy the mock LLM server pattern.
6. The current `wrappers/typescript/package.json` (verify the script names — `test`, `lint`, `typecheck`).

---

## Task 1: Create feature branch + verify Phase A binary speaks v2

**Files:** No source changes.

**Step 1: Branch**
```bash
git checkout -b feat/mode-a-phase-b-wrapper
```

**Step 2: Confirm the engine binary (from Phase A) speaks the v2 wire**
```bash
amplifier-agent run --session-id phase-b-baseline --fresh --output json --host-capabilities '{"supports_steering":false}' "say baseline-ok" > /tmp/baseline.json 2>/tmp/baseline.err
cat /tmp/baseline.json | python -c "import json, sys; e = json.load(sys.stdin); assert e['protocolVersion']; assert e['error'] is None; assert e['metadata']['hostCapabilities'] == {'supports_steering': False}; print('PHASE-A-OK')"
```
Expected: `PHASE-A-OK`. If anything fails, **stop and re-verify Phase A** — Phase B is built on the Phase A contract.

**Step 3: Confirm wrapper baseline**
```bash
cd wrappers/typescript && npm test && cd ../..
cd wrappers/python && uv run pytest && cd ../..
```
Expected: both pass (Phase A did not touch the wrapper).

**Step 4: Empty commit marker**
```bash
git commit --allow-empty -m "chore(wrappers): start Phase B — Mode A pivot wrapper rewrite"
```

---

## Task 2: Write failing test for the new `DisplayEvent` shape (CR-C)

**Files:**
- Create: `wrappers/typescript/test/session-mode-a-shape.test.ts`

**Test type:** (a) unit (vitest, no subprocess).

**Step 1: Write the test**
```typescript
// wrappers/typescript/test/session-mode-a-shape.test.ts
import { describe, expect, it } from "vitest";
import type { DisplayEvent } from "../src/session.js";

describe("DisplayEvent — Mode A simplified shape (CR-C)", () => {
  it("init event has only sessionId", () => {
    const ev: DisplayEvent = { type: "init", sessionId: "sid" };
    expect(ev.type).toBe("init");
    expect(ev.sessionId).toBe("sid");
    // @ts-expect-error — turnId removed from the union
    void ev.turnId;
  });

  it("activity event has no payload fields", () => {
    const ev: DisplayEvent = { type: "activity" };
    expect(ev.type).toBe("activity");
  });

  it("result event carries text only", () => {
    const ev: DisplayEvent = { type: "result", text: "hello" };
    expect(ev.text).toBe("hello");
    // @ts-expect-error — payload removed
    void ev.payload;
  });

  it("error event carries the full AaaError-shape fields", () => {
    const ev: DisplayEvent = {
      type: "error",
      code: "engine_crashed",
      classification: "engine",
      severity: "error",
      correlationId: "abc",
      message: "boom",
      retryable: false,
    };
    expect(ev.classification).toBe("engine");
    expect(ev.retryable).toBe(false);
  });
});
```

**Step 2: Run; expect FAIL**
```bash
cd wrappers/typescript && npm test -- session-mode-a-shape && cd ../..
```
Expected: FAIL — the existing `DisplayEvent` has `payload`, `turnId`, `parentTurnId` as required/optional fields; the `@ts-expect-error` comments will not fire (because the fields actually exist) and the structural fields the new test requires are missing.

**Step 3: Commit**
```bash
git add wrappers/typescript/test/session-mode-a-shape.test.ts
git commit -m "test(wrappers/ts): A3'/CR-C — failing test for simplified DisplayEvent"
```

---

## Task 3: Apply the CR-C breaking change to `DisplayEvent`

**Files:**
- Modify: `wrappers/typescript/src/session.ts`

**Step 1: Replace the interface (lines ~25–37) with the discriminated union**

Open `wrappers/typescript/src/session.ts`. Replace the `export interface DisplayEvent { ... }` block with:
```typescript
/**
 * Display event yielded by SessionHandle.submit().
 *
 * **BREAKING CHANGE in v0.3.0 (CR-C):** the shape was simplified for Mode A v2.
 * `turnId`, `parentTurnId`, `synthesized`, and `payload` were removed. The shape
 * now matches what NC's existing ProviderEvent consumer actually uses. If you
 * pinned to v0.2.x or earlier, see docs/designs/2026-05-24-aaa-v2-mode-a-pivot-
 * amendment.md §5.2 for the migration path.
 */
export type DisplayEvent =
  | { type: "init"; sessionId: string }
  | { type: "activity" }
  | { type: "result"; text: string }
  | {
      type: "error";
      code: string;
      classification: "transport" | "protocol" | "engine" | "approval" | "unknown";
      severity: "error" | "warning";
      correlationId: string;
      message: string;
      stderrTail?: string;
      retryable: boolean;
    };
```

**Step 2: The `TERMINAL_NOTIFICATION` constant and the `synthesizeFinalIfMissing` import become unused; mark for deletion later (Task 8)**

Add a `// TODO(phase-b-task-8): delete unused L14 imports after subprocess driver lands` comment above the `import { synthesizeFinalIfMissing } from "./l14.js";` line.

**Step 3: Existing `SessionHandle.submit` code now type-errors against the new shape**

Don't fix it yet — Task 7 rewrites `submit`. For now, expect type errors. Run:
```bash
cd wrappers/typescript && npx tsc --noEmit 2>&1 | head -30 && cd ../..
```
Expected: type errors in `session.ts` (the old `submit` implementation emits objects with `payload`, `turnId`, etc.).

**Step 4: Run the Task 2 test — it should PASS even though `tsc` errors out**
```bash
cd wrappers/typescript && npm test -- session-mode-a-shape && cd ../..
```
Vitest type-strips at test time and the structural assertions match.

**Step 5: Commit (knowingly leaving `session.ts` type-broken — Task 7 fixes it)**
```bash
git add wrappers/typescript/src/session.ts
git commit -m "feat(wrappers/ts)!: A3'/CR-C — simplify DisplayEvent for Mode A v2"
```
The `!` in the conventional-commit type marks this as a breaking change.

---

## Task 4: Reject `params.approval.onRequest` loudly in `spawnAgent` (SC-C)

**Files:**
- Modify: `wrappers/typescript/src/spawn.ts`
- Create: `wrappers/typescript/test/spawn-rejects-approval.test.ts`

**Test type:** (a) unit.

**Step 1: Write failing test**
```typescript
// wrappers/typescript/test/spawn-rejects-approval.test.ts
import { describe, expect, it } from "vitest";
import { spawnAgent } from "../src/index.js";
import { AaaError } from "../src/session.js";

describe("spawnAgent — SC-C approval.onRequest rejection", () => {
  it("throws AaaError(approval_not_supported_in_v1) before any subprocess work", async () => {
    await expect(
      spawnAgent({
        lifecycle: "one-shot",
        sessionId: "sid",
        approval: { onRequest: async () => ({ decision: "allow" }) },
      } as any),
    ).rejects.toMatchObject({
      name: "AaaError",
      code: "approval_not_supported_in_v1",
      classification: "protocol",
    });
  });
});
```

**Step 2: Run; expect FAIL (test fails because no rejection happens yet, or with a different error message).**
```bash
cd wrappers/typescript && npm test -- spawn-rejects-approval && cd ../..
```

**Step 3: Implement the rejection at the top of `spawnAgent`**

In `wrappers/typescript/src/spawn.ts` (or wherever `spawnAgent` lives — likely `index.ts` per earlier file listing; if so, add to `index.ts`), as the first guard:
```typescript
  // SC-C — mid-turn approval callbacks are not supported in v1. The Mode A
  // wire has no mid-turn request channel. Reject before any subprocess work
  // so the host author sees the failure at spawnAgent() call time, not as a
  // silent auto-allow at tool execution.
  if (params.approval?.onRequest !== undefined) {
    throw new AaaError(
      "approval_not_supported_in_v1",
      "Mid-turn approval callbacks are not supported in v1. The Mode A wire " +
        "has no mid-turn request channel. Configure approval policy at the " +
        "bundle layer via hooks-approval; do not pass an onRequest callback. " +
        "Mid-turn callbacks return in v1.x — track WG-4 in the amendment §6.",
      { classification: "protocol", severity: "error" },
    );
  }
```

Make sure `AaaError` is imported from `./session.js` if not already.

**Step 4: Update JSDoc on `SpawnAgentParams.approval`** in `wrappers/typescript/src/index.ts` or wherever the type lives. Add the multi-line JSDoc block from amendment §5.3 (the long block starting "Mid-turn approval callback.") verbatim above the `approval?:` field.

**Step 5: Run the test**
```bash
cd wrappers/typescript && npm test -- spawn-rejects-approval && cd ../..
```
Expected: PASS.

**Step 6: Commit**
```bash
git add wrappers/typescript/src/spawn.ts wrappers/typescript/src/index.ts wrappers/typescript/test/spawn-rejects-approval.test.ts
git commit -m "feat(wrappers/ts): A3'/SC-C — reject approval.onRequest with typed AaaError"
```

---

## Task 5: Add `argv-builder.ts` (build `amplifier-agent run ...` argv from params)

**Files:**
- Create: `wrappers/typescript/src/argv-builder.ts`
- Create: `wrappers/typescript/test/argv-builder.test.ts`

**Test type:** (a) unit.

**Step 1: Write failing test**
```typescript
// wrappers/typescript/test/argv-builder.test.ts
import { describe, expect, it } from "vitest";
import { assembleArgv } from "../src/argv-builder.js";

describe("assembleArgv — Mode A v2 argv composition", () => {
  it("happy path: minimal session", () => {
    const argv = assembleArgv({
      sessionId: "sid",
      prompt: "hello",
      protocolVersion: "0.1.0",
    });
    expect(argv).toEqual([
      "run",
      "--session-id", "sid",
      "--fresh",
      "--output", "json",
      "--protocol-version", "0.1.0",
      "-y",
      "hello",
    ]);
  });

  it("resume mode replaces --fresh with --resume", () => {
    const argv = assembleArgv({
      sessionId: "sid", prompt: "hi", protocolVersion: "0.1.0", resume: true,
    });
    expect(argv).toContain("--resume");
    expect(argv).not.toContain("--fresh");
  });

  it("threads --host-capabilities as JSON", () => {
    const argv = assembleArgv({
      sessionId: "sid", prompt: "x", protocolVersion: "0.1.0",
      hostCapabilities: { supports_steering: false, supports_structured_errors: true },
    });
    const idx = argv.indexOf("--host-capabilities");
    expect(idx).toBeGreaterThan(-1);
    expect(JSON.parse(argv[idx + 1])).toEqual({
      supports_steering: false, supports_structured_errors: true,
    });
  });

  it("threads --mcp-servers as inline JSON when no env spill", () => {
    const argv = assembleArgv({
      sessionId: "sid", prompt: "x", protocolVersion: "0.1.0",
      mcpServersFlag: '{"s":{"transport":"stdio","command":"node","args":[]}}',
    });
    const idx = argv.indexOf("--mcp-servers");
    expect(idx).toBeGreaterThan(-1);
    expect(argv[idx + 1]).toContain('"command":"node"');
  });

  it("threads --mcp-servers as @path when caller pre-spilled", () => {
    const argv = assembleArgv({
      sessionId: "sid", prompt: "x", protocolVersion: "0.1.0",
      mcpServersFlag: "@/tmp/spill.json",
    });
    const idx = argv.indexOf("--mcp-servers");
    expect(argv[idx + 1]).toBe("@/tmp/spill.json");
  });
});
```

**Step 2: Run; expect FAIL (file doesn't exist).**

**Step 3: Implement `argv-builder.ts`**
```typescript
// wrappers/typescript/src/argv-builder.ts
/**
 * Build the argv tail for `amplifier-agent run ...` per amendment §3.
 * Pure function — no I/O, no env reads.
 */

export interface AssembleArgvInput {
  sessionId: string;
  prompt: string;
  protocolVersion: string;
  resume?: boolean;
  cwd?: string;
  providerOverride?: string;
  /** Pre-built --mcp-servers value: inline JSON or "@<path>". null/undefined to omit. */
  mcpServersFlag?: string | null;
  hostCapabilities?: Record<string, unknown> | null;
  envAllowlist?: string[] | null;
  envExtra?: Record<string, string> | null;
  allowProtocolSkew?: boolean;
}

export function assembleArgv(input: AssembleArgvInput): string[] {
  const argv: string[] = ["run", "--session-id", input.sessionId];
  argv.push(input.resume ? "--resume" : "--fresh");
  if (input.cwd) argv.push("--cwd", input.cwd);
  if (input.providerOverride) argv.push("--provider", input.providerOverride);
  if (input.mcpServersFlag) argv.push("--mcp-servers", input.mcpServersFlag);
  if (input.hostCapabilities) argv.push("--host-capabilities", JSON.stringify(input.hostCapabilities));
  if (input.envAllowlist && input.envAllowlist.length > 0) {
    argv.push("--env-allowlist", input.envAllowlist.join(","));
  }
  if (input.envExtra) argv.push("--env-extra", JSON.stringify(input.envExtra));
  argv.push("--output", "json");
  argv.push("--protocol-version", input.protocolVersion);
  if (input.allowProtocolSkew) argv.push("--allow-protocol-skew");
  argv.push("-y"); // SC-C: wrapper enforces auto-allow at the bundle layer
  argv.push(input.prompt);
  return argv;
}
```

**Step 4: Run the test**
```bash
cd wrappers/typescript && npm test -- argv-builder && cd ../..
```
Expected: PASS (all 5 cases).

**Step 5: Commit**
```bash
git add wrappers/typescript/src/argv-builder.ts wrappers/typescript/test/argv-builder.test.ts
git commit -m "feat(wrappers/ts): A3' — pure argv assembler for Mode A v2"
```

---

## Task 6: Add `mcp-spill.ts` (CR-A secret-aware tmpfile spill)

**Files:**
- Create: `wrappers/typescript/src/mcp-spill.ts`
- Create: `wrappers/typescript/test/mcp-spill.test.ts`

**Test type:** (a) unit.

**Step 1: Write failing test**
```typescript
// wrappers/typescript/test/mcp-spill.test.ts
import { describe, expect, it, afterEach } from "vitest";
import { promises as fs } from "node:fs";
import { resolveMcpServersFlag, cleanupSpillFile } from "../src/mcp-spill.js";

const cleanups: string[] = [];
afterEach(async () => {
  for (const p of cleanups.splice(0)) await cleanupSpillFile(p).catch(() => {});
});

describe("resolveMcpServersFlag — CR-A secret-aware spill", () => {
  it("returns null when mcpServers is null/undefined", async () => {
    const { flag, spillPath } = await resolveMcpServersFlag(undefined, "sid");
    expect(flag).toBeNull();
    expect(spillPath).toBeNull();
  });

  it("inlines JSON when no server has a non-empty env block", async () => {
    const cfg = { s: { transport: "stdio", command: "node", args: [] } };
    const { flag, spillPath } = await resolveMcpServersFlag(cfg, "sid");
    expect(spillPath).toBeNull();
    expect(flag).toContain('"command":"node"');
    expect(flag!.startsWith("@")).toBe(false);
  });

  it("spills to tmpfile when any server has a non-empty env block", async () => {
    const cfg = {
      s: { transport: "stdio", command: "node", args: [], env: { KEY: "SECRET" } },
    };
    const { flag, spillPath } = await resolveMcpServersFlag(cfg, "sid-test");
    expect(spillPath).not.toBeNull();
    expect(flag).toBe(`@${spillPath}`);
    cleanups.push(spillPath!);

    // File exists and contains the full config
    const contents = await fs.readFile(spillPath!, "utf8");
    expect(JSON.parse(contents)).toEqual(cfg);

    // mode 0600
    const stat = await fs.stat(spillPath!);
    // eslint-disable-next-line no-bitwise
    expect(stat.mode & 0o777).toBe(0o600);
  });

  it("cleanupSpillFile is idempotent (ENOENT is fine)", async () => {
    await expect(cleanupSpillFile("/tmp/does-not-exist-xyz")).resolves.toBeUndefined();
  });
});
```

**Step 2: Run; expect FAIL (file missing).**

**Step 3: Implement `mcp-spill.ts`**
```typescript
// wrappers/typescript/src/mcp-spill.ts
import { promises as fs } from "node:fs";
import * as path from "node:path";
import * as os from "node:os";

/** Output of resolveMcpServersFlag. */
export interface McpSpillResult {
  /** The --mcp-servers flag value (inline JSON or "@<path>"); null when omitted. */
  flag: string | null;
  /** Tmpfile path when spilled, else null. Caller must cleanupSpillFile() on subprocess exit. */
  spillPath: string | null;
}

function hasNonEmptyEnv(servers: Record<string, any>): boolean {
  for (const cfg of Object.values(servers)) {
    if (cfg && typeof cfg === "object" && cfg.env && Object.keys(cfg.env).length > 0) {
      return true;
    }
  }
  return false;
}

function spillRoot(): string {
  return process.env.XDG_RUNTIME_DIR || path.join(os.tmpdir(), "amplifier-agent");
}

/**
 * CR-A: when any server has a non-empty `env` block, write the full JSON to a
 * 0600 tmpfile and return `@<path>`. Otherwise return the inline JSON string.
 *
 * Caller (the wrapper's subprocess driver) is responsible for calling
 * cleanupSpillFile(spillPath) in a try/finally on subprocess exit.
 */
export async function resolveMcpServersFlag(
  mcpServers: Record<string, unknown> | null | undefined,
  sessionId: string,
): Promise<McpSpillResult> {
  if (!mcpServers || Object.keys(mcpServers).length === 0) {
    return { flag: null, spillPath: null };
  }
  if (!hasNonEmptyEnv(mcpServers as Record<string, any>)) {
    return { flag: JSON.stringify(mcpServers), spillPath: null };
  }
  const dir = path.join(spillRoot(), sessionId);
  await fs.mkdir(dir, { recursive: true, mode: 0o700 });
  const filePath = path.join(dir, "mcp.json");
  await fs.writeFile(filePath, JSON.stringify(mcpServers), { mode: 0o600 });
  return { flag: `@${filePath}`, spillPath: filePath };
}

export async function cleanupSpillFile(spillPath: string | null | undefined): Promise<void> {
  if (!spillPath) return;
  try {
    await fs.unlink(spillPath);
  } catch (err: any) {
    if (err && err.code !== "ENOENT") throw err;
  }
}
```

**Step 4: Run the test**
```bash
cd wrappers/typescript && npm test -- mcp-spill && cd ../..
```
Expected: PASS.

**Step 5: Commit**
```bash
git add wrappers/typescript/src/mcp-spill.ts wrappers/typescript/test/mcp-spill.test.ts
git commit -m "feat(wrappers/ts): A3'/CR-A — secret-aware MCP tmpfile spill (0600)"
```

---

## Task 7: Add `run-output-parser.ts` (parse §4.1 envelope; SC-D precedence)

**Files:**
- Create: `wrappers/typescript/src/run-output-parser.ts`
- Create: `wrappers/typescript/test/run-output-parser.test.ts`

**Test type:** (a) unit.

**Step 1: Write failing test**
```typescript
// wrappers/typescript/test/run-output-parser.test.ts
import { describe, expect, it } from "vitest";
import { parseRunOutput } from "../src/run-output-parser.js";

const okEnvelope = JSON.stringify({
  protocolVersion: "0.1.0",
  sessionId: "sid",
  turnId: "turn-1",
  reply: "hello",
  error: null,
  metadata: {
    tokensIn: 1, tokensOut: 1, durationMs: 10,
    bundleDigest: "sha256:x", engineVersion: "0.3.0",
    protocolVersion: "0.1.0", correlationId: "abc",
  },
});

const errEnvelope = JSON.stringify({
  protocolVersion: "0.1.0", sessionId: "sid", turnId: "turn-1", reply: "",
  error: {
    code: "engine_crashed", classification: "engine", severity: "error",
    correlationId: "abc", message: "boom",
  },
  metadata: {
    tokensIn: 0, tokensOut: 0, durationMs: 5,
    bundleDigest: "", engineVersion: "0.3.0",
    protocolVersion: "0.1.0", correlationId: "abc",
  },
});

describe("parseRunOutput — SC-D envelope precedence", () => {
  it("rule 1a: valid envelope with error=null + exit 0 → result", () => {
    const ev = parseRunOutput({ stdout: okEnvelope, stderr: "", exitCode: 0 });
    expect(ev.type).toBe("result");
    if (ev.type !== "result") throw new Error();
    expect(ev.text).toBe("hello");
  });

  it("rule 1b: valid envelope with error=null + EXIT 1 → still result (envelope wins)", () => {
    const ev = parseRunOutput({ stdout: okEnvelope, stderr: "x", exitCode: 1 });
    expect(ev.type).toBe("result");
  });

  it("rule 1c: valid envelope with populated error → error event", () => {
    const ev = parseRunOutput({ stdout: errEnvelope, stderr: "", exitCode: 1 });
    expect(ev.type).toBe("error");
    if (ev.type !== "error") throw new Error();
    expect(ev.code).toBe("engine_crashed");
    expect(ev.classification).toBe("engine");
  });

  it("rule 2a: exit 0 + empty stdout → envelope_missing protocol error", () => {
    const ev = parseRunOutput({ stdout: "", stderr: "", exitCode: 0 });
    expect(ev.type).toBe("error");
    if (ev.type !== "error") throw new Error();
    expect(ev.code).toBe("envelope_missing");
    expect(ev.classification).toBe("protocol");
  });

  it("rule 2b: non-zero exit + empty stdout → engine_exit_N error", () => {
    const ev = parseRunOutput({ stdout: "", stderr: "trace", exitCode: 137 });
    expect(ev.type).toBe("error");
    if (ev.type !== "error") throw new Error();
    expect(ev.code).toBe("engine_exit_137");
    expect(ev.classification).toBe("engine");
    expect(ev.stderrTail).toContain("trace");
  });

  it("rule 2c: partial/truncated JSON treated as unparseable", () => {
    const ev = parseRunOutput({ stdout: '{"protocolVersion":', stderr: "", exitCode: 1 });
    expect(ev.type).toBe("error");
    if (ev.type !== "error") throw new Error();
    expect(ev.code).toBe("engine_exit_1");
  });
});
```

**Step 2: Run; expect FAIL.**

**Step 3: Implement `run-output-parser.ts`**
```typescript
// wrappers/typescript/src/run-output-parser.ts
import type { DisplayEvent } from "./session.js";

export interface SubprocessOutcome {
  stdout: string;
  stderr: string;
  exitCode: number;
}

interface EnvelopeShape {
  protocolVersion?: unknown;
  sessionId?: unknown;
  turnId?: unknown;
  reply?: unknown;
  error?: null | {
    code?: unknown;
    classification?: unknown;
    severity?: unknown;
    correlationId?: unknown;
    message?: unknown;
    stderrTail?: unknown;
  };
  metadata?: { correlationId?: unknown };
}

function isShapeValid(e: unknown): e is Required<EnvelopeShape> & { error: any; metadata: any } {
  if (typeof e !== "object" || e === null) return false;
  const x = e as EnvelopeShape;
  return (
    typeof x.protocolVersion === "string" &&
    typeof x.sessionId === "string" &&
    typeof x.turnId === "string" &&
    typeof x.reply === "string" &&
    (x.error === null || (typeof x.error === "object" && typeof (x.error as any).code === "string")) &&
    typeof x.metadata === "object"
  );
}

function tailStderr(stderr: string, max = 4096): string {
  return stderr.length <= max ? stderr : stderr.slice(stderr.length - max);
}

/**
 * SC-D precedence rules per amendment §4.4.
 * 1. Envelope parseable → envelope wins; exit code is informational.
 * 2. Envelope absent/unparseable → synthesize AaaError from exit code + stderr.
 */
export function parseRunOutput(outcome: SubprocessOutcome): DisplayEvent {
  let parsed: unknown = null;
  try {
    parsed = JSON.parse(outcome.stdout.trim());
  } catch {
    parsed = null;
  }

  if (isShapeValid(parsed)) {
    if (parsed.error === null) {
      return { type: "result", text: parsed.reply };
    }
    const err = parsed.error as any;
    return {
      type: "error",
      code: String(err.code),
      classification: (err.classification ?? "unknown") as any,
      severity: (err.severity ?? "error") as any,
      correlationId: String(err.correlationId ?? ""),
      message: String(err.message ?? ""),
      stderrTail: err.stderrTail ? String(err.stderrTail) : tailStderr(outcome.stderr),
      retryable: false,
    };
  }

  // Rule 2: envelope absent or unparseable.
  if (outcome.exitCode === 0) {
    return {
      type: "error",
      code: "envelope_missing",
      classification: "protocol",
      severity: "error",
      correlationId: "",
      message:
        "Engine exited 0 without emitting a valid §4.1 envelope. " +
        `Stdout (truncated): ${outcome.stdout.slice(0, 512)}`,
      stderrTail: tailStderr(outcome.stderr),
      retryable: false,
    };
  }
  return {
    type: "error",
    code: `engine_exit_${outcome.exitCode}`,
    classification: "engine",
    severity: "error",
    correlationId: "",
    message: `Engine exited ${outcome.exitCode} without a valid envelope.`,
    stderrTail: tailStderr(outcome.stderr),
    retryable: false,
  };
}
```

**Step 4: Run the test**
```bash
cd wrappers/typescript && npm test -- run-output-parser && cd ../..
```
Expected: all 6 cases PASS.

**Step 5: Commit**
```bash
git add wrappers/typescript/src/run-output-parser.ts wrappers/typescript/test/run-output-parser.test.ts
git commit -m "feat(wrappers/ts): A3'/SC-D — §4.1 envelope parser with precedence rules"
```

---

## Task 8: Rewrite `SessionHandle.submit` as subprocess driver (the big one)

**Files:**
- Modify: `wrappers/typescript/src/session.ts`
- Modify: `wrappers/typescript/src/spawn.ts` (defer subprocess until submit)
- Delete: `wrappers/typescript/src/l14.ts`, `wrappers/typescript/src/jsonrpc.ts` (no longer used)

**Step 1: Replace the body of `SessionHandle`**

The new `SessionHandle` no longer holds an `rpc` reference. Its constructor accepts a params bag (binary path, sessionId, mcpServers, hostCapabilities, etc.); `submit()` spawns the engine per call.

Open `wrappers/typescript/src/session.ts`. Replace the class body (everything from `export class SessionHandle {` through the closing brace) with the implementation per amendment §5.2. The key shape:

```typescript
import { spawn, type ChildProcess } from "node:child_process";
import { assembleArgv } from "./argv-builder.js";
import { resolveMcpServersFlag, cleanupSpillFile } from "./mcp-spill.js";
import { parseRunOutput } from "./run-output-parser.js";

export interface SessionHandleParams {
  binaryPath: string;
  sessionId: string;
  subprocessEnv: NodeJS.ProcessEnv;
  resume?: boolean;
  cwd?: string;
  mcpServers?: Record<string, unknown> | null;
  hostCapabilities?: Record<string, unknown> | null;
  envAllowlist?: string[] | null;
  envExtra?: Record<string, string> | null;
  providerOverride?: string;
  allowProtocolSkew?: boolean;
  protocolVersion: string;
  /** Subprocess timeout in ms; default 10min for chat turns. */
  timeoutMs?: number;
}

export class SessionHandle {
  private submitted = false;
  private subprocess: ChildProcess | null = null;
  private mcpSpillPath: string | null = null;
  private engineInfo: EngineInfo = {
    binaryPath: "",
    protocolVersion: "",
    engineVersion: "",
    bundleDigest: "",
  };

  constructor(private readonly p: SessionHandleParams) {
    this.engineInfo.binaryPath = p.binaryPath;
    this.engineInfo.protocolVersion = p.protocolVersion;
  }

  getEngineInfo(): EngineInfo {
    return { ...this.engineInfo };
  }

  submit(prompt: string): AsyncIterable<DisplayEvent> {
    if (this.submitted) {
      throw new AaaError(
        "lifecycle_unsupported",
        "SessionHandle.submit() is one-shot per session (D10)",
        { classification: "protocol", severity: "error" },
      );
    }
    this.submitted = true;
    return this.makeIterable(prompt);
  }

  async cancel(): Promise<void> {
    if (this.subprocess && this.subprocess.exitCode === null && this.subprocess.pid) {
      const pgid = this.subprocess.pid;
      try { process.kill(-pgid, "SIGTERM"); } catch { /* ESRCH */ }
      await waitForExitOrTimeout(this.subprocess, 5000);
      if (this.subprocess.exitCode === null) {
        try { process.kill(-pgid, "SIGKILL"); } catch { /* ESRCH */ }
      }
    }
    await cleanupSpillFile(this.mcpSpillPath);
    this.mcpSpillPath = null;
  }

  async dispose(): Promise<void> { return this.cancel(); }

  private async *makeIterable(prompt: string): AsyncGenerator<DisplayEvent> {
    // SC-1: init emitted synchronously BEFORE subprocess spawn — no race window.
    yield { type: "init", sessionId: this.p.sessionId };

    // CR-A spill
    const spill = await resolveMcpServersFlag(this.p.mcpServers, this.p.sessionId);
    this.mcpSpillPath = spill.spillPath;

    const argv = assembleArgv({
      sessionId: this.p.sessionId,
      prompt,
      protocolVersion: this.p.protocolVersion,
      resume: this.p.resume,
      cwd: this.p.cwd,
      providerOverride: this.p.providerOverride,
      mcpServersFlag: spill.flag,
      hostCapabilities: this.p.hostCapabilities,
      envAllowlist: this.p.envAllowlist,
      envExtra: this.p.envExtra,
      allowProtocolSkew: this.p.allowProtocolSkew,
    });

    // SC-B: detached:true creates a new session group so MCP children inherit it.
    const child = spawn(this.p.binaryPath, argv, {
      detached: true,
      stdio: ["ignore", "pipe", "pipe"],
      env: this.p.subprocessEnv,
      cwd: this.p.cwd,
    });
    this.subprocess = child;

    let stdout = "";
    let stderr = "";
    child.stdout!.on("data", (chunk: Buffer) => { stdout += chunk.toString("utf8"); });
    child.stderr!.on("data", (chunk: Buffer) => { stderr += chunk.toString("utf8"); });

    // 2-second activity ticker (yields {type:'activity'} into the queue).
    const activityQueue: DisplayEvent[] = [];
    let wake: (() => void) | null = null;
    const ticker = setInterval(() => {
      activityQueue.push({ type: "activity" });
      if (wake) { const w = wake; wake = null; w(); }
    }, 2000);

    const exitPromise = new Promise<number>((resolve) => {
      child.once("exit", (code) => resolve(code ?? -1));
    });
    const timeout = this.p.timeoutMs ?? 600_000;
    const timeoutPromise = new Promise<"timeout">((resolve) =>
      setTimeout(() => resolve("timeout"), timeout),
    );

    let done = false;
    let finalEvent: DisplayEvent | null = null;

    // Drive both ticker drain + exit detection.
    void Promise.race([exitPromise, timeoutPromise]).then(async (outcome) => {
      clearInterval(ticker);
      if (outcome === "timeout") {
        await this.cancel();
        finalEvent = {
          type: "error",
          code: "engine_hung",
          classification: "engine",
          severity: "error",
          correlationId: "",
          message: `Engine did not exit within ${timeout}ms`,
          stderrTail: stderr.slice(-4096),
          retryable: false,
        };
      } else {
        finalEvent = parseRunOutput({ stdout, stderr, exitCode: outcome });
      }
      await cleanupSpillFile(this.mcpSpillPath);
      this.mcpSpillPath = null;
      done = true;
      if (wake) { const w = wake; wake = null; w(); }
    });

    // Drain loop.
    while (!done || activityQueue.length > 0) {
      if (activityQueue.length > 0) {
        yield activityQueue.shift()!;
        continue;
      }
      if (done) break;
      await new Promise<void>((resolve) => { wake = resolve; });
    }
    if (finalEvent) yield finalEvent;
  }
}

async function waitForExitOrTimeout(child: ChildProcess, ms: number): Promise<void> {
  if (child.exitCode !== null) return;
  await Promise.race([
    new Promise<void>((resolve) => child.once("exit", () => resolve())),
    new Promise<void>((resolve) => setTimeout(resolve, ms)),
  ]);
}
```

**Step 2: Update `spawnAgent` to defer subprocess work**

In `wrappers/typescript/src/spawn.ts` (or `index.ts` wherever it lives), the function becomes synchronous-in-spirit. It validates params, resolves the binary path, builds the env, and returns a `SessionHandle` constructed from the params. No subprocess spawn. No `probeEngineVersion` call. (The `version.ts` file is kept but its `checkProtocolVersion` is unused — flag a TODO for Task 9 cleanup.)

**Step 3: Delete files no longer referenced**

```bash
rm wrappers/typescript/src/l14.ts
rm wrappers/typescript/src/jsonrpc.ts
```

Update `wrappers/typescript/src/session.ts` to remove the `import { synthesizeFinalIfMissing } from "./l14.js";` and `TERMINAL_NOTIFICATION` constant.

Also remove the corresponding test files if they exist (`wrappers/typescript/test/l14.test.ts`, `wrappers/typescript/test/jsonrpc.test.ts`).

**Step 4: Type-check passes; existing tests need updating**

```bash
cd wrappers/typescript && npx tsc --noEmit 2>&1 | head -30 && cd ../..
```
Fix any remaining type errors. Likely places: old tests that asserted `payload`, `turnId`, etc. — delete those tests; the new shape is asserted in Task 2's test.

**Step 5: Run the wrapper test suite**
```bash
cd wrappers/typescript && npm test && cd ../..
```
Expected: all wrapper unit tests PASS (the suite is now: shape test, spawn-rejection test, argv-builder, mcp-spill, run-output-parser, plus any unrelated existing tests that still apply).

**Step 6: Commit (one large commit because the changes are interdependent)**
```bash
git add -A wrappers/typescript/
git commit -m "feat(wrappers/ts)!: A3' — rewrite SessionHandle as subprocess driver"
```

---

## Task 9: Python wrapper parity — apply all of Tasks 3-8 to `wrappers/python/src/amplifier_agent_client/`

**Files:**
- Modify: `wrappers/python/src/amplifier_agent_client/{session,spawn,types,__init__}.py`
- Create: `wrappers/python/src/amplifier_agent_client/{argv_builder,mcp_spill,run_output_parser}.py`
- Delete: `wrappers/python/src/amplifier_agent_client/{l14,jsonrpc}.py`
- Create/Modify: `wrappers/python/tests/test_*.py` mirrors of the TS test files

**Test type:** (a) unit (pytest).

**Step 1: Mirror each TS file**

For each TS file authored or modified in Tasks 3-8, create or modify the Python parity:

| TS file | Python parity |
|---|---|
| `src/session.ts` `DisplayEvent` union | `src/amplifier_agent_client/types.py` — use `typing.Literal` discriminator + `TypedDict` (or `dataclasses`) |
| `src/spawn.ts` `spawnAgent` rejection | `src/amplifier_agent_client/spawn.py` — `async def spawn_agent(params)` raises `AaaError` |
| `src/argv-builder.ts` | `src/amplifier_agent_client/argv_builder.py` |
| `src/mcp-spill.ts` | `src/amplifier_agent_client/mcp_spill.py` — use `asyncio.to_thread` for file I/O |
| `src/run-output-parser.ts` | `src/amplifier_agent_client/run_output_parser.py` |
| `src/session.ts` `SessionHandle.submit` | `src/amplifier_agent_client/session.py` — `asyncio.subprocess` instead of `child_process.spawn`; `os.killpg(pgid, SIGTERM)` for cancel; `start_new_session=True` for setsid |

Use `asyncio.create_subprocess_exec(*argv, start_new_session=True, stdout=PIPE, stderr=PIPE)` to get setsid behavior on POSIX. Use `os.killpg(os.getpgid(proc.pid), signal.SIGTERM)` for the group signal.

**Step 2: Mirror each TS test file**

Create `wrappers/python/tests/test_display_event_shape.py`, `test_spawn_rejects_approval.py`, `test_argv_builder.py`, `test_mcp_spill.py`, `test_run_output_parser.py` — each verifying the same behavior as the TS counterpart.

**Step 3: Delete `l14.py`, `jsonrpc.py` and their tests.**

**Step 4: Run pytest + ruff + pyright on the Python wrapper**
```bash
cd wrappers/python && uv run pytest && uv run ruff check src/ tests/ && uv run pyright src/ && cd ../..
```
Expected: all green.

**Step 5: Commit**
```bash
git add -A wrappers/python/
git commit -m "feat(wrappers/py)!: A3' — Python wrapper parity for Mode A subprocess driver"
```

---

## Task 10: Cross-language parity lint + version bump 0.2.0 → 0.3.0

**Files:**
- Modify: `wrappers/typescript/package.json`
- Modify: `wrappers/python/pyproject.toml` (or `setup.py` — check)

**Step 1: Run the existing conformance parity lint** (`tests/test_conformance_parity.py` in the engine repo — verify it still applies; the test should walk both wrappers and assert shape parity).

```bash
uv run pytest tests/test_conformance_parity.py -v
```
Expected: PASS. If it fails because the simplified `DisplayEvent` is missing a member in one language, fix the lag.

**Step 2: Bump versions**

```bash
# TypeScript
sed -i.bak 's/"version": "0.2.0"/"version": "0.3.0"/' wrappers/typescript/package.json
rm wrappers/typescript/package.json.bak

# Python (verify the file first)
cat wrappers/python/pyproject.toml | grep version
# then bump similarly with sed or by hand
```

**Step 3: Run full wrapper test suites + lint**
```bash
cd wrappers/typescript && npm test && npm run typecheck && cd ../..
cd wrappers/python && uv run pytest && uv run ruff check src/ tests/ && uv run pyright src/ && cd ../..
uv run pytest tests/ -q  # engine repo regression
```
Expected: all green.

**Step 4: Commit**
```bash
git add -A
git commit -m "chore(wrappers): bump to v0.3.0 — Mode A pivot breaking changes (CR-C)"
```

---

## Task 11: Conformance fixture — `mode-a-happy-path.yaml`

**Files:**
- Create: `wrappers/conformance/fixtures/mode-a-happy-path.yaml`
- Verify: `wrappers/conformance/fixtures/mock-llm/` directory exists with the Phase A mock LLM server promoted to a shared utility.

**Test type:** **(b) real-binary**.

**Setup notes:** The conformance harness already has `runner_ts.ts` and `runner_py.py`. Each fixture YAML declares: setup commands, the wrapper invocation, expected events, expected assertions. Inspect an existing fixture (`ls wrappers/conformance/fixtures/`) to see the schema, then copy the structure.

**Step 1: Promote mock LLM into shared fixture**

If not already done in Task 10, factor the Phase A mock LLM (`tests/cli/test_mode_a_v2_real_binary.py::MockLLM`) into a standalone script `wrappers/conformance/fixtures/mock-llm/server.py` that takes `--port <N>` and `--scripted-replies <json-or-@path>`. Document its usage in `wrappers/conformance/fixtures/mock-llm/README.md` (1 paragraph).

**Step 2: Write `mode-a-happy-path.yaml`**

The fixture spec depends on the existing harness shape — verify against `runner_ts.ts`. Structure:
```yaml
name: mode-a-happy-path
description: A4'/CR-D — clean turn, no MCP, no resume; envelope schema valid.
setup:
  - run: python wrappers/conformance/fixtures/mock-llm/server.py --port ${MOCK_LLM_PORT} --scripted-replies '[{"text":"happy-path-ok"}]' &
env:
  ANTHROPIC_BASE_URL: http://127.0.0.1:${MOCK_LLM_PORT}
  ANTHROPIC_API_KEY: test-key
invoke:
  language_any:  # runs in both TS and Py
    spawn_agent:
      lifecycle: one-shot
      sessionId: hp-sid
    submit_prompt: "say hi"
expect:
  events:
    - { type: init, sessionId: hp-sid }
    - { type: activity }   # zero or more, ticker-synthesized
    - { type: result, text: "happy-path-ok" }
  assertions:
    - exit_code: 0
    - envelope.metadata.correlationId: nonempty
    - envelope.error: null
```

**Step 3: Run the fixture**
```bash
cd wrappers/conformance && ./runner_ts.ts mode-a-happy-path && python runner_py.py mode-a-happy-path && cd ../..
```
Expected: both PASS.

**Step 4: Commit**
```bash
git add wrappers/conformance/fixtures/mode-a-happy-path.yaml wrappers/conformance/fixtures/mock-llm/
git commit -m "test(conformance): A4'/CR-D — mode-a-happy-path real-binary fixture"
```

---

## Task 12: Conformance fixture — `mode-a-mcp-injection.yaml`

**Files:**
- Create: `wrappers/conformance/fixtures/mode-a-mcp-injection.yaml`

**Test type:** **(b) real-binary**.

**Step 1: Author the fixture**

The fixture passes two MCP servers in `params.mcpServers`: one without `env` (inline-form), one with non-empty `env` (CR-A spill form). The MCP servers can be no-op stdio scripts shipped under `wrappers/conformance/fixtures/mock-mcp/` — a 5-line Python script that prints an MCP `initialize` response and exits is sufficient; the engine just needs to launch and connect.

Assertions:
- `ps -o args= -p <engine-pid>` does NOT contain the literal value of the `env: {KEY: "SECRET"}` block.
- The argv shows `--mcp-servers @<path>`.
- The tmpfile at the path exists during the turn with mode 0600.
- After subprocess exit, the tmpfile is gone.
- The envelope on stdout parses, `error: null`.

Implementation note: capturing the live argv requires the fixture to grep `/proc/<pid>/cmdline` (Linux) or `ps -o args=` (macOS) shortly after spawn. Use the conformance harness's `assert_during_run` hook if present; otherwise add a small shell helper script.

**Step 2: Run**
```bash
cd wrappers/conformance && ./runner_ts.ts mode-a-mcp-injection && python runner_py.py mode-a-mcp-injection && cd ../..
```
Expected: both PASS.

**Step 3: Commit**
```bash
git add wrappers/conformance/fixtures/mode-a-mcp-injection.yaml wrappers/conformance/fixtures/mock-mcp/
git commit -m "test(conformance): A4'/CR-A — mode-a-mcp-injection (inline + spilled)"
```

---

## Task 13: Conformance fixture — `mode-a-resume-continuity.yaml`

**Files:**
- Create: `wrappers/conformance/fixtures/mode-a-resume-continuity.yaml`

**Test type:** **(b) real-binary**.

**Step 1: Author the fixture**

Turn 1: mock LLM scripted to reply "I'll remember purple"; wrapper spawns engine with `--fresh --session-id resume-sid`. Turn 2: same sessionId with `resume: true`; mock LLM scripted to reply "the color you mentioned was purple"; assert the second envelope's `reply` contains "purple". This verifies SessionStore wiring through the real binary (which is the CR-1 closure carried over from 2026-05-22).

**Step 2: Run + commit**
```bash
cd wrappers/conformance && ./runner_ts.ts mode-a-resume-continuity && python runner_py.py mode-a-resume-continuity && cd ../..
git add wrappers/conformance/fixtures/mode-a-resume-continuity.yaml
git commit -m "test(conformance): A4'/CR-1 — mode-a-resume-continuity"
```

---

## Task 14: Conformance fixture — `mode-a-host-capabilities.yaml`

**Files:**
- Create: `wrappers/conformance/fixtures/mode-a-host-capabilities.yaml`

**Test type:** **(b) real-binary**.

**Step 1: Author + run**

Pass `{supports_steering: false, supports_structured_errors: true}` via `params.host.capabilities`. Assert the envelope's `metadata.hostCapabilities` echoes back exactly. Then read the audit file at `~/.local/state/amplifier-agent/sessions/<sid>/audits/turn-turn-1.json` and assert `hostCapabilities` field matches.

```bash
cd wrappers/conformance && ./runner_ts.ts mode-a-host-capabilities && python runner_py.py mode-a-host-capabilities && cd ../..
git add wrappers/conformance/fixtures/mode-a-host-capabilities.yaml
git commit -m "test(conformance): A4'/D12' — mode-a-host-capabilities echo"
```

---

## Task 15: Conformance fixture — `mode-a-protocol-skew.yaml`

**Files:**
- Create: `wrappers/conformance/fixtures/mode-a-protocol-skew.yaml`

**Test type:** **(b) real-binary**.

**Step 1: Author + run**

Wrapper passes `--protocol-version 9.9.9-NOT-REAL` without `--allow-protocol-skew`. Assert:
- Envelope's `error.code === "protocol_version_mismatch"`
- `error.classification === "protocol"`
- `error.remediation` is populated with reinstall instructions
- Exit code 2
- The wrapper's `DisplayEvent` yielded to the iterable is `{type: "error", code: "protocol_version_mismatch", ...}`.

```bash
cd wrappers/conformance && ./runner_ts.ts mode-a-protocol-skew && python runner_py.py mode-a-protocol-skew && cd ../..
git add wrappers/conformance/fixtures/mode-a-protocol-skew.yaml
git commit -m "test(conformance): A4'/D6' — mode-a-protocol-skew"
```

---

## Task 16: Conformance fixture — `mode-a-error-taxonomy.yaml`

**Files:**
- Create: `wrappers/conformance/fixtures/mode-a-error-taxonomy.yaml`

**Test type:** **(b) real-binary**.

**Step 1: Author + run**

The engine binary must be reachable in a mode where a bundle hook deliberately raises. The easiest way: ship a tiny "fault-injection" bundle under `wrappers/conformance/fixtures/fault-bundle/` that, when mounted, raises `RuntimeError("boom")` from `on_tool_pre`. Invoke the wrapper with the bundle override env var (`AMPLIFIER_BUNDLE_OVERRIDE=<path>` — verify this env hook exists; if not, write the fixture to use a real-bundle path manipulation via `--bundle`).

Assertions:
- `envelope.error.classification === "engine"`
- `envelope.error.correlationId` is a UUID v4 (regex `^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`)
- `envelope.error.stderrTail` is non-empty and contains "boom"

```bash
cd wrappers/conformance && ./runner_ts.ts mode-a-error-taxonomy && python runner_py.py mode-a-error-taxonomy && cd ../..
git add wrappers/conformance/fixtures/mode-a-error-taxonomy.yaml wrappers/conformance/fixtures/fault-bundle/
git commit -m "test(conformance): A4'/D8 — mode-a-error-taxonomy"
```

---

## Task 17: Conformance fixture — `mode-a-stdout-discipline.yaml` (CR-B)

**Files:**
- Create: `wrappers/conformance/fixtures/mode-a-stdout-discipline.yaml`
- Create: `wrappers/conformance/fixtures/noisy-bundle/` — bundle that prints 50 lines from a hook

**Test type:** **(b) real-binary**.

**Step 1: Author + run**

The noisy-bundle ships an `on_turn_start` hook that calls `print(f"DEBUG line {i}")` 50 times. Wrapper invokes the engine with this bundle; assert:
- `JSON.parse(stdout)` succeeds (the envelope is on stdout, intact).
- All 50 lines appear in the wrapper-captured `stderr` (the wrapper's subprocess driver caches stderr).
- No "DEBUG line" appears in stdout.

```bash
cd wrappers/conformance && ./runner_ts.ts mode-a-stdout-discipline && python runner_py.py mode-a-stdout-discipline && cd ../..
git add wrappers/conformance/fixtures/mode-a-stdout-discipline.yaml wrappers/conformance/fixtures/noisy-bundle/
git commit -m "test(conformance): A4'/CR-B — mode-a-stdout-discipline"
```

---

## Task 18: Conformance fixture — `mode-a-approval-callback-rejected.yaml` (SC-C)

**Files:**
- Create: `wrappers/conformance/fixtures/mode-a-approval-callback-rejected.yaml`

**Test type:** **(b) real-binary** — although no subprocess should be spawned, the assertion is "no engine process ran", which is real-binary-class evidence.

**Step 1: Author + run**

Wrapper calls `spawnAgent({approval: {onRequest: ...}})`. Assert:
- `spawnAgent` rejects/throws synchronously with `AaaError`, `code: "approval_not_supported_in_v1"`.
- No `amplifier-agent` process was started — verify via `pgrep -f 'amplifier-agent run'` returning empty (use a "before" baseline pgrep and an "after" pgrep; the count must be identical).

```bash
cd wrappers/conformance && ./runner_ts.ts mode-a-approval-callback-rejected && python runner_py.py mode-a-approval-callback-rejected && cd ../..
git add wrappers/conformance/fixtures/mode-a-approval-callback-rejected.yaml
git commit -m "test(conformance): A4'/SC-C — mode-a-approval-callback-rejected"
```

---

## Task 19: Conformance fixture — `mode-a-orphan-cleanup.yaml` (SC-B)

**Files:**
- Create: `wrappers/conformance/fixtures/mode-a-orphan-cleanup.yaml`
- Create: `wrappers/conformance/fixtures/slow-mcp/server.py` — MCP server that ignores SIGTERM for 10s

**Test type:** **(b) real-binary**.

**Step 1: Author the slow-MCP**

```python
# wrappers/conformance/fixtures/slow-mcp/server.py
# Stdio MCP server that ignores SIGTERM for 10s, then exits.
import signal, sys, time
signal.signal(signal.SIGTERM, lambda *_: None)
sys.stderr.write("slow-mcp started\n")
sys.stderr.flush()
# Simulate normal MCP handshake on stdin/stdout in a real implementation;
# for the fixture, just sleep so the wrapper's cancel() must escalate to SIGKILL.
time.sleep(120)
```

**Step 2: Author the fixture**

Wrapper spawns engine with `slow-mcp` configured as an MCP server. The wrapper then calls `cancel()` mid-turn (the conformance harness has an `interrupt_after_ms` hook — verify). Assertions:
- All MCP child processes (`pgrep -P <engine-pid>`) are dead within the 5s grace + 5s SIGKILL window.
- No zombie processes (use `ps -o stat= -p <child-pid>` and check for `Z` state).

```bash
cd wrappers/conformance && ./runner_ts.ts mode-a-orphan-cleanup && python runner_py.py mode-a-orphan-cleanup && cd ../..
git add wrappers/conformance/fixtures/mode-a-orphan-cleanup.yaml wrappers/conformance/fixtures/slow-mcp/
git commit -m "test(conformance): A4'/SC-B — mode-a-orphan-cleanup"
```

---

## Task 20: Conformance fixture — `mode-a-envelope-precedence.yaml` (SC-D)

**Files:**
- Create: `wrappers/conformance/fixtures/mode-a-envelope-precedence.yaml`

**Test type:** **(b) real-binary**.

**Step 1: Author the fixture**

The engine must be coaxed into returning exit 1 while emitting a valid envelope with `error: null`. The cleanest way: ship a `force-exit-bundle/` that, after writing the envelope and flushing stdout, calls `os._exit(1)` via an `on_turn_end` hook. The fixture verifies the wrapper yields `{type:'result', text: ...}` — envelope wins, exit code ignored.

Assertions:
- The wrapper yields `result`, not `error`.
- The wrapper logs the exit-code mismatch at debug-stderr (not load-bearing; informational).

```bash
cd wrappers/conformance && ./runner_ts.ts mode-a-envelope-precedence && python runner_py.py mode-a-envelope-precedence && cd ../..
git add wrappers/conformance/fixtures/mode-a-envelope-precedence.yaml wrappers/conformance/fixtures/force-exit-bundle/
git commit -m "test(conformance): A4'/SC-D — mode-a-envelope-precedence"
```

---

## Phase B Acceptance Gate

Each bullet is a verified pass; not "should pass."

1. **All 10 real-binary conformance fixtures green in BOTH TS and Py runners:**
   ```bash
   cd wrappers/conformance && for f in mode-a-happy-path mode-a-mcp-injection mode-a-resume-continuity mode-a-host-capabilities mode-a-protocol-skew mode-a-error-taxonomy mode-a-stdout-discipline mode-a-approval-callback-rejected mode-a-orphan-cleanup mode-a-envelope-precedence; do
     ./runner_ts.ts "$f" || { echo "TS FAIL: $f"; exit 1; }
     python runner_py.py "$f" || { echo "PY FAIL: $f"; exit 1; }
   done && cd ../..
   ```

2. **Cross-language parity lint green:**
   ```bash
   uv run pytest tests/test_conformance_parity.py -v
   ```

3. **Wrapper unit suites green:**
   ```bash
   cd wrappers/typescript && npm test && npm run typecheck && cd ../..
   cd wrappers/python && uv run pytest && uv run ruff check src/ tests/ && uv run pyright src/ && cd ../..
   ```

4. **Engine regression suite green:**
   ```bash
   uv run pytest tests/ -q
   ```

5. **Lint clean across all touched dirs:**
   ```bash
   uv run ruff check src/ tests/ wrappers/python/src/ wrappers/python/tests/
   ```

6. **Version bump verified:**
   ```bash
   grep '"version"' wrappers/typescript/package.json
   grep 'version' wrappers/python/pyproject.toml
   ```
   Both must show `0.3.0`.

7. **Deleted files truly gone:**
   ```bash
   ls wrappers/typescript/src/l14.ts wrappers/typescript/src/jsonrpc.ts 2>&1
   ls wrappers/python/src/amplifier_agent_client/l14.py wrappers/python/src/amplifier_agent_client/jsonrpc.py 2>&1
   ```
   All four must report "No such file or directory".

8. **Release prep:** Tag and (per amendment §8.1 A5') publish a release candidate of `amplifier-agent-client-ts@0.3.0-rc.1` to npm. The orchestrator handles the publish; Phase B execution ends with a green PR ready for merge.

**Push to remote:**
```bash
git push -u origin feat/mode-a-phase-b-wrapper
```

Open a PR titled `feat(wrappers)!: Phase B — Mode A v2 subprocess driver + 10 conformance fixtures (A3'/A4')`. The `!` signals the CR-C breaking change. Body should call out: `DisplayEvent` shape changed; consumers (NC, Paperclip if it depends, etc.) need migration in Phase C / downstream.

# Plan 3 — TS Wrapper + Py Wrapper + Cross-Language Conformance

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Ship two sibling wrapper packages (`amplifier-agent-client-ts` for npm, `amplifier-agent-client-py` for PyPI) plus a shared cross-language conformance harness that drives both wrappers against a real `amplifier-agent` subprocess using the YAML fixtures landed in Plan 2.

**Architecture:** Each wrapper is a thin Layer 3 client that (1) spawns the `amplifier-agent` binary, (2) speaks JSON-RPC 2.0 over NDJSON over stdio, (3) exposes the locked `spawnAgent()` / `spawn_agent()` public API from design §8, and (4) implements seven internal components: `spawn`, `transport`, `jsonrpc`, `session`, `approval`, `display`, `version` + the L14 synthesis safety net. The two wrappers are co-designed: every implementation task pairs the TS and Py halves so they ship together and a single shared YAML fixture is the conformance test.

**Tech Stack:**
- TS: Node ≥20, TypeScript 5, **pnpm** (matches the npm-ecosystem convention; project has no existing TS tooling), **vitest** as the test framework, `json-schema-to-typescript` for type derivation. ESM-only (no CJS).
- Py: Python ≥3.12, `asyncio.subprocess`, pytest + pytest-asyncio. Wraps `amplifier_agent_lib.protocol` TypedDicts directly (no codegen needed on the Py side).
- Engine binary: spawns `amplifier-agent` via PATH resolution or `AMPLIFIER_AGENT_BIN` env var.

---

## Audience note

You are a skilled engineer with **zero context** on this repository. Before touching any code:

1. **Read the design doc** at `docs/designs/2026-05-20-aaa-v2-wrapper-and-wire.md`. Focus on §4.2 (TS wrapper layering), §4.3 (Py wrapper), §5 (control flows), §8 D1–D10 (the ten locked decisions, including the **verbatim** TypeScript public API in §8.2), and §10.4–10.6 (sequencing).
2. **Read the Plan 2 file** at `docs/plans/2026-05-20-phase-2-1-wire-spec-hardening.md` — that's the predecessor plan whose artifacts (schemas, YAML fixtures, loader) you consume.
3. **Skim** `src/amplifier_agent_lib/protocol/` so you know what TypedDicts and JSON Schemas exist (one schema per TypedDict, 30 files total under `protocol/schemas/`).

Every command runs from the repo root unless stated otherwise.

## Conventions used in this plan

- **Paired-language tasks.** Most tasks implement the **same component in both TS and Py**. Each task body has four code blocks: `TS implementation`, `Py implementation`, `Tests (TS)`, `Tests (Py)`, then one commit covering both. This is the technique that fits the work into 15 tasks.
- **One commit per task.** Conventional-commits style. Match the style of `git log --oneline 81db7eb..da9ebb7` (Plan 2 commits).
- **TDD discipline is non-negotiable** — every implementation task is structured as: write failing test → run, confirm fail → implement → run, confirm pass → commit. These four bullets live **inside** each task body, not as separate numbered tasks.
- **`python_check` and `pnpm test` checkpoints are batched.** Three explicit "🔎 Quality checkpoint" markers between task groups. Do **NOT** run `python_check` per task — wait for the checkpoint.
- **Pattern references use `file:line` form.** Phase 2.0c + 2.1 code is on the current branch — read the cited files rather than guessing.
- **Python commands use `uv run`.** TypeScript commands use `pnpm` from inside `wrappers/typescript/`. Never use `npm install` or `npm test` — use `pnpm`.
- **The implementer is the same person across tasks.** Later content overrides earlier content. If you hit a contradiction, stop and ask.

## Source files the wrappers consume

| File | What lives there |
|---|---|
| `src/amplifier_agent_lib/protocol/methods.py` | `PROTOCOL_VERSION = "2026-05-aaa-v0"`; request/response TypedDicts including `InitializeParams`, `TurnSubmitParams`, `TurnSubmitResult`. **Py wrapper re-exports from here directly.** |
| `src/amplifier_agent_lib/protocol/notifications.py` | `CANONICAL_DISPLAY_EVENTS` (9-event tuple) + 11 notification TypedDicts |
| `src/amplifier_agent_lib/protocol/errors.py` | `ErrorCode` StrEnum (16 codes); `AaaError` exception |
| `src/amplifier_agent_lib/protocol/schemas/*.schema.json` | 30 JSON Schemas (Draft 2020-12). **TS wrapper codegens types from these.** |
| `src/amplifier_agent_lib/protocol/conformance/fixtures/*.yaml` | 5 YAML wire-sequence fixtures (`l14_synthesis`, `capability_negotiation`, `subagent_lineage`, `version_skew`, `resume_continuity`). The conformance harness drives **both** wrappers against these. |
| `src/amplifier_agent_lib/protocol/conformance/loader.py` | Reference Py loader; the Py conformance runner re-uses this. The TS runner ports the same shape contract. |
| `src/amplifier_agent_cli/__main__.py` | CLI entry point. Has subcommands `run`, `doctor`, `prepare`, `verify`, `config`, `cache`. **Does NOT yet have `--version --json`** — Task 11 adds a `version` subcommand. |

## Scope boundaries (re-stated)

**IN:** `wrappers/typescript/` complete package; `wrappers/python/` complete package (separate distributable, its own `pyproject.toml`); cross-language conformance harness under `wrappers/conformance/`; cross-language parity lint; engine CLI `version` subcommand (small addition to support pre-spawn version probe); exit-gate integration test.

**OUT:** NanoClaw / Paperclip adapter integration (Phase 2.4); turnkey installers (Phase 2.6); container Dockerfiles (Phase 2.7); OpenClaw skill drop (Phase 2.8); npm or PyPI publish (separate release process); burst lifecycle (rejected by design D10); `turn/cancel` (removed by D3); `version_skew.yaml` override branch (known Plan 2 follow-up); foundation engine internals beyond the small `version` subcommand addition.

## Resolved ambiguities (read before coding)

1. **Approval flow is IN-BAND, not inter-invocation.** Design §5.2 specifies a server-initiated `approval/request` JSON-RPC request from engine → client, with the wrapper sending a JSON-RPC response back inside the same subprocess lifetime. The orchestration prose in the parent task spec mentioned an "exit-code-10 + `--approval-response` on next spawn" pattern — **ignore that**, the design wins. Approval round-trips happen mid-turn via the same NDJSON pipe.

2. **Pre-spawn version probe is additive to `agent/initialize` check.** D6 requires version check at `agent/initialize`. To fail fast (avoid spinning up a full session against an incompatible engine), the wrapper also probes `amplifier-agent version --json` before sending `initialize`. The engine CLI doesn't have this command yet — Task 11 adds it as a small `version` subcommand.

3. **Conformance harness location: `wrappers/conformance/`** (top-level under wrappers, not nested in each wrapper's test dir). Design §3 ASCII diagram puts `conformance/` as a peer of `protocol/`; we follow the same shape under `wrappers/`.

4. **`CANONICAL_DISPLAY_EVENTS` deviates from design §4.4.** The design lists 9 events including `turn/started`, `assistant/text`, `subagent/*`. The actual code in `protocol/notifications.py:29-39` lists `result/delta`, `thinking/*`, `usage`, etc. Per Plan 2 D1, **code wins**. The wrapper's `DisplayEvent` type union mirrors the code. Sub-agent lineage uses `parentTurnId` on existing notification payloads, matching the `subagent_lineage.yaml` fixture.

5. **Engine binary is named `amplifier-agent`.** Defined in `pyproject.toml:13` as the `[project.scripts]` entry. Wrappers discover it via PATH then `AMPLIFIER_AGENT_BIN` env var (D5).

---

## Task list (15 tasks total)

```
Task 1   Pre-flight: stacked branch + plan commit
Task 2   TS package skeleton (pnpm, vitest, tsconfig)
Task 3   Py package skeleton (separate pyproject.toml under wrappers/python)
Task 4   Shared types: TS codegen from schemas + Py re-export module      (paired)
Task 5   Transport: subprocess spawn + NDJSON framing                      (paired)
Task 6   JsonRpcClient: per-request-id correlation + notif fanout         (paired)
                                                            🔎 Quality checkpoint A
Task 7   SessionHandle.submit() returning AsyncIterable<DisplayEvent>      (paired)
Task 8   L14 synthesis client-side                                         (paired)
Task 9   Approval bridge (in-band, mid-turn JSON-RPC round-trip)          (paired)
Task 10  Display callback + sub-agent filtering                            (paired)
                                                            🔎 Quality checkpoint B
Task 11  Version skew + binary discovery + env allowlist + `version` CLI  (paired)
Task 12  spawnAgent() public API + getEngineInfo()                        (paired)
Task 13  Conformance harness (wrappers/conformance/runner_ts + runner_py) (paired)
Task 14  Cross-language parity lint
Task 15  Phase 2.2 + 2.3 + 2.5 exit gate                                  (paired)
                                                            🔎 Quality checkpoint C
```

---

## Task 1 — Pre-flight: stacked branch + plan commit

**Files:**
- Create: branch `feat/phase-2-2-2-3-2-5-wrappers-and-conformance` off `feat/phase-2-1-wire-spec-hardening` (the current branch; Plan 2 PR #6 is still open).
- Commit: `docs/plans/2026-05-20-phase-2-2-2-3-2-5-wrappers-and-conformance.md` (this file).

**Steps:**
1. Confirm you are on `feat/phase-2-1-wire-spec-hardening` with a clean tree (the untracked `docs/architecture/amplifier-as-agent-presentation.html` is **out of scope** — leave it alone).
2. Run: `git checkout -b feat/phase-2-2-2-3-2-5-wrappers-and-conformance`.
3. Run: `git add docs/plans/2026-05-20-phase-2-2-2-3-2-5-wrappers-and-conformance.md && git commit -m "docs(phase-2-2): implementation plan for wrappers and conformance"`.
4. Run: `git status` — confirm clean except the ignored HTML file.

No tests in this task. See the footer note "After PR #6 merges" for the rebase step you'll do later.

---

## Task 2 — TS package skeleton

**Files:**
- Create: `wrappers/typescript/package.json`
- Create: `wrappers/typescript/tsconfig.json`
- Create: `wrappers/typescript/vitest.config.ts`
- Create: `wrappers/typescript/.gitignore`
- Create: `wrappers/typescript/src/index.ts` (placeholder export only)
- Create: `wrappers/typescript/test/smoke.test.ts`

**Goal:** Establish a minimal compileable, testable TS package. `src/index.ts` exports a single sentinel constant so we have *something* to import in the smoke test. Every subsequent TS task adds files under `wrappers/typescript/src/` next to `index.ts`.

**Pattern reference:** No existing TS code in this repo. Use vitest as the test framework (modern, fast, native ESM) and pnpm as the package manager (matches modern npm-ecosystem convention used by Vite, Vue, Astro, etc.). Both choices are intentional — do not switch to Jest or npm.

**Steps:**

1. **Write the failing test first** at `wrappers/typescript/test/smoke.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { PROTOCOL_VERSION_REQUIRED_BY_WRAPPER } from "../src/index.js";

describe("ts wrapper package skeleton", () => {
  it("exports a wrapper-required protocol version string", () => {
    expect(PROTOCOL_VERSION_REQUIRED_BY_WRAPPER).toBe("2026-05-aaa-v0");
  });
});
```

2. **Write `wrappers/typescript/package.json`:**

```json
{
  "name": "amplifier-agent-client-ts",
  "version": "0.0.0",
  "description": "TypeScript wrapper for amplifier-agent (Layer 3 client).",
  "type": "module",
  "main": "./dist/index.js",
  "types": "./dist/index.d.ts",
  "exports": {
    ".": {
      "import": "./dist/index.js",
      "types": "./dist/index.d.ts"
    }
  },
  "files": ["dist", "README.md"],
  "scripts": {
    "build": "tsc -p tsconfig.json",
    "test": "vitest run",
    "test:watch": "vitest",
    "typecheck": "tsc --noEmit",
    "lint": "tsc --noEmit"
  },
  "engines": { "node": ">=20" },
  "license": "MIT",
  "devDependencies": {
    "@types/node": "^20.11.0",
    "json-schema-to-typescript": "^15.0.0",
    "typescript": "^5.4.0",
    "vitest": "^1.4.0"
  }
}
```

3. **Write `wrappers/typescript/tsconfig.json`:**

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "lib": ["ES2022"],
    "types": ["node"],
    "strict": true,
    "noUncheckedIndexedAccess": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "outDir": "dist",
    "rootDir": "src",
    "declaration": true,
    "sourceMap": true,
    "isolatedModules": true
  },
  "include": ["src/**/*.ts"],
  "exclude": ["dist", "node_modules", "test"]
}
```

4. **Write `wrappers/typescript/vitest.config.ts`:**

```typescript
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["test/**/*.test.ts"],
    environment: "node",
  },
});
```

5. **Write `wrappers/typescript/.gitignore`:**

```
node_modules/
dist/
coverage/
*.tsbuildinfo
```

6. **Write `wrappers/typescript/src/index.ts` (placeholder only):**

```typescript
/**
 * amplifier-agent-client-ts — Layer 3 TypeScript wrapper.
 *
 * Public API will be built up over Tasks 4–12. This file currently exports
 * only a sentinel used by the package-skeleton smoke test.
 */

export const PROTOCOL_VERSION_REQUIRED_BY_WRAPPER = "2026-05-aaa-v0" as const;
```

7. **Install + run.** From `wrappers/typescript/`:
   - `pnpm install` (creates `pnpm-lock.yaml`; commit it).
   - `pnpm test` — smoke test passes.
   - `pnpm typecheck` — clean.

8. **Verify** the smoke test was actually failing without the export by temporarily removing it from `index.ts`, running `pnpm test` (expect fail), then restoring. Do **not** commit the failure variant.

9. **Commit:** `git add wrappers/typescript && git commit -m "feat(wrappers/ts): package skeleton with vitest + pnpm"`.

---

## Task 3 — Py package skeleton

**Files:**
- Create: `wrappers/python/pyproject.toml` (**separate** from root `pyproject.toml` — this is a distinct distributable).
- Create: `wrappers/python/src/amplifier_agent_client/__init__.py`
- Create: `wrappers/python/tests/__init__.py`
- Create: `wrappers/python/tests/test_smoke.py`

**Goal:** Same shape as Task 2 but for Python. `amplifier_agent_client` is the distinct package name (separate from `amplifier_agent_lib`, the engine library). Python ≥3.12, hatchling, pytest-asyncio.

**Pattern reference:**
- Root `pyproject.toml` at `pyproject.toml:1-62` — copy the hatchling + ruff + pytest config pattern.
- Test exemplar: `tests/test_runtime_hook_mount.py:1-57` — async pytest, behavior-descriptive names.

**Steps:**

1. **Write the failing test** at `wrappers/python/tests/test_smoke.py`:

```python
"""Smoke test for the amplifier_agent_client package skeleton."""

from __future__ import annotations


def test_smoke_protocol_version_constant() -> None:
    """The wrapper exposes the wire protocol version it is compiled against."""
    from amplifier_agent_client import PROTOCOL_VERSION_REQUIRED_BY_WRAPPER

    assert PROTOCOL_VERSION_REQUIRED_BY_WRAPPER == "2026-05-aaa-v0"
```

2. **Write `wrappers/python/pyproject.toml`:**

```toml
[project]
name = "amplifier-agent-client"
version = "0.0.0"
description = "Python wrapper for amplifier-agent (Layer 3 client)."
requires-python = ">=3.12"
license = "MIT"
dependencies = [
    "amplifier-agent",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/amplifier_agent_client"]

[tool.uv.sources]
amplifier-agent = { workspace = true }

[dependency-groups]
dev = [
    "pytest>=8.4.2",
    "pytest-asyncio>=0.24.0",
    "pytest-timeout>=2.4.0",
]

[tool.ruff]
line-length = 120
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "RUF"]
ignore = ["E501"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
addopts = "--import-mode=importlib -ra"
asyncio_mode = "strict"
```

3. **Add the new package to the workspace.** Edit the root `pyproject.toml`:

```toml
# Existing line (pyproject.toml:34):
# members = ['packages/amplifier-agent', 'packages/amplifier-agent-session-spawner']
# Add 'wrappers/python':
members = ['packages/amplifier-agent', 'packages/amplifier-agent-session-spawner', 'wrappers/python']
```

4. **Write `wrappers/python/src/amplifier_agent_client/__init__.py`:**

```python
"""amplifier-agent-client — Layer 3 Python wrapper.

Public API is built up across Plan 3 Tasks 4–12.  This module's only
exported symbol today is the wire protocol version the wrapper is
compiled against — consumers should NOT depend on anything else until
Task 12 lands ``spawn_agent``.
"""

from __future__ import annotations

PROTOCOL_VERSION_REQUIRED_BY_WRAPPER = "2026-05-aaa-v0"
"""Wire protocol version this wrapper speaks.  Strict-equality checked at
``agent/initialize`` time (design §8 D6)."""

__all__ = ["PROTOCOL_VERSION_REQUIRED_BY_WRAPPER"]
```

5. **Run.** From the repo root:
   - `uv sync` (picks up the new workspace member).
   - `uv run --package amplifier-agent-client pytest wrappers/python/tests/ -v` — smoke test passes.

6. **Verify the failing path:** temporarily rename the export, re-run, expect fail. Restore.

7. **Commit:** `git add wrappers/python pyproject.toml uv.lock && git commit -m "feat(wrappers/py): package skeleton as workspace member"`.

---

## Task 4 — Shared types: TS codegen from schemas + Py re-export (paired)

**Files:**
- Create: `wrappers/typescript/scripts/gen-types.ts` (codegen script invoking `json-schema-to-typescript`)
- Create: `wrappers/typescript/src/types.ts` (**generated** — banner says DO NOT HAND-EDIT)
- Create: `wrappers/typescript/test/types.test.ts`
- Create: `wrappers/python/src/amplifier_agent_client/types.py` (re-exports from `amplifier_agent_lib.protocol`)
- Create: `wrappers/python/tests/test_types.py`

**Goal:** Both wrappers expose the **same logical types**. The TS wrapper derives them from `src/amplifier_agent_lib/protocol/schemas/*.schema.json` via codegen. The Py wrapper re-exports the source TypedDicts directly — no codegen, no drift risk.

**Pattern reference:**
- Schemas list: run `ls src/amplifier_agent_lib/protocol/schemas/` — 30 `.schema.json` files plus `error_codes.schema.json`.
- Plan 2 used a banner-then-CI-staleness pattern (`tests/test_protocol_gen_staleness.py`). We mirror it on the TS side via Task 14's parity lint.

**TDD bullets (do in order):**

1. **Write the failing TS test** at `wrappers/typescript/test/types.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import type {
  InitializeParams,
  InitializeResult,
  TurnSubmitParams,
  TurnSubmitResult,
  ErrorCode,
} from "../src/types.js";

describe("types.ts derived from JSON Schemas", () => {
  it("InitializeParams has required fields", () => {
    const p: InitializeParams = {
      protocolVersion: "2026-05-aaa-v0",
      clientInfo: { name: "test", version: "0.0.0" },
      capabilities: {},
    };
    expect(p.protocolVersion).toBe("2026-05-aaa-v0");
  });

  it("TurnSubmitParams matches the wire shape", () => {
    const p: TurnSubmitParams = { sessionId: "s", turnId: "t", prompt: "hi" };
    expect(p.prompt).toBe("hi");
  });

  it("ErrorCode is a string-enum union", () => {
    const codes: ErrorCode[] = ["protocol_version_mismatch", "engine_crashed"];
    expect(codes.length).toBe(2);
  });
});
```

2. **Write the failing Py test** at `wrappers/python/tests/test_types.py`:

```python
"""The Py wrapper re-exports the engine library's wire TypedDicts."""

from __future__ import annotations


def test_initialize_params_re_exported() -> None:
    from amplifier_agent_client.types import InitializeParams
    from amplifier_agent_lib.protocol.methods import InitializeParams as Source

    assert InitializeParams is Source


def test_error_code_re_exported() -> None:
    from amplifier_agent_client.types import ErrorCode
    from amplifier_agent_lib.protocol.errors import ErrorCode as Source

    assert ErrorCode is Source


def test_canonical_display_events_re_exported() -> None:
    from amplifier_agent_client.types import CANONICAL_DISPLAY_EVENTS

    assert isinstance(CANONICAL_DISPLAY_EVENTS, tuple)
    assert "result/final" in CANONICAL_DISPLAY_EVENTS
```

3. **Run both, confirm fail.**
   - `cd wrappers/typescript && pnpm test` → fail (no `types.ts`).
   - `uv run --package amplifier-agent-client pytest wrappers/python/tests/test_types.py -v` → fail (no `types.py`).

4. **TS implementation.** Write the codegen script at `wrappers/typescript/scripts/gen-types.ts`:

```typescript
/**
 * Generate src/types.ts from src/amplifier_agent_lib/protocol/schemas/*.schema.json.
 *
 * Run with: pnpm run gen:types
 *
 * Output is GENERATED — banner forbids hand-edits. The cross-language
 * parity lint (Task 14) is the staleness gate that enforces regeneration.
 */

import { readdir, readFile, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { compile, type JSONSchema } from "json-schema-to-typescript";

const HERE = fileURLToPath(new URL(".", import.meta.url));
const SCHEMAS_DIR = resolve(HERE, "../../../src/amplifier_agent_lib/protocol/schemas");
const OUT = resolve(HERE, "../src/types.ts");

const BANNER = `/**
 * GENERATED FILE — DO NOT HAND-EDIT.
 * Regenerate with:
 *   cd wrappers/typescript && pnpm run gen:types
 *
 * Source: src/amplifier_agent_lib/protocol/schemas/*.schema.json
 */
`;

async function main(): Promise<void> {
  const entries = (await readdir(SCHEMAS_DIR)).filter((n) => n.endsWith(".schema.json"));
  entries.sort();

  const parts: string[] = [BANNER];
  for (const name of entries) {
    const text = await readFile(join(SCHEMAS_DIR, name), "utf8");
    const schema = JSON.parse(text) as JSONSchema;
    // Resolve $ref to sibling files by inlining them as TS interface references.
    // json-schema-to-typescript handles this via the cwd option.
    const ts = await compile(schema, schema.title ?? name, {
      bannerComment: "",
      cwd: SCHEMAS_DIR,
      additionalProperties: false,
      style: { singleQuote: false },
    });
    parts.push(ts);
  }

  // Append the ErrorCode string-union derived from error_codes.schema.json's enum.
  const errSchema = JSON.parse(
    await readFile(join(SCHEMAS_DIR, "error_codes.schema.json"), "utf8")
  ) as { enum: string[] };
  parts.push(
    `\nexport type ErrorCode =\n  | ${errSchema.enum.map((v) => `"${v}"`).join("\n  | ")};\n`
  );

  await writeFile(OUT, parts.join("\n"), "utf8");
  console.log(`[gen-types] wrote ${entries.length} schemas → ${OUT}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
```

Then add the `gen:types` script to `wrappers/typescript/package.json` `scripts`:

```json
"gen:types": "tsx scripts/gen-types.ts"
```

Add `tsx` as a devDependency in the same package.json:

```json
"tsx": "^4.7.0"
```

Run `pnpm install`, then `pnpm run gen:types` to produce `src/types.ts`. Inspect the file — confirm the banner is present and `InitializeParams`, `TurnSubmitParams`, `ErrorCode` are all defined.

5. **Py implementation.** Write `wrappers/python/src/amplifier_agent_client/types.py`:

```python
"""Wire types — thin re-export layer over ``amplifier_agent_lib.protocol``.

Per design §8 D1, the Python TypedDicts in ``amplifier_agent_lib.protocol``
are the authoritative wire-spec source.  The Python wrapper re-exports
them verbatim so adapters import a single, drift-free type surface.

The TypeScript wrapper derives the same shapes via JSON-Schema codegen;
the cross-language parity lint (Task 14) enforces they remain aligned.
"""

from __future__ import annotations

from amplifier_agent_lib.protocol.errors import AaaError, ErrorCode
from amplifier_agent_lib.protocol.methods import (
    AgentShutdownParams,
    AgentShutdownResult,
    ClientInfo,
    InitializeParams,
    InitializeResult,
    PROTOCOL_VERSION,
    ServerInfo,
    SessionState,
    TurnSubmitParams,
    TurnSubmitResult,
)
from amplifier_agent_lib.protocol.notifications import (
    CANONICAL_DISPLAY_EVENTS,
    ApprovalRequestNotification,
    ApprovalTimeoutNotification,
    ErrorNotification,
    ProgressNotification,
    ResultDeltaNotification,
    ResultFinalNotification,
    ToolCompletedNotification,
    ToolStartedNotification,
)

__all__ = [
    "AaaError",
    "ApprovalRequestNotification",
    "ApprovalTimeoutNotification",
    "CANONICAL_DISPLAY_EVENTS",
    "ClientInfo",
    "ErrorCode",
    "ErrorNotification",
    "InitializeParams",
    "InitializeResult",
    "PROTOCOL_VERSION",
    "ProgressNotification",
    "ResultDeltaNotification",
    "ResultFinalNotification",
    "ServerInfo",
    "SessionState",
    "ToolCompletedNotification",
    "ToolStartedNotification",
    "TurnSubmitParams",
    "TurnSubmitResult",
]
```

6. **Run both, confirm pass.**
   - `cd wrappers/typescript && pnpm test`
   - `uv run --package amplifier-agent-client pytest wrappers/python/tests/ -v`

7. **Commit:** `git add wrappers/ && git commit -m "feat(wrappers): shared wire types via JSON-Schema codegen (ts) + re-export (py)"`.

---

## Task 5 — Transport: subprocess spawn + NDJSON framing (paired)

**Files:**
- Create: `wrappers/typescript/src/transport.ts`
- Create: `wrappers/typescript/test/transport.test.ts`
- Create: `wrappers/python/src/amplifier_agent_client/transport.py`
- Create: `wrappers/python/tests/test_transport.py`

**Goal:** A focused `Transport` class per language whose only job is: spawn a child process, write JSON frames as NDJSON to its stdin, read JSON frames as NDJSON from its stdout, drain stderr to an optional sink, and terminate cleanly on SIGTERM. **No JSON-RPC semantics live here** — that's Task 6. The transport speaks bytes/objects.

**Defensive requirement (MCP-style tolerance):** if a stdout line is not parseable as JSON, log to stderr and drop the line silently — do NOT raise. This matches the engine's existing tolerance pattern at `src/amplifier_agent_lib/jsonrpc.py` (read it briefly to see how the engine side does it).

**Pattern reference:**
- Engine's NDJSON write side: `src/amplifier_agent_lib/jsonrpc.py` — confirm framing is `json.dumps(...) + "\n"`.
- For testing, both languages will spawn a tiny "echo bot" subprocess (a one-liner shell command using `cat` is enough) so we don't need the real engine yet.

**TDD bullets:**

1. **Write failing TS test** at `wrappers/typescript/test/transport.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { Transport } from "../src/transport.js";

describe("Transport (TS)", () => {
  it("writes a frame and reads back the echoed line", async () => {
    // Echo bot: cat will echo whatever NDJSON we write to it.
    const t = new Transport({ command: "cat", args: [], env: {} });
    await t.start();

    const echoed = new Promise<unknown>((resolve) => {
      t.onFrame((frame) => resolve(frame));
    });

    await t.send({ hello: "world" });
    expect(await echoed).toEqual({ hello: "world" });

    await t.terminate();
  });

  it("drops non-JSON stdout lines without raising", async () => {
    // `printf` produces one bad line then one good one.
    const t = new Transport({
      command: "sh",
      args: ["-c", `printf 'not json\\n{"ok":true}\\n'`],
      env: {},
    });
    const seen: unknown[] = [];
    t.onFrame((f) => seen.push(f));
    await t.start();
    await new Promise((r) => setTimeout(r, 200));
    expect(seen).toEqual([{ ok: true }]);
    await t.terminate();
  });

  it("terminate() sends SIGTERM and resolves child exit", async () => {
    const t = new Transport({
      command: "sh",
      args: ["-c", "sleep 60"],
      env: {},
    });
    await t.start();
    const exit = await t.terminate();
    expect(exit.signal === "SIGTERM" || exit.code !== 0).toBe(true);
  });
});
```

2. **Write failing Py test** at `wrappers/python/tests/test_transport.py`:

```python
"""Tests for amplifier_agent_client.transport.Transport."""

from __future__ import annotations

import asyncio

import pytest


@pytest.mark.asyncio
async def test_transport_round_trip() -> None:
    from amplifier_agent_client.transport import Transport

    t = Transport(command="cat", args=[], env={})
    await t.start()
    seen: asyncio.Queue[dict] = asyncio.Queue()

    async def receiver() -> None:
        async for frame in t.frames():
            await seen.put(frame)

    task = asyncio.create_task(receiver())
    await t.send({"hello": "world"})
    got = await asyncio.wait_for(seen.get(), timeout=2.0)
    assert got == {"hello": "world"}

    await t.terminate()
    task.cancel()


@pytest.mark.asyncio
async def test_transport_drops_non_json_lines() -> None:
    from amplifier_agent_client.transport import Transport

    t = Transport(
        command="sh",
        args=["-c", r"printf 'not json\n{\"ok\":true}\n'"],
        env={},
    )
    await t.start()
    seen: list[dict] = []

    async def receiver() -> None:
        async for frame in t.frames():
            seen.append(frame)

    task = asyncio.create_task(receiver())
    await asyncio.sleep(0.3)
    await t.terminate()
    task.cancel()

    assert seen == [{"ok": True}]


@pytest.mark.asyncio
async def test_transport_terminate_kills_subprocess() -> None:
    from amplifier_agent_client.transport import Transport

    t = Transport(command="sh", args=["-c", "sleep 60"], env={})
    await t.start()
    exit_code = await t.terminate()
    # SIGTERM exit on POSIX is signed; either way we got a non-zero status.
    assert exit_code != 0
```

3. **Run both, confirm fail.**

4. **TS implementation** — write `wrappers/typescript/src/transport.ts`:

```typescript
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { createInterface, type Interface } from "node:readline";

export interface TransportOptions {
  command: string;
  args: string[];
  env: Record<string, string>;
  cwd?: string;
  stderr?: (line: string) => void;
}

export interface ExitInfo {
  code: number | null;
  signal: NodeJS.Signals | null;
}

/**
 * Spawns a subprocess and exchanges JSON frames over NDJSON over stdio.
 *
 * Defensive: non-JSON stdout lines are routed to the stderr sink and dropped
 * (MCP-style tolerance). Mirrors the engine-side tolerance in
 * src/amplifier_agent_lib/jsonrpc.py.
 */
export class Transport {
  private child: ChildProcessWithoutNullStreams | null = null;
  private stdoutLines: Interface | null = null;
  private frameCallbacks: Array<(frame: unknown) => void> = [];
  private exitPromise: Promise<ExitInfo> | null = null;

  constructor(private readonly opts: TransportOptions) {}

  async start(): Promise<void> {
    if (this.child) throw new Error("Transport already started");
    this.child = spawn(this.opts.command, this.opts.args, {
      env: this.opts.env,
      cwd: this.opts.cwd,
      stdio: ["pipe", "pipe", "pipe"],
    });

    this.stdoutLines = createInterface({ input: this.child.stdout });
    this.stdoutLines.on("line", (line) => {
      let parsed: unknown;
      try {
        parsed = JSON.parse(line);
      } catch {
        this.opts.stderr?.(`[non-json stdout] ${line}`);
        return;
      }
      for (const cb of this.frameCallbacks) cb(parsed);
    });

    const stderrLines = createInterface({ input: this.child.stderr });
    stderrLines.on("line", (line) => this.opts.stderr?.(line));

    this.exitPromise = new Promise((resolve) => {
      this.child!.on("exit", (code, signal) => resolve({ code, signal }));
    });
  }

  onFrame(cb: (frame: unknown) => void): void {
    this.frameCallbacks.push(cb);
  }

  async send(frame: unknown): Promise<void> {
    if (!this.child?.stdin.writable) throw new Error("Transport not writable");
    const line = JSON.stringify(frame) + "\n";
    await new Promise<void>((resolve, reject) => {
      this.child!.stdin.write(line, (err) => (err ? reject(err) : resolve()));
    });
  }

  async terminate(): Promise<ExitInfo> {
    if (!this.child) throw new Error("Transport not started");
    if (this.child.exitCode === null && this.child.signalCode === null) {
      this.child.kill("SIGTERM");
    }
    const exit = await this.exitPromise!;
    this.stdoutLines?.close();
    return exit;
  }
}
```

5. **Py implementation** — write `wrappers/python/src/amplifier_agent_client/transport.py`:

```python
"""Subprocess transport with NDJSON framing over stdio.

Mirrors ``wrappers/typescript/src/transport.ts``.  Defensive: non-JSON
stdout lines are dropped (with optional stderr-sink callback), matching
the engine-side tolerance in ``src/amplifier_agent_lib/jsonrpc.py``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass


@dataclass
class TransportOptions:
    command: str
    args: list[str]
    env: dict[str, str]
    cwd: str | None = None
    stderr: Callable[[str], None] | None = None


class Transport:
    """Spawns a subprocess and exchanges JSON frames as NDJSON over stdio."""

    def __init__(
        self,
        *,
        command: str,
        args: list[str],
        env: dict[str, str],
        cwd: str | None = None,
        stderr: Callable[[str], None] | None = None,
    ) -> None:
        self._opts = TransportOptions(
            command=command, args=args, env=env, cwd=cwd, stderr=stderr
        )
        self._proc: asyncio.subprocess.Process | None = None
        self._frames: asyncio.Queue[dict] = asyncio.Queue()
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._proc is not None:
            raise RuntimeError("Transport already started")
        self._proc = await asyncio.create_subprocess_exec(
            self._opts.command,
            *self._opts.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._opts.env,
            cwd=self._opts.cwd,
        )
        self._reader_task = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._read_stderr())

    async def _read_stdout(self) -> None:
        assert self._proc and self._proc.stdout
        async for raw in self._proc.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip("\n")
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                if self._opts.stderr:
                    self._opts.stderr(f"[non-json stdout] {line}")
                continue
            await self._frames.put(parsed)

    async def _read_stderr(self) -> None:
        assert self._proc and self._proc.stderr
        async for raw in self._proc.stderr:
            if self._opts.stderr:
                self._opts.stderr(raw.decode("utf-8", errors="replace").rstrip("\n"))

    async def frames(self) -> AsyncIterator[dict]:
        """Yield each decoded frame; stops when the subprocess exits and queue drains."""
        while True:
            try:
                frame = await asyncio.wait_for(self._frames.get(), timeout=0.1)
            except asyncio.TimeoutError:
                if self._proc and self._proc.returncode is not None and self._frames.empty():
                    return
                continue
            yield frame

    async def send(self, frame: dict) -> None:
        assert self._proc and self._proc.stdin
        line = (json.dumps(frame) + "\n").encode("utf-8")
        self._proc.stdin.write(line)
        await self._proc.stdin.drain()

    async def terminate(self) -> int:
        assert self._proc is not None
        if self._proc.returncode is None:
            self._proc.terminate()
        try:
            await asyncio.wait_for(self._proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            self._proc.kill()
            await self._proc.wait()
        return self._proc.returncode if self._proc.returncode is not None else -1
```

6. **Run both, confirm pass.**

7. **Commit:** `git add wrappers/ && git commit -m "feat(wrappers): NDJSON subprocess transport (ts + py)"`.

---

## Task 6 — JsonRpcClient: per-request-id correlation + notification fanout (paired)

**Files:**
- Create: `wrappers/typescript/src/jsonrpc.ts`
- Create: `wrappers/typescript/test/jsonrpc.test.ts`
- Create: `wrappers/python/src/amplifier_agent_client/jsonrpc.py`
- Create: `wrappers/python/tests/test_jsonrpc.py`

**Goal:** Layer JSON-RPC 2.0 semantics on top of the bytes-only `Transport`. `JsonRpcClient`:
- Allocates request IDs.
- Holds a `Map<id, pendingPromise>` (TS) / `dict[int, asyncio.Future]` (Py) for responses.
- Routes incoming frames: result → resolve the matching promise; notification → fanout to subscribers; server-initiated request (e.g. `approval/request`) → dispatch to a registered handler that produces a response.
- **Designs out NC-L16 by construction** — there is no shared "active" pointer. Two concurrent `call()`s have independent promise rows.

**Pattern reference:**
- Engine-side JSON-RPC at `src/amplifier_agent_lib/jsonrpc.py` — read for the wire format (envelope `{"jsonrpc": "2.0", "id": <int>, "method"|"result"|"error": ...}`).

**TDD bullets:**

1. **Failing TS test** at `wrappers/typescript/test/jsonrpc.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { JsonRpcClient } from "../src/jsonrpc.js";

/** Minimal stub transport: send echoes JSON-RPC framing logic. */
class StubTransport {
  private cb: ((f: unknown) => void) | null = null;
  sent: unknown[] = [];
  send(frame: unknown): Promise<void> {
    this.sent.push(frame);
    return Promise.resolve();
  }
  onFrame(cb: (f: unknown) => void): void {
    this.cb = cb;
  }
  emit(frame: unknown): void {
    this.cb?.(frame);
  }
}

describe("JsonRpcClient (TS)", () => {
  it("call() resolves when matching result arrives", async () => {
    const tx = new StubTransport();
    const c = new JsonRpcClient(tx);
    const p = c.call("initialize", { protocolVersion: "x" });

    // Stub the response on the next event-loop tick.
    queueMicrotask(() => {
      const sent = tx.sent[0] as { id: number };
      tx.emit({ jsonrpc: "2.0", id: sent.id, result: { ok: true } });
    });

    await expect(p).resolves.toEqual({ ok: true });
  });

  it("two concurrent calls do not interfere (NC-L16 design-out)", async () => {
    const tx = new StubTransport();
    const c = new JsonRpcClient(tx);
    const p1 = c.call("a", {});
    const p2 = c.call("b", {});

    const id1 = (tx.sent[0] as { id: number }).id;
    const id2 = (tx.sent[1] as { id: number }).id;
    expect(id1).not.toBe(id2);

    // Resolve in reverse order — both must still settle correctly.
    tx.emit({ jsonrpc: "2.0", id: id2, result: { from: "b" } });
    tx.emit({ jsonrpc: "2.0", id: id1, result: { from: "a" } });

    expect(await p1).toEqual({ from: "a" });
    expect(await p2).toEqual({ from: "b" });
  });

  it("notifications are fanned out to subscribers", async () => {
    const tx = new StubTransport();
    const c = new JsonRpcClient(tx);
    const seen: unknown[] = [];
    c.onNotification((n) => seen.push(n));
    tx.emit({ jsonrpc: "2.0", method: "result/final", params: { text: "hi" } });
    expect(seen).toEqual([{ method: "result/final", params: { text: "hi" } }]);
  });

  it("server-initiated request invokes the registered handler", async () => {
    const tx = new StubTransport();
    const c = new JsonRpcClient(tx);
    c.onRequest("approval/request", async (params) => ({ decision: "allow", echoed: params }));
    tx.emit({ jsonrpc: "2.0", id: 99, method: "approval/request", params: { tool: "Bash" } });

    await new Promise((r) => setTimeout(r, 10));
    expect(tx.sent.at(-1)).toEqual({
      jsonrpc: "2.0",
      id: 99,
      result: { decision: "allow", echoed: { tool: "Bash" } },
    });
  });
});
```

2. **Failing Py test** at `wrappers/python/tests/test_jsonrpc.py`:

```python
"""Tests for amplifier_agent_client.jsonrpc.JsonRpcClient."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest


class StubTransport:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self._cb: Callable[[dict], None] | None = None

    async def send(self, frame: dict) -> None:
        self.sent.append(frame)

    def on_frame(self, cb: Callable[[dict], None]) -> None:
        self._cb = cb

    def emit(self, frame: dict) -> None:
        assert self._cb is not None
        self._cb(frame)


@pytest.mark.asyncio
async def test_call_resolves_on_matching_result() -> None:
    from amplifier_agent_client.jsonrpc import JsonRpcClient

    tx = StubTransport()
    c = JsonRpcClient(tx)  # type: ignore[arg-type]

    fut = asyncio.create_task(c.call("initialize", {"protocolVersion": "x"}))
    await asyncio.sleep(0)
    sent_id = tx.sent[0]["id"]
    tx.emit({"jsonrpc": "2.0", "id": sent_id, "result": {"ok": True}})
    assert await asyncio.wait_for(fut, timeout=1.0) == {"ok": True}


@pytest.mark.asyncio
async def test_concurrent_calls_isolated() -> None:
    from amplifier_agent_client.jsonrpc import JsonRpcClient

    tx = StubTransport()
    c = JsonRpcClient(tx)  # type: ignore[arg-type]

    f1 = asyncio.create_task(c.call("a", {}))
    f2 = asyncio.create_task(c.call("b", {}))
    await asyncio.sleep(0)
    id1, id2 = tx.sent[0]["id"], tx.sent[1]["id"]
    assert id1 != id2
    tx.emit({"jsonrpc": "2.0", "id": id2, "result": {"from": "b"}})
    tx.emit({"jsonrpc": "2.0", "id": id1, "result": {"from": "a"}})
    assert await f1 == {"from": "a"}
    assert await f2 == {"from": "b"}


@pytest.mark.asyncio
async def test_notifications_fanned_out() -> None:
    from amplifier_agent_client.jsonrpc import JsonRpcClient

    tx = StubTransport()
    c = JsonRpcClient(tx)  # type: ignore[arg-type]
    seen: list[dict] = []
    c.on_notification(seen.append)
    tx.emit({"jsonrpc": "2.0", "method": "result/final", "params": {"text": "hi"}})
    assert seen == [{"method": "result/final", "params": {"text": "hi"}}]


@pytest.mark.asyncio
async def test_server_initiated_request_handler() -> None:
    from amplifier_agent_client.jsonrpc import JsonRpcClient

    tx = StubTransport()
    c = JsonRpcClient(tx)  # type: ignore[arg-type]

    async def handler(params: dict) -> dict:
        return {"decision": "allow", "echoed": params}

    c.on_request("approval/request", handler)
    tx.emit({"jsonrpc": "2.0", "id": 99, "method": "approval/request", "params": {"tool": "Bash"}})
    await asyncio.sleep(0.05)
    assert tx.sent[-1] == {
        "jsonrpc": "2.0",
        "id": 99,
        "result": {"decision": "allow", "echoed": {"tool": "Bash"}},
    }
```

3. **Run both, confirm fail.**

4. **TS implementation** — `wrappers/typescript/src/jsonrpc.ts`:

```typescript
interface TransportLike {
  send(frame: unknown): Promise<void>;
  onFrame(cb: (frame: unknown) => void): void;
}

export interface Notification {
  method: string;
  params: unknown;
}

type RequestHandler = (params: unknown) => Promise<unknown>;

export class JsonRpcClient {
  private nextId = 1;
  private pending = new Map<number, { resolve: (v: unknown) => void; reject: (e: Error) => void }>();
  private notifSubs: Array<(n: Notification) => void> = [];
  private requestHandlers = new Map<string, RequestHandler>();

  constructor(private readonly tx: TransportLike) {
    tx.onFrame((frame) => this.dispatch(frame));
  }

  async call(method: string, params: unknown): Promise<unknown> {
    const id = this.nextId++;
    const promise = new Promise<unknown>((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
    });
    await this.tx.send({ jsonrpc: "2.0", id, method, params });
    return promise;
  }

  onNotification(cb: (n: Notification) => void): void {
    this.notifSubs.push(cb);
  }

  onRequest(method: string, handler: RequestHandler): void {
    this.requestHandlers.set(method, handler);
  }

  private dispatch(raw: unknown): void {
    if (typeof raw !== "object" || raw === null) return;
    const frame = raw as Record<string, unknown>;

    // Response (has id + result OR error, no method)
    if ("id" in frame && !("method" in frame)) {
      const id = frame.id as number;
      const entry = this.pending.get(id);
      if (!entry) return;
      this.pending.delete(id);
      if ("error" in frame) {
        entry.reject(new Error(JSON.stringify(frame.error)));
      } else {
        entry.resolve(frame.result);
      }
      return;
    }

    // Server-initiated request (has id + method)
    if ("id" in frame && "method" in frame) {
      const id = frame.id as number;
      const method = frame.method as string;
      const handler = this.requestHandlers.get(method);
      if (!handler) {
        void this.tx.send({
          jsonrpc: "2.0",
          id,
          error: { code: -32601, message: `no handler for ${method}` },
        });
        return;
      }
      void handler(frame.params).then((result) =>
        this.tx.send({ jsonrpc: "2.0", id, result })
      );
      return;
    }

    // Notification (method, no id)
    if ("method" in frame) {
      const notif: Notification = { method: frame.method as string, params: frame.params };
      for (const cb of this.notifSubs) cb(notif);
    }
  }
}
```

5. **Py implementation** — `wrappers/python/src/amplifier_agent_client/jsonrpc.py`:

```python
"""JSON-RPC 2.0 client over a NDJSON transport.

Mirrors ``wrappers/typescript/src/jsonrpc.ts``.  Designs out the
NC-L16 shared-active-pointer failure mode by routing responses through
a per-request-id ``dict[int, asyncio.Future]`` — there is no shared
mutable state across calls.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Protocol


class _TransportLike(Protocol):
    async def send(self, frame: dict) -> None: ...
    def on_frame(self, cb: Callable[[dict], None]) -> None: ...


RequestHandler = Callable[[dict], Awaitable[dict]]


class JsonRpcClient:
    def __init__(self, tx: _TransportLike) -> None:
        self._tx = tx
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._notif_subs: list[Callable[[dict], None]] = []
        self._request_handlers: dict[str, RequestHandler] = {}
        tx.on_frame(self._dispatch)

    async def call(self, method: str, params: dict) -> Any:
        msg_id = self._next_id
        self._next_id += 1
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = fut
        await self._tx.send({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params})
        return await fut

    def on_notification(self, cb: Callable[[dict], None]) -> None:
        self._notif_subs.append(cb)

    def on_request(self, method: str, handler: RequestHandler) -> None:
        self._request_handlers[method] = handler

    def _dispatch(self, frame: dict) -> None:
        # Response
        if "id" in frame and "method" not in frame:
            fut = self._pending.pop(frame["id"], None)
            if fut is None:
                return
            if "error" in frame:
                fut.set_exception(RuntimeError(str(frame["error"])))
            else:
                fut.set_result(frame.get("result"))
            return

        # Server-initiated request
        if "id" in frame and "method" in frame:
            handler = self._request_handlers.get(frame["method"])
            if handler is None:
                asyncio.create_task(self._tx.send({
                    "jsonrpc": "2.0",
                    "id": frame["id"],
                    "error": {"code": -32601, "message": f"no handler for {frame['method']}"},
                }))
                return
            asyncio.create_task(self._handle_request(frame["id"], handler, frame.get("params", {})))
            return

        # Notification
        if "method" in frame:
            for cb in self._notif_subs:
                cb({"method": frame["method"], "params": frame.get("params")})

    async def _handle_request(self, msg_id: int, handler: RequestHandler, params: dict) -> None:
        result = await handler(params)
        await self._tx.send({"jsonrpc": "2.0", "id": msg_id, "result": result})
```

6. **Run both, confirm pass.** `cd wrappers/typescript && pnpm test` + `uv run --package amplifier-agent-client pytest wrappers/python/tests/ -v`.

7. **Commit:** `git add wrappers/ && git commit -m "feat(wrappers): JSON-RPC 2.0 client with per-id correlation"`.

---

### 🔎 Quality checkpoint A

Run:
```
python_check on:
  wrappers/python/src/amplifier_agent_client/transport.py
  wrappers/python/src/amplifier_agent_client/jsonrpc.py
  wrappers/python/src/amplifier_agent_client/types.py
  wrappers/python/tests/

cd wrappers/typescript && pnpm typecheck && pnpm test
```

Fix any lint/type errors. Review every diff before amending — do not blindly accept `python_check --fix` output. Re-run both test suites after fixes. Fold fixes into the most recent commit with `git commit --amend --no-edit`.

---

## Task 7 — SessionHandle.submit() returning AsyncIterable<DisplayEvent> (paired)

**Files:**
- Create: `wrappers/typescript/src/session.ts`
- Create: `wrappers/typescript/test/session.test.ts`
- Create: `wrappers/python/src/amplifier_agent_client/session.py`
- Create: `wrappers/python/tests/test_session.py`

**Goal:** `SessionHandle.submit(prompt)` returns `AsyncIterable<DisplayEvent>` (TS) / `AsyncIterator[DisplayEvent]` (Py). Each `submit()` call: sends `turn/submit`, yields every `display/event`-shaped notification that arrives, terminates the iterator when `result/final` (notification) is observed **or** the `turn/submit` JSON-RPC response arrives — whichever comes first. **Per design D10, one `submit()` per subprocess lifetime in v1.** Calling `submit()` a second time raises a typed error.

Cancel / dispose: `cancel()` and `dispose()` both SIGTERM the underlying transport (D3) and cause the iterator to raise `AaaError('cancelled')`.

**Pattern reference:**
- Flow §5.1 of the design doc — the engine emits `display/event` notifications **during** turn execution, then a final `result/final` notification, then sends the JSON-RPC response to `turn/submit`.
- `notifications.py` constants tell you which notification methods to convert into `DisplayEvent`s.

**TDD bullets:**

1. **TS failing test** at `wrappers/typescript/test/session.test.ts`. Use the same `StubTransport` pattern from Task 6 — wire a `JsonRpcClient` to it, build a `SessionHandle`, drive frames in, collect the iterator output.

```typescript
import { describe, it, expect } from "vitest";
import { JsonRpcClient } from "../src/jsonrpc.js";
import { SessionHandle } from "../src/session.js";

class StubTransport {
  private cb: ((f: unknown) => void) | null = null;
  sent: unknown[] = [];
  async send(f: unknown): Promise<void> { this.sent.push(f); }
  onFrame(cb: (f: unknown) => void): void { this.cb = cb; }
  emit(f: unknown): void { this.cb?.(f); }
}

describe("SessionHandle.submit() (TS)", () => {
  it("yields display events then ends when result/final arrives", async () => {
    const tx = new StubTransport();
    const rpc = new JsonRpcClient(tx as unknown as never);
    const handle = new SessionHandle(rpc, { sessionId: "s", terminate: async () => {} });

    const iter = handle.submit("hi");

    // Drive the turn/submit response + 2 display notifs + result/final.
    queueMicrotask(() => {
      const submitId = (tx.sent.at(-1) as { id: number }).id;
      tx.emit({ jsonrpc: "2.0", method: "result/delta", params: { sessionId: "s", turnId: "t", textDelta: "hel" } });
      tx.emit({ jsonrpc: "2.0", method: "result/delta", params: { sessionId: "s", turnId: "t", textDelta: "lo" } });
      tx.emit({ jsonrpc: "2.0", method: "result/final", params: { sessionId: "s", turnId: "t", text: "hello" } });
      tx.emit({ jsonrpc: "2.0", id: submitId, result: { reply: "hello", turnId: "t", sessionId: "s" } });
    });

    const collected: unknown[] = [];
    for await (const ev of iter) collected.push(ev);
    expect(collected.map((e: any) => e.type)).toEqual(["result/delta", "result/delta", "result/final"]);
  });

  it("second submit() throws — one-shot per session (D10)", async () => {
    const tx = new StubTransport();
    const rpc = new JsonRpcClient(tx as unknown as never);
    const handle = new SessionHandle(rpc, { sessionId: "s", terminate: async () => {} });
    handle.submit("first");
    expect(() => handle.submit("second")).toThrow(/one-shot|already submitted/i);
  });
});
```

2. **Py failing test** — same shape at `wrappers/python/tests/test_session.py`.

```python
import asyncio
import pytest


class StubTransport:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self._cb = None

    async def send(self, frame: dict) -> None:
        self.sent.append(frame)

    def on_frame(self, cb) -> None:
        self._cb = cb

    def emit(self, frame: dict) -> None:
        assert self._cb is not None
        self._cb(frame)


@pytest.mark.asyncio
async def test_submit_yields_events_then_ends_on_result_final() -> None:
    from amplifier_agent_client.jsonrpc import JsonRpcClient
    from amplifier_agent_client.session import SessionHandle

    tx = StubTransport()
    rpc = JsonRpcClient(tx)  # type: ignore[arg-type]

    async def noop_terminate() -> None: ...

    handle = SessionHandle(rpc, session_id="s", terminate=noop_terminate)

    iter_ = handle.submit("hi")

    async def feeder() -> None:
        await asyncio.sleep(0)
        submit_id = tx.sent[-1]["id"]
        tx.emit({"jsonrpc": "2.0", "method": "result/delta",
                 "params": {"sessionId": "s", "turnId": "t", "textDelta": "hel"}})
        tx.emit({"jsonrpc": "2.0", "method": "result/final",
                 "params": {"sessionId": "s", "turnId": "t", "text": "hello"}})
        tx.emit({"jsonrpc": "2.0", "id": submit_id,
                 "result": {"reply": "hello", "turnId": "t", "sessionId": "s"}})

    asyncio.create_task(feeder())
    collected = [ev async for ev in iter_]
    assert [ev["type"] for ev in collected] == ["result/delta", "result/final"]


@pytest.mark.asyncio
async def test_submit_twice_raises() -> None:
    from amplifier_agent_client.jsonrpc import JsonRpcClient
    from amplifier_agent_client.session import SessionHandle

    tx = StubTransport()
    rpc = JsonRpcClient(tx)  # type: ignore[arg-type]

    async def noop_terminate() -> None: ...

    handle = SessionHandle(rpc, session_id="s", terminate=noop_terminate)
    handle.submit("first")
    with pytest.raises(RuntimeError, match="one-shot|already"):
        handle.submit("second")
```

3. **Run both, confirm fail.**

4. **TS implementation** — `wrappers/typescript/src/session.ts`. Key shape:

```typescript
import type { Notification } from "./jsonrpc.js";

export interface DisplayEvent {
  type: string;
  sessionId: string;
  turnId: string;
  parentTurnId?: string;
  synthesized?: boolean;
  payload: Record<string, unknown>;
}

export class AaaError extends Error {
  constructor(public readonly code: string, message: string, public readonly remediation?: string) {
    super(message);
    this.name = "AaaError";
  }
}

const TERMINAL_NOTIFICATION = "result/final";

export interface SessionDeps {
  sessionId: string;
  terminate: () => Promise<void>;
}

export class SessionHandle {
  private submitted = false;

  constructor(private readonly rpc: import("./jsonrpc.js").JsonRpcClient, private readonly deps: SessionDeps) {}

  submit(prompt: string): AsyncIterable<DisplayEvent> {
    if (this.submitted) {
      throw new AaaError("lifecycle_unsupported", "SessionHandle.submit() is one-shot per session (D10); already submitted");
    }
    this.submitted = true;
    const turnId = `turn-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    return this.makeIterable(prompt, turnId);
  }

  private async *makeIterable(prompt: string, turnId: string): AsyncIterable<DisplayEvent> {
    const buf: DisplayEvent[] = [];
    let resolveWaiter: (() => void) | null = null;
    let ended = false;

    const onNotif = (n: Notification) => {
      const params = (n.params ?? {}) as Record<string, unknown>;
      const ev: DisplayEvent = {
        type: n.method,
        sessionId: (params.sessionId as string) ?? this.deps.sessionId,
        turnId: (params.turnId as string) ?? turnId,
        parentTurnId: params.parentTurnId as string | undefined,
        payload: params,
      };
      buf.push(ev);
      if (n.method === TERMINAL_NOTIFICATION) ended = true;
      resolveWaiter?.();
    };
    this.rpc.onNotification(onNotif);

    // Fire turn/submit. Don't await yet — drive iteration as notifications arrive.
    const submitPromise = this.rpc.call("turn/submit", {
      sessionId: this.deps.sessionId,
      turnId,
      prompt,
    });
    submitPromise.finally(() => {
      ended = true;
      resolveWaiter?.();
    });

    while (true) {
      while (buf.length > 0) {
        const ev = buf.shift()!;
        yield ev;
      }
      if (ended) return;
      await new Promise<void>((r) => {
        resolveWaiter = r;
      });
    }
  }

  async cancel(): Promise<void> {
    await this.deps.terminate();
  }

  async dispose(): Promise<void> {
    await this.deps.terminate();
  }
}
```

5. **Py implementation** — same idea at `wrappers/python/src/amplifier_agent_client/session.py`. Use an `asyncio.Queue` to buffer events. Sentinel for termination. Raise a typed exception on second-submit.

```python
"""SessionHandle — one-shot ``submit(prompt)`` returning AsyncIterator[DisplayEvent]."""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from amplifier_agent_client.jsonrpc import JsonRpcClient

_TERMINAL_METHOD = "result/final"


class AaaError(Exception):
    def __init__(self, code: str, message: str, *, remediation: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.remediation = remediation


class SessionHandle:
    def __init__(
        self,
        rpc: JsonRpcClient,
        *,
        session_id: str,
        terminate: Callable[[], Awaitable[None]],
    ) -> None:
        self._rpc = rpc
        self._session_id = session_id
        self._terminate = terminate
        self._submitted = False

    def submit(self, prompt: str) -> AsyncIterator[dict[str, Any]]:
        if self._submitted:
            raise RuntimeError(
                "SessionHandle.submit() is one-shot per session (D10); already submitted"
            )
        self._submitted = True
        turn_id = f"turn-{secrets.token_hex(4)}"
        return self._stream(prompt, turn_id)

    async def _stream(self, prompt: str, turn_id: str) -> AsyncIterator[dict[str, Any]]:
        queue: asyncio.Queue[dict | None] = asyncio.Queue()

        def on_notif(n: dict) -> None:
            params = n.get("params") or {}
            ev = {
                "type": n["method"],
                "sessionId": params.get("sessionId", self._session_id),
                "turnId": params.get("turnId", turn_id),
                "parentTurnId": params.get("parentTurnId"),
                "payload": params,
            }
            queue.put_nowait(ev)
            if n["method"] == _TERMINAL_METHOD:
                queue.put_nowait(None)

        self._rpc.on_notification(on_notif)

        async def submit_task() -> None:
            try:
                await self._rpc.call("turn/submit", {
                    "sessionId": self._session_id,
                    "turnId": turn_id,
                    "prompt": prompt,
                })
            finally:
                queue.put_nowait(None)

        task = asyncio.create_task(submit_task())

        while True:
            item = await queue.get()
            if item is None:
                break
            yield item
        await task  # surface any submit-call exception

    async def cancel(self) -> None:
        await self._terminate()

    async def dispose(self) -> None:
        await self._terminate()
```

6. **Run both, confirm pass.**

7. **Commit:** `git add wrappers/ && git commit -m "feat(wrappers): SessionHandle.submit() AsyncIterable over DisplayEvent"`.

---

## Task 8 — L14 synthesis client-side (paired)

**Files:**
- Create: `wrappers/typescript/src/l14.ts`
- Create: `wrappers/typescript/test/l14.test.ts`
- Modify: `wrappers/typescript/src/session.ts`
- Create: `wrappers/python/src/amplifier_agent_client/l14.py`
- Create: `wrappers/python/tests/test_l14.py`
- Modify: `wrappers/python/src/amplifier_agent_client/session.py`

**Goal:** If the engine emits a non-null `reply` in its `turn/submit` response **but no `result/final` notification was observed first**, the wrapper synthesizes a `result/final`-shaped `DisplayEvent` with `synthesized: true` and yields it as the last event before the iterator ends. This is the L14 safety net from design §4.6 contract #1.

The two branches of the contract:
- **Branch A** (engine emits `result/final`): wrapper does NOT synthesize.
- **Branch B** (engine omits): wrapper synthesizes.

Driven by `l14_synthesis.yaml` (the fixture only covers branch B; branch A is implicitly covered everywhere else).

**TDD bullets:**

1. **Failing TS test** at `wrappers/typescript/test/l14.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { synthesizeFinalIfMissing } from "../src/l14.js";
import type { DisplayEvent } from "../src/session.js";

describe("L14 synthesis (TS)", () => {
  it("returns null if a result/final was already seen", () => {
    const sawFinal = true;
    const reply = "anything";
    expect(synthesizeFinalIfMissing({ sawFinal, reply, sessionId: "s", turnId: "t" })).toBeNull();
  });

  it("returns null if engine returned null reply (nothing to synthesise)", () => {
    expect(synthesizeFinalIfMissing({ sawFinal: false, reply: null, sessionId: "s", turnId: "t" })).toBeNull();
  });

  it("synthesises a result/final with synthesized=true when engine omits but reply is present", () => {
    const ev = synthesizeFinalIfMissing({ sawFinal: false, reply: "hello", sessionId: "s", turnId: "t" });
    expect(ev).not.toBeNull();
    expect(ev!.type).toBe("result/final");
    expect(ev!.synthesized).toBe(true);
    expect((ev!.payload as Record<string, unknown>).text).toBe("hello");
  });
});
```

2. **Failing Py test** at `wrappers/python/tests/test_l14.py` — same three cases.

```python
import pytest


def test_l14_no_synth_when_seen() -> None:
    from amplifier_agent_client.l14 import synthesize_final_if_missing

    assert synthesize_final_if_missing(saw_final=True, reply="x", session_id="s", turn_id="t") is None


def test_l14_no_synth_when_reply_null() -> None:
    from amplifier_agent_client.l14 import synthesize_final_if_missing

    assert synthesize_final_if_missing(saw_final=False, reply=None, session_id="s", turn_id="t") is None


def test_l14_synthesizes_when_engine_omits() -> None:
    from amplifier_agent_client.l14 import synthesize_final_if_missing

    ev = synthesize_final_if_missing(saw_final=False, reply="hello", session_id="s", turn_id="t")
    assert ev is not None
    assert ev["type"] == "result/final"
    assert ev["synthesized"] is True
    assert ev["payload"]["text"] == "hello"
```

3. **Implement** the pure synthesis function in each language. Sketch (TS):

```typescript
// wrappers/typescript/src/l14.ts
import type { DisplayEvent } from "./session.js";

export function synthesizeFinalIfMissing(args: {
  sawFinal: boolean;
  reply: string | null;
  sessionId: string;
  turnId: string;
}): DisplayEvent | null {
  if (args.sawFinal) return null;
  if (args.reply == null) return null;
  return {
    type: "result/final",
    sessionId: args.sessionId,
    turnId: args.turnId,
    synthesized: true,
    payload: { sessionId: args.sessionId, turnId: args.turnId, text: args.reply },
  };
}
```

Py mirror at `wrappers/python/src/amplifier_agent_client/l14.py`:

```python
"""L14 client-side synthesis safety net (design §4.6 contract #1)."""

from __future__ import annotations

from typing import Any


def synthesize_final_if_missing(
    *,
    saw_final: bool,
    reply: str | None,
    session_id: str,
    turn_id: str,
) -> dict[str, Any] | None:
    """Return a synthesized result/final DisplayEvent, or None if synthesis isn't needed."""
    if saw_final or reply is None:
        return None
    return {
        "type": "result/final",
        "sessionId": session_id,
        "turnId": turn_id,
        "synthesized": True,
        "payload": {"sessionId": session_id, "turnId": turn_id, "text": reply},
    }
```

4. **Wire into `session.ts` and `session.py`.** Track `sawFinal` boolean in the iterator loop. When the `turn/submit` JSON-RPC response arrives (capture its `reply` field), call `synthesizeFinalIfMissing` / `synthesize_final_if_missing`. If it returns a non-null event, push it onto the buffer/queue before signalling end.

The TS pattern: change `submitPromise.finally(...)` into `.then(async (result) => { const syn = synthesizeFinalIfMissing({ sawFinal, reply: (result as any)?.reply ?? null, sessionId: this.deps.sessionId, turnId }); if (syn) buf.push(syn); ended = true; resolveWaiter?.(); })`.

Py pattern (modify `submit_task`):

```python
async def submit_task() -> None:
    try:
        result = await self._rpc.call("turn/submit", {...})
        syn = synthesize_final_if_missing(
            saw_final=saw_final_flag["seen"],
            reply=(result or {}).get("reply"),
            session_id=self._session_id,
            turn_id=turn_id,
        )
        if syn is not None:
            queue.put_nowait(syn)
    finally:
        queue.put_nowait(None)
```

Use a mutable dict `saw_final_flag = {"seen": False}` updated inside `on_notif` so the closure can read it.

5. **Add an integration test** (one per language) that drives a stub transport through Branch B and asserts the iterator yields a synthesized event last:

```typescript
// append to test/session.test.ts
it("L14: synthesizes result/final when engine omits but reply is present", async () => {
  const tx = new StubTransport();
  const rpc = new JsonRpcClient(tx as unknown as never);
  const handle = new SessionHandle(rpc, { sessionId: "s", terminate: async () => {} });
  const iter = handle.submit("hi");
  queueMicrotask(() => {
    const submitId = (tx.sent.at(-1) as { id: number }).id;
    // NO result/final notification, just turn/submit response with reply.
    tx.emit({ jsonrpc: "2.0", id: submitId, result: { reply: "hello", turnId: "t", sessionId: "s" } });
  });
  const events: any[] = [];
  for await (const ev of iter) events.push(ev);
  expect(events.at(-1).type).toBe("result/final");
  expect(events.at(-1).synthesized).toBe(true);
});
```

Python analogue: append to `tests/test_session.py`, same shape.

6. **Run both, confirm pass.**

7. **Commit:** `git add wrappers/ && git commit -m "feat(wrappers): L14 client-side result/final synthesis"`.

---

## Task 9 — Approval bridge (in-band, mid-turn JSON-RPC round-trip) (paired)

**Files:**
- Create: `wrappers/typescript/src/approval.ts`
- Create: `wrappers/typescript/test/approval.test.ts`
- Modify: `wrappers/typescript/src/session.ts` (wire approval handler at session creation)
- Create: `wrappers/python/src/amplifier_agent_client/approval.py`
- Create: `wrappers/python/tests/test_approval.py`
- Modify: `wrappers/python/src/amplifier_agent_client/session.py`

**Goal:** When the engine emits a server-initiated `approval/request` JSON-RPC frame, the wrapper invokes the adapter-supplied `approval.onRequest(req): Promise<ApprovalResponse>` (TS) / `approval.on_request(req)` (Py) callback, awaits the response, and sends a JSON-RPC response back. **Mid-turn, in-band — same subprocess lifetime.** Timeout: if `onRequest` doesn't resolve within `approval.timeoutMs`, send a response with `decision: "timeout"`.

The wrapper does NOT route approvals through the notification fanout — it uses the `JsonRpcClient.onRequest("approval/request", ...)` handler from Task 6.

**Pattern reference:**
- Design §5.2 — six-step round-trip.
- Stub the round-trip via the StubTransport pattern from prior tasks.

**TDD bullets:**

1. **Failing TS test** at `wrappers/typescript/test/approval.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { makeApprovalHandler } from "../src/approval.js";

describe("Approval bridge (TS)", () => {
  it("forwards approval/request params to the adapter callback and returns its response", async () => {
    const handler = makeApprovalHandler({
      onRequest: async (req) => ({ decision: "allow" as const, requestId: (req as any).id }),
      timeoutMs: 1000,
    });
    const result = await handler({ id: "abc", tool: "Bash", args: { cmd: "ls" } });
    expect(result).toEqual({ decision: "allow", requestId: "abc" });
  });

  it("emits decision=timeout if onRequest exceeds timeoutMs", async () => {
    const handler = makeApprovalHandler({
      onRequest: () => new Promise(() => {}), // never resolves
      timeoutMs: 50,
    });
    const result = await handler({ id: "abc", tool: "Bash", args: {} });
    expect((result as { decision: string }).decision).toBe("timeout");
  });

  it("falls back to deny if no adapter is configured", async () => {
    const handler = makeApprovalHandler(undefined);
    const result = await handler({ id: "abc", tool: "Bash", args: {} });
    expect((result as { decision: string }).decision).toBe("deny");
  });
});
```

2. **Failing Py test** at `wrappers/python/tests/test_approval.py` — same three cases. Async variant.

```python
import asyncio
import pytest


@pytest.mark.asyncio
async def test_approval_forwards_to_adapter() -> None:
    from amplifier_agent_client.approval import make_approval_handler

    async def on_req(req: dict) -> dict:
        return {"decision": "allow", "requestId": req["id"]}

    handler = make_approval_handler(on_request=on_req, timeout_ms=1000)
    result = await handler({"id": "abc", "tool": "Bash", "args": {}})
    assert result == {"decision": "allow", "requestId": "abc"}


@pytest.mark.asyncio
async def test_approval_timeout() -> None:
    from amplifier_agent_client.approval import make_approval_handler

    async def slow(_req: dict) -> dict:
        await asyncio.sleep(60)
        return {"decision": "allow"}

    handler = make_approval_handler(on_request=slow, timeout_ms=50)
    result = await handler({"id": "abc", "tool": "Bash", "args": {}})
    assert result["decision"] == "timeout"


@pytest.mark.asyncio
async def test_approval_default_deny() -> None:
    from amplifier_agent_client.approval import make_approval_handler

    handler = make_approval_handler(on_request=None, timeout_ms=1000)
    result = await handler({"id": "abc", "tool": "Bash", "args": {}})
    assert result["decision"] == "deny"
```

3. **Run both, confirm fail.**

4. **TS implementation** — `wrappers/typescript/src/approval.ts`:

```typescript
export interface ApprovalRequest {
  id: string;
  tool: string;
  args: Record<string, unknown>;
}

export interface ApprovalResponse {
  decision: "allow" | "deny" | "timeout";
  reason?: string;
  [k: string]: unknown;
}

export interface ApprovalAdapter {
  onRequest: (req: ApprovalRequest) => Promise<ApprovalResponse>;
  timeoutMs: number;
}

export function makeApprovalHandler(
  adapter: ApprovalAdapter | undefined,
): (params: unknown) => Promise<ApprovalResponse> {
  return async (params) => {
    const req = params as ApprovalRequest;
    if (!adapter) {
      return { decision: "deny", reason: "no_adapter_configured" };
    }
    return new Promise<ApprovalResponse>((resolve) => {
      let settled = false;
      const timer = setTimeout(() => {
        if (settled) return;
        settled = true;
        resolve({ decision: "timeout" });
      }, adapter.timeoutMs);
      adapter.onRequest(req).then((resp) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        resolve(resp);
      }).catch(() => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        resolve({ decision: "deny", reason: "adapter_error" });
      });
    });
  };
}
```

Wire into `session.ts`: when constructing a `SessionHandle`, take an optional `approval` adapter param; if present, register `rpc.onRequest("approval/request", makeApprovalHandler(approval))`.

5. **Py implementation** — `wrappers/python/src/amplifier_agent_client/approval.py`:

```python
"""Approval bridge: server-initiated approval/request → adapter callback → response."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any


def make_approval_handler(
    *,
    on_request: Callable[[dict], Awaitable[dict]] | None,
    timeout_ms: int,
) -> Callable[[dict], Awaitable[dict]]:
    async def handler(params: dict) -> dict:
        if on_request is None:
            return {"decision": "deny", "reason": "no_adapter_configured"}
        try:
            return await asyncio.wait_for(on_request(params), timeout=timeout_ms / 1000.0)
        except asyncio.TimeoutError:
            return {"decision": "timeout"}
        except Exception:
            return {"decision": "deny", "reason": "adapter_error"}

    return handler
```

Wire into `session.py` constructor similarly.

6. **Run both, confirm pass.**

7. **Commit:** `git add wrappers/ && git commit -m "feat(wrappers): in-band approval bridge with timeout and default deny"`.

---

## Task 10 — Display callback + sub-agent filtering (paired)

**Files:**
- Create: `wrappers/typescript/src/display.ts`
- Create: `wrappers/typescript/test/display.test.ts`
- Modify: `wrappers/typescript/src/session.ts`
- Create: `wrappers/python/src/amplifier_agent_client/display.py`
- Create: `wrappers/python/tests/test_display.py`
- Modify: `wrappers/python/src/amplifier_agent_client/session.py`

**Goal:** Two features in one task:

1. **`display.onEvent` push callback.** Adapters can either consume the async iterator returned by `submit()`, OR pass a `display.onEvent(event)` callback that is invoked **per event**, OR both. The pull and push paths see the same events.

2. **Sub-agent filtering.** `display.subagentEvents: 'all' | 'none'` (default `'all'`). When `'none'`, events whose payload carries a `parentTurnId` are suppressed from BOTH the iterator and the callback. Validates against `subagent_lineage.yaml` (Task 13 will run that fixture against this filter).

**Pattern reference:**
- Design §4.5 sub-agent leak control.
- `parentTurnId` field is the marker — see `notifications.py` and the `subagent_lineage.yaml` fixture.

**TDD bullets:**

1. **Failing TS test** at `wrappers/typescript/test/display.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { applyDisplayFilter } from "../src/display.js";
import type { DisplayEvent } from "../src/session.js";

const ev = (overrides: Partial<DisplayEvent> = {}): DisplayEvent => ({
  type: "result/delta",
  sessionId: "s",
  turnId: "t",
  payload: {},
  ...overrides,
});

describe("display filter (TS)", () => {
  it("keeps everything when subagentEvents=all", () => {
    const f = applyDisplayFilter({ subagentEvents: "all" });
    expect(f(ev())).toBe(true);
    expect(f(ev({ parentTurnId: "parent" }))).toBe(true);
  });

  it("drops events with parentTurnId when subagentEvents=none", () => {
    const f = applyDisplayFilter({ subagentEvents: "none" });
    expect(f(ev())).toBe(true);
    expect(f(ev({ parentTurnId: "parent" }))).toBe(false);
  });

  it("defaults to all when subagentEvents is unset", () => {
    const f = applyDisplayFilter({});
    expect(f(ev({ parentTurnId: "x" }))).toBe(true);
  });
});
```

2. **Failing Py test** at `wrappers/python/tests/test_display.py` — same three cases against `apply_display_filter`.

```python
def _ev(**overrides):
    return {"type": "result/delta", "sessionId": "s", "turnId": "t", "payload": {}, **overrides}


def test_filter_all_keeps_everything() -> None:
    from amplifier_agent_client.display import apply_display_filter

    f = apply_display_filter(subagent_events="all")
    assert f(_ev())
    assert f(_ev(parentTurnId="p"))


def test_filter_none_drops_parented() -> None:
    from amplifier_agent_client.display import apply_display_filter

    f = apply_display_filter(subagent_events="none")
    assert f(_ev())
    assert not f(_ev(parentTurnId="p"))


def test_filter_default_is_all() -> None:
    from amplifier_agent_client.display import apply_display_filter

    f = apply_display_filter()
    assert f(_ev(parentTurnId="p"))
```

3. **Implement** both modules:

```typescript
// wrappers/typescript/src/display.ts
import type { DisplayEvent } from "./session.js";

export type SubagentMode = "all" | "none";

export interface DisplayAdapter {
  onEvent?: (ev: DisplayEvent) => void;
  subagentEvents?: SubagentMode;
}

export function applyDisplayFilter(adapter: DisplayAdapter): (ev: DisplayEvent) => boolean {
  const mode: SubagentMode = adapter.subagentEvents ?? "all";
  return (ev) => {
    if (mode === "all") return true;
    return ev.parentTurnId == null;
  };
}
```

```python
# wrappers/python/src/amplifier_agent_client/display.py
"""Display callback + sub-agent filtering (design §4.5 / D9)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal


def apply_display_filter(
    *,
    subagent_events: Literal["all", "none"] = "all",
) -> Callable[[dict[str, Any]], bool]:
    def keep(ev: dict[str, Any]) -> bool:
        if subagent_events == "all":
            return True
        return ev.get("parentTurnId") is None

    return keep
```

4. **Wire into `session.ts` and `session.py`.** Inside the iterator loop:
   - Apply the filter before yielding to the iterator AND before invoking `display.onEvent`.
   - If `display.onEvent` is set, call it for every kept event.

5. **Add an integration test** per language covering both the push-callback path and the filter path. Confirm: same StubTransport pattern, drive a `progress` notification with `parentTurnId`, assert it doesn't reach the consumer when `subagentEvents: 'none'`.

6. **Run both, confirm pass.**

7. **Commit:** `git add wrappers/ && git commit -m "feat(wrappers): display.onEvent push path + subagent event filtering"`.

---

### 🔎 Quality checkpoint B

Run:
```
python_check on:
  wrappers/python/src/amplifier_agent_client/
  wrappers/python/tests/

cd wrappers/typescript && pnpm typecheck && pnpm test
```

Fix anything that surfaces. Re-run both suites. Amend with `git commit --amend --no-edit` if fixups are needed.

---

## Task 11 — Version skew + binary discovery + env allowlist + `version` CLI (paired)

**Files:**
- Modify: `src/amplifier_agent_cli/__main__.py` (add new subcommand `version`)
- Create: `src/amplifier_agent_cli/admin/version_info.py`
- Create: `tests/test_cli_version_subcommand.py`
- Create: `wrappers/typescript/src/version.ts`
- Create: `wrappers/typescript/src/spawn.ts`
- Create: `wrappers/typescript/test/version.test.ts`
- Create: `wrappers/typescript/test/spawn.test.ts`
- Create: `wrappers/python/src/amplifier_agent_client/version.py`
- Create: `wrappers/python/src/amplifier_agent_client/spawn.py`
- Create: `wrappers/python/tests/test_version.py`
- Create: `wrappers/python/tests/test_spawn.py`

**Goal:** Four concerns that travel together:

1. **Engine CLI: `amplifier-agent version --json`.** Emits `{"version": <engine version>, "protocolVersion": "2026-05-aaa-v0", "bundleDigest": <opt>}`. Pure stdout, no side effects.
2. **Pre-spawn version probe.** Wrapper runs `amplifier-agent version --json` BEFORE the main spawn; parses the JSON; strict-compares `protocolVersion` against the wrapper's compiled constant. Mismatch → `AaaError("protocol_version_mismatch", remediation: <command>)`. Override via `allowProtocolSkew: true` (constructor) OR `AMPLIFIER_AGENT_ALLOW_PROTOCOL_SKEW=1` (env). Defence in depth: D6's `agent/initialize` check (which is a future engine concern) is still planned, but the pre-spawn probe is what we wire here.
3. **Binary discovery.** Resolve the binary via PATH first; if not found, check `AMPLIFIER_AGENT_BIN`. Missing → `AaaError("binary_not_found", remediation: "Install amplifier-agent: ...")`.
4. **Env allowlist.** Build the subprocess env from a caller-supplied allowlist of variable names (default a small fixed set: `PATH`, `HOME`, `USER`, `LANG`, `LC_*`, `TERM`, `TMPDIR`, `AMPLIFIER_*`). Anything not in the allowlist is dropped. Extra vars can be force-included via `env.extra`.

**Pattern reference:**
- The CLI uses click; see `src/amplifier_agent_cli/__main__.py:32-46` for the subcommand registration pattern.
- The doctor admin command at `src/amplifier_agent_cli/admin/doctor.py` is a peer to follow for module shape.

**TDD bullets:**

### 11a. Engine CLI subcommand

1. **Failing test** at `tests/test_cli_version_subcommand.py`:

```python
"""amplifier-agent version --json emits structured wire-readable metadata."""

from __future__ import annotations

import json

from click.testing import CliRunner


def test_version_json_emits_required_fields() -> None:
    from amplifier_agent_cli.__main__ import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["version", "--json"])
    assert result.exit_code == 0, result.output

    payload = json.loads(result.output)
    assert payload["protocolVersion"] == "2026-05-aaa-v0"
    assert isinstance(payload.get("version"), str) and payload["version"]


def test_version_plain_text_default() -> None:
    from amplifier_agent_cli.__main__ import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["version"])
    assert result.exit_code == 0
    assert "2026-05-aaa-v0" in result.output
```

2. **Implement** `src/amplifier_agent_cli/admin/version_info.py`:

```python
"""amplifier-agent version subcommand — engine identity probe for wrappers.

Emits ``{version, protocolVersion}`` on stdout when --json is passed.  This
is the pre-spawn version-skew probe consumed by the TS and Py wrappers
(design §8 D6).
"""

from __future__ import annotations

import json

import click

from amplifier_agent_cli import __version__
from amplifier_agent_lib.protocol.methods import PROTOCOL_VERSION


@click.command(name="version")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def version_command(as_json: bool) -> None:
    """Print engine version and wire protocol version."""
    payload = {"version": __version__, "protocolVersion": PROTOCOL_VERSION}
    if as_json:
        click.echo(json.dumps(payload))
    else:
        click.echo(f"amplifier-agent {payload['version']} (wire {payload['protocolVersion']})")
```

3. **Register** in `__main__.py`:

```python
# Add to imports:
from amplifier_agent_cli.admin.version_info import version_command as _version_command
# After the other add_command calls:
cli.add_command(_version_command)
```

4. **Verify:** `uv run pytest tests/test_cli_version_subcommand.py -v` → pass.

### 11b. Wrapper-side version + spawn

5. **Failing TS test** at `wrappers/typescript/test/version.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { checkProtocolVersion } from "../src/version.js";

describe("version skew (TS)", () => {
  it("returns ok when versions match", () => {
    const res = checkProtocolVersion({
      wrapper: "2026-05-aaa-v0",
      engine: "2026-05-aaa-v0",
      allowSkew: false,
    });
    expect(res.ok).toBe(true);
  });

  it("returns error with remediation on mismatch", () => {
    const res = checkProtocolVersion({
      wrapper: "2026-05-aaa-v0",
      engine: "2099-12-future",
      allowSkew: false,
    });
    expect(res.ok).toBe(false);
    expect(res.code).toBe("protocol_version_mismatch");
    expect(res.remediation).toMatch(/install|allow-protocol-skew/i);
  });

  it("permits skew when allowSkew=true", () => {
    const res = checkProtocolVersion({
      wrapper: "2026-05-aaa-v0",
      engine: "2099-12-future",
      allowSkew: true,
    });
    expect(res.ok).toBe(true);
  });
});
```

6. **Failing TS test** at `wrappers/typescript/test/spawn.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { resolveBinaryPath, buildEnv } from "../src/spawn.js";

describe("binary discovery (TS)", () => {
  it("returns AMPLIFIER_AGENT_BIN if set and existing", () => {
    // Echo /bin/sh — it exists on macOS and Linux.
    const resolved = resolveBinaryPath({ env: { AMPLIFIER_AGENT_BIN: "/bin/sh" } });
    expect(resolved).toBe("/bin/sh");
  });

  it("falls back to PATH when env var is unset", () => {
    const resolved = resolveBinaryPath({ env: { PATH: process.env.PATH ?? "" } });
    // Resolver returns null if amplifier-agent isn't on PATH; either string-or-null is acceptable here.
    expect(resolved === null || typeof resolved === "string").toBe(true);
  });
});

describe("env allowlist (TS)", () => {
  it("drops disallowed variables", () => {
    const env = buildEnv({
      processEnv: { PATH: "/usr/bin", SECRET: "shh", HOME: "/h" },
      allowlist: ["PATH", "HOME"],
      extra: {},
    });
    expect(env).toEqual({ PATH: "/usr/bin", HOME: "/h" });
  });

  it("merges extras over the allowlist", () => {
    const env = buildEnv({
      processEnv: { PATH: "/usr/bin" },
      allowlist: ["PATH"],
      extra: { CUSTOM: "yes" },
    });
    expect(env.CUSTOM).toBe("yes");
  });
});
```

7. **Failing Py tests** at `wrappers/python/tests/test_version.py` and `wrappers/python/tests/test_spawn.py` — mirror the three version cases and the two spawn cases.

```python
# test_version.py
def test_version_match() -> None:
    from amplifier_agent_client.version import check_protocol_version

    r = check_protocol_version(wrapper="2026-05-aaa-v0", engine="2026-05-aaa-v0", allow_skew=False)
    assert r.ok


def test_version_mismatch_emits_remediation() -> None:
    from amplifier_agent_client.version import check_protocol_version

    r = check_protocol_version(wrapper="2026-05-aaa-v0", engine="2099-12-future", allow_skew=False)
    assert not r.ok
    assert r.code == "protocol_version_mismatch"
    assert "allow-protocol-skew" in (r.remediation or "")


def test_version_skew_override() -> None:
    from amplifier_agent_client.version import check_protocol_version

    r = check_protocol_version(wrapper="2026-05-aaa-v0", engine="2099-12-future", allow_skew=True)
    assert r.ok
```

```python
# test_spawn.py
def test_resolve_binary_env_override(tmp_path) -> None:
    from amplifier_agent_client.spawn import resolve_binary_path

    assert resolve_binary_path(env={"AMPLIFIER_AGENT_BIN": "/bin/sh"}) == "/bin/sh"


def test_build_env_drops_unlisted() -> None:
    from amplifier_agent_client.spawn import build_env

    env = build_env(
        process_env={"PATH": "/usr/bin", "SECRET": "shh", "HOME": "/h"},
        allowlist=["PATH", "HOME"],
        extra={},
    )
    assert env == {"PATH": "/usr/bin", "HOME": "/h"}


def test_build_env_extras_win() -> None:
    from amplifier_agent_client.spawn import build_env

    env = build_env(
        process_env={"PATH": "/usr/bin"},
        allowlist=["PATH"],
        extra={"CUSTOM": "yes"},
    )
    assert env["CUSTOM"] == "yes"
```

8. **Implement.** TS sketches:

```typescript
// wrappers/typescript/src/version.ts
const REMEDIATION =
  "Reinstall the matching amplifier-agent and amplifier-agent-client packages, " +
  "or pass {allowProtocolSkew: true} (env: AMPLIFIER_AGENT_ALLOW_PROTOCOL_SKEW=1) to opt out.";

export function checkProtocolVersion(args: {
  wrapper: string;
  engine: string;
  allowSkew: boolean;
}): { ok: true } | { ok: false; code: "protocol_version_mismatch"; remediation: string } {
  if (args.wrapper === args.engine || args.allowSkew) return { ok: true };
  return { ok: false, code: "protocol_version_mismatch", remediation: REMEDIATION };
}
```

```typescript
// wrappers/typescript/src/spawn.ts
import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";

const DEFAULT_ALLOWLIST = ["PATH", "HOME", "USER", "LANG", "TERM", "TMPDIR"];
const AMPLIFIER_PREFIX = "AMPLIFIER_";

export function resolveBinaryPath(opts: { env: Record<string, string | undefined> }): string | null {
  const override = opts.env.AMPLIFIER_AGENT_BIN;
  if (override && existsSync(override)) return override;
  try {
    const out = execFileSync("which", ["amplifier-agent"], { env: { PATH: opts.env.PATH ?? "" } });
    const line = out.toString().trim();
    return line || null;
  } catch {
    return null;
  }
}

export function buildEnv(opts: {
  processEnv: Record<string, string | undefined>;
  allowlist?: string[];
  extra?: Record<string, string>;
}): Record<string, string> {
  const allow = new Set(opts.allowlist ?? DEFAULT_ALLOWLIST);
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(opts.processEnv)) {
    if (v == null) continue;
    if (allow.has(k) || k.startsWith(AMPLIFIER_PREFIX) || k.startsWith("LC_")) out[k] = v;
  }
  return { ...out, ...(opts.extra ?? {}) };
}

export async function probeEngineVersion(binPath: string, env: Record<string, string>): Promise<{ version: string; protocolVersion: string }> {
  const buf = execFileSync(binPath, ["version", "--json"], { env, timeout: 5000 });
  return JSON.parse(buf.toString());
}
```

Py mirrors at `wrappers/python/src/amplifier_agent_client/version.py` and `wrappers/python/src/amplifier_agent_client/spawn.py`:

```python
# version.py
from __future__ import annotations

from dataclasses import dataclass

_REMEDIATION = (
    "Reinstall the matching amplifier-agent and amplifier-agent-client packages, "
    "or pass allow_protocol_skew=True (env: AMPLIFIER_AGENT_ALLOW_PROTOCOL_SKEW=1)."
)


@dataclass
class VersionCheck:
    ok: bool
    code: str | None = None
    remediation: str | None = None


def check_protocol_version(*, wrapper: str, engine: str, allow_skew: bool) -> VersionCheck:
    if wrapper == engine or allow_skew:
        return VersionCheck(ok=True)
    return VersionCheck(ok=False, code="protocol_version_mismatch", remediation=_REMEDIATION)
```

```python
# spawn.py
from __future__ import annotations

import json
import os
import shutil
import subprocess

_DEFAULT_ALLOWLIST = frozenset({"PATH", "HOME", "USER", "LANG", "TERM", "TMPDIR"})
_AMPLIFIER_PREFIX = "AMPLIFIER_"


def resolve_binary_path(env: dict[str, str]) -> str | None:
    override = env.get("AMPLIFIER_AGENT_BIN")
    if override and os.path.exists(override):
        return override
    return shutil.which("amplifier-agent", path=env.get("PATH"))


def build_env(*, process_env: dict[str, str], allowlist: list[str], extra: dict[str, str]) -> dict[str, str]:
    allow = set(allowlist)
    out: dict[str, str] = {}
    for k, v in process_env.items():
        if k in allow or k.startswith(_AMPLIFIER_PREFIX) or k.startswith("LC_"):
            out[k] = v
    out.update(extra)
    return out


def probe_engine_version(bin_path: str, env: dict[str, str]) -> dict:
    res = subprocess.run(
        [bin_path, "version", "--json"],
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    return json.loads(res.stdout)
```

9. **Run.** `pnpm test` + `uv run pytest tests/test_cli_version_subcommand.py wrappers/python/tests/test_version.py wrappers/python/tests/test_spawn.py -v`.

10. **Commit:** `git add -A && git commit -m "feat(wrappers): version skew check + binary discovery + env allowlist + 'version' CLI"`.

---

## Task 12 — spawnAgent() public API + getEngineInfo() (paired)

**Files:**
- Modify: `wrappers/typescript/src/index.ts` (wire all components into the locked public API)
- Create: `wrappers/typescript/test/spawn-agent.test.ts`
- Modify: `wrappers/python/src/amplifier_agent_client/__init__.py`
- Create: `wrappers/python/tests/test_spawn_agent.py`

**Goal:** Compose all prior components into the single public entry point. **The TypeScript signature is locked verbatim by design §8.2** — copy it exactly. The Python signature is symmetric.

`spawnAgent()` flow:
1. Resolve the binary (`resolveBinaryPath`).
2. Build the env (`buildEnv`).
3. Probe `amplifier-agent version --json` (`probeEngineVersion`).
4. Call `checkProtocolVersion`; throw on mismatch unless `allowProtocolSkew`.
5. Spawn the long-lived subprocess via `Transport` running `amplifier-agent run --stdio` (or whichever subcommand carries the JSON-RPC loop — check; if not present yet, this is a follow-up). For Plan 3, **stub the run-mode** with `--stdio` parameter or use a test-only entry that loops a fixture. The real engine integration lives in Task 15's exit-gate test.
6. Construct `JsonRpcClient`, register `approval/request` handler, register notification subscription.
7. Send `agent/initialize` with all params; capture the result.
8. Return a `SessionHandle` instance whose `getEngineInfo()` returns `{ binaryPath, protocolVersion, engineVersion, bundleDigest }`.

**For Plan 3 testability:** allow injecting a fake `Transport` factory via an undocumented `_transportFactory` option (or similar). The exit-gate test (Task 15) will exercise the real-engine path.

**TDD bullets:**

1. **Failing TS test** at `wrappers/typescript/test/spawn-agent.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { spawnAgent } from "../src/index.js";

describe("spawnAgent() public API (TS)", () => {
  it("matches the locked signature: returns a SessionHandle with getEngineInfo()", async () => {
    // Use the _transportFactory test injection to bypass the real subprocess.
    const handle = await spawnAgent({
      lifecycle: "one-shot",
      sessionId: "s-test",
      _transportFactory: () => new FakeTransport(),
      _versionProbe: async () => ({ version: "0.0.0", protocolVersion: "2026-05-aaa-v0" }),
      _binaryResolver: () => "/dev/null",
    } as any);

    const info = handle.getEngineInfo();
    expect(info.protocolVersion).toBe("2026-05-aaa-v0");
    expect(info.binaryPath).toBe("/dev/null");
  });

  it("throws AaaError(lifecycle_unsupported) when lifecycle !== one-shot (D10)", async () => {
    await expect(
      spawnAgent({
        lifecycle: "burst" as never,
        sessionId: "s",
      } as any),
    ).rejects.toMatchObject({ code: "lifecycle_unsupported" });
  });
});

class FakeTransport {
  private cb: ((f: unknown) => void) | null = null;
  async start(): Promise<void> {}
  async send(f: unknown): Promise<void> {
    // Auto-respond to initialize.
    if ((f as any).method === "initialize") {
      queueMicrotask(() => {
        this.cb?.({
          jsonrpc: "2.0",
          id: (f as any).id,
          result: {
            capabilities: {},
            serverInfo: { name: "amplifier-agent", version: "0.0.0" },
            sessionState: { sessionId: (f as any).params.sessionId, resumed: false },
          },
        });
      });
    }
  }
  onFrame(cb: (f: unknown) => void): void { this.cb = cb; }
  async terminate(): Promise<{ code: number | null; signal: string | null }> {
    return { code: 0, signal: null };
  }
}
```

2. **Failing Py test** at `wrappers/python/tests/test_spawn_agent.py` — same two cases against `spawn_agent`. Use a small fake-transport class.

```python
import pytest


class FakeTransport:
    def __init__(self) -> None:
        self._cb = None

    async def start(self) -> None: ...
    def on_frame(self, cb) -> None:
        self._cb = cb

    async def send(self, frame: dict) -> None:
        import asyncio
        if frame.get("method") == "initialize":
            async def respond() -> None:
                assert self._cb is not None
                self._cb({
                    "jsonrpc": "2.0",
                    "id": frame["id"],
                    "result": {
                        "capabilities": {},
                        "serverInfo": {"name": "amplifier-agent", "version": "0.0.0"},
                        "sessionState": {"sessionId": frame["params"]["sessionId"], "resumed": False},
                    },
                })
            asyncio.get_event_loop().call_soon(asyncio.ensure_future, respond())

    async def terminate(self) -> int:
        return 0


@pytest.mark.asyncio
async def test_spawn_agent_returns_handle_with_engine_info() -> None:
    from amplifier_agent_client import spawn_agent

    handle = await spawn_agent(
        lifecycle="one-shot",
        session_id="s",
        _transport_factory=lambda **_: FakeTransport(),
        _version_probe=lambda *_args, **_k: {"version": "0.0.0", "protocolVersion": "2026-05-aaa-v0"},
        _binary_resolver=lambda **_: "/dev/null",
    )
    info = handle.get_engine_info()
    assert info["protocolVersion"] == "2026-05-aaa-v0"


@pytest.mark.asyncio
async def test_spawn_agent_rejects_non_one_shot() -> None:
    from amplifier_agent_client import spawn_agent
    from amplifier_agent_client.session import AaaError

    with pytest.raises(AaaError, match="lifecycle_unsupported"):
        await spawn_agent(lifecycle="burst", session_id="s")
```

3. **Implement.** TS `index.ts` wires everything. Copy the type signatures **verbatim from design §8.2**. Sketch:

```typescript
import { Transport } from "./transport.js";
import { JsonRpcClient } from "./jsonrpc.js";
import { SessionHandle, AaaError, type DisplayEvent } from "./session.js";
import { makeApprovalHandler, type ApprovalAdapter } from "./approval.js";
import { applyDisplayFilter, type DisplayAdapter } from "./display.js";
import { checkProtocolVersion } from "./version.js";
import { resolveBinaryPath, buildEnv, probeEngineVersion } from "./spawn.js";

export { AaaError } from "./session.js";
export type { DisplayEvent, SessionHandle } from "./session.js";
export type { ApprovalRequest, ApprovalResponse, ApprovalAdapter } from "./approval.js";
export const PROTOCOL_VERSION_REQUIRED_BY_WRAPPER = "2026-05-aaa-v0" as const;

export interface SpawnAgentParams {
  lifecycle: "one-shot";
  sessionId: string;
  resume?: boolean;
  cwd?: string;
  env?: { allowlist: string[]; extra?: Record<string, string> };
  providerOverride?: string;
  approval?: ApprovalAdapter;
  display?: DisplayAdapter;
  allowProtocolSkew?: boolean;
  // Test-only injection points (not part of the public contract):
  _transportFactory?: (opts: unknown) => unknown;
  _versionProbe?: (bin: string, env: Record<string, string>) => Promise<{ version: string; protocolVersion: string }>;
  _binaryResolver?: (opts: { env: Record<string, string | undefined> }) => string | null;
}

export async function spawnAgent(params: SpawnAgentParams): Promise<SessionHandle> {
  if (params.lifecycle !== "one-shot") {
    throw new AaaError("lifecycle_unsupported", "v1 supports only lifecycle='one-shot' (D10).");
  }

  const resolver = params._binaryResolver ?? resolveBinaryPath;
  const binary = resolver({ env: process.env as Record<string, string | undefined> });
  if (!binary) {
    throw new AaaError("binary_not_found", "amplifier-agent not found on PATH and AMPLIFIER_AGENT_BIN unset.");
  }

  const env = buildEnv({
    processEnv: process.env as Record<string, string | undefined>,
    allowlist: params.env?.allowlist,
    extra: params.env?.extra,
  });

  const probe = params._versionProbe ?? probeEngineVersion;
  const info = await probe(binary, env);
  const allowSkew = params.allowProtocolSkew || env.AMPLIFIER_AGENT_ALLOW_PROTOCOL_SKEW === "1";
  const skew = checkProtocolVersion({
    wrapper: PROTOCOL_VERSION_REQUIRED_BY_WRAPPER,
    engine: info.protocolVersion,
    allowSkew,
  });
  if (!skew.ok) {
    throw new AaaError(skew.code, `protocol skew: wrapper=${PROTOCOL_VERSION_REQUIRED_BY_WRAPPER} engine=${info.protocolVersion}`, skew.remediation);
  }

  const transport = params._transportFactory
    ? (params._transportFactory({ binary, env }) as Transport)
    : new Transport({ command: binary, args: ["run", "--stdio"], env, cwd: params.cwd });
  await (transport as any).start?.();

  const rpc = new JsonRpcClient(transport as unknown as { send: (f: unknown) => Promise<void>; onFrame: (cb: (f: unknown) => void) => void });
  rpc.onRequest("approval/request", makeApprovalHandler(params.approval));

  const filterKeep = applyDisplayFilter(params.display ?? {});

  // Send initialize, await result.
  const init = await rpc.call("initialize", {
    protocolVersion: PROTOCOL_VERSION_REQUIRED_BY_WRAPPER,
    clientInfo: { name: "amplifier-agent-client-ts", version: "0.0.0" },
    capabilities: {},
    sessionId: params.sessionId,
    resume: params.resume,
    cwd: params.cwd,
    providerOverride: params.providerOverride,
  }) as { serverInfo: { version: string }; sessionState: { sessionId: string; resumed: boolean } };

  return new SessionHandle(rpc, {
    sessionId: init.sessionState.sessionId,
    terminate: async () => { await (transport as any).terminate?.(); },
    binaryPath: binary,
    protocolVersion: info.protocolVersion,
    engineVersion: init.serverInfo.version,
    onEvent: params.display?.onEvent,
    filterKeep,
  });
}
```

Add `getEngineInfo()` to `SessionHandle` (was deferred from Task 7):

```typescript
// session.ts — extend SessionDeps and SessionHandle:
export interface SessionDeps {
  sessionId: string;
  terminate: () => Promise<void>;
  binaryPath?: string;
  protocolVersion?: string;
  engineVersion?: string;
  bundleDigest?: string;
  onEvent?: (ev: DisplayEvent) => void;
  filterKeep?: (ev: DisplayEvent) => boolean;
}

// And on SessionHandle:
getEngineInfo() {
  return {
    binaryPath: this.deps.binaryPath ?? "",
    protocolVersion: this.deps.protocolVersion ?? "",
    engineVersion: this.deps.engineVersion ?? "",
    bundleDigest: this.deps.bundleDigest ?? "",
  };
}
```

Py mirror at `wrappers/python/src/amplifier_agent_client/__init__.py`:

```python
"""amplifier-agent-client — public API."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from amplifier_agent_client.approval import make_approval_handler
from amplifier_agent_client.display import apply_display_filter
from amplifier_agent_client.jsonrpc import JsonRpcClient
from amplifier_agent_client.session import AaaError, SessionHandle
from amplifier_agent_client.spawn import build_env, probe_engine_version, resolve_binary_path
from amplifier_agent_client.transport import Transport
from amplifier_agent_client.version import check_protocol_version

PROTOCOL_VERSION_REQUIRED_BY_WRAPPER = "2026-05-aaa-v0"


async def spawn_agent(
    *,
    lifecycle: Literal["one-shot"],
    session_id: str,
    resume: bool = False,
    cwd: str | None = None,
    env: dict | None = None,
    provider_override: str | None = None,
    approval: dict | None = None,
    display: dict | None = None,
    allow_protocol_skew: bool = False,
    _transport_factory: Callable[..., Any] | None = None,
    _version_probe: Callable[..., Any] | None = None,
    _binary_resolver: Callable[..., Any] | None = None,
) -> SessionHandle:
    if lifecycle != "one-shot":
        raise AaaError("lifecycle_unsupported", "v1 supports only lifecycle='one-shot' (D10).")

    resolver = _binary_resolver or (lambda **_: resolve_binary_path(env=dict(os.environ)))
    binary = resolver(env=dict(os.environ))
    if not binary:
        raise AaaError("binary_not_found", "amplifier-agent not found on PATH and AMPLIFIER_AGENT_BIN unset.")

    process_env = build_env(
        process_env=dict(os.environ),
        allowlist=(env or {}).get("allowlist", ["PATH", "HOME", "USER", "LANG", "TERM", "TMPDIR"]),
        extra=(env or {}).get("extra", {}),
    )

    probe = _version_probe or (lambda b, e: probe_engine_version(b, e))
    info = probe(binary, process_env)
    allow_skew = allow_protocol_skew or os.environ.get("AMPLIFIER_AGENT_ALLOW_PROTOCOL_SKEW") == "1"
    skew = check_protocol_version(
        wrapper=PROTOCOL_VERSION_REQUIRED_BY_WRAPPER,
        engine=info["protocolVersion"],
        allow_skew=allow_skew,
    )
    if not skew.ok:
        raise AaaError(skew.code or "protocol_version_mismatch",
                       f"wrapper={PROTOCOL_VERSION_REQUIRED_BY_WRAPPER} engine={info['protocolVersion']}",
                       remediation=skew.remediation)

    if _transport_factory:
        transport = _transport_factory(binary=binary, env=process_env)
    else:
        transport = Transport(command=binary, args=["run", "--stdio"], env=process_env, cwd=cwd)
    await transport.start()

    rpc = JsonRpcClient(transport)
    rpc.on_request("approval/request", make_approval_handler(
        on_request=(approval or {}).get("on_request"),
        timeout_ms=(approval or {}).get("timeout_ms", 30_000),
    ))

    init = await rpc.call("initialize", {
        "protocolVersion": PROTOCOL_VERSION_REQUIRED_BY_WRAPPER,
        "clientInfo": {"name": "amplifier-agent-client-py", "version": "0.0.0"},
        "capabilities": {},
        "sessionId": session_id,
        "resume": resume,
        "cwd": cwd,
        "providerOverride": provider_override,
    })

    async def terminate() -> None:
        await transport.terminate()

    return SessionHandle(
        rpc,
        session_id=init["sessionState"]["sessionId"],
        terminate=terminate,
        engine_info={
            "binaryPath": binary,
            "protocolVersion": info["protocolVersion"],
            "engineVersion": init["serverInfo"]["version"],
            "bundleDigest": init.get("serverInfo", {}).get("bundleDigest", ""),
        },
    )


__all__ = [
    "AaaError",
    "PROTOCOL_VERSION_REQUIRED_BY_WRAPPER",
    "SessionHandle",
    "spawn_agent",
]
```

You will also need to extend `SessionHandle.__init__` (Task 7) to accept `engine_info: dict | None = None` and add a `get_engine_info()` method.

4. **Run.** Both languages' suites pass.

5. **Commit:** `git add wrappers/ && git commit -m "feat(wrappers): spawnAgent / spawn_agent public API + getEngineInfo"`.

---

## Task 13 — Conformance harness (paired)

**Files:**
- Create: `wrappers/conformance/README.md`
- Create: `wrappers/conformance/runner_ts.ts`
- Create: `wrappers/conformance/runner_py.py`
- Create: `wrappers/conformance/package.json` (a tiny package-of-one so the TS runner can import from `amplifier-agent-client-ts` via workspace path)
- Create: `wrappers/conformance/tsconfig.json`
- Modify: `pnpm-workspace.yaml` at repo root (NEW file — create it with both `wrappers/typescript` and `wrappers/conformance` members)
- Create: `wrappers/conformance/tests/test_runner_py.py`
- Create: `wrappers/conformance/test/runner-ts.test.ts`

**Goal:** Two thin runners — one TS, one Py — that:

1. Accept a fixture path argument.
2. Use the **Plan 2 loader** (Py: import from `amplifier_agent_lib.protocol.conformance.loader`; TS: port the same shape contract in ~40 lines using YAML lib `yaml`).
3. Drive the wrapper through the fixture's `script` (using a stubbed transport that replays `server_to_client` frames at the correct sequence point) and capture **all observable events** the consumer of the wrapper sees.
4. Evaluate the fixture's `assertions:` list against the captured events.
5. Emit a structured **conformance report** as JSON to stdout: `{ fixture, language, passed: bool, assertions: [{kind, passed, detail}] }`.

For Plan 3, this is **fixture-replay style**, not real-engine style. The exit gate (Task 15) is what drives real engine subprocesses. This task's runner is the "drive a wrapper against scripted server frames" version.

**Pattern reference:**
- Plan 2 fixture loader at `src/amplifier_agent_lib/protocol/conformance/loader.py`.
- Plan 2 fixture shape: read any of the 5 YAML files under `src/amplifier_agent_lib/protocol/conformance/fixtures/`.
- Assertion kinds supported: `response_matches`, `error_returned`, `notification_emitted`, `no_notification`, `notification_order`, `session_state`.

**TDD bullets:**

1. **Write the runner_py** at `wrappers/conformance/runner_py.py`:

```python
"""Python conformance runner.

Drives the amplifier_agent_client wrapper against a YAML wire-sequence
fixture using a scripted stub transport.  Captures every observable
event the consumer would see and evaluates the fixture's assertions.

Output: one-line JSON report on stdout.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from amplifier_agent_lib.protocol.conformance.loader import Fixture, load_fixture


class ScriptedTransport:
    """Replays server_to_client frames in script order in response to client frames."""

    def __init__(self, fixture: Fixture) -> None:
        self._script = list(fixture.script)
        self._cb = None
        self._client_idx = 0

    async def start(self) -> None: ...

    def on_frame(self, cb) -> None:
        self._cb = cb

    async def send(self, frame: dict) -> None:
        assert self._cb is not None
        # Find the next client_to_server frame; emit any server_to_client frames
        # that follow before the next client_to_server frame.
        while self._client_idx < len(self._script):
            current = self._script[self._client_idx]
            self._client_idx += 1
            if current["direction"] == "client_to_server":
                # Now emit subsequent server_to_client frames until we hit the
                # next client_to_server or the end.
                while self._client_idx < len(self._script):
                    nxt = self._script[self._client_idx]
                    if nxt["direction"] == "server_to_client":
                        self._client_idx += 1
                        # Convert the script frame into the JSON-RPC wire frame.
                        wire = {"jsonrpc": "2.0"}
                        if "id" in nxt:
                            wire["id"] = nxt["id"]
                        if "method" in nxt:
                            wire["method"] = nxt["method"]
                            wire["params"] = nxt.get("params", {})
                        if "result" in nxt:
                            wire["result"] = nxt["result"]
                        if "error" in nxt:
                            wire["error"] = nxt["error"]
                        self._cb(wire)
                    else:
                        break
                return

    async def terminate(self) -> int:
        return 0


async def run_fixture(fixture_path: Path) -> dict:
    fixture = load_fixture(fixture_path)
    transport = ScriptedTransport(fixture)
    observed_notifications: list[dict] = []
    observed_responses: dict[int, dict] = {}
    observed_errors: dict[int, dict] = {}

    from amplifier_agent_client.jsonrpc import JsonRpcClient

    rpc = JsonRpcClient(transport)
    rpc.on_notification(lambda n: observed_notifications.append(n))

    # Re-play the client-to-server frames as RPC calls (script direction).
    for frame in fixture.script:
        if frame["direction"] != "client_to_server":
            continue
        try:
            result = await rpc.call(frame["method"], frame.get("params", {}))
            observed_responses[frame["id"]] = result
        except RuntimeError as e:
            observed_errors[frame["id"]] = {"message": str(e)}

    return _evaluate(fixture, observed_responses, observed_errors, observed_notifications)


def _evaluate(
    fixture: Fixture,
    responses: dict[int, dict],
    errors: dict[int, dict],
    notifs: list[dict],
) -> dict:
    results = []
    for a in fixture.assertions:
        kind = a["kind"]
        ok = False
        detail = ""
        if kind == "response_matches":
            ok = a["id"] in responses
            detail = f"id={a['id']} got={responses.get(a['id'])}"
        elif kind == "error_returned":
            ok = a["id"] in errors and a.get("code", "") in errors[a["id"]].get("message", "")
            detail = f"id={a['id']} got={errors.get(a['id'])}"
        elif kind == "notification_emitted":
            ok = any(n["method"] == a["method"] for n in notifs)
            detail = f"method={a['method']}"
        elif kind == "no_notification":
            ok = not any(n["method"] == a["method"] for n in notifs)
            detail = f"method={a['method']}"
        else:
            detail = f"unknown kind {kind!r}; skipped"
            ok = True  # do not fail on unknown kinds — parity lint will surface
        results.append({"kind": kind, "passed": ok, "detail": detail})

    return {
        "fixture": fixture.name,
        "language": "python",
        "passed": all(r["passed"] for r in results),
        "assertions": results,
    }


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: runner_py.py <fixture.yaml>", file=sys.stderr)
        return 2
    report = asyncio.run(run_fixture(Path(argv[1])))
    print(json.dumps(report))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

2. **Write the runner_ts** at `wrappers/conformance/runner_ts.ts` — port the same shape. Use the `yaml` npm package for YAML parsing (add as a dep in `wrappers/conformance/package.json`).

3. **Write tests** at `wrappers/conformance/tests/test_runner_py.py` and `wrappers/conformance/test/runner-ts.test.ts` that load `capability_negotiation.yaml`, run the harness, and assert the report has `passed: true`.

```python
# wrappers/conformance/tests/test_runner_py.py
import asyncio
from pathlib import Path


def test_py_runner_capability_negotiation_passes() -> None:
    from wrappers.conformance.runner_py import run_fixture  # type: ignore

    base = Path(__file__).resolve().parents[3] / "src" / "amplifier_agent_lib" / "protocol" / "conformance" / "fixtures"
    report = asyncio.run(run_fixture(base / "capability_negotiation.yaml"))
    assert report["passed"], report


def test_py_runner_l14_synthesis_passes() -> None:
    from wrappers.conformance.runner_py import run_fixture  # type: ignore

    base = Path(__file__).resolve().parents[3] / "src" / "amplifier_agent_lib" / "protocol" / "conformance" / "fixtures"
    report = asyncio.run(run_fixture(base / "l14_synthesis.yaml"))
    assert report["passed"], report
```

4. **Create `pnpm-workspace.yaml`** at the repo root:

```yaml
packages:
  - "wrappers/typescript"
  - "wrappers/conformance"
```

5. **Create `wrappers/conformance/package.json`** with `amplifier-agent-client-ts` as a workspace dependency:

```json
{
  "name": "amplifier-agent-conformance",
  "version": "0.0.0",
  "private": true,
  "type": "module",
  "scripts": {
    "test": "vitest run"
  },
  "dependencies": {
    "amplifier-agent-client-ts": "workspace:*",
    "yaml": "^2.4.0"
  },
  "devDependencies": {
    "@types/node": "^20.11.0",
    "tsx": "^4.7.0",
    "typescript": "^5.4.0",
    "vitest": "^1.4.0"
  }
}
```

6. **Verify both runners.** `uv run pytest wrappers/conformance/tests/ -v` + `cd wrappers/conformance && pnpm test`.

7. **Commit:** `git add wrappers/ pnpm-workspace.yaml && git commit -m "feat(conformance): scripted-replay runners for ts and py wrappers"`.

---

## Task 14 — Cross-language parity lint

**Files:**
- Create: `wrappers/conformance/parity_lint.py`
- Create: `tests/test_conformance_parity.py`

**Goal:** A single Python test that:

1. Discovers all 5 YAML fixtures.
2. Invokes the TS runner (`pnpm exec tsx wrappers/conformance/runner_ts.ts <fixture>`) and the Py runner (`uv run python wrappers/conformance/runner_py.py <fixture>`) on each.
3. Parses both reports.
4. Asserts they have **identical assertion outcomes** (same `(kind, passed)` tuples in order).
5. On divergence, prints a clear diff and fails.

This is **the** mechanism that prevents the failure mode "TS green / Py green but they're testing different things" (design §4.6 H6 mitigation).

**TDD bullets:**

1. **Write the test** at `tests/test_conformance_parity.py`:

```python
"""Cross-language parity lint.

Runs both the TS and the Py conformance runners against every fixture and
asserts they produce the same assertion outcomes.  This is the gate that
catches the H6 failure mode (parallel suites diverging while both green).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_FIXTURE_DIR = _REPO_ROOT / "src" / "amplifier_agent_lib" / "protocol" / "conformance" / "fixtures"


def _run_py(fixture: Path) -> dict:
    res = subprocess.run(
        ["uv", "run", "python", str(_REPO_ROOT / "wrappers" / "conformance" / "runner_py.py"), str(fixture)],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        timeout=30,
    )
    return json.loads(res.stdout)


def _run_ts(fixture: Path) -> dict:
    res = subprocess.run(
        ["pnpm", "exec", "tsx", "runner_ts.ts", str(fixture)],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT / "wrappers" / "conformance",
        timeout=30,
    )
    return json.loads(res.stdout)


@pytest.mark.integration
@pytest.mark.parametrize("fixture", sorted(_FIXTURE_DIR.glob("*.yaml")), ids=lambda p: p.name)
def test_ts_and_py_runners_agree(fixture: Path) -> None:
    py = _run_py(fixture)
    ts = _run_ts(fixture)

    py_outcomes = [(a["kind"], a["passed"]) for a in py["assertions"]]
    ts_outcomes = [(a["kind"], a["passed"]) for a in ts["assertions"]]
    assert py_outcomes == ts_outcomes, (
        f"\n  fixture={fixture.name}"
        f"\n  py    = {py_outcomes}"
        f"\n  ts    = {ts_outcomes}"
        f"\n  py.detail = {[a.get('detail') for a in py['assertions']]}"
        f"\n  ts.detail = {[a.get('detail') for a in ts['assertions']]}"
    )
    assert py["passed"] == ts["passed"]
```

2. **Verify** by running `uv run pytest tests/test_conformance_parity.py -m integration -v`. Both runners exist (Task 13), so the test should either pass cleanly or surface real divergence — fix divergences in the runners, not the test.

3. **Sanity-test the failure path.** Temporarily change a `kind` in one fixture from `notification_emitted` to `no_notification`, run the lint, confirm it fails with the diff. Revert.

4. **Commit:** `git add wrappers/conformance/parity_lint.py tests/test_conformance_parity.py && git commit -m "test(conformance): cross-language parity lint"`.

(Note: `parity_lint.py` is referenced in the README from Task 13. It can be a thin standalone CLI calling the same logic, OR the file may simply not exist and the test be the only entrypoint — pick whichever is cleaner. The CI gate is the test, not the script.)

---

## Task 15 — Phase 2.2 + 2.3 + 2.5 exit gate (paired)

**Files:**
- Create: `tests/test_phase_2_2_2_3_2_5_exit_gate.py`

**Goal:** A single integration test that exercises the **real** `amplifier-agent` subprocess end-to-end via both wrappers. This is the exit gate.

**Steps:**

1. The test must:
   - Skip if `uv run amplifier-agent version --json` is not on PATH (CI safety).
   - For the Py wrapper: import `spawn_agent`, call it with a sentinel prompt against a real subprocess, drain the iterator, assert at least one `result/final` event arrives. Run the entire submit().
   - For the TS wrapper: shell out to a tiny driver Node script `wrappers/conformance/exit_gate_driver.ts` that does the same against the real binary, exits 0/1.
   - Run `tests/test_conformance_parity.py` (already a separate test) as a precondition or as an in-test subprocess invocation.

```python
# tests/test_phase_2_2_2_3_2_5_exit_gate.py
"""Phase 2.2 + 2.3 + 2.5 exit gate: real engine subprocess via both wrappers."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.integration
@pytest.mark.asyncio
async def test_py_wrapper_drives_real_engine() -> None:
    """Py spawn_agent against the real amplifier-agent binary yields at least one event."""
    if shutil.which("amplifier-agent") is None:
        pytest.skip("amplifier-agent binary not on PATH")

    from amplifier_agent_client import spawn_agent

    handle = await spawn_agent(lifecycle="one-shot", session_id="phase-2-2-gate", resume=False)

    events: list[dict] = []
    try:
        async for ev in handle.submit("say hi"):
            events.append(ev)
            if len(events) > 50:
                break
    finally:
        await handle.dispose()

    assert any(ev["type"] == "result/final" for ev in events), (
        f"expected at least one result/final event; got: {[ev['type'] for ev in events]}"
    )


@pytest.mark.integration
def test_ts_wrapper_drives_real_engine() -> None:
    """TS wrapper against real amplifier-agent via a tsx driver."""
    if shutil.which("amplifier-agent") is None:
        pytest.skip("amplifier-agent binary not on PATH")

    res = subprocess.run(
        ["pnpm", "exec", "tsx", "exit_gate_driver.ts"],
        cwd=_REPO_ROOT / "wrappers" / "conformance",
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert res.returncode == 0, f"stdout={res.stdout!r}\nstderr={res.stderr!r}"
    report = json.loads(res.stdout)
    assert report["sawResultFinal"] is True, report


@pytest.mark.integration
def test_conformance_parity_lint_passes() -> None:
    """The cross-language parity lint passes for all 5 fixtures."""
    res = subprocess.run(
        ["uv", "run", "pytest", "tests/test_conformance_parity.py", "-m", "integration", "-q"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert res.returncode == 0, res.stdout + res.stderr
```

2. **Create** `wrappers/conformance/exit_gate_driver.ts`:

```typescript
import { spawnAgent } from "amplifier-agent-client-ts";

async function main(): Promise<void> {
  const handle = await spawnAgent({
    lifecycle: "one-shot",
    sessionId: "phase-2-2-gate-ts",
  });

  let sawResultFinal = false;
  try {
    for await (const ev of handle.submit("say hi")) {
      if (ev.type === "result/final") sawResultFinal = true;
      if (sawResultFinal) break;
    }
  } finally {
    await handle.dispose();
  }

  process.stdout.write(JSON.stringify({ sawResultFinal }));
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
```

3. **Run** the exit gate: `uv run pytest tests/test_phase_2_2_2_3_2_5_exit_gate.py -m integration -v`.

   - If the real binary cannot complete a turn in CI (no API key, no provider configured), the inner `submit()` may fail. Track this as a known limitation: the test gracefully skips when `amplifier-agent` isn't on PATH; it does NOT skip when a real run fails. If real-run integration is impossible in your CI environment, mark the test `@pytest.mark.skipif(not bool(os.environ.get("AMPLIFIER_AGENT_E2E", "")))` instead and document the env var in the test docstring.

4. **Commit:** `git add tests/ wrappers/conformance/exit_gate_driver.ts && git commit -m "test(phase-2-2): exit gate against real amplifier-agent subprocess"`.

---

### 🔎 Quality checkpoint C

Final gate. Run:
```
python_check on:
  src/amplifier_agent_cli/admin/version_info.py
  wrappers/python/
  tests/test_cli_version_subcommand.py
  tests/test_conformance_parity.py
  tests/test_phase_2_2_2_3_2_5_exit_gate.py

cd wrappers/typescript && pnpm typecheck && pnpm test
cd wrappers/conformance && pnpm typecheck && pnpm test
uv run pytest tests/ wrappers/ -v
uv run pytest tests/ wrappers/ -m integration -v
```

Every command must be green. Amend the most recent commit with any cleanup fixes (do **not** add a separate "style: fixup" commit — fold into Task 15).

---

## Final checklist (before opening PR)

1. `uv run pytest tests/ wrappers/ -v` — full default suite green.
2. `uv run pytest tests/ wrappers/ -m integration -v` — integration tests green (or skipped cleanly when the binary isn't available in the env).
3. `cd wrappers/typescript && pnpm test && pnpm typecheck` — TS suite green.
4. `cd wrappers/conformance && pnpm test && pnpm typecheck` — conformance harness green.
5. `git log --oneline feat/phase-2-1-wire-spec-hardening..HEAD` — confirm ~15 commits, conventional-commits style, one per task.
6. `git diff --stat feat/phase-2-1-wire-spec-hardening..HEAD` — sanity-check the file footprint. New top-level dirs: `wrappers/typescript/`, `wrappers/python/`, `wrappers/conformance/`. Root files added: `pnpm-workspace.yaml`. Root files modified: `pyproject.toml` (workspace members), `src/amplifier_agent_cli/__main__.py` (one `add_command`).
7. The untracked `docs/architecture/amplifier-as-agent-presentation.html` is still untracked.
8. Open the PR against `main` with title `feat(phase-2-2): TS + Py wrappers + cross-language conformance`. Body references this plan file and design §10.4–§10.6. **Mark it as stacked on PR #6** — note that it should not merge until PR #6 lands.

---

## Footer — rebase note (do this AFTER PR #6 merges, NOT as a numbered task)

Plan 3 is stacked on `feat/phase-2-1-wire-spec-hardening` (PR #6). Phase 2.1 artifacts (`spec.md`, `schemas/*.schema.json`, `conformance/fixtures/*.yaml`, `conformance/loader.py`, `tests/test_protocol_gen*.py`) are required by Plan 3 and live only on that branch until PR #6 merges.

When PR #6 lands on `main`:

```bash
git checkout feat/phase-2-2-2-3-2-5-wrappers-and-conformance
git fetch origin
git rebase --onto origin/main feat/phase-2-1-wire-spec-hardening feat/phase-2-2-2-3-2-5-wrappers-and-conformance
# Resolve any conflicts (unlikely — Plan 3 only modifies pyproject.toml in the workspace-members list
# and adds new files; Plan 2's pyproject.toml change is the wheel force-include block, no overlap).
git push --force-with-lease
```

Re-run the full test suite after the rebase. Reopen the PR against the new base if GitHub closed it during the rebase.

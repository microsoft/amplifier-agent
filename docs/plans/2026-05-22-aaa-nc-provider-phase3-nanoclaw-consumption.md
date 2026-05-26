# AaA NC Provider — Phase 3: NanoClaw Consumption Implementation Plan

> **For execution:** Use `/execute-plan` mode or the subagent-driven-development recipe.

**Prerequisite:** Phase 2 plan must have shipped `amplifier-agent v0.2.0` to PyPI **and**
`amplifier-agent-client-ts@0.2.0` to npm. Verify both artifacts are installable from a clean
environment before starting this plan.

**Repo:** NanoClaw (`/Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh`). NOT amplifier-agent.

**Goal:** Make `amplifier-agent` selectable as a NanoClaw provider — install the binary at image
build time, register the host-side mount and provider config, implement the in-container adapter
with B1 buffer chaining and structured event translation, gate the wrapper-engine version
coordination with a CI lint, and prove end-to-end happy-path and steering scenarios work.

**Architecture:** 6-line Dockerfile addition + 3 host-side files (provider registration, runtime
mount helper, version lint) + 3 in-container files (provider class + 2 pure-function helpers) + 2
E2E test scenarios. The adapter is wire-uniform with future hosts (Paperclip, OpenCode, Claude Code)
via the `initialize.host.capabilities` field declared in `SpawnAgentParams`.

**Tech Stack:** TypeScript (Bun runtime in-container, Node/pnpm host-side), vitest (host tests),
bun:test (container tests), Docker multi-stage build, `semver` npm library for CI lint.

**Design references:**
- Authoritative spec: `/Users/mpaidiparthy/repos/AaA/opus-recon/amplifier-agent/docs/designs/2026-05-22-aaa-v2-amplifier-agent-nc-provider.md`
- Wire design: `/Users/mpaidiparthy/repos/AaA/opus-recon/amplifier-agent/docs/designs/2026-05-20-aaa-v2-wrapper-and-wire.md`

---

## Prerequisites Verification

Run these commands before touching any code. If any check fails, stop and complete Phase 2 first.

```bash
cd /Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh

# 1. Verify amplifier-agent-client-ts@0.2.0 is on npm
npm view amplifier-agent-client-ts@0.2.0 version
# Expected output: 0.2.0

# 2. Verify amplifier-agent@0.2.0 is on PyPI
pip index versions amplifier-agent 2>/dev/null | grep 0.2.0
# Expected output: line containing "0.2.0"

# 3. Verify amplifier-agent installs cleanly
uv tool install --dry-run "amplifier-agent==0.2.0"
# Expected: no errors

# 4. Verify the NC repo is on a clean feature branch
git status
git checkout -b feat/amplifier-agent-provider   # only if not already on one
```

---

## Known Constraints and Build-time Prerequisites

### `file:` package reference for `amplifier-agent-client-ts`

**DTU verification finding F3.** The dev-time pin in `container/agent-runner/package.json` (`"amplifier-agent-client-ts": "file:../../../amplifier-agent/wrappers/typescript"`) works in repo-sibling dev layouts and in DTU, but does **NOT** work in a standalone `docker build` because the Dockerfile build context (typically `container/`) does not include the sibling amplifier-agent tree. To produce a buildable image, EITHER (a) publish `amplifier-agent-client-ts@0.2.0` to npm and switch the pin to `^0.2.0`, OR (b) add a `COPY` step in the Dockerfile that brings the wrapper source into the build context (requires running `docker build` from the parent directory or using `--build-context wrapper=../amplifier-agent/wrappers/typescript`). This is tracked as DTU verification finding F3.

> **Why this matters for Phase 3:** Task 5 Step 1 (`bun add amplifier-agent-client-ts@^0.2.0`) resolves this by switching from the dev-time `file:` pin to the published npm package. The phase prerequisite gate on `amplifier-agent-client-ts@0.2.0` being available on npm is therefore load-bearing — do not skip it.

---

## Dependency Graph

```
N2 (CI lint) ───────────────────────────────────────────────────────────┐
N1 (Dockerfile) ────────────────────────────────────────────────────────┤
N3 (host-side) ─────────────────────────────────────────────────────────┤
                                                                         ▼
                                               N4 (adapter) ──── N5 (E2E happy path)
                                                                         │
                                                                         ▼
                                                               N6 (E2E steering)
                                                                         │
                                                                         ▼
                                                                N7 (rollout plumbing)
```

N2, N1, N3 can all land in any order — none blocks the others. N4 requires both N1 and N3.
N5 requires N1 through N4. N6 requires N5. N7 requires N5 and N6.

---

## Task 1: Add semver devDependency (N2 prep)

**Files:**
- Modify: `package.json` (root of NC repo)
- Modify: `pnpm-lock.yaml` (auto-generated — commit it)

**Step 1: Add semver to root devDependencies**

```bash
cd /Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh
pnpm add -D semver @types/semver
```

This updates both `package.json` and `pnpm-lock.yaml`. Verify both changed:

```bash
git diff --stat
# Expected: package.json | N +++ and pnpm-lock.yaml | N +++
```

**Step 2: Verify the import works**

```bash
pnpm exec tsx -e "import semver from 'semver'; console.log(semver.satisfies('0.2.0', '^0.2.0'))"
# Expected output: true
```

**Step 3: Commit**

```bash
git add package.json pnpm-lock.yaml
git commit -m "chore(deps): add semver for CI version lint"
```

---

## Task 2: Write failing CI lint test (N2 TDD — test first)

**Files:**
- Create: `scripts/lint-aaa-version.test.ts`

vitest picks up `scripts/**/*.test.ts` (see `vitest.config.ts`: `include: ['src/**/*.test.ts',
'setup/**/*.test.ts', 'scripts/**/*.test.ts']`). Host tests run with `pnpm exec vitest run`.

**Step 1: Create the test file**

```typescript
// scripts/lint-aaa-version.test.ts
import { describe, it, expect } from 'vitest';
import { checkVersionCompatibility } from './lint-aaa-version.js';

describe('checkVersionCompatibility', () => {
  it('passes when version exactly satisfies a caret range', () => {
    const result = checkVersionCompatibility('^0.2.0', '0.2.0');
    expect(result.ok).toBe(true);
  });

  it('passes when a later patch version satisfies the range', () => {
    const result = checkVersionCompatibility('^0.2.0', '0.2.3');
    expect(result.ok).toBe(true);
  });

  it('fails when version is below the range floor', () => {
    const result = checkVersionCompatibility('^0.2.0', '0.1.9');
    expect(result.ok).toBe(false);
    expect(result.message).toMatch(/does not satisfy/);
  });

  it('fails when version is above the caret range ceiling (0.x.y — minor locked)', () => {
    // ^0.2.0 means >=0.2.0 <0.3.0 — 0.3.0 must NOT satisfy
    const result = checkVersionCompatibility('^0.2.0', '0.3.0');
    expect(result.ok).toBe(false);
  });

  it('fails when version string is not valid semver', () => {
    const result = checkVersionCompatibility('^0.2.0', 'latest');
    expect(result.ok).toBe(false);
    expect(result.message).toMatch(/not.*valid semver/i);
  });

  it('returns warn: true when range allows minor-version upgrades (^1.x.x)', () => {
    // ^1.2.0 satisfies 1.3.0 (minor bump above floor) — should warn
    const result = checkVersionCompatibility('^1.2.0', '1.2.0');
    expect(result.ok).toBe(true);
    expect(result.warn).toBe(true);
  });
});
```

**Step 2: Run test to verify it fails**

```bash
cd /Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh
pnpm exec vitest run scripts/lint-aaa-version.test.ts
# Expected: FAIL — "Cannot find module './lint-aaa-version.js'"
```

---

## Task 3: Implement lint-aaa-version.ts (N2 TDD — make tests pass)

**Files:**
- Create: `scripts/lint-aaa-version.ts`

**Step 1: Write the implementation**

```typescript
// scripts/lint-aaa-version.ts
//
// CI version-coordination guard.
// Reads container/agent-runner/package.json → amplifier-agent-client-ts range.
// Reads container/Dockerfile → AMPLIFIER_AGENT_VERSION ARG default.
// Exits non-zero if Dockerfile version does not satisfy package.json range.
//
// Usage (CLI): pnpm exec tsx scripts/lint-aaa-version.ts
// Usage (test): import { checkVersionCompatibility } from './lint-aaa-version.js'

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import semver from 'semver';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..');

// ── Exported pure function (unit-testable) ───────────────────────────────────

export interface CompatibilityResult {
  ok: boolean;
  message: string;
  /**
   * True when the range allows minor-version upgrades above the floor,
   * meaning multiple distinct engine versions can satisfy it.
   */
  warn?: boolean;
}

/**
 * Check whether semver `version` satisfies semver `range`.
 * Returns {ok, message, warn?}. Never throws; all errors surface as ok:false.
 */
export function checkVersionCompatibility(range: string, version: string): CompatibilityResult {
  const clean = semver.clean(version);
  if (!clean) {
    return { ok: false, message: `"${version}" is not a valid semver string` };
  }
  if (!semver.satisfies(clean, range)) {
    return {
      ok: false,
      message:
        `"${clean}" does not satisfy range "${range}". ` +
        `Bump AMPLIFIER_AGENT_VERSION in container/Dockerfile to match the range, ` +
        `or update the range in container/agent-runner/package.json.`,
    };
  }

  // Warn when the range allows minor-version upgrades (next minor still satisfies).
  const minVer = semver.minVersion(range);
  let warn = false;
  if (minVer) {
    const nextMinor = semver.inc(minVer.version, 'minor');
    if (nextMinor && semver.satisfies(nextMinor, range)) {
      warn = true;
    }
  }

  const suffix = warn ? ' (WARN: range allows minor-version upgrades)' : '';
  return { ok: true, message: `"${clean}" satisfies "${range}"${suffix}`, warn };
}

// ── CLI entry point ──────────────────────────────────────────────────────────
// Only runs when invoked directly, not when imported for tests.

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  // 1. Read the package.json range
  const pkgPath = path.join(ROOT, 'container/agent-runner/package.json');
  const pkg = JSON.parse(fs.readFileSync(pkgPath, 'utf8')) as {
    dependencies?: Record<string, string>;
  };
  const range = pkg.dependencies?.['amplifier-agent-client-ts'];
  if (!range) {
    console.error(
      'ERROR: amplifier-agent-client-ts not found in container/agent-runner/package.json dependencies.\n' +
        '  Add it: cd container/agent-runner && bun add amplifier-agent-client-ts@^0.2.0',
    );
    process.exit(1);
  }

  // 2. Read the Dockerfile ARG default
  const dockerfilePath = path.join(ROOT, 'container/Dockerfile');
  const dockerfile = fs.readFileSync(dockerfilePath, 'utf8');
  const match = /^ARG\s+AMPLIFIER_AGENT_VERSION=(.+)$/m.exec(dockerfile);
  if (!match) {
    console.error(
      'ERROR: ARG AMPLIFIER_AGENT_VERSION=<version> not found in container/Dockerfile.\n' +
        '  Add it (below the ARG PNPM_VERSION line):\n' +
        '    ARG AMPLIFIER_AGENT_VERSION=0.2.0',
    );
    process.exit(1);
  }
  const version = match[1].trim();

  console.log(`amplifier-agent-client-ts range (package.json): ${range}`);
  console.log(`AMPLIFIER_AGENT_VERSION (Dockerfile ARG default): ${version}`);

  // 3. Check
  const result = checkVersionCompatibility(range, version);
  if (!result.ok) {
    console.error(`\nERROR: ${result.message}`);
    process.exit(1);
  }
  if (result.warn) {
    console.warn(`\nWARN: ${result.message}`);
  }
  console.log(`\nOK: ${result.message}`);
  process.exit(0);
}
```

**Step 2: Run tests to verify they pass**

```bash
cd /Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh
pnpm exec vitest run scripts/lint-aaa-version.test.ts
# Expected: 6 tests PASS
```

**Step 3: Verify the script fails correctly before the ARG is in the Dockerfile**

```bash
pnpm exec tsx scripts/lint-aaa-version.ts
# Expected: ERROR: ARG AMPLIFIER_AGENT_VERSION=<version> not found in container/Dockerfile
echo $?   # should print 1
```

---

## Task 4: Wire lint into CI workflow (N2 CI)

**Files:**
- Modify: `.github/workflows/ci.yml`

**Step 1: Add the version-lint step to ci.yml**

Open `.github/workflows/ci.yml`. The current file ends with:

```yaml
      - name: Container tests
        working-directory: container/agent-runner
        run: bun test
```

Find the block right after `pnpm install --frozen-lockfile`. Insert a new step **after** the
`Install agent-runner deps (Bun)` step and **before** the `Format check` step:

```yaml
      - name: Version pin lint (amplifier-agent)
        run: pnpm exec tsx scripts/lint-aaa-version.ts
```

The final ordering of the first several steps should be:

```yaml
      - run: pnpm install --frozen-lockfile
      - name: Install agent-runner deps (Bun)
        working-directory: container/agent-runner
        run: bun install --frozen-lockfile

      - name: Version pin lint (amplifier-agent)
        run: pnpm exec tsx scripts/lint-aaa-version.ts

      - name: Format check
        run: pnpm run format:check
      # ... rest unchanged
```

**NOTE:** This CI step will fail until Task 5 adds `ARG AMPLIFIER_AGENT_VERSION` to the Dockerfile
and pins `amplifier-agent-client-ts` in `container/agent-runner/package.json`. That's intentional —
the lint guards the pin. If you need CI to pass before Task 5, skip this step temporarily.

**Step 2: Run host typecheck to ensure no type errors in new script**

```bash
cd /Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh
pnpm exec tsc --noEmit
# Expected: no errors
```

**Step 3: Commit all N2 work**

```bash
git add scripts/lint-aaa-version.ts scripts/lint-aaa-version.test.ts .github/workflows/ci.yml
git commit -m "feat(ci): add amplifier-agent version pin lint (N2)"
```

---

## Task 5: Dockerfile edits + package.json version pin (N1)

**Files:**
- Modify: `container/Dockerfile`
- Modify: `container/agent-runner/package.json`
- Modify: `container/agent-runner/bun.lock` (auto-updated by `bun add`)

**Step 1: Pin amplifier-agent-client-ts in container/agent-runner/package.json**

```bash
cd /Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh/container/agent-runner
bun add amplifier-agent-client-ts@^0.2.0
# This adds the dependency AND updates bun.lock
```

Verify the install worked:

```bash
bun -e "import { AaaError } from 'amplifier-agent-client-ts'; console.log('OK:', typeof AaaError)"
# Expected: OK: function
```

**Step 2: Edit container/Dockerfile**

The current Dockerfile ends with (lines 122–132):

```dockerfile
# ---- Workspace + permissions -------------------------------------------------
RUN mkdir -p /workspace/group /workspace/extra && \
    chown -R node:node /workspace && \
    chmod 777 /home/node

USER node
WORKDIR /workspace/group

# tini is PID 1, reaps zombies, forwards signals cleanly.
ENTRYPOINT ["/usr/bin/tini", "--", "/app/entrypoint.sh"]
```

Replace the `USER node` line (keep everything else). The new amplifier-agent block goes **between**
the `chmod 777 /home/node` line and `WORKDIR /workspace/group`:

```dockerfile
# ---- Workspace + permissions -------------------------------------------------
RUN mkdir -p /workspace/group /workspace/extra && \
    chown -R node:node /workspace && \
    chmod 777 /home/node

# ---- amplifier-agent --------------------------------------------------------
# uv and the amplifier-agent binary are installed as root so they land in
# /usr/local/bin (system-wide PATH). The prepare and doctor steps run as the
# node user who will execute the binary at runtime.
RUN curl -LsSf https://astral.sh/uv/install.sh | sh -s -- --no-modify-path && \
    mv /root/.local/bin/uv /usr/local/bin/uv
ARG AMPLIFIER_AGENT_VERSION=0.2.0
# uv 0.11+ removed the --bin-dir flag; use UV_TOOL_BIN_DIR env var instead (DTU finding F2)
RUN UV_TOOL_BIN_DIR=/usr/local/bin uv tool install "amplifier-agent==${AMPLIFIER_AGENT_VERSION}"
USER node
RUN amplifier-agent prepare
RUN amplifier-agent doctor --strict

WORKDIR /workspace/group

# tini is PID 1, reaps zombies, forwards signals cleanly.
ENTRYPOINT ["/usr/bin/tini", "--", "/app/entrypoint.sh"]
```

**Step 3: Verify the Docker build passes locally**

```bash
cd /Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh/container
docker build \
  --build-arg AMPLIFIER_AGENT_VERSION=0.2.0 \
  -t nanoclaw-agent:aaa-test \
  .
```

Expected: build exits 0. The `RUN amplifier-agent doctor --strict` step MUST appear and MUST exit 0.
If it fails, the image build fails — this is the design's gate (D11). Debug with:

```bash
# Run doctor without --strict to see which checks are failing
docker run --rm --entrypoint amplifier-agent nanoclaw-agent:aaa-test doctor
```

**Step 4: Verify the version lint now passes**

```bash
cd /Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh
pnpm exec tsx scripts/lint-aaa-version.ts
# Expected:
# amplifier-agent-client-ts range (package.json): ^0.2.0
# AMPLIFIER_AGENT_VERSION (Dockerfile ARG default): 0.2.0
# OK: "0.2.0" satisfies "^0.2.0"
```

**Step 5: Commit**

```bash
cd /Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh
git add container/Dockerfile \
        container/agent-runner/package.json \
        container/agent-runner/bun.lock
git commit -m "feat(container): install amplifier-agent via uv, prepare+doctor gate (N1)"
```

---

## Task 6: Update NC provider types.ts (N3 prep)

**Files:**
- Modify: `container/agent-runner/src/providers/types.ts`

**Why:** `McpServerConfig` currently only models stdio servers (`{ command, args, env }`).
The wire's `McpServerConfig` (from `amplifier-agent-client-ts`) has a required `transport`
discriminant. The `mcp-translator.ts` (Task 9) converts between the two. Adding `transport?` to
NC's type makes it forward-compatible while keeping all existing usages (Claude, mock) unchanged.

The `mcpServers?` addition to `QueryInput` lets per-query MCP servers flow through the adapter.
All existing providers silently ignore fields they don't read.

**Step 1: Edit types.ts**

Open `container/agent-runner/src/providers/types.ts`. Make these two additive changes:

**Change A — extend `McpServerConfig`** (add `transport?`, `url?`, `headers?`):

```typescript
export interface McpServerConfig {
  command: string;
  args: string[];
  env: Record<string, string>;
  /**
   * Transport type. Defaults to 'stdio' when absent.
   * Added for amplifier-agent wire compatibility (design §4.3).
   * Existing Claude/mock MCP configs that omit this field are treated as stdio.
   */
  transport?: 'stdio' | 'sse' | 'streamable_http';
  /** URL for sse / streamable_http transports. */
  url?: string;
  /** HTTP headers for sse / streamable_http transports. */
  headers?: Record<string, string>;
}
```

**Change B — add `mcpServers?` to `QueryInput`**:

```typescript
export interface QueryInput {
  /** Initial prompt (already formatted by agent-runner). */
  prompt: string;

  /**
   * Opaque continuation token from a previous query. The provider decides
   * what this means (session ID, thread ID, nothing at all).
   */
  continuation?: string;

  /** Working directory inside the container. */
  cwd: string;

  /**
   * System context to inject. Providers translate this into whatever their
   * SDK expects (preset append, full system prompt, per-turn injection…).
   */
  systemContext?: {
    instructions?: string;
  };

  /**
   * Per-query MCP server configs. Providers that don't support MCP ignore this.
   * Merged with any constructor-level MCP config (query-level wins on conflict).
   * Added for amplifier-agent (design §4.1.4 / §4.3).
   */
  mcpServers?: Record<string, McpServerConfig>;
}
```

**Step 2: Run container typecheck — no regressions**

```bash
cd /Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh/container/agent-runner
bun run typecheck
# Expected: no errors
```

**Step 3: Run all existing container tests**

```bash
bun test
# Expected: ALL existing tests PASS (factory.test.ts, poll-loop.test.ts, etc.)
```

**Step 4: Commit**

```bash
cd /Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh
git add container/agent-runner/src/providers/types.ts
git commit -m "feat(provider-types): add transport/mcpServers fields for amplifier-agent (N3)"
```

---

## Task 7: Add readwriteMountArgs to container-runtime.ts (N3)

**Files:**
- Modify: `src/container-runtime.ts`
- Modify: `src/container-runtime.test.ts`

**Why:** `container-runtime.ts` has `readonlyMountArgs` (returns `['-v', 'host:container:ro']`).
The amplifier-agent state volume is RW. Adding a symmetric `readwriteMountArgs` helper documents
the pattern and provides test coverage. The `VolumeMount` interface's `readonly: false` already
works in `buildContainerArgs` — this helper is for parity and clarity.

**Step 1: Write the failing test first**

Open `src/container-runtime.test.ts`. Add `readwriteMountArgs` to the import at the top:

```typescript
import {
  CONTAINER_RUNTIME_BIN,
  readonlyMountArgs,
  readwriteMountArgs,    // ADD THIS
  stopContainer,
  ensureContainerRuntimeRunning,
  cleanupOrphans,
} from './container-runtime.js';
```

Then add a new describe block at the end of the file:

```typescript
describe('readwriteMountArgs', () => {
  it('returns a -v flag with no :ro suffix', () => {
    expect(readwriteMountArgs('/host/state', '/container/state')).toEqual([
      '-v',
      '/host/state:/container/state',
    ]);
  });

  it('differs from readonlyMountArgs (no :ro)', () => {
    const ro = readonlyMountArgs('/h', '/c');
    const rw = readwriteMountArgs('/h', '/c');
    expect(rw).not.toEqual(ro);
    expect(rw.join(' ')).not.toContain(':ro');
  });
});
```

**Step 2: Run test to verify it fails**

```bash
cd /Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh
pnpm exec vitest run src/container-runtime.test.ts
# Expected: FAIL — readwriteMountArgs is not exported from container-runtime.ts
```

**Step 3: Add readwriteMountArgs to container-runtime.ts**

Open `src/container-runtime.ts`. After the existing `readonlyMountArgs` function, add:

```typescript
/** Returns CLI args for a readwrite bind mount. */
export function readwriteMountArgs(hostPath: string, containerPath: string): string[] {
  return ['-v', `${hostPath}:${containerPath}`];
}
```

**Step 4: Run tests to verify they pass**

```bash
pnpm exec vitest run src/container-runtime.test.ts
# Expected: all tests PASS (including new readwriteMountArgs tests)
```

**Step 5: Commit**

```bash
git add src/container-runtime.ts src/container-runtime.test.ts
git commit -m "feat(container-runtime): add readwriteMountArgs helper for RW volume mounts"
```

---

## Task 8: Host-side provider registration (N3)

**Files:**
- Create: `src/providers/amplifier-agent.ts`
- Create: `src/providers/amplifier-agent.test.ts`
- Modify: `src/providers/index.ts`

**Key facts from the actual codebase:**
- `ProviderContainerContext` has: `{ sessionDir: string; agentGroupId: string; hostEnv: NodeJS.ProcessEnv }`
- `VolumeMount` has: `{ hostPath: string; containerPath: string; readonly: boolean }`
- `DATA_DIR` is exported from `src/config.ts` as `path.resolve(process.cwd(), 'data')`
- `src/providers/index.ts` is currently empty (no existing imports) — this is the first registration

**Step 1: Write the failing test**

Create `src/providers/amplifier-agent.test.ts`:

```typescript
import { describe, it, expect, vi } from 'vitest';

// Mock DATA_DIR so the test doesn't touch the filesystem.
vi.mock('../config.js', () => ({ DATA_DIR: '/mock/data' }));

// Mock fs to prevent actual directory creation.
vi.mock('node:fs', () => ({ default: { mkdirSync: vi.fn() } }));

// Import the pure factory function (testable without the side-effect registration).
import { buildAmplifierAgentContainerConfig } from './amplifier-agent.js';

describe('buildAmplifierAgentContainerConfig', () => {
  it('derives the host mount path from DATA_DIR and agentGroupId', () => {
    const result = buildAmplifierAgentContainerConfig({
      agentGroupId: 'grp-abc123',
      sessionDir: '/mock/session/grp-abc123/sess-xyz',
      hostEnv: {},
    });
    expect(result.mounts).toHaveLength(1);
    expect(result.mounts![0].hostPath).toBe('/mock/data/amplifier-agent/grp-abc123');
  });

  it('mounts to /home/node/.local/state/amplifier-agent inside the container', () => {
    const result = buildAmplifierAgentContainerConfig({
      agentGroupId: 'x',
      sessionDir: '/mock/s',
      hostEnv: {},
    });
    expect(result.mounts![0].containerPath).toBe('/home/node/.local/state/amplifier-agent');
  });

  it('sets readonly: false so the engine can write session transcripts', () => {
    const result = buildAmplifierAgentContainerConfig({
      agentGroupId: 'x',
      sessionDir: '/mock/s',
      hostEnv: {},
    });
    expect(result.mounts![0].readonly).toBe(false);
  });

  it('passes AMPLIFIER_AGENT_LOG_LEVEL env var to the container', () => {
    const result = buildAmplifierAgentContainerConfig({
      agentGroupId: 'x',
      sessionDir: '/mock/s',
      hostEnv: {},
    });
    expect(result.env!.AMPLIFIER_AGENT_LOG_LEVEL).toBe('info');
  });
});
```

**Step 2: Run test to verify it fails**

```bash
cd /Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh
pnpm exec vitest run src/providers/amplifier-agent.test.ts
# Expected: FAIL — Cannot find module './amplifier-agent.js'
```

**Step 3: Create src/providers/amplifier-agent.ts**

```typescript
/**
 * Host-side container config for the amplifier-agent provider.
 *
 * Declares the per-group host-mounted state volume that persists session
 * transcripts across container restarts (design §4.4 / D7).
 *
 * Volume layout on host:
 *   $DATA_DIR/amplifier-agent/$AGENT_GROUP_ID/
 *     sessions/<sessionId>/transcript.jsonl    ← engine writes here
 *     sessions/<sessionId>/metadata.json
 *
 * Mounted inside the container at:
 *   /home/node/.local/state/amplifier-agent/   ← $XDG_STATE_HOME
 */
import fs from 'node:fs';
import path from 'node:path';

import { DATA_DIR } from '../config.js';
import {
  registerProviderContainerConfig,
  type ProviderContainerContext,
  type ProviderContainerContribution,
} from './provider-container-registry.js';

/**
 * Pure factory — exported for unit testing without the registry side-effect.
 */
export function buildAmplifierAgentContainerConfig(
  ctx: ProviderContainerContext,
): ProviderContainerContribution {
  const hostPath = path.join(DATA_DIR, 'amplifier-agent', ctx.agentGroupId);
  // Create the directory before Docker tries to bind-mount it.
  // If Docker creates it on-demand it does so owned by root; the container's
  // `node` user would then fail to write session transcripts.
  fs.mkdirSync(hostPath, { recursive: true });
  return {
    env: {
      AMPLIFIER_AGENT_LOG_LEVEL: 'info',
    },
    mounts: [
      {
        hostPath,
        containerPath: '/home/node/.local/state/amplifier-agent',
        readonly: false,
      },
    ],
  };
}

registerProviderContainerConfig('amplifier-agent', buildAmplifierAgentContainerConfig);
```

**Step 4: Edit src/providers/index.ts**

The file currently reads:

```typescript
// Host-side provider container-config barrel.
// Providers that need host-side container setup (extra mounts, env passthrough,
// per-session directories) self-register on import. Providers with no host
// needs (claude, mock) don't appear here.
//
// Skills add a new provider by appending one import line below.
```

Append the import line:

```typescript
// Host-side provider container-config barrel.
// Providers that need host-side container setup (extra mounts, env passthrough,
// per-session directories) self-register on import. Providers with no host
// needs (claude, mock) don't appear here.
//
// Skills add a new provider by appending one import line below.
import './amplifier-agent.js';
```

**Step 5: Run tests**

```bash
pnpm exec vitest run src/providers/amplifier-agent.test.ts
# Expected: 4 tests PASS
```

**Step 6: Run full host typecheck**

```bash
pnpm exec tsc --noEmit
# Expected: no errors
```

**Step 7: Commit**

```bash
git add src/providers/amplifier-agent.ts \
        src/providers/amplifier-agent.test.ts \
        src/providers/index.ts
git commit -m "feat(providers): register amplifier-agent host-side container config (N3)"
```

---

## Task 9: mcp-translator.ts — TDD (N4)

**Files:**
- Create: `container/agent-runner/src/providers/amplifier-agent/mcp-translator.test.ts`
- Create: `container/agent-runner/src/providers/amplifier-agent/mcp-translator.ts`

Container tests use `bun test` with `import { describe, it, expect } from 'bun:test'`.

**Step 1: Create the directory and test file**

```bash
mkdir -p /Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh/container/agent-runner/src/providers/amplifier-agent
```

Create `container/agent-runner/src/providers/amplifier-agent/mcp-translator.test.ts`:

```typescript
import { describe, it, expect } from 'bun:test';
import { translateMcp } from './mcp-translator.js';

describe('translateMcp', () => {
  it('returns undefined when input is undefined', () => {
    expect(translateMcp(undefined)).toBeUndefined();
  });

  it('passes through empty object (no servers to validate)', () => {
    const result = translateMcp({});
    expect(result).toBeDefined();
    expect(Object.keys(result!).length).toBe(0);
  });

  it('defaults missing transport to "stdio"', () => {
    const result = translateMcp({
      'my-server': { command: 'bun', args: ['server.ts'], env: {} },
    });
    expect(result!['my-server'].transport).toBe('stdio');
  });

  it('preserves an explicit stdio transport', () => {
    const result = translateMcp({
      'nc-mcp': { command: 'npx', args: ['-y', 'my-mcp'], env: { KEY: 'val' }, transport: 'stdio' },
    });
    expect(result!['nc-mcp'].transport).toBe('stdio');
    expect(result!['nc-mcp'].command).toBe('npx');
    expect(result!['nc-mcp'].env).toEqual({ KEY: 'val' });
  });

  it('preserves sse transport with url and headers', () => {
    const result = translateMcp({
      'sse-srv': {
        command: '',
        args: [],
        env: {},
        transport: 'sse',
        url: 'https://example.com/mcp',
        headers: { Authorization: 'Bearer tok' },
      },
    });
    expect(result!['sse-srv'].transport).toBe('sse');
    expect(result!['sse-srv'].url).toBe('https://example.com/mcp');
    expect(result!['sse-srv'].headers).toEqual({ Authorization: 'Bearer tok' });
  });

  it('throws with the server name when transport is unknown', () => {
    expect(() =>
      translateMcp({
        'my-bad-server': {
          command: 'x',
          args: [],
          env: {},
          transport: 'grpc' as unknown as 'stdio',
        },
      }),
    ).toThrow(/my-bad-server.*unknown transport 'grpc'/);
  });
});
```

**Step 2: Run test to verify it fails**

```bash
cd /Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh/container/agent-runner
bun test src/providers/amplifier-agent/mcp-translator.test.ts
# Expected: FAIL — Cannot find module './mcp-translator.js'
```

**Step 3: Create mcp-translator.ts**

```typescript
/**
 * mcp-translator.ts — pure function helper.
 *
 * Validates and normalises NC's McpServerConfig map into the wire format
 * expected by SpawnAgentParams.mcpServers (amplifier-agent-client-ts@0.2.0).
 *
 * NC's McpServerConfig.transport is optional (defaults to 'stdio').
 * The wire requires transport as a discriminant field.
 * This function validates the value and fills in the default.
 *
 * Identity passthrough for all other fields.
 *
 * Design reference: §4.3 of 2026-05-22-aaa-v2-amplifier-agent-nc-provider.md
 */

import type { McpServerConfig as NcMcpServerConfig } from '../types.js';

/**
 * Wire-level McpServerConfig shape.
 * Mirrors SpawnAgentParams.mcpServers from amplifier-agent-client-ts@0.2.0.
 * Defined inline so this module compiles before the package is installed.
 * When the package is available, use: import type { McpServerConfig } from 'amplifier-agent-client-ts'
 */
export interface WireMcpServerConfig {
  transport: 'stdio' | 'sse' | 'streamable_http';
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  url?: string;
  headers?: Record<string, string>;
}

const VALID_TRANSPORTS = new Set<string>(['stdio', 'sse', 'streamable_http']);

/**
 * Translate NC MCP server configs to wire format.
 * Returns undefined when input is undefined (no MCP configured).
 * Throws synchronously with a descriptive message on invalid transport.
 */
export function translateMcp(
  input: Record<string, NcMcpServerConfig> | undefined,
): Record<string, WireMcpServerConfig> | undefined {
  if (!input) return undefined;

  const result: Record<string, WireMcpServerConfig> = {};

  for (const [name, cfg] of Object.entries(input)) {
    const transport = (cfg.transport ?? 'stdio') as string;
    if (!VALID_TRANSPORTS.has(transport)) {
      throw new Error(
        `mcp-translator: server '${name}' has unknown transport '${transport}'. ` +
          `Valid: ${[...VALID_TRANSPORTS].join(', ')}`,
      );
    }
    result[name] = {
      transport: transport as WireMcpServerConfig['transport'],
      command: cfg.command,
      args: cfg.args,
      env: cfg.env,
      ...(cfg.url != null ? { url: cfg.url } : {}),
      ...(cfg.headers != null ? { headers: cfg.headers } : {}),
    };
  }

  return result;
}
```

**Step 4: Run tests to verify they pass**

```bash
bun test src/providers/amplifier-agent/mcp-translator.test.ts
# Expected: 6 tests PASS
```

**Step 5: Commit**

```bash
cd /Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh
git add container/agent-runner/src/providers/amplifier-agent/mcp-translator.ts \
        container/agent-runner/src/providers/amplifier-agent/mcp-translator.test.ts
git commit -m "feat(adapter): mcp-translator pure function with TDD (N4)"
```

---

## Task 10: event-translator.ts — TDD (N4)

**Files:**
- Create: `container/agent-runner/src/providers/amplifier-agent/event-translator.test.ts`
- Create: `container/agent-runner/src/providers/amplifier-agent/event-translator.ts`

**Context on DisplayEvent:** `amplifier-agent-client-ts@0.2.0` exports `DisplayEvent` from its
public API (confirmed in the package's index.ts). After Task 5, verify with:

```bash
cd /Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh/container/agent-runner
bun -e "import('amplifier-agent-client-ts').then(m => console.log(Object.keys(m)))"
# Expected: includes 'AaaError', 'SessionHandle', and type exports including 'DisplayEvent'
```

The design (§4.2) specifies `DisplayEvent` as a discriminated union with these `type` values:
`'message'`, `'tool_use'`, `'tool_result'`, `'progress'`, `'subagent_progress'`, `'error'`.
The translator also handles unknown types (future wire additions) by emitting `activity`.

**Step 1: Create the test file**

Create `container/agent-runner/src/providers/amplifier-agent/event-translator.test.ts`:

```typescript
import { describe, it, expect } from 'bun:test';
import { translate, type TranslateCtx } from './event-translator.js';

const NO_MCP: TranslateCtx = { mcpServersProvided: false, sessionId: 's_test' };
const WITH_MCP: TranslateCtx = { mcpServersProvided: true, sessionId: 's_test' };

// ── message ──────────────────────────────────────────────────────────────────
describe('translate: message', () => {
  it('produces [activity, result] for a message event', () => {
    const out = translate({ type: 'message', text: 'Hello from agent' }, NO_MCP);
    expect(out).toHaveLength(2);
    expect(out[0]).toEqual({ type: 'activity' });
    expect(out[1]).toEqual({ type: 'result', text: 'Hello from agent' });
  });
});

// ── tool events ───────────────────────────────────────────────────────────────
describe('translate: tool events', () => {
  it('converts tool_use to a single activity', () => {
    const out = translate({ type: 'tool_use', name: 'bash', input: {} }, NO_MCP);
    expect(out).toEqual([{ type: 'activity' }]);
  });

  it('converts tool_result to a single activity', () => {
    const out = translate({ type: 'tool_result', toolUseId: 'tu_1', content: 'done' }, NO_MCP);
    expect(out).toEqual([{ type: 'activity' }]);
  });
});

// ── progress ──────────────────────────────────────────────────────────────────
describe('translate: progress', () => {
  it('forwards progress message as-is', () => {
    const out = translate({ type: 'progress', message: 'Searching files…' }, NO_MCP);
    expect(out).toEqual([{ type: 'progress', message: 'Searching files…' }]);
  });
});

// ── subagent_progress (SC-5 deferral) ────────────────────────────────────────
describe('translate: subagent_progress', () => {
  it('collapses to a single activity (no user text in v1)', () => {
    // Cast via unknown to sidestep strict type checking — unknown future event type
    const out = translate({ type: 'subagent_progress' } as unknown as Parameters<typeof translate>[0], NO_MCP);
    expect(out).toEqual([{ type: 'activity' }]);
  });
});

// ── unknown future types ──────────────────────────────────────────────────────
describe('translate: catch-all', () => {
  it('returns [activity] for unrecognised event types', () => {
    const out = translate({ type: 'thinking_delta', text: '…' } as unknown as Parameters<typeof translate>[0], NO_MCP);
    expect(out).toEqual([{ type: 'activity' }]);
  });
});

// ── error classification table (design §4.2) ──────────────────────────────────
describe('translate: error classification', () => {
  it('maps engine_not_primed → engine, retryable: true', () => {
    const out = translate({ type: 'error', code: 'engine_not_primed', message: 'not primed' }, NO_MCP);
    expect(out[0]).toMatchObject({ type: 'error', classification: 'engine', retryable: true });
  });

  it('maps spawn_failed → transport, retryable: true', () => {
    const out = translate({ type: 'error', code: 'spawn_failed', message: 'failed' }, NO_MCP);
    expect(out[0]).toMatchObject({ type: 'error', classification: 'transport', retryable: true });
  });

  it('maps transport_* prefix → transport, retryable: true', () => {
    const out = translate({ type: 'error', code: 'transport_timeout', message: 'timeout' }, NO_MCP);
    expect(out[0]).toMatchObject({ type: 'error', classification: 'transport', retryable: true });
  });

  it('maps protocol_mismatch → protocol, retryable: false', () => {
    const out = translate({ type: 'error', code: 'protocol_mismatch', message: 'mismatch' }, NO_MCP);
    expect(out[0]).toMatchObject({ type: 'error', classification: 'protocol', retryable: false });
  });

  it('maps approval_timeout → approval, retryable: false', () => {
    const out = translate({ type: 'error', code: 'approval_timeout', message: 'timeout' }, NO_MCP);
    expect(out[0]).toMatchObject({ type: 'error', classification: 'approval', retryable: false });
  });

  it('maps unknown codes → unknown, retryable: false', () => {
    const out = translate({ type: 'error', code: 'some_new_code', message: 'err' }, NO_MCP);
    expect(out[0]).toMatchObject({ type: 'error', classification: 'unknown', retryable: false });
  });
});

// ── stderrTail redaction (CR-3) ───────────────────────────────────────────────
describe('translate: stderrTail redaction', () => {
  it('preserves stderrTail when mcpServersProvided is false', () => {
    const out = translate(
      { type: 'error', code: 'engine_crashed', message: 'err', stderrTail: 'SECRET=abc123' },
      NO_MCP,
    );
    expect((out[0] as { stderrTail?: string }).stderrTail).toBe('SECRET=abc123');
  });

  it('replaces stderrTail with [REDACTED] when mcpServersProvided is true (CR-3)', () => {
    const out = translate(
      { type: 'error', code: 'engine_crashed', message: 'err', stderrTail: 'traceback…' },
      WITH_MCP,
    );
    expect((out[0] as { stderrTail?: string }).stderrTail).toBe('[REDACTED]');
  });

  it('omits the stderrTail field entirely when it was absent', () => {
    const out = translate(
      { type: 'error', code: 'engine_crashed', message: 'err' },
      WITH_MCP,
    );
    expect('stderrTail' in out[0]).toBe(false);
  });
});
```

**Step 2: Run test to verify it fails**

```bash
cd /Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh/container/agent-runner
bun test src/providers/amplifier-agent/event-translator.test.ts
# Expected: FAIL — Cannot find module './event-translator.js'
```

**Step 3: Create event-translator.ts**

```typescript
/**
 * event-translator.ts — pure function helper.
 *
 * Translates a single DisplayEvent (from the amplifier-agent wire) into zero
 * or more ProviderEvents for NanoClaw's poll-loop.
 *
 * Stateless. All decisions are pure functions of the event + context.
 * The caller (AmplifierAgentQuery.makeEvents) enforces the SC-1 invariant
 * that `init` is emitted before any event that produces `activity`.
 *
 * Design reference: §4.2 of 2026-05-22-aaa-v2-amplifier-agent-nc-provider.md
 */

import type { ProviderEvent } from '../types.js';

// ── DisplayEvent shape ────────────────────────────────────────────────────────
// Defined inline to decouple from the installed package version at compile
// time. Replace with the import below once the package is installed:
//   import type { DisplayEvent } from 'amplifier-agent-client-ts';

export interface DisplayEventError {
  type: 'error';
  code: string;
  message: string;
  classification?: string;
  severity?: string;
  correlationId?: string;
  stderrTail?: string;
}

export type DisplayEvent =
  | { type: 'message'; text: string }
  | { type: 'tool_use'; name: string; input: unknown }
  | { type: 'tool_result'; toolUseId: string; content: unknown }
  | { type: 'progress'; message: string }
  | { type: 'subagent_progress' }
  | DisplayEventError
  | { type: string; [key: string]: unknown }; // forward-compat catch-all

export interface TranslateCtx {
  /** True when the host supplied MCP server configs — enables stderrTail redaction (CR-3). */
  mcpServersProvided: boolean;
  /** Wire-level session ID, for correlation logging. */
  sessionId: string;
}

// ── Error classification table (design §4.2) ──────────────────────────────────

type ErrorClassification = 'transport' | 'protocol' | 'engine' | 'approval' | 'unknown';

interface ClassificationRule {
  /** Exact codes, OR a prefix string ending with '_' for prefix matching. */
  codes: string[];
  classification: ErrorClassification;
  retryable: boolean;
}

const ERROR_RULES: ClassificationRule[] = [
  { codes: ['engine_not_primed'], classification: 'engine', retryable: true },
  { codes: ['spawn_failed', 'stdio_closed'], classification: 'transport', retryable: true },
  { codes: ['transport_'], classification: 'transport', retryable: true }, // prefix match
  {
    codes: ['protocol_mismatch', 'unsupported_method', 'schema_violation', 'wire_protocol_violation'],
    classification: 'protocol',
    retryable: false,
  },
  {
    codes: ['approval_translation_failed', 'approval_timeout', 'approval_protocol_violation'],
    classification: 'approval',
    retryable: false,
  },
  {
    codes: ['engine_crashed', 'bundle_failed', 'module_failed', 'bundle_load_failed'],
    classification: 'engine',
    retryable: false,
  },
];

function classifyError(code: string): { classification: ErrorClassification; retryable: boolean } {
  for (const rule of ERROR_RULES) {
    for (const ruleCode of rule.codes) {
      const isPrefix = ruleCode.endsWith('_');
      if (isPrefix ? code.startsWith(ruleCode) : code === ruleCode) {
        return { classification: rule.classification, retryable: rule.retryable };
      }
    }
  }
  return { classification: 'unknown', retryable: false };
}

function translateError(ev: DisplayEventError, ctx: TranslateCtx): ProviderEvent {
  const { classification, retryable } = classifyError(ev.code);

  // CR-3: When MCP servers were provided, redact stderrTail to prevent Python
  // tracebacks from leaking secrets that may appear in engine stderr output.
  const stderrTail =
    ev.stderrTail != null ? (ctx.mcpServersProvided ? '[REDACTED]' : ev.stderrTail) : undefined;

  const event = {
    type: 'error' as const,
    message: ev.message,
    retryable,
    classification,
  } as ProviderEvent;

  // Attach optional fields only when they carry a value.
  // These extend ProviderEvent beyond its declared type — useful for operator
  // grep on correlationId and for debugging stderrTail.
  const ext = event as typeof event & { correlationId?: string; stderrTail?: string };
  if (ev.correlationId != null) ext.correlationId = ev.correlationId;
  if (stderrTail != null) ext.stderrTail = stderrTail;

  return ext;
}

// ── Main translate function ───────────────────────────────────────────────────

/**
 * Translate one DisplayEvent into zero or more ProviderEvents.
 *
 * Invariant (SC-1): the caller must have already yielded {type:'init'} before
 * calling this function for any event that produces an {type:'activity'} output.
 */
export function translate(ev: DisplayEvent, ctx: TranslateCtx): ProviderEvent[] {
  switch (ev.type) {
    case 'message':
      return [{ type: 'activity' }, { type: 'result', text: (ev as { text: string }).text }];

    case 'tool_use':
    case 'tool_result':
      return [{ type: 'activity' }];

    case 'progress':
      return [{ type: 'progress', message: (ev as { message: string }).message }];

    case 'subagent_progress':
      // SC-5 deferral: collapsed to liveness tick. No user-visible text in v1.
      // Promotion trigger: NC UX request for sub-agent activity (Appendix A D-v1.x-09).
      return [{ type: 'activity' }];

    case 'error':
      return [translateError(ev as DisplayEventError, ctx)];

    default:
      // Unknown event types (future wire additions) → keep the poll-loop alive.
      return [{ type: 'activity' }];
  }
}
```

**Step 4: Run tests to verify they pass**

```bash
cd /Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh/container/agent-runner
bun test src/providers/amplifier-agent/event-translator.test.ts
# Expected: all tests PASS
```

**Step 5: Commit**

```bash
cd /Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh
git add container/agent-runner/src/providers/amplifier-agent/event-translator.ts \
        container/agent-runner/src/providers/amplifier-agent/event-translator.test.ts
git commit -m "feat(adapter): event-translator pure function with TDD and stderrTail redaction (N4)"
```

---

## Task 11: Buffer unit tests + factory test prep (N4 prep)

**Files:**
- Create: `container/agent-runner/src/providers/amplifier-agent/buffer.test.ts`
- Modify: `container/agent-runner/src/providers/factory.test.ts`

**Why separate buffer tests:** The B1 buffer (cap=256, visible-drop signal) is important enough
to verify the exported constant before wiring the full class. The factory test change is written
now so you know exactly what Task 12 must produce.

**Step 1: Create buffer.test.ts**

This file imports from `amplifier-agent.ts` which doesn't exist yet — it WILL fail until Task 12.
Write it now. The test is intentionally minimal (just the exported constant).

Create `container/agent-runner/src/providers/amplifier-agent/buffer.test.ts`:

```typescript
/**
 * Buffer cap verification.
 * The full buffer behavior (overflow signal, chain-drain) is verified in the
 * E2E scenarios (Tasks 13-14). This test confirms the exported cap constant.
 */
import { describe, it, expect } from 'bun:test';

describe('AMPLIFIER_AGENT_BUFFER_CAP', () => {
  it('is exported from the provider module and equals 256', async () => {
    // Lazy import avoids module-load failures when spawnAgent is unavailable
    const mod = await import('../amplifier-agent.js');
    expect(mod.AMPLIFIER_AGENT_BUFFER_CAP).toBe(256);
  });
});
```

**Step 2: Update factory.test.ts**

Open `container/agent-runner/src/providers/factory.test.ts`. It currently imports from
`'./claude.js'` and `'./mock.js'`. Add the amplifier-agent import and test case:

```typescript
import { describe, it, expect } from 'bun:test';

import { createProvider, type ProviderName } from './factory.js';
import { ClaudeProvider } from './claude.js';
import { MockProvider } from './mock.js';
import { AmplifierAgentProvider } from './amplifier-agent.js';   // ADD

describe('createProvider', () => {
  it('returns ClaudeProvider for claude', () => {
    expect(createProvider('claude')).toBeInstanceOf(ClaudeProvider);
  });

  it('returns MockProvider for mock', () => {
    expect(createProvider('mock')).toBeInstanceOf(MockProvider);
  });

  // ADD:
  it('returns AmplifierAgentProvider for amplifier-agent', () => {
    expect(createProvider('amplifier-agent')).toBeInstanceOf(AmplifierAgentProvider);
  });

  it('throws for unknown name', () => {
    expect(() => createProvider('bogus' as ProviderName)).toThrow(/Unknown provider/);
  });
});
```

**Step 3: Confirm both new test files fail (as expected — Task 12 is next)**

```bash
cd /Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh/container/agent-runner
bun test src/providers/amplifier-agent/buffer.test.ts src/providers/factory.test.ts
# Expected: FAIL — amplifier-agent.js not found
# This is correct. Proceed to Task 12.
```

---

## Task 12: AmplifierAgentProvider class + registration (N4 main)

**Files:**
- Create: `container/agent-runner/src/providers/amplifier-agent.ts`
- Modify: `container/agent-runner/src/providers/index.ts`

**Before writing code:** Read `container/agent-runner/src/providers/claude.ts` (the existing
provider) and `container/agent-runner/src/providers/types.ts` to confirm import styles and
interface requirements. Specifically note:

- `ProviderEvent.init` uses `continuation: string` — NOT `sessionId: string`. This is NC's actual
  field name. The adapter MUST emit `{ type: 'init', continuation: handle.sessionId }`.
- `AgentProvider` requires `readonly supportsNativeSlashCommands: boolean` AND
  `isSessionInvalid(err: unknown): boolean`.
- `registerProvider` takes `(name, (options) => new Provider(options))`.

**Step 1: Verify the spawnAgent API from the installed package**

```bash
cd /Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh/container/agent-runner
bun -e "
  const m = await import('amplifier-agent-client-ts');
  console.log('Exports:', Object.keys(m));
  // Check SpawnAgentParams shape — look for lifecycle, sessionId, approval, host, mcpServers
"
# Review the output and adjust the spawnAgent() call in the implementation below
# to match the actual parameter names in the installed @0.2.0 package.
```

**Step 2: Create amplifier-agent.ts**

```typescript
/**
 * AmplifierAgentProvider — NanoClaw in-container provider for amplifier-agent.
 *
 * Implements AgentProvider by spawning the amplifier-agent subprocess via the
 * locked wire (amplifier-agent-client-ts@0.2.0). Each wire turn = one
 * SessionHandle. Push-during-turn is handled by the B1 buffer: messages
 * accumulate and are chained as the next turn in the same wire session.
 *
 * Design reference: §4.1 of 2026-05-22-aaa-v2-amplifier-agent-nc-provider.md
 */

import { spawnAgent, AaaError, type SessionHandle } from 'amplifier-agent-client-ts';

import { registerProvider } from './provider-registry.js';
import type {
  AgentProvider,
  AgentQuery,
  McpServerConfig,
  ProviderEvent,
  ProviderOptions,
  QueryInput,
} from './types.js';
import { translate, type DisplayEvent, type TranslateCtx } from './amplifier-agent/event-translator.js';
import { translateMcp } from './amplifier-agent/mcp-translator.js';

// ── Constants ─────────────────────────────────────────────────────────────────

/** Exported for tests. CR-4: raised from 32 → 256. */
export const AMPLIFIER_AGENT_BUFFER_CAP = 256;

/**
 * Capabilities declared to the engine on every spawnAgent() call.
 * NC uses B1 buffer chaining — NOT wire-level steering — so supports_steering=false.
 * NC reads structured errors (classification, correlationId) — so supports_structured_errors=true.
 * Design: §8.13 D12.
 */
const NC_HOST_CAPABILITIES = {
  supports_steering: false,
  supports_structured_errors: true,
} as const;

/**
 * Patterns that mean the stored continuation (session ID) is gone on the engine
 * side. NC will clear continuation and start fresh when isSessionInvalid() returns true.
 */
const STALE_SESSION_RE = /session_not_found|stale_session|invalid_session|session.*not found/i;

// ── Activity ticker ───────────────────────────────────────────────────────────

/**
 * Emits { type: 'activity' } every 2 seconds while a turn is in flight.
 * Prevents NC's poll-loop stuck-detection from firing during long tool runs.
 *
 * SC-1 INVARIANT: the caller MUST NOT start the ticker until after
 * { type: 'init' } has been yielded. Otherwise, activity fires before init.
 */
function startTicker(push: (ev: ProviderEvent) => void): { stop: () => void } {
  const id = setInterval(() => push({ type: 'activity' }), 2000);
  return { stop: () => clearInterval(id) };
}

// ── Lazy-prepare fallback (Q10) ───────────────────────────────────────────────

async function runPrepare(): Promise<void> {
  const { execSync } = await import('node:child_process');
  execSync('amplifier-agent prepare', { stdio: 'pipe' });
}

// ── Error translation helper ──────────────────────────────────────────────────

function translateError(err: unknown): ProviderEvent {
  if (err instanceof AaaError) {
    const code = err.code as string;
    let classification: string;
    let retryable: boolean;
    if (code === 'engine_not_primed' || code === 'engine_crashed' || code === 'bundle_load_failed') {
      classification = 'engine';
      retryable = code === 'engine_not_primed';
    } else if (code === 'spawn_failed' || code === 'stdio_closed' || code.startsWith('transport_')) {
      classification = 'transport';
      retryable = true;
    } else if (
      code === 'protocol_mismatch' ||
      code === 'wire_protocol_violation' ||
      code === 'unsupported_method'
    ) {
      classification = 'protocol';
      retryable = false;
    } else if (code.startsWith('approval_')) {
      classification = 'approval';
      retryable = false;
    } else {
      classification = 'unknown';
      retryable = false;
    }
    return { type: 'error', message: err.message, retryable, classification } as ProviderEvent;
  }
  return {
    type: 'error',
    message: err instanceof Error ? err.message : String(err),
    retryable: false,
    classification: 'unknown',
  } as ProviderEvent;
}

// ── AmplifierAgentQuery ───────────────────────────────────────────────────────

class AmplifierAgentQuery implements AgentQuery {
  // B1 buffer — accumulates push() messages while a turn is in flight.
  private readonly buffer: string[] = [];
  private overflowDropped = 0;

  private aborted = false;
  private active: SessionHandle | undefined;
  private initEmitted = false;
  private sessionId: string | null;
  private readonly mcpServersProvided: boolean;

  constructor(
    private readonly input: QueryInput,
    private readonly mcpServers: Record<string, McpServerConfig>,
  ) {
    this.sessionId = input.continuation ?? null;
    // Determine if MCP is in use (affects stderrTail redaction in event-translator).
    const allMcp = { ...mcpServers, ...(input.mcpServers ?? {}) };
    this.mcpServersProvided = Object.keys(allMcp).length > 0;
  }

  // ── AgentQuery public surface ───────────────────────────────────────────────

  push(message: string): void {
    if (this.aborted) return;
    if (this.buffer.length >= AMPLIFIER_AGENT_BUFFER_CAP) {
      this.overflowDropped++;
      return; // visible-drop signal emitted at turn boundary (design §4.1.3)
    }
    this.buffer.push(message);
  }

  end(): void {
    this.aborted = true;
    this.buffer.length = 0;
  }

  abort(): void {
    this.aborted = true;
    this.active?.cancel();
    this.buffer.length = 0;
  }

  // ── Async iterable ──────────────────────────────────────────────────────────

  readonly events: AsyncIterable<ProviderEvent> = this.makeEvents();

  private async *makeEvents(): AsyncIterable<ProviderEvent> {
    // Merge constructor-level and query-level MCP configs (query-level wins).
    const allMcp = { ...this.mcpServers, ...(this.input.mcpServers ?? {}) };
    const wireMcpServers = translateMcp(Object.keys(allMcp).length > 0 ? allMcp : undefined);

    let prompt = this.input.prompt;

    while (!this.aborted) {
      // ── spawn subprocess turn ───────────────────────────────────────────────
      let handle: SessionHandle;
      try {
        handle = await spawnAgent({
          lifecycle: 'one-shot',
          sessionId: this.sessionId ?? `nc_${Date.now()}`,
          resume: this.sessionId != null,
          cwd: this.input.cwd,
          ...(wireMcpServers ? { mcpServers: wireMcpServers } : {}),
          host: { capabilities: NC_HOST_CAPABILITIES },
          approval: {
            onRequest: async () => ({ decision: 'allow' as const }),  // A10: NC auto-allow
            timeoutMs: 30_000,
          },
        });
      } catch (e) {
        if (e instanceof AaaError && e.code === 'engine_not_primed') {
          // Lazy-prepare fallback (Q10): prepare once and retry.
          try { await runPrepare(); } catch { /* prepare itself failed; let next spawnAgent fail */ }
          continue;
        }
        yield translateError(e);
        return;
      }

      this.active = handle;

      // ── SC-1: emit init BEFORE starting the activity ticker ─────────────────
      if (!this.initEmitted) {
        // NC's ProviderEvent.init uses `continuation` (not `sessionId`).
        yield { type: 'init', continuation: handle.sessionId };
        this.initEmitted = true;
        this.sessionId = handle.sessionId;
      }

      // ── start ticker only after init has been yielded (SC-1) ───────────────
      const tickerQueue: ProviderEvent[] = [];
      const ticker = startTicker((ev) => tickerQueue.push(ev));

      // ── stream display events ───────────────────────────────────────────────
      try {
        const ctx: TranslateCtx = {
          mcpServersProvided: this.mcpServersProvided,
          sessionId: handle.sessionId,
        };

        const submission = handle.submit(prompt);
        for await (const ev of submission.events as AsyncIterable<DisplayEvent>) {
          if (this.aborted) break;
          // Flush any queued ticker events first.
          while (tickerQueue.length > 0) yield tickerQueue.shift()!;
          for (const t of translate(ev, ctx)) yield t;
        }
        // Flush any remaining ticker events after the turn.
        while (tickerQueue.length > 0) yield tickerQueue.shift()!;
      } catch (e) {
        ticker.stop();
        this.active = undefined;
        yield translateError(e);
        return;
      } finally {
        ticker.stop();
        this.active = undefined;
      }

      // ── visible-drop signal (CR-4) ──────────────────────────────────────────
      if (this.overflowDropped > 0) {
        yield { type: 'progress', message: `buffer overflow: ${this.overflowDropped} messages dropped` };
        this.overflowDropped = 0;
      }

      // ── check buffer for B1 chaining ────────────────────────────────────────
      if (this.buffer.length === 0) return;  // no pushed messages — done

      // Chain: use same sessionId + resume:true for the next turn.
      prompt = this.buffer.join('\n\n');
      this.buffer.length = 0;
      // loop continues → next spawnAgent with same sessionId
    }
  }
}

// ── AmplifierAgentProvider ────────────────────────────────────────────────────

export class AmplifierAgentProvider implements AgentProvider {
  /** amplifier-agent does not use Claude Code's native slash commands. */
  readonly supportsNativeSlashCommands = false;

  private readonly mcpServers: Record<string, McpServerConfig>;

  constructor(options: ProviderOptions = {}) {
    this.mcpServers = options.mcpServers ?? {};
  }

  /**
   * Returns true if the error means the stored continuation (session ID) is
   * invalid on the engine side. NC will clear the continuation and start fresh.
   */
  isSessionInvalid(err: unknown): boolean {
    if (err instanceof AaaError) {
      const code = err.code as string;
      return code === 'session_not_found' || code === 'stale_session' || code === 'invalid_session';
    }
    const msg = err instanceof Error ? err.message : String(err);
    return STALE_SESSION_RE.test(msg);
  }

  query(input: QueryInput): AgentQuery {
    return new AmplifierAgentQuery(input, this.mcpServers);
  }
}

// ── Self-register ─────────────────────────────────────────────────────────────

registerProvider('amplifier-agent', (opts) => new AmplifierAgentProvider(opts));
```

**Step 3: Edit container/agent-runner/src/providers/index.ts**

Open the file. It currently reads:

```typescript
import './claude.js';
import './mock.js';
```

Add:

```typescript
import './claude.js';
import './mock.js';
import './amplifier-agent.js';
```

**Step 4: Run container typecheck**

```bash
cd /Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh/container/agent-runner
bun run typecheck
# Expected: no errors.
# If you see "Module not found: amplifier-agent-client-ts", run: bun install
```

**IMPORTANT — verify spawnAgent parameters:** If the installed `amplifier-agent-client-ts@0.2.0`
has different parameter names (e.g., `sessionId` might be required differently, or `host` might
not exist yet), adjust the `spawnAgent()` call accordingly. Read the installed package's TypeScript
declaration files:

```bash
bun -e "
  import { readFileSync } from 'fs';
  import { resolve } from 'path';
  const pkg = JSON.parse(readFileSync('node_modules/amplifier-agent-client-ts/package.json', 'utf8'));
  console.log('Types entry:', pkg.types || pkg.typings);
"
# Then read the .d.ts file to see the actual SpawnAgentParams shape
```

**Step 5: Run all container tests**

```bash
bun test
# Expected:
#   factory.test.ts: 4 tests PASS (including new amplifier-agent test)
#   buffer.test.ts: PASS (AMPLIFIER_AGENT_BUFFER_CAP === 256)
#   mcp-translator.test.ts: 6 PASS
#   event-translator.test.ts: all PASS
#   All other existing tests: PASS
```

**Step 6: Run host typecheck**

```bash
cd /Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh
pnpm exec tsc --noEmit
# Expected: no errors
```

**Step 7: Commit all N4 work**

```bash
git add container/agent-runner/src/providers/amplifier-agent.ts \
        container/agent-runner/src/providers/amplifier-agent/buffer.test.ts \
        container/agent-runner/src/providers/index.ts \
        container/agent-runner/src/providers/factory.test.ts
git commit -m "feat(adapter): AmplifierAgentProvider B1 buffer + event/mcp translators complete (N4)"
```

---

## Task 13: E2E happy-path scenario (N5)

**Integration time: 30–60 minutes. This is NOT a unit test — it exercises a live container.**

**Prerequisites:**
- Docker image built from Task 5 (`nanoclaw-agent:aaa-test`) exists
- NanoClaw host process is runnable (`pnpm run dev` or equivalent)
- `amplifier-agent v0.2.0` is on PyPI and installs correctly

**Step 1: Rebuild the container image with all changes from Tasks 5–12**

```bash
cd /Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh/container
docker build \
  --build-arg AMPLIFIER_AGENT_VERSION=0.2.0 \
  -t nanoclaw-agent:aaa-test \
  .
# Look for: "RUN amplifier-agent doctor --strict" completing with exit 0
```

**Step 2: Create a test agent group with provider='amplifier-agent'**

Find the DB:
```bash
ls /Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh/store/
# Expected: nanoclaw.db (or similar)
```

List agent groups and pick or create an internal test group:
```bash
sqlite3 /Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh/store/nanoclaw.db \
  "SELECT id, name FROM agent_groups LIMIT 10;"
```

Set the provider for your test group:
```bash
TEST_GROUP_ID="<your-group-id-from-above>"
sqlite3 /Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh/store/nanoclaw.db \
  "INSERT OR REPLACE INTO container_configs (agent_group_id, provider) VALUES ('${TEST_GROUP_ID}', 'amplifier-agent');"
```

**Step 3: Start NC with the test image**

```bash
cd /Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh
CONTAINER_IMAGE=nanoclaw-agent:aaa-test pnpm run dev
```

**Step 4: Send a simple test message**

Use the channel connected to your test group, OR:

```bash
# Via ncl chat (if configured for your test group's channel)
pnpm exec tsx scripts/chat.ts
# Type: "What is the current time?"
```

**Step 5: Verify green state (ALL of these must be true)**

1. NC logs show the container spawning successfully:
   ```
   Spawning container { sessionId: '...', agentGroup: 'your-group-name' }
   ```

2. The `init` event fires with a continuation token (check NC logs):
   ```
   { type: 'init', continuation: 's_abc123...' }
   ```

3. The agent replies via `mcp__nanoclaw__send_message` (the reply appears in the channel):
   - The reply should mention the current time or similar

4. The continuation is persisted in NC's DB:
   ```bash
   sqlite3 /Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh/store/nanoclaw.db \
     "SELECT continuation FROM sessions WHERE agent_group_id='${TEST_GROUP_ID}' ORDER BY created_at DESC LIMIT 1;"
   # Expected: a non-null value like "s_abc123..."
   ```

5. The amplifier-agent state dir was created on the host:
   ```bash
   ls /Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh/data/amplifier-agent/${TEST_GROUP_ID}/
   # Expected: sessions/ directory exists
   ```

---

## Task 14: E2E steering + buffer overflow scenario (N6)

**Prerequisites:** Task 13 is fully green.

**Integration time: 30–60 minutes.**

**Scenario A — B1 chain (steering during a long turn):**

1. Send a message that takes >10 seconds to complete:
   ```
   "Read all TypeScript files in /app/src/ and summarize their exports."
   ```

2. While the agent is running (within 10 seconds of the first message), send a follow-up:
   ```
   "Only include files larger than 2KB."
   ```

**Verify:**
- Two `result` events appear in NC logs on the same `continuation`/session ID
- The transcript has grown (check line count):
  ```bash
  SESSION_ID="<from-step-13>"
  wc -l /Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh/data/amplifier-agent/${TEST_GROUP_ID}/sessions/${SESSION_ID}/transcript.jsonl
  # Expected: > 1 line (two turns serialized)
  ```
- No new `continuation` was issued (same session ID, `resume: true` for second turn)

**Scenario B — Buffer overflow signal (cap=256):**

This requires sending 257 push messages during a single long-running turn. The easiest way
without a dedicated test harness is to use a very long-running task and send many rapid messages.

The key observable: when >256 messages are pushed during a single turn, NC logs should include:
```
{ type: 'progress', message: 'buffer overflow: N messages dropped' }
```

where N = number of messages beyond 256.

If you cannot trigger the overflow in normal testing, at minimum verify the cap constant:
```bash
cd /Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh/container/agent-runner
bun -e "
  const m = await import('./src/providers/amplifier-agent.js');
  console.log('Buffer cap:', m.AMPLIFIER_AGENT_BUFFER_CAP);
  // Expected: 256
"
```

**Acceptance gate for N6:**
- Steering: second message processed as a new turn within the same session
- Transcript: JSONL file has entries from both turns
- Overflow: either verified in live test OR confirmed via the exported constant (256)

---

## Task 15: Phased rollout plumbing + deployment runbook (N7)

**Files:** No code changes. This task verifies the DB schema and documents the canary setup.

**Step 1: Verify the DB accepts 'amplifier-agent' as a provider value**

NC's `container_configs.provider` column is TEXT with no enum constraint:

```bash
sqlite3 /Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh/store/nanoclaw.db \
  ".schema container_configs" | grep -i provider
# Expected: "provider TEXT" (no CHECK constraint)
```

`resolveProviderName()` in `src/container-runner.ts` passes the string directly to the
container config registry — any string is accepted at the DB level.

**Step 2: Verify the host-side registration loads on NC startup**

`container-runner.ts` imports `'./providers/index.js'` at startup (side-effect only).
`src/providers/index.ts` now imports `'./amplifier-agent.js'`. Confirm via a quick test:

```bash
cd /Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh
node --input-type=module << 'EOF'
import { listProviderContainerConfigNames } from './src/providers/provider-container-registry.js';
import './src/providers/index.js';
console.log('Registered providers:', listProviderContainerConfigNames());
EOF
# Expected output includes: 'amplifier-agent'
```

**Step 3: Final commit**

```bash
cd /Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh
git add -A
git commit -m "feat: Phase 3 complete — amplifier-agent provider E2E verified (N5/N6/N7)"
```

---

### R0 Canary Deployment Runbook

**Pre-conditions (all must be true):**
- All 15 tasks complete
- `pnpm exec vitest run` PASS
- `bun test` in `container/agent-runner/` PASS
- `docker build` with `doctor --strict` exits 0
- E2E happy-path is green (Task 13)
- E2E steering is green (Task 14)

**R0: Internal canary (1–2 weeks)**

1. Identify 2–3 agent groups used by the L3 team and NC team for internal work.
2. For each group: `UPDATE container_configs SET provider='amplifier-agent' WHERE agent_group_id='...'`
3. Rebuild NC container image with `AMPLIFIER_AGENT_VERSION=0.2.0` and deploy to internal env.
4. Monitor daily:
   - `ls data/amplifier-agent/*/sessions/` — transcript files accumulate
   - NC logs: watch for `classification: 'engine'` or `classification: 'transport'` errors
   - `pnpm exec tsx scripts/lint-aaa-version.ts` stays OK on every image rebuild

**Rollback (immediate):**
```bash
sqlite3 ./store/nanoclaw.db \
  "UPDATE container_configs SET provider='claude' WHERE provider='amplifier-agent';"
# Containers restart on next wake; no in-flight work is killed.
```

**Advance to R1 (5% of new session groups) when:**
- Zero `engine_crashed` errors in 7 days of canary
- Turn success rate ≥ 99% (turns reaching `turn/completed`)
- No `protocol_mismatch` errors (wire version pin is solid)

**R1–R3 (5% → default-new → fleet ramp):** See design §10.7. These are operational steps, not
implementation tasks. Schedule with NC team after R0 observes for 1 week.

---

## End-of-Phase 3 Acceptance Gate

All of the following must be true before declaring Phase 3 complete:

- [ ] `pnpm exec vitest run` passes (CI lint tests, host provider tests, container-runtime tests)
- [ ] `bun test` in `container/agent-runner/` passes (mcp-translator, event-translator, buffer, factory)
- [ ] `pnpm exec tsc --noEmit` no errors (host TypeScript)
- [ ] `bun run typecheck` no errors in `container/agent-runner/`
- [ ] `pnpm exec tsx scripts/lint-aaa-version.ts` exits 0
- [ ] `docker build --build-arg AMPLIFIER_AGENT_VERSION=0.2.0 -t nanoclaw-agent:aaa-test container/` exits 0 with `doctor --strict` passing in the build output
- [ ] E2E happy-path: agent group with `provider='amplifier-agent'` completes a turn end-to-end (message → engine → reply via `mcp__nanoclaw__send_message`)
- [ ] Session continuation persisted in NC DB after first turn
- [ ] E2E steering: second message processed as a second turn in the same session
- [ ] No `git push` / `gh pr create` / image publish — those are `/finish` mode operations

---

## Key Implementation Notes

1. **`continuation` not `sessionId` in init event.** NC's `ProviderEvent` uses `continuation: string`
   for the `init` event (verified in `types.ts` and `mock.ts`). The adapter MUST emit
   `{ type: 'init', continuation: handle.sessionId }`. The design doc's §1.2 pseudocode says
   `sessionId` but the actual `types.ts` in this repo says `continuation`. Trust the codebase.

2. **Verify `SpawnAgentParams` from installed package.** The current `amplifier-agent-client-ts`
   (pre-v0.2.0) requires `lifecycle: "one-shot"` as a required field and `sessionId: string` as
   required. After Phase 2 ships, these may change. Always read the installed package's `.d.ts`
   before finalizing the `spawnAgent()` call.

3. **`DATA_DIR` is process.cwd()-relative.** `DATA_DIR` in `src/config.ts` resolves to
   `path.resolve(process.cwd(), 'data')`. The amplifier-agent state dir will be at
   `<NC_PROJECT_ROOT>/data/amplifier-agent/<GROUP_ID>/`. This is consistent with all other NC
   host state dirs.

4. **`ProviderContainerContext` fields.** The actual fields are `agentGroupId`, `sessionDir`,
   `hostEnv` — NOT `groupId` and `hostStateDir` as the design doc's §4.4 pseudocode shows.
   The implementation in Task 8 uses the correct field names.

5. **`McpServerConfig` inline vs. package import.** `WireMcpServerConfig` is defined inline in
   `mcp-translator.ts` to decouple compile-time from package availability. Once
   `amplifier-agent-client-ts@0.2.0` is installed, you can replace the inline definition with:
   `import type { McpServerConfig as WireMcpServerConfig } from 'amplifier-agent-client-ts'`

6. **`DisplayEvent` inline vs. package import.** Same pattern as above for `event-translator.ts`.
   Replace the inline `DisplayEvent` definition with the package import once @0.2.0 is available.

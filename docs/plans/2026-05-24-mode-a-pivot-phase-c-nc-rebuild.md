# AaA Mode A Pivot — Phase C: NC Adapter Rebuild + DTU Re-verify Implementation Plan

> **For execution:** Use `/execute-plan` mode or the subagent-driven-development recipe.
>
> **Note on working directory:** This plan lives in the amplifier-agent repo's `docs/plans/` for co-location with its sibling Phase A and B plans and the Mode A pivot amendment, but **most of its work happens in the nanoclaw-fresh repo** at `/Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh`. Tasks state their working directory explicitly. Phase C produces TWO PRs — one in nanoclaw-fresh (the bulk), and (potentially) one in amplifier-agent if a final wrapper tweak is needed for an issue surfaced during DTU re-verify.

**Prerequisite:**
- **Phase A merged** to amplifier-agent main (engine binary speaks the §4.1 v2 envelope).
- **Phase B merged** to amplifier-agent main, with `amplifier-agent-client-ts@0.3.0` (and `amplifier-agent-client-py@0.3.0`) published — or at minimum installable via `file:` reference from a local checkout.
- The Mode A pivot amendment, especially CR-C (DisplayEvent simplified shape) and §5.2 (subprocess driver behavior).
- Access to the macOS host with Incus VM running. The DTU instance `aaa-nc-verify-v2` from the prior verification round (whose harness lives at `/workspace/nanoclaw-fresh/container/agent-runner/e2e-harness.ts` inside the instance) **may or may not still be reachable** — Task 5 starts with a check and re-launches if needed.

**Goal:** Migrate NC's amplifier-agent provider adapter (in `nanoclaw-fresh/container/agent-runner/src/providers/amplifier-agent.ts` + the two helpers `event-translator.ts` and `mcp-translator.ts`) onto the new wrapper's simplified `DisplayEvent` shape (CR-C). Re-land the Dockerfile fix from F2 (`UV_TOOL_BIN_DIR=/usr/local/bin uv tool install`) cleanly. Re-verify in a Digital Twin Universe end-to-end against a real Anthropic key + the Slack reply path, confirming the four behaviors the prior verification confirmed (happy turn, resume continuity, MCP passthrough, B1 buffer chain) all work against the new wrapper without regression.

**Architecture:**
- The wrapper's `SpawnAgentParams` and `SessionHandle` **public API surface** is preserved (per amendment §5.1 / N1' note). NC's adapter file should compile against `amplifier-agent-client-ts@0.3.0` with **minimal changes**.
- The change set in `event-translator.ts` is targeted: the function that mapped the old verbose `DisplayEvent` shape (`{type, sessionId, turnId, payload, ...}`) onto NC's `ProviderEvent` (`{type, sessionId, ...}`) now consumes the simplified union (`init | activity | result | error`). The translation is **strictly simpler**: NC was already collapsing the verbose shape into 4 cases that match the new union 1:1.
- `mcp-translator.ts` is unchanged (shape-validate + identity-pass).
- `factory.ts` and `provider-registry.ts` are unchanged.
- The Dockerfile gets the F2 fix re-landed cleanly: `UV_TOOL_BIN_DIR=/usr/local/bin uv tool install "amplifier-agent==${AMPLIFIER_AGENT_VERSION}"` runs as root before the `USER node` line; `prepare` and `doctor --strict` run as `node` after.
- F3 fix: `container/agent-runner/package.json`'s `amplifier-agent-client-ts` dependency moves from `file:../../../amplifier-agent/wrappers/typescript` to a clean reference — `^0.3.0` if the npm publish landed, or a tagged git URL if not. The DTU container ships a copy of the wrapper source in the build context if `file:` is the only option.

**Tech Stack:**
- nanoclaw-fresh: TypeScript 5.7, bun (test + runtime per `package.json`), zod 4.
- DTU: Amplifier digital-twin-universe bundle (already installed; see the digital-twin-universe skill).
- Mock vs real LLM: Phase C uses a **real** Anthropic key (the user's working one) to maintain the prior DTU verification's empirical-validation posture. The mock LLM from Phase A/B is not used here — Phase C is the integration-test step that validates against the real provider.

**Real-binary gate:** Phase C is end-to-end (b) by definition. Tasks 6, 8, and 9 are real-DTU tests; the rest are repo-local rebuild work that culminates in those tests.

**Task count:** 10 tasks.

---

## Required reading before starting

1. `docs/designs/2026-05-24-aaa-v2-mode-a-pivot-amendment.md` §5.2 (the new DisplayEvent), §8.2 (NC stages N1'-N4'), §11 O5' (the pre-A5' audit note).
2. `/Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh/container/agent-runner/src/providers/amplifier-agent.ts` — the adapter being rebuilt.
3. `/Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh/container/agent-runner/src/providers/amplifier-agent/event-translator.ts` — the file most likely to change.
4. `/Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh/container/agent-runner/Dockerfile` — for the F2 re-landing.
5. `/Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh/container/agent-runner/package.json` — for the F3 ref change.
6. The digital-twin-universe skill (load with `load_skill(skill_name="digital-twin-universe")`) — for `launch`, `exec`, `destroy` commands.
7. The prior DTU verification's harness, recoverable via either: `amplifier-digital-twin exec aaa-nc-verify-v2 -- cat /workspace/nanoclaw-fresh/container/agent-runner/e2e-harness.ts` (if instance still alive) or `git log -- container/agent-runner/e2e-harness.ts` in nanoclaw-fresh to find the commit where it was authored.

---

## Task 1: Snapshot NC main; create rebuild branch

**Working dir:** `/Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh`

**Files:** No code changes yet.

**Step 1: Confirm clean main**
```bash
cd /Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh
git status
git log --oneline -5
```
Expected: clean working tree, recent commits show the prior NC adapter work landed.

**Step 2: Inventory the F2-impacted commit (per parent session context, the DTU agent applied it locally in `8f5ef86` but did not push)**
```bash
git log --all --oneline | head -10
git stash list
```
If commit `8f5ef86` is present but not on a remote branch, note it for Task 4.

**Step 3: Branch from main**
```bash
git checkout main
git pull
git checkout -b feat/mode-a-pivot-adapter-rebuild
```

**Step 4: Confirm baseline tests green**
```bash
cd container/agent-runner
bun install
bun test
```
Expected: all tests pass (count from the parent session: 43 tests). Note any failures — those must clear before Phase C work begins.

**Step 5: Empty commit marker**
```bash
cd /Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh
git commit --allow-empty -m "chore(adapter): start Phase C — Mode A pivot rebuild"
```

---

## Task 2: Write failing test for the new `DisplayEvent` shape in event-translator

**Working dir:** `/Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh/container/agent-runner`

**Files:**
- Modify: `src/providers/amplifier-agent/event-translator.test.ts`

**Test type:** (a) unit (bun:test).

**Step 1: Inspect the current translator + its test file**
```bash
cat src/providers/amplifier-agent/event-translator.ts
cat src/providers/amplifier-agent/event-translator.test.ts | head -60
```

Note the existing translator's signature. It almost certainly looks something like `function translateEvent(ev: DisplayEvent): ProviderEvent` and matches on the old shape's `.type` discriminator. The new shape preserves the discriminator strings (`init`, `activity`, `result`, `error`) but drops `payload`, `turnId`, etc.

**Step 2: Append failing tests that pin the new shape**
```typescript
// at end of event-translator.test.ts
import { test, expect } from "bun:test";
import { translateEvent } from "./event-translator";
import type { DisplayEvent } from "amplifier-agent-client-ts";

test("CR-C: translate init event (new simplified shape)", () => {
  const ev: DisplayEvent = { type: "init", sessionId: "sid-xyz" };
  const out = translateEvent(ev);
  expect(out.type).toBe("init");
  // ProviderEvent shape — verify against existing tests for the expected fields
  expect((out as any).sessionId).toBe("sid-xyz");
});

test("CR-C: translate activity event (no payload fields)", () => {
  const ev: DisplayEvent = { type: "activity" };
  const out = translateEvent(ev);
  expect(out.type).toBe("activity");
});

test("CR-C: translate result event (text field only)", () => {
  const ev: DisplayEvent = { type: "result", text: "hello world" };
  const out = translateEvent(ev);
  expect(out.type).toBe("result");
  expect((out as any).text).toBe("hello world");
});

test("CR-C: translate error event (full AaaError-shape)", () => {
  const ev: DisplayEvent = {
    type: "error",
    code: "engine_crashed",
    classification: "engine",
    severity: "error",
    correlationId: "abc-def",
    message: "boom",
    retryable: false,
  };
  const out = translateEvent(ev);
  expect(out.type).toBe("error");
  expect((out as any).code).toBe("engine_crashed");
  expect((out as any).classification).toBe("engine");
  expect((out as any).correlationId).toBe("abc-def");
});
```

**Step 3: Update the `amplifier-agent-client-ts` dependency reference temporarily**

The test imports the new shape from `amplifier-agent-client-ts`. To get the new types locally before npm publish, edit `package.json`:
```bash
# temporary: point at local Phase B checkout
# (final form decided in Task 4)
```
Change the dependency:
```json
"amplifier-agent-client-ts": "file:../../../amplifier-agent/wrappers/typescript",
```
to ensure it picks up the new types. Then:
```bash
cd container/agent-runner
bun install --force
```

**Step 4: Run; expect FAIL** (the test should compile against the new types, but the translator's runtime behavior may still expect the old `payload` field).
```bash
bun test src/providers/amplifier-agent/event-translator.test.ts
```

If the tests fail to **compile** (TS errors), that's actually a stronger failure — proves the new shape is incompatible. If they compile but the translator throws "Cannot read property 'sessionId' of undefined" or similar, that's the runtime failure. Either way: red.

**Step 5: Commit the failing tests**
```bash
git add src/providers/amplifier-agent/event-translator.test.ts
git commit -m "test(adapter): CR-C — failing tests for simplified DisplayEvent translation"
```

---

## Task 3: Rewrite `event-translator.ts` for the new shape

**Working dir:** `/Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh/container/agent-runner`

**Files:**
- Modify: `src/providers/amplifier-agent/event-translator.ts`

**Step 1: Read the old translator + understand the existing ProviderEvent shape**
```bash
cat src/providers/amplifier-agent/event-translator.ts
cat src/providers/types.ts | head -40  # likely where ProviderEvent is defined
```

**Step 2: Rewrite the translator**

The new translator is strictly simpler. Discriminate on `ev.type`; map each case 1:1:
```typescript
// src/providers/amplifier-agent/event-translator.ts
import type { DisplayEvent } from "amplifier-agent-client-ts";
import type { ProviderEvent } from "../types";

/**
 * Translate wrapper DisplayEvent → NC ProviderEvent.
 * CR-C: consumes the simplified Mode A v2 union (init | activity | result | error).
 * The old verbose shape's tool/* and assistant/text mid-turn events no longer
 * exist on the wire (amendment WG-3). The wrapper synthesizes activity ticks
 * every 2s while the engine subprocess runs.
 */
export function translateEvent(ev: DisplayEvent): ProviderEvent {
  switch (ev.type) {
    case "init":
      return { type: "init", sessionId: ev.sessionId };
    case "activity":
      return { type: "activity" };
    case "result":
      return { type: "result", text: ev.text };
    case "error":
      return {
        type: "error",
        code: ev.code,
        classification: ev.classification,
        severity: ev.severity,
        correlationId: ev.correlationId,
        message: ev.message,
        stderrTail: ev.stderrTail,
        retryable: ev.retryable,
      };
  }
}
```

If the existing `ProviderEvent` type is missing fields the new error event provides (e.g. `retryable`), update `src/providers/types.ts` to add them. **Do not** delete fields from `ProviderEvent` that other providers (Claude) populate — additive only.

**Step 3: Apply CR-3 stderrTail redaction** (preserved from prior design)

If `mcp-translator.ts` declared MCP env keys, the error-path stderrTail must redact those values. The existing CR-3 redaction logic likely lives near the translator or in the provider class. Verify with:
```bash
grep -rn "stderrTail\|redact" src/providers/amplifier-agent/
```
If a redactor exists, ensure it's invoked on the new error event's `stderrTail` before yielding. If it doesn't, log a TODO and bring it up in the Phase C acceptance review.

**Step 4: Run the test**
```bash
bun test src/providers/amplifier-agent/event-translator.test.ts
```
Expected: all 4 new tests PASS. The existing event-translator tests may need updates if they pinned the old shape — adapt or delete based on whether the assertion is still meaningful.

**Step 5: Run the full bun:test suite**
```bash
bun test
```
Expected: all 43+ tests green (some old translator tests may have been deleted; the count may shift).

**Step 6: Commit**
```bash
git add src/providers/amplifier-agent/event-translator.ts src/providers/types.ts
git commit -m "feat(adapter): CR-C — event-translator consumes simplified DisplayEvent"
```

---

## Task 4: Re-land F2 (Dockerfile fix) cleanly

**Working dir:** `/Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh`

**Files:**
- Modify: `container/agent-runner/Dockerfile`
- Modify: `container/agent-runner/package.json` (F3 — wrapper reference)

**Step 1: Read the current Dockerfile**
```bash
cat container/agent-runner/Dockerfile
```

**Step 2: Check whether `8f5ef86` (the DTU agent's local commit) is present**
```bash
git log --all --oneline --grep="UV_TOOL_BIN_DIR\|prepare\|amplifier-agent" | head -10
git show 8f5ef86 --stat 2>/dev/null || echo "8f5ef86 not in this clone — apply F2 fresh"
```

**Step 3: Apply the F2 fix verbatim**

The fix per the parent session context (and amendment §8.1 N1'):
```dockerfile
ARG AMPLIFIER_AGENT_VERSION=0.3.0
RUN UV_TOOL_BIN_DIR=/usr/local/bin uv tool install "amplifier-agent==${AMPLIFIER_AGENT_VERSION}"
USER node
RUN amplifier-agent prepare
RUN amplifier-agent doctor --strict
```

The Dockerfile may already have lines that need replacing. Make the edit so:
- `UV_TOOL_BIN_DIR=/usr/local/bin` is set inline on the `uv tool install` line (NOT as a separate `ENV` — the env-var-on-RUN form is what the DTU agent verified works).
- `prepare` and `doctor --strict` run AFTER `USER node`.
- Bump the `AMPLIFIER_AGENT_VERSION` ARG default to `0.3.0` to match Phase B's published version.

**Step 4: Apply the F3 fix**

In `container/agent-runner/package.json`, update the `amplifier-agent-client-ts` reference. Two cases:

**Case A: Phase B published v0.3.0 to npm.** Use:
```json
"amplifier-agent-client-ts": "^0.3.0"
```
The Dockerfile build will pull it via npm during `bun install`. No COPY step needed.

**Case B: Phase B is a release candidate or not yet published.** Use the file ref AND add a Dockerfile COPY step that brings the wrapper source into the build context:
```json
"amplifier-agent-client-ts": "file:./vendor/amplifier-agent-client-ts"
```
Then in `Dockerfile`:
```dockerfile
COPY vendor/amplifier-agent-client-ts /tmp/vendor/amplifier-agent-client-ts
```
(adjusted to match the workdir layout). The vendor copy is populated by a `scripts/vendor-wrapper.sh` script that the orchestrator runs before `docker build`.

Decide between A and B based on what Phase B's release prep ended at. Check with:
```bash
npm view amplifier-agent-client-ts@0.3.0 2>&1 | head -5
```
If npm returns a valid package, use Case A. Otherwise use Case B.

**Step 5: Verify the Dockerfile parses**
```bash
docker --version  # or `podman --version`
# Local docker build is slow and not always available on dev hosts;
# the real build happens inside the DTU at Task 6. Skip local build.
# Instead, smoke-test syntax via `docker-file-utils` if installed, or just
# eyeball the file for the four anchor lines above.
grep -n "UV_TOOL_BIN_DIR\|USER node\|prepare\|doctor --strict" container/agent-runner/Dockerfile
```
Expected: all four lines present in the correct order (`UV_TOOL_BIN_DIR` first, `USER node` next, then `prepare`, then `doctor --strict`).

**Step 6: Commit**
```bash
git add container/agent-runner/Dockerfile container/agent-runner/package.json
git commit -m "fix(container): F2/F3 — UV_TOOL_BIN_DIR fix + wrapper v0.3.0 reference"
```

---

## Task 5: Verify N2'/N3' (CI lint + host-side provider) need no changes

**Working dir:** `/Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh`

**Files:** No changes — this is a verification task.

**Step 1: Re-read amendment §8.2 N2' + N3'**

The amendment states: NC's `package.json` pin (N2') and the host-side provider (N3') should not need changes because the wrapper's public API is preserved. Task 4 already touched `package.json` for F3 — so N2' is folded into Task 4.

**Step 2: Verify host-side provider doesn't depend on the changed shape**

Grep for any host-side (non-container) code that imports `DisplayEvent`:
```bash
grep -rn "DisplayEvent\|amplifier-agent-client" host/ 2>&1 | head -20
grep -rn "DisplayEvent" --include="*.ts" --include="*.tsx" . | grep -v node_modules | grep -v container/agent-runner
```
Expected: zero matches outside `container/agent-runner/`. If matches appear in `host/` or another top-level dir, those files need a CR-C migration too — open a new task and a new commit.

**Step 3: Verify CI lint (`scripts/lint-aaa-version.ts`) still works**
```bash
ls scripts/lint-aaa-version.ts 2>&1
# If present:
bun scripts/lint-aaa-version.ts || echo "lint failed — investigate"
```
The lint script checks that `package.json`'s `amplifier-agent-client-ts` version aligns with the Dockerfile's `AMPLIFIER_AGENT_VERSION`. After Task 4 both should be `0.3.0`. If the lint fails, fix and re-commit; if the lint script doesn't exist or doesn't cover this, note it as a Phase C observation but don't expand scope.

**Step 4: Commit verification artifact (empty marker)**
```bash
git commit --allow-empty -m "chore(adapter): N2'/N3' — verified host-side + CI lint require no changes"
```

---

## Task 6: Determine DTU instance state; launch or reuse `aaa-nc-verify-v2`

**Working dir:** macOS host (any).

**Files:** No source changes; this is infra orchestration.

**Step 1: Check if the prior DTU instance survives**
```bash
amplifier-digital-twin list 2>&1
```
Look for an entry named `aaa-nc-verify-v2`. If present and `status: running`, proceed to step 4. If present but stopped, attempt restart via `amplifier-digital-twin exec aaa-nc-verify-v2 -- echo alive` (which will fail-loud); if restart isn't supported, treat as gone and re-launch (step 2). If absent, re-launch.

**Step 2: Re-launch a fresh DTU using the same profile**

The prior verification used the `amplifier-user-sim` profile or a custom one. Load the digital-twin-universe skill for the launch incantation:
```bash
# Load the skill for full guidance (do this in the orchestrator's session, not in shell)
# Then:
amplifier-digital-twin launch amplifier-user-sim --hostname aaa-nc-verify-v3 --var GITEA_URL=... --var GITEA_TOKEN=...
```
(Substitute the real `--var` values from the user's Gitea credentials — they are not in this plan because they live in user env, not source. Refer to the digital-twin-universe skill for the correct profile and var set.)

The new instance ID returned by `launch` is the one used for the rest of Phase C. Replace `<dtu-id>` below with the actual ID returned.

**Step 3: Wait for readiness**
```bash
while ! amplifier-digital-twin check-readiness <dtu-id> | jq -e '.ready'; do
  sleep 3
done
```
Expected: `ready: true` within ~60s.

**Step 4: Push the rebuild branch + Phase B wrapper to the in-DTU Gitea**

The DTU's URL-rewrite proxy redirects GitHub URLs to the in-DTU Gitea. To push the local feature branch:
```bash
# From host, in nanoclaw-fresh:
cd /Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh
git remote add gitea-dtu http://admin:<GITEA_TOKEN>@<gitea-host>:10110/admin/nanoclaw-fresh.git || true
git push gitea-dtu feat/mode-a-pivot-adapter-rebuild

# Symmetrically push the amplifier-agent Phase A+B work:
cd /Users/mpaidiparthy/repos/AaA/opus-recon/amplifier-agent
git push gitea-dtu feat/mode-a-phase-b-wrapper
```

If the wrapper has not been npm-published, the Phase 4 Dockerfile (Case B) vendor-copy approach from Task 4 needs the wrapper source brought into the build context. Run `scripts/vendor-wrapper.sh` (or the equivalent manual cp) before re-building the container image in the DTU.

**Step 5: Re-build the agent-runner image inside the DTU**
```bash
amplifier-digital-twin exec <dtu-id> --stream --timeout 600 -- bash -c '
  cd /workspace/nanoclaw-fresh &&
  git fetch &&
  git checkout feat/mode-a-pivot-adapter-rebuild &&
  cd container/agent-runner &&
  docker build -t nc-agent-runner:phase-c .
'
```
Expected: clean build. If `amplifier-agent doctor --strict` fails (as it did pre-F2 fix), the Dockerfile fix from Task 4 has not been picked up — verify with `cat Dockerfile | head -20` inside the DTU.

**Step 6: Note: no commit in this task.** Phase C plan doesn't track DTU orchestration in git — it's runtime work. Just confirm step 5 succeeded before proceeding.

---

## Task 7: Re-install the prior e2e-harness in the new DTU

**Working dir:** Inside the DTU.

**Files:** Recover `e2e-harness.ts` from prior commit or rebuild it.

**Step 1: Find the prior harness**

Per the parent session context, the harness lived at `/workspace/nanoclaw-fresh/container/agent-runner/e2e-harness.ts` in the prior DTU. Check git history in nanoclaw-fresh for its origin:
```bash
cd /Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh
git log --all --oneline -- container/agent-runner/e2e-harness.ts 2>&1 | head -5
```

If the file was authored in a commit, restore it:
```bash
git show <commit>:container/agent-runner/e2e-harness.ts > /tmp/e2e-harness.ts.recovered
```

If it was only ever a DTU-local file (lost when `aaa-nc-verify-v2` died), rebuild it. The harness's job is small: spawn the agent-runner container (or run agent-runner as a bun process), send a test prompt, capture the `ProviderEvent` stream + the final reply, exit with code 0 on success / 1 on failure.

A minimal rebuild:
```typescript
// container/agent-runner/e2e-harness.ts
// E2E harness for Phase C — exercise the amplifier-agent provider end-to-end.
// Run via: `bun e2e-harness.ts` inside the DTU.

import { AmplifierAgentProvider } from "./src/providers/amplifier-agent";

async function main() {
  const provider = new AmplifierAgentProvider({
    // ... real config, see how poll-loop.ts wires it ...
  });
  const query = await provider.query({
    prompt: "Remember the color purple, then tell me what color you remember.",
    continuation: null,
    mcpServers: {
      nanoclaw_send_message: {
        transport: "stdio",
        command: "node",
        args: ["/path/to/nc-mcp-send-message.js"],
      },
    },
  });
  for await (const ev of query) {
    console.log(JSON.stringify(ev));
    if (ev.type === "result") {
      if (!ev.text.toLowerCase().includes("purple")) {
        console.error("FAIL: reply did not contain 'purple'");
        process.exit(1);
      }
      console.log("OK: happy-path turn complete");
      return;
    }
    if (ev.type === "error") {
      console.error("FAIL: error event:", ev);
      process.exit(1);
    }
  }
}

main().catch((e) => { console.error("FAIL: harness crash:", e); process.exit(1); });
```

**Step 2: Commit the harness if it's a fresh authoring**

If the harness was lost and you rebuilt it, commit it to the rebuild branch:
```bash
git add container/agent-runner/e2e-harness.ts
git commit -m "test(adapter): re-author E2E harness for Phase C DTU verification"
```

If the harness was recovered from git, push to the DTU's gitea remote and pull inside the DTU.

**Step 3: Sync the harness into the DTU**
```bash
amplifier-digital-twin file-push <dtu-id> ./container/agent-runner/e2e-harness.ts /workspace/nanoclaw-fresh/container/agent-runner/e2e-harness.ts
```

---

## Task 8: Run E2E happy-path test in DTU

**Working dir:** Inside the DTU via `exec`.

**Files:** No source changes — this is the verification.

**Test type:** **(b) real-binary, real-LLM** — end-to-end.

**Step 1: Set up env vars + run the harness**
```bash
amplifier-digital-twin exec <dtu-id> --stream --timeout 300 -- bash -c '
  cd /workspace/nanoclaw-fresh/container/agent-runner &&
  ANTHROPIC_API_KEY="${REAL_ANTHROPIC_KEY}" \
  AMPLIFIER_AGENT_BIN=$(which amplifier-agent) \
  bun e2e-harness.ts
'
```

The harness from Task 7 spawns the amplifier-agent provider against a real Anthropic key. The `REAL_ANTHROPIC_KEY` env var was passed into the DTU via the profile's `passthrough.services` block at launch time — verify with `amplifier-digital-twin exec <dtu-id> -- env | grep ANTHROPIC`.

**Step 2: Verify the output**

Expected on stdout:
```
{"type":"init","sessionId":"<some-uuid>"}
{"type":"activity"}  (zero or more)
{"type":"result","text":"... purple ..."}
OK: happy-path turn complete
```
Exit code 0.

**Step 3: Capture the failure mode if any**

If exit code is non-zero or "FAIL" appears in stdout, dump diagnostics:
```bash
amplifier-digital-twin exec <dtu-id> -- bash -c '
  ls /home/node/.local/state/amplifier-agent/sessions/ &&
  tail -100 ~/.local/state/amplifier-agent/sessions/*/transcript.jsonl
'
```
Common failures and what they mean:
- `Error: No such option '--mcp-servers'` → Phase A's CLI flag did not land. Re-verify Phase A.
- `JSON parse error in run-output-parser` → envelope shape mismatch. Compare `amplifier-agent run --output json ...` raw output against `wrappers/typescript/src/run-output-parser.ts` expectations.
- `Cannot read property 'sessionId' of undefined` in event-translator → CR-C translation logic has a bug. Re-read Task 3 and fix.

If a fix is needed, edit, push to gitea, sync into the DTU (`file-push` or rebuild container), and re-run.

---

## Task 9: Run E2E resume continuity test in DTU

**Working dir:** Inside the DTU.

**Files:**
- Modify: `container/agent-runner/e2e-harness-resume.ts` (a sibling harness)

**Test type:** **(b) real-binary, real-LLM**.

**Step 1: Author the resume harness**

Mirrors Task 7's harness but: (1) turn 1 plants a fact ("Remember purple"), (2) turn 2 resumes the same sessionId, asks for the color, asserts "purple" appears in the reply.

```typescript
// container/agent-runner/e2e-harness-resume.ts
import { AmplifierAgentProvider } from "./src/providers/amplifier-agent";

async function runTurn(provider, prompt, continuation) {
  const query = await provider.query({ prompt, continuation, mcpServers: {} });
  let sessionId = "";
  let reply = "";
  for await (const ev of query) {
    if (ev.type === "init") sessionId = ev.sessionId;
    if (ev.type === "result") { reply = ev.text; break; }
    if (ev.type === "error") throw new Error(JSON.stringify(ev));
  }
  return { sessionId, reply };
}

async function main() {
  const provider = new AmplifierAgentProvider({ /* config */ });
  const turn1 = await runTurn(provider, "Remember the color purple. Reply just 'OK'.", null);
  console.log("turn1:", JSON.stringify(turn1));
  const turn2 = await runTurn(provider, "What color did I ask you to remember?", { sessionId: turn1.sessionId });
  console.log("turn2:", JSON.stringify(turn2));
  if (!turn2.reply.toLowerCase().includes("purple")) {
    console.error("FAIL: turn2 reply did not contain 'purple'");
    process.exit(1);
  }
  console.log("OK: resume continuity verified");
}

main().catch((e) => { console.error("FAIL:", e); process.exit(1); });
```

**Step 2: Push + run**
```bash
amplifier-digital-twin file-push <dtu-id> ./container/agent-runner/e2e-harness-resume.ts /workspace/nanoclaw-fresh/container/agent-runner/e2e-harness-resume.ts

amplifier-digital-twin exec <dtu-id> --stream --timeout 300 -- bash -c '
  cd /workspace/nanoclaw-fresh/container/agent-runner &&
  ANTHROPIC_API_KEY="${REAL_ANTHROPIC_KEY}" bun e2e-harness-resume.ts
'
```

Expected exit code: 0 with `OK: resume continuity verified` on stdout.

**Step 3: Commit the resume harness**
```bash
cd /Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh
git add container/agent-runner/e2e-harness-resume.ts
git commit -m "test(adapter): N4' — resume continuity E2E harness"
```

---

## Task 10: Run `doctor --strict` inside the DTU; final acceptance check

**Working dir:** Inside the DTU.

**Files:** No source changes.

**Test type:** **(b) real-binary**.

**Step 1: Doctor check**
```bash
amplifier-digital-twin exec <dtu-id> -- amplifier-agent doctor --strict
```
Expected: exit code 0. This validates the Phase A engine + Phase B wrapper + Phase C container image all align.

**Step 2: Optional MCP passthrough verification**

If a real MCP server (`nanoclaw_send_message` or similar) is configured in the user's profile, run a final harness that exercises the full Slack→NC→amplifier-agent→reply chain. Author this only if requested by the user; otherwise the Task 8 + Task 9 harnesses are sufficient.

**Step 3: Capture results for the closing comment**

Save the harness outputs into a single artifacts dir:
```bash
mkdir -p /tmp/phase-c-evidence
amplifier-digital-twin exec <dtu-id> -- bun e2e-harness.ts > /tmp/phase-c-evidence/happy-path.log 2>&1
amplifier-digital-twin exec <dtu-id> -- bun e2e-harness-resume.ts > /tmp/phase-c-evidence/resume.log 2>&1
amplifier-digital-twin exec <dtu-id> -- amplifier-agent doctor --strict > /tmp/phase-c-evidence/doctor.log 2>&1
```

These logs are the evidence package for the Phase C acceptance gate — paste them into the PR description.

**Step 4: No commit needed.** All code changes already landed in Tasks 2–4 and 7–9.

---

## Phase C Acceptance Gate

Every item below must be a verified pass — not "should pass" or "probably passes."

1. **NC adapter bun:test suite green:**
   ```bash
   cd /Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh/container/agent-runner && bun test
   ```
   Expected: full prior-baseline test count + new CR-C tests from Task 2, all pass.

2. **Type-check clean:**
   ```bash
   cd /Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh/container/agent-runner && bun run typecheck
   ```

3. **Dockerfile fix landed cleanly** (re-grep verifies):
   ```bash
   grep -n "UV_TOOL_BIN_DIR\|USER node\|amplifier-agent prepare\|amplifier-agent doctor" container/agent-runner/Dockerfile
   ```
   Must show four lines in the right order.

4. **DTU happy-path E2E green** (Task 8): `OK: happy-path turn complete` on stdout, exit 0.

5. **DTU resume-continuity E2E green** (Task 9): `OK: resume continuity verified` on stdout, exit 0.

6. **`amplifier-agent doctor --strict` inside DTU green** (Task 10): exit 0.

7. **Host-side / CI lint** (Task 5): if `scripts/lint-aaa-version.ts` exists, it passes.

8. **No regressions in non-amplifier providers**: NC's other providers (`claude.ts`, `mock.ts`) still pass their bun tests.

**Push the rebuild branch:**
```bash
cd /Users/mpaidiparthy/repos/AaA/opus-recon/nanoclaw-fresh
git push -u origin feat/mode-a-pivot-adapter-rebuild
```

Open a PR titled `feat(adapter): Phase C — Mode A pivot rebuild + DTU re-verify (N1'-N4')`. Body should include:
- The three logs from Task 10 (`happy-path.log`, `resume.log`, `doctor.log`).
- The DTU instance ID used (so the user can re-verify by re-running the harness).
- A note that `amplifier-agent-client-ts` is pinned to v0.3.0 (Phase B); if Phase B was a pre-release, mention the exact version (e.g. `0.3.0-rc.1`).
- A reminder that the host-side adapter file `container/agent-runner/src/providers/amplifier-agent.ts` did NOT change at the source level — the wrapper's public API is preserved by design.

**DTU cleanup:** After the PR is merged and post-merge verification is complete, destroy the DTU:
```bash
amplifier-digital-twin destroy <dtu-id>
```
Do NOT iterate `list` and destroy everything — only destroy by the specific ID returned at Task 6.

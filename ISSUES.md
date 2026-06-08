# Known Issues

Deferred work that needs attention but is not blocking the current development line.
Each issue has a status, a summary, a concrete reproducer, and everything needed
to re-open and complete the work in a future session.

---

## ISSUE-001 — hooks-approval not wired end-to-end for headless mode

**Status:** Deferred — temporarily unmounted from default bundle.

**Summary:**
`hooks-approval` (upstream: <https://github.com/microsoft/amplifier-module-hooks-approval>)
is an **opt-in** hook per its own
[USAGE_GUIDE.md](https://github.com/microsoft/amplifier-module-hooks-approval/blob/main/USAGE_GUIDE.md):

> *"Backward compatible — Tools work without approval hook."*

The module is no longer in `src/amplifier_agent_lib/bundle/bundle.md`.
When it is eventually re-mounted, all five gaps below must be closed first.

---

### Why it was unmounted

Two prior fix attempts exposed a **dual-system impedance mismatch** and were both
reverted before merge:

| Attempt | What it tried | What went wrong |
|---------|---------------|-----------------|
| PR #38 | Propagate `"approval.request"` capability from parent → child via the coordinator capability registry | Registry propagation is correct for the *wire-protocol surface*, but `ApprovalHook` reads `self.provider` (an instance attribute set via `register_provider()`), not the registry — so every command outside `DEFAULT_RULES` still auto-denied |
| Bridge fix (reverted, same commit as this unmount) | Push `WireApprovalProvider` into `ApprovalHook.self.provider` via `approval.register_provider` capability | Connected the systems, but `WireApprovalProvider.request_approval` returns a plain `dict` (wire-envelope shape); the hook calls `.approved` on the return value and crashed: `'dict' object has no attribute 'approved'` |

Three failed fix attempts is the signal to stop patching symptoms and fix the
architecture. The unmount is the correct short-term outcome.

---

### What is needed to wire it properly

Five concrete pieces, in the order they should be built:

**1. `AutoApprovalProvider` class**

A new class that takes `mode: Literal["yes", "no"]` and returns
`ApprovalResponse(approved=True/False)` immediately, without any wire-protocol
round-trip. Currently only `WireApprovalProvider` exists, and it is designed
for the JSON-RPC host-callback path — it is the wrong abstraction for
headless auto-approve/deny modes.

```python
from amplifier_core import ApprovalProvider, ApprovalRequest, ApprovalResponse

class AutoApprovalProvider(ApprovalProvider):
    def __init__(self, mode: Literal["yes", "no"]) -> None:
        self._approved = mode == "yes"

    async def request_approval(self, request: ApprovalRequest) -> ApprovalResponse:
        return ApprovalResponse(
            approved=self._approved,
            reason="Auto-approved (mode=yes)" if self._approved else "Auto-denied (mode=no)",
        )
```

**2. `WireApprovalProvider.request_approval` return type fix**

`WireApprovalProvider.request_approval` currently returns a raw `TypedDict`
(wire-envelope shape). It must be updated to return `ApprovalResponse(**wire_dict)`
so that any code path that calls it through the hook contract gets the correct
Pydantic object.

**3. Mode-aware provider factory in `_runtime.py`**

The current code unconditionally wraps `ctx.approval` in `WireApprovalProvider`
for the wire-protocol surface, but never selects a provider for the hook.
`_runtime.py` must choose the right provider at mount time:

| `approval.mode` | TTY? | Host capability? | Provider to register |
|-----------------|------|------------------|----------------------|
| `yes`           | any  | any              | `AutoApprovalProvider("yes")` |
| `no`            | any  | any              | `AutoApprovalProvider("no")` |
| `prompt`        | yes  | any              | `CliApprovalProvider` (interactive TTY) |
| `prompt`        | no   | `approval` cap present | `WireApprovalProvider` |
| `prompt`        | no   | no host approval  | fail-closed: `AutoApprovalProvider("no")` |

This factory should call `coordinator.get_capability("approval.register_provider")`
and pass the selected provider, exactly as the USAGE_GUIDE shows.

**4. Host-side wire responder (paperclip / `amplifier-agent-ts`)**

The engine emits `approval/request` JSON-RPC envelopes over stdout, but the
`amplifier-agent-ts` TypeScript wrapper currently has no handler that receives
these inbound requests from the engine and routes them to the VS Code extension
or host UI. Without this, `WireApprovalProvider` (used in `prompt` mode with a
remote host) will time out waiting for a response that never arrives.

This is a host-side concern: `paperclip` or `amplifier-agent-ts` needs new
infrastructure to receive `approval/request` notifications and either:
(a) surface them in the extension UI, or
(b) auto-respond based on extension settings.

**5. End-to-end tests for each (mode × environment) combination**

| Scenario | Expected outcome |
|----------|-----------------|
| `mode=yes`, no host, headless | `AutoApprovalProvider("yes")` → bash runs, no denial logged |
| `mode=no`, no host, headless | `AutoApprovalProvider("no")` → bash denied, `approval.denied` event emitted |
| `mode=prompt`, TTY present | `CliApprovalProvider` → user sees prompt |
| `mode=prompt`, no TTY, host wire | `WireApprovalProvider` → `approval/request` envelope sent, host responds |

---

### Reproducer

When re-wiring, this command should produce **zero** `No approval provider` or
denial log lines:

```bash
mkdir -p ~/amp-cwd-test
env -i PATH=$PATH HOME=$HOME USER=$USER LANG=$LANG TERM=$TERM TMPDIR=$TMPDIR \
  ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
  amplifier-agent run --session-id approval-verify --fresh \
    --cwd ~/amp-cwd-test --output json --protocol-version 0.3.0 -y \
    "Use the bash tool to run 'cat /etc/hostname'." < /dev/null \
    2>&1 | grep -iE 'approval|denied'
```

Today (hooks-approval unmounted): empty output — bash runs unchecked (backward
compatible per USAGE_GUIDE).

After re-wire with `mode=yes`: empty output — `AutoApprovalProvider` returns
`approved=True`; the tool runs identically from the user's perspective, but
audit events are now emitted by the hook.

---

### Reference material

- USAGE_GUIDE: <https://github.com/microsoft/amplifier-module-hooks-approval/blob/main/USAGE_GUIDE.md>
- `ApprovalResponse` schema: `from amplifier_core import ApprovalResponse` — Pydantic `BaseModel`
  with fields `approved: bool`, `reason: str | None`, `remember: bool`.
- The `approval` config forwarding is still in place in `config/merger.py:168-171`; the
  `host_config["approval"]["mode"]` key is preserved and flows to `merged["hooks-approval"]`
  (which is currently unused). When the hook is re-mounted, it will pick up this config.
- PR #38 capability-inheritance loop in `spawn.py` is still present; it propagates the
  wire-protocol `"approval.request"` capability to child sessions and remains correct
  scaffolding for when the wire provider path is fully wired.
- Unmount commit: `<this PR>` (the commit that introduced this file).

---

## ISSUE-002 — No wrapper-level hang detection when `timeoutMs` is disabled

**Status:** Deferred — design question; tracking the consequence of making `timeoutMs` opt-in (PR #41).

**Summary:**
After PR #41 (`fix: make timeout opt-in instead of silently imposing 10-min wall-clock cap`),
the TypeScript wrapper (`amplifier-agent-ts`) arms a wall-clock hang timer only when the
caller passes a positive `timeoutMs`. Callers that pass `0` or `undefined` (the
`amplifier-app-paperclip` adapters do this deliberately, per PR
[microsoft/amplifier-app-paperclip#13](https://github.com/microsoft/amplifier-app-paperclip/pull/13))
get **no wrapper-side hang detection at all**.

The 2-second activity ticker still emits `{type: "activity"}` heartbeats into the
event stream (`ACTIVITY_TICK_MS = 2000` in `wrappers/typescript/src/session.ts`),
but the ticker is a **heartbeat, not an escalation mechanism** — it never calls
`cancel()`, never synthesizes `engine_hung`, and never terminates the subprocess.
If the engine subprocess hangs (deadlock, infinite loop, wedged tool), the only
mechanism that will eventually kill it is the caller's own watchdog plus an
explicit `handle.cancel()`.

This is the **intended** behavior of PR #41 — the wrapper now treats wall-clock
caps as opt-in. But it shifts a previously-implicit responsibility (subprocess
hang recovery) onto every consumer, and the current JSDoc says only "no
wall-clock cap unless you ask for one" — it does not name the new caller
responsibility.

---

### Open design question: how do we detect "actually hung" vs "doing long work"?

A 12-second engine doing genuine deep work and a 12-second engine deadlocked on
a tool call look **identical** from outside the subprocess. Wall-clock
timeouts treat both the same way (hard kill at N seconds) — that is the
old behavior PR #41 deliberately moved away from, because it killed
real long-running agent turns.

The real signal of liveness is not wall-clock time but **progress**. The wrapper
already receives signals that should let it distinguish the two without
re-introducing a wall-clock cap:

1. **NDJSON event flow on stderr** — `tool/started`, `tool/finished`, model
   token deltas, etc. The activity ticker fires every 2s regardless; what
   matters is whether *real* engine events have arrived in the recent window.
2. **stdout/stderr byte deltas** — even without parseable NDJSON, output is
   evidence the subprocess is alive and making progress.
3. **Tool-lifecycle events specifically** — a `tool/started` without a matching
   `tool/finished` for >N seconds is the most meaningful "stuck" signal,
   because most legitimate long spans are inside a single tool call.

None of these are currently wired into any escalation path. The activity
ticker is the closest plumbing but its purpose today is purely UI/feedback.

---

### What is needed to wire it properly

Five concrete pieces, roughly in the order they should be built:

**1. Define "stuck" precisely**

Pick one of (or layer them):

| Signal | What "stuck" means | Default threshold |
|--------|--------------------|-------------------|
| `tool/started` without matching `tool/finished` | A tool call that has not completed | e.g. 5 min |
| No new stdout/stderr bytes | Subprocess produced no output | e.g. 2 min |
| No NDJSON event on stderr | No structured progress reported | e.g. 2 min |

The current wall-clock `timeoutMs` measures none of these — it just times the
whole turn. The above are all **progress-based** and tolerate genuinely
long deep-work spans.

**2. Add a `stuckDetection` option to `SessionHandleParams` / `SpawnAgentParams`**

A new option distinct from `timeoutMs`. Probable shape:

```ts
interface StuckDetectionConfig {
  /** Idle threshold in ms — no progress signal within this window → "stuck". */
  noProgressMs: number;
  /** What counts as progress. Default: any stdout/stderr byte OR any NDJSON event. */
  signal?: "any-output" | "ndjson-event" | "tool-finished";
}

interface SessionHandleParams {
  // …existing fields…
  timeoutMs?: number;            // wall-clock cap (opt-in, post-#41)
  stuckDetection?: StuckDetectionConfig;  // progress-based (NEW)
}
```

This is **independent** of `timeoutMs`: a caller can have no wall-clock cap
*and* still get hang protection by setting `stuckDetection`. Both default to
unset (caller responsibility).

**3. Implement progress tracking in `SessionHandle.submit()`**

Add a `lastProgressAt` timestamp updated on every stdout/stderr chunk (or
filtered subset per `signal`). Replace the all-or-nothing `setTimeout` with a
recurring check (could reuse the 2s activity ticker) that compares
`Date.now() - lastProgressAt` against `stuckDetection.noProgressMs` and
escalates the same way the old timeout did: synthesize `engine_hung` (or a new
`engine_stuck` code), call `cancel()`.

**4. Document the consumer contract**

Update JSDoc on `timeoutMs` to explicitly state: *"With no `timeoutMs` and no
`stuckDetection`, the caller is responsible for detecting and cancelling hung
subprocesses. The wrapper will not auto-recover."*

Also worth a section in the wrapper README on the three regimes:
- Pure deep work, no caps (current PR #41 path) — caller owns recovery
- Progress-based detection — wrapper escalates on lack-of-progress
- Hard wall-clock cap — wrapper escalates at fixed deadline (legacy behavior)

**5. End-to-end tests for each regime**

Mirroring the existing `timeout-longwindow-integration.test.ts` style:

| Scenario | Expected outcome |
|----------|-----------------|
| `stuckDetection: {noProgressMs: 500}`, engine emits NDJSON every 200ms for 5s | Completes normally, no `engine_stuck` |
| `stuckDetection: {noProgressMs: 500}`, engine emits one event then sleeps 5s | `engine_stuck` fires at ~500ms after last event |
| `timeoutMs: 5000` + `stuckDetection: {noProgressMs: 500}`, engine silent | `engine_stuck` fires first (~500ms), `engine_hung` would have fired at 5000ms |
| Neither set, engine hangs forever | No escalation; caller must `cancel()` |

---

### Reproducer

The hang-without-detection condition is trivially observable today:

```bash
# In wrappers/typescript:
cat > /tmp/hang.mjs <<'JS'
import { spawnAgent } from "./dist/index.js";
const handle = await spawnAgent({
  lifecycle: "one-shot",
  sessionId: "hang-test",
  timeoutMs: 0,             // explicit no-timeout per PR #41 contract
  _binaryResolver: () => "/bin/sh",
  _engineVersionProbe: async () => ({ version: "0.0.0", protocolVersion: "0.3.0" }),
});
const start = Date.now();
for await (const ev of handle.submit("-c 'sleep 3600'")) {
  console.log(Date.now() - start, ev);
}
JS
node /tmp/hang.mjs
```

Today: prints `init`, then `activity` heartbeats every 2s for an hour — no
escalation, no recovery. The subprocess is killed only when the Node process
exits or the user manually calls `handle.cancel()`.

After this issue is wired (`stuckDetection: {noProgressMs: 5000}`): the same
script emits `init`, ~2 activity events, then an `error` event with code
`engine_stuck` at ~5s, and the subprocess is cancelled.

---

### Reference material

- PR introducing the opt-in change: <https://github.com/microsoft/amplifier-agent/pull/41>
- Downstream consumer that pins `timeoutMs: 0`: <https://github.com/microsoft/amplifier-app-paperclip/pull/13>
- Activity ticker source: `wrappers/typescript/src/session.ts` (search for `ACTIVITY_TICK_MS`)
- Existing wall-clock test that should be preserved as a positive control:
  `wrappers/typescript/test/timeout-longwindow-integration.test.ts` case (3)
- The `engine_hung` synthesis pattern is the right template for `engine_stuck` —
  same `AaaError`-shaped `DisplayEvent`, just emitted from a different trigger.

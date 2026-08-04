# Known Issues

Deferred work that needs attention but is not blocking the current development line.
Each issue has a status, a summary, a concrete reproducer, and everything needed
to re-open and complete the work in a future session.

---

## ISSUE-001: hooks-approval not wired end-to-end for headless mode

**Status:** Deferred, temporarily unmounted from default bundle.

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
| PR #38 | Propagate `"approval.request"` capability from parent → child via the coordinator capability registry | Registry propagation is correct for the *wire-protocol surface*, but `ApprovalHook` reads `self.provider` (an instance attribute set via `register_provider()`), not the registry, so every command outside `DEFAULT_RULES` still auto-denied |
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
for the JSON-RPC host-callback path. It is the wrong abstraction for
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

Today (hooks-approval unmounted): empty output. Bash runs unchecked (backward
compatible per USAGE_GUIDE).

After re-wire with `mode=yes`: empty output. `AutoApprovalProvider` returns
`approved=True`; the tool runs identically from the user's perspective, but
audit events are now emitted by the hook.

---

### Reference material

- USAGE_GUIDE: <https://github.com/microsoft/amplifier-module-hooks-approval/blob/main/USAGE_GUIDE.md>
- `ApprovalResponse` schema: `from amplifier_core import ApprovalResponse` (Pydantic `BaseModel`
  with fields `approved: bool`, `reason: str | None`, `remember: bool`).
- The `approval` config forwarding is still in place in `config/merger.py:168-171`; the
  `host_config["approval"]["mode"]` key is preserved and flows to `merged["hooks-approval"]`
  (which is currently unused). When the hook is re-mounted, it will pick up this config.
- PR #38 capability-inheritance loop in `spawn.py` is still present; it propagates the
  wire-protocol `"approval.request"` capability to child sessions and remains correct
  scaffolding for when the wire provider path is fully wired.
- Unmount commit: `<this PR>` (the commit that introduced this file).

---

## ISSUE-002: No wrapper-level hang detection when `timeoutMs` is disabled

**Status:** Deferred. Design question; tracking the consequence of making `timeoutMs` opt-in (PR #41).

**Summary:**
After PR #41 (`fix: make timeout opt-in instead of silently imposing 10-min wall-clock cap`),
the TypeScript wrapper (`amplifier-agent-ts`) arms a wall-clock hang timer only when the
caller passes a positive `timeoutMs`. Callers that pass `0` or `undefined` (the
`amplifier-app-paperclip` adapters do this deliberately, per PR
[microsoft/amplifier-app-paperclip#13](https://github.com/microsoft/amplifier-app-paperclip/pull/13))
get **no wrapper-side hang detection at all**.

The 2-second activity ticker still emits `{type: "activity"}` heartbeats into the
event stream (`ACTIVITY_TICK_MS = 2000` in `wrappers/typescript/src/session.ts`),
but the ticker is a **heartbeat, not an escalation mechanism**. It never calls
`cancel()`, never synthesizes `engine_hung`, and never terminates the subprocess.
If the engine subprocess hangs (deadlock, infinite loop, wedged tool), the only
mechanism that will eventually kill it is the caller's own watchdog plus an
explicit `handle.cancel()`.

This is the **intended** behavior of PR #41: the wrapper now treats wall-clock
caps as opt-in. But it shifts a previously-implicit responsibility (subprocess
hang recovery) onto every consumer, and the current JSDoc says only "no
wall-clock cap unless you ask for one". It does not name the new caller
responsibility.

---

### Open design question: how do we detect "actually hung" vs "doing long work"?

A 12-second engine doing genuine deep work and a 12-second engine deadlocked on
a tool call look **identical** from outside the subprocess. Wall-clock
timeouts treat both the same way (hard kill at N seconds). That is the
old behavior PR #41 deliberately moved away from, because it killed
real long-running agent turns.

The real signal of liveness is not wall-clock time but **progress**. The wrapper
already receives signals that should let it distinguish the two without
re-introducing a wall-clock cap:

1. **NDJSON event flow on stderr**: `tool/started`, `tool/finished`, model
   token deltas, etc. The activity ticker fires every 2s regardless; what
   matters is whether *real* engine events have arrived in the recent window.
2. **stdout/stderr byte deltas**: even without parseable NDJSON, output is
   evidence the subprocess is alive and making progress.
3. **Tool-lifecycle events specifically**: a `tool/started` without a matching
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

The current wall-clock `timeoutMs` measures none of these. It just times the
whole turn. The above are all **progress-based** and tolerate genuinely
long deep-work spans.

**2. Add a `stuckDetection` option to `SessionHandleParams` / `SpawnAgentParams`**

A new option distinct from `timeoutMs`. Probable shape:

```ts
interface StuckDetectionConfig {
  /** Idle threshold in ms: no progress signal within this window → "stuck". */
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
- Pure deep work, no caps (current PR #41 path): caller owns recovery
- Progress-based detection: wrapper escalates on lack-of-progress
- Hard wall-clock cap: wrapper escalates at fixed deadline (legacy behavior)

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

Today: prints `init`, then `activity` heartbeats every 2s for an hour. No
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
- The `engine_hung` synthesis pattern is the right template for `engine_stuck`:
  same `AaaError`-shaped `DisplayEvent`, just emitted from a different trigger.

---

## Minor defect log

Surfaced while restructuring `docs/spec/`. None of these block anything, and none were
fixed in that pass; they are recorded so the next contributor does not have to rediscover
them. Fix them when you are already in the neighborhood. This is not a backlog.

### Declared but unimplemented surface

- `session/create`, `session/end` and `cache/info` have TypedDicts, generated schemas and
  rows in the generated `protocol/spec.md`, but no branch in `Engine.dispatch`
  (`engine.py:259-264`). Calling one raises `ValueError`.
- The generated spec declares the method name `initialize` (`protocol/_gen.py:179`);
  `dispatch` matches `agent/initialize`. A caller following the generated spec verbatim fails.
- `TurnSubmitResult.finalEvent` (`protocol/methods.py:117`, mirrored in
  `wrappers/typescript/src/types.ts:309`) is never populated on any engine path.
- `bundleDigest` is hardcoded `""` on all three envelope paths
  (`modes/single_turn.py:211`, `:354`, `:404`) while being a typed public field on both
  wrapper SDKs, and the TypeScript wrapper's own tests assert a populated `sha256:...` value.
- `run --bundle` is declared hidden at `modes/single_turn.py:574`, bound to the `bundle`
  parameter at `:646`, and never read in the body. Wire it up or delete it.
- `stderr_tail` is a parameter of `_build_error_envelope` (`modes/single_turn.py:347`) that
  neither caller (`:857`, `:882`) passes, so the engine cannot emit `stderrTail` today.
- `AMPLIFIER_AGENT_HTTP_MODEL_NAME` / `ServerConfig.model_display_name`
  (`_config.py:23`, `:94`) is loaded and read by no route.
- `resources.list_modes(config)` accepts a `config` argument its own docstring admits is
  unused. Honour it or drop the parameter.
- `Engine.boot()` performs no storage-layout check, so the documented "refuse and tell the
  user to run `migrate`" contract does not exist. A stale flat `sessions/` tree is silently
  ignored and a workspace-nested one is created alongside it.
- `tool_calls/delta` is emitted through the governed `display.emit` capability
  (`bundle/host_tool_hook.py:55`) and translated by the HTTP face
  (`_event_translator.py:89`) but has no TypedDict, no schema and no membership in
  `CANONICAL_DISPLAY_EVENTS`. Declare it or route it off the display channel.
- The conformance loader accepts `notification_order` and `session_state` assertion kinds
  that neither runner evaluates; they are reported `ok=true` and skipped
  (`wrappers/conformance/README.md:34`). Implement them or reject them at load time.

### Dead or unreachable code

- `_runtime.handle_initialize` (`_runtime.py:624`) has no production caller. Every importer
  is a test. It is the entry point for a wire face that was never built.
- `amplifier_agent_lib/jsonrpc.py` has no production caller; the wrappers and conformance
  runners ship their own framing.
- `_emit_error` (`modes/single_turn.py:51`) has no callers and emits a pre-envelope
  `{"error": {"code", "message"}}` shape nothing on the current path produces.
- `persistence.session_state_dir()` (`persistence.py:158`) returns the pre-workspace flat
  layout. Its only callers anywhere are its own tests. Delete both.
- `_PROVIDER_NAME_TO_MODULE_KEY` (`config/merger.py:40-45`) maps friendly names to
  `<name>-provider` while the real mount-plan ids are `provider-<name>`, so the host
  `provider` block merge writes to a key no module reads. Behavior stays correct only
  because `provider.config` also travels the injection path. The map also omits
  `github-copilot`, which the loader accepts.
- `bundle.md:94-98` declares a `session.provider` block naming `anthropic-provider` from
  `amplifier-module-anthropic-provider`, a module id in no catalog entry. Its own comment
  says the top-level `providers:` stubs do the install. Looks vestigial.
- `update.py:216`: the third clause of
  `needs_install = (not versions_match) or force or (tag_override is not None and force)`
  is subsumed by the second and has no effect.

### Error handling and correctness gaps

- `_read_bundle_default_provider()` is called at `modes/single_turn.py:728`, outside any
  `try`. An `AaaError(bundle_load_failed)` raised there produces a traceback instead of an
  error envelope. Move it inside the guarded region.
- `_build_error_envelope` classifies via `_CLASSIFICATION_BY_CODE` and ignores the
  `classification` the raiser attached to the `AaaError`, so `bundle_load_failed`
  (raised with `classification="protocol"`) would classify as `"engine"` and exit 1.
- `_emit_argv_envelope`'s metadata block (`modes/single_turn.py:207-215`) omits `activeMode`
  entirely while `_build_error_envelope` sets it to `None`, so consumers cannot read
  `metadata.activeMode` uniformly across failure shapes.
- `_auth.py:38` compares the bearer token with `!=` rather than `hmac.compare_digest`.
  Low risk on a localhost default, but a one-line fix.
- `serve status` reads `api_key` from the state file. The file is 0600, but the shared
  secret sits in plaintext at rest; consider whether it needs to be there at all.
- `publish-python.yml` admits `workflow_dispatch` at the job level (`:35`) while gating the
  tag-vs-version check on `github.event_name != 'workflow_dispatch'` (`:51`). A manual
  dispatch can publish whatever is on the default branch with no tag correspondence.
- The Python spawn entry point catches only `AaaError` around the version probe; TypeScript
  catches everything. Divergent under a failure mode neither is likely to hit.
- `hook-context-intelligence` is configured with the literal base path
  `~/.amplifier-agent/state/workspaces` (`bundle.md:242`). It expands `~` but not
  `$AMPLIFIER_AGENT_HOME`, so a relocated storage root still writes under the default root.

### Stale constants, docstrings and strings

- `src/amplifier_agent_lib/__init__.py:26` hardcodes a fallback `__version__ = "0.3.0"`
  against an actual package version of 0.12.0. That value is the prepared-bundle cache key
  and the `version --json` payload, so a fallback hit silently reads and writes
  `prepared/0.3.0/...` and misreports to every wrapper probe. Derive it or remove it so the
  failure is loud.
- Ten docstrings describe the cache root as `$XDG_CACHE_HOME/amplifier-agent/`; the real
  root is `~/.amplifier-agent/cache/`, overridable only via `$AMPLIFIER_AGENT_HOME`. Sites:
  `bundle/cache.py:52`, `:72`; `admin/cache_clear.py:4`, `:7`, `:35`, `:38`;
  `engine.py:129`, `:166`; `post_install.py:1`; `__main__.py:19`. Same class of error in
  `config/__init__.py:6` ("a conventional XDG location", there is no XDG config tier) and
  `admin/doctor.py:1`, `:6-8`, `:501`. The `XDG_*` reads in `migration.py` are deliberate;
  leave those alone.
- `wrappers/typescript/README.md:94` claims protocol version 0.1.0; the actual is 0.3.0
  (`wrappers/typescript/src/index.ts:133`).
- The protocol-mismatch remediation in `wrappers/typescript/src/version.ts:53` and
  `wrappers/python-py/src/amplifier_agent_py/version.py:61` tells operators to set
  `AMPLIFIER_AGENT_ALLOW_PROTOCOL_SKEW=1`. The engine no longer honors that variable
  (`engine.py:150`); the only override is the `allowProtocolSkew` host-config key.
  Operator-visible.
- `modes/single_turn.py:779` names `npm install amplifier-agent-client-ts@latest`. That
  package is unpublished scaffolding; the published one is `amplifier-agent-ts`.
- `.github/workflows/install-script.yml:52` pins its smoke test at `--tag v0.9.0`, so the
  installer is never exercised against the current release.
- `src/amplifier_agent_cli/__init__.py:7` still describes Mode B stdio as "stubbed; full
  implementation in Phase 3" for a removed feature.
- `README.md:168` and `:464` say `--config <path-to-yaml>`; host config is JSON, as `:227`
  correctly states.
- `handle_initialize`'s docstring (`_runtime.py:626-630`) claims it stores
  `params.host.capabilities` on `session.metadata`. The body never touches `session.metadata`;
  `hostCapabilities` was removed from every surface and the docstring survived.
- `_wire.py:71-73` says host tools are "accepted but ignored in the POC". Host-tool
  delegation is implemented end to end and the terminal chunk does emit
  `finish_reason: "tool_calls"`.
- `_event_translator.extract_usage`'s docstring (`:165-168`) says the last usage event in the
  turn is kept; `chat_completions.py:503-508` sums across all of them.
- `_session_runner.py:337-341` describes an unknown mode as warn-not-crash. The route rejects
  unresolved modes with 400/503 before the runner is reached, so the comment describes an
  unreachable state.
- `wrappers/typescript/src/argv-builder.ts:72`, `index.ts:209` and `session.ts:196` describe
  a workspace slug of "cwd basename plus an 8-char sha256". The engine does straight path
  substitution without hashing, so `/home/u/proj` yields `-home-u-proj`, not `proj-9e80f0e7`.
- `resolveBinaryPath`'s docstring documents step 1 as `AMPLIFIER_AGENT_BIN` "if set and the
  path exists on disk". The body returns the env value regardless, which is intended.
- The TypeScript `DisplayEvent` JSDoc lists a third, wrong taxonomy: it omits `usage` and
  adds the two approval notifications. Nothing enforces the comment.
- `tests/cli/test_provider_sources.py:26` `test_catalog_lists_all_four_detection_names` is
  named for four providers and asserts the correct five-element set. Rename.
- `version_skew.yaml` scripts an `error.data` carrying `clientVersion`, `serverVersion` and
  `remediation`. The engine emits only `code` and `message`. The fixture is a scripted replay,
  so the divergence is invisible to the parity test.
- Two live TODO markers: `bundle.md`'s `TODO(upstream-tag)` pinning
  `hook-context-intelligence` at `@main`, and `auth.py:74` `_CONFIG_CREDENTIAL_UNSUPPORTED`,
  marked TEMPORARY, which gates only `github-copilot` out of `auth set` (`:316-327`). Delete
  the constant and its gate together; do not grow it into a general capability mechanism.

### Duplicated or divergent implementations

- Adding a provider requires editing five literals: `bundle.md` plus `PROVIDER_CATALOG`,
  `KNOWN_PROVIDERS`, `PROVIDER_CREDENTIAL_VARS` and `_VALID_PROVIDER_MODULES`. Only the first
  three are held together by `tests/cli/test_provider_catalog.py`. The other two can drift
  silently. Note also that every pre-wired provider is installed during cold-prepare for all
  users regardless of whether they hold credentials for it.
- The root `package.json` is named `amplifier-agent-client-ts` while serving only as the pnpm
  workspace root manifest. Nothing publishes it. The name collides conceptually with the
  inner `amplifier-agent-ts` that `publish-wrapper.yml` actually publishes.
- The HTTP face bypasses `Engine` entirely and calls `_session_runner.run_chat_turn`, a
  copy-and-adapt of `_runtime.make_turn_handler`. Nothing states which path is normative and
  no test ties them together.
- The `workspace` / `project_slug` dual-key write exists twice: `_runtime.py:99-106`
  (unconditional) and `_session_runner.py:296`, `:331` (guarded by `if workspace:`).
  Route it through one shared helper.
- `_config.py:101` collapses `$AMPLIFIER_AGENT_HTTP_WORKSPACE` and
  `$AMPLIFIER_AGENT_WORKSPACE` into one value and then calls the shared resolver with an
  empty env dict, so the env tier is re-implemented one layer up. Pass the env through instead.
- `_render_table`, `_render_conflicts` and `_CONFLICT_MARKER` are duplicated verbatim in
  `admin/skills.py:93`, `:96`, `:121` and `admin/modes.py:76`, `:79`, `:103`. Extract one
  implementation.
- Two divergent canonical event lists: `protocol/notifications.py:29` declares nine display
  events; `bundle/hook_streaming.py:30` declares a seven-element `CANONICAL_WIRE_EVENTS`
  that omits `progress` and `error` even though the same hook emits `error`. Each is checked
  by its own test and nothing asserts a subset relation. Make one derive from the other.
- `resources.list_skills` does not consult `$AMPLIFIER_SKILLS_DIR`; only the CLI turn handler
  does (`_runtime.py:44-57`). A skill provisioned that way is invocable but invisible to
  `skills list` and `GET /v1/skills`.
- `inject_routing_matrix` runs only on the CLI path (`modes/single_turn.py:510`). The HTTP
  session runner injects the provider but not the matrix, so served requests always use the
  bundle's default matrix. Decide whether that divergence is intended.
- The `result/final` synthesis obligation is implemented only in the conformance runners.
  The shipped SDK path yields a `result` event derived from the envelope's `reply`, which is
  a different type on a different channel. Implement it live or restate the obligation.

### Missing test coverage

- Nothing asserts that every declared protocol method has a dispatch branch and every
  dispatched name is declared. That gap is what let the `initialize` naming and the three
  undispatched methods above drift unnoticed.
- Nothing pins the TypeScript `assembleArgv` against the Python `assemble_argv` even though
  the Python module declares itself a 1:1 mirror. Binary discovery precedence, blocked env
  key parity and the MCP spill path are likewise untested across languages; the existing
  parity test covers only the JSON-RPC assertion layer, which the shipped SDK path does not use.
- Bundle cache: no test that two manifest contents at the same version produce different
  cache directories (the exact failure the content hash prevents), none for a corrupt cached
  artifact producing a warning plus cold rebuild, and none for `bundle_load_failed` on a
  missing `default_provider:`.
- CLI: `--host-capabilities` and `--mcp-servers` have no removal test unlike the other
  removed flags, and there is no coverage for `os.setsid()` process-group setup,
  `AMPLIFIER_AGENT_DEBUG_SIDLOG`, `doctor --emit-sha`, or `auth` file permissions beyond
  `tests/cli/test_admin_auth_set.py`.
- Envelope: the audit trail is covered only on the success path, and neither
  `severity: "warning"` nor the exact classification per code in `_CLASSIFICATION_BY_CODE`
  is asserted.
- Config: no coverage for `config_invalid_type` on a non-dict `debug` block or for any
  `providers` registry validation path. The merger tests build synthetic mount plans keyed on
  the merger's own spelling, which is why the provider-merge no-op above went unnoticed;
  build the plan from the vendored bundle instead.
- HTTP: no `tests/http/test_auth.py` (both 401 paths for `require_bearer` are unguarded), no
  test for `translate_event` / `extract_usage`, nothing asserting SSE chunk ordering or the
  keepalive interval, no test for host-tool delegation despite it being a full round trip
  with an external client, and nothing guarding the exclusion of `mode-<name>` aliases from
  `GET /v1/models` (it holds by construction today).
- Skills and modes: no `tests/cli/test_admin_skills.py` or `test_admin_modes.py`, so the JSON
  bare-list shape, table columns, `(!)` footer and `--json` stdout discipline are guarded only
  by e2e. `$AMPLIFIER_SKILLS_DIR` precedence and the built-in skill and mode sets are likewise
  e2e-only.
- Providers: `inject_routing_matrix` / `PROVIDER_MATRIX_MAP` (`provider_sources.py:664-674`)
  has no test file, so a typo in a matrix name falls through to the bundle default silently.
- Storage: no dedicated test for the dual-key identity write or for the context-intelligence
  pre-seed, and `migrate --output json` has no payload-shape test at all.
- Install: no coverage for `install.sh` prerequisite checks or tag-resolution failures (the
  smoke test covers only the happy path with an explicit `--tag`), for prepared-cache
  accumulation, or for session-state survival across an engine upgrade.

# Baked-in Bundle Decision — Strategy 1 (Vendored Opinionated Manifest)

**Status:** RESOLVED — supersedes `docs/designs/2026-05-19-baked-in-bundle-revisit.md`.
**Decision owner:** Manoj Prabhakar Paidiparthy
**Date:** 2026-05-19

---

## 1. Problem framing

The Layer 4 design checkpoint (`docs/status/amplifier-as-agent-design-checkpoint.md`) repeatedly claims amplifier-agent ships a **"Built-in bundle, vendored"** that **"strips bundle-loading from cold-start"** and delivers **"near-instant"** startup. Phase 1–4 shipped something narrower: only the manifest YAML (~2 KB) lives inside the wheel. Every module the manifest references — the build-up-foundation bundle, the orchestrator, the context module, the providers, the tool/hook modules — is git-cloned and pip-installed on first invocation. The first run on a fresh machine pays 5–30 s of network + install cost; only subsequent runs hit the warm XDG pickle.

The 2026-05-19 cheatsheet walk-through made the gap concrete. Even after the Thread 1 fix (`context-persistent → context-simple`, commit `654dfac`) and the URI-fragment correction (commit `44db0f4`), the `includes: build-up-foundation` block continued to fail with `No handler for URI: build-up-foundation`. Investigation revealed why: foundation's include resolver, in our call path, only honors named-bundle URIs that have been pre-populated into a registry by `amplifier bundle add` (an out-of-band CLI command that lives in `amplifier-app-cli`). The `git+https://…#subdirectory=…` form documented in build-up's README is what `amplifier bundle add` consumes to write that registry entry — not what foundation's include resolver itself parses. amplifier-agent runs no registry-population step. We are reusing app-cli's manifest format while skipping app-cli's registry layer that the manifest format silently assumes exists.

This is a structural gap, not a config bug. The question is not "which include URI works?" but "which packaging strategy is right for amplifier-agent's actual product positioning?"

### Product positioning that drives the decision

> *"We will ship an opinionated bundle. The opinionated bundle may come with fixed dependencies, like tool modules, hook modules, orchestrator and provider modules which live in their own repos and may get periodic updates."* — design owner, 2026-05-19

That sentence rules out everything that bundles module content (it forces us to track upstream SHAs and re-release on every module change) and everything that defers identity to an external registry (it makes our packaging depend on app-cli). It pulls us toward a self-contained, explicit, opinionated manifest that points at modules' own repos.

## 2. Assumptions and constraints

**Inherent constraints (cannot be revisited in this pass):**

- **C1.** Foundation's lazy activator runs `uv pip install --no-sources` for every module install. Quoted verbatim from `amplifier_foundation/modules/activator.py:471`: *"Ignore [tool.uv.sources] in the package's pyproject.toml. Modules use this section for dev convenience (pointing amplifier-core to git), but at runtime the PyPI wheel is already installed. Without this flag, uv would try to build amplifier-core from git source, which requires native toolchains (Rust, protobuf) that users don't have."* Every module we declare must resolve under this flag.
- **C2.** Foundation's `load_bundle()` URI grammar in our flow only handles `file://` and direct `git+https://` URLs to a manifest file. `includes:` with named-bundle references requires registry population. We do not run a registry-population step.
- **C3.** Python wheel format and XDG path conventions. Standard, unchanged.
- **C4.** Upstream module repos move at their own cadence. amplifier-agent has no commit access to them and no release coordination.

**Self-imposed constraints (the surfaces we are deliberately choosing):**

- **S1.** Vendoring scope is "manifest + agent files" — not module sources, not module wheels, not prepared pickles.
- **S2.** Module `source:` refs are `@main`. Upstream updates flow automatically; AaA releases are not gated on module-repo state.
- **S3.** First-run cost of 5–30 s is acceptable and documented honestly.
- **S4.** Post-install cache priming remains opt-in (`amplifier-agent-post-install` console script).
- **S5.** amplifier-agent diverges from amplifier-app-cli on packaging. We do not share the foundation bundle, do not depend on the registry, do not use `includes:`.

## 3. Boundaries and components

The decision affects exactly one subsystem: `src/amplifier_agent_lib/bundle/` plus its wheel-build configuration. Everything else (engine, wire protocol, mode A/B split, provider injection from commit `e67bdd9`, the `Engine.boot/submit_turn/shutdown` shape) is untouched.

```
src/amplifier_agent_lib/bundle/
├── bundle.md         ← manifest text (sealed). Rewritten: explicit modules, no `includes:`.
├── agents/           ← NEW. Vendored markdown for explorer/planner/coder/tester.
│   ├── explorer.md
│   ├── planner.md
│   ├── coder.md
│   └── tester.md
├── context/          ← NEW (if needed). Vendored agent context files referenced by agents.
├── cache.py          ← Cache key extended: aaa_version + sha256(bundle.md).
├── loader.py         ← Unchanged.
└── __init__.py       ← Unchanged.
```

**What lives in the wheel:** manifest text, agent definition markdown, agent context markdown.
**What does NOT live in the wheel:** module source trees, module wheels, prepared pickles, third-party dependencies of modules.

## 4. Decision: Strategy 1 — Vendored Opinionated Manifest

Drop the `includes:` block from `bundle.md`. Declare every module the runtime needs — orchestrator, context, providers, tool modules, hook modules — directly in the manifest with explicit `module:` + `source:` entries. Each `source:` is a `git+https://` URL with `@main`. Vendor the four sub-session agent definitions from build-up (`explorer`, `planner`, `coder`, `tester`) as markdown files inside the wheel. Reference them via an explicit `agents:` block in the manifest.

This resolves the structural gap by removing the dependency on it: there is no `includes:` to fail, no registry to populate, no `foundation:` namespace prefix to satisfy. amplifier-agent's bundle becomes self-describing in its own manifest.

### 4.1 Resolutions to the seven decisions captured in the predecessor doc

| Decision | Resolution |
|---|---|
| **D1 — Vendoring scope** | Vendor (a) the manifest text and (b) the four sub-session agent definitions as markdown files. Do not vendor module source trees, module wheels, or prepared pickles. |
| **D2 — Cache-key invariant** | New cache key: `aaa_version + sha256(bundle.md content)`. Manifest edits self-invalidate the warm pickle. Fixes the silent stale-cache failure mode (F8 in predecessor doc). |
| **D3 — First-invocation cost** | Accept the 5–30 s first-run cliff. Document it honestly. The opt-in `amplifier-agent-post-install` console script remains the amortization path for users who want fast first-run. |
| **D4 — What "sealed" means** | Sealed *manifest text* only. Module `source:` URLs use `@main`. Upstream module updates flow automatically. Drift is intentional product behavior, not a defect. |
| **D5 — Transcript persistence** | Out of scope. Remains a future CLI-layer hook concern (the `IncrementalSaveHook` pattern from app-cli). Not blocking. |
| **D6 — Relationship to amplifier-app-cli** | Diverge by design. amplifier-agent ships an opinionated, self-contained manifest. We do not share foundation's bundle, do not depend on the registry, do not use `includes:`. |
| **D7 — Post-install hook discoverability** | Keep the current opt-in console script. Do not make it mandatory. Document the two-step install for users who want a primed cache. |

### 4.2 The four claims that get honestly renamed

The Phase 1 architect surfaced four mismatches between the design checkpoint's language and shipped reality. Strategy 1 **renames** them rather than **closing** them — the original claims were too aspirational for what amplifier-agent actually is.

| Original claim | New language |
|---|---|
| "Built-in bundle, vendored" | "Vendored opinionated manifest" — only the manifest text and four agent files are vendored. Modules live in their own repos. |
| "Sealed" | "Sealed manifest text" — the YAML inside the wheel is immutable per release. The `@main` refs it points to are not sealed. |
| "Strips bundle-loading from cold-start" | "Near-instant on warm cache" — first-run pays 5–30 s. Subsequent runs are sub-second. |
| "Near-instant once bundle-loading overhead is stripped" | "Near-instant after first invocation" — same correction. |

These rewordings land in the design checkpoint and the cheatsheet as part of the implementation work.

## 5. Flows

### 5.1 First invocation (cold cache)

```
amplifier-agent run "hello"
 └─ load_and_prepare_cached(aaa_version, sha256(bundle.md))
     ├─ cache miss
     └─ load_and_prepare_bundle()
         ├─ amplifier_foundation.load_bundle(file://BUNDLE_MD)
         │   ├─ parse manifest (no includes; explicit modules + agents)
         │   ├─ git clone microsoft/amplifier-module-loop-streaming@main
         │   ├─ git clone microsoft/amplifier-module-context-simple@main
         │   ├─ git clone microsoft/amplifier-module-anthropic-provider@main
         │   ├─ git clone <each declared tool/hook module>@main
         │   └─ uv pip install --no-sources <each module>
         ├─ load vendored agents/*.md from inside wheel (no network)
         └─ pickle.dumps(PreparedBundle) → write to XDG cache
                                          key = aaa_version + sha256(bundle.md)
```

Cost: 5–30 s, dominated by N module clones + N pip installs.

### 5.2 Subsequent invocations (warm cache)

```
amplifier-agent run "hello"
 └─ load_and_prepare_cached(aaa_version, sha256(bundle.md))
     ├─ cache hit
     └─ pickle.loads(cached) → PreparedBundle
```

Cost: sub-second.

### 5.3 Manifest edit

A developer edits `bundle.md` (e.g. swaps a module). On next invocation, the cache key changes because `sha256(bundle.md)` changes. The old pickle is bypassed; the cold path runs once; the new pickle is written under the new key. No manual `cache clear` needed.

## 6. Risks and what would have to be true for Strategy 1 to be the wrong choice

Strategy 1 is the right choice given the design owner's product positioning. The catalytic question — *"what would have to become true for this to be the wrong call?"* — surfaces the signals that should force a revisit:

- **Upstream `@main` drift causes a user-visible regression** that AaA cannot release-around fast enough. Signal: bug reports correlated to a specific module-repo commit.
- **First-run cost grows beyond ~30 s.** Signal: post-install hook timing telemetry; user complaints about install UX.
- **The opinionated manifest's content choice becomes contested** between app-cli and amplifier-agent. Signal: design-review pressure to "just use the foundation bundle" recurring.
- **Supply chain risk materializes.** Signal: any module repo gets compromised; push-access concerns at any upstream.
- **The four vendored agents drift behind build-up's evolution** in ways that matter. Signal: build-up upstream adds capabilities our vendored copies lack.

These belong on a watchlist, not in the implementation plan. If any becomes true, this decision document gets superseded by another.

## 7. Tradeoffs considered

The candidate set evaluated in the design pass:

| # | Strategy | Cold-start (1st run) | Wheel size | Verdict |
|---|---|---|---|---|
| **1** | **Vendored opinionated manifest** (chosen) | 5–30 s | ~0 KB delta + few KB for agents | Matches product positioning. Smallest implementation. Eliminates the broken-include gap structurally. |
| 2 | Run `amplifier bundle add` at install time | Same | ~0 KB | Adds install-time dependency on app-cli CLI. Couples our install to a sibling tool. Rejected. |
| 3 | Vendor foundation source tree | 4–28 s | +80 KB to +10 MB | Wheel grows. Build-time network. Modules still install on first run. Solves only the include URI problem, which Strategy 1 dissolves entirely. Rejected. |
| 4 | Vendor module wheels | <1 s | +tens of MB | Forces AaA to pin module SHAs at release time. Directly contradicts the "modules update independently" model. Rejected. |
| 5 | Vendor pre-prepared pickle | <500 ms | +tens of MB | Requires #4 plus Python/foundation version coupling. Most fragile against upstream-kernel drift. Rejected. |
| 6 | Skip build-up; ship own bundle | 5–30 s | ~0 KB delta | Near-duplicate of Strategy 1 once we vendor build-up's agents. Folded into Strategy 1. |

Strategy 1 wins because the design owner explicitly accepts the first-run cliff (D3) and explicitly wants module updates to flow independently of AaA releases (D4). Every other candidate either bundles module content (forcing release coupling) or defers identity to something we do not own (the registry).

## 8. Simplest credible alternative

The cheapest path is the predecessor doc's **Archetype A** — change nothing in code, just rewrite the design language to be honest about manifest-only vendoring and the first-run cliff. This is dominated by Strategy 1 along the dimension that matters most: Archetype A leaves the broken `includes: build-up-foundation` resolution in place. The cheatsheet §3 test reaches the model today only because of the Option C provider injection workaround (commit `e67bdd9`); the include warning is suppressed but not resolved. Strategy 1 makes the bundle actually work end-to-end. The marginal cost is small (a manifest rewrite, four vendored markdown files, a cache-key change) and the architectural clarity is much higher.

## 9. Migration

This is a forward-only change. There is no existing user state to migrate. The work items, in commit-shape order:

1. **Vendor agent files.** Copy `experiments/build-up/agents/{explorer,planner,coder,tester}.md` from amplifier-foundation into `src/amplifier_agent_lib/bundle/agents/`. If those agents `@`-mention any context files (per build-up's `experiments/build-up/context/system-base.md` pattern), vendor those too into `src/amplifier_agent_lib/bundle/context/`.

2. **Rewrite `bundle.md`.** Remove the `includes:` block. Add explicit `tools:`, `hooks:`, and `agents:` blocks. Declare every module with `module:` + `source: git+https://…@main`. Reference the vendored agents by their wheel-relative paths.

3. **Update wheel packaging.** Extend `[tool.hatch.build.targets.wheel.force-include]` in `pyproject.toml` to include the new `agents/` and (if applicable) `context/` markdown files.

4. **Extend the cache key.** In `src/amplifier_agent_lib/bundle/cache.py`, change `cache_dir_for_version(aaa_version)` to also consume `sha256(bundle.md content)`. Preserve existing failure-mode handling (cache corruption → silent rebuild).

5. **Honest renaming.** Update `docs/status/amplifier-as-agent-design-checkpoint.md` and `docs/test-docs/CHEATSHEET.md` per the four renames in §4.2 above. Cheatsheet §3 "Where stuff lives" gets explicit "first-run cost: 5–30 s" language.

6. **Regression test.** Add a test that loads the new manifest, verifies the `agents:` block resolves against the vendored files, and verifies `sha256(bundle.md)` participates in cache-key derivation.

The full task breakdown, test-first sequencing, and per-task acceptance criteria belong to a separate `/write-plan` session that consumes this doc.

## 10. Success metrics

- `amplifier-agent run "Reply with exactly one word: pong"` returns a real model reply on a fresh machine, after the documented two-step install. (Today this works only because of the workaround; Strategy 1 makes it work end-to-end through the bundle.)
- `grep "No handler for URI" <run stderr>` returns nothing. The broken-include warning is gone because there is no include.
- Editing `bundle.md` and re-running without `cache clear` picks up the change. (Today it serves a stale pickle.)
- The four `amplifier-agent` documented agents (`explorer`, `planner`, `coder`, `tester`) are addressable via the `delegate` tool without network resolution.
- The design checkpoint and cheatsheet language matches shipped reality. A reader of either document is not misled about what "vendored" or "sealed" means.

## 11. What is NOT changed by this decision

Explicitly out of scope so that the implementation plan stays focused:

- Mode A / Mode B split.
- Wire protocol (JSON-RPC over stdio).
- `Engine.boot` / `submit_turn` / `shutdown` shape.
- Provider injection from commit `e67bdd9`. Stays as-is.
- The "stdio coprocess, not a server" framing in the design checkpoint.
- Transcript persistence. Deferred to a future hook design.

---

## Next step

`/write-plan` to produce the implementation plan for this decision. The plan should structure the §9 migration items as discrete, test-first tasks with explicit acceptance criteria, and sequence them so that the cache-key change lands before or alongside the manifest rewrite (so that developers iterating on the manifest get correct invalidation behavior immediately).

---

## 12. Capstone verification log

**Date:** 2026-05-19
**Branch:** `feat/baked-in-bundle-revisit`
**Commit at capstone:** `519c66105691bcbbe966ab2e2a953a6f8805eaf2`

| §10 metric | Result | Evidence |
|---|---|---|
| `amplifier-agent run "Reply with exactly one word: pong"` returns a real model reply | PASS | Sub-step 7.5 stdout contained `"reply": "pong"`, exit 0. |
| `grep "No handler for URI" <stderr>` returns nothing | PASS | Sub-step 7.5 stderr verified clean. |
| Editing `bundle.md` and re-running without `cache clear` picks up the change | PASS | Sub-step 7.6 hash differed: `a72996aefc75b5ee` → `bfae3424e6fc4bd4`. |
| Four documented agents addressable via `delegate` without network resolution | PASS | Sub-step 7.7 wheel contains `bundle/agents/{explorer,planner,coder,tester}.md`. |
| Design-checkpoint + cheatsheet language matches shipped reality | PASS | Tasks 1–5 of Phase 3 landed the four renames + first-run cost note. |
| Test suite + ruff + pyright all clean | PASS | Sub-steps 7.1–7.3. |

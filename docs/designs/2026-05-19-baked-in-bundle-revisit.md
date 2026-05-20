# What "baked in" actually means — bundle-vendoring architectural revisit

**Status:** OPEN — design question, not yet a decision. Captured during the 2026-05-19 cheatsheet walk-through when the `context-persistent → context-simple` Thread 1 fix revealed a deeper assumption gap.

**Owner:** TBD (recommend systems-design pass before assigning)

**Scope of this doc:** Capture the question, the current state with evidence, and the decision space. Do **not** propose an answer — the answer belongs to a `/systems-design` pass with adversarial review.

---

## The question

The design checkpoint at `docs/status/amplifier-as-agent-design-checkpoint.md` says, repeatedly:

> "Built-in bundle, vendored." (§1, Executive summary)
> "Strips bundle-loading from cold-start — the engineering work behind Brian's 'near-instant once bundle-loading overhead is stripped' claim." (§1)
> "Built-in bundle. Vendored with the package." (§5)

What we shipped at the end of Phase 1–4 vendors **only the manifest** (a 2-kilobyte YAML+markdown file). The actual modules referenced by the manifest — including the `build-up-foundation` bundle itself — are git-cloned over the network on first invocation.

**The question:** does our implementation deliver what the design checkpoint claims, or does the gap between "vendored manifest" and "vendored modules" warrant either:

1. correcting the design language to match reality, or
2. correcting the implementation to match the design intent, or
3. some hybrid that meets the intent at a different layer (install-time pre-fetch, lazy-with-warm-cache, etc.)?

---

## Current state — evidence

### What is actually in the wheel

```
src/amplifier_agent_lib/bundle/
├── __init__.py     988 B  ─ exposes BUNDLE_MD: Path constant
├── bundle.md      2035 B  ─ the manifest (sealed text)
├── cache.py      3803 B  ─ XDG-cache pickle/unpickle of PreparedBundle
└── loader.py     2103 B  ─ calls foundation.load_bundle(f"file://{BUNDLE_MD}")
```

`pyproject.toml`:

```toml
[tool.hatch.build.targets.wheel]
packages = ["src/amplifier_agent_cli", "src/amplifier_agent_lib"]

[tool.hatch.build.targets.wheel.force-include]
"src/amplifier_agent_lib/bundle/bundle.md" = "amplifier_agent_lib/bundle/bundle.md"
```

There is no build-time fetch step. No `[tool.hatch.build.hooks.*]`. No pre-downloaded `_vendored/` directory. No pre-built wheels staged alongside the manifest.

### What the manifest references — none of which is in the wheel

```yaml
# src/amplifier_agent_lib/bundle/bundle.md
includes:
  - bundle: build-up-foundation
    source: git+https://github.com/microsoft/amplifier-foundation@main       ← cloned on first run

session:
  orchestrator:
    module: loop-streaming
    source: git+https://github.com/microsoft/amplifier-module-loop-streaming@main   ← cloned on first run

  context:
    module: context-simple   # after Thread 1 fix (was context-persistent)
    source: git+https://github.com/microsoft/amplifier-module-context-simple@main   ← cloned on first run
```

`build-up-foundation` lives inside the `microsoft/amplifier-foundation` git repo, not in our package. So does every transitive module both bundles declare.

### First-invocation sequence on a cold cache

```
amplifier-agent run "hello"
 └─ load_and_prepare_cached(aaa_version)
     ├─ cache miss (no $XDG_CACHE_HOME/amplifier-agent/prepared/<version>/prepared.pickle)
     └─ load_and_prepare_bundle()
         ├─ amplifier_foundation.load_bundle(f"file://{BUNDLE_MD}")
         │   ├─ git clone microsoft/amplifier-foundation     # for the build-up-foundation include
         │   ├─ git clone microsoft/amplifier-module-loop-streaming
         │   ├─ git clone microsoft/amplifier-module-context-simple
         │   └─ ... recursively for any transitive includes/deps
         └─ bundle.prepare(install_deps=True)
             └─ for each module: subprocess uv pip install -e <module_path> --no-sources
     └─ pickle.dumps(PreparedBundle) → write to XDG cache
```

The "5–30 s" the cheatsheet labels "first-run cost" is the network + uv-pip-install time of steps inside `load_and_prepare_bundle()`. Subsequent runs deserialize the cached pickle and skip everything above.

### What the post-install hook does (and doesn't do)

```python
# src/amplifier_agent_lib/post_install.py
async def main() -> int:
    # Idempotent: if both exist, the cache is already primed.
    if cache_dir.exists() and manifest.exists():
        return 0
    try:
        await load_and_prepare_cached(aaa_version=__version__)
    except Exception as exc:
        # Failures NEVER fail the install — runtime first-invocation is the safety net.
        ...
    return 0
```

This is a standalone console script (`amplifier-agent-post-install`). `pip install` and `uv tool install` do **not** invoke it automatically. The README documents the two-step incantation; nothing in the wheel forces it. Plain `uv tool install amplifier-agent` leaves the cache cold and the first `run` pays the network cost.

### Cache invalidation

`cache.py:cache_dir_for_version(aaa_version)` keys the cache directory by `aaa_version` only — the AaA package version string (`"0.0.1"`). The cache does **not** key on the bundle.md content hash. Practical consequence: editing `bundle.md` without bumping the AaA version means the next invocation may still serve a stale prepared pickle. (This is a separate bug we already work around by running `amplifier-agent cache clear` after manifest edits; it is implied by the manifest-only-vendoring choice, so it belongs in the same revisit.)

---

## The honest mapping — claim vs reality

| Design-checkpoint / cheatsheet claim | Implemented reality |
|---|---|
| "Built-in bundle, vendored." | The **manifest** (`bundle.md`, ~2 KB) is vendored. The modules and the included `build-up-foundation` bundle are referenced by `git+https://` URL and fetched on first run. |
| "Sealed." | The manifest **text** cannot be edited at runtime — that part holds. But `@main` upstreams drift, so the *content* of the sealed manifest's references is not sealed; it changes over time without any signal to the user. |
| "Strips bundle-loading from cold-start" | Strips on **subsequent** starts (after the XDG pickle is warm). First start — on any new machine, after `cache clear`, or after a version bump — still pays full git-clone + pip-install cost. |
| "Near-instant once bundle-loading overhead is stripped" | True on warm cache. Not delivered for first invocation, which is what users experience day one. |

---

## The decisions hiding behind "baked in"

Each row is independently decidable but the rows interact. A `/systems-design` pass should resolve each.

| Decision | Possible answers | Tradeoff axes |
|---|---|---|
| **D1. What gets vendored at wheel-build time?** | (a) just the manifest [current]; (b) manifest + module source trees; (c) manifest + module wheels; (d) manifest + a full pre-prepared `prepared.pickle`. | wheel size · build complexity · release cadence coupling to upstream module versions · cache-invalidation semantics |
| **D2. What is the cache-key invariant?** | (a) AaA version [current]; (b) AaA version + bundle.md hash; (c) AaA version + bundle.md hash + resolved-module SHAs (build-up-foundation, loop-streaming, etc.). | re-prepare frequency · stale-cache risk · ability to detect upstream drift |
| **D3. What does first invocation cost?** | (a) 5–30 s of network + install [current]; (b) <500 ms always (zero network on first run); (c) install-time amortized, run is always fast; (d) hybrid: minimum viable subset cold-vendored, optional modules lazy. | Brian's "near-instant" claim · install-script complexity · offline-install viability · wheel size |
| **D4. What does "sealed" mean?** | (a) sealed manifest text only [current]; (b) sealed manifest + pinned module SHAs; (c) sealed manifest + pinned + cryptographically signed module sources. | drift risk · reproducibility · supply-chain risk · upgrade UX |
| **D5. Where does transcript persistence live?** | (a) context-module concern (`context-persistent` pattern with `transcript_path` config); (b) CLI-hook concern (`IncrementalSaveHook` pattern from `amplifier-app-cli`); (c) both — context module owns message restore-on-resume, hook owns the human-readable audit log; (d) explicitly not-this-version. | architectural fit · what the cheatsheet currently claims · what app-cli sibling actually does |
| **D6. Relationship to `amplifier-app-cli`.** | (a) we diverge by shipping a sealed manifest at all [current]; (b) we mirror — drop our manifest, default to `foundation` resolved by registry name; (c) we share a substrate library and each ship our own sealed manifest on top. | code reuse · upgrade story · "what makes amplifier-agent different from app-cli?" answer |
| **D7. Post-install hook discoverability.** | (a) separate console script the user must remember [current]; (b) auto-run via uv's post-install mechanism if one exists; (c) auto-run on first import; (d) drop the hook entirely and accept the first-run cost; (e) drop the hook and replace with vendoring per D1. | first-run UX · install reliability · failure-mode complexity |

---

## Three illustrative archetypes (not recommendations)

To make the decision space concrete, three points in the design space that combine the per-row decisions differently. The point of the systems-design pass will be to pick one (or invent a fourth) and justify the choice.

### Archetype A — Honest renaming, no code change

Accept that we ship a sealed *manifest* and that modules are fetched on first run. Update the design checkpoint and cheatsheet to use precise language: "vendored manifest", "first-run prep cost: 5–30 s on a fresh machine; warm cache after that". Keep the post-install hook as the documented amortization path. **Implications:** Brian's "near-instant cold-start" claim must be re-litigated honestly; the eight-axis "is it a server" framing is unaffected; nothing about the engine, wire protocol, or CLI changes. Cheapest option. Does not deliver the original engineering claim.

### Archetype B — True wheel-time vendoring of modules

At wheel build time, the build backend (Hatch hook) `git clone`s each module referenced by `bundle.md` into `src/amplifier_agent_lib/bundle/_vendored/<sha-prefix>/`, and rewrites the manifest's `source:` URLs to `path://` against the vendored paths in a derived manifest used at runtime. **Implications:** wheel size grows from ~tens of KB to ~few MB; build needs network; we control which SHAs are vendored; first run has zero network; "sealed" gains a precise meaning; `cache clear` cost drops dramatically since we still avoid git-clone but `uv pip install --no-sources` still runs. This is what "baked in" usually means.

### Archetype C — Vendored prepared.pickle (cached at build time)

At wheel build time, the build backend runs `load_and_prepare_bundle()` and ships the resulting `prepared.pickle` inside the wheel at a known path. First invocation copies it into `$XDG_CACHE_HOME/...` instead of preparing. **Implications:** wheel size grows further (the pickle is bigger than the source); the pickle's compatibility depends on the user's Python version and foundation kernel version — version-mismatch surfaces as a fail-soft fallback to Archetype A's flow. Strongest cold-start guarantee. Most fragile against upstream-kernel drift.

(Hybrids exist: vendor sources in the wheel + prepare on first run; vendor sources + ship a sample pickle + verify; etc.)

---

## What is NOT in scope of this revisit

- The bundle's *content choice* (which orchestrator, which context module, which capabilities). The 2026-05-19 Thread 1 swap of `context-persistent → context-simple` is content; this revisit is about packaging mechanism.
- The wire protocol, CLI flag surface, or mode-A/mode-B split. None of those depend on D1–D7.
- The "is it a server" framing from §5 of the checkpoint. Unaffected.

---

## Recommended next step

Run a `/systems-design` pass with the systems-design-methodology skill, working through D1–D7 in the table above. Generate at least three candidate designs (the archetypes above are one starting set). Have `systems-design-critic` adversarially review the chosen candidate before any implementation work. Produce a design doc in `docs/designs/` that supersedes this one and clearly answers each of D1–D7.

Until that pass happens, the small Thread 1 fix (`context-persistent → context-simple` + cheatsheet §7 correction) is independent and can land without committing to any D1–D7 answer.

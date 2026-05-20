# Baked-in Bundle (Strategy 1) — Phase 3: Honest Renaming + Capstone Verification

> **Execution:** Use the `subagent-driven-development` workflow to implement this plan.

**Goal:** Land the four "honest renames" from `docs/designs/2026-05-19-baked-in-bundle-decision.md` §4.2 into `docs/status/amplifier-as-agent-design-checkpoint.md` and `docs/test-docs/CHEATSHEET.md`. Add an explicit "first-run cost: 5–30 s" note to the cheatsheet's §3. Run the capstone verification that proves every §10 success metric from the design doc.

**Architecture:** Pure documentation editing followed by an empirical capstone. No production code changes in this phase.

**Source of truth:** `docs/designs/2026-05-19-baked-in-bundle-decision.md` §4.2 (the four renames), §10 (success metrics that become the capstone acceptance criteria), §11 (out-of-scope guardrails).

**Prerequisites:**
- Phases 1 and 2 are complete; all exit criteria satisfied.
- The branch `feat/baked-in-bundle-revisit` contains the cache-key commit, four agent-vendor commits, the manifest rewrite, and the packaging update.
- `uv run pytest -q` is green.

**Out of scope:**
- Any further changes to `bundle.md`, `cache.py`, or `agents/*.md`.
- Engine/wire-protocol/Mode A-B/provider injection — design doc §11.

**Conventions:** Same as Phases 1+2 — atomic commits, Conventional Commits, ruff + pyright clean throughout. Doc-only changes still get small, reviewable commits.

---

## Task 1: Honest rename #1 — "Built-in bundle, vendored" → "Vendored opinionated manifest"

**Files:**
- Modify: `docs/status/amplifier-as-agent-design-checkpoint.md`

**Step 1: Rewrite the Phase-2 bullet on line 72**

In `docs/status/amplifier-as-agent-design-checkpoint.md`, find the row in the L4 features table that today reads (line 72):

```
| 2 | **Built-in bundle, vendored.** | Strips bundle-loading from cold-start — the engineering work behind Brian's "near-instant once bundle-loading overhead is stripped" claim. First-invocation prepare-and-cache to XDG cache. |
```

Replace with:

```
| 2 | **Vendored opinionated manifest.** | Manifest text and four sub-session agent files are vendored in the wheel; modules referenced by the manifest live in their own repos at `@main` and are git-cloned on first invocation. First-run cost: 5–30 s. Subsequent runs hit the warm XDG pickle in <1 s. Per Strategy 1 of `docs/designs/2026-05-19-baked-in-bundle-decision.md`. |
```

**Step 2: Verify the edit was localized**

Run:

```bash
grep -n "Built-in bundle, vendored" docs/status/amplifier-as-agent-design-checkpoint.md || echo "OK: phrase removed"
grep -n "Vendored opinionated manifest" docs/status/amplifier-as-agent-design-checkpoint.md
```

Expected: first line prints `OK: phrase removed`; second line prints exactly one match.

**Step 3: Commit**

```bash
git add docs/status/amplifier-as-agent-design-checkpoint.md
git commit -m "docs(status): rename 'Built-in bundle, vendored' → 'Vendored opinionated manifest'

Per §4.2 of docs/designs/2026-05-19-baked-in-bundle-decision.md — only the
manifest text and four agent files are vendored. Modules live in their own
repos. First-run cost is 5–30 s, not 'strips cold-start'."
```

---

## Task 2: Honest rename #2 — "Strips bundle-loading from cold-start" → "Near-instant on warm cache"

**Files:**
- Modify: `docs/status/amplifier-as-agent-design-checkpoint.md`

**Step 1: Locate every occurrence of the old language**

Run:

```bash
grep -n "near-instant once bundle-loading overhead is stripped\|Strips bundle-loading\|literally stripping it" docs/status/amplifier-as-agent-design-checkpoint.md
```

Expected: several hits, including at least lines 96, 106, 584, and 604.

**Step 2: Rewrite each occurrence**

For each hit, replace as follows (use `edit_file` with replace_all when the phrase is identical across hits, otherwise per-occurrence with surrounding context for uniqueness):

- Original: `Built-in bundle gives us near-instant cold-start as you predicted` (line 96 and line 584)
  → New: `Built-in bundle gives us near-instant cold-start AFTER FIRST INVOCATION as you predicted (first run pays the documented 5–30 s install cliff)`

- Original (line 106): `session stand-up near-instant once bundle-loading overhead is stripped`
  → New: `session stand-up near-instant after first invocation (first-run pays 5–30 s; warm cache is sub-second)`

- Original (line 604): `This is the engineering work behind Brian's "near-instant once bundle-loading overhead is stripped" claim — we're literally stripping it.`
  → New: `This is the engineering work behind Brian's "near-instant" claim — we honor it on warm cache (sub-second). First invocation pays the 5–30 s manifest-resolve + module-install cost; the post-install hook (`amplifier-agent-post-install`) is an opt-in amortizer for users who want fast first-run.`

**Step 3: Verify nothing referencing the old "strips" framing remains**

Run:

```bash
grep -n "stripping it\|strips bundle-loading\|Strips bundle-loading" docs/status/amplifier-as-agent-design-checkpoint.md && echo "REMAINING — fix these" || echo "OK: rename complete"
```

Expected: prints `OK: rename complete`.

**Step 4: Commit**

```bash
git add docs/status/amplifier-as-agent-design-checkpoint.md
git commit -m "docs(status): rename 'strips bundle-loading' → 'near-instant on warm cache'

Per §4.2 of docs/designs/2026-05-19-baked-in-bundle-decision.md — first
invocation pays a 5–30 s cliff for manifest resolve + module installs. The
'near-instant' claim is honored on warm cache only."
```

---

## Task 3: Honest rename #3 — "Sealed" → "Sealed manifest text"

**Files:**
- Modify: `docs/status/amplifier-as-agent-design-checkpoint.md`

**Step 1: Locate occurrences**

Run:

```bash
grep -n "Seal it\|sealed bundle\|sealed (D4)\|pretty sealed\|Sealed bundle\|sealed\"" docs/status/amplifier-as-agent-design-checkpoint.md
```

Expected hits include line 107 (D2 row), line 202, line 993, and line 737.

**Step 2: Rewrite for honesty**

- Line 107 (D2 row): keep as-is. The Brian quote ("we've packaged up like this and it's pretty sealed") is preserved verbatim — that's a direct quotation, not an editorial claim, and renaming inside a quote would be dishonest.

- Line 202: original — `- V1 mount_plan truthy-vs-semantic bug (NC-L8). Replaced by sealed bundle (D4) — no host-facing mount plan to validate.`
  → New: `- V1 mount_plan truthy-vs-semantic bug (NC-L8). Replaced by the vendored opinionated manifest (D4 + Strategy 1) — no host-facing mount plan to validate; the manifest text is sealed per release, modules are at @main.`

- Line 993 (Issue map row): original — `| mount_plan truthy-vs-semantic bug (NC-L8) | V1 L4 | Sealed bundle (D4); no host-facing mount plan |`
  → New: `| mount_plan truthy-vs-semantic bug (NC-L8) | V1 L4 | Sealed manifest text (D4 + Strategy 1); no host-facing mount plan |`

- Line 737: original — `| Spawner library was "opt-in convenience" (V1 Decision #6) | Spawner is the only spawn path; opinionated and sealed |`
  → New: `| Spawner library was "opt-in convenience" (V1 Decision #6) | Spawner is the only spawn path; opinionated; the bundle's manifest text is sealed per release |`

**Step 3: Verify**

Run:

```bash
grep -n "Sealed bundle (D4)\|sealed bundle (D4)" docs/status/amplifier-as-agent-design-checkpoint.md && echo "REMAINING — fix these" || echo "OK"
```

Expected: `OK`.

**Step 4: Commit**

```bash
git add docs/status/amplifier-as-agent-design-checkpoint.md
git commit -m "docs(status): rename 'sealed bundle' → 'sealed manifest text'

Per §4.2 of docs/designs/2026-05-19-baked-in-bundle-decision.md — the YAML
text inside the wheel is immutable per release. The @main refs it points to
are not sealed. Strategy 1 ships an opinionated manifest, not sealed modules.
Brian's direct quote at line 107 is preserved verbatim."
```

---

## Task 4: Honest rename #4 — "vendored bundle" residual references

**Files:**
- Modify: `docs/status/amplifier-as-agent-design-checkpoint.md`

**Step 1: Sweep for remaining "vendored bundle" / "vendored built-in bundle" phrasing**

Run:

```bash
grep -n "vendored bundle\|vendored built-in bundle\|VENDORED built-in bundle\|built-in vendored bundle\|Built-in vendored bundle" docs/status/amplifier-as-agent-design-checkpoint.md
```

Expected hits include lines 173, 205, 244, 422, 461, 604 (after Task 2's edit may have shifted it), 712, 759, 789, 836, 910, 997, 998.

**Step 2: For each hit, decide between three rewrites:**

- If the phrase means "the manifest text packaged in the wheel": rewrite to `vendored opinionated manifest`.
- If the phrase refers to runtime *integrity* (e.g. `vendored bundle integrity` on line 461 referring to what `doctor` checks): rewrite to `vendored manifest + agent files integrity`.
- If the phrase is internal to a quote: leave the quote intact.

Concrete examples (use `edit_file` per occurrence with enough context to keep each replacement unique):

- Line 173: `· Built-in vendored bundle (foundation, opinionated)` → `· Vendored opinionated manifest (Strategy 1 — manifest + agents in wheel; modules @main)`
- Line 205: `Replaced by uv tool install amplifier-agent and a vendored bundle.` → `Replaced by uv tool install amplifier-agent and a vendored opinionated manifest.`
- Line 244 (ASCII art comment): `# VENDORED built-in bundle (source)` → `# VENDORED opinionated manifest + agents (source)`
- Line 422: `Defaults to vendored built-in bundle.` → `Defaults to the vendored opinionated manifest.`
- Line 461: `vendored bundle integrity` → `vendored manifest + agent files integrity`
- Line 712: `with vendored bundle + primed cache` → `with vendored manifest + primed cache`
- Line 759: `vendored built-in bundle` → `vendored opinionated manifest`
- Line 789 (Cold-start metric row): `(first invocation, prepare from vendored source)` → `(first invocation; manifest is vendored, modules cloned from @main)`
- Line 836: `(single wheel? sdist? what's vendored?)` → keep as-is — this is a historical open-question quote about a different decision.
- Line 910 (error code row): `Vendored bundle failed to prepare or load` → `Vendored opinionated manifest failed to prepare or load`
- Lines 997–998: `Vendored bundle in wheel` / `Vendored bundle;` → `Vendored opinionated manifest in wheel` / `Vendored opinionated manifest;`

**Step 3: Final verification**

Run:

```bash
grep -n "vendored bundle\|vendored built-in bundle\|Built-in vendored bundle\|built-in vendored bundle\|Built-in bundle, vendored" docs/status/amplifier-as-agent-design-checkpoint.md
```

Expected: zero matches (or only matches that are inside a `>` blockquote or a Brian-attributed quote which must remain verbatim — verify each hit individually).

**Step 4: Commit**

```bash
git add docs/status/amplifier-as-agent-design-checkpoint.md
git commit -m "docs(status): sweep residual 'vendored bundle' references → 'vendored opinionated manifest'

Final honest-rename pass per §4.2 of docs/designs/2026-05-19-baked-in-bundle-decision.md.
Only verbatim quotations remain unchanged; editorial uses now consistently say
'vendored opinionated manifest' to match shipped reality (manifest + agent files
in wheel; modules at @main resolved at runtime)."
```

---

## Task 5: Cheatsheet §3 — add explicit first-run cost language

**Files:**
- Modify: `docs/test-docs/CHEATSHEET.md`

**Step 1: Read the current cheatsheet structure**

Run: `uv run python -c "print(open('docs/test-docs/CHEATSHEET.md').read())"`

Locate §3 ("Where stuff lives" or whatever the third numbered section is called). Note the exact heading text — the exact rewrite in Step 2 depends on it.

**Step 2: Add the first-run cost callout under §3**

Under §3's heading, insert a callout block (above any subheadings inside §3). The exact insertion is judgement based on the section structure, but the content must include:

```markdown
> **First-run cost:** The first `amplifier-agent run` on a fresh machine pays a
> 5–30 s cliff. The vendored opinionated manifest references module repos at
> `@main`; on first invocation foundation `git clone`s them and runs
> `uv pip install --no-sources` for each. Subsequent runs hit the warm XDG
> pickle at `$XDG_CACHE_HOME/amplifier-agent/prepared/<aaa_version>/<sha256(bundle.md)>/`
> and complete in under a second. The post-install hook
> (`amplifier-agent-post-install`) is the opt-in amortizer if you want a primed
> cache without paying it on the first user-facing run.
>
> Editing `src/amplifier_agent_lib/bundle/bundle.md` changes the cache key
> (`sha256(bundle.md)`) and automatically invalidates the warm pickle — no
> `cache clear` needed.
```

**Step 3: Rewrite the cheatsheet §3 example block if it currently asserts "near-instant"**

If §3 contains a literal claim of "near-instant" or "instant" cold-start without qualification, rewrite the surrounding sentence to read along the lines of:

```markdown
After the first invocation primes the cache (5–30 s), subsequent
`amplifier-agent run "<prompt>"` calls return in under a second.
```

**Step 4: Verify cheatsheet still scans correctly**

Run:

```bash
grep -n "First-run cost\|5–30 s\|near-instant" docs/test-docs/CHEATSHEET.md
```

Expected: `First-run cost` and `5–30 s` are both present. Any remaining `near-instant` reference is qualified ("near-instant on warm cache" or "near-instant after first invocation"), not bare.

**Step 5: Commit**

```bash
git add docs/test-docs/CHEATSHEET.md
git commit -m "docs(cheatsheet): document the 5–30 s first-run cost honestly in §3

Per §4.2 of docs/designs/2026-05-19-baked-in-bundle-decision.md — readers of
the cheatsheet must not be misled into expecting sub-second cold-start. The
warm cache delivers sub-second; the first run pays a documented cliff."
```

---

## Task 6: VERIFY — full test suite + linters clean after doc edits

**Step 1: Run the suite**

```bash
uv run pytest -q
```

Expected: all PASS. (Documentation edits should not affect tests, but run anyway in case any test reads doc content.)

**Step 2: Run linters**

```bash
uv run ruff check
uv run pyright
```

Expected: both clean.

If anything regressed, fix and amend the most recent doc commit before proceeding to the capstone.

---

## Task 7: CAPSTONE — full design-doc §10 success-metrics verification

**Goal:** Empirically demonstrate every §10 success metric from `docs/designs/2026-05-19-baked-in-bundle-decision.md`. This is the final acceptance gate. If any sub-step fails, STOP and report — do NOT commit until all sub-steps pass.

**Prerequisites:**
- `ANTHROPIC_API_KEY` is set in the environment to a valid key (the cheatsheet §3 test requires a real model reply).
- Network access to `github.com` and `api.anthropic.com`.

**Sub-step 7.1 — Test suite green**

```bash
uv run pytest -q
```

Expected: all tests PASS. Capture the final summary line.

**Sub-step 7.2 — Lint clean**

```bash
uv run ruff check
```

Expected: clean (no output, exit 0).

**Sub-step 7.3 — Types clean**

```bash
uv run pyright
```

Expected: `0 errors, 0 warnings, 0 informations` (or equivalent zero-error summary).

**Sub-step 7.4 — Cold cache flush**

```bash
uv run amplifier-agent cache clear
```

Expected: exit 0. (If the subcommand does not exist in this build, `rm -rf "$HOME/.cache/amplifier-agent"` is the documented equivalent. Either is acceptable as long as the XDG cache for amplifier-agent is empty after this step. Verify with `ls "$HOME/.cache/amplifier-agent" 2>/dev/null || echo "GONE"`.)

**Sub-step 7.5 — End-to-end pong test (the cheatsheet §3 flagship test)**

```bash
uv run amplifier-agent run "Reply with exactly one word: pong" 2>/tmp/aaa-pong.stderr 1>/tmp/aaa-pong.stdout
echo "exit_code=$?"
echo "--- stdout ---"
cat /tmp/aaa-pong.stdout
echo "--- stderr ---"
cat /tmp/aaa-pong.stderr
```

Expected (the union of design-doc §10 metrics):
- `exit_code=0`.
- `stdout` contains a JSON object with a `reply` field whose value is a non-empty string (case-insensitive match of `pong` is the happy path, but any non-empty model reply counts — providers vary).
- `stdout` contains `"turnId"` (any value — proves Mode A wire-up reached the engine and back).
- `stderr` does NOT contain `No handler for URI: build-up-foundation`. Verify:
  ```bash
  ! grep -q "No handler for URI" /tmp/aaa-pong.stderr && echo "OK: no broken-include warning"
  ```
  Expected: `OK: no broken-include warning`.

**Sub-step 7.6 — Manifest-edit cache-invalidation proof (D2)**

```bash
# Capture warm-cache dir path before edit.
PRE_HASH=$(uv run python -c "
from amplifier_agent_lib.bundle import BUNDLE_MD
from amplifier_agent_lib.bundle.cache import cache_dir_for_version
print(cache_dir_for_version('0.0.0', bundle_path=BUNDLE_MD).name)
")
echo "pre-edit hash: $PRE_HASH"

# Append a trivial comment.
echo "" >> src/amplifier_agent_lib/bundle/bundle.md
echo "<!-- capstone: cache-invalidation proof appended at $(date -u +%Y-%m-%dT%H:%M:%SZ) -->" >> src/amplifier_agent_lib/bundle/bundle.md

# Capture post-edit hash.
POST_HASH=$(uv run python -c "
from amplifier_agent_lib.bundle import BUNDLE_MD
from amplifier_agent_lib.bundle.cache import cache_dir_for_version
print(cache_dir_for_version('0.0.0', bundle_path=BUNDLE_MD).name)
")
echo "post-edit hash: $POST_HASH"

# Assert the hash component changed.
if [ "$PRE_HASH" = "$POST_HASH" ]; then
  echo "FAIL: D2 cache-key invariant broken — hash unchanged after manifest edit"
  exit 1
else
  echo "OK: D2 wired — hash changed from $PRE_HASH to $POST_HASH"
fi

# REVERT the comment so the repo is clean.
git checkout -- src/amplifier_agent_lib/bundle/bundle.md
```

Expected: `OK: D2 wired — hash changed from <a> to <b>` and the file is reverted (verify with `git status` — should show no modifications).

**Sub-step 7.7 — Vendored agent files present in installed wheel**

```bash
uv build --wheel --out-dir /tmp/aaa-capstone-wheel 2>&1 | tail -5
WHEEL=$(ls /tmp/aaa-capstone-wheel/amplifier_agent-*.whl)
echo "built: $WHEEL"
unzip -l "$WHEEL" | grep "bundle/agents/" | sort
```

Expected output ends with four lines listing:
```
amplifier_agent_lib/bundle/agents/coder.md
amplifier_agent_lib/bundle/agents/explorer.md
amplifier_agent_lib/bundle/agents/planner.md
amplifier_agent_lib/bundle/agents/tester.md
```

(Order may vary; the four names must all appear.)

**Sub-step 7.8 — Branch is push-ready**

```bash
git status
git log --oneline feat/baked-in-bundle-revisit ^main | head -30
```

Expected:
- `git status` shows a clean working tree (no uncommitted changes, no untracked files except possibly the `/tmp/aaa-*` capture files and `/tmp/aaa-capstone-wheel`).
- `git log` shows the full series: design doc commit (already present pre-Phase-1) → 1 cache-key commit → 4 agent-vendor commits → AGENTS_DIR commit → manifest-rewrite commit → packaging commit → 4–5 doc-rename commits → cheatsheet commit. Roughly 13–15 commits since `main`.

---

## Task 8: COMMIT — capstone verification record

**Step 1: Append a verification log to the design doc**

Open `docs/designs/2026-05-19-baked-in-bundle-decision.md` and append a new section at the end:

```markdown

---

## 12. Capstone verification log

**Date:** <YYYY-MM-DD of capstone run>
**Branch:** `feat/baked-in-bundle-revisit`
**Commit at capstone:** `<git rev-parse HEAD output>`

| §10 metric | Result | Evidence |
|---|---|---|
| `amplifier-agent run "Reply with exactly one word: pong"` returns a real model reply | PASS | Sub-step 7.5 stdout contained `"reply": "<reply>"`, exit 0. |
| `grep "No handler for URI" <stderr>` returns nothing | PASS | Sub-step 7.5 stderr verified clean. |
| Editing `bundle.md` and re-running without `cache clear` picks up the change | PASS | Sub-step 7.6 hash differed: `<pre>` → `<post>`. |
| Four documented agents addressable via `delegate` without network resolution | PASS | Sub-step 7.7 wheel contains `bundle/agents/{explorer,planner,coder,tester}.md`. |
| Design-checkpoint + cheatsheet language matches shipped reality | PASS | Tasks 1–5 of Phase 3 landed the four renames + first-run cost note. |
| Test suite + ruff + pyright all clean | PASS | Sub-steps 7.1–7.3. |
```

Fill in `<YYYY-MM-DD>`, `<git rev-parse HEAD>`, `<reply>`, `<pre>`, `<post>` with the actual values captured during Task 7.

**Step 2: Commit**

```bash
git add docs/designs/2026-05-19-baked-in-bundle-decision.md
git commit -m "docs(designs): record Strategy 1 capstone verification — all §10 metrics PASS

Empirical verification log for the baked-in-bundle Strategy 1 implementation.
All success metrics from §10 of this design doc verified against the working
implementation on feat/baked-in-bundle-revisit. The 'No handler for URI'
warning is gone, manifest edits self-invalidate, and the four vendored agents
ship in the wheel."
```

---

## Phase 3 Exit Criteria

All of the following are true:

1. `grep -n "Built-in bundle, vendored\|Strips bundle-loading\|sealed bundle (D4)" docs/status/amplifier-as-agent-design-checkpoint.md` returns zero matches.
2. `grep -n "First-run cost\|5–30 s" docs/test-docs/CHEATSHEET.md` returns at least one match each.
3. `uv run pytest -q` — green.
4. `uv run ruff check` — clean.
5. `uv run pyright` — clean.
6. `uv run amplifier-agent run "Reply with exactly one word: pong"` returns exit 0 with a JSON body containing a non-empty `reply` and a `turnId`, and stderr does NOT contain `No handler for URI`.
7. Editing `bundle.md` trivially (without `cache clear`) changes `cache_dir_for_version(...).name` (the sha256 hash component) — proves D2 is wired.
8. The four files `bundle/agents/{explorer,planner,coder,tester}.md` are present in the built wheel (verified by `unzip -l`).
9. `git status` is clean; `git log feat/baked-in-bundle-revisit ^main` shows the full implementation series.
10. The capstone verification log is appended to `docs/designs/2026-05-19-baked-in-bundle-decision.md` §12 with concrete evidence values filled in.

---

## Out-of-band finishing

After Phase 3 exits cleanly, follow `superpowers:finishing-a-development-branch` to decide on merge / PR / cleanup. The branch is ready to push and open a PR against `main` once exit criteria 1–10 are all green.
